"""Downloads the Natural Earth map layers the reports use, for working offline (e.g. at sea).

Usage: uv run python scripts/fetch_map_data.py

Layers are cached in cartopy's data directory (cartopy.config["data_dir"]).
"""

import cartopy

from sbe_qa_processing.maps import prefetch

for layer in prefetch():
    print(f"cached {layer}")
print(f"in {cartopy.config['data_dir']}")
