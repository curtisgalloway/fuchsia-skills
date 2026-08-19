---
name: fuchsia-claude-setup
description: >-
  Bridge a Fuchsia checkout's in-tree agent config (Gemini-oriented GEMINI.md files and ~80
  scattered SKILL.md skills) into Claude Code's conventions: an AGENTS.md symlink, a generated
  .claude/skills farm, and .git/info/exclude entries — all local, nothing committed. Use when
  setting up Claude Code on a new Fuchsia checkout or worktree, when in-tree skills are missing
  from a session, after `jiri update` (link rot), or when asked how Fuchsia's Gemini skills map
  to Claude. Ships scripts/link_fuchsia_skills.py, the re-runnable regenerator.
---

<!--
SPDX-FileCopyrightText: 2026 Curtis Galloway
SPDX-License-Identifier: Apache-2.0
-->

# fuchsia-claude-setup — use Fuchsia's in-tree agent config from Claude Code

Upstream fuchsia.git ships real agent guidance, but all of it in Gemini-oriented locations that
Claude Code never reads. This skill installs the local bridge and keeps it fresh.

> Verified against a public fuchsia.git checkout on 2026-08-19 (~79 tracked skills). Upstream adds
> and moves skills continuously — the counts and paths below will drift; the script does not care.

## What upstream provides vs. what Claude Code reads

| Upstream (fuchsia.git, Gemini-oriented) | Claude Code equivalent | Bridge |
|---|---|---|
| `GEMINI.md` at the root (+ nested ones, e.g. wlan, forensics) | `AGENTS.md` / `CLAUDE.md`, loaded natively | symlink `AGENTS.md -> GEMINI.md` beside each |
| ~80 `SKILL.md` skills: 12 global in `//.agents/skills/`, the rest in team dirs (`src/devices/skills/`, `zircon/skills/`, `docs/skills/`, …) | `.claude/skills/<name>/SKILL.md` | symlink farm generated from `git ls-files` |
| Discovery via `skills.json` + `fx manage-skills` (gemini-cli only) | directory presence is the discovery | the regenerator script IS the discovery step |
| `.geminiignore` (un-ignores `/local/`, `/third_party/*`, `/vendor/` for search) | none — Grep respects gitignore | guidance below, not a file |

There is no upstream `.claude/` or root `AGENTS.md`, and you cannot commit one — so everything the
bridge creates is untracked and listed in `.git/info/exclude` (never `.gitignore`, which is a
tracked upstream file).

## Procedure

```bash
python3 <skill-dir>/scripts/link_fuchsia_skills.py --root <fuchsia-dir>
```

Run it from anywhere inside the checkout and `--root` can be omitted (it walks up to `.jiri_root`).
Preview with `--dry-run`. Then start a fresh Claude session in the checkout and confirm the in-tree
skills appear (e.g. `debugging-driver-binding`, `manage-emulator`) and that AGENTS.md guidance is
loaded.

**Re-run after every `jiri update`.** This is the whole reason it is a script: upstream renames and
moves skill directories, and a static symlink farm rots silently — an audit of one real checkout
found 4 dangling links and ~15 missing skills after two months. The script prunes dangling/stale
links, fixes renames, and reports what changed. It only ever touches symlinks; real files and
directories in `.claude/skills/` are left alone.

### Keeping the skill list lean

~80 skill descriptions load into every session. To carry only what you work on:

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
3. Symlinks `AGENTS.md -> GEMINI.md` at the root and beside every nested tracked `GEMINI.md`. An
   existing *regular* `AGENTS.md` is never replaced — reported and skipped.
4. Appends `/.claude/` and each `AGENTS.md` path to `.git/info/exclude` (idempotent).

## Caveats and judgment calls

- **GEMINI.md has a few gemini-cli-specific passages** — the `FindFiles '**/*'` glob warning and
  the `hover`/`definition` language-server tool instructions. They are harmless as Claude context;
  follow the intent (avoid tree-wide globs; use LSP-grade lookup where the harness offers it).
- **Search scope:** `.geminiignore` exists to *un*-ignore `/local/`, `/third_party/*`, and
  `/vendor/` for Gemini's search tools. Claude's Grep respects gitignore, so API-usage searches
  silently skip those petals. For cross-petal searches use `jiri grep <text>` or
  `rg --no-ignore-vcs`.
- **Rules files:** upstream also ships `//.agents/rules/` (e.g. `rust_zx.md`). These are not
  auto-loaded by the bridge; read them when working in the relevant area, or fold the ones you
  want into your own project memory.
- **Per checkout, not per machine.** The farm and AGENTS.md are git-excluded local state — every
  separate checkout and every `fx worktree` slot needs its own run. See `fuchsia-multi-checkout`.
- **Antigravity users need none of this:** its workspace skills dir is `<workspace>/.agents/skills/`,
  which is exactly where upstream puts the global skills — they are discovered natively. This
  bridge is for Claude Code (and any harness reading `AGENTS.md` + a skills directory; point the
  farm elsewhere by editing `target_dir` if yours differs).

## Note for the agent

- Never commit `.claude/`, `AGENTS.md` symlinks, or `.gitignore` edits to fuchsia.git — the script
  uses `.git/info/exclude` precisely because upstream must stay clean.
- If a just-added upstream skill is missing from a session, the fix is: re-run the script, then
  restart the session (skills are enumerated at session start).
- The script is stdlib-only Python 3 and read-only toward tracked files; it is safe to run in a
  dirty tree.
