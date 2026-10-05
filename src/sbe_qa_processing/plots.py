"""Figures for the PDF report and the notebook. Each function returns a matplotlib Figure."""

import cartopy.crs as ccrs
import gsw
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from matplotlib.ticker import MaxNLocator
from seabirdscientific.contour import contour_from_t_s_p

from sbe_qa_processing import maps
from sbe_qa_processing.bottles import bottle_fires
from sbe_qa_processing.cast import CastData
from sbe_qa_processing.config import CruiseConfig
from sbe_qa_processing.r2r import cast_position
from sbe_qa_processing.science import downcast_slice

PRIMARY = "#1f5f8b"
SECONDARY = "#d1495b"
PAGE = (8.5, 11)  # US letter, portrait
TS_MAX_POINTS = 4000
RATING_COLORS = {"G": "#2e8b57", "Y": "#e0b000", "R": "#c0392b", "N": "#8c8c8c", "X": "#222222"}
STATUS_COLORS = {
    "pass": "#d8f0df",
    "warn": "#fff1bf",
    "fail": "#f6d0cc",
    "info": "#eef2f7",
    "n/a": "#f2f2f2",
}


def _downcast(data: CastData, values: np.ndarray | None) -> np.ndarray | None:
    return None if values is None else values[downcast_slice(data)]


def _running_median(values: np.ndarray, window: int) -> np.ndarray:
    if window < 2 or len(values) < window:
        return values
    padded = np.pad(values, window // 2, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(padded, window), axis=1)[
        : len(values)
    ]


def profiles(data: CastData) -> Figure:
    """Downcast temperature, salinity and conductivity for both sensors, and their differences"""
    figure, axes = plt.subplots(1, 4, figsize=(7.5, 9.5), sharey=True, layout="constrained")
    pressure = _downcast(data, data.pressure)
    for axis, label, primary, secondary in (
        (axes[0], "Temperature [°C]", data.temperature, data.temperature2),
        (axes[1], "Salinity [PSU]", data.salinity, data.salinity2),
        (axes[2], "Conductivity [S/m]", data.conductivity, data.conductivity2),
    ):
        axis.plot(_downcast(data, primary), pressure, color=PRIMARY, lw=0.8, label="primary")
        if secondary is not None:
            axis.plot(
                _downcast(data, secondary),
                pressure,
                color=SECONDARY,
                lw=0.8,
                alpha=0.8,
                label="secondary",
            )
        axis.set_xlabel(label)
        axis.grid(alpha=0.3)
    axes[0].set_ylabel("Pressure [dbar]")
    axes[0].invert_yaxis()
    axes[0].legend(loc="lower right", fontsize=7)

    axis = axes[3]
    if data.has_secondary:
        # Raw 24 Hz differences faintly, with a 2 s running median on top
        window = max(round(2 / data.config.sample_interval), 1)
        for difference, color, label in (
            (data.temperature - data.temperature2, PRIMARY, "ΔT [°C]"),
            (data.salinity - data.salinity2, SECONDARY, "ΔS [PSU]"),
        ):
            down = _downcast(data, difference)
            axis.plot(down, pressure, color=color, lw=0.3, alpha=0.25)
            axis.plot(_running_median(down, window), pressure, color=color, lw=0.9, label=label)
        axis.axvline(0, color="k", lw=0.5)
        axis.set_xlim(-0.05, 0.05)
        axis.set_xticks([-0.04, 0, 0.04])
        axis.legend(loc="lower right", fontsize=7)
    else:
        axis.text(
            0.5,
            0.5,
            "no secondary\nsensor pair",
            ha="center",
            va="center",
            transform=axis.transAxes,
            color="0.5",
        )
    axis.set_xlabel("Primary − secondary")
    axis.grid(alpha=0.3)
    figure.suptitle(f"{data.name}: downcast profiles", fontsize=11)
    return figure


def _padded_range(values: np.ndarray, minimum_pad: float) -> tuple[float, float]:
    low, high = float(np.nanmin(values)), float(np.nanmax(values))
    pad = max(0.08 * (high - low), minimum_pad)
    return low - pad, high + pad


