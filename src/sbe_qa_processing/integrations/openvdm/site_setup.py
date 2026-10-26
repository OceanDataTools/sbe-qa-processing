"""Builds the per-ship site TOML for the OpenVDM hook by asking for each field.

Prompts go to stderr, so the TOML alone can go to stdout. Each prompt shows its default in
brackets: Enter keeps it, ``-`` clears it. At the end of input every remaining field keeps its
default, so with no terminal an existing file is written back unchanged.
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TextIO

from sbe_qa_processing.config import Extent
from sbe_qa_processing.integrations.openvdm.hook import SiteConfig

CLEAR = "-"


@dataclass
class Prompter:
    stdin: TextIO = field(default_factory=lambda: sys.stdin)
    out: TextIO = field(default_factory=lambda: sys.stderr)
    ended: bool = False  # end of input: every later prompt keeps its default

    def say(self, text: str) -> None:
        self.out.write(text + "\n")

    def ask(
        self, label: str, default: str = "", check: Callable[[str], str | None] | None = None
    ) -> str:
        """The answer, or default; check returns a problem to re-ask with, or None"""
        while True:
            self.out.write(f"{label} [{default}]: " if default else f"{label}: ")
            self.out.flush()
            line = "" if self.ended else self.stdin.readline()
            if not line:
                self.ended = True
                self.out.write("\n")
                return default
            answer = line.strip()
            answer = "" if answer == CLEAR else answer or default
            problem = check(answer) if check else None
            if problem is None:
                return answer
            if self.ended:  # no one to re-ask; keep the default rather than loop
                return default
            self.say(f"  {problem}")


def _email(text: str) -> str | None:
    return None if not text or ("@" in text and " " not in text) else "not an email address"


def _degrees(limit: float, required: bool = False) -> Callable[[str], str | None]:
    def check(text: str) -> str | None:
        if not text:
            return "required" if required else None
        try:
            value = float(text)
        except ValueError:
            return "not a number"
        return None if -limit <= value <= limit else f"outside ±{limit:g}"

    return check


def _ask_extent(prompt: Prompter, default: Extent | None) -> Extent | None:
    prompt.say(
        "\nExpected cruise area, used when OpenVDM has no cruise extent. "
        "Leave westernmost blank to skip. westernmost > easternmost is a box across the "
        "antimeridian (180°)."
    )
    while True:
        text = lambda edge: str(getattr(default, edge)) if default else ""
        west = prompt.ask("  westernmost longitude [deg E]", text("westernmost"), _degrees(180))
        if not west:
            return None
        values = {"westernmost": west}
        for edge, limit, what in (
            ("easternmost", 180, "longitude [deg E]"),
            ("southernmost", 90, "latitude [deg N]"),
            ("northernmost", 90, "latitude [deg N]"),
        ):
            values[edge] = prompt.ask(f"  {edge} {what}", text(edge), _degrees(limit, True))
            if not values[edge]:  # input ended part way through
                return default
        extent = Extent(**{k: float(v) for k, v in values.items()})
        if extent.southernmost <= extent.northernmost:
            if extent.crosses_antimeridian:
                prompt.say("  westernmost > easternmost: a box across the antimeridian")
            return extent
        prompt.say("  southernmost must be <= northernmost; try again")
        if prompt.ended:
            return default


def prompt_site_config(
    defaults: SiteConfig, prompt: Prompter | None = None, from_openvdm: dict | None = None
) -> SiteConfig:
    """A site config from answers to prompts, starting from defaults. Each vessel field's
    default is the site config's value, or else from_openvdm's (openvdm.yaml's vessel settings,
    by SiteConfig field): the site config's win when the hook runs, and openvdm.yaml fills
    those left blank
    """
    prompt = prompt or Prompter()
    from_openvdm = from_openvdm or {}
    prompt.say(f"Enter keeps the [default]; {CLEAR} clears it.\n")
    if from_openvdm:
        prompt.say("openvdm.yaml's vessel block has these, offered where the site TOML has none:")
        for name, value in from_openvdm.items():
            prompt.say(f"  {name} = {value}")
        prompt.say("")

    def ask(field_name: str, label: str, check=None) -> str:
        default = getattr(defaults, field_name) or from_openvdm.get(field_name, "")
        return prompt.ask(label, default, check)

    site = replace(
        defaults,
        vessel_name=ask("vessel_name", "Vessel name (e.g. Roger Revelle)"),
        contact_institution=ask("contact_institution", "Contact institution for the QA report"),
        contact_email=ask("contact_email", "Contact email", _email),
        ignored=[],
        output_extra_directory=ask(
            "output_extra_directory", "OpenVDM extra directory for the reports"
        ),
    )
    site.extent = _ask_extent(prompt, defaults.extent)
    return site
