"""SBE 911plus status bits."""

import numpy as np
import pytest
import xarray as xr
from seabirdscientific import instrument_data as idata
from seabirdscientific.instrument_data import Sensors

from sbe_qa_processing import status

ALL_CHANNELS = (
    Sensors.SecondaryTemperature,
    Sensors.SecondaryConductivity,
    *(getattr(Sensors, f"ExtVolt{n}") for n in range(8)),
)


def probe_scan(nibble: int) -> str:
    """A 911plus scan with all 5 frequencies and 4 voltage words, no NMEA, and the given
    status nibble
    """
    return "0" * 30 + "0" * 24 + "000" + f"{nibble:X}" + "00"


def decode(nibble: int) -> int:
    fields = idata.read_sbe911plus_data(probe_scan(nibble), ALL_CHANNELS)
    dataset = xr.Dataset({k: ("scan", [v]) for k, v in fields.items() if "status" in k})
    return int(status.reconstruct_nibble(dataset)[0])


@pytest.mark.parametrize("nibble", range(16))
def test_recovers_raw_nibble_from_installed_seabirdscientific(nibble):
    assert decode(nibble) == nibble


def test_decodes_deck_and_in_water_scans():
    # Real data: deck tests read 0b0010, in-water casts 0b0011, and 0b0111 while bottles fire
    nibbles = np.array([0b0010, 0b0011, 0b0111])
    assert status.pump_on(nibbles).tolist() == [False, True, True]
    assert status.bottle_confirm(nibbles).tolist() == [False, False, True]
    assert status.bottom_contact(nibbles).tolist() == [False, False, False]
