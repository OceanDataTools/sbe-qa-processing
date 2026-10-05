# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`sbe-qa-processing` (GitHub: `OceanDataTools/sbe-qa-processing`, private for now) runs quality assessment on Sea-Bird **SBE 9/911plus** CTD filesets. It reads a BagIt bag as R2R/NCEI distribute it, or a plain directory of Seasave output, together with a cruise TOML. It writes:
- an R2R QA 2.0 XML,
- a PDF report,
- every figure as an SVG,
- a Jupyter notebook that re-runs the assessment.

It runs from the CLI or as an OpenVDM post-transfer hook.

The R2R XML is a parity target. `tests/test_r2r_comparison.py` requires its `certificate` and manifest to be **identical** to R2R's own reports for released filesets. Treat changes to `r2r.py`, `xml_report.py`, or anything feeding them as behavior changes.

## Commands

Use uv. Run from the repo root.

- Install: `uv sync`. Use `uv sync --locked` to check the lock.
- All tests: `uv run pytest`. This takes about 80 s with the R2R filesets present.
- One test: `uv run pytest tests/test_status.py::test_name`
- Lint and format: `uv run ruff check .` and `uv run ruff format --check .` (line length 99).
- Fetch the R2R validation filesets into `data/`. Without them, `test_r2r_comparison.py` and parts of `test_openvdm.py` skip, and the skip message names the command:
  `python scripts/fetch_r2r_fileset.py SP2613 169847`, plus the same for `RR2605 170644` and `BH18-18 132368`.
- Natural Earth map data for offline use: `uv run python scripts/fetch_map_data.py`. The tests monkeypatch the map layers, so they don't need it.
- Run on a fileset: `uv run sbe-qa-processing run configs/SP2613.toml data/SP2613_169847_ctd -o output/SP2613 [--execute-notebook]`
- Make a cruise TOML from R2R's catalog API: `uv run python scripts/fetch_r2r_config.py CRUISE FILESET [--out configs] [--force]`, which writes `<out>/<CRUISE>.toml`. It uses `config.config_from_r2r_api`. The API has no port coordinates, country or state.
- Make a cruise TOML from an R2R QA XML: `uv run sbe-qa-processing config-from-r2r data/<...>/r2r_qa.2.0.xml`

`data/` (downloaded filesets) and `output/` (generated reports) are gitignored. `configs/` holds the three validation cruises and `site.example.toml`.

## seabirdscientific dependency

- seabirdscientific comes from git: the `integration` branch of the fork `webbpinner/seabirdscientific`, which is upstream `v3.0.0` plus every pending fix. It's a direct reference in `[project].dependencies` (`seabirdscientific @ git+...@integration`, which needs hatch's `allow-direct-references`), not a `[tool.uv.sources]` entry, so pip installs the fork too. PyPI's seabirdscientific is 2.x and won't work. `uv.lock` pins its commit.
- That branch is rebuilt and force-pushed whenever a fork PR branch changes. Afterwards, run `uv lock --upgrade-package seabirdscientific`, re-run the tests, and commit `uv.lock`.
- To try unpushed seabirdscientific changes, install a local checkout over the locked one: `uv pip install -e ../../Sea-BirdScientific/seabirdscientific`. Then use `uv run --no-sync ...`, because a plain `uv run`/`uv sync` reverts to the lock.
- The code must keep working on plain upstream `v3.0.0` too:
  - `status.py` detects at runtime whether the installed version reads the 911plus status nibble reversed (fork issue 24).
  - The TS plot copes with the old one-column contour grid (fork issue 21).
  - Keep such workarounds version-detecting, not version-pinned.
- **Never contact upstream** (`Sea-BirdScientific/seabirdscientific`): no issues, PRs, comments, or `Sea-BirdScientific/seabirdscientific#N` references in commits or issues. Refer to seabirdscientific bugs as fork issues by full URL (`https://github.com/webbpinner/seabirdscientific/issues/N`).
- seabirdscientific has no slope/offset for temperature and conductivity, so `cast.py` applies the `.XMLCON` slope/offset. Digiquartz pressure slope/offset is applied inside seabirdscientific.

## Architecture

All code is in `src/sbe_qa_processing/`. `qa.run_qa(config, fileset)` is the pipeline. It returns a `QAResult`, which every writer consumes.

1. **`fileset.py`** finds casts in a bag or directory by grouping `.hex/.dat`, `.XMLCON/.con`, `.hdr` and `.bl` files by their case-insensitive path without suffix. It also reads `manifest-md5.txt`. Cast names matching `deck|dock|test` are deck tests. R2R excludes them from the NAV tests, and the science checks skip them.
2. **`xmlcon.py`** parses `.XMLCON` into seabirdscientific coefficient dataclasses (seabirdscientific has no XMLCON reader). **`hdr.py`** parses `.hdr` headers.
3. **`cast.py`** has `read_cast`, which calls seabirdscientific's `read_hex_file` and conversion functions and returns a `CastData` of per-scan numpy arrays.
   - Absent channels are `None`.
   - Failures go into `errors` (`"xmlcon"`, `"hex"`, `"conversion"`) instead of raising, so one bad cast never stops a fileset.
   - Salinity uses `gsw.SP_from_C`.
4. **`status.py`** decodes the 911plus status bits: pump, bottom contact, bottle confirm, modem.
5. **`r2r.py`** holds the R2R tests and infos and the G/Y/R/N/X ratings.
   - R2R rules that aren't documented upstream were inferred, and they're documented in the module docstring.
   - Changing them breaks parity.
6. **`science.py`** holds the science checks (`CheckResult` with pass/warn/fail/info/n/a), with limits in `config.Thresholds`. **`loadout.py`** builds the sensor/channel tables. **`bottles.py`** parses `.bl` bottle logs.
7. **`config.py`** handles the cruise TOML (`CruiseConfig`, `Extent`, `Thresholds`, `Port`) `config_toml_from_r2r_qa` and `config_from_r2r_api`. R2R's XML identifies the vessel by its ICES code, which the API calls `vessel_ices_code`; the API's own `vessel_id` field holds the vessel's name. **`openvdm.py`** builds a `CruiseConfig` from OpenVDM's `api/warehouse/getCruiseConfig`, GeoJSON tracklines and an optional site TOML. It also handles the hook's skip, chown and pending-checksum logic.
8. **Writers:**
   - `xml_report.py` writes the R2R schema with its stylesheet reference.
   - `pdf_report.py` uses ReportLab and embeds the SVGs as vectors via svglib.
   - `plots.py` has one matplotlib Figure per function, shared by the PDF and the notebook.
   - `maps.py` loads Natural Earth via cartopy, clipped to the view, and degrades to no coastlines when the data can't load.
   - `notebook.py` writes a notebook (nbformat) whose cells import this package.
9. **`cli.py`** provides the `run`, `openvdm` and `config-from-r2r` subcommands. It forces the matplotlib `Agg` backend. In `openvdm` mode, exceptions become a one-line stderr message and exit 1, because OpenVDM displays it.

## Conventions

- Python ≥3.11 with modern syntax. Prefer dataclasses. Write short docstrings without `:param:` boilerplate, and put units in brackets in comments (`# [dbar]`).
- Renaming a public function or module changes the generated notebook too: `notebook.py` writes import lines and calls as strings. No test executes the notebook, so check by hand with `run ... --execute-notebook` after such changes.
- The seawater deprecation warning from seabirdscientific's EOS-80 import is filtered in `cli.py`, `tests/conftest.py` and the notebook's setup cell.
- Tests are plain pytest functions. Use small synthetic inputs where possible, and use the downloaded R2R filesets for end-to-end parity.
