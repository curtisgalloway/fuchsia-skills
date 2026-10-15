---
name: fuchsia-multi-checkout
description: >-
  Run several Fuchsia workstreams (or several agents) on one machine without their builds,
  emulators, or target devices colliding. Use when setting up a second workstream, when fx/ffx
  picks up the wrong tree or targets the wrong device, or on cross-tree symptoms (two emulators
  fighting, a shell running another tree's tools). Covers `fx worktree` (the current upstream
  mechanism, with the in-tree fx-worktree skill as its reference), the two conventions that
  prevent the real collisions, and the legacy separate-checkout setup.
---

<!--
SPDX-FileCopyrightText: 2026 Curtis Galloway
SPDX-License-Identifier: Apache-2.0
-->

# fuchsia-multi-checkout — several Fuchsia workstreams on one machine

Goal: several agents working different topics concurrently without stepping on each other's builds,
emulators, or target devices.

**Headline:** Fuchsia now ships **`fx worktree`** — first-class support for parallel workstreams
backed by a pool of physical checkouts. Prefer it for new workstreams. Hand-managed separate
checkouts still work and are still what most existing trees are; that path is documented near the
end. Either way, the **two conventions** below are what actually prevent collisions.

> Verified against fuchsia 5ded3fc08ae, 2026-10-15 unless marked otherwise. `fx worktree` landed
> between 2026-06 and 2026-08 — a tree older than that will not have it, and `jiri update` is the
> prerequisite.

## `fx worktree` (preferred)

**The command reference is upstream's, not ours.** Read the in-tree skill
`//.agents/skills/fx-worktree/SKILL.md` (linked into Claude Code by `fuchsia-claude-setup`) and
`//tools/devshell/worktree/README.md`. In brief: `fx worktree add <name>` leases a pool slot
(provisioning one if the pool is empty, and syncing it by default), `locate`, `list`, and `remove`
manage the task, and `fx worktree pool …` manages the physical checkouts. `add`, `list`, and
`pool list` take `--json`.

What the upstream docs say less loudly:

- **The help text is stale.** `tools/devshell/worktree.fx` still lists `lease` and `release`, which
  do not exist, and omits `pool` and `metrics`. The argparse surface in
  `tools/devshell/worktree/main.py` is authoritative; leasing happens inside `add`/`remove`.
- **Detached HEAD by design.** Slots stay on a detached HEAD synced to the parent's `JIRI_HEAD`,
  despite `add`'s argument help calling the name "Name of worktree / branch"; `remove` detaches
  again. Create a branch yourself if you need one.
- **Slots keep `out/` across leases.** `add` backs up each `args.gn` and `remove` restores it, so
  `fx set` changes made during a task do not survive into the next lease.
- **`jiri update` does not move the worktrees.** Updating the main checkout leaves every slot on its
  previous manifest revision; run `jiri worktree sync` inside the slot (or
  `jiri worktree sync "$(fx worktree locate <name>)"`) to bring it along.

### Cost model — worktrees are about lifecycle, not disk

*Unverified since 2026-08:* a worktree was measured **not** sharing `prebuilt/` with the parent tree
(different inodes, free space dropping by the full amount). The current upstream README says
`jiri worktree add` "links CIPD prebuilts", so re-measure (`stat -c %i` on a prebuilt file in both
trees, `df` before and after) before relying on the numbers below.

| | Disk |
|---|---|
| `fx worktree` slot | ~30 GB (own 25 GB `prebuilt/`) + its build dir |
| Separate full checkout | ~44 GB+ (own prebuilt *and* own git objects) |

Real savings, but ~30% — not the near-free duplication the layout suggests. **Choose `fx worktree`
because every slot follows the main checkout's manifest (`jiri update`, then `jiri worktree sync` per
slot) and slots are managed as a pool**, not to save space. The failure mode it fixes is N
hand-maintained checkouts silently drifting to N different integration revisions.

### Workflow

