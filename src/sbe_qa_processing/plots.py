"""Figures for the PDF report and the notebook. Each function returns a matplotlib Figure."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import cartopy.crs as ccrs
import gsw
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from matplotlib.ticker import FixedLocator, MaxNLocator
from seabirdscientific.contour import contour_from_t_s_p

from sbe_qa_processing import maps
from sbe_qa_processing.bottles import bottle_fires
from sbe_qa_processing.cast import CastData
from sbe_qa_processing.config import QAConfig
from sbe_qa_processing.integrity import cast_position, valid_position
from sbe_qa_processing.science import downcast_slice
from sbe_qa_processing.theme import DEFAULT_THEME, Theme

FIGURE_WIDTH = 7.5  # [in] US letter less margins; the PDF scales figures to its frame
TS_MAX_POINTS = 4000
TS_MIN_SALINITY = 0.01  # [PSU] samples at or below this are left out of the TS diagram


def _font_family(path: Path) -> str:
    """matplotlib's name for a TTF, registering it"""
    font_manager.fontManager.addfont(str(path))
    return font_manager.FontProperties(fname=str(path)).get_name()


@contextmanager
def themed(theme: Theme) -> Iterator[None]:
    """matplotlib settings for the theme's fonts, while a figure is made"""
    settings = {}
    if theme.font is not None:
        settings["font.family"] = _font_family(theme.font)
        if theme.bold_font is not None:
            _font_family(theme.bold_font)  # the same family's bold, for bold text
    if theme.figure_font_scale != 1:
        settings["font.size"] = plt.rcParams["font.size"] * theme.figure_font_scale
    with plt.rc_context(settings):
        yield


def _pt(theme: Theme, size: float) -> float:
    """A figure font size [pt] scaled by the theme"""
    return size if theme.figure_font_scale == 1 else size * theme.figure_font_scale


def _downcast(data: CastData, values: np.ndarray | None) -> np.ndarray | None:
    return None if values is None else values[downcast_slice(data)]


def _running_median(values: np.ndarray, window: int) -> np.ndarray:
    if window < 2 or len(values) < window:
        return values
    padded = np.pad(values, window // 2, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(padded, window), axis=1)[
        : len(values)
    ]


def profiles(data: CastData, theme: Theme = DEFAULT_THEME) -> Figure:
    """Downcast temperature, salinity and conductivity for both sensors, and their differences"""
    with themed(theme):
        return _profiles(data, theme)


def _profiles(data: CastData, theme: Theme) -> Figure:
    colors = theme.colors
    figure, axes = plt.subplots(1, 4, figsize=(7.5, 9.5), sharey=True, layout="constrained")
    pressure = _downcast(data, data.pressure)
    for axis, label, primary, secondary in (
        (axes[0], "Temperature [°C]", data.temperature, data.temperature2),
        (axes[1], "Salinity [PSU]", data.salinity, data.salinity2),
        (axes[2], "Conductivity [S/m]", data.conductivity, data.conductivity2),
    ):
        axis.plot(
            _downcast(data, primary), pressure, color=colors.primary, lw=0.8, label="primary"
        )
        if secondary is not None:
            axis.plot(
                _downcast(data, secondary),
                pressure,
                color=colors.secondary,
                lw=0.8,
                alpha=0.8,
                label="secondary",
            )
        axis.set_xlabel(label)
        axis.grid(alpha=0.3)
    axes[0].set_ylabel("Pressure [dbar]")
    axes[0].invert_yaxis()
    axes[0].legend(loc="lower right", fontsize=_pt(theme, 7))

    axis = axes[3]
    if data.has_secondary:
        # Raw 24 Hz differences faintly, with a 2 s running median on top
        window = max(round(2 / data.config.sample_interval), 1)
        for difference, color, label in (
            (data.temperature - data.temperature2, colors.primary, "ΔT [°C]"),
            (data.salinity - data.salinity2, colors.secondary, "ΔS [PSU]"),
        ):
            down = _downcast(data, difference)
            axis.plot(down, pressure, color=color, lw=0.3, alpha=0.25)
            axis.plot(_running_median(down, window), pressure, color=color, lw=0.9, label=label)
        axis.axvline(0, color="k", lw=0.5)
        axis.set_xlim(-0.05, 0.05)
        axis.set_xticks([-0.04, 0, 0.04])
        axis.legend(loc="lower right", fontsize=_pt(theme, 7))
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
    figure.suptitle(f"{data.name}: downcast profiles", fontsize=_pt(theme, 11))
    return figure


