from datetime import date

import numpy as np
import pytest

from sbe_qa_processing.cast import CastData
from sbe_qa_processing.config import CruiseConfig, Extent
from sbe_qa_processing.fileset import Cast
from sbe_qa_processing.r2r import (
    BLACK,
    GREEN,
    GREY,
    RED,
    YELLOW,
    TestResult,
    location_test,
    overall_rating,
)


def result(rating, passed=0, total=0):
    return TestResult("t", "", rating, [], passed=passed, total=total)


@pytest.mark.parametrize(
    "ratings, expected",
    [
        ([result(GREEN, 6, 6), result(GREEN, 26, 26)], GREEN),
        # RR2605: one cast of three outside the cruise extent
        (
            [
                result(GREEN, 6, 6),
                result(GREEN, 26, 26),
                result(YELLOW, 2, 3),
                result(GREEN, 3, 3),
            ],
            YELLOW,
        ),
        ([result(RED, 0, 5), result(GREEN, 1, 1)], RED),
        ([result(GREEN, 1, 1), result(BLACK)], BLACK),
        ([result(GREEN, 1, 1), result(GREY)], GREY),
    ],
)
def test_overall_rating(ratings, expected):
    assert overall_rating(ratings) == expected


@pytest.mark.parametrize(
    "name, deck",
    [
        ("SP2613_DeckTest", True),
        ("RR2605_decktest001", True),
        ("05082018_DockTest", True),
        ("SP2613_Station1", False),
        ("RR2605_cast001", False),
    ],
)
def test_deck_test_names(name, deck):
    assert Cast(name).is_deck_test is deck


@pytest.mark.parametrize(
    "latitude, longitude, inside",
    [
        (32.6, -117.3, True),
        (32.6, -117.5, False),
        (33.0, -117.3, False),
    ],
)
def test_extent_contains(latitude, longitude, inside):
    assert Extent(-117.38, -117.22, 32.59, 32.71).contains(latitude, longitude) is inside


@pytest.mark.parametrize(
    "longitude, inside",
    [(179.5, True), (178.0, True), (180.0, True), (-180.0, True), (-179.0, True), (0.0, False)],
)
def test_extent_across_the_antimeridian(longitude, inside):
    # R2R's convention: westernmost > easternmost
    extent = Extent(178.0, -178.0, -20.0, -15.0)
    assert extent.crosses_antimeridian
    assert extent.contains(-17.0, longitude) is inside
    assert not extent.contains(-21.0, 179.5)


def test_location_test_across_the_antimeridian():
    def cast(name, latitude, longitude):
        # one scan with an appended NMEA position
        return CastData(
            cast=Cast(name), latitude=np.array([latitude]), longitude=np.array([longitude])
        )

    config = CruiseConfig(
        cruise_id="TEST",
        depart_date=date(2026, 7, 9),
        arrive_date=date(2026, 7, 10),
        extent=Extent(178.0, -178.0, -20.0, -15.0),
    )
    casts = [cast("Station1", -17.0, 179.5), cast("Station2", -17.5, -179.2)]
    result = location_test(casts, config)
    assert (result.rating, result.passed, result.total) == (GREEN, 2, 2)
    casts.append(cast("Station3", -17.0, 170.0))
    assert location_test(casts, config).rating == YELLOW
