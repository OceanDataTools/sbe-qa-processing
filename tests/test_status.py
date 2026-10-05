"""SBE 911plus status bits, with either seabirdscientific status-bit mapping."""

import numpy as np
import pytest
import xarray as xr
from seabirdscientific import instrument_data as idata

from sbe_qa_processing import status


def decode(nibble: int) -> int:
    fields = idata.read_sbe911plus_data(status.probe_scan(nibble), status._ALL_CHANNELS)
    dataset = xr.Dataset({k: ("scan", [v]) for k, v in fields.items() if "status" in k})
    return int(status.reconstruct_nibble(dataset)[0])


@pytest.mark.parametrize("nibble", range(16))
def test_recovers_raw_nibble_from_installed_seabirdscientific(nibble):
    assert decode(nibble) == nibble


@pytest.mark.parametrize("reversed_fields", [False, True])
def test_recovers_nibble_with_either_mapping(monkeypatch, reversed_fields):
    # Fields as each seabirdscientific version would report 0b0011 (pump on, no bottom contact)
    if reversed_fields:
        values = {"modem": 1, "confirm": 1, "bottom contact": 0, "pump": 0}
    else:
        values = {"modem": 0, "confirm": 0, "bottom contact": 1, "pump": 1}
    dataset = xr.Dataset({f"SBE911 {k} status": ("scan", [v]) for k, v in values.items()})
    monkeypatch.setattr(status, "status_fields_reversed", lambda: reversed_fields)
    assert status.reconstruct_nibble(dataset)[0] == 0b0011


def test_decodes_deck_and_in_water_scans():
    # Real data: deck tests read 0b0010, in-water casts 0b0011, and 0b0111 while bottles fire
    nibbles = np.array([0b0010, 0b0011, 0b0111])
    assert status.pump_on(nibbles).tolist() == [False, True, True]
    assert status.bottle_confirm(nibbles).tolist() == [False, False, True]
    assert status.bottom_contact(nibbles).tolist() == [False, False, False]