```bash
cd "$FUCHSIA_DIR"
fx worktree add <topic>                       # may provision a whole checkout — heavy
cd "$(fx worktree locate <topic>)"            # or cd .jiri_root/worktrees/<topic>
fx set <product>.<board> --debug --ccache
fx set-device <device-or-emu>                 # NOT `ffx target default set`
fx build
```

`fx set` and `fx build` behave normally inside a worktree; the build dir is an ordinary `out/` inside
it. To retire a workstream: `fx worktree remove <topic>` returns the slot for reuse.

**Claude Code users:** the agent bridge (the `.claude/skills/` farm) is
git-excluded local state and does **not** come along into a new worktree or checkout — run
`fuchsia-claude-setup` in each one.

## The two conventions (this is the whole trick)

These apply to worktrees and separate checkouts alike.

1. **Targets:** use `fx set-device <name>` — it stores the default **per build dir**
   (`out/<dir>.device`, surfaced to ffx as `$FUCHSIA_NODENAME`). Do **not** use
   `ffx target default set`: current trees removed the command outright and bypass stateful
   `target.default` config entirely (fxbug.dev/394619603); on older checkouts it writes a **shared**
   user-level default that overrides every tree. Clear a stale value with
   `ffx config remove target.default`.
2. **Emulators:** always pass a unique name: `fx ffx emu start --name <topic>-emu …`. Emulator
   instances live under `~/.local/share/Fuchsia/ffx/emu/instances/<name>`, are keyed by name only,
   and are shared across every tree — two workstreams both using `fuchsia-emulator` corrupt each
   other. This is the one genuinely shared piece of ffx state.

**Default build flags — `--debug --ccache`.** Debug mode (assertions on, full symbols) suits driver
work, and ccache speeds rebuilds. Pass `--ccache` *explicitly*: Fuchsia only auto-enables it when
`CCACHE_DIR` is set, and the default compilation mode is `balanced`. Requires `ccache` installed. The
build dir encodes the mode (`out/<product>.<board>-debug`) and the default device is per-build-dir,
so re-run `fx set-device` if you switch modes.

## Why it works (per-build-dir isolation)

Already isolated per build dir — no action needed. *Verified against fuchsia 5ded3fc08ae, 2026-10-15;
cited by file, since line numbers drift.* (The ffx daemon, which used to keep a per-build-dir socket,
was removed upstream in 2026-08 — "[ffx] Remove the daemon from ffx", relanded 3abfd4fd3e6.)

| Concern | Where | Source |
|---|---|---|
| Which tree `fx`/`ffx` act on | resolved from **cwd** (`.fx-root` walk); `$FUCHSIA_DIR` ignored; `$PATH`-invoked binary auto-redirects into the cwd's tree | `scripts/fx` (`find_tree_root`), `src/developer/ffx/scripts/ffx` |
| ffx build config (product bundle, repo, qemu/sdk overrides) | `out/<dir>/ffx-config.json` | `src/developer/ffx/config/src/environment.rs`, `paths.rs` (`DEFAULT_BUILD_CONFIG_FILE`) |
| default target device | `out/<dir>.device` → `$FUCHSIA_NODENAME` | `tools/devshell/set-device`, `tools/devshell/lib/vars.sh` |
| default target resolution | `-t` flag, else `$FUCHSIA_DEVICE_ADDR`/`$FUCHSIA_NODENAME`; stateful `target.default` **bypassed** | `src/developer/ffx/lib/target/src/lib.rs` (fxbug.dev/394619603) |
| build lock | `out/<dir>.build_lock` | `tools/devshell/lib/vars.sh` |

The remaining single-tree assumption is the **shell environment**: a typical `~/.bashrc` pins one
tree's `.jiri_root/bin` onto `PATH` and sources its `fx-env.sh`, so another shell inherits the wrong
tools and survives only via the cwd-redirect. Worktrees do **not** fix this — see `fx-tree env` below.

## Do NOT use `FFX_ISOLATE_DIR` / `--isolate-dir` as a per-tree switch

