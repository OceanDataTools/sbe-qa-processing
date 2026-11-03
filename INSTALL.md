# Installing sbe-qa-processing

sbe-qa-processing needs **Python 3.11 or later**, [uv](https://docs.astral.sh/uv/) and git. uv
uses a Python 3.11+ already on the system, or downloads the newest, so an older system Python
doesn't matter. Every dependency (seabirdscientific, gsw, numpy, matplotlib, cartopy, shapely,
reportlab, svglib) installs as a binary wheel on Linux (x86_64 and aarch64), macOS and Windows,
so no compiler, GEOS or PROJ is needed.

## Platform:

- [macOS](#macos)
- [Debian and Ubuntu](#debian-and-ubuntu)
- [RHEL, AlmaLinux and Rocky Linux](#rhel-almalinux-and-rocky-linux)
- [Windows](#windows)

The Linux commands below were run in clean containers (Debian 12, Ubuntu 22.04 and 24.04,
AlmaLinux 8, Rocky Linux 9; aarch64) through to the test suite and a QA run on a committed
fileset.

### macOS

```bash
brew install uv                       # git comes with the Xcode command line tools
git clone https://github.com/OceanDataTools/sbe-qa-processing.git
cd sbe-qa-processing
uv sync
uv run pytest
```

### Debian and Ubuntu

| Release | System Python | Use |
|---|---|---|
| Debian 12 | 3.11 | the system Python |
| Ubuntu 24.04 LTS | 3.12 | the system Python |
| Ubuntu 22.04 LTS | 3.10, too old | a Python uv downloads |

```bash
sudo apt-get update
sudo apt-get install -y git curl ca-certificates python3    # python3: not needed on 22.04
curl -LsSf https://astral.sh/uv/install.sh | sh             # uv in ~/.local/bin
source "$HOME/.local/bin/env"                               # uv on the PATH; or a new shell
git clone https://github.com/OceanDataTools/sbe-qa-processing.git
cd sbe-qa-processing
uv sync
uv run pytest
```

### RHEL, AlmaLinux and Rocky Linux

Releases 8 and 9. Their default Python (3.6 or 3.9) is too old, but both have a `python3.11`
package. curl comes installed: on 9, `curl-minimal` provides it, and asking for `curl` conflicts
with it.

```bash
sudo dnf install -y git-core python3.11
curl -LsSf https://astral.sh/uv/install.sh | sh             # uv in ~/.local/bin
source "$HOME/.local/bin/env"                               # uv on the PATH; or a new shell
git clone https://github.com/OceanDataTools/sbe-qa-processing.git
cd sbe-qa-processing
uv sync
uv run pytest
```

### Windows

The core and R2R commands run on Windows (CI tests them there); the OpenVDM hook runs where
OpenVDM does, on Linux. In PowerShell, with [Git for Windows](https://git-scm.com/download/win):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
git clone https://github.com/OceanDataTools/sbe-qa-processing.git
cd sbe-qa-processing
uv sync
uv run pytest
```

Open a new terminal after installing uv, so it's on the PATH. The commands work the same in
PowerShell, with Windows paths, e.g.
`uv run sbe-qa-processing run D:\cruises\MYCRUISE\CTD -o output\MYCRUISE`.

`uv sync` makes a virtual environment in `.venv` with the exact versions in `uv.lock`, the
dev group included (pytest, ruff, and nbclient and ipykernel, which `--execute-notebook` needs);
`uv sync --no-dev` leaves those out. `uv run` runs a command in it; the command itself is
`.venv/bin/sbe-qa-processing` (`.venv\Scripts\sbe-qa-processing.exe` on Windows), which works
without uv on the PATH. `uv run pytest` runs the whole suite on the three R2R validation filesets
committed in `tests/data/`, so it downloads nothing, and checks this tool's R2R reports against
R2R's own.

## Post-Install:

- [Install for the OpenVDM hook](#install-for-the-openvdm-hook)
- [Check the install](#check-the-install)
- [Map data](#map-data)
- [Proxies and ships without internet](#proxies-and-ships-without-internet)
- [Updating](#updating)
- [About the dependencies](#about-the-dependencies)

### Install for the OpenVDM hook

[OpenVDM](https://github.com/OceanDataTools/openvdm) runs the QA from its
`postCollectionSystemTransfer` hook, after each transfer of the CTD's collection system that
brings new or updated CTD files. Setting it up takes five steps: install, add an extra directory
for the reports, write the ship's site TOML, add the hook to `openvdm.yaml`, and try it. The
README's [OpenVDM section](README.md#openvdm) describes what the hook does, where its values
come from, troubleshooting, and running it on a past cruise.

Each run assesses the whole cruise so far, so it takes longer, and needs more memory, as casts
accumulate: about 1.4 s a 28-minute cast and about 250 MB to start. Measured on synthetic
50-cast cruises, 28-minute casts take 72 s and peak at 534 MB; 4-hour casts take about 4½
minutes and peak at about 1 GB, most of the time spent reading the casts again.

#### 1. Install

Install it where OpenVDM's workers can run it, in its own environment (not OpenVDM's venv), with
the `openvdm` extra (pyyaml, to read `openvdm.yaml`). Do it as OpenVDM's user (`OPENVDM_USER`,
`survey` by default):
1. Install uv for that user.
2. Clone the repository in that user's home.
3. Move the clone to `/opt`. It stays that user's, as OpenVDM's installer leaves
   `/opt/openvdm`.
4. Install the packages in `/opt/sbe-qa-processing`.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"   # uv in this shell
cd ~
git clone https://github.com/OceanDataTools/sbe-qa-processing.git
sudo mv ~/sbe-qa-processing /opt/
cd /opt/sbe-qa-processing
uv sync --no-dev --extra openvdm --no-managed-python   # the system Python 3.11+
```

It's a git checkout, so [Updating](#updating) pulls into it directly. The hook runs the venv's
own Python, `.venv/bin/sbe-qa-processing`, never uv, so OpenVDM's workers run it as root (or any
user) without uv on their `PATH`. `--no-managed-python` makes the venv use the system Python
(Debian 12's and Ubuntu 24.04's `python3`, or `python3.11` on RHEL, AlmaLinux and Rocky 8 and 9
after `sudo dnf install -y python3.11`), which every user can read; a Python uv downloads lives
in the installing user's home.

On **Ubuntu 22.04**, whose Python is too old, run these in place of that `uv sync`. They install
a Python with uv, kept beside the install so it doesn't depend on your home directory:

```bash
export UV_PYTHON_INSTALL_DIR=/opt/sbe-qa-processing/python
uv python install 3.12
uv sync --no-dev --extra openvdm --python 3.12
```

Then fetch the map data **as the user OpenVDM's workers run as**, since cartopy caches it in that
user's home. OpenVDM's installer runs every worker as root (supervisor's `user=root`), so:

```bash
sudo /opt/sbe-qa-processing/.venv/bin/sbe-qa-processing fetch-map-data
# workers run as another user: sudo -u <worker> /opt/sbe-qa-processing/.venv/bin/...
```

Leave out `--no-dev` to use the hook's `--execute-notebook` (it needs nbclient and ipykernel).

#### 2. Add an extra directory

In OpenVDM's web interface, add an extra directory for the reports (Configuration > Extra
Directories): name `CTD_QA`, destination e.g. `Products/CTD_QA`. The hook writes each run's
reports there, replacing the previous run's. Another name goes in the site TOML's
`[openvdm] output_extra_directory`.

#### 3. Write the site TOML

OpenVDM knows the cruise (ID, name, PI, dates, extent) and where each transfer's files go. The
**site TOML** holds, once per ship, what it doesn't: the reports' extra directory, an expected
cruise area for cruises without an extent, the science checks' limits and, before OpenVDM 2.17,
the vessel's name and the report contact.

`site-config` writes it by asking for each field, and works offline:

```bash
cd /opt/sbe-qa-processing
.venv/bin/sbe-qa-processing site-config        # writes configs/site.toml
```

`configs/site.toml` in the install is where the hook reads it, so the hook commands below
needn't name it, and `site-config` writes it there from any directory. (`-o` writes another,
and the hook's `--site-config` reads another.)

It asks for, in order (Enter keeps the `[default]`, `-` clears it):

1. **The vessel and the report contact:** the vessel's name, the contact institution and email.
   From OpenVDM 2.17, `openvdm.yaml`'s `vessel` block supplies the defaults; what you enter here
   wins over it, and a field left blank comes from it. It reads `openvdm.yaml` from
   `/opt/openvdm/server/etc/`; if OpenVDM is installed elsewhere (the installer's
   `INSTALL_ROOT`), pass `--openvdm-config <INSTALL_ROOT>/openvdm/server/etc/openvdm.yaml`.
2. **The extra directory** from step 2.
3. **An expected cruise area**, used only when OpenVDM has no cruise extent (OpenVDM 2.17's
   `cruiseExtent`, which `build_cruise_tracks.py` sets from the ship's track). Leave
   westernmost blank to skip it.

The result, for R/V Robert Gordon Sproul:

```toml
[vessel]
name = "Robert Gordon Sproul"

[openvdm]
output_extra_directory = "CTD_QA"        # where reports go

[provenance]
contact_institution = "Scripps Institution of Oceanography"
contact_email = "ship-tech@example.org"
```

`configs/site.example.toml` shows every key, commented, the thresholds at their defaults; the
README's [Site TOML](README.md#site-toml) table describes each. Run `site-config --force` to
change the file (its current values become the defaults), or edit it: the QA thresholds (e.g.
for fresh water) and the report theme are edited by hand. A misspelled or misplaced key is an
error that says where it belongs. A site TOML from before the hook dropped R2R still works: its
R2R settings are ignored, with a note, and `site-config --force` drops them.

To check the site TOML against OpenVDM's current cruise without running the QA, print the
config the hook would use:

```bash
.venv/bin/sbe-qa-processing config-from-openvdm CTD -o -
```

It shows the cruise's dates, the vessel and the extent, and notes what's missing or assumed
(e.g. an open-ended cruise, which ends today).

#### 4. Add the hook to openvdm.yaml

The QA runs from OpenVDM's `postCollectionSystemTransfer` hook, set in its `openvdm.yaml`
(`/opt/openvdm/server/etc/openvdm.yaml` by default) under `postHookCommands`. `openvdm.yaml`
already has that key, with an example commented out: put this entry under it rather than
writing the key again, since YAML keeps only the last of two keys with one name, silently.

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
            - "--openvdm-config=/opt/openvdm/server/etc/openvdm.yaml"
            - "--changed-files={newFiles}"
            - "--changed-files={updatedFiles}"
```

| Line | What it is |
|---|---|
| `collectionSystemTransferName: CTD` | The CTD's collection system transfer, by its name in OpenVDM (Configuration > Collection System Transfers) |
| `/opt/.../sbe-qa-processing`, `openvdm` | The install from step 1, and the hook command |
| `"{collectionSystemTransferName}"` | OpenVDM fills in the transfer's name: the hook reads that transfer's directory |
| (no `--site-config`) | The hook reads the site TOML from step 3 at `/opt/sbe-qa-processing/configs/site.toml`; give `--site-config=PATH` for one elsewhere |
| `--openvdm-config` | `openvdm.yaml` itself, for OpenVDM's web address (`siteRoot`) and `vessel` block. This path is the default; give yours if OpenVDM is installed elsewhere |
| `--changed-files={newFiles}`, `={updatedFiles}` | The transfer's new and updated files, so the QA runs only when CTD files (`.hex/.dat`, `.XMLCON/.con`, `.hdr`, `.bl`) changed. Keep the `--changed-files=` form: OpenVDM drops a bare `{newFiles}` argument when there are none |

Optional arguments, one list item each: `--execute-notebook` saves the notebook with its outputs
(an install without `--no-dev`); `--theme=/path/theme.toml` changes the reports' look.

OpenVDM reads `openvdm.yaml` when its workers start, so restart the hook worker after editing it:

```bash
sudo supervisorctl restart openvdm:post_hooks
```

#### 5. Try it

The hook runs after the next transfer that brings new or updated CTD files. To run it now, give
the same command by hand, as root (as OpenVDM runs it) and without `--changed-files`, which makes
it assess the cruise whatever changed:

```bash
sudo /opt/sbe-qa-processing/.venv/bin/sbe-qa-processing openvdm CTD \
    --openvdm-config=/opt/openvdm/server/etc/openvdm.yaml
```

It prints the cruise it found, the QA summary and the files it wrote into the extra directory
(in the cruise's directory, e.g. `Products/CTD_QA`). OpenVDM logs its runs in
`/var/log/openvdm/post_hooks.log`, and a failure shows in OpenVDM as the hook's one-line
message. The README's [Troubleshooting](README.md#troubleshooting) table says what each
message means.

### Check the install

```bash
.venv/bin/sbe-qa-processing --version
```

To run the QA on a real fileset, unpack a committed validation fileset (needs `xz`: `xz-utils`
on Debian and Ubuntu, `xz` on RHEL):

```bash
mkdir -p /tmp/check
tar -xJf tests/data/SP2613_169847_ctd.tar.xz -C /tmp/check
.venv/bin/sbe-qa-processing run /tmp/check/SP2613_169847_ctd -o /tmp/sbeqa
```

It prints 5 casts and the checks that warned or failed, and writes the PDF, plots and notebook
into `/tmp/sbeqa`.

### Map data

The cast map's coastlines use Natural Earth's 1:10m land, lakes and coastlines, and its world
inset the 1:110m land, which cartopy downloads on first use. `fetch-map-data` downloads them all
at once (about 27 MB) into cartopy's data directory, `~/.local/share/cartopy`
(`$XDG_DATA_HOME/cartopy` when that's set), and prints where. Without them, the maps have no
coastlines; nothing else changes.

### Proxies and ships without internet

- **A proxy:** uv and `fetch-map-data` use the usual `HTTPS_PROXY`, `HTTP_PROXY` and `NO_PROXY`
  variables. Keep them for `sudo` with `sudo -E` (or `sudo env HTTPS_PROXY=... ...`).
- **Install in port.** The install needs PyPI (packages) and, for `fetch-map-data`, Natural
  Earth's servers; after that the QA needs no network. R2R's commands (`r2r fetch-fileset`,
  `r2r config-from-catalog`) need R2R's servers whenever they run.
- **No connection at all:** install on a connected machine with the same OS release and
  architecture, at the same path, then copy across `/opt/sbe-qa-processing` (with its `.venv`,
  and `python/` on Ubuntu 22.04) and the map data directory (`~/.local/share/cartopy` of the
  user who'll run it). A venv only works at the path it was made in.

### Updating

```bash
cd /opt/sbe-qa-processing
git pull
uv sync --no-dev --extra openvdm --no-managed-python   # the same options as the install
```

`uv sync` installs exactly the versions in `uv.lock`, so every install of a commit runs the same
packages. For the OpenVDM hook's install, update as the OpenVDM user, who owns
`/opt/sbe-qa-processing` and has uv. Installed elsewhere, `git pull && uv sync`, with the same
options as before.

### About the dependencies

seabirdscientific comes from PyPI (3.0.0 or later; the README's
[seabirdscientific version](README.md#seabirdscientific-version) says why). `uv sync` installs the
version pinned in `uv.lock`. `pip install git+https://github.com/OceanDataTools/sbe-qa-processing.git`
also works, and takes the newest seabirdscientific release that satisfies the version range.

Installed: [COOKBOOK.md](COOKBOOK.md) has worked examples, on a directory of Seasave files and on
an R2R fileset.
