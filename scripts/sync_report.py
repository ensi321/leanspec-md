#!/usr/bin/env python3
"""Map upstream commits since the pinned SHA to chapters that need review.

Reads each `specs/**/*.md` frontmatter, extracts `source_files` + `last_synced_commit`,
runs `git log <pin>..HEAD -- <files>` against the upstream leanSpec repo, and prints
a per-chapter report of new commits touching its tracked source files.

Usage:
    scripts/sync_report.py [--verbose]

Environment:
    LEANSPEC_REPO    Path to upstream leanSpec checkout (default ~/Documents/leanSpec)
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = Path(os.environ.get("LEANSPEC_REPO", Path.home() / "Documents" / "leanSpec"))
GLOBAL_PIN = (ROOT / ".upstream-sha").read_text().strip()

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
COMMENT_FRONTMATTER_RE = re.compile(r"^(?:# *---\n)((?:#.*\n)+?)(?:# *---)", re.MULTILINE)
FIELD_RE = re.compile(r"^(\w+):\s*(.*)$", re.MULTILINE)


def parse_frontmatter(path: Path) -> dict[str, object]:
    """Parse a minimal subset of YAML frontmatter: scalars and simple lists.

    Supports two forms:
    - Plain YAML frontmatter (---\\n...\\n---) used in .md files.
    - Comment-fence frontmatter (# ---\\n# key: ...\\n# ---) used in .yaml files
      where the frontmatter must coexist with the file's actual YAML payload.

    No PyYAML dependency — keeps the script standalone.
    """
    text = path.read_text()
    m = FRONTMATTER_RE.match(text)
    if m:
        block = m.group(1)
    else:
        m2 = COMMENT_FRONTMATTER_RE.search(text[:2000])
        if not m2:
            return {}
        block = "\n".join(
            line.lstrip("#").lstrip() for line in m2.group(1).splitlines() if line.strip() != "#"
        )
        return _parse_yaml_subset(block)
    return _parse_yaml_subset(block)


def _parse_yaml_subset(block: str) -> dict[str, object]:
    out: dict[str, object] = {}
    lines = block.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val:
            # Inline value (scalar or [a, b, c])
            if val.startswith("[") and val.endswith("]"):
                items = [x.strip() for x in val[1:-1].split(",") if x.strip()]
                out[key] = items
            else:
                out[key] = val
            i += 1
            continue
        # Block list: subsequent indented "- item" lines
        items = []
        i += 1
        while i < len(lines):
            next_line = lines[i]
            if next_line.startswith("  - "):
                items.append(next_line[4:].strip())
                i += 1
            elif next_line.startswith("- "):
                items.append(next_line[2:].strip())
                i += 1
            else:
                break
        out[key] = items
    return out


def upstream_commits(pin: str, files: list[str]) -> list[str]:
    """Return one-line commit summaries between pin..HEAD touching given files."""
    if not files:
        return []
    cmd = [
        "git",
        "-C",
        str(UPSTREAM),
        "log",
        "--oneline",
        f"{pin}..HEAD",
        "--",
        *files,
    ]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        print(f"git log failed: {exc.stderr}", file=sys.stderr)
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    if not UPSTREAM.exists():
        print(f"Upstream not found at {UPSTREAM}", file=sys.stderr)
        print("Set LEANSPEC_REPO=/path/to/leanSpec", file=sys.stderr)
        return 1

    print(f"Global pin: {GLOBAL_PIN}")
    print(f"Upstream:   {UPSTREAM}")
    print()

    chapters: list[Path] = []
    for root in ("specs", "beacon-api"):
        base = ROOT / root
        if not base.exists():
            continue
        for ext in ("*.md", "*.yaml", "*.yml"):
            chapters.extend(base.rglob(ext))
    chapters.sort()

    if not chapters:
        print("No chapters found under specs/ or beacon-api/")
        return 0

    dirty = 0
    clean = 0
    for chapter in chapters:
        fm = parse_frontmatter(chapter)
        pin = str(fm.get("last_synced_commit") or GLOBAL_PIN).strip()
        files = fm.get("source_files") or []
        if not isinstance(files, list):
            files = [files]
        if not files:
            if verbose:
                rel = chapter.relative_to(ROOT)
                print(f"  {rel}: no source_files declared (skipped)")
            continue

        commits = upstream_commits(pin, [str(f) for f in files])
        rel = chapter.relative_to(ROOT)
        if commits:
            dirty += 1
            print(f"DIRTY  {rel}  ({len(commits)} commits since {pin[:7]})")
            for c in commits[:10]:
                print(f"         {c}")
            if len(commits) > 10:
                print(f"         ... {len(commits) - 10} more")
        else:
            clean += 1
            if verbose:
                print(f"clean  {rel}")

    print()
    print(f"Summary: {dirty} chapter(s) need review, {clean} clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
