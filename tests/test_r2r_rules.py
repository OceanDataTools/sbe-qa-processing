import pytest

from sbe_qa_processing.fileset import Cast
from sbe_qa_processing.r2r import BLACK, GREEN, GREY, RED, YELLOW, TestResult, overall_rating


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
