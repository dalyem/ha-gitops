"""Test setup: make the add-on's ``engine`` package importable and isolate /data."""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

# ha_gitops/ holds the `engine` package (it's the Docker build context).
ADDON_DIR = Path(__file__).resolve().parent.parent / "ha_gitops"
sys.path.insert(0, str(ADDON_DIR))

# Keep any accidental writes out of a real /data, and clean up afterwards.
_TMP = tempfile.mkdtemp(prefix="ha-gitops-test-")
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)
os.environ.setdefault("HA_GITOPS_DATA_DIR", _TMP)
os.environ.setdefault("HA_GITOPS_HA_CONFIG", str(Path(_TMP) / "homeassistant"))
