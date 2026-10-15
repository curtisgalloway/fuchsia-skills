---
name: fuchsia-claude-setup
description: >-
  Make a Fuchsia checkout's in-tree skills (~95 SKILL.md files scattered across the tree, discovered
  upstream only through Gemini's skills.json) visible to Claude Code: a generated .claude/skills
  symlink farm plus a .git/info/exclude entry — all local, nothing committed. Use when setting up
  Claude Code on a new Fuchsia checkout or worktree, when in-tree skills are missing from a
  session, after `jiri update` (link rot), or when asked how Fuchsia's in-tree agent config maps to
  Claude. Ships scripts/link_fuchsia_skills.py, the re-runnable regenerator.
---

<!--
SPDX-FileCopyrightText: 2026 Curtis Galloway
SPDX-License-Identifier: Apache-2.0
-->

# fuchsia-claude-setup — use Fuchsia's in-tree skills from Claude Code

Upstream fuchsia.git ships real agent guidance. Its project instructions are in `AGENTS.md`, which
Claude Code reads natively. Its skills are not: they are spread across the tree and discovered
through a Gemini-specific `skills.json`. This skill installs the local bridge for the skills and
keeps it fresh.

> Verified against fuchsia 5ded3fc08ae, 2026-10-15: 95 tracked SKILL.md files
> (`git ls-files '*SKILL.md' | wc -l`, none under the vendored subtrees the script prunes).
> Upstream adds and moves skills continuously — the counts and paths below will drift; the script
> does not care.

## What upstream provides vs. what Claude Code reads

| Upstream (fuchsia.git) | Claude Code | Bridge |
|---|---|---|
| `AGENTS.md` at the root, plus nested ones (wlan, ffx, forensics, debug) | read natively | none needed — renamed from `GEMINI.md` upstream in a7ed50bab94 (2026-10-01) |
| ~95 `SKILL.md` skills: 14 global in `//.agents/skills/` (plus one tracked symlink, `fuchsia-debugger`), the rest under `skills/`, `.agents/skills/`, or `.agent/skills/` dirs across the tree (`src/devices/skills/`, `zircon/skills/`, `build/beads/.agent/skills/`, …) | `.claude/skills/<name>/SKILL.md` | symlink farm generated from `git ls-files` |
| Discovery via `skills.json` (`~/.gemini/config/skills.json`, `.agents/skills.json`) and `fx manage-skills` — see `//.agents/README.md` | directory presence is the discovery | the regenerator script IS the discovery step |
| `.geminiignore` (un-ignores `/local/`, `/third_party/*`, `/vendor/` for search) | none — Grep respects gitignore | guidance below, not a file |

There is no upstream `.claude/`, and you cannot commit one — so the farm is untracked and listed in
`.git/info/exclude` (never `.gitignore`, which is a tracked upstream file).

## Procedure

```bash
python3 <skill-dir>/scripts/link_fuchsia_skills.py --root <fuchsia-dir>
```

Run it from anywhere inside the checkout and `--root` can be omitted (it walks up to `.jiri_root`).
Preview with `--dry-run`. Then start a fresh Claude session in the checkout and confirm the in-tree
skills appear (e.g. `debugging-driver-binding`, `manage-emulator`).

**Re-run after every `jiri update`.** This is the whole reason it is a script: upstream renames and
moves skill directories, and a static symlink farm rots silently — an audit of one real checkout
found 4 dangling links and ~15 missing skills after two months. The script prunes dangling/stale
links, fixes renames, and reports what changed. It only ever touches symlinks; real files and
directories in `.claude/skills/` are left alone.

### Keeping the skill list lean

~95 skill descriptions load into every session. To carry only what you work on:

```bash
link_fuchsia_skills.py --only .agents/skills --only src/devices/skills
```

`--only`/`--exclude` take repo-relative path prefixes, are repeatable, and are **persisted** to
`.claude/skills-link.json` — later plain runs reapply them, so the post-`jiri update` habit keeps
your selection. Links outside the selection are pruned (the farm mirrors the filter).
`--reset-filters` returns to everything.

## What the script does (for auditing)

1. `git ls-files` in fuchsia.git → every tracked `SKILL.md` (vendored subtrees like
   `third_party/bazel_vendor/` excluded).
2. Symlinks each skill dir into `.claude/skills/`, named by the skill's frontmatter `name:`
   (upstream dir names differ — dir `debug_driver_binding` is skill `debugging-driver-binding`),
   falling back to a path-derived slug; name collisions get a path qualifier and a report line.
3. Appends `/.claude/` to `.git/info/exclude` (idempotent).

## Caveats and judgment calls

- **Never create a `CLAUDE.md` in the tree** (or in any directory above it). Claude Code reads
  `AGENTS.md` only when no `CLAUDE.md` exists on the path; one stray `CLAUDE.md` silently drops the
  root and nested `AGENTS.md` guidance. Put personal additions in your user-level memory instead.
- **AGENTS.md has a few gemini-cli-specific passages** — the `FindFiles '**/*'` glob warning and
  the `hover`/`definition` language-server tool instructions. They are harmless as Claude context;
  follow the intent (avoid tree-wide globs; use LSP-grade lookup where the harness offers it).
- **Search scope:** `.geminiignore` exists to *un*-ignore `/local/`, `/third_party/*`, and
  `/vendor/` for Gemini's search tools. Claude's Grep respects gitignore, so API-usage searches
  silently skip those petals. Upstream's `AGENTS.md` now recommends `git grep` (`fx grep` was
  removed and `jiri grep` deprecated in 6cf57af7b34, 2026-10-07); `git grep` searches only the
  repo it runs in, so for a cross-petal search run it in each petal's repo, or use
  `rg --no-ignore-vcs`.
- **Not bridged:** `//.agents/rules/` (e.g. `rust_zx.md`) — read them when working in the relevant
  area. Also `//.agents/agents/` (subagent definitions written with Antigravity tool names such as
  `view_file` and `run_command`), `//.agents/config/permissions/` (tiered command allowlists), and
  `//.agents/code-review/` (hosted review-agent config). None of these map onto Claude Code files
  as-is.
- **Per checkout, not per machine.** The farm is git-excluded local state — every separate
  checkout and every `fx worktree` slot needs its own run. See `fuchsia-multi-checkout`.
- **Antigravity users need none of this:** its workspace skills dir is `<workspace>/.agents/skills/`,
  which is exactly where upstream puts the global skills — they are discovered natively. This
  bridge is for Claude Code (and any harness reading a skills directory; point the farm elsewhere
  by editing `target_dir` if yours differs).

## Note for the agent

- Never commit `.claude/`, a `CLAUDE.md`, or `.gitignore` edits to fuchsia.git — the script uses
  `.git/info/exclude` precisely because upstream must stay clean.
- If a just-added upstream skill is missing from a session, the fix is: re-run the script, then
  restart the session (skills are enumerated at session start).
- The script is stdlib-only Python 3 and read-only toward tracked files; it is safe to run in a
  dirty tree.
- Earlier versions also created `AGENTS.md -> GEMINI.md` symlinks. On a checkout bridged before
  the upstream rename, check that each `AGENTS.md` is now a regular file (`test -L AGENTS.md`
  should fail); if a dangling symlink is in the way of the tracked file, delete it and re-run
  `jiri update`. Leftover `/…/AGENTS.md` lines in `.git/info/exclude` can be deleted.
