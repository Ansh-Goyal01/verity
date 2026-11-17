"""W1.4: the held-out split was opened exactly once; a second ``--final`` run must refuse before reading any trip."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FINAL_FILE = ROOT / "results" / "bmw_i3_heldout.json"


def test_a_second_final_run_is_refused_before_any_held_out_trip_is_read() -> None:
    if not FINAL_FILE.exists():
        pytest.skip("the held-out split has not been opened yet; running --final here would open it")
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "bmw_heldout.py"), "--final"], capture_output=True,
                          text=True, timeout=120)
    assert done.returncode != 0
    assert "REFUSED" in (done.stdout + done.stderr)
    assert "already opened" in (done.stdout + done.stderr)
