"""Builds the per-ship site TOML for the OpenVDM hook by asking for each field.

Prompts go to stderr, so the TOML alone can go to stdout. Each prompt shows its default in
brackets: Enter keeps it, ``-`` clears it. At the end of input every remaining field keeps its
default, so ``--from-r2r`` with no terminal writes the R2R values unchanged.
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TextIO

from sbe_qa_processing.config import Extent, config_from_r2r_api
from sbe_qa_processing.openvdm import SiteConfig

CLEAR = "-"


def site_from_r2r(cruise: dict, base: SiteConfig) -> SiteConfig:
    """base with the vessel, operator and scheduler of a record from R2R's ``api/cruise``"""
    config = config_from_r2r_api(cruise)
    return replace(
        base,
        vessel_id=config.vessel_id or base.vessel_id,
        vessel_name=config.vessel_name or base.vessel_name,
        operator_id=config.operator_id or base.operator_id,
        scheduler_id=config.scheduler_id or base.scheduler_id,
    )


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
        "\nFallback cruise bounding box, used when OpenVDM has no tracklines. "
        "Leave westernmost blank to skip."
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
        if extent.westernmost <= extent.easternmost and extent.southernmost <= extent.northernmost:
            return extent
        prompt.say("  westernmost must be <= easternmost, southernmost <= northernmost; try again")
        if prompt.ended:
            return default


def prompt_site_config(defaults: SiteConfig, prompt: Prompter | None = None) -> SiteConfig:
    """A site config from answers to prompts, starting from defaults"""
    prompt = prompt or Prompter()
    prompt.say(f"Enter keeps the [default]; {CLEAR} clears it.\n")
    ask = prompt.ask
    site = replace(
        defaults,
        vessel_id=ask("R2R vessel ID (ICES code, e.g. 33RR)", defaults.vessel_id),
        vessel_name=ask("Vessel name (e.g. Roger Revelle)", defaults.vessel_name),
        operator_id=ask("R2R operator ID (e.g. edu.ucsd.sio)", defaults.operator_id),
        scheduler_id=ask("R2R scheduler ID (e.g. org.unols)", defaults.scheduler_id),
        contact_institution=ask(
            "Contact institution for the QA report", defaults.contact_institution
        ),
        contact_institution_id=ask(
            "Contact institution's R2R ID (e.g. edu.ucsd.sio)", defaults.contact_institution_id
        ),
        contact_email=ask("Contact email", defaults.contact_email, _email),
        distro_type=ask("R2R distribution type", defaults.distro_type),
        output_extra_directory=ask(
            "OpenVDM extra directory for the reports", defaults.output_extra_directory
        ),
        tracklines_extra_directory=ask(
            "OpenVDM extra directory with GeoJSON tracklines", defaults.tracklines_extra_directory
        ),
    )
    site.extent = _ask_extent(prompt, defaults.extent)
    return site