def _padded_range(values: np.ndarray, minimum_pad: float) -> tuple[float, float]:
    low, high = float(np.nanmin(values)), float(np.nanmax(values))
    pad = max(0.08 * (high - low), minimum_pad)
    return low - pad, high + pad


def ts_diagram(data: CastData, theme: Theme = DEFAULT_THEME) -> Figure:
    """Conservative temperature vs absolute salinity with σ0 contours (seabirdscientific)"""
    with themed(theme):
        return _ts_diagram(data, theme)


def _ts_diagram(data: CastData, theme: Theme) -> Figure:
    figure, axis = plt.subplots(figsize=(7.5, 5.5), layout="constrained")
    position = cast_position(data)
    lat, lon = position if position else (0.0, 0.0)
    down = downcast_slice(data)
    salinity = data.salinity[down]
    if not np.any(np.isfinite(salinity) & (salinity > TS_MIN_SALINITY)):
        # e.g. a dead conductivity cell; seabirdscientific's contour grid can't be built from
        # no samples, and the salinity range check already fails the cast
        axis.text(
            0.5,
            0.5,
            f"no salinity above {TS_MIN_SALINITY} PSU for a TS diagram",
            ha="center",
            va="center",
            transform=axis.transAxes,
            fontsize=_pt(theme, 9),
            color="0.4",
        )
        axis.set_xlabel("Absolute salinity [g/kg]")
        axis.set_ylabel("Conservative temperature [°C]")
        axis.set_title(f"{data.name}: TS (σ0 contours)", fontsize=_pt(theme, 10))
        return figure
    contour = contour_from_t_s_p(
        data.temperature[down],
        salinity,
        data.pressure[down],
        min_salinity=TS_MIN_SALINITY,
        lat=lat,
        lon=lon,
    )
    # View the data with a small margin rather than the whole density grid, whose fixed margins
    # dwarf a narrow (e.g. freshwater) cast
    x_view = _padded_range(contour.x, minimum_pad=0.02)
    y_view = _padded_range(contour.y, minimum_pad=0.1)
    axis.set_xlim(*x_view)
    axis.set_ylim(*y_view)
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
    axis.clabel(lines, fontsize=_pt(theme, 6), fmt=lambda level: f"{level:g}")
    # At most TS_MAX_POINTS evenly spaced scans: indistinguishable at page scale, and keeps the
    # SVG small (a deep cast has ~80,000 downcast scans)
    keep = np.unique(np.linspace(0, len(contour.x) - 1, TS_MAX_POINTS).astype(int))
    points = axis.scatter(
        contour.x[keep], contour.y[keep], c=data.pressure[down][keep], s=1, cmap="viridis_r"
    )
    figure.colorbar(points, ax=axis, label="Pressure [dbar]")
    axis.set_xlabel("Absolute salinity [g/kg]")
    axis.set_ylabel("Conservative temperature [°C]")
    axis.set_title(f"{data.name}: TS (σ0 contours)", fontsize=_pt(theme, 10))
    return figure


def pressure_series(data: CastData, theme: Theme = DEFAULT_THEME) -> Figure:
    """Pressure over time with pump status and bottle fires"""
    with themed(theme):
        return _pressure_series(data, theme)


def _pressure_series(data: CastData, theme: Theme) -> Figure:
    colors = theme.colors
    figure, axis = plt.subplots(figsize=(7.5, 4.3), layout="constrained")
    minutes = data.time / 60
    axis.plot(minutes, data.pressure, color=colors.primary, lw=0.8)
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
            color=colors.secondary,
            zorder=3,
            label="bottle fired",
        )
    axis.invert_yaxis()
    axis.set_xlabel("Elapsed time [min]")
    axis.set_ylabel("Pressure [dbar]")
    axis.grid(alpha=0.3)
    if axis.get_legend_handles_labels()[0]:
        axis.legend(fontsize=_pt(theme, 7))
    axis.set_title(f"{data.name}: pressure", fontsize=_pt(theme, 10))
    return figure


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


