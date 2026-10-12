# sbe-qa-processing

> [!WARNING]
> **Under heavy development.** This project is shared for peer review. Its outputs, command
> line, config format and Python API may change without notice, and its results haven't been
> validated beyond the R2R filesets listed under [Validation against R2R](#validation-against-r2r).
> Don't rely on it for operational QA yet. Issues and comments are welcome.

Quality assessment for Sea-Bird **SBE 9/911plus** CTD filesets. From a fileset (a BagIt bag as
R2R/NCEI distribute it, or a plain directory of Seasave output) and an optional config, it
writes the files below. Their names start with the config's cruise ID, or the fileset
directory's name without one (`<TITLE>`). With R2R's tests (`r2r run`), they're named
`<CRUISE>_<FILESET>`.

| Output | Contents |
|---|---|
| `<TITLE>_qa_report.pdf` | R2R certificate (with `r2r run`); a cast map with coastlines and a world inset; and per cast: profiles, dual-sensor differences, TS diagram, pressure record, integrity and science checks and sensor loadout |
| `<TITLE>_plots/*.svg` | Every report figure as SVG: the cast map, and each cast's profiles, TS diagram and pressure record. The PDF embeds these same SVGs as vector graphics |
| `<TITLE>_qa.ipynb` | A notebook that re-runs the whole assessment step by step, to inspect or change it |
| `<CRUISE>_<FILESET>_qa.2.0.xml` | With `r2r run` only: the R2R QA 2.0 rollup report, with the same tests, infos, ratings and manifest R2R's `r2r-ctd` produces, in R2R's schema and with its stylesheet reference |

Raw `.hex` data is read and converted with
[seabirdscientific](https://github.com/Sea-BirdScientific/seabirdscientific), installed from
the `integration` branch of [a fork](https://github.com/webbpinner/seabirdscientific) that
carries fixes not yet released upstream (see [seabirdscientific version](#seabirdscientific-version)).
The `.XMLCON` parser lives here, since seabirdscientific doesn't have one.

## Setup

Needs [uv](https://docs.astral.sh/uv/) (`brew install uv`) and git.

```bash
git clone https://github.com/OceanDataTools/sbe-qa-processing.git
cd sbe-qa-processing
uv sync
uv run sbe-qa-processing fetch-map-data   # Natural Earth coastlines for the maps (~27 MB, once)
```

seabirdscientific is installed from the `integration` branch of
[webbpinner/seabirdscientific](https://github.com/webbpinner/seabirdscientific), not from PyPI,
whose release lacks the fixes this tool needs. `uv sync` installs the exact commit pinned in
`uv.lock`. `pip install git+https://github.com/OceanDataTools/sbe-qa-processing.git` also works,
and takes the branch's latest commit.

The fork is temporary. Once its fixes are released in the official
[seabirdscientific](https://github.com/Sea-BirdScientific/seabirdscientific), this project will
depend on that release instead. Until then, the fork's `integration` branch is rebuilt as fixes
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
that need an expected area or dates are n/a. A config sets those and the thresholds; every
field is optional (see `src/sbe_qa_processing/config.py`):

```toml
[cruise]
id = "LAKE1"        # the reports' title
name = "..."
depart_date = 2026-07-09   # expected dates
arrive_date = 2026-07-10

[cruise.extent]     # expected area
westernmost = -88.0
easternmost = -87.0
southernmost = 42.0
northernmost = 43.0

[thresholds]        # e.g. fresh water
salinity_range = [0.0, 42.0]
```

### R2R

The `r2r` commands add R2R's CTD tests, its rating and the R2R QA 2.0 XML. They take a cruise
TOML with R2R's IDs, ports and fileset ID, which come from R2R's catalog:

```bash
# from R2R's catalog API, for a cruise and CTD fileset R2R knows:
uv run sbe-qa-processing r2r config-from-catalog MYCRUISE 123456   # writes configs/MYCRUISE.toml
# or from an existing R2R QA 2.0 report (adds port coordinates):
uv run sbe-qa-processing r2r config-from-qa path/to/r2r_qa.2.0.xml   # configs/MYCRUISE.toml
uv run sbe-qa-processing r2r run configs/MYCRUISE.toml path/to/fileset -o output/MYCRUISE
```

`run -c` takes the same cruise TOML and ignores the R2R fields.

Every command is listed by `sbe-qa-processing --help`, and documents its arguments under
`sbe-qa-processing COMMAND --help`. They share these conventions:
- `-o/--output` is a directory; only `site-config` writes a single file. Commands that make a
  cruise TOML write `<OUTPUT_DIR>/<CRUISE_ID>.toml` (default `configs/`), or to stdout with
  `-o -`.
- Existing outputs, reports included, aren't overwritten without `--force`. The exception is
  the `openvdm` hook, which replaces its reports after each transfer.

## Running from OpenVDM

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

To review the config TOML before relying on the hook, or to run the QA by hand, write it without
running the QA:

```bash
uv run sbe-qa-processing config-from-openvdm --site-config configs/site.toml \
    --openvdm-config /opt/openvdm/server/etc/openvdm.yaml                      # configs/<CRUISE>.toml
uv run sbe-qa-processing run /path/to/cruise/CTD -c configs/<CRUISE>.toml -o output/<CRUISE>
```

`config-from-openvdm` takes the same OpenVDM options as the hook, plus `-o DIR` (or `-o -` for
stdout) and `--force`. It builds the TOML the same way the hook builds its snapshot, and it
reports what's missing or assumed: where the cruise extent came from, an open-ended cruise's
end date (set to today) and site config settings that are no longer used. The hook still
rebuilds the TOML on every run, since OpenVDM's cruise details and extent change during a
cruise.

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
- Position inside the cruise extent and time inside the cruise dates (n/a for deck tests).
- Informational: the range of cast times and positions, cast count, deck tests, casts with
  bottles fired, instrument model.

**R2R tests** (`r2r run`; written to the XML and the PDF):
- Presence of all raw files: every cast has `.hex/.dat`, `.XMLCON/.con` and `.hdr`.
- Valid checksums: every file in the bag's `manifest-md5.txt` matches. BLACK (X) without a
  manifest.
- Lat/lon and dates within the cruise extent and dates (deck tests excluded).
- Infos: raw file count, casts with bottles fired, instrument model, casts with NAV on every
  scan, and casts with missing files, unreadable `.hex` or `.XMLCON`, deck tests, sensor serial
  number mismatches (`.hdr` vs `.XMLCON`), or bad/outside navigation.

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

## Validation against R2R

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
noted in `src/sbe_qa_processing/integrations/r2r/rules.py` and reproduce R2R's ratings for both filesets.

## seabirdscientific version

seabirdscientific comes from the **`integration`** branch of the fork
`webbpinner/seabirdscientific`: upstream's `v3.0.0` plus every pending fix (including the three
below). `uv.lock` pins the commit; after the branch changes, update with
`uv lock --upgrade-package seabirdscientific`. The tool also works on plain upstream `v3.0.0`,
with the workarounds noted.

To develop against a local seabirdscientific checkout instead, install it over the locked one
with `uv pip install -e path/to/seabirdscientific` and run commands with `uv run --no-sync`
(a plain `uv run` or `uv sync` puts the locked version back).

- **911plus status bits read in reverse** (fork issue 24, fixed on `integration`). Older
  seabirdscientific reads the status nibble most significant bit first, so its "pump status" is
  really the modem bit. `status.py` checks at runtime how the installed version decodes a scan
  with only the pump bit set, and reads the bits correctly either way. Real data shows the
  documented order: deck tests 0b0010, casts 0b0011, 0b0111 while bottles fire.
- **TS contour grid** (fork issue 21, fixed on `integration`). Older versions return a
  one-column grid for a narrow salinity range (e.g. fresh water), and the TS plot then skips the
  contours. Either way, the plot zooms to the data and picks contour levels from the density
  range in view.
- **`eos80_processing.potential_temperature` ignored in-situ pressure** (fork issue 19, fixed on
  `integration`). Not used by this tool.
- Temperature and conductivity coefficients have no slope/offset in seabirdscientific; the
  `.XMLCON` slope/offset is applied here. Digiquartz pressure slope/offset is applied by
  seabirdscientific.

## License

[MIT](LICENSE)
