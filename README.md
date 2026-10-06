# sbe-qa-processing

> [!WARNING]
> **Under heavy development.** This project is shared for peer review. Its outputs, command
> line, config format and Python API may change without notice, and its results haven't been
> validated beyond the R2R filesets listed under [Validation against R2R](#validation-against-r2r).
> Don't rely on it for operational QA yet. Issues and comments are welcome.

Quality assessment for Sea-Bird **SBE 9/911plus** CTD filesets. From a fileset (a BagIt bag as
R2R/NCEI distribute it, or a plain directory of Seasave output) and a small cruise config, it
writes:

| Output | Contents |
|---|---|
| `<CRUISE>_<FILESET>_qa.2.0.xml` | R2R QA 2.0 rollup report: the same tests, infos, ratings and manifest R2R's `r2r-ctd` produces, in R2R's schema and with its stylesheet reference |
| `<CRUISE>_<FILESET>_qa_report.pdf` | R2R certificate; a cast map with coastlines and a world inset; and per cast: profiles, dual-sensor differences, TS diagram, pressure record, science checks and sensor loadout |
| `<CRUISE>_<FILESET>_plots/*.svg` | Every report figure as SVG: the cast map, and each cast's profiles, TS diagram and pressure record. The PDF embeds these same SVGs as vector graphics |
| `<CRUISE>_<FILESET>_qa.ipynb` | A notebook that re-runs the whole assessment step by step, to inspect or change it |

Raw `.hex` data is read and converted with
[seabirdscientific](https://github.com/Sea-BirdScientific/seabirdscientific), installed from
the `integration` branch of [a fork](https://github.com/webbpinner/seabirdscientific) that
carries fixes not yet released upstream (see [seabirdscientific version](#seabirdscientific-version)).
The `.XMLCON` parser lives here, since seabirdscientific doesn't have one.

## Setup

Needs [uv](https://docs.astral.sh/uv/) (`brew install uv`) and git.

```bash
git clone https://github.com/OceanDataTools/sbe-qa-processing.git
cd sbe-qa-processing
uv sync
uv run sbe-qa-processing fetch-map-data   # Natural Earth coastlines for the maps (~27 MB, once)
```

seabirdscientific is installed from the `integration` branch of
[webbpinner/seabirdscientific](https://github.com/webbpinner/seabirdscientific), not from PyPI,
whose release lacks the fixes this tool needs. `uv sync` installs the exact commit pinned in
`uv.lock`. `pip install git+https://github.com/OceanDataTools/sbe-qa-processing.git` also works,
and takes the branch's latest commit.

The fork is temporary. Once its fixes are released in the official
[seabirdscientific](https://github.com/Sea-BirdScientific/seabirdscientific), this project will
depend on that release instead. Until then, the fork's `integration` branch is rebuilt as fixes
change, so a `pip` install can pick up a different commit from one day to the next.

The maps use [cartopy](https://scitools.org.uk/cartopy/) with Natural Earth 1:10m land, lakes
and coastlines, and a 1:110m world inset. cartopy downloads them on first use; run `fetch-map-data`
beforehand to work offline (e.g. at sea). Without the data, maps are drawn without
coastlines.

## Usage

```bash
# Cruise metadata R2R normally takes from its catalog: id, dates, bounding box, ...
# from R2R's catalog API, for a cruise and CTD fileset R2R knows:
uv run sbe-qa-processing config-from-r2r MYCRUISE 123456   # writes configs/MYCRUISE.toml
# or from an existing R2R QA 2.0 report (adds port coordinates), or write it by hand:
uv run sbe-qa-processing config-from-r2r-qa path/to/r2r_qa.2.0.xml   # configs/MYCRUISE.toml

uv run sbe-qa-processing run configs/MYCRUISE.toml path/to/fileset -o output/MYCRUISE
uv run sbe-qa-processing run configs/MYCRUISE.toml path/to/fileset -o output/MYCRUISE --execute-notebook
```

Every command is listed by `sbe-qa-processing --help`, and documents its arguments under
`sbe-qa-processing COMMAND --help`. They share these conventions:
- `-o/--output` is a directory; only `site-config` writes a single file. Commands that make a
  cruise TOML write `<OUTPUT_DIR>/<CRUISE_ID>.toml` (default `configs/`), or to stdout with
  `-o -`.
- Existing outputs, reports included, aren't overwritten without `--force`. The exception is
  the `openvdm` hook, which replaces its reports after each transfer.

A cruise config (see `src/sbe_qa_processing/config.py` for every field):

```toml
[cruise]
id = "SP2613"
name = "..."
vessel_id = "32ST"
depart_date = 2026-07-09
arrive_date = 2026-07-09

[cruise.extent]   # the NAV tests check casts against this box
westernmost = -117.3773
easternmost = -117.2262
southernmost = 32.5997
northernmost = 32.7054

[fileset]
id = 169847

[thresholds]      # optional; e.g. fresh water
salinity_range = [0.0, 42.0]
```

## Running from OpenVDM

[OpenVDM](https://github.com/OceanDataTools/openvdm) can run the QA after each CTD transfer, as a
`postCollectionSystemTransfer` hook. The hook reads the current cruise from OpenVDM's API
(`api/warehouse/getCruiseConfig`): cruise ID, name, PI, location, dates, ports, the warehouse
directory, the CTD transfer's destination, the extra directories and the MD5 summary.

1. Install this project somewhere OpenVDM's worker can run it (it has its own environment, not
   OpenVDM's venv), and fetch the map data: `uv sync && uv run sbe-qa-processing fetch-map-data`.
2. In OpenVDM, add an extra directory for the reports (Configuration > Extra Directories), named
   `CTD_QA` by default, e.g. with destination `Products/CTD_QA`.
3. Write the ship's site config once, for what OpenVDM doesn't store: the vessel and its R2R
   IDs, the report contact, the extra directories and a fallback cruise extent. It prompts for
   each field and works offline. `--from-r2r` pre-fills the vessel, operator and scheduler from
   any past cruise of the ship in R2R:

   ```bash
   uv run sbe-qa-processing site-config [--from-r2r RR2605]   # writes configs/site.toml
   ```

   Run it again with `--force` to change the file; its current values are the defaults. QA
   thresholds (e.g. for fresh water) are edited by hand under `[thresholds]`.
   `configs/site.example.toml` shows every field.
4. Add the hook to `/opt/openvdm/server/etc/openvdm.yaml`:

```yaml
postHookCommands:
    postCollectionSystemTransfer:
        - collectionSystemTransferName: CTD
          commandList:
          - name: "SBE 9 QA report"
            command:
            - /opt/sbe-qa-processing/.venv/bin/sbe-qa-processing
            - openvdm
            - "{collectionSystemTransferName}"
            - "--site-config=/opt/sbe-qa-processing/configs/site.toml"
            - "--changed-files={newFiles}"
            - "--changed-files={updatedFiles}"
```

The hook:
- **skips** transfers whose new or updated files include no `.hex/.dat`, `.XMLCON/.con`, `.hdr`
  or `.bl` (use the `--changed-files={newFiles}` form, so an empty list isn't dropped);
- writes the XML, PDF, SVG plots and notebook into the extra directory, plus
  `<cruise>_ctd_cruise.toml`, a snapshot of what OpenVDM supplied that the notebook re-runs from,
  and gives them to the warehouse user when run as root;
- checks checksums against OpenVDM's MD5 summary. Files changed after the summary was written
  count as pending, not failures, since OpenVDM updates the summary in parallel with the hook;
- takes the cruise extent from GeoJSON tracklines in the `Tracklines` extra directory (OpenVDM's
  `build_cruise_tracks`), else from the site config; without either, the Lat/Lon test is GREY (N);
- names reports `<cruise>_ctd_...` until an R2R fileset ID is passed with `--fileset-id`;
- exits non-zero with a message on failure, which OpenVDM shows.

To review the cruise TOML before relying on the hook, or to run the QA by hand, write it without
running the QA:

```bash
uv run sbe-qa-processing config-from-openvdm --site-config configs/site.toml   # configs/<CRUISE>.toml
uv run sbe-qa-processing run configs/<CRUISE>.toml /path/to/cruise/CTD -o output/<CRUISE>
```

`config-from-openvdm` takes the same OpenVDM options as the hook, plus `-o DIR` (or `-o -` for
stdout), `--force` and `--fileset-id`. It builds the TOML the same way the hook builds its
snapshot, and it reports what's missing or assumed: where the cruise extent came from, an
open-ended cruise's end date (set to today), blank R2R port IDs and a missing vessel ID. The
hook still rebuilds the TOML on every run, since OpenVDM's cruise details and tracklines change
during a cruise.

## What it checks

**R2R tests** (written to the XML and the PDF):
- Presence of all raw files: every cast has `.hex/.dat`, `.XMLCON/.con` and `.hdr`.
- Valid checksums: every file in the bag's `manifest-md5.txt` matches. BLACK (X) without a
  manifest.
- Lat/lon and dates within the cruise extent and dates (deck tests excluded).
- Infos: raw file count, casts with bottles fired, instrument model, casts with NAV on every
  scan, and casts with missing files, unreadable `.hex` or `.XMLCON`, deck tests, sensor serial
  number mismatches (`.hdr` vs `.XMLCON`), or bad/outside navigation.

**Science checks** (PDF and notebook), per cast that isn't a deck test:
- Primary/secondary temperature, conductivity and salinity agreement below 20 dbar (median).
- Plausible ranges, Wild Edit spikes (seabirdscientific, SBE Data Processing's 2/20 σ settings).
- Lost scans from the modulo counter, NMEA position on every scan, pump on in the water.
- Sensor calibration age: warns when a sensor's `.XMLCON` calibration date is more than
  `max_calibration_age_days` (default 365) before the cast.
- Informational: maximum pressure, descent rate and heave, duration, bottle fires.

**Sensor loadout** (PDF and notebook), per cast: the sensor on each frequency and voltage
channel with its serial number, calibration date and age at the time of the cast, and the deck
unit settings (scan rate, suppressed channels, data appended to each scan).

## Validation against R2R

`tests/test_r2r_comparison.py` runs the tool on two released R2R filesets and requires the
XML's `certificate` (ratings, results, bounds, infos) and manifest to be identical to R2R's own
reports:

| Fileset | R2R | This tool |
|---|---|---|
| SP2613 / 169847 (Sproul) | G | G, certificate identical |
| RR2605 / 170644 (Revelle) | Y (one cast outside the extent) | Y, certificate identical |
| BH18-18 / 132368 (Blue Heron) | no QA 2.0 report | R presence test (a cast with only a `.hdr`), SBE 21 configuration flagged |

Fetch them with:

```bash
uv run sbe-qa-processing fetch-fileset SP2613 169847   # into data/SP2613_169847_ctd/
uv run sbe-qa-processing fetch-fileset RR2605 170644
uv run sbe-qa-processing fetch-fileset BH18-18 132368
uv run pytest
```

R2R doesn't document every rule; the ones inferred here (deck tests excluded from NAV tests, a
NAV test is YELLOW at ≥ 50% of casts, the overall rating counts individual per-cast checks) are
noted in `src/sbe_qa_processing/r2r.py` and reproduce R2R's ratings for both filesets.

## seabirdscientific version

seabirdscientific comes from the **`integration`** branch of the fork
`webbpinner/seabirdscientific`: upstream's `v3.0.0` plus every pending fix (including the three
below). `uv.lock` pins the commit; after the branch changes, update with
`uv lock --upgrade-package seabirdscientific`. The tool also works on plain upstream `v3.0.0`,
with the workarounds noted.

To develop against a local seabirdscientific checkout instead, install it over the locked one
with `uv pip install -e path/to/seabirdscientific` and run commands with `uv run --no-sync`
(a plain `uv run` or `uv sync` puts the locked version back).

- **911plus status bits read in reverse** (fork issue 24, fixed on `integration`). Older
  seabirdscientific reads the status nibble most significant bit first, so its "pump status" is
  really the modem bit. `status.py` checks at runtime how the installed version decodes a scan
  with only the pump bit set, and reads the bits correctly either way. Real data shows the
  documented order: deck tests 0b0010, casts 0b0011, 0b0111 while bottles fire.
- **TS contour grid** (fork issue 21, fixed on `integration`). Older versions return a
  one-column grid for a narrow salinity range (e.g. fresh water), and the TS plot then skips the
  contours. Either way, the plot zooms to the data and picks contour levels from the density
  range in view.
- **`eos80_processing.potential_temperature` ignored in-situ pressure** (fork issue 19, fixed on
  `integration`). Not used by this tool.
- Temperature and conductivity coefficients have no slope/offset in seabirdscientific; the
  `.XMLCON` slope/offset is applied here. Digiquartz pressure slope/offset is applied by
  seabirdscientific.

## License

[MIT](LICENSE)
