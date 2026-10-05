"""Science QA checks on converted SBE 911plus casts (reported in the PDF and notebook; the R2R
XML only carries R2R's own tests).
"""

from dataclasses import dataclass

import numpy as np
from seabirdscientific import processing

from sbe_qa_processing.cast import CastData
from sbe_qa_processing.config import Thresholds
from sbe_qa_processing.loadout import cast_loadout

PASS, WARN, FAIL, INFO, NA = "pass", "warn", "fail", "info", "n/a"


@dataclass
class CheckResult:
    name: str
    status: str
    value: str
    limit: str = ""
    detail: str = ""


def _grade(value: float, limit: float, warn_factor: float = 2.0) -> str:
    if value <= limit:
        return PASS
    return WARN if value <= limit * warn_factor else FAIL


def spike_mask(values: np.ndarray) -> np.ndarray:
    """Wild points by SBE Data Processing's Wild Edit with its standard settings (2 and 20
    standard deviations over 100-scan blocks), via seabirdscientific
    """
    edited = processing.wild_edit(
        values.astype(float),
        np.zeros(len(values)),
        std_pass_1=2,
        std_pass_2=20,
        scans_per_block=100,
        distance_to_mean=0,
        exclude_bad_flags=False,
    )
    return edited == processing.FLAG_VALUE


def downcast_slice(data: CastData) -> slice:
    return slice(0, int(np.nanargmax(data.pressure)) + 1)


def descent_rate(data: CastData, smoothing_seconds: float = 2.0) -> np.ndarray:
    """[dbar/s] pressure rate of change, smoothed with a running mean"""
    interval = data.config.sample_interval
    window = max(round(smoothing_seconds / interval), 1)
    smoothed = np.convolve(data.pressure, np.ones(window) / window, mode="same")
    return np.gradient(smoothed, interval)


def lost_scans(modulo: np.ndarray) -> int:
    """Scans missing from the 8-bit modulo counter, which should step by one each scan"""
    steps = np.diff(modulo) % 256
    return int(np.sum(np.where(steps == 0, 0, steps - 1)))