def ts_diagram(data: CastData) -> Figure:
    """Conservative temperature vs absolute salinity with σ0 contours (seabirdscientific)"""
    figure, axis = plt.subplots(figsize=(7.5, 5.5), layout="constrained")
    position = cast_position(data)
    lat, lon = position if position else (0.0, 0.0)
    down = downcast_slice(data)
    contour = contour_from_t_s_p(
        data.temperature[down],
        data.salinity[down],
        data.pressure[down],
        min_salinity=0.01,
        lat=lat,
        lon=lon,
    )
    # View the data with a small margin rather than the whole density grid, whose fixed margins
    # dwarf a narrow (e.g. freshwater) cast
    x_view = _padded_range(contour.x, minimum_pad=0.02)
    y_view = _padded_range(contour.y, minimum_pad=0.1)
    axis.set_xlim(*x_view)
    axis.set_ylim(*y_view)
    # seabirdscientific versions before the contour grid fix can return a one-column grid for
    # a narrow salinity range (e.g. fresh water)
    if min(contour.z_mat.shape) >= 2:
        # Levels spanning the density range inside the view, so lines appear at any scale
        corners = gsw.rho(np.array(x_view)[:, None], np.array(y_view)[None, :], 0).ravel() - 1000
        levels = MaxNLocator(8).tick_values(corners.min(), corners.max())
        lines = axis.contour(
            contour.x_vec,
            contour.y_vec,
            contour.z_mat,
            levels=levels,
            colors="0.6",
            linewidths=0.5,
        )
        axis.clabel(lines, fontsize=6, fmt=lambda level: f"{level:g}")
    else:
        axis.text(
            0.02,
            0.98,
            "density grid too narrow to contour",
            transform=axis.transAxes,
            fontsize=7,
            color="0.4",
            va="top",
        )
    # At most TS_MAX_POINTS evenly spaced scans: indistinguishable at page scale, and keeps the
    # SVG small (a deep cast has ~80,000 downcast scans)
    keep = np.unique(np.linspace(0, len(contour.x) - 1, TS_MAX_POINTS).astype(int))
    points = axis.scatter(
        contour.x[keep], contour.y[keep], c=data.pressure[down][keep], s=1, cmap="viridis_r"
    )
    figure.colorbar(points, ax=axis, label="Pressure [dbar]")
    axis.set_xlabel("Absolute salinity [g/kg]")
    axis.set_ylabel("Conservative temperature [°C]")
    axis.set_title(f"{data.name}: TS (σ0 contours)", fontsize=10)
    return figure


def pressure_series(data: CastData) -> Figure:
    """Pressure over time with pump status and bottle fires"""
    figure, axis = plt.subplots(figsize=(7.5, 4.3), layout="constrained")
    minutes = data.time / 60
    axis.plot(minutes, data.pressure, color=PRIMARY, lw=0.8)
    if data.pump is not None and (~data.pump).any():
        axis.fill_between(
            minutes,
            0,
            1,
            where=~data.pump,
            transform=axis.get_xaxis_transform(),
            color="0.85",
            label="pump off",
        )
    # Fires from the bottle log (matching the bottle table), else from the confirm status bit
    fires = np.array([f.first_scan for f in bottle_fires(data) if 0 <= f.first_scan < data.scans])
    if not fires.size and data.bottle_confirm is not None:
        fires = np.flatnonzero(np.diff(data.bottle_confirm.astype(int)) == 1) + 1
    if fires.size:
        axis.scatter(
            minutes[fires],
            data.pressure[fires],
            marker=">",
            color=SECONDARY,
            zorder=3,
            label="bottle fired",
        )
    axis.invert_yaxis()
    axis.set_xlabel("Elapsed time [min]")
    axis.set_ylabel("Pressure [dbar]")
    axis.grid(alpha=0.3)
    if axis.get_legend_handles_labels()[0]:
        axis.legend(fontsize=7)
    axis.set_title(f"{data.name}: pressure", fontsize=10)
    return figure


