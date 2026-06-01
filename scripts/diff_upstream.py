#!/usr/bin/env python3
"""Show file-level changes in upstream leanSpec since the pinned SHA.

Usage:
    scripts/diff_upstream.py [--stat]

Environment:
    LEANSPEC_REPO    Path to upstream leanSpec checkout (default ~/Documents/leanSpec)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = Path(os.environ.get("LEANSPEC_REPO", Path.home() / "Documents" / "leanSpec"))
PIN = (ROOT / ".upstream-sha").read_text().strip()


def main() -> int:
    if not UPSTREAM.exists():
        print(f"Upstream not found at {UPSTREAM}", file=sys.stderr)
        return 1

    args = sys.argv[1:]
    stat = "--stat" in args

    cmd = ["git", "-C", str(UPSTREAM), "log", "--oneline", f"{PIN}..HEAD"]
    if stat:
        cmd.append("--stat")
    print(f"# Commits in {UPSTREAM} since {PIN[:7]}")
    print()
    subprocess.run(cmd, check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