def _wraps_longitudes(extent, longitudes: list[float]) -> bool:
    """Whether the map is drawn in 0-360° longitudes instead of ±180°: when the cruise extent
    crosses the antimeridian, or, without an extent, when the casts are closer together that way
    """
    if extent is not None:
        return extent.crosses_antimeridian
    if not longitudes:
        return False
    span = lambda values: max(values) - min(values)
    return span([lon % 360 for lon in longitudes]) < span(longitudes)


def _corners_by_emptiness(points: list[tuple[float, float]], extent) -> list[str]:
    """The view's corners, the one whose quadrant has the fewest (lat, lon) points first: the
    order in which the world inset tries them
    """
    west, east, south, north = extent
    mid_lon, mid_lat = (west + east) / 2, (south + north) / 2
    counts = {
        "lower left": sum(1 for lat, lon in points if lat < mid_lat and lon < mid_lon),
        "lower right": sum(1 for lat, lon in points if lat < mid_lat and lon >= mid_lon),
        "upper left": sum(1 for lat, lon in points if lat >= mid_lat and lon < mid_lon),
        "upper right": sum(1 for lat, lon in points if lat >= mid_lat and lon >= mid_lon),
    }
    return sorted(counts, key=counts.get)


Rect = tuple[float, float, float, float]  # (x, y, width, height) in axes coordinates

# The legend's places on the map, in the order they're tried after the corner opposite the inset
LEGEND_PLACES = (
    "upper right",
    "upper left",
    "lower left",
    "lower right",
    "upper center",
    "lower center",
    "center left",
    "center right",
)
INSET_EDGES = ("upper center", "lower center", "center left", "center right")  # after corners
OPPOSITE = {
    "upper right": "lower left",
    "upper left": "lower right",
    "lower right": "upper left",
    "lower left": "upper right",
}
INSET_SIZES = (0.3, 0.2)  # [axes fraction] the inset's width and height, tried in order
INSET_PAD = 0.02  # [axes fraction] the inset's distance from the map's edges
CLEARANCE = 0.02  # [axes fraction] kept between a cast marker and a legend or the inset


def _corner_rect(place: str, width: float, height: float, pad_x: float, pad_y: float) -> Rect:
    """The rectangle of a legend or inset of this size at a matplotlib legend location"""
    if "left" in place:
        x = pad_x
    elif "right" in place:
        x = 1 - pad_x - width
    else:
        x = (1 - width) / 2
    if "lower" in place:
        y = pad_y
    elif "upper" in place:
        y = 1 - pad_y - height
    else:
        y = (1 - height) / 2
    return x, y, width, height


def _covers(rect: Rect, points: np.ndarray, margin: float = CLEARANCE) -> bool:
    """Whether any of the points (axes coordinates) is within margin of the rectangle"""
    x, y, width, height = rect
    inside = (
        (points[:, 0] >= x - margin)
        & (points[:, 0] <= x + width + margin)
        & (points[:, 1] >= y - margin)
        & (points[:, 1] <= y + height + margin)
    )
    return bool(inside.any())


