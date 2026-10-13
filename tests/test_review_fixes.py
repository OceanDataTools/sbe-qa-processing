"""Regressions for the code review findings (issues 39-48)."""

from pathlib import Path

import numpy as np

from sbe_qa_processing import qa
from sbe_qa_processing.cast import CastData
from sbe_qa_processing.fileset import Cast, Fileset

FILE = Path("x")  # never opened


def test_conductivity_failure_doesnt_stop_the_run(tmp_path, monkeypatch):
    """Issue 39"""
    cast = Cast("Station1", FILE, FILE, FILE)

    def read_cast(c):
        n = 100
        return CastData(
            cast=c,
            time=np.arange(n) / 24,
            pressure=np.linspace(0, 50, n),
            temperature=np.full(n, 10.0),
            errors={"conversion": "conductivity: no G-J coefficients"},
        )

    monkeypatch.setattr(qa, "read_cast", read_cast)
    result = qa.run_qa(Fileset(tmp_path, tmp_path, [cast]))
    (data,) = result.casts
    assert not data.converted and result.science == {}
    readable = {c.name: c for c in result.integrity.casts["Station1"]}["Files readable"]
    assert (readable.status, readable.value) == ("fail", "failed: conversion")