Tempting but **wrong**. Isolate mode flips ffx to `EnvironmentKind::Isolated`, which has no
`build_dir`, so ffx stops loading `out/<dir>/ffx-config.json` (product bundle, repository, qemu/sdk
overrides) — breaking `fx ffx emu`, `fx serve`, and product-bundle resolution. It is for hermetic
tests, not day-to-day multi-tree dev. (Overriding `XDG_*` is also wrong: it leaks into every other
XDG-aware tool.) The per-build-dir isolation above is what you want.

## Legacy: separate full checkouts and the `fx-tree` helper

Still valid, and still what most existing trees are. Use it for trees that predate `fx worktree`, or
when a workstream genuinely needs its own integration revision (e.g. keeping a known-good bench tree
pinned while another moves forward).

`fx-tree` lives in this skill's directory; symlink it onto `$PATH` if you like
(`ln -s <skill-dir>/fx-tree ~/.local/bin/fx-tree`).

| Command | What it does |
|---|---|
| `eval "$(fx-tree env)"` | Point the **current shell** at the tree your cwd is in: strips other trees' `.jiri_root/bin` from `PATH`, prepends this one's, sets `FUCHSIA_DIR`, adds an `[fx:<tree>]` prompt tag. **Still useful with worktrees** — this is the shell-env gap nothing else closes. |
| `fx-tree doctor` | Diagnose multi-tree hazards (PATH/`FUCHSIA_DIR`, per-build-dir device, shared user-level default target, emulator-name collisions). |
| `fx-tree list` | All checkouts under `$FX_TREE_ROOTS` (default `~/src`): build dir and default device. |
| `fx-tree new NAME [opts]` | **Superseded by `fx worktree add`** for new workstreams. Still the tool if you need a genuinely independent checkout. Dry-run by default; `--run` to execute. |
| `fx-tree shim [DIR]` | Drop a `.envrc` (`eval "$(fx-tree env)"`) into a tree and git-exclude it (for direnv). |

Upstream also ships `//tools/multifuchsia/` (README there), a long-standing alternative that clones
checkouts as btrfs snapshots of a nightly-built clean tree; consider it if your disk is btrfs.

## What changed, and why the old advice was wrong

Earlier versions of this skill said **do not use git worktrees**, because `git worktree add` only
materializes git-tracked files while Fuchsia's heavy parts (`prebuilt/`, `.jiri_root/`,
`integration/`) are gitignored, so a worktree came up missing all of it.

That reasoning was correct; upstream simply solved it. `fx worktree` does not create a bare git
worktree — it calls `jiri worktree add`, which provisions a **complete checkout** as a pool slot.
The gitignored parts are materialized per slot rather than shared, which is exactly why the disk
cost is ~30 GB rather than near-zero.

## Note for the agent

- **Verify the command surface against the tree in front of you.** `worktree.fx`'s header and
  `--help` both disagree with the implemented subcommands; read `tools/devshell/worktree/main.py`.
- `fx worktree add` provisions a whole checkout when the pool is empty — heavy, confirm-first. So is
  `fx-tree new --run`.
- After `jiri update` in the main checkout, existing slots are still on the old revision until
  `jiri worktree sync` runs in each one.
- `fx worktree` requires a recent tree. On an older checkout the answer is `jiri update` first; before
  running it, sweep **every** project for local work (`jiri runp -show-name-prefix git status
  --porcelain`), not just the root repo — local commits and dirty files hide in `third_party/*`.
- `jiri update` defaults to `-gc=true`, which deletes projects that left the manifest. If a project
  carrying local branches has moved path upstream, that work goes with it. Use `-gc=false` on the
  first pass after a long gap.
- `jiri update` will not move a project that is sitting on a local branch. Detach (`git checkout
  --detach JIRI_HEAD`) any project you want updated, or the tree looks updated while the projects
  you care about silently stay behind.
- The `fx-tree env` output is shell code meant to be `eval`'d — emitting it without eval just prints
  the snippet.