LAND = "#ece6d6"
WATER = "#dbe9f4"
COAST = "#7a7a7a"
# [degrees] smallest half-width of the map view, so a compact cruise still shows its coastline
MAP_MIN_HALF_SPAN = 0.15


def _map_extent(
    latitudes: list[float], longitudes: list[float]
) -> tuple[float, float, float, float]:
    """(west, east, south, north) around the points with a margin, at least MAP_MIN_HALF_SPAN"""
    center_lat = (max(latitudes) + min(latitudes)) / 2
    center_lon = (max(longitudes) + min(longitudes)) / 2
    half_lat = max((max(latitudes) - min(latitudes)) * 0.6, MAP_MIN_HALF_SPAN)
    half_lon = max((max(longitudes) - min(longitudes)) * 0.6, MAP_MIN_HALF_SPAN)
    return (
        center_lon - half_lon,
        center_lon + half_lon,
        center_lat - half_lat,
        center_lat + half_lat,
    )


def _emptiest_corner(points: list[tuple[float, float]], extent) -> str:
    """The view quadrant with the fewest (lat, lon) points, for placing the world inset"""
    west, east, south, north = extent
    mid_lon, mid_lat = (west + east) / 2, (south + north) / 2
    counts = {
        "lower left": sum(1 for lat, lon in points if lat < mid_lat and lon < mid_lon),
        "lower right": sum(1 for lat, lon in points if lat < mid_lat and lon >= mid_lon),
        "upper left": sum(1 for lat, lon in points if lat >= mid_lat and lon < mid_lon),
        "upper right": sum(1 for lat, lon in points if lat >= mid_lat and lon >= mid_lon),
    }
    return min(counts, key=counts.get)


def _world_inset(figure: Figure, axis, corner: str, latitude: float, longitude: float) -> None:
    """A small world map marking the cruise, in a corner of the cast map"""
    width, height = 0.3, 0.3
    x = 0.02 if "left" in corner else 0.98 - width
    y = 0.02 if "lower" in corner else 0.98 - height
    inset = axis.inset_axes(
        (x, y, width, height), projection=ccrs.Robinson(central_longitude=longitude)
    )
    inset.set_global()
    inset.set_facecolor(WATER)
    land = maps.world_geometries("physical", "land")
    if land:
        inset.add_geometries(
            land, ccrs.PlateCarree(), facecolor=LAND, edgecolor=COAST, linewidth=0.2
        )
    inset.plot(
        longitude,
        latitude,
        marker="o",
        markersize=5,
        color=SECONDARY,
        markeredgecolor="white",
        markeredgewidth=0.6,
        transform=ccrs.PlateCarree(),
    )
    inset.spines["geo"].set_edgecolor("0.5")