def run_science_checks(data: CastData, thresholds: Thresholds) -> list[CheckResult]:
    results: list[CheckResult] = []
    deep = data.pressure > thresholds.dual_sensor_min_pressure

    # Dual sensor agreement below the surface layer. The median is the sensors' offset; the 95th
    # percentile is dominated by sharp gradients, where the two sensors briefly disagree
    for label, primary, secondary, limit, unit in (
        (
            "Temperature",
            data.temperature,
            data.temperature2,
            thresholds.temperature_difference,
            "°C",
        ),
        (
            "Conductivity",
            data.conductivity,
            data.conductivity2,
            thresholds.conductivity_difference,
            "S/m",
        ),
        ("Salinity", data.salinity, data.salinity2, thresholds.salinity_difference, "PSU"),
    ):
        name = f"Dual {label.lower()} difference (median)"
        if secondary is None:
            results.append(CheckResult(name, NA, "no secondary sensor"))
        elif not deep.any():
            results.append(
                CheckResult(
                    name, NA, f"no data below {thresholds.dual_sensor_min_pressure:g} dbar"
                )
            )
        else:
            difference = np.abs(primary - secondary)[deep]
            median = float(np.median(difference))
            results.append(
                CheckResult(
                    name,
                    _grade(median, limit),
                    f"{median:.4g} {unit}",
                    f"≤ {limit:g} {unit}",
                    f"95th percentile {np.percentile(difference, 95):.4g} {unit}, "
                    f"below {thresholds.dual_sensor_min_pressure:g} dbar",
                )
            )

    # Plausible ranges
    for label, values, (low, high) in (
        ("Pressure", data.pressure, thresholds.pressure_range),
        ("Temperature", data.temperature, thresholds.temperature_range),
        ("Conductivity", data.conductivity, thresholds.conductivity_range),
        ("Salinity", data.salinity, thresholds.salinity_range),
    ):
        # Surface scans before the pump starts sit outside the salinity range in air
        mask = (
            data.pump
            if (label in ("Conductivity", "Salinity") and data.pump is not None)
            else slice(None)
        )
        subset = values[mask]
        if subset.size == 0:
            results.append(CheckResult(f"{label} range", NA, "no pumped scans"))
            continue
        outside = float(np.mean((subset < low) | (subset > high)) * 100)
        status = PASS if outside == 0 else WARN if outside < 1 else FAIL
        results.append(
            CheckResult(
                f"{label} range",
                status,
                f"{outside:.2f}% outside",
                f"{low:g} to {high:g}",
                f"min {np.nanmin(subset):.4g}, max {np.nanmax(subset):.4g}",
            )
        )

    # Spikes
    for label, values in (
        ("Temperature", data.temperature),
        ("Conductivity", data.conductivity),
        ("Pressure", data.pressure),
    ):
        percent = float(np.mean(spike_mask(values)) * 100)
        results.append(
            CheckResult(
                f"{label} spikes",
                _grade(percent, thresholds.max_spike_percent),
                f"{percent:.3f}% of scans",
                f"≤ {thresholds.max_spike_percent:g}%",
            )
        )

    # Data integrity
    if data.modulo is not None:
        lost = lost_scans(data.modulo)
        percent = 100 * lost / max(data.scans + lost, 1)
        results.append(
            CheckResult(
                "Lost scans (modulo count)",
                _grade(percent, thresholds.max_lost_scan_percent, warn_factor=10),
                f"{lost} ({percent:.3f}%)",
                f"≤ {thresholds.max_lost_scan_percent:g}%",
            )
        )
    if data.latitude is not None:
        missing = np.isnan(data.latitude) | ((data.latitude == 0) & (data.longitude == 0))
        results.append(
            CheckResult(
                "NMEA position on every scan",
                PASS if not missing.any() else WARN,
                f"{int(missing.sum())} scans without position",
            )
        )
    else:
        results.append(CheckResult("NMEA position on every scan", NA, "not appended"))
    if data.pump is not None:
        submerged = data.pressure > 2
        share = float(np.mean(data.pump[submerged]) * 100) if submerged.any() else 0.0
        status = PASS if share >= 99 else WARN if share >= 90 else FAIL
        results.append(
            CheckResult("Pump on below 2 dbar", status, f"{share:.1f}% of scans", "≥ 99%")
        )

    # Calibration age of every sensor in use
    limit = thresholds.max_calibration_age_days
    rows = cast_loadout(data)
    dated = [r for r in rows if r.age_days is not None]
    if not dated:
        results.append(CheckResult("Sensor calibration age", NA, "no readable calibration dates"))
    else:
        stale = [r for r in dated if r.age_days > limit]
        undated = len(rows) - len(dated)
        results.append(
            CheckResult(
                "Sensor calibration age",
                WARN if stale else PASS,
                f"{len(stale)} of {len(dated)} older than {limit} days",
                f"≤ {limit} days",
                "; ".join(f"{r.sensor} {r.serial_number}: {r.age_days} d" for r in stale)
                + (f" ({undated} without a date)" if undated else ""),
            )
        )

    # Cast behavior (informational)
    downcast = downcast_slice(data)
    rate = descent_rate(data)[downcast]
    moving = rate[data.pressure[downcast] > 5]
    results.append(CheckResult("Maximum pressure", INFO, f"{np.nanmax(data.pressure):.1f} dbar"))
    if moving.size:
        reversals = float(np.mean(moving < 0) * 100)
        results.append(
            CheckResult(
                "Downcast descent rate (median)",
                INFO,
                f"{np.median(moving):.2f} dbar/s",
                detail=f"{reversals:.1f}% of downcast ascending (heave)",
            )
        )
    results.append(
        CheckResult("Duration", INFO, f"{data.time[-1] / 60:.1f} min, {data.scans} scans")
    )
    if data.bottle_confirm is not None:
        fires = int(np.sum(np.diff(data.bottle_confirm.astype(int)) == 1))
        results.append(CheckResult("Bottle fire confirms", INFO, str(fires)))
    return results
