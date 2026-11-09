"""Guards for the test harness itself.

The suite must never write into the developer's real data folders or into the
real Prefect metadata database. The app paths are covered by the session
config redirect; Prefect needs an explicit ``PREFECT_HOME``.
"""

from __future__ import annotations

import os
from pathlib import Path


def test_prefect_home_is_inside_the_session_temp_dir():
    home = os.environ.get("PREFECT_HOME")
    assert home, "PREFECT_HOME must be set by tests/conftest.py"
    path = Path(home)
    assert path.name == "prefect"
    assert path.parent.name.startswith("aam-merger-tests-"), path
