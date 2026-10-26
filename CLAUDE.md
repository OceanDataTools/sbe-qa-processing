# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`sbe-qa-processing` (GitHub: `OceanDataTools/sbe-qa-processing`, public, MIT License) runs quality assessment on Sea-Bird **SBE 9/911plus** CTD casts. Its first audience is anyone with a directory of raw Seasave files (or a BagIt bag as R2R/NCEI distribute it), with an optional config TOML. The core runs integrity and science checks and writes:
- a PDF report,
- every figure as an SVG,
- a Jupyter notebook that re-runs the assessment.

Two optional integrations build on the core, for their own users only:
- **R2R** (`integrations/r2r`, the `r2r` commands) adds R2R's CTD tests, rating and QA 2.0 XML.
- **OpenVDM** (`integrations/openvdm`, the `openvdm` hook, `site-config` and `config-from-openvdm`; the `openvdm` extra) runs the core QA after each CTD transfer.

Core must never depend on either. Keep R2R- or OpenVDM-specific behavior in its integration, and add hooks to core (like `write_pdf`'s `front` or `NotebookExtras`) rather than integration knowledge.

The R2R XML is a parity target. `tests/test_r2r_comparison.py` requires its `certificate` and manifest to be **identical** to R2R's own reports for released filesets. Treat changes to `integrations/r2r/rules.py`, `integrations/r2r/xml_report.py`, or anything feeding them as behavior changes.

## Commands

Use uv. Run from the repo root.

- Install: `uv sync`. Use `uv sync --locked` to check the lock. The OpenVDM hook needs the `openvdm` extra (pyyaml): `uv sync --extra openvdm`. The dev group has pyyaml, so the tests don't.
- All tests: `uv run pytest`. This takes about 80 s with the R2R filesets present.
- One test: `uv run pytest tests/test_status.py::test_name`
- Lint and format: `uv run ruff check .` and `uv run ruff format --check .` (line length 99).
- CI (`.github/workflows/ci.yml`) runs on PRs and pushes to `main`:
  - `lint`: ruff;
  - `test`: Linux on Python 3.11–3.14, plus macOS and Windows on 3.13, without `data/`, so the R2R comparisons skip;
  - `r2r`: the full suite with the three validation filesets, cached under a fixed key and fetched from R2R only on a cache miss. Bump the key's `-vN` suffix to refetch them, and add a new validation fileset to both the key and the fetch loop.

  Everything uses `uv sync --locked`, so commit `uv.lock` with `pyproject.toml` changes.
- Fetch the R2R validation filesets into `data/`. Without them, `test_r2r_comparison.py` and parts of `test_openvdm.py` skip, and the skip message names the command:
  `uv run sbe-qa-processing r2r fetch-fileset SP2613 169847`, plus the same for `RR2605 170644` and `BH18-18 132368`.
- Natural Earth map data for offline use: `uv run sbe-qa-processing fetch-map-data`. The tests monkeypatch the map layers, so they don't need it.
- Run on a fileset: `uv run sbe-qa-processing run data/SP2613_169847_ctd [-c configs/SP2613.toml] -o output/SP2613 [--force] [--execute-notebook]`. Without `-c`, the reports are titled after the fileset directory.
- Run with R2R's tests and XML: `uv run sbe-qa-processing r2r run configs/SP2613.toml data/SP2613_169847_ctd -o output/SP2613 [--force] [--execute-notebook]`
- Make a cruise TOML from R2R's catalog API: `uv run sbe-qa-processing r2r config-from-catalog CRUISE_ID FILESET_ID [-o configs|-] [--force]`, which writes `<output>/<CRUISE_ID>.toml`. It uses `integrations.r2r.catalog.config_from_r2r_catalog`. The API has no port coordinates, country or state.
- Make a cruise TOML from an R2R QA XML: `uv run sbe-qa-processing r2r config-from-qa data/<...>/r2r_qa.2.0.xml [-o configs|-] [--force]`
- Make the per-ship site TOML by prompting (stdin, prompts on stderr): `uv run sbe-qa-processing site-config [-o configs/site.toml] [--force]`
- Make a cruise TOML from OpenVDM's current cruise without running the QA: `uv run sbe-qa-processing config-from-openvdm [TRANSFER] --site-root URL --site-config site.toml [-o configs|-] [--force]`, which writes `<output>/<CRUISE_ID>.toml`.

`data/` (downloaded filesets) and `output/` (generated reports) are gitignored. `configs/` holds the three validation cruises' R2R cruise TOMLs, `site.example.toml` and `theme.example.toml`.

## seabirdscientific dependency

- seabirdscientific comes from PyPI: `seabirdscientific >= 3.0.0` in `[project].dependencies`, and `uv.lock` pins the version. 3.0.0 (2026-10-14) is the first release with the status-bit (fork issue 24), contour-grid (fork issue 21) and `potential_temperature` (fork issue 19) fixes; 2.x won't work. Update with `uv lock --upgrade-package seabirdscientific`, re-run the tests, and commit `uv.lock`.
- The fork `webbpinner/seabirdscientific` is where fixes are prepared. If this tool needs an unreleased fix again, prefer waiting for a release; a git dependency on the fork needs hatch's `allow-direct-references` again.
- To try unreleased seabirdscientific changes, install a local checkout over the locked one: `uv pip install -e ../../Sea-BirdScientific/seabirdscientific`. Then use `uv run --no-sync ...`, because a plain `uv run`/`uv sync` reverts to the lock.
- The code relies on those fixes: `status.py` reads the 911plus status fields in 3.0.0's order, and the TS plot assumes a full contour grid. `tests/test_status.py` checks the installed version's bit order. If a future fix is needed before a release, a version-detecting workaround beats a version pin.
- **Never contact upstream** (`Sea-BirdScientific/seabirdscientific`): no issues, PRs, comments, or `Sea-BirdScientific/seabirdscientific#N` references in commits or issues. Refer to seabirdscientific bugs as fork issues by full URL (`https://github.com/webbpinner/seabirdscientific/issues/N`).
- seabirdscientific has no slope/offset for temperature and conductivity, so `cast.py` applies the `.XMLCON` slope/offset. Digiquartz pressure slope/offset is applied inside seabirdscientific.

## Architecture

All code is in `src/sbe_qa_processing/`. `qa.run_qa(fileset, config=None)` is the core pipeline. It returns a `QAResult`, which every writer consumes, and `reports.write_reports` writes the core reports. Integrations live in `integrations/` and build on the core; **core modules never import them** (`tests/test_boundaries.py`). An integration adds PDF pages (`write_pdf`'s `front`) and notebook cells (`notebook.NotebookExtras`) instead of core knowing about it.

1. **`fileset.py`** finds casts in a bag or directory by grouping `.hex/.dat`, `.XMLCON/.con`, `.hdr` and `.bl` files by their case-insensitive path without suffix. It also reads `manifest-md5.txt`. Cast names matching `deck|dock|test` are deck tests. R2R excludes them from the NAV tests, and the science checks skip them.
2. **`xmlcon.py`** parses `.XMLCON` into seabirdscientific coefficient dataclasses (seabirdscientific has no XMLCON reader). **`hdr.py`** parses `.hdr` headers.
3. **`cast.py`** has `read_cast`, which calls seabirdscientific's `read_hex_file` and conversion functions and returns a `CastData` of per-scan numpy arrays.
   - Absent channels are `None`.
   - Failures go into `errors` (`"xmlcon"`, `"hex"`, `"conversion"`) instead of raising, so one bad cast never stops a fileset.
   - Salinity uses `gsw.SP_from_C`.
4. **`status.py`** decodes the 911plus status bits: pump, bottom contact, bottle confirm, modem.
5. **`integrity.py`** holds the integrity checks (raw files, checksums, positions, cast times, serial numbers) for any fileset, per cast and summarized for the fileset, as `CheckResult`s. Its per-cast predicates (`cast_position`, `same_serial`, `verify_manifest`, ...) are shared with R2R's `integrations/r2r/rules.py`.
6. **`science.py`** holds the science checks (`CheckResult` with pass/warn/fail/info/n/a), with limits in `config.Thresholds`. **`loadout.py`** builds the sensor/channel tables. **`bottles.py`** parses `.bl` bottle logs.
7. **`config.py`** handles the core config TOML: `QAConfig` (every field optional), `Extent` and `Thresholds`. `load_config` checks the file's keys and shapes with `check_keys` against `CONFIG_KEYS`: core's (`CORE_KEYS`) plus R2R's cruise TOML keys (`INTEGRATION_KEYS`, written out as strings since core never imports an integration; `tests/test_r2r_config.py::test_core_knows_the_keys_r2r_reads` keeps them in step). An unknown key or a wrong shape is a `ConfigError` saying where it belongs; the site TOML is checked against the hook's `SITE_KEYS`. Add a new TOML key to the schema as well as to its loader.
8. **Writers:**
   - `theme.py` holds `Theme` (page, fonts, scales, logo, footer, palette and `Colors`), loaded from a `--theme` file and the config's `[theme]` table (`QAConfig.theme`, applied on top) by `resolve_theme`. The writers take the theme explicitly; `DEFAULT_THEME` is the original look.
   - `pdf_report.py` uses ReportLab and embeds the SVGs as vectors via svglib. `layout(theme)` makes the theme concrete (page and frame sizes, registered fonts, paragraph styles) as a `Layout` passed to every page helper; an integration's `front` pages are a function of the same `Layout`.
   - `plots.py` has one matplotlib Figure per function, shared by the PDF and the notebook. Each takes a `theme`, renders inside `plots.themed(theme)` (fonts) and scales explicit font sizes with `_pt`.
   - `maps.py` loads Natural Earth via cartopy, clipped to the view, and degrades to no coastlines when the data can't load.
   - `notebook.py` writes a notebook (nbformat) whose cells import this package.
9. **`integrations/r2r/`** is R2R:
   - `config.py`: `CruiseConfig(QAConfig)` (R2R's IDs, ports, provenance and `yellow_minimum_percent`, with the cruise ID and dates required), `load_cruise_config` and `config_to_toml`;
   - `catalog.py`: configs from R2R's catalog API and QA XML. R2R's XML identifies the vessel by its ICES code, which the API calls `vessel_ices_code`; the API's own `vessel_id` field holds the vessel's name;
   - `rules.py`: the R2R tests and infos and the G/Y/R/N/X ratings. R2R rules that aren't documented upstream were inferred and are documented in its docstring; changing them breaks parity;
   - `xml_report.py`: the R2R schema with its stylesheet reference;
   - `certificate.py`: the certificate's PDF pages and notebook cells;
   - `reports.py`: `run_r2r` and `write_r2r_reports`;
   - `download.py`: `r2r fetch-fileset`.
10. **`integrations/openvdm/`** is OpenVDM. It writes the core reports only, never R2R's:
   - `hook.py` builds a core `QAConfig` from OpenVDM's `api/warehouse/getCruiseConfig` (including `cruiseExtent`, from OpenVDM 2.17), OpenVDM 2.17's `vessel` block (name and contact; its R2R IDs are ignored) and an optional site TOML. The vessel block comes from `openvdm.yaml`, or from the cruise's `ovdmConfig.json` when the hook only has `--site-root`. OpenVDM's values win over the site TOML's, field by field. A site TOML's old R2R settings are ignored and listed in `SiteConfig.ignored`;
   - it also handles the hook's skip, chown and pending-checksum logic, and queues the reports for OpenVDM's MD5 summary by running OpenVDM's own `utils/update_md5_summary.py` with OpenVDM's Python (`queue_md5_update`), since this project can't import OpenVDM;
   - `resolve` and `cruise_toml` are shared by the hook's snapshot and `config-from-openvdm`, and `HookRun.notes` lists what the config is missing or assumed;
   - it imports pyyaml only in `load_openvdm_yaml`, so it loads without the extra;
   - `site_setup.py` holds `site-config`'s prompts.
11. **`cli.py`** provides every command as a subcommand: `run`, `openvdm`, `config-from-openvdm`, `site-config`, `fetch-map-data`, and the `r2r` group (`r2r run`, `r2r config-from-catalog`, `r2r config-from-qa`, `r2r fetch-fileset`). The hook's `--fileset-id` is accepted and ignored, for installed `openvdm.yaml` files. Its module docstring states the argument conventions (`-o/--output`, `-o -`, `--force`, integer fileset IDs, uppercase metavars), and `tests/test_cli.py` checks them for every command. The `openvdm` hook's arguments are called from installed `openvdm.yaml` files, so don't rename them. It forces the matplotlib `Agg` backend. Expected errors (`cli.EXPECTED_ERRORS`: `NetworkError`, `ConfigError`, `R2RError`, `OpenVDMError`, `ThemeError`, `FilesetError`: no fileset or bag data directory) become a one-line stderr message and exit 1; anything else raises. In `openvdm` mode every exception becomes that one-line message, because OpenVDM displays it. `-v`/`--verbose` (the OceanDataTools convention: `-v` INFO, `-vv` DEBUG) is added to every parser by `_add_verbose`, so it works before or after the command; it sets the `sbe_qa_processing` logger's level (not the root's), and `-vv` also prints expected errors' tracebacks.
12. **`network.py`** has `reaching(service, url)`, which turns a server that can't be reached (`URLError`, timeouts, dropped connections; not `HTTPError`) into a `NetworkError` naming the service and host. Wrap every network call in it (R2R's catalog and downloads, OpenVDM's API, `maps.prefetch`), so being offline at sea is a one-line message, not a traceback. HTTP errors pass through it (the server answered); R2R's `catalog.http_error` turns them into an `R2RError` naming what was fetched and the status, in `fetch_r2r_records` and `download.fetch`.
13. **`filenames.py`** has `safe_filename` / `report_stem`: every file named after a cruise ID or identifier (report paths, the plots directory, the notebook's write cells, the hook's snapshot, `<CRUISE_ID>.toml`) goes through `report_stem`, so `/`, `..` and Windows-forbidden characters can't move or break the reports (issue 52). Text that shows the ID (PDF title, notebook, R2R XML `identifier`) keeps it as given.

## Conventions

- Python ≥3.11 with modern syntax. Prefer dataclasses. Write short docstrings without `:param:` boilerplate, and put units in brackets in comments (`# [dbar]`).
- Classes people inspect (results, casts, configs, checks) inherit `readable.Readable` and define a short `__str__`; `Readable` makes IPython and Jupyter show that text for a bare expression, and `repr()` stays the dataclass's. Configs print as their TOML. Plumbing classes (`Layout`, `NotebookExtras`, `Theme`, ...) don't need it. `tests/test_readable.py` checks each one.
- The README documents every command's arguments and defaults ("Command line", "R2R commands", "OpenVDM commands") and every config, R2R cruise and site TOML key ("Config TOML", "R2R cruise TOML", "Site TOML"). Update those tables when you add, rename or change a CLI argument or TOML key.
- Renaming a public function or module changes the generated notebook too: `notebook.py` and `integrations/r2r/reports.notebook_extras` write import lines and calls as strings. No test executes the notebook, so check by hand with `run ... --execute-notebook` and `r2r run ... --execute-notebook` after such changes.
- Report styling comes from the theme: no colors, fonts or text sizes written into the writers or the plots. The default theme must reproduce the existing reports: SVGs are deterministic (no date, fixed `svg.hashsalt`), so compare their checksums before and after a change, and the PDFs' text and rendered pages (only the "Generated" timestamp differs).
- The seawater deprecation warning from seabirdscientific's EOS-80 import is filtered in `cli.py`, `tests/conftest.py` and the notebook's setup cell.
- An `Extent` with westernmost > easternmost crosses the antimeridian, as in R2R's reports and OpenVDM's `cruiseExtent`. Test positions with `Extent.contains`, never by comparing edges, and draw anything spanning the box relative to a central longitude, as `plots.cast_map` does.
- Tests are plain pytest functions. Use small synthetic inputs where possible, and use the downloaded R2R filesets for end-to-end parity.
