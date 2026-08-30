from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def test_astrbot_plugin_manager_loads_plugin_in_clean_process() -> None:
    plugin_parent = PLUGIN_ROOT.parent
    astrbot_root = PLUGIN_ROOT.parents[2]
    pythonpath = os.pathsep.join((str(plugin_parent), str(astrbot_root)))
    result = subprocess.run(
        [sys.executable, "-m", "tests.astrbot_loader_smoke"],
        cwd=PLUGIN_ROOT,
        env={**os.environ, "PYTHONPATH": pythonpath},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