def _overlap(a: Rect, b: Rect) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def _place_legend_and_inset(
    figure: Figure,
    axis,
    positions: list[tuple[float, float]],
    corners: list[str],
    latitude: float,
    longitude: float,
    theme: Theme,
) -> None:
    """The legend (when there is one) and the world inset where they cover no cast, each
    candidate place checked against the casts' (lat, lon). In order of preference: the inset
    and the legend each in a clear place on the map; the inset with the legend below the map;
    the legend on the map without the inset; the legend below the map without it.

    Nothing is drawn to find them: the casts' axes coordinates follow from the map's extent, and
    the legend's size from a renderer, so the layout is worked out once, when the figure is
    saved, as it was before the placement was checked
    """
    if positions:
        lats, lons = np.array(positions, dtype=float).T
        projected = axis.projection.transform_points(ccrs.PlateCarree(), lons, lats)
        points = axis.transLimits.transform(projected[:, :2])
        points = points[np.isfinite(points).all(axis=1)]
    else:
        points = np.empty((0, 2))
    insets = [  # (place, rect) clear of the casts, by preference
        (place, rect)
        for size in INSET_SIZES
        for place in [*corners, *INSET_EDGES]
        if not _covers(rect := _corner_rect(place, size, size, INSET_PAD, INSET_PAD), points)
    ]
    handles, labels = axis.get_legend_handles_labels()
    if not handles:
        if insets:
            _world_inset(axis, insets[0][1], latitude, longitude, theme)
        return

    # The legend's size, from one measured and removed
    renderer = figure.canvas.get_renderer()
    measured = axis.legend(fontsize=_pt(theme, 7))
    box, area = measured.get_window_extent(renderer), axis.get_window_extent(renderer)
    pad = measured.borderaxespad * measured.prop.get_size_in_points() * figure.dpi / 72
    measured.remove()
    width, height = box.width / area.width, box.height / area.height
    pad_x, pad_y = pad / area.width, pad / area.height

    def legend_place(inset: tuple[str, Rect] | None) -> str | None:
        first = [OPPOSITE[inset[0]]] if inset and inset[0] in OPPOSITE else []
        for place in first + [p for p in LEGEND_PLACES if p not in first]:
            rect = _corner_rect(place, width, height, pad_x, pad_y)
            if not _covers(rect, points) and not (inset and _overlap(rect, inset[1])):
                return place
        return None

    # Keep the inset if anywhere is clear for it, moving the legend below the map if need be
    inset = next((i for i in insets if legend_place(i) is not None), None)
    inset = inset or (insets[0] if insets else None)
    place = legend_place(inset)
    if place is not None:
        axis.legend(fontsize=_pt(theme, 7), loc=place)
    else:
        figure.legend(handles, labels, loc="outside lower center", fontsize=_pt(theme, 7))
    if inset is not None:
        _world_inset(axis, inset[1], latitude, longitude, theme)


def _world_inset(axis, rect: Rect, latitude: float, longitude: float, theme: Theme) -> None:
    """A small world map marking the cruise, at rect on the cast map"""
    colors = theme.colors
    inset = axis.inset_axes(rect, projection=ccrs.Robinson(central_longitude=longitude))
    inset.set_global()
    inset.set_facecolor(colors.water)
    land = maps.world_geometries("physical", "land")
    if land:
        inset.add_geometries(
            land, ccrs.PlateCarree(), facecolor=colors.land, edgecolor=colors.coast, linewidth=0.2
        )
    inset.plot(
        longitude,
        latitude,
        marker="o",
        markersize=5,
        color=colors.secondary,
        markeredgecolor="white",
        markeredgewidth=0.6,
        transform=ccrs.PlateCarree(),
    )
    inset.spines["geo"].set_edgecolor("0.5")


def cast_map(casts: list[CastData], config: QAConfig, theme: Theme = DEFAULT_THEME) -> Figure:
    """Cast positions over coastlines, against the expected area when the config has one, with
    a world inset showing where the casts were. The legend and inset go where they cover no
    cast; the inset is left out when there's no such place
    """
    with themed(theme):
        return _cast_map(casts, config, theme)


