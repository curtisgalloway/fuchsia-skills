---
name: fuchsia-checkout
description: >-
  Drive a fresh Fuchsia source checkout from zero with an agent: prerequisites, preflight,
  the bootstrap script, environment setup, first build (fx set/fx build), and emulator smoke
  test — with the agent-specific handling (multi-hour background steps, non-persistent shell
  env, the Googler .gitcookies auth failure) that the docs don't cover. Use when asked to
  fetch/bootstrap/check out the Fuchsia source on a machine that has no tree, or when a
  bootstrap fails mid-way. For a second tree on a machine that already has one, use
  fuchsia-multi-checkout instead.
---

<!--
SPDX-FileCopyrightText: 2026 Curtis Galloway
SPDX-License-Identifier: Apache-2.0
-->

# fuchsia-checkout — fetch and stand up a Fuchsia tree from zero

Source of truth: `//docs/get-started/get_fuchsia_source.md` and `build_fuchsia.md` (on the web:
fuchsia.dev/fuchsia-src/get-started/). This skill encodes that flow **plus the agent-shaped
gotchas**. If this skill and the docs in the fetched tree disagree, the tree's docs win — re-read
them once the checkout exists.

> Verified against the docs as of 2026-08. Linux x64 is the supported host path (`apt`-based
> instructions; the preflight tool is x64-only). For anything else, read the docs first rather
> than improvising.

## 0. Before touching the network

- **Disk:** require ~100 GB free on the target filesystem (`df -h <parent-dir>`). The checkout
  alone is ~45 GB (25 GB of that is `prebuilt/`); the first build adds tens of GB more. Stop and
  tell the user if it isn't there.
- **Destination:** the bootstrap creates a `fuchsia/` directory **under the cwd it runs in**. Run
  it from the intended parent (e.g. `~/src`), never from inside another git repo.
- **Prereqs:** `curl`, `file`, `unzip`, and `git` ≥ 2.31 — `sudo apt install curl file git unzip`
  (needs the user for sudo). If the emulator is a goal, KVM access
  (`ls -l /dev/kvm`, user in the `kvm` group) is worth checking now too.

## 1. Preflight

```bash
curl -sO https://storage.googleapis.com/fuchsia-ffx/ffx-linux-x64 \
  && chmod +x ffx-linux-x64 && ./ffx-linux-x64 platform preflight
```

Fix anything it flags before spending hours on the fetch.

## 2. Bootstrap (the long step)

```bash
curl -s "https://fuchsia.googlesource.com/fuchsia/+/HEAD/scripts/bootstrap?format=TEXT" \
  | base64 --decode > bootstrap.sh
```

Review `bootstrap.sh` briefly (the docs themselves recommend it), then run it **in the
background** with output to a log file — the fetch takes on the order of hours and will outlive
any foreground tool timeout:

```bash
bash bootstrap.sh > bootstrap.log 2>&1   # run backgrounded via the harness, not with `&`
```

Poll the log and the tree (`ls fuchsia/.jiri_root` appearing, log tail advancing) rather than
waiting. Progress can look stalled during large pack downloads; check byte counts, not just
timestamps.

**Failure modes:**

- **`Invalid authentication credentials`** — near-certain for Googlers: `~/.gitcookies` carries
  credentials for `googlesource.com` repos the script wants to fetch anonymously. Fix per the
  docs appendix (get passwords for the listed repos, or delete the offending cookie lines), then
  re-run.
- **Network timeouts on slow links** — the docs sanction editing `bootstrap.sh` to raise its
  fetch timeouts.
- **Interrupted bootstrap** — don't start over. If `fuchsia/.jiri_root` exists, resume with
  `fuchsia/.jiri_root/bin/jiri update` (also backgrounded); jiri picks up where it left off.

## 3. Environment

The user-facing setup is two shell-profile lines (checkout at `~/fuchsia` in the docs — substitute
the real path):

```sh
export PATH=<fuchsia-dir>/.jiri_root/bin:$PATH
source <fuchsia-dir>/scripts/fx-env.sh
```

**Agent note:** your shell does not persist env between commands and may not source the profile.
Don't depend on step 3 having happened — invoke tools by absolute path
(`<fuchsia-dir>/.jiri_root/bin/jiri`, `<fuchsia-dir>/scripts/fx`) or rely on cwd being inside the
tree (`fx` self-locates via the `.fx-root` walk). Verify with `jiri help` / `fx help` from the
checkout dir.

Optional, needs sudo: `fx setup-ufw` (firewall rules for emulator traffic on Linux).

## 4. First build

```bash
fx set core.x64        # minimal product, boots in the emulator; workbench_eng.x64 for more
fx build
```

`fx build` is another hours-scale background-and-poll step on first run. Driver-development trees
usually want `fx set <product>.<board> --debug --ccache` instead — see `fuchsia-multi-checkout`
for the reasoning; `--ccache` requires ccache installed.

## 5. Smoke test and handoff

- Emulator: follow `//docs/get-started/set_up_femu.md`; the in-tree `manage-emulator` skill
  covers start/stop once the bridge below is installed. If the machine will ever host a second
  tree, pass a unique `--name` from day one.
- **Run `fuchsia-claude-setup` now.** The fresh tree ships ~80 in-tree agent skills and its
  project instructions in GEMINI.md, with no AGENTS.md — Claude Code sees none of it until that
  bridge exists. Checkout without the bridge is half a setup.

## Note for the agent

- Two steps here (bootstrap, first build) each take hours. Background them, schedule quiet
  polls, and never conclude failure from a harness timeout — check the log.
- `sudo` steps (apt, setup-ufw) need the user; batch them into one ask.
- Don't run two jiri operations concurrently in one tree, and don't `jiri update` a tree that
  might carry local work without the sweep described in `fuchsia-multi-checkout`'s agent notes.
- Once the tree exists, prefer its own `//docs/get-started/` over this skill for anything that
  looks version-dependent (product names, flags).
