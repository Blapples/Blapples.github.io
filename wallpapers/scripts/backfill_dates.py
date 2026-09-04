#!/usr/bin/env python3
"""
backfill_dates.py - regenerate BASELINE_CONFIRMED_DATES from real git history.

This reads a LOCAL CLONE of the wallpapers repo directly (git log), so it
costs zero GitHub API requests and never hits the unauthenticated 60/hour
rate limit, no matter how large the collection gets. It's what keeps the
gallery's sort order (newest upload first) correct and identical on every
device, including a first-time visitor who never had a chance to "confirm"
a wallpaper's date live before it aged out of the 30-day window the site's
own git-history check uses (see DATE_CONFIRM_WINDOW_MS in index.html).

USAGE
  Print the generated object (for a manual paste, same as before):
    python3 scripts/backfill_dates.py --repo /path/to/wallpapers-clone

  Write it straight into index.html between its marker comments (used by
  the scheduled GitHub Action - see .github/workflows/backfill-wallpaper-dates.yml):
    python3 scripts/backfill_dates.py --repo /path/to/wallpapers-clone --apply /path/to/index.html

REQUIREMENTS
  - The wallpapers repo must be cloned with full history, e.g.:
      git clone --no-single-branch https://github.com/Blapples/wallpapers wallpapers-clone
    (a shallow clone only has the most recent commit for each file, which
    would make every "first added" date wrong.)
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

WALLPAPER_IMAGE_EXT = re.compile(r"\.(png|jpe?g|webp|gif)$", re.IGNORECASE)
PRESETS_SUBFOLDER = "presets"

START_MARKER = "// AUTO-GENERATED:BASELINE_CONFIRMED_DATES:START"
END_MARKER = "// AUTO-GENERATED:BASELINE_CONFIRMED_DATES:END"


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def list_root_wallpaper_files(repo: Path) -> list[str]:
    """Root-level image files only, same scope the site's own git-history
    check uses (presets/ lives in the same repo but is a different
    feature entirely and must never be counted as a wallpaper)."""
    raw = run_git(repo, "ls-tree", "--name-only", "-z", "HEAD")
    names = [n for n in raw.split("\0") if n]
    files = []
    for name in names:
        if name == PRESETS_SUBFOLDER:
            continue
        if "/" in name:
            continue  # any other nested path - not a root-level file
        if not WALLPAPER_IMAGE_EXT.search(name):
            continue
        files.append(name)
    return files


def first_added_epoch_ms(repo: Path, filename: str) -> int | None:
    """Earliest commit that ADDED this filename, following renames so a
    file's true original upload date survives a later rename. git log
    prints newest-first, so the earliest add is the LAST 'A' entry."""
    out = run_git(
        repo,
        "log",
        "--diff-filter=A",
        "--follow",
        "--format=%aI",
        "--",
        filename,
    )
    dates = [line for line in out.splitlines() if line.strip()]
    if not dates:
        return None
    iso = dates[-1]
    from datetime import datetime

    dt = datetime.fromisoformat(iso)
    return int(dt.timestamp() * 1000)


def build_baseline(repo: Path) -> dict[str, int]:
    baseline: dict[str, int] = {}
    for filename in list_root_wallpaper_files(repo):
        ts = first_added_epoch_ms(repo, filename)
        if ts is None:
            print(f"warning: no 'added' commit found for {filename!r}, skipping", file=sys.stderr)
            continue
        baseline[filename] = ts
    return dict(sorted(baseline.items(), key=lambda kv: kv[0].lower()))


def render_object_literal(baseline: dict[str, int]) -> str:
    lines = [f"const BASELINE_CONFIRMED_DATES = {{"]
    for filename, ts in baseline.items():
        key = json.dumps(filename, ensure_ascii=True)
        lines.append(f"  {key}: {ts},")
    lines.append("};")
    return "\n".join(lines) + "\n"


def apply_to_file(html_path: Path, object_literal: str) -> bool:
    text = html_path.read_text(encoding="utf-8")
    if START_MARKER not in text or END_MARKER not in text:
        raise SystemExit(
            f"Could not find {START_MARKER!r} / {END_MARKER!r} markers in {html_path}. "
            "Have they been removed or renamed?"
        )
    start_idx = text.index(START_MARKER) + len(START_MARKER)
    end_idx = text.index(END_MARKER)
    if start_idx >= end_idx:
        raise SystemExit("Markers are out of order or malformed - refusing to touch the file.")

    new_middle = "\n" + object_literal + "\n"
    new_text = text[:start_idx] + new_middle + text[end_idx:]

    if new_text == text:
        return False
    html_path.write_text(new_text, encoding="utf-8")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True, type=Path, help="Path to a full (non-shallow) local clone of the wallpapers repo")
    parser.add_argument("--apply", type=Path, default=None, help="If given, write the result straight into this index.html between its marker comments instead of printing it")
    args = parser.parse_args()

    if not (args.repo / ".git").exists():
        raise SystemExit(f"{args.repo} doesn't look like a git repo (no .git found)")

    baseline = build_baseline(args.repo)
    object_literal = render_object_literal(baseline)

    if args.apply:
        changed = apply_to_file(args.apply, object_literal)
        print(f"{'Updated' if changed else 'No changes needed for'} {args.apply} ({len(baseline)} filenames)")
    else:
        print(object_literal, end="")


if __name__ == "__main__":
    main()