def _cast_map(casts: list[CastData], config: QAConfig, theme: Theme) -> Figure:
    colors = theme.colors
    extent = config.extent
    # Invalid positions (0,0 from a blank NMEA, out of range) would stretch the view; the
    # integrity check "Cast position" reports them
    positions = [(c, cast_position(c)) for c in casts]
    positions = [(c, p) for c, p in positions if p is not None and valid_position(*p)]
    box = (
        []
        if extent is None
        else [(extent.southernmost, extent.westernmost), (extent.northernmost, extent.easternmost)]
    )
    latitudes = [lat for lat, _ in box] + [p[0] for _, p in positions]
    longitudes = [lon for _, lon in box] + [p[1] for _, p in positions]
    if not latitudes:  # no extent and no cast positions
        latitudes, longitudes = [0.0], [0.0]
    # The view is worked out in longitudes that don't jump at 180°, e.g. 178 to 182
    wraps = _wraps_longitudes(extent, longitudes)
    unwrap = (lambda lon: lon % 360) if wraps else (lambda lon: lon)
    view = _map_extent(latitudes, [unwrap(lon) for lon in longitudes])
    center_lat, center_lon = (view[2] + view[3]) / 2, (view[0] + view[1]) / 2
    # The view, the extent box and the coastlines are drawn relative to the center, so they
    # stay whole across the antimeridian; cast positions are points and need no shift
    central = (center_lon + 180) % 360 - 180  # the same meridian, within ±180
    frame = ccrs.PlateCarree(central_longitude=central)
    relative = (view[0] - center_lon, view[1] - center_lon, view[2], view[3])

    figure = plt.figure(figsize=(FIGURE_WIDTH, 5.2), layout="constrained")
    axis = figure.add_subplot(projection=ccrs.Mercator(central_longitude=central))
    axis.set_extent(relative, crs=frame)
    axis.set_facecolor(colors.water)
    data_crs = ccrs.PlateCarree()

    # Natural Earth 10m layers, clipped to a slightly larger window than the view
    window = (view[0] - 0.5, view[1] + 0.5, view[2] - 0.5, view[3] + 0.5)
    layer = lambda name: maps.clipped_geometries("physical", name, "10m", window, center_lon)
    land = layer("land")
    if land:
        axis.add_geometries(land, frame, facecolor=colors.land, edgecolor="none", zorder=0.5)
    lakes = layer("lakes")
    if lakes:
        axis.add_geometries(
            lakes, frame, facecolor=colors.water, edgecolor=colors.coast, linewidth=0.4, zorder=0.6
        )
    coastline = layer("coastline")
    if coastline:
        axis.add_geometries(
            coastline, frame, facecolor="none", edgecolor=colors.coast, linewidth=0.5, zorder=0.7
        )
    if land is None:
        axis.text(
            0.01,
            0.01,
            "coastline data unavailable",
            transform=axis.transAxes,
            fontsize=_pt(theme, 6),
            color="0.4",
        )

    if extent is not None:
        axis.add_patch(
            Rectangle(
                (unwrap(extent.westernmost) - center_lon, extent.southernmost),
                unwrap(extent.easternmost) - unwrap(extent.westernmost),
                extent.northernmost - extent.southernmost,
                fill=False,
                ls="--",
                color="0.3",
                label="cruise extent",
                transform=frame,
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
                color=colors.primary if inside else colors.secondary,
                edgecolors="white",
                linewidths=0.5,
                transform=data_crs,
                zorder=4,
            )

    gridlines = axis.gridlines(draw_labels=True, linewidth=0.3, color="0.6", alpha=0.6)
    if wraps:
        # cartopy's default meridians are worked out in ±180°, which leaves only 180° in view
        ticks = MaxNLocator(nbins=6).tick_values(view[0], view[1])
        gridlines.xlocator = FixedLocator([(tick + 180) % 360 - 180 for tick in ticks])
    gridlines.top_labels = gridlines.right_labels = False
    gridlines.xlabel_style = gridlines.ylabel_style = {"size": _pt(theme, 7)}
    corners = _corners_by_emptiness([(lat, unwrap(lon)) for _, (lat, lon) in positions], view)
    _place_legend_and_inset(
        figure, axis, [p for _, p in positions], corners, center_lat, central, theme
    )
    axis.set_title(
        "Cast positions (hollow squares are deck tests"
        + ("; red is outside the extent)" if extent is not None else ")"),
        fontsize=_pt(theme, 10),
    )
    _label_casts(figure, axis, ordered, theme)
    return figure


def _label_casts(figure: Figure, axis, ordered, theme: Theme) -> None:
    """Labels each cast beside its marker, moving a label down until it doesn't overlap the
    labels already placed. Science casts are labeled first, from north to south
    """
    figure.canvas.draw()  # settle the layout so screen positions are final
    to_screen = ccrs.PlateCarree()._as_mpl_transform(axis)
    points_per_pixel = 72 / figure.dpi
    font_size, line = _pt(theme, 6), _pt(theme, 7.5)
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
