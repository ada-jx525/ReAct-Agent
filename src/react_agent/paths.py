"""Resolve external project assets without writing into installed packages."""

import os
from pathlib import Path

_checkout = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(
    os.environ.get(
        "REACT_AGENT_PROJECT_ROOT",
        str(_checkout if (_checkout / "pyproject.toml").is_file() else Path.cwd()),
    )
).resolve()
