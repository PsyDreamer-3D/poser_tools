# SPDX-License-Identifier: GPL-3.0-or-later

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "poser_tools"))

# External test assets (real Poser CR2/FBX/PMD files) — not committed to the
# repo, large binaries, machine-specific. Tests that need them skip cleanly
# when the directory or a specific file isn't present, so this suite still
# runs (minus fixture-backed cases) on a machine without the asset folder.
TEST_ASSETS_DIR = Path("/home/jess-green/Documents/Blender_Add-on_Dev/Test_Poser_Assets")


@pytest.fixture
def asset_path():
    """Return a function that resolves a Test_Poser_Assets/<name> path, or
    skips the test if the assets directory or the named file isn't there."""
    if not TEST_ASSETS_DIR.is_dir():
        pytest.skip(f"Test_Poser_Assets not found at {TEST_ASSETS_DIR}")

    def _resolve(name: str) -> Path:
        path = TEST_ASSETS_DIR / name
        if not path.is_file():
            pytest.skip(f"{name!r} not found in {TEST_ASSETS_DIR}")
        return path

    return _resolve
