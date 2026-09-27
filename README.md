<!--
SPDX-FileCopyrightText: 2026 Curtis Galloway
SPDX-License-Identifier: Apache-2.0
-->

# Claude Code for Fuchsia developers

Agent skills for working on the public [Fuchsia](https://fuchsia.dev) tree: checking out the
source, bridging the tree's Gemini-oriented agent content into Claude Code, running several
workstreams on one machine, answering deep source questions, debugging driver binding, and driving
real hardware.

Everything here is Apache 2.0 and written to be agent-neutral where it can be — but the setup story
is specifically about Claude Code, because that is the harness the tree does *not* ship support for.

## Install (once per machine)

This repo is a Claude Code plugin and hosts its own single-plugin marketplace
(`.claude-plugin/marketplace.json`; skills are auto-discovered from `skills/`). In a session, or
with the `claude plugin` CLI outside one:

```
/plugin marketplace add curtisgalloway/fuchsia-skills
/plugin install fuchsia-skills@fuchsia-skills
```

For a local clone, add the clone directory as the marketplace instead:

```
/plugin marketplace add /path/to/fuchsia-skills
/plugin install fuchsia-skills@fuchsia-skills
```

Full walkthrough: [docs/fuchsia-claude-onboarding.md](docs/fuchsia-claude-onboarding.md).

## Start here, in this order

| Skill | Use it when |
|---|---|
| **`fuchsia-checkout`** | You have no tree yet. Drives the bootstrap end to end, including the multi-hour steps that need to run in the background and the `.gitcookies` auth failure on anonymous checkouts. |
| **`fuchsia-claude-setup`** | Right after the checkout, and again after every `jiri update`. Bridges the tree's Gemini-oriented agent content into Claude: `AGENTS.md → GEMINI.md` symlinks, a generated `.claude/skills/` farm over all ~80 in-tree skills, and `.git/info/exclude` entries so nothing leaks into fuchsia.git. |
| **`fuchsia-multi-checkout`** | You want a second workstream (or a second agent) on one machine without builds, `ffx` daemons, or target devices colliding. Covers `fx worktree`. Note the bridge above is local state — re-run it in each worktree. |

**Antigravity users:** you don't need `fuchsia-claude-setup`. Its workspace skills directory is
already `<workspace>/.agents/skills/`, exactly where upstream puts the global skills.

## Day-to-day

- **`fuchsia-source`** — deep source questions answered by actually reading the tree: DFv2 API
  usage, CML shard and bind routing, what a `ZX_ERR_PEER_CLOSED` really means, in-tree examples to
  model on. Returns the answer with `path:line` citations.
- **`fuchsia-driver-bind-debug`** — your driver didn't bind. An ordered ladder from
  `ffx driver list-devices --unbound` through the offline bind debugger to `zxdb`, with the
  realistic limits of each rung.

## If you drive real hardware

Both are written against one specific bench — treat them as templates for your own lab rather than
turn-key:

- **`fuchsia-hardware-bench`** — remote board bring-up: HDMI capture, serial-HID keyboard, network
  power control, gigaboot fastboot-over-TCP, link-local `ffx`.
- **`fuchsia-boot-test-ci`** — turning a boot on real hardware into a trustworthy pass/fail verdict,
  and diagnosing the false ones.

## Keeping a tree built: fx-updater

**[curtisgalloway/fx-updater](https://github.com/curtisgalloway/fx-updater)** is a small CLI, not a
skill: it runs `jiri update` and `fx build` on a systemd timer so the first build of your day is
warm. It skips the run when `jiri status` shows uncommitted work, stops below a free-disk floor,
and records each outcome in a status file. Early: Linux with systemd only.

```bash
uv tool install git+https://github.com/curtisgalloway/fx-updater
fx-updater install --fuchsia-dir /path/to/fuchsia --build-dir core.x64
fx-updater status
```

Two habits here depend on it. Re-run `fuchsia-claude-setup` after the scheduled update lands (a
`--hook` command can do that for you). And a boot-test runner (`fuchsia-boot-test-ci`) that builds
the same tree should wait on fx-updater's lock rather than build over a `jiri update` in progress;
its `docs/contract.md` says what is promised. `fx-updater --skill` prints its agent-facing usage.

## Companion skills in driver-lab and public-skills

These are not Fuchsia-specific, so they live elsewhere, but several of the skills here hand off
to them by name. Both repos install from one marketplace, `curtisg-skills`, which
[curtisgalloway/public-skills](https://github.com/curtisgalloway/public-skills) hosts:

```
/plugin marketplace add curtisgalloway/public-skills
/plugin install driver-porting@curtisg-skills
/plugin install agent-workflow@curtisg-skills
```

`driver-porting` is served from
**[curtisgalloway/driver-lab](https://github.com/curtisgalloway/driver-lab)**; `agent-workflow`
from public-skills itself.

### Porting a driver into Fuchsia (driver-lab)

Three skills compose into one pipeline for reimplementing a driver from a differently-licensed OS,
splitting the work across contexts so encumbered source never reaches the agent that writes the new
code:

- **`os-investigator`**: reads the Linux/vendor source and returns hardware facts and mechanism
  descriptions *in original words*, never source, with every fact tagged by provenance
  (databook / standard / device-tree / source-observed). Ships a mechanical leak scanner.
- **`cleanroom-spec`**: orchestrates the above into a complete clean-room implementation spec for
  one peripheral (Ethernet MAC, UART, SD/MMC, USB, I2C/SPI, …), and enforces the transfer protocol
  and the provenance ledger.
- **`cleanroom-implementer`**: the consumer side, with the rules, hooks, and audit procedure for
  the agent that turns that spec into Fuchsia driver code without ever having seen the original.

For driver source you own (or may otherwise copy from), **`anchored-peripheral-spec`** produces
the same spec shape without the wall: every fact carries a `file:line` anchor at a pinned commit
so a reviewer can check the spec against the code. **`reference-driver-review`** reviews a driver
against its reference, and **`spec-verifier`** checks a spec against the sources it cites.

Pair these with **`fuchsia-source`** for the target-side question: how the DFv2 API, bind rules,
and CML routing actually work in the tree you're writing into.

### Board experts (driver-lab)

- **`rpi-expert`** (Pi 5 / CM5, BCM2712 + RP1), **`rpi4-expert`** (Pi 4, BCM2711),
  **`indiedroid-nova-expert`** (RK3588S), **`pixel10-expert`** (Pixel 10, Tensor G5): memory maps and MMIO
  addresses, device tree, boot chain and exception-level hand-off, PSCI/SMP, interrupts, timers,
  clocks/power, and which datasheet to cite. `fuchsia-source` and `fuchsia-driver-bind-debug`
  both hand off to these for board-specific hardware questions. **`board-spec-scaffold`** starts a
  new one for a board that has none.

### Working-style skills (public-skills, `agent-workflow`)

- **`intern-mode`** (stop and report after 12 turns without progress, useful on long bring-up
  sessions), **`design-partner`** (think through an approach without touching code),
  **`project-plan`** / **`orchestrate-milestones`** (design doc to milestones, run one per
  session), **`lab-notebook`** (running notes for a bring-up), **`handoff`** (carry state across a
  context clear), **`learn`** (capture lessons into instruction files),
  **`agent-agnostic-skills`** (write skills that survive a change of harness).

## Installing in Antigravity

Skills are plain directories. Clone the repo and symlink the ones you want into
`~/.gemini/antigravity/skills/` (user-level) or `<workspace>/.agents/skills/` (one project):

```bash
git clone https://github.com/curtisgalloway/fuchsia-skills ~/src/fuchsia-skills
ln -s ~/src/fuchsia-skills/skills/fuchsia-source ~/.gemini/antigravity/skills/fuchsia-source
```

Confirm with `/skills` that they loaded. `fuchsia-source` dispatches a subagent; under Antigravity
install it as `<workspace>/.agents/agents/<name>.md` with `subagent: true` in the frontmatter
instead, where it shows up under `/agents`.

## License

Apache 2.0 — see [LICENSE](LICENSE). Source files carry the SPDX identifier only.
