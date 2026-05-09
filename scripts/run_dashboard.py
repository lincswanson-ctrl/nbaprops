"""Launch the Streamlit dashboard.

Used by NBA Props Launcher and anyone who prefers a script entrypoint.
Equivalent to: ``streamlit run app.py`` from the repository root.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def project_root() -> Path:
    """Directory that contains ``app.py`` (repo root)."""
    here = Path(__file__).resolve().parent
    return here.parent


def main() -> None:
    root = project_root()
    app = root / "app.py"
    if not app.exists():
        print(f"Invalid project root: missing {app}", file=sys.stderr)
        sys.exit(1)
    os.chdir(root)
    cmd = [sys.executable, "-m", "streamlit", "run", str(app)]
    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
