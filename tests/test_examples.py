"""The example TOMLs in configs/ show every key their schema takes, commented or not, so a key
added to a schema has to be added to its example; and their thresholds are the defaults.
"""

import re
import tomllib

from conftest import EXAMPLES

from sbe_qa_processing.config import CHECKED, CORE_KEYS, Thresholds, load_config
from sbe_qa_processing.integrations.openvdm.hook import R2R_SETTINGS, SITE_KEYS
from sbe_qa_processing.theme import _COLOR_KEYS, _KEYS

CONFIG = EXAMPLES / "config.example.toml"
SITE = EXAMPLES / "site.example.toml"
THEME = EXAMPLES / "theme.example.toml"
# Site TOML settings from before the hook dropped R2R, accepted and ignored with a note
IGNORED = {f"{table}.{key}" for table, keys in R2R_SETTINGS.items() for key in keys} | {
    "openvdm.tracklines_extra_directory",
    "thresholds.yellow_minimum_percent",
}


def shown(path) -> set[str]:
    """Every `table.key` the file sets or shows commented out (`# key = value`)"""
    table, keys = "", set()
    for line in path.read_text().splitlines():
        bare = line.lstrip("#").strip()
        if heading := re.fullmatch(r"\[\[?([\w.]+)\]\]?.*", bare):
            table = heading.group(1)
        elif setting := re.match(r"(\w+)\s*=", bare):
            keys.add(f"{table}.{setting.group(1)}" if table else setting.group(1))
    return keys


def schema_keys(schema: dict, prefix: str = "") -> set[str]:
    keys = set()
    for name, value in schema.items():
        path = f"{prefix}.{name}" if prefix else name
        keys |= schema_keys(value, path) if isinstance(value, dict) else {path}
    return keys


def test_the_config_example_shows_every_key():
    """The core config TOML's keys; R2R's are the cruise TOMLs `r2r config-from-*` writes"""
    assert CORE_KEYS["theme"] is CHECKED  # theme.py checks it: its example shows its keys
    keys = {key for key in schema_keys(CORE_KEYS) if key != "theme"}
    assert keys - shown(CONFIG) == set()
    assert "configs/theme.example.toml" in CONFIG.read_text()
    assert load_config(CONFIG).cruise_id == "LAKE1"


def test_the_site_example_shows_every_key():
    keys = {key for key in schema_keys(SITE_KEYS) if key != "theme"} - IGNORED
    assert keys - shown(SITE) == set()
    assert shown(SITE) & IGNORED == set()  # the ignored settings aren't suggested
    assert "configs/theme.example.toml" in SITE.read_text()


def commented_thresholds(path) -> dict:
    """The [thresholds] an example shows commented out, as a table"""
    text = path.read_text()
    block = text[text.index("[thresholds]") : text.index("# The reports' theme")]
    return tomllib.loads(
        "\n".join(line[2:] for line in block.splitlines() if re.match(r"# \w+ = ", line))
    )


def test_the_examples_thresholds_are_the_defaults():
    """Commented thresholds, uncommented, are Thresholds' defaults, so they can't drift"""
    every = schema_keys(CORE_KEYS["thresholds"])
    for path in (CONFIG, SITE):
        table = commented_thresholds(path)
        assert set(table) == every, path.name
        values = {k: tuple(v) if isinstance(v, list) else v for k, v in table.items()}
        assert Thresholds(**values) == Thresholds(), path.name


def test_the_theme_example_shows_every_setting():
    keys = shown(THEME)
    assert {k for k in _KEYS if k != "colors"} - keys == set()
    assert {f"colors.{k}" for k in _COLOR_KEYS} - keys == set()
