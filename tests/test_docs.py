"""The docs' commands stay true to the CLI: the README's openvdm.yaml hook and every
sbe-qa-processing command in the docs' shell examples parse with the CLI's own parser, so a
renamed or removed option fails here rather than leaving the docs stale.
"""

import re
import shlex

import pytest
import yaml
from conftest import ROOT

from sbe_qa_processing.cli import build_parser

DOCS = ("README.md", "COOKBOOK.md")
INSTALLED = "/opt/sbe-qa-processing/.venv/bin/sbe-qa-processing"
# OpenVDM's placeholders, as it fills them in
PLACEHOLDERS = {
    "{collectionSystemTransferName}": "CTD",
    "{newFiles}": "SP2613_Station1.hex SP2613_Station1.hdr",
    "{updatedFiles}": "",
}
# The command starting a line, maybe after `uv run` or `sudo`: not a path or a name it's part of
COMMAND = re.compile(r"\s*(?:uv run\s+|sudo\s+)?(?:\S*/bin/)?sbe-qa-processing\s+(.*)$")


def blocks(name: str, language: str) -> list[str]:
    return re.findall(rf"```{language}\n(.*?)```", (ROOT / name).read_text(), re.DOTALL)


def test_the_readme_hook_parses():
    (block,) = [b for b in blocks("README.md", "yaml") if "postHookCommands" in b]
    (transfer,) = yaml.safe_load(block)["postHookCommands"]["postCollectionSystemTransfer"]
    command = transfer["commandList"][0]["command"]
    assert command[0] == INSTALLED
    args = command[1:]
    for placeholder, value in PLACEHOLDERS.items():
        args = [arg.replace(placeholder, value) for arg in args]
    parsed = build_parser().parse_args(args)
    assert parsed.command == "openvdm"
    assert parsed.transfer == transfer["collectionSystemTransferName"]
    assert parsed.changed_files == [PLACEHOLDERS["{newFiles}"], ""]


def documented_commands() -> list[tuple[str, str, list[str]]]:
    """(doc, line, arguments) for each sbe-qa-processing command in the docs' bash blocks"""
    found = []
    for name in DOCS:
        for block in blocks(name, "bash"):
            for line in block.replace("\\\n", " ").splitlines():
                if command := COMMAND.match(line):
                    found.append(
                        (name, line.strip(), shlex.split(command.group(1), comments=True))
                    )
    return found


@pytest.mark.parametrize(
    "doc, line, args",
    documented_commands(),
    ids=lambda value: value if isinstance(value, str) and value.endswith(".md") else "",
)
def test_documented_commands_parse(doc, line, args):
    try:
        build_parser().parse_args(args)
    except SystemExit as stop:
        assert stop.code == 0, f"{doc}: {line}"  # --version and --help exit 0; an error, 2
