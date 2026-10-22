"""SBE 911plus deck unit status bits.

The 4-bit status nibble that follows the pressure sensor temperature in each scan is, from the
least significant bit: pump on, no bottom contact, bottle-fire confirm, modem carrier lost. In
real data, deck tests read 0b0010 (pump off) and in-water casts 0b0011, with 0b0111 while bottles
fire. seabirdscientific reads them in this order from 3.0.0 (fork issue 24).
"""

import numpy as np

# seabirdscientific's field for each bit, most significant first
FIELDS_MSB_FIRST = (
    "SBE911 modem status",
    "SBE911 confirm status",
    "SBE911 bottom contact status",
    "SBE911 pump status",
)


def reconstruct_nibble(dataset) -> np.ndarray | None:
    """The raw status nibble per scan, from seabirdscientific's four status fields"""
    if not all(name in dataset for name in FIELDS_MSB_FIRST):
        return None
    nibble = np.zeros(dataset.sizes["scan"], dtype=int)
    for name in FIELDS_MSB_FIRST:
        nibble = (nibble << 1) | dataset[name].values.astype(int)
    return nibble


def pump_on(nibble: np.ndarray) -> np.ndarray:
    return (nibble & 0b0001).astype(bool)


def bottom_contact(nibble: np.ndarray) -> np.ndarray:
    # the bit is set when there is no contact
    return ~(nibble & 0b0010).astype(bool)


def bottle_confirm(nibble: np.ndarray) -> np.ndarray:
    return (nibble & 0b0100).astype(bool)
