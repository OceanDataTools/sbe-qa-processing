import matplotlib.pyplot as plt
import numpy as np

from sbe_qa_processing import maps, plots
from sbe_qa_processing.cast import CastData
from sbe_qa_processing.config import CruiseConfig, Extent
from sbe_qa_processing.fileset import Cast
from sbe_qa_processing.hdr import Header


def config():
    return CruiseConfig(
        cruise_id="TEST",
        fileset_id="1",
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
    fiji = CruiseConfig(
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
