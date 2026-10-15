# sbe-qa-processing

> [!WARNING]
> **Under heavy development.** This project is shared for peer review. Its outputs, command
> line, config format and Python API may change without notice, and its results haven't been
> validated beyond the R2R filesets listed under [Validation against R2R](#validation-against-r2r).
> Don't rely on it for operational QA yet. Issues and comments are welcome.

Quality assessment for Sea-Bird **SBE 9/911plus** CTD casts. Point it at a directory of raw
Seasave files (`.hex/.dat`, `.XMLCON/.con`, `.hdr`, `.bl`) or a BagIt bag, optionally with a
small config, and it checks the files and the data and writes:

| Output | Contents |
|---|---|
| `<TITLE>_qa_report.pdf` | A cast map with coastlines and a world inset; the fileset's integrity checks; and per cast: profiles, dual-sensor differences, TS diagram, pressure record, integrity and science checks and sensor loadout |
| `<TITLE>_plots/*.svg` | Every report figure as SVG: the cast map, and each cast's profiles, TS diagram and pressure record. The PDF embeds these same SVGs as vector graphics |
| `<TITLE>_qa.ipynb` | A notebook that re-runs the whole assessment step by step, to inspect or change it (see [Opening the notebook](#opening-the-notebook)) |

`<TITLE>` is the config's cruise ID, or the fileset directory's name without one.

Two optional integrations build on this:
- **[R2R](#r2r)** (Rolling Deck to Repository) adds R2R's CTD tests, its rating and its QA 2.0
  XML, identical to R2R's own reports.
- **[OpenVDM](#openvdm)** runs the QA from a post-transfer hook on each cruise's CTD files.

Raw `.hex` data is read and converted with
[seabirdscientific](https://github.com/Sea-BirdScientific/seabirdscientific), installed from
the `integration` branch of [a fork](https://github.com/webbpinner/seabirdscientific) that
carries fixes not yet released upstream (see [seabirdscientific version](#seabirdscientific-version)).
The `.XMLCON` parser lives here, since seabirdscientific doesn't have one.

## Setup

It needs [git](https://git-scm.com/) and [uv](https://docs.astral.sh/uv/), and runs on Linux,
macOS and Windows with Python 3.11 or later. uv uses a Python 3.11+ already on the system, or
downloads one, so the system Python doesn't matter. Every dependency installs as a prebuilt
wheel (Linux x86_64 and ARM, macOS, Windows), so no compiler is needed. Install git and uv for
your platform, then [install the project](#install-the-project).

### Debian / Ubuntu

```bash
sudo apt update
sudo apt install -y git curl ca-certificates
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"   # puts uv on the PATH; or open a new shell
```

Debian 12 and 13 and Ubuntu 24.04 have a new enough `python3`. On Ubuntu 22.04 (Python 3.10),
uv downloads Python itself.

### Alma / Rocky / RHEL

```bash
sudo dnf install -y git tar     # curl is already installed
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"   # puts uv on the PATH; or open a new shell
```

The default `python3` of RHEL 8 (3.6) and 9 (3.9) is too old, so uv downloads Python itself.
To use a system Python instead (recommended for a [server install](#server-install-for-the-openvdm-hook)),
install Python 3.12 from AppStream with `sudo dnf install -y python3.12`. RHEL 10's `python3`
is 3.12.

### macOS

```bash
brew install git uv
```

### Windows

In PowerShell:

```powershell
winget install --id Git.Git -e
winget install --id astral-sh.uv -e
```

Or install uv with its installer:
`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`. Open a
new terminal afterwards, so both are on the PATH. The commands below work the same in
PowerShell, with Windows paths, e.g.
`uv run sbe-qa-processing run D:\cruises\MYCRUISE\CTD -o output\MYCRUISE`. The core QA and
the R2R commands run on Windows; the OpenVDM hook runs on the Linux server where OpenVDM is
installed.

### Install the project

```bash
git clone https://github.com/OceanDataTools/sbe-qa-processing.git
cd sbe-qa-processing
uv sync                                    # uv sync --extra openvdm for the OpenVDM hook
uv run sbe-qa-processing fetch-map-data   # Natural Earth coastlines for the maps (~27 MB, once)
uv run sbe-qa-processing --help
```

`uv sync` makes a virtual environment in `.venv` with the exact versions in `uv.lock`. It also
installs the dev group: pytest, ruff, and `nbclient` and `ipykernel`, which `--execute-notebook`
needs. `uv sync --no-dev` leaves those out. `uv run` runs a command in that environment; the
command itself is `.venv/bin/sbe-qa-processing` (`.venv\Scripts\sbe-qa-processing.exe` on
Windows), which works without uv on the PATH.

To check the install against R2R's own reports, fetch the validation filesets and run the
tests:

```bash
uv run sbe-qa-processing r2r fetch-fileset SP2613 169847
uv run sbe-qa-processing r2r fetch-fileset RR2605 170644
uv run sbe-qa-processing r2r fetch-fileset BH18-18 132368
uv run pytest
```

CI (`.github/workflows/ci.yml`) runs ruff and the tests on every PR and push to `main`: on
Linux with Python 3.11–3.14 and on macOS and Windows, then once more with these filesets, which
it caches so R2R only needs to be reachable the first time.

To update, pull and sync again: `git pull && uv sync` (with the same options as before).

### Server install for the OpenVDM hook

On the Linux server where OpenVDM runs, install into a shared directory such as
`/opt/sbe-qa-processing`, which the hook example under [OpenVDM](#openvdm) uses:

```bash
sudo mkdir -p /opt/sbe-qa-processing
sudo chown "$USER": /opt/sbe-qa-processing
git clone https://github.com/OceanDataTools/sbe-qa-processing.git /opt/sbe-qa-processing
cd /opt/sbe-qa-processing
uv sync --no-dev --extra openvdm --no-managed-python      # a system Python 3.11+
sudo /opt/sbe-qa-processing/.venv/bin/sbe-qa-processing fetch-map-data
```

Two things depend on the user the hook runs as, which is the user OpenVDM's transfer workers
run as (check OpenVDM's supervisor configuration; when it's root, the hook also gives the
reports to the warehouse user):
- **Python.** The environment links to the Python it was made with. A uv-downloaded Python
  lives in the installing user's home (`~/.local/share/uv/python`), which other users may not
  be able to read. `--no-managed-python` makes uv use a system Python 3.11+ instead: Debian
  12 and 13's and Ubuntu 24.04's `python3`, or `python3.12` on Alma/Rocky/RHEL 8 and 9 after
  `sudo dnf install -y python3.12`. Where there's none (Ubuntu 22.04), leave the option out
  and keep uv's Python in the shared directory instead, by setting
  `UV_PYTHON_INSTALL_DIR=/opt/sbe-qa-processing/python` for this and every later `uv sync`.
- **Map data.** cartopy caches the maps in the home of the user who downloads them
  (`~/.local/share/cartopy`). Run `fetch-map-data` as the hook's user, e.g. with `sudo` as
  above when the workers run as root, or `sudo -u <user>` otherwise. `fetch-map-data` prints
  where it put them.

Leave out `--no-dev` to use the hook's `--execute-notebook`.

### About the dependencies

seabirdscientific is installed from the `integration` branch of
[webbpinner/seabirdscientific](https://github.com/webbpinner/seabirdscientific), not from PyPI,
whose release lacks the fixes this tool needs. `uv sync` installs the exact commit pinned in
`uv.lock`. `pip install git+https://github.com/OceanDataTools/sbe-qa-processing.git` also works,
and takes the branch's latest commit.

The fork is temporary. Once its fixes are released in the official
[seabirdscientific](https://github.com/Sea-BirdScientific/seabirdscientific) (two are already
merged upstream; see [seabirdscientific version](#seabirdscientific-version)), this project
will depend on that release instead. Until then, the fork's `integration` branch is rebuilt as fixes
change, so a `pip` install can pick up a different commit from one day to the next.

The maps use [cartopy](https://scitools.org.uk/cartopy/) with Natural Earth 1:10m land, lakes
and coastlines, and a 1:110m world inset. cartopy downloads them on first use; run `fetch-map-data`
beforehand to work offline (e.g. at sea). Without the data, maps are drawn without
coastlines.

## Usage

```bash
uv run sbe-qa-processing run path/to/fileset -o output/MYCASTS
uv run sbe-qa-processing run path/to/fileset -c my_config.toml -o output/MYCASTS --execute-notebook
```

Without `-c`, the reports are titled after the fileset directory, and the integrity checks
that need an expected area or dates are n/a. A [config TOML](#config-toml) sets those, the
thresholds and the report's details; every field is optional.

### Command line

`sbe-qa-processing --help` lists the commands, and `sbe-qa-processing COMMAND --help` (or
`sbe-qa-processing r2r COMMAND --help`) documents each one. The core commands are `run` and
`fetch-map-data`; the [R2R commands](#r2r-commands) are a group under `r2r`, and the
[OpenVDM commands](#openvdm-commands) are `openvdm`, `config-from-openvdm` and `site-config`.

Every command follows these conventions:
- `-o/--output` is a directory; only `site-config` writes a single file. Commands that make a
  cruise TOML write `<OUTPUT_DIR>/<CRUISE_ID>.toml` (default `configs/`), or to stdout with
  `-o -`.
- Existing outputs, reports included, aren't overwritten without `--force`. The exception is
  the `openvdm` hook, which replaces its reports after each transfer.
- A command exits 0 on success and 1 when it refuses (e.g. existing outputs) or fails with an
  expected error (a server that can't be reached, a bad theme, R2R or OpenVDM not having what was
  asked for), with a one-line message on stderr, e.g.
  `sbe-qa-processing: couldn't reach R2R (service.rvdata.us): ... Check the network connection and try again`.
- `-v`/`--verbose`, before or after the command, logs more: `-v` is INFO and `-vv` is DEBUG
  (default WARNING). `-vv` also prints the traceback of an expected error, e.g.
  `sbe-qa-processing r2r fetch-fileset SP2613 169847 -vv`.

#### `run`

```text
sbe-qa-processing run [-c CONFIG_TOML] [-o OUTPUT_DIR] [--force] [--execute-notebook]
                      [--theme THEME_TOML] FILESET_DIR
```

Assesses a fileset with the integrity and science checks, and writes the PDF, the SVG plots and
the notebook.

| Argument | Default | Description |
|---|---|---|
| `FILESET_DIR` | required | A directory of casts, searched recursively, or a BagIt bag (a directory with `bagit.txt`, `manifest-md5.txt` and the casts under `data/`). A cast's `.hex`/`.dat`, `.XMLCON`/`.con`, `.hdr` and `.bl` files are matched by name, ignoring case. Casts whose names contain `deck`, `dock` or `test` are deck tests |
| `-c`, `--config CONFIG_TOML` | none | A [config TOML](#config-toml). Without one, the reports are titled after `FILESET_DIR` |
| `-o`, `--output OUTPUT_DIR` | `output` | Directory for the reports, created if needed |
| `--force` | off | Replace existing reports; without it, `run` stops if any of them exists |
| `--execute-notebook` | off | Run the notebook (needs `nbclient` and `ipykernel`, in the dev group) and save it with its outputs |
| `--theme THEME_TOML` | none | A [theme](#themes) file; the config's `[theme]` applies on top |

It writes `<TITLE>_qa_report.pdf`, `<TITLE>_plots/*.svg` and `<TITLE>_qa.ipynb` into
`OUTPUT_DIR`, where `<TITLE>` is the config's `[cruise] id`, or the fileset directory's name.
It prints a summary: the number of casts, then each integrity check and science check that warned
or failed.

#### `fetch-map-data`

```text
sbe-qa-processing fetch-map-data
```

Downloads the Natural Earth layers the cast map uses (1:10m land, lakes and coastlines, and the
1:110m world for the inset, about 27 MB) into cartopy's data directory, so the maps have
coastlines offline, e.g. at sea. It takes no arguments.

### Config TOML

A config TOML describes the cruise for the reports, sets the expected area and dates for the
integrity checks, and tunes the science checks. Every table and key is optional; leave out
what you don't know. A complete example:

```toml
[cruise]
id = "LAKE1"                          # the reports' title and file name prefix
name = "Lake Michigan spring survey"  # under the title
pi = "A. Scientist"                   # report header details, shown when given
location = "Southern Lake Michigan"
vessel_name = "Blue Heron"
depart_date = 2026-07-09              # expected cast dates: TOML dates, not strings
arrive_date = 2026-07-10

[cruise.extent]                       # expected cast area [decimal degrees, east and north positive]
westernmost = -88.0
easternmost = -87.0
southernmost = 42.0
northernmost = 43.0

[fileset]
manifest = "MD5_Summary.txt"          # checksums for a plain directory; relative to this file

[provenance]                          # the report contact, in the header
contact_institution = "Large Lakes Observatory"
contact_email = "ship-tech@example.org"

[thresholds]                          # science check limits; defaults below
salinity_range = [0.0, 2.0]           # fresh water
conductivity_range = [0.0, 0.2]

[theme]                               # the reports' look; see Themes
page_size = "A4"
```

| Table | Key | Used for |
|---|---|---|
| `[cruise]` | `id` | The reports' title and file names. Default: the fileset directory's name. In file names, characters that aren't valid in a file name on every platform (`/ \ < > : " \| ? *`, control characters) become `_`, with a warning; the title keeps the ID as given |
| | `name`, `pi`, `location`, `vessel_name` | The report header, when given |
| | `depart_date`, `arrive_date` | "Cast time inside the expected dates" (needs both; n/a without them) |
| `[cruise.extent]` | `westernmost`, `easternmost`, `southernmost`, `northernmost` | "Position inside the expected area" (n/a without it) and the box on the cast map. All four are needed. A box across the antimeridian has `westernmost` > `easternmost`, e.g. 178 to -178 |
| `[fileset]` | `manifest` | A checksum file for a plain directory: `<md5> <path>` lines with paths relative to the manifest's directory, as in OpenVDM's MD5 summary. Lines without a real hash are skipped, and files changed after the manifest was written count as pending. A BagIt bag uses its own `manifest-md5.txt` instead |
| `[provenance]` | `contact_institution`, `contact_email` | The report header |
| `[thresholds]` | see below | The science checks |
| `[theme]` | see [Themes](#themes) | The PDF, figures and notebook |

`[thresholds]`, with their defaults. Ranges are `[low, high]` arrays.

| Key | Default | Check |
|---|---|---|
| `temperature_difference` | `0.01` [°C] | Dual temperature difference: median of \|primary − secondary\| below `dual_sensor_min_pressure`. Warns up to twice the limit, then fails |
| `conductivity_difference` | `0.001` [S/m] | Dual conductivity difference, as above |
| `salinity_difference` | `0.01` [PSU] | Dual salinity difference, as above |
| `dual_sensor_min_pressure` | `20.0` [dbar] | Depth below which the dual-sensor differences are taken, leaving out the surface layer |
| `temperature_range` | `[-2.5, 40.0]` [°C] | Plausible range: passes with no scans outside, warns under 1% outside, else fails |
| `conductivity_range` | `[0.0, 7.0]` [S/m] | Plausible range, pumped scans only |
| `salinity_range` | `[2.0, 42.0]` [PSU] | Plausible range, pumped scans only; lower it for fresh water |
| `pressure_range` | `[-5.0, 11000.0]` [dbar] | Plausible range |
| `max_spike_percent` | `0.5` [%] | Share of scans Wild Edit flags (2 and 20 standard deviations, 100-scan blocks). Warns up to twice the limit |
| `max_lost_scan_percent` | `0.1` [%] | Share of scans missing from the modulo counter. Warns up to ten times the limit |
| `max_calibration_age_days` | `365` [days] | Sensors calibrated longer ago than this at the time of the cast warn |

Parsing rules:
- An unknown key in `[thresholds]` or `[theme]` is an error, so a typo doesn't go unnoticed.
- Other tables and keys are ignored, so an [R2R cruise TOML](#r2r-cruise-toml) or an OpenVDM
  snapshot works as a config too.
- Relative paths (`[fileset] manifest`, theme fonts and logo) are relative to the TOML file.

### Themes

The PDF, its figures and the notebook can take a house style: page size and margins, fonts,
text sizes, colors, a logo and the footer. Pass a theme file with `--theme` (`run`, `r2r run`
and the `openvdm` hook), or set a `[theme]` table in the config, which applies over the file's
settings. `configs/theme.example.toml` shows every setting:

```bash
uv run sbe-qa-processing run path/to/fileset --theme configs/theme.example.toml -o output/MYCASTS
```

```toml
[theme]                     # in a config TOML
page_size = "A4"
logo = "logo.png"           # relative to the TOML file
organization = "Example Oceanographic Institution"
palette = "colorblind"      # Okabe-Ito colors, distinct with common color vision deficiencies

[theme.colors]
water = "#e6f0f7"
```

Without a theme, the reports look as they always have. The notebook re-creates the same theme,
so figures re-run there match the PDF. For the OpenVDM hook, a `[theme]` table can also go in
the site TOML. Fonts must be TrueType (`.ttf`); with `font_scale` above about 1.05, narrow
table headers such as "Status" wrap.

### Opening the notebook

The notebook imports this package and seabirdscientific, so it has to run on the project's
Python (`.venv`), not on the Python of whichever Jupyter opens it. A Jupyter installed with
Homebrew, Anaconda or pip starts its own Python by default. That Python doesn't have this
project's packages, so the first cell fails with
`ModuleNotFoundError: No module named 'matplotlib'` (or `'sbe_qa_processing'`). There are two
ways to fix this.

**Run JupyterLab from the project.** Nothing to set up, and nothing is added to `.venv` or
`uv.lock`:

```bash
uv run --with jupyterlab jupyter lab output/MYCASTS/MYCASTS_qa.ipynb
```

**Or use the Jupyter you already have.** Register the project's Python as a kernel, once per
checkout:

```bash
uv run python -m ipykernel install --user --name sbe-qa-processing --display-name "Python (sbe-qa-processing)"
```

Then, in each notebook, choose **Kernel → Change Kernel… → Python (sbe-qa-processing)**. The
generated notebooks ask for the default `python3` kernel, so do this for each new one. Some
notes on this way:
- It needs `ipykernel` from the dev group, so it doesn't work after `uv sync --no-dev`.
- The kernel runs `.venv` at the path where you registered it. If you move the checkout or
  delete `.venv`, register it again.
- `jupyter kernelspec list` shows the registered kernels, and
  `jupyter kernelspec uninstall sbe-qa-processing` removes this one.

The notebook's paths to the fileset, the config, the theme and the output directory are
absolute, so it doesn't matter where Jupyter starts. `--execute-notebook` runs the notebook
with the project's Python and needs neither setup.

## What it checks

**Integrity checks** (PDF, notebook and the command's summary), for every cast, deck tests
included, and summarized for the fileset:
- Raw files complete (`.hex/.dat`, `.XMLCON/.con`, `.hdr`; `.bl` noted) and readable.
- Checksums against the bag's `manifest-md5.txt` or the config's `[fileset] manifest`; n/a
  without a manifest.
- A valid NMEA position (not 0,0), and the position on every scan.
- A cast time that isn't before 1990 (an unset clock) or in the future, and casts in time order
  by file name.
- Header temperature and conductivity serial numbers match the `.XMLCON`.
- Position inside the config's expected area and time inside its dates (n/a without them,
  and for deck tests).
- Informational: the range of cast times and positions, cast count, deck tests, casts with
  bottles fired, instrument model.

**Science checks** (PDF and notebook), per cast that isn't a deck test:
- Primary/secondary temperature, conductivity and salinity agreement below 20 dbar (median).
- Plausible ranges, Wild Edit spikes (seabirdscientific, SBE Data Processing's 2/20 σ settings).
- Lost scans from the modulo counter, pump on in the water.
- Sensor calibration age: warns when a sensor's `.XMLCON` calibration date is more than
  `max_calibration_age_days` (default 365) before the cast.
- Informational: maximum pressure, descent rate and heave, duration, bottle fires.

**Sensor loadout** (PDF and notebook), per cast: the sensor on each frequency and voltage
channel with its serial number, calibration date and age at the time of the cast, and the deck
unit settings (scan rate, suppressed channels, data appended to each scan).

## R2R

The `r2r` commands add R2R's CTD tests, its rating and the R2R QA 2.0 XML. They take a cruise
TOML with R2R's IDs, ports and fileset ID, which come from R2R's catalog:

```bash
# from R2R's catalog API, for a cruise and CTD fileset R2R knows:
uv run sbe-qa-processing r2r config-from-catalog MYCRUISE 123456   # writes configs/MYCRUISE.toml
# or from an existing R2R QA 2.0 report (adds port coordinates):
uv run sbe-qa-processing r2r config-from-qa path/to/r2r_qa.2.0.xml   # configs/MYCRUISE.toml
uv run sbe-qa-processing r2r run configs/MYCRUISE.toml path/to/fileset -o output/MYCRUISE
```

`r2r run` writes the core reports, with R2R's certificate (rating, tests and infos) at the
front of the PDF and in the notebook, named `<CRUISE>_<FILESET>`, plus:

| Output | Contents |
|---|---|
| `<CRUISE>_<FILESET>_qa.2.0.xml` | R2R QA 2.0 rollup report: the same tests, infos, ratings and manifest R2R's `r2r-ctd` produces, in R2R's schema and with its stylesheet reference |

`run -c` takes the same cruise TOML and ignores the R2R fields.

### R2R commands

#### `r2r run`

```text
sbe-qa-processing r2r run [-o OUTPUT_DIR] [--force] [--execute-notebook] [--theme THEME_TOML]
                          CRUISE_TOML FILESET_DIR
```

Assesses a fileset as `run` does, plus R2R's CTD tests, and writes the R2R QA 2.0 XML and the
core reports with R2R's certificate.

| Argument | Default | Description |
|---|---|---|
| `CRUISE_TOML` | required | An [R2R cruise TOML](#r2r-cruise-toml), with the cruise ID and dates |
| `FILESET_DIR` | required | A BagIt bag or directory of casts, as for `run` |
| `-o`, `--output OUTPUT_DIR` | `output` | Directory for the reports |
| `--force` | off | Replace existing reports |
| `--execute-notebook` | off | Run the notebook and save its outputs |
| `--theme THEME_TOML` | none | A [theme](#themes) file; the cruise TOML's `[theme]` applies on top |

It writes `<CRUISE>_<FILESET>_qa.2.0.xml`, `_qa_report.pdf`, `_plots/` and `_qa.ipynb`, where
`<FILESET>` is the R2R fileset ID, or `ctd` without one. It prints the R2R rating and each
test's rating, then the core summary.

#### `r2r config-from-catalog`

```text
sbe-qa-processing r2r config-from-catalog [-o OUTPUT_DIR] [--force] CRUISE_ID FILESET_ID
```

Writes an R2R cruise TOML from R2R's catalog API: the cruise's name, chief scientist, water
body, vessel, operator and scheduler, dates, ports and navigation bounding box. It checks that
`FILESET_ID` is one of the cruise's CTD filesets, and lists them if it isn't.

| Argument | Default | Description |
|---|---|---|
| `CRUISE_ID` | required | R2R cruise ID, e.g. `SP2613` |
| `FILESET_ID` | required | R2R fileset ID, an integer, e.g. `169847` |
| `-o`, `--output OUTPUT_DIR` | `configs` | Directory for `<CRUISE_ID>.toml`, or `-` for stdout |
| `--force` | off | Replace an existing TOML |

The API has no port coordinates, country or state, so the ports have only a name and R2R ID.
Without a navigation bounding box in R2R, it warns and leaves out `[cruise.extent]`; R2R's
location test is then GREY (N) until you add one.

#### `r2r config-from-qa`

```text
sbe-qa-processing r2r config-from-qa [-o OUTPUT_DIR] [--force] R2R_QA_XML
```

Writes an R2R cruise TOML from the fileset info in one of R2R's QA 2.0 reports, including the
ports' coordinates, country and state. Use it to check this tool's results against R2R's.

| Argument | Default | Description |
|---|---|---|
| `R2R_QA_XML` | required | R2R's QA 2.0 XML for the fileset, e.g. the `r2r_qa.2.0.xml` that `r2r fetch-fileset` downloads |
| `-o`, `--output OUTPUT_DIR` | `configs` | Directory for `<CRUISE_ID>.toml`, or `-` for stdout |
| `--force` | off | Replace an existing TOML |

#### `r2r fetch-fileset`

```text
sbe-qa-processing r2r fetch-fileset [-o OUTPUT_DIR] [--force] CRUISE_ID FILESET_ID
```

Downloads a released R2R CTD fileset (a BagIt bag) into
`<OUTPUT_DIR>/<CRUISE_ID>_<FILESET_ID>_ctd/`, with R2R's QA report beside it as
`r2r_qa.2.0.xml` (`r2r_qa.xml` for older filesets).

| Argument | Default | Description |
|---|---|---|
| `CRUISE_ID` | required | R2R cruise ID |
| `FILESET_ID` | required | R2R fileset ID, an integer |
| `-o`, `--output OUTPUT_DIR` | `data` | Directory for the fileset's directory |
| `--force` | off | Download into an existing fileset directory |

### R2R cruise TOML

An R2R cruise TOML is a [config TOML](#config-toml) with R2R's catalog details added. It must
have `[cruise] id`, `depart_date` and `arrive_date`. The `r2r config-from-*` commands write it;
this is `configs/RR2605.toml`, from R2R's QA report:

```toml
[cruise]
id = "RR2605"
name = "R/V Roger Revelle Annual Instrumentation Calibrations"
vessel_id = "33RR"                # R2R's vessel ID: the ICES ship code
operator_id = "edu.ucsd.sio"      # R2R organization IDs
scheduler_id = "org.unols"
depart_date = 2026-07-21          # required
arrive_date = 2026-07-24          # required

[cruise.extent]                   # the NAV location test; without it, GREY (N)
westernmost = -118.632308
easternmost = -117.224462
southernmost = 32.61502
northernmost = 33.370637

[cruise.depart_port]              # and [cruise.arrive_port]
name = "San Diego"
port_id = "100055"                # R2R port ID
country_id = "us"
state_id = "ca"
longitude = -117.175
latitude = 32.71667

[cruise.arrive_port]
name = "San Diego"
port_id = "100055"
country_id = "us"
state_id = "ca"
longitude = -117.175
latitude = 32.71667

[fileset]
id = 170644                       # R2R fileset ID; reports are named <CRUISE>_<FILESET>

[provenance]
distro_type = "post-cruise"
```

The keys it adds to a config TOML, all written to the XML:

| Table | Key | Default | Description |
|---|---|---|---|
| `[cruise]` | `id`, `depart_date`, `arrive_date` | required | Also the dates R2R's "Dates within NAV Ranges" test uses |
| | `vessel_id` | blank | R2R's vessel ID, the ICES ship code (e.g. `33RR`) |
| | `operator_id`, `scheduler_id` | blank | R2R organization IDs (e.g. `edu.ucsd.sio`, `org.unols`) |
| `[cruise.depart_port]`, `[cruise.arrive_port]` | `name`, `port_id`, `country_id`, `state_id`, `longitude`, `latitude` | blank | The ports, as in R2R's catalog |
| `[fileset]` | `id` | blank | R2R's fileset ID. Without it, reports are named `<CRUISE>_ctd` |
| `[provenance]` | `distro_type` | `post-cruise` | R2R's distribution type |
| | `contact_institution_id` | blank | The contact institution's R2R organization ID |
| `[thresholds]` | `yellow_minimum_percent` | `50.0` [%] | An R2R NAV test is YELLOW at or above this share of passing casts, else RED |

### R2R tests

Written to the XML and the certificate:
- Presence of all raw files: every cast has `.hex/.dat`, `.XMLCON/.con` and `.hdr`.
- Valid checksums: every file in the bag's `manifest-md5.txt` matches. BLACK (X) without a
  manifest.
- Lat/lon and dates within the cruise extent and dates (deck tests excluded).
- Infos: raw file count, casts with bottles fired, instrument model, casts with NAV on every
  scan, and casts with missing files, unreadable `.hex` or `.XMLCON`, deck tests, sensor serial
  number mismatches (`.hdr` vs `.XMLCON`), or bad/outside navigation.

### Validation against R2R

`tests/test_r2r_comparison.py` runs the tool on two released R2R filesets and requires the
XML's `certificate` (ratings, results, bounds, infos) and manifest to be identical to R2R's own
reports:

| Fileset | R2R | This tool |
|---|---|---|
| SP2613 / 169847 (Sproul) | G | G, certificate identical |
| RR2605 / 170644 (Revelle) | Y (one cast outside the extent) | Y, certificate identical |
| BH18-18 / 132368 (Blue Heron) | no QA 2.0 report | R presence test (a cast with only a `.hdr`), SBE 21 configuration flagged |

Fetch them with:

```bash
uv run sbe-qa-processing r2r fetch-fileset SP2613 169847   # into data/SP2613_169847_ctd/
uv run sbe-qa-processing r2r fetch-fileset RR2605 170644
uv run sbe-qa-processing r2r fetch-fileset BH18-18 132368
uv run pytest
```

R2R doesn't document every rule; the ones inferred here (deck tests excluded from NAV tests, a
NAV test is YELLOW at ≥ 50% of casts, the overall rating counts individual per-cast checks) are
noted in `src/sbe_qa_processing/integrations/r2r/rules.py` and reproduce R2R's ratings for both
filesets.

## OpenVDM

[OpenVDM](https://github.com/OceanDataTools/openvdm) can run the QA after each CTD transfer, as a
`postCollectionSystemTransfer` hook. It writes the core reports (PDF, SVG plots, notebook), not
R2R's. The hook reads the current cruise from OpenVDM's API
(`api/warehouse/getCruiseConfig`): cruise ID, name, PI, location, dates, the cruise extent, the
warehouse directory, the CTD transfer's destination, the extra directories and the MD5 summary.
From OpenVDM 2.17, the vessel's name and contact come from the `vessel` block of `openvdm.yaml`,
which OpenVDM's installer asks for. With `--site-root` instead of `--openvdm-config`, they come
from the copy in the cruise's `ovdmConfig.json`.

1. Install this project with the `openvdm` extra (pyyaml, to read `openvdm.yaml`) somewhere
   OpenVDM's worker can run it (it has its own environment, not OpenVDM's venv), and fetch the
   map data: `uv sync --extra openvdm && uv run sbe-qa-processing fetch-map-data`.
2. In OpenVDM, add an extra directory for the reports (Configuration > Extra Directories), named
   `CTD_QA` by default, e.g. with destination `Products/CTD_QA`.
3. Write the ship's site config once, for what OpenVDM doesn't store: the reports' extra
   directory, an expected cruise area and QA thresholds, and, for OpenVDM before 2.17, the
   vessel name and the report contact. It prompts for each field and works offline. Fields
   already in `openvdm.yaml`'s `vessel` block aren't asked for, since the hook uses OpenVDM's
   values ahead of the site config's:

   ```bash
   uv run sbe-qa-processing site-config   # writes configs/site.toml
   ```

   It reads `openvdm.yaml` from `/opt/openvdm/server/etc/`. If OpenVDM is installed elsewhere
   (the installer's `INSTALL_ROOT`), pass `--openvdm-config <INSTALL_ROOT>/openvdm/server/etc/openvdm.yaml`.

   Run it again with `--force` to change the file; its current values are the defaults. QA
   thresholds (e.g. for fresh water) are edited by hand under `[thresholds]`.
   `configs/site.example.toml` shows every field. A site config from before the hook dropped
   R2R still works: its R2R settings are ignored, with a note, and rewriting it with
   `site-config --force` drops them.
4. Add the hook to OpenVDM's `openvdm.yaml`, `/opt/openvdm/server/etc/openvdm.yaml` by default.
   `--openvdm-config` tells the hook where that file is, since OpenVDM may be installed outside
   `/opt`:

```yaml
postHookCommands:
    postCollectionSystemTransfer:
        - collectionSystemTransferName: CTD
          commandList:
          - name: "SBE 9 QA report"
            command:
            - /opt/sbe-qa-processing/.venv/bin/sbe-qa-processing
            - openvdm
            - "{collectionSystemTransferName}"
            - "--openvdm-config=/opt/openvdm/server/etc/openvdm.yaml"  # <INSTALL_ROOT>/openvdm/...
            - "--site-config=/opt/sbe-qa-processing/configs/site.toml"
            - "--changed-files={newFiles}"
            - "--changed-files={updatedFiles}"
```

The hook:
- **skips** transfers whose new or updated files include no `.hex/.dat`, `.XMLCON/.con`, `.hdr`
  or `.bl` (use the `--changed-files={newFiles}` form, so an empty list isn't dropped);
- writes the PDF, SVG plots and notebook into the extra directory as `<cruise>_qa_report.pdf`,
  `<cruise>_plots/` and `<cruise>_qa.ipynb`, plus `<cruise>_cruise.toml`, a snapshot of what
  OpenVDM supplied that the notebook re-runs from, and gives them to the warehouse user when run
  as root;
- adds those files to the cruise's MD5 summary, with OpenVDM's `utils/update_md5_summary.py`
  (OpenVDM 2.16.1 or later), found from `--openvdm-config`'s install directory, or
  `/opt/openvdm` with `--site-root`. If that fails (e.g. an older OpenVDM), the hook logs a
  warning and still succeeds, and the reports reach the summary at its next full rebuild;
- checks checksums against OpenVDM's MD5 summary. Files changed after the summary was written
  count as pending, not failures, since OpenVDM updates the summary in parallel with the hook;
- takes the cruise extent from OpenVDM's `cruiseExtent`, else from the site config's `[extent]`;
  without either, the area check is n/a. OpenVDM 2.17 stores the extent, which
  `build_cruise_tracks.py` sets from the ship's track each time the data dashboard runs. A cast
  near the newest end of the track can fall just outside it until the next dashboard update;
  the next hook run then passes it. A box across the antimeridian has westernmost > easternmost,
  as in R2R's reports;
- accepts and ignores `--fileset-id`, which older hook commands pass;
- exits non-zero with a message on failure, which OpenVDM shows.

Before this version, the hook also wrote R2R's QA XML and named its reports
`<cruise>_ctd_...`. Those older files stay in the extra directory until removed by hand. To
check a cruise against R2R's tests, use `r2r run` with a cruise TOML from R2R's catalog.

### OpenVDM commands

#### `openvdm`

```text
sbe-qa-processing openvdm [--openvdm-config OPENVDM_YAML | --site-root URL]
                          [--site-config SITE_TOML] [--changed-files FILES]
                          [--execute-notebook] [--theme THEME_TOML] [--fileset-id FILESET_ID]
                          [TRANSFER]
```

The post-transfer hook: assesses the collection system transfer's CTD files for OpenVDM's
current cruise, and writes the reports into an OpenVDM extra directory, replacing earlier ones.
Installed `openvdm.yaml` files call it, so its arguments keep their names.

| Argument | Default | Description |
|---|---|---|
| `TRANSFER` | `CTD` | The collection system transfer's name; in a hook, `{collectionSystemTransferName}` |
| `--openvdm-config OPENVDM_YAML` | `/opt/openvdm/server/etc/openvdm.yaml` | OpenVDM's settings: its web root (`siteRoot`) and, from OpenVDM 2.17, the `vessel` block. Needs the `openvdm` extra |
| `--site-root URL` | none | OpenVDM's web root, instead of `--openvdm-config`. The vessel block then comes from the cruise's `ovdmConfig.json` |
| `--site-config SITE_TOML` | none | The ship's [site TOML](#site-toml) |
| `--changed-files FILES` | none | OpenVDM's `{newFiles}` and `{updatedFiles}`; repeatable. When given, the QA runs only if they include CTD files. Use the `--changed-files={newFiles}` form, so an empty list isn't dropped |
| `--execute-notebook` | off | Run the notebook and save its outputs |
| `--theme THEME_TOML` | none | A [theme](#themes) file; the site TOML's `[theme]` applies on top |
| `--fileset-id FILESET_ID` | none | Ignored, with a warning; older hook commands pass it |

#### `config-from-openvdm`

```text
sbe-qa-processing config-from-openvdm [--openvdm-config OPENVDM_YAML | --site-root URL]
                                      [--site-config SITE_TOML] [-o OUTPUT_DIR] [--force]
                                      [TRANSFER]
```

Builds the config TOML the hook would use, from OpenVDM's current cruise and the site TOML,
without running the QA, and reports what's missing or assumed. `TRANSFER`, `--openvdm-config`,
`--site-root` and `--site-config` are as for `openvdm`.

| Argument | Default | Description |
|---|---|---|
| `-o`, `--output OUTPUT_DIR` | `configs` | Directory for `<CRUISE_ID>.toml`, or `-` for stdout |
| `--force` | off | Replace an existing TOML |

#### `site-config`

```text
sbe-qa-processing site-config [-o SITE_TOML] [--openvdm-config OPENVDM_YAML] [--force]
```

Writes the ship's [site TOML](#site-toml) by prompting for each field on stderr; Enter keeps
the default in brackets, and `-` clears it. Over an existing file (with `--force`), its values
are the defaults. It works offline.

| Argument | Default | Description |
|---|---|---|
| `-o`, `--output SITE_TOML` | `configs/site.toml` | The file to write, or `-` for stdout |
| `--openvdm-config OPENVDM_YAML` | `/opt/openvdm/server/etc/openvdm.yaml`, when it exists | Settings already in its `vessel` block (OpenVDM 2.17) aren't asked for |
| `--force` | off | Replace an existing site TOML |

### Site TOML

The per-ship settings OpenVDM doesn't store. Every field is optional;
`configs/site.example.toml` shows them all.

| Table | Key | Default | Description |
|---|---|---|---|
| `[vessel]` | `name` | blank | The vessel's name, for the report header. From OpenVDM 2.17, `openvdm.yaml`'s `vessel.name` wins |
| `[extent]` | `westernmost`, `easternmost`, `southernmost`, `northernmost` | none | The expected cruise area, used when OpenVDM has no `cruiseExtent` |
| `[openvdm]` | `output_extra_directory` | `CTD_QA` | The OpenVDM extra directory the reports go into |
| `[provenance]` | `contact_institution`, `contact_email` | blank | The report contact. From OpenVDM 2.17, `openvdm.yaml`'s `vessel.contact` wins |
| `[thresholds]` | as in a [config TOML](#config-toml) | defaults | e.g. for fresh water |
| `[theme]` | as in a [config TOML](#config-toml) | none | The reports' look; paths relative to the site TOML |

R2R settings from older site TOMLs (`[vessel] id`, `[cruise] operator_id` and
`scheduler_id`, `[provenance] distro_type` and `contact_institution_id`,
`[thresholds] yellow_minimum_percent`) are ignored with a note.

### Reviewing the hook's config

To review the config TOML before relying on the hook, or to run the QA by hand, write it without
running the QA:

```bash
uv run sbe-qa-processing config-from-openvdm --site-config configs/site.toml \
    --openvdm-config /opt/openvdm/server/etc/openvdm.yaml                      # configs/<CRUISE>.toml
uv run sbe-qa-processing run /path/to/cruise/CTD -c configs/<CRUISE>.toml -o output/<CRUISE>
```

`config-from-openvdm` builds the TOML the same way the hook builds its snapshot, and it
reports what's missing or assumed: where the cruise extent came from, an open-ended cruise's
end date (set to today) and site config settings that are no longer used. The hook still
rebuilds the TOML on every run, since OpenVDM's cruise details and extent change during a
cruise.

## seabirdscientific version

seabirdscientific comes from the **`integration`** branch of the fork
[webbpinner/seabirdscientific](https://github.com/webbpinner/seabirdscientific): upstream's
`v3.0.0` branch plus the fork's pending changes. `uv.lock` pins the commit (currently
`1290437`, version `3.0.0a41+fork`); after the branch changes, update with
`uv lock --upgrade-package seabirdscientific`.

Two of the fixes this tool needs (the status bits and the TS contour grid, below) were merged
into upstream's `v3.0.0` branch on 2026-10-12 and 13, but no release has them yet: the newest on
PyPI is the `3.0.0a36` pre-release, and the newest stable release is 2.8.1. The fork still
carries the `potential_temperature` fix and its build, test and docs changes. Once a release
has the fixes, this project will depend on it instead of the fork. Until then, the tool also
works on releases without them, with the workarounds noted.

To develop against a local seabirdscientific checkout instead, install it over the locked one
with `uv pip install -e path/to/seabirdscientific` and run commands with `uv run --no-sync`
(a plain `uv run` or `uv sync` puts the locked version back).

- **911plus status bits read in reverse**
  ([fork issue 24](https://github.com/webbpinner/seabirdscientific/issues/24); fixed upstream
  on the `v3.0.0` branch, not yet released). Older seabirdscientific reads the status nibble
  most significant bit first, so its "pump status" is really the modem bit. `status.py` checks
  at runtime how the installed version decodes a scan with only the pump bit set, and reads the
  bits correctly either way. Real data shows the documented order: deck tests 0b0010, casts
  0b0011, 0b0111 while bottles fire.
- **TS contour grid**
  ([fork issue 21](https://github.com/webbpinner/seabirdscientific/issues/21); fixed upstream
  on the `v3.0.0` branch, not yet released). Older versions stop the grid short of the coldest
  samples and can return a one-column grid for a narrow salinity range (e.g. fresh water); the
  TS plot then skips the contours. Upstream's fix also uses a finer temperature step (0.1 °C)
  and wider salinity padding, so the σ0 contours are smoother than with the earlier fork fix.
  Either way, the plot zooms to the data and picks contour levels from the density range in
  view.
- **`eos80_processing.potential_temperature` ignored in-situ pressure**
  ([fork issue 19](https://github.com/webbpinner/seabirdscientific/issues/19); fixed on
  `integration`). Not used by this tool.
- Temperature and conductivity coefficients have no slope/offset in seabirdscientific; the
  `.XMLCON` slope/offset is applied here. Digiquartz pressure slope/offset is applied by
  seabirdscientific.

## License

[MIT](LICENSE)
