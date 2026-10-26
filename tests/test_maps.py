import matplotlib.pyplot as plt
import numpy as np
import pytest

from sbe_qa_processing import maps, plots
from sbe_qa_processing.cast import CastData
from sbe_qa_processing.config import Extent, QAConfig
from sbe_qa_processing.fileset import Cast
from sbe_qa_processing.hdr import Header


def config():
    return QAConfig(
        cruise_id="TEST",
        depart_date=__import__("datetime").date(2026, 7, 9),
        arrive_date=__import__("datetime").date(2026, 7, 9),
        extent=Extent(-117.38, -117.22, 32.59, 32.71),
    )


def casts():
    def cast(name, lat_text, lon_text):
        header = Header(values={"nmea latitude": lat_text, "nmea longitude": lon_text})
        return CastData(cast=Cast(name), header=header)

    return [
        cast("STATION1", "32 36.25 N", "117 22.04 W"),
        cast("STATION2", "32 36.06 N", "117 21.90 W"),
        cast("DeckTest", "32 42.50 N", "117 14.20 W"),
    ]


def text_of(figure):
    return [t.get_text() for ax in figure.axes for t in ax.texts]


def test_map_without_coastline_data(monkeypatch):
    # e.g. at sea without the Natural Earth layers downloaded
    monkeypatch.setattr(maps, "_feature", lambda *a: (_ for _ in ()).throw(OSError("offline")))
    figure = plots.cast_map(casts(), config())
    assert "coastline data unavailable" in text_of(figure)
    labels = [t.get_text() for t in figure.axes[0].texts]
    assert {"STATION1", "STATION2", "DeckTest"} <= set(labels)
    plt.close(figure)


def test_clipped_geometries_are_inside_the_window(monkeypatch):
    from shapely.geometry import box

    class Feature:
        def intersecting_geometries(self, extent):
            return [box(-120, 30, -110, 40)]  # much larger than the window

    monkeypatch.setattr(maps, "_feature", lambda *a: Feature())
    (geometry,) = maps.clipped_geometries("physical", "land", "10m", (-118, -117, 32, 33))
    assert np.allclose(geometry.bounds, (-118, 32, -117, 33))


def test_labels_do_not_overlap(monkeypatch):
    monkeypatch.setattr(maps, "_feature", lambda *a: (_ for _ in ()).throw(OSError("offline")))
    figure = plots.cast_map(casts(), config())
    figure.canvas.draw()
    renderer = figure.canvas.get_renderer()
    boxes = [t.get_window_extent(renderer) for t in figure.axes[0].texts if t.get_text() != ""]
    boxes = [b for b in boxes if b.width > 0]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert not a.overlaps(b)
    plt.close(figure)


def test_clipped_geometries_across_the_antimeridian(monkeypatch):
    from shapely.geometry import box

    requested = []

    class Feature:
        def intersecting_geometries(self, extent):
            requested.append(extent)
            return [box(-180, -20, 180, -15)]  # a band around the world

    monkeypatch.setattr(maps, "_feature", lambda *a: Feature())
    # 178 to 182 east, drawn around 180: each side of 180 is looked up on its own
    pieces = maps.clipped_geometries("physical", "land", "10m", (178, 182, -18, -16), 180)
    assert sorted(requested) == [(-180.0, -178.0, -18, -16), (178.0, 180.0, -18, -16)]
    bounds = sorted(piece.bounds for piece in pieces)
    assert np.allclose(bounds, [(-2, -18, 0, -16), (0, -18, 2, -16)])


def test_map_across_the_antimeridian(monkeypatch):
    monkeypatch.setattr(maps, "_feature", lambda *a: (_ for _ in ()).throw(OSError("offline")))
    fiji = QAConfig(
        cruise_id="TEST",
        depart_date=__import__("datetime").date(2026, 7, 9),
        arrive_date=__import__("datetime").date(2026, 7, 9),
        extent=Extent(179.0, -179.5, -17.5, -16.5),
    )

    def cast(name, latitude, longitude):
        return CastData(
            cast=Cast(name), latitude=np.array([latitude]), longitude=np.array([longitude])
        )

    stations = [cast("EAST", -17.0, -179.8), cast("WEST", -17.2, 179.6), cast("FAR", -17.0, 175.0)]
    figure = plots.cast_map(stations, fiji)
    axis = figure.axes[0]
    # The view spans 180 rather than the whole globe
    west, east, _, _ = axis.get_extent(crs=plots.ccrs.PlateCarree(central_longitude=180))
    assert -10 < west < east < 10
    (box,) = [p for p in axis.patches if p.get_label() == "cruise extent"]
    assert box.get_width() == 1.5
    # Casts on both sides of 180 are inside the extent; FAR is outside
    colors = [tuple(c.get_facecolor()[0]) for c in axis.collections]
    assert len(set(colors)) == 2 and colors[0] == colors[1] != colors[2]
    plt.close(figure)


