# Cookbook

Worked examples, end to end: assessing **a directory of Seasave files** (a ship's own CTD
data), and assessing **an R2R fileset** (downloaded from R2R, with R2R's tests and QA 2.0
XML). Every command and output here comes from a real run. The [README](README.md) covers
installing and lists every command, argument and TOML key.

The examples call `sbe-qa-processing`. From a clone, that's `uv run sbe-qa-processing`; from an
install like the OpenVDM hook's, `/opt/sbe-qa-processing/.venv/bin/sbe-qa-processing`.

- [A directory of Seasave files](#a-directory-of-seasave-files)
  - [A quick look](#a-quick-look)
  - [A proper run, with a config TOML](#a-proper-run-with-a-config-toml)
  - [Change a threshold and run again](#change-a-threshold-and-run-again)
  - [The notebook](#the-notebook)
  - [From Python](#from-python)
- [An R2R fileset](#an-r2r-fileset)
  - [Download it](#download-it)
  - [Make its cruise TOML](#make-its-cruise-toml)
  - [Run R2R's assessment](#run-r2rs-assessment)
  - [Compare with R2R's report](#compare-with-r2rs-report)
  - [Without R2R's tests](#without-r2rs-tests)
- [An OpenVDM cruise, by hand](#an-openvdm-cruise-by-hand)

## A directory of Seasave files

The example is Sproul's SP2613, a one-day cruise with four stations and a deck test, copied to
`sp2613_raw/`. Each cast is a `.hex` with its `.XMLCON`, `.hdr` and `.bl`:

```text
sp2613_raw/
  SP2613_DeckTest.bl   SP2613_DeckTest.hdr   SP2613_DeckTest.hex   SP2613_DECKTEST.XMLCON
  SP2613_Station1.bl   SP2613_Station1.hdr   SP2613_Station1.hex   SP2613_STATION1.XMLCON
  ...
  SP2613_Station4.bl   SP2613_Station4.hdr   SP2613_Station4.hex   SP2613_STATION4.XMLCON
```

A cast's files are matched by name ignoring case, so `SP2613_STATION1.XMLCON` goes with
`SP2613_Station1.hex`. The directory is searched recursively, and a cast whose name contains
`deck`, `dock` or `test` is a deck test: its files are checked, but not its data.

### A quick look

With no config, `run` assesses every cast and names the reports after the directory:

```bash
sbe-qa-processing run sp2613_raw -o out_quick
```

```text
sp2613_raw: 5 casts
  integrity checks: 0 warnings/failures
  science checks: 4 warnings/failures
    SP2613_Station1: Sensor calibration age warn (3 of 9 older than 365 days)
    SP2613_Station2: Sensor calibration age warn (3 of 9 older than 365 days)
    SP2613_Station3: Sensor calibration age warn (3 of 9 older than 365 days)
    SP2613_Station4: Sensor calibration age warn (3 of 9 older than 365 days)
wrote out_quick/sp2613_raw_qa_report.pdf
wrote out_quick/sp2613_raw_plots
wrote out_quick/sp2613_raw_qa.ipynb
```

Without an expected area or dates, the checks that need them are n/a. For a real assessment,
give the cruise a config.

### A proper run, with a config TOML

A config TOML names the cruise, gives the expected area and dates for the integrity checks, and
any limits that differ from the defaults. Every key is optional:

```toml
# SP2613.toml
[cruise]
id = "SP2613"                        # the reports' title and file name prefix
name = "MPL Internship Program Day at Sea"
pi = "Pereira, Filipe"
vessel_name = "Sproul"
depart_date = 2026-07-09             # expected cast dates: TOML dates, not strings
arrive_date = 2026-07-09

[cruise.extent]                      # expected area, decimal degrees
westernmost = -117.3773103608
easternmost = -117.2262308092
southernmost = 32.5997440998
northernmost = 32.7053695673

[provenance]
contact_institution = "Scripps Institution of Oceanography"
```

```bash
sbe-qa-processing run sp2613_raw -c SP2613.toml -o output/SP2613
```

```text
SP2613: 5 casts
  integrity checks: 0 warnings/failures
  science checks: 4 warnings/failures
    SP2613_Station1: Sensor calibration age warn (3 of 9 older than 365 days)
    ...
wrote output/SP2613/SP2613_qa_report.pdf
wrote output/SP2613/SP2613_plots
wrote output/SP2613/SP2613_qa.ipynb
```

`output/SP2613/` then holds the PDF report, every figure as an SVG in `SP2613_plots/` (the cast
map, and each station's profiles, TS diagram and pressure record) and `SP2613_qa.ipynb`.

- **An area across the antimeridian:** `westernmost` greater than `easternmost`, e.g. 178 to
  -178, is a box across 180°, as in R2R's reports and OpenVDM's `cruiseExtent`.
- **A plain directory's checksums:** a bag's `manifest-md5.txt` is checked automatically; for a
  plain directory, point `[fileset] manifest` at an MD5 list such as OpenVDM's MD5 summary.
- **A typo in the TOML** is an error that says where the key belongs:

  ```text
  sbe-qa-processing: typo.toml: unknown key: [cruise] depart_dat: did you mean depart_date?
  ```

### Change a threshold and run again

`run` won't replace existing reports:

```text
output/SP2613/SP2613_qa_report.pdf exists; use --force to overwrite the reports
```

Add or edit the config's `[thresholds]` (the README's [Config TOML](README.md#config-toml)
lists every limit), then run with `--force`, or into a new `-o` directory to keep both. Say this
ship calibrates every two years:

```toml
[thresholds]
max_calibration_age_days = 730    # this ship calibrates every two years
```

```bash
sbe-qa-processing run sp2613_raw -c SP2613.toml -o output/SP2613 --force
```

```text
SP2613: 5 casts
  integrity checks: 0 warnings/failures
  science checks: 4 warnings/failures
    SP2613_Station1: Sensor calibration age warn (1 of 9 older than 730 days)
    ...
```

### The notebook

`SP2613_qa.ipynb` re-runs the whole assessment, step by step: reading and converting each cast,
the integrity and science checks, the sensor loadout, the bottle fires and every figure, then
writing the reports. Open it in Jupyter or VS Code ([Opening the
notebook](README.md#opening-the-notebook)) and edit a threshold to see its effect. `run
--execute-notebook` saves it with its outputs already in it (needs the dev dependencies: an
install without `--no-dev`).

### From Python

```python
import warnings

warnings.filterwarnings("ignore", "The seawater library is deprecated")

from sbe_qa_processing.qa import run_qa
from sbe_qa_processing.reports import write_reports

result = run_qa("sp2613_raw", "SP2613.toml")  # or a QAConfig built in Python
print(result)
station = result.science_casts[0]
print(station.name, station.scans, f"{station.pressure.max():.1f} dbar")
for check in result.science[station.name]:
    print(f"  {check.status:4}  {check.name}: {check.value}")
```

```text
SP2613: 5 casts (5 converted, 1 deck test) in sp2613_raw
  run 2026-10-26 15:02:55 UTC, 1.5 s; sbe-qa-processing 0.1.0, seabirdscientific 3.1.1, gsw 3.6.23, numpy 2.5.3
  integrity checks: 9 pass, 6 info, 1 n/a
  science checks: 48 pass, 4 warn, 16 info
    SP2613_Station1: Sensor calibration age warn (1 of 9 older than 730 days)
    ...
SP2613_Station1 39865 192.9 dbar
  pass  Dual temperature difference (median): 0.001826 °C
  pass  Dual conductivity difference (median): 0.000348 S/m
  pass  Dual salinity difference (median): 0.002627 PSU
  ...
  warn  Sensor calibration age: 1 of 9 older than 730 days
  info  Maximum pressure: 192.9 dbar
  info  Downcast descent rate (median): 0.20 dbar/s
  info  Duration: 27.7 min, 39865 scans
```

Each cast in `result.casts` is a `CastData` of per-scan numpy arrays (`pressure`,
`temperature`, `salinity`, ...; `None` for a channel the cast doesn't have), and
`result.integrity` and `result.science` hold every check. `write_reports(result, "output/SP2613")`
writes the reports.

## An R2R fileset

R2R distributes raw CTD filesets as BagIt bags, and for released filesets it has a QA 2.0
report. The R2R commands download them, make a cruise TOML from R2R's catalog, and run the
assessment with R2R's CTD tests, writing a QA 2.0 XML beside the core reports. The example is
SP2613 again, as R2R released it: CTD fileset 169847.

### Download it

```bash
sbe-qa-processing r2r fetch-fileset SP2613 169847
```

```text
25 files from https://g-1e773d.99817a.0ec8.data.globus.org/SP2613/SP2613_169847_ctd.tar.gz -> data/SP2613_169847_ctd
```

```text
data/SP2613_169847_ctd/
  bagit.txt, bag-info.txt, manifest-md5.txt, tagmanifest-md5.txt, file-info.txt
  data/                                  the raw Seasave files
  r2r_qa.2.0.xml                         R2R's QA report
```

The report is the one R2R's catalog lists for the fileset (`api/qa_info`). When R2R has none,
the command says "R2R has no QA report for this fileset"; when R2R lists one but doesn't serve
it, it says so, with R2R's answer. Either way the bag is in.

### Make its cruise TOML

```bash
sbe-qa-processing r2r config-from-catalog SP2613 169847
```

writes `configs/SP2613.toml` from R2R's catalog: the cruise's dates, IDs, ports and extent.

```toml
# Cruise metadata from R2R's catalog API, 2026-10-26
[cruise]
id = "SP2613"
name = "MPL Internship Program Day at Sea"
pi = "Pereira, Filipe"
vessel_id = "32QU"
vessel_name = "Sproul"
operator_id = "edu.ucsd.sio"
scheduler_id = "org.unols"
depart_date = 2026-07-09
arrive_date = 2026-07-09

[cruise.extent]
westernmost = -117.3773103608
easternmost = -117.2262308092
southernmost = 32.5997440998
northernmost = 32.7053695673

[cruise.depart_port]
name = "San Diego"
port_id = "100055"

[cruise.arrive_port]
name = "San Diego"
port_id = "100055"

[fileset]
id = "169847"

[provenance]
distro_type = "post-cruise"
```

R2R's catalog has no port coordinates, country or state. When you have the fileset's QA 2.0
report, `r2r config-from-qa data/SP2613_169847_ctd/r2r_qa.2.0.xml` makes the TOML from the
report instead, ports and all, with no request to R2R.

### Run R2R's assessment

```bash
sbe-qa-processing r2r run configs/SP2613.toml data/SP2613_169847_ctd -o output/SP2613-r2r
```

```text
SP2613_169847: R2R rating G
  G  Presence of All Raw Files
  G  Valid Checksum for All Files in Manifest
  G  Lat/Lon within NAV Ranges
  G  Dates within NAV Ranges
  integrity checks: 0 warnings/failures
  science checks: 4 warnings/failures
    SP2613_Station1: Sensor calibration age warn (3 of 9 older than 365 days)
    ...
wrote output/SP2613-r2r/SP2613_169847_qa.2.0.xml
wrote output/SP2613-r2r/SP2613_169847_qa_report.pdf
wrote output/SP2613-r2r/SP2613_169847_plots
wrote output/SP2613-r2r/SP2613_169847_qa.ipynb
```

`output/SP2613-r2r/` gets the core reports, with R2R's certificate in the PDF and R2R's tests in
the notebook, named `<CRUISE_ID>_<FILESET_ID>`, plus `SP2613_169847_qa.2.0.xml`, the QA 2.0
report, with the same structure and stylesheet reference as R2R's own reports.

### Compare with R2R's report

The two reports have the same schema, so their ratings can be read side by side:

```python
import xml.etree.ElementTree as ET

R2R = {"r2r": "https://service.rvdata.us/schema/r2r-2.0"}


def ratings(path):
    """The certificate's rating, and each test's"""
    certificate = ET.parse(path).getroot().find("r2r:certificate", R2R)
    tests = {
        test.get("name"): test.findtext("r2r:rating", namespaces=R2R)
        for test in certificate.iterfind("r2r:tests/r2r:test", R2R)
    }
    return certificate.findtext("r2r:rating", namespaces=R2R), tests


print(ratings("data/SP2613_169847_ctd/r2r_qa.2.0.xml"))  # R2R's
print(ratings("output/SP2613-r2r/SP2613_169847_qa.2.0.xml"))  # ours
```

```text
('G', {'Presence of All Raw Files': 'G', 'Valid Checksum for All Files in Manifest': 'G', 'Lat/Lon within NAV Ranges': 'G', 'Dates within NAV Ranges': 'G'})
('G', {'Presence of All Raw Files': 'G', 'Valid Checksum for All Files in Manifest': 'G', 'Lat/Lon within NAV Ranges': 'G', 'Dates within NAV Ranges': 'G'})
```

The tests go further: `tests/test_r2r_comparison.py` requires the whole certificate and
manifest to be identical to R2R's for SP2613, RR2605 and BH18-18 (README, [Validation against
R2R](README.md#validation-against-r2r)).

### Without R2R's tests

The core `run` reads an R2R bag and an R2R cruise TOML too, and writes the core reports without
R2R's tests, certificate or XML:

```bash
sbe-qa-processing run data/SP2613_169847_ctd -c configs/SP2613.toml -o output/SP2613-core
```

## An OpenVDM cruise, by hand

On a ship running OpenVDM, the hook runs the QA after each CTD transfer (README,
[OpenVDM](README.md#openvdm)). To run it yourself, give the hook's command without
`--changed-files`:

```bash
sudo /opt/sbe-qa-processing/.venv/bin/sbe-qa-processing openvdm CTD \
    --site-config=/opt/sbe-qa-processing/configs/site.toml
```

or write out the config the hook would use and run it anywhere, e.g. to try other thresholds
without touching OpenVDM's extra directory:

```bash
sbe-qa-processing config-from-openvdm CTD --site-config configs/site.toml   # configs/<CRUISE_ID>.toml
sbe-qa-processing run /path/to/cruise/CTD -c configs/<CRUISE_ID>.toml -o output/<CRUISE_ID>
```
