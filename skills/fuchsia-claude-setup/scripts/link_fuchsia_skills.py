#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""
link_fuchsia_skills.py - bridge a Fuchsia checkout's in-tree agent config
into Claude Code's conventions.

Upstream fuchsia.git ships its agent guidance in Gemini-oriented locations:
project instructions in GEMINI.md files and ~80 skills spread across the
tree (//.agents/skills/ plus per-team dirs), discovered via skills.json /
`fx manage-skills` - none of which Claude Code reads. This script builds the
local, git-excluded bridge:

  1. .claude/skills/<name> symlinks for every tracked SKILL.md, named by
     each skill's frontmatter `name:` (falling back to a path-derived slug)
  2. AGENTS.md -> GEMINI.md symlinks at the root and beside every nested
     tracked GEMINI.md
  3. .git/info/exclude entries so none of the above ever dirties the tree
     (never .gitignore - that file is tracked upstream)

It is a *regenerator*, not a one-time installer: upstream adds, renames and
moves skills continuously, so a hand-made symlink farm rots (observed: four
dangling links and ~15 missing skills after two months of `jiri update`).
Re-run this after every `jiri update`. It owns the symlinks in the target
dir - stale and dangling ones are pruned; real files and directories are
never touched.

Filters given via --only/--exclude are persisted to
.claude/skills-link.json and reapplied on later plain runs, so the
"re-run after jiri update" habit keeps your selection.

Usage:
  link_fuchsia_skills.py [--root <fuchsia-dir>] [--dry-run]
                         [--only PREFIX ...] [--exclude PREFIX ...]
                         [--reset-filters] [--no-agents-md]

Stdlib only; no dependency on fx, jiri, or any harness.
"""

import argparse
import json
import os
import re
import subprocess
import sys

# Vendored subtrees whose SKILL.md / GEMINI.md files belong to *other*
# projects, not Fuchsia.
DEFAULT_PRUNE_PREFIXES = (
    "third_party/bazel_vendor/",
    "third_party/rust_crates/vendor/",
    "out/",
    "prebuilt/",
)

CONFIG_RELPATH = os.path.join(".claude", "skills-link.json")
EXCLUDE_MARKER = "# managed by link_fuchsia_skills.py (fuchsia-claude-setup)"


def find_root(explicit):
    if explicit:
        root = os.path.abspath(explicit)
        if not os.path.isdir(os.path.join(root, ".jiri_root")) and not os.path.isfile(
            os.path.join(root, "GEMINI.md")
        ):
            sys.exit(f"error: {root} does not look like a Fuchsia checkout")
        return root
    d = os.getcwd()
    while True:
        if os.path.isdir(os.path.join(d, ".jiri_root")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            sys.exit(
                "error: no .jiri_root found above the current directory; "
                "pass --root <fuchsia-dir>"
            )
        d = parent


def git(root, *args):
    out = subprocess.run(
        ["git", "-C", root, *args], capture_output=True, text=True, check=True
    )
    return out.stdout


def tracked_files(root):
    return git(root, "ls-files", "-z").split("\0")


def frontmatter_name(skill_md):
    try:
        with open(skill_md, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:60]:
        if line.strip() == "---":
            break
        m = re.match(r"^name:\s*['\"]?([^'\"\s][^'\"]*?)['\"]?\s*$", line)
        if m:
            return m.group(1)
    return None


def sanitize(name):
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


def path_slug(rel_dir):
    parts = rel_dir.split("/")
    # Drop everything up to and including the last "skills" marker directory
    # so src/devices/skills/driver_fidl/client -> driver_fidl-client.
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] in ("skills", ".agents", ".agent"):
            parts = parts[i + 1 :]
            break
    return sanitize("-".join(parts)) or sanitize(os.path.basename(rel_dir))


def load_filters(root, args):
    cfg_path = os.path.join(root, CONFIG_RELPATH)
    if args.reset_filters:
        if os.path.exists(cfg_path) and not args.dry_run:
            os.remove(cfg_path)
        return [], []
    if args.only or args.exclude:
        if not args.dry_run:
            os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump({"only": args.only, "exclude": args.exclude}, f, indent=2)
        return args.only, args.exclude
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
            return cfg.get("only", []), cfg.get("exclude", [])
        except (OSError, ValueError):
            print(f"warning: could not read {cfg_path}; ignoring saved filters")
    return [], []


def under(rel_dir, prefix):
    prefix = prefix.strip("/")
    return rel_dir == prefix or rel_dir.startswith(prefix + "/")


def want(rel_dir, only, exclude):
    if any(under(rel_dir, p) for p in exclude):
        return False
    if only:
        return any(under(rel_dir, p) for p in only)
    return True


def collect_skills(root, files, only, exclude):
    """Return {link_name: rel_dir} for every skill that passes the filters."""
    rel_dirs = sorted(
        os.path.dirname(p)
        for p in files
        if p.endswith("/SKILL.md")
        and not p.startswith(DEFAULT_PRUNE_PREFIXES)
        and want(os.path.dirname(p), only, exclude)
    )
    desired = {}
    collisions = []
    for rel_dir in rel_dirs:
        name = frontmatter_name(os.path.join(root, rel_dir, "SKILL.md"))
        slug = sanitize(name) if name else path_slug(rel_dir)
        if slug in desired:
            qualified = sanitize(rel_dir.split("/")[0]) + "-" + slug
            n = 2
            base = qualified
            while qualified in desired:
                qualified = f"{base}-{n}"
                n += 1
            collisions.append((slug, desired[slug], qualified, rel_dir))
            slug = qualified
        desired[slug] = rel_dir
    return desired, collisions


def sync_symlink(link_path, target, dry_run):
    """Ensure link_path is a symlink to target. Returns 'new'|'fixed'|'kept'|'skipped'."""
    if os.path.islink(link_path):
        if os.readlink(link_path) == target:
            return "kept"
        if not dry_run:
            os.remove(link_path)
            os.symlink(target, link_path)
        return "fixed"
    if os.path.lexists(link_path):
        return "skipped"
    if not dry_run:
        os.symlink(target, link_path)
    return "new"


def sync_skills(root, desired, dry_run):
    target_dir = os.path.join(root, ".claude", "skills")
    if not dry_run:
        os.makedirs(target_dir, exist_ok=True)
    counts = {"new": 0, "fixed": 0, "kept": 0, "skipped": 0, "removed": 0}
    for slug, rel_dir in desired.items():
        link = os.path.join(target_dir, slug)
        target = os.path.relpath(os.path.join(root, rel_dir), target_dir)
        action = sync_symlink(link, target, dry_run)
        counts[action] += 1
        if action in ("new", "fixed"):
            print(f"  {action:5}  {slug} -> {rel_dir}")
        elif action == "skipped":
            print(f"  skip   {slug}: exists and is not a symlink; leaving it alone")
    # Prune: dangling links, and links we own (resolving inside the checkout)
    # that no longer correspond to a desired skill.
    if os.path.isdir(target_dir):
        for entry in sorted(os.listdir(target_dir)):
            link = os.path.join(target_dir, entry)
            if not os.path.islink(link) or entry in desired:
                continue
            resolved = os.path.realpath(link)
            dangling = not os.path.exists(link)
            ours = resolved.startswith(os.path.realpath(root) + os.sep)
            if dangling or ours:
                why = "dangling" if dangling else "stale"
                print(f"  prune  {entry} ({why})")
                if not dry_run:
                    os.remove(link)
                counts["removed"] += 1
    return counts


def sync_agents_md(root, files, dry_run):
    """Symlink AGENTS.md -> GEMINI.md beside every tracked GEMINI.md."""
    made = []
    for p in files:
        if os.path.basename(p) != "GEMINI.md" or p.startswith(DEFAULT_PRUNE_PREFIXES):
            continue
        rel_dir = os.path.dirname(p)
        link = os.path.join(root, rel_dir, "AGENTS.md")
        action = sync_symlink(link, "GEMINI.md", dry_run)
        rel_link = os.path.join(rel_dir, "AGENTS.md") if rel_dir else "AGENTS.md"
        if action in ("new", "fixed"):
            print(f"  {action:5}  {rel_link} -> GEMINI.md")
        elif action == "skipped":
            print(f"  skip   {rel_link}: a real file already exists; not bridging")
        if action != "skipped":
            made.append("/" + rel_link)
    return made


def sync_git_exclude(root, agents_links, dry_run):
    exclude_path = os.path.join(
        root, git(root, "rev-parse", "--git-path", "info/exclude").strip()
    )
    wanted = ["/.claude/"] + agents_links
    try:
        with open(exclude_path, encoding="utf-8") as f:
            existing = f.read().splitlines()
    except OSError:
        existing = []
    missing = [w for w in wanted if w not in existing]
    if not missing:
        return []
    if not dry_run:
        os.makedirs(os.path.dirname(exclude_path), exist_ok=True)
        with open(exclude_path, "a", encoding="utf-8") as f:
            if EXCLUDE_MARKER not in existing:
                f.write("\n" + EXCLUDE_MARKER + "\n")
            for line in missing:
                f.write(line + "\n")
    for line in missing:
        print(f"  exclude  {line}")
    return missing


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--root", help="Fuchsia checkout root (default: found via .jiri_root)")
    ap.add_argument("--dry-run", action="store_true", help="print actions, change nothing")
    ap.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="PREFIX",
        help="link only skills under this repo-relative path prefix (repeatable; persisted)",
    )
    ap.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PREFIX",
        help="skip skills under this repo-relative path prefix (repeatable; persisted)",
    )
    ap.add_argument(
        "--reset-filters", action="store_true", help="forget persisted --only/--exclude"
    )
    ap.add_argument(
        "--no-agents-md", action="store_true", help="skip the AGENTS.md -> GEMINI.md bridge"
    )
    args = ap.parse_args()

    root = find_root(args.root)
    print(f"checkout: {root}" + ("  (dry run)" if args.dry_run else ""))
    files = tracked_files(root)
    only, exclude = load_filters(root, args)
    if only or exclude:
        print(f"filters: only={only or '-'} exclude={exclude or '-'}")

    desired, collisions = collect_skills(root, files, only, exclude)
    print(f"\nskills ({len(desired)} tracked SKILL.md dirs -> .claude/skills/):")
    counts = sync_skills(root, desired, args.dry_run)
    for slug, first, renamed, rel_dir in collisions:
        print(f"  note   name collision on '{slug}' ({first}); linked {rel_dir} as '{renamed}'")

    agents_links = []
    if not args.no_agents_md:
        print("\nAGENTS.md bridge:")
        agents_links = sync_agents_md(root, files, args.dry_run)

    print("\ngit exclude (.git/info/exclude):")
    added = sync_git_exclude(root, agents_links, args.dry_run)
    if not added:
        print("  up to date")

    print(
        f"\nsummary: {counts['new']} new, {counts['fixed']} fixed, "
        f"{counts['kept']} kept, {counts['removed']} pruned, "
        f"{counts['skipped']} skipped"
    )
    print("re-run this script after every `jiri update`.")


if __name__ == "__main__":
    main()