def test_tests_run_without_map_data():
    # conftest sets SBE_QA_NO_MAP_DATA for the session, so nothing is downloaded
    assert maps.clipped_geometries("physical", "land", "10m", (-118, -117, 32, 33)) is None
    assert maps.world_geometries("physical", "land") is None
    figure = plots.cast_map(casts(), config())
    assert "coastline data unavailable" in text_of(figure)
    plt.close(figure)


def test_cached(tmp_path, monkeypatch):
    monkeypatch.setitem(maps.cartopy.config, "data_dir", str(tmp_path))
    assert not maps.cached()
    for scale, category, name in maps._layers():
        shapefile = tmp_path / "shapefiles" / "natural_earth" / category / f"ne_{scale}_{name}.shp"
        shapefile.parent.mkdir(parents=True, exist_ok=True)
        shapefile.touch()
    assert maps.cached()


def test_download_notices_are_logged_not_warned(monkeypatch, caplog):
    import warnings

    from cartopy.io import DownloadWarning

    class Feature:
        def geometries(self):
            warnings.warn("Downloading: ne_110m_land.zip", DownloadWarning, stacklevel=1)
            return []

    monkeypatch.setattr(maps, "_feature", lambda *a: Feature())
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with caplog.at_level("INFO", logger="sbe_qa_processing.maps"):
            assert maps.world_geometries("physical", "land") == []
    assert "Downloading: ne_110m_land.zip" in caplog.text


@pytest.mark.map_data
def test_map_with_coastline_data():
    land = maps.clipped_geometries("physical", "land", "10m", (-117.38, -117.22, 32.59, 32.71))
    assert land  # San Diego
    figure = plots.cast_map(casts(), config())
    assert "coastline data unavailable" not in text_of(figure)
    plt.close(figure)


def casts_at(positions):
    """Casts at (lat, lon) in decimal degrees"""

    def nmea(value, positive, negative):
        degrees, minutes = divmod(abs(value) * 60, 60)
        return f"{int(degrees)} {minutes:.4f} {positive if value >= 0 else negative}"

    return [
        CastData(
            cast=Cast(f"STATION{i}"),
            header=Header(
                values={
                    "nmea latitude": nmea(lat, "N", "S"),
                    "nmea longitude": nmea(lon, "E", "W"),
                }
            ),
        )
        for i, (lat, lon) in enumerate(positions)
    ]


def in_axes(axis, positions):
    """(lat, lon) positions in the map's axes coordinates"""
    import cartopy.crs as ccrs

    lats, lons = np.array(positions, dtype=float).T
    projected = axis.projection.transform_points(ccrs.PlateCarree(), lons, lats)
    return axis.transLimits.transform(projected[:, :2])


def axes_box(axis, artist):
    """An artist's drawn box in the map's axes coordinates, (x0, y0, x1, y1)"""
    box = artist.get_window_extent().transformed(axis.transAxes.inverted())
    return box.x0, box.y0, box.x1, box.y1


def covered(box, points):
    x0, y0, x1, y1 = box
    return [(x, y) for x, y in points if x0 <= x <= x1 and y0 <= y <= y1]


def test_legend_and_inset_cover_no_cast():
    # A cast in every corner of the extent, where the inset and the legend used to go
    extent = config().extent
    corners = [
        (lat, lon)
        for lat in (extent.southernmost, extent.northernmost)
        for lon in (extent.westernmost, extent.easternmost)
    ]
    figure = plots.cast_map(casts_at(corners), config())
    figure.canvas.draw()
    axis = figure.axes[0]
    points = in_axes(axis, corners)
    (inset,) = axis.child_axes
    assert not covered(axes_box(axis, inset), points)
    legend = axis.get_legend()
    assert legend is not None and not covered(axes_box(axis, legend), points)
    plt.close(figure)


@pytest.mark.filterwarnings("error")
def test_a_map_full_of_casts_has_no_inset_and_its_legend_covers_none():
    # The view's margin is clear of casts, so the legend fits there; the inset doesn't
    extent = config().extent
    lats = np.linspace(extent.southernmost - 0.05, extent.northernmost + 0.05, 9)
    lons = np.linspace(extent.westernmost - 0.05, extent.easternmost + 0.05, 9)
    positions = [(lat, lon) for lat in lats for lon in lons]
    figure = plots.cast_map(casts_at(positions), config())
    figure.canvas.draw()
    axis = figure.axes[0]
    assert not axis.child_axes  # no clear place for the inset
    legend = axis.get_legend()
    assert legend is not None and not covered(axes_box(axis, legend), in_axes(axis, positions))
    plt.close(figure)
