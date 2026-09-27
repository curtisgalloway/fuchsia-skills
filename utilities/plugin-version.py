#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""Bump and check the calendar versions of this marketplace's plugins.

Claude Code reinstalls a plugin only when the `version` in its plugin.json
changes, so a merged change that leaves the version alone never reaches
anyone who installed the plugin. Every change to a plugin's files therefore
bumps its version.

Versions are YYYY.MDD.N: the year, the month and two-digit day run together
(September 27 is 927, October 1 is 1001), and a counter for further releases
on the same day. 2026.927.0, then 2026.927.1. That is valid semver and sorts
correctly in every tool that orders versions numerically.

A plugin's files are what its marketplace entry points at: the `skills`
paths it lists, or its whole `source` directory. A plugin that bundles other
plugins' skills is bumped whenever any of them change.

  plugin-version.py bump [--base REF] [--date YYYY-MM-DD] [PLUGIN ...]
      Bump the named plugins, or every plugin whose files changed since REF
      (default origin/main) and whose version has not been bumped yet.
  plugin-version.py check [--base REF]
      Fail if a plugin changed since REF without a new, well-formed, higher
      version, or if a marketplace entry declares a version of its own.

Exit codes follow the dev-tools/cli-conventions contract:

  0  ok
  1  ran fine; found plugins that need a bump
  2  usage error
  3  missing precondition (not a git checkout, no marketplace.json)
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

VERSION_RE = re.compile(r"^(\d{4})\.([1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\.(0|[1-9]\d*)$")
FIELD_RE = re.compile(r'("version"\s*:\s*")([^"]*)(")')


class Precondition(Exception):
    """Something the tool needs is missing."""


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise Precondition(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result


def parse(version: str) -> tuple[int, int, int] | None:
    """(year, MDD, N) for a calendar version, None for anything else."""
    m = VERSION_RE.match(version or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2) + m.group(3)), int(m.group(4))


def next_version(old: str | None, today: datetime.date) -> str:
    """Today's first version, or the next counter when that is not higher."""
    new = (today.year, today.month * 100 + today.day, 0)
    prev = parse(old or "")
    if prev and new <= prev:
        new = (prev[0], prev[1], prev[2] + 1)
    return f"{new[0]}.{new[1]}.{new[2]}"


def plugins(root: Path) -> dict[str, dict]:
    """name -> {manifest, paths} for every plugin that lives in this checkout."""
    mkt_path = root / ".claude-plugin" / "marketplace.json"
    if not mkt_path.is_file():
        raise Precondition(f"no marketplace at {mkt_path}")
    out = {}
    for entry in json.loads(mkt_path.read_text()).get("plugins") or []:
        src = entry.get("source")
        if not isinstance(src, str) or not src.startswith("./"):
            continue  # hosted in another repository
        base = (root / src).resolve()
        listed = entry.get("skills") or []
        paths = [(base / s).resolve() for s in listed] or [base]
        out[entry["name"]] = {
            "manifest": base / ".claude-plugin" / "plugin.json",
            "paths": [p.relative_to(root.resolve()).as_posix() for p in paths],
            "entry_version": entry.get("version"),
        }
    return out


def covers(paths: list[str], changed: str) -> bool:
    return any(p == "." or changed == p or changed.startswith(p + "/") for p in paths)


def read_version(manifest: Path) -> str | None:
    m = FIELD_RE.search(manifest.read_text())
    return m.group(2) if m else None


def base_version(root: Path, merge_base: str, manifest: Path) -> str | None:
    rel = manifest.relative_to(root.resolve()).as_posix()
    shown = git(root, "show", f"{merge_base}:{rel}", check=False)
    if shown.returncode != 0:
        return None  # a plugin new since the base
    m = FIELD_RE.search(shown.stdout)
    return m.group(2) if m else None


def changed_since(root: Path, base: str) -> tuple[str, list[str]]:
    merge_base = git(root, "merge-base", base, "HEAD").stdout.strip()
    committed = git(root, "diff", "--name-only", merge_base).stdout.split()
    untracked = git(root, "ls-files", "--others", "--exclude-standard").stdout.split()
    return merge_base, sorted(set(committed) | set(untracked))


def cmd_check(root: Path, base: str) -> int:
    merge_base, changed = changed_since(root, base)
    problems = []
    for name, p in sorted(plugins(root).items()):
        if p["entry_version"] is not None:
            problems.append(f"{name}: the marketplace entry declares version "
                            f"{p['entry_version']!r}; keep it only in plugin.json")
        if not any(covers(p["paths"], c) for c in changed):
            continue
        new = read_version(p["manifest"])
        old = base_version(root, merge_base, p["manifest"])
        if new == old:
            problems.append(f"{name}: files changed but the version is still "
                            f"{old}; run utilities/plugin-version.py bump")
        elif new is None or parse(new) is None:
            problems.append(f"{name}: version {new!r} is not YYYY.MDD.N")
        elif parse(old or "") and parse(new) <= parse(old):
            problems.append(f"{name}: version {new} is not higher than {old}")
    for line in problems:
        print(line)
    if not problems:
        print("plugin versions ok")
    return 1 if problems else 0


def cmd_bump(root: Path, base: str, today: datetime.date, names: list[str]) -> int:
    known = plugins(root)
    unknown = [n for n in names if n not in known]
    if unknown:
        print(f"unknown plugin(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    if not names:
        merge_base, changed = changed_since(root, base)
        names = [n for n, p in sorted(known.items())
                 if any(covers(p["paths"], c) for c in changed)
                 and read_version(p["manifest"])
                 == base_version(root, merge_base, p["manifest"])]
    for name in names:
        manifest = known[name]["manifest"]
        text = manifest.read_text()
        old = read_version(manifest)
        if old is None:
            print(f"{name}: {manifest} has no version field", file=sys.stderr)
            return 3
        new = next_version(old, today)
        manifest.write_text(FIELD_RE.sub(lambda m: m.group(1) + new + m.group(3),
                                         text, count=1))
        print(f"{name}: {old} -> {new}")
    if not names:
        print("nothing to bump")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bump")
    b.add_argument("--base", default="origin/main")
    b.add_argument("--date", type=datetime.date.fromisoformat,
                   default=datetime.date.today())
    b.add_argument("plugins", nargs="*")
    c = sub.add_parser("check")
    c.add_argument("--base", default="origin/main")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "bump":
            return cmd_bump(args.root, args.base, args.date, args.plugins)
        return cmd_check(args.root, args.base)
    except Precondition as e:
        print(e, file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