def cast_map(casts: list[CastData], config: CruiseConfig) -> Figure:
    """Cast positions over coastlines, against the cruise bounding box used by the NAV test,
    with a world inset showing where the cruise was
    """
    extent = config.extent
    positions = [(c, cast_position(c)) for c in casts]
    positions = [(c, p) for c, p in positions if p is not None]
    box = (
        []
        if extent is None
        else [(extent.southernmost, extent.westernmost), (extent.northernmost, extent.easternmost)]
    )
    latitudes = [lat for lat, _ in box] + [p[0] for _, p in positions]
    longitudes = [lon for _, lon in box] + [p[1] for _, p in positions]
    if not latitudes:  # no extent and no cast positions
        latitudes, longitudes = [0.0], [0.0]
    view = _map_extent(latitudes, longitudes)
    center_lat, center_lon = (view[2] + view[3]) / 2, (view[0] + view[1]) / 2

    figure = plt.figure(figsize=(PAGE[0] - 1, 5.2), layout="constrained")
    axis = figure.add_subplot(projection=ccrs.Mercator(central_longitude=center_lon))
    axis.set_extent(view, crs=ccrs.PlateCarree())
    axis.set_facecolor(WATER)
    data_crs = ccrs.PlateCarree()

    # Natural Earth 10m layers, clipped to a slightly larger window than the view
    window = (view[0] - 0.5, view[1] + 0.5, view[2] - 0.5, view[3] + 0.5)
    land = maps.clipped_geometries("physical", "land", "10m", window)
    if land:
        axis.add_geometries(land, data_crs, facecolor=LAND, edgecolor="none", zorder=0.5)
    lakes = maps.clipped_geometries("physical", "lakes", "10m", window)
    if lakes:
        axis.add_geometries(
            lakes, data_crs, facecolor=WATER, edgecolor=COAST, linewidth=0.4, zorder=0.6
        )
    coastline = maps.clipped_geometries("physical", "coastline", "10m", window)
    if coastline:
        axis.add_geometries(
            coastline, data_crs, facecolor="none", edgecolor=COAST, linewidth=0.5, zorder=0.7
        )
    if land is None:
        axis.text(
            0.01,
            0.01,
            "coastline data unavailable",
            transform=axis.transAxes,
            fontsize=6,
            color="0.4",
        )

    if extent is not None:
        axis.add_patch(
            Rectangle(
                (extent.westernmost, extent.southernmost),
                extent.easternmost - extent.westernmost,
                extent.northernmost - extent.southernmost,
                fill=False,
                ls="--",
                color="0.3",
                label="cruise extent",
                transform=data_crs,
                zorder=2,
            )
        )

    # Deck tests first (hollow, grey) so casts at the same spot stay visible on top
    ordered = sorted(positions, key=lambda item: not item[0].cast.is_deck_test)
    for data, (lat, lon) in ordered:
        if data.cast.is_deck_test:
            axis.scatter(
                lon,
                lat,
                marker="s",
                s=60,
                facecolors="none",
                edgecolors="0.45",
                transform=data_crs,
                zorder=3,
            )
        else:
            inside = extent is None or extent.contains(lat, lon)
            axis.scatter(
                lon,
                lat,
                marker="o",
                color=PRIMARY if inside else SECONDARY,
                edgecolors="white",
                linewidths=0.5,
                transform=data_crs,
                zorder=4,
            )

    gridlines = axis.gridlines(draw_labels=True, linewidth=0.3, color="0.6", alpha=0.6)
    gridlines.top_labels = gridlines.right_labels = False
    gridlines.xlabel_style = gridlines.ylabel_style = {"size": 7}
    corner = _emptiest_corner([p for _, p in positions], view)
    legend_corner = {
        "upper right": "lower left",
        "upper left": "lower right",
        "lower right": "upper left",
        "lower left": "upper right",
    }[corner]
    if axis.get_legend_handles_labels()[0]:
        axis.legend(fontsize=7, loc=legend_corner)
    _world_inset(figure, axis, corner, center_lat, center_lon)
    axis.set_title(
        "Cast positions (hollow squares are deck tests; red is outside the extent)", fontsize=10
    )
    _label_casts(figure, axis, ordered)
    return figure


def _label_casts(figure: Figure, axis, ordered) -> None:
    """Labels each cast beside its marker, moving a label down until it doesn't overlap the
    labels already placed. Science casts are labeled first, from north to south
    """
    figure.canvas.draw()  # settle the layout so screen positions are final
    to_screen = ccrs.PlateCarree()._as_mpl_transform(axis)
    points_per_pixel = 72 / figure.dpi
    font_size, line = 6, 7.5
    placed: list[tuple[float, float, float, float]] = []  # label boxes in points
    labels = sorted(ordered, key=lambda item: (item[0].cast.is_deck_test, -item[1][0]))
    for data, (lat, lon) in labels:
        x, y = to_screen.transform((lon, lat)) * points_per_pixel
        width = 0.6 * font_size * len(data.name)
        offset = 3.0
        while any(
            x + 5 < right
            and x + 5 + width > left
            and y + offset < top
            and y + offset + line > bottom
            for left, bottom, right, top in placed
        ):
            offset -= line
        placed.append((x + 5, y + offset, x + 5 + width, y + offset + line))
        axis.annotate(
            data.name,
            (lon, lat),
            xycoords=to_screen,
            fontsize=font_size,
            xytext=(5, offset),
            textcoords="offset points",
            color="0.35" if data.cast.is_deck_test else "k",
            zorder=5,
        )
