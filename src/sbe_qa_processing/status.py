"""SBE 911plus deck unit status bits.

The 4-bit status nibble that follows the pressure sensor temperature in each scan is, from the
least significant bit: pump on, no bottom contact, bottle-fire confirm, modem carrier lost. In
real data, deck tests read 0b0010 (pump off) and in-water casts 0b0011, with 0b0111 while bottles
fire.

seabirdscientific before the status-bit fix (fork issue 24) read the nibble most significant bit
first, so its "SBE911 pump status" was really the modem bit. The checkout this project uses may
be either version, so status_fields_reversed() asks seabirdscientific how it decodes a scan with
only the pump bit set, and reconstruct_nibble() undoes the old mapping when needed.
"""

from functools import cache

import numpy as np
from seabirdscientific import instrument_data as idata
from seabirdscientific.instrument_data import Sensors

# seabirdscientific's field for each bit, most significant first
FIELDS_MSB_FIRST = (
    "SBE911 modem status",
    "SBE911 confirm status",
    "SBE911 bottom contact status",
    "SBE911 pump status",
)

_ALL_CHANNELS = (
    Sensors.SecondaryTemperature,
    Sensors.SecondaryConductivity,
    *(getattr(Sensors, f"ExtVolt{n}") for n in range(8)),
)


def probe_scan(nibble: int) -> str:
    """A 911plus scan with all 5 frequencies and 4 voltage words, no NMEA, and the given
    status nibble
    """
    return "0" * 30 + "0" * 24 + "000" + f"{nibble:X}" + "00"


@cache
def status_fields_reversed() -> bool:
    """True if the installed seabirdscientific reads the status nibble most significant first"""
    fields = idata.read_sbe911plus_data(probe_scan(0b0001), _ALL_CHANNELS)
    return fields["SBE911 pump status"] == 0 and fields["SBE911 modem status"] == 1


def reconstruct_nibble(dataset) -> np.ndarray | None:
    """The raw status nibble per scan, from seabirdscientific's four status fields"""
    if not all(name in dataset for name in FIELDS_MSB_FIRST):
        return None
    order = FIELDS_MSB_FIRST[::-1] if status_fields_reversed() else FIELDS_MSB_FIRST
    nibble = np.zeros(dataset.sizes["scan"], dtype=int)
    for name in order:
        nibble = (nibble << 1) | dataset[name].values.astype(int)
    return nibble


def pump_on(nibble: np.ndarray) -> np.ndarray:
    return (nibble & 0b0001).astype(bool)


def bottom_contact(nibble: np.ndarray) -> np.ndarray:
    # the bit is set when there is no contact
    return ~(nibble & 0b0010).astype(bool)


def bottle_confirm(nibble: np.ndarray) -> np.ndarray:
    return (nibble & 0b0100).astype(bool)
