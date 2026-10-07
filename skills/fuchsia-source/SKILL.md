---
name: fuchsia-source
description: >-
  Launch a subagent to answer deep Fuchsia source questions by reading the tree — DFv2 API usage,
  CML shard and bind routing, framework behavior behind runtime errors (ZX_ERR_PEER_CLOSED,
  ZX_ERR_NOT_FOUND, ZX_ERR_NOT_SUPPORTED), and working in-tree examples (PDev, serialimpl,
  composite) to model on. Use whenever the answer requires tracing Fuchsia source rather than
  recalling it. Not for Linux/clean-room hardware questions (use cleanroom-investigator, or hardware-investigator + board-expert) or
  simple symbol lookups (use Explore). Returns the answer, the correct pattern, and path:line
  sources.
---

<!--
SPDX-FileCopyrightText: 2026 Curtis Galloway
SPDX-License-Identifier: Apache-2.0
-->

# fuchsia-source — Fuchsia internal source investigation

Launches a general-purpose subagent to answer deep Fuchsia source questions.
Designed for multi-layer investigations: SDK API → framework implementation → working examples →
concrete recommendation.

## When to use

- How does DFv2 route a service offer from a parent driver to a child driver's namespace?
- What instance name should I use when connecting to `fuchsia.hardware.platform.device.Service`?
- What does a CML shard add, and what does my driver's CML need to include?
- Find a working example of a non-composite driver that uses `fdf::PDev`.
- How does `MakeOffer2<Service>(name)` affect what instance is in the target's namespace?
- What Zircon kernel API or constant should I use for X?
- What does a bind library symbol expand to?

Do NOT use this for Linux kernel / clean-room hardware questions — use `cleanroom-investigator` (in the
`cleanroom-skills` repo) for the clean-room case, or `hardware-investigator` + `board-expert` (in the
`driver-lab` repo) when the answer may be cited. Do NOT use this for a simple "where is this
file / symbol defined" lookup — use `Explore`.

---

## Resolve paths before launching (do this first — do not hardcode)

Paths, branch names, and worktree locations drift. Resolve them at call time and **pass the resolved
absolute paths into the subagent prompt** rather than relying on the literals in this skill. Have the
calling agent run these once and substitute the results:

```bash
# Canonical upstream tree (SDK / framework / examples live here). Substitute
# this machine's checkout location (commonly ~/src/fuchsia); verify it exists
# before trusting it.
FUCHSIA_ROOT="$(git -C <fuchsia-checkout> rev-parse --show-toplevel 2>/dev/null \
                || echo <fuchsia-checkout>)"

# Our worktree (OUR driver + OUR CML/bind edits live here), if the project uses
# one. Resolve by branch, not by a hardcoded path, since the worktree dir
# changes with the branch name — substitute the project's topic branch.
WORKTREE="$(git -C "$FUCHSIA_ROOT" worktree list --porcelain \
            | awk '/^worktree /{w=$2} /branch .*<topic-branch>/{print w}')"

# Sanity-check both before proceeding. If either is empty or missing, stop and
# ask the user for the correct location rather than guessing.
[ -d "$FUCHSIA_ROOT" ] && [ -d "$WORKTREE" ] || echo "PATHS NOT RESOLVED — ask the user"
```

Tell the subagent explicitly: **verify any path exists (`test -d` / `test -f`) before reasoning about
its contents** — the layout map below is a starting point, not ground truth.

### Which tree to read from

- **SDK, framework, and in-tree example drivers → read from `$FUCHSIA_ROOT`** (canonical upstream).
- **Our driver, and our CML/bind/board changes → read from `$WORKTREE`.**
- When comparing "our driver" against a working example, compare the **`$WORKTREE` copy of our driver**
  against the **`$FUCHSIA_ROOT` copy of the example**. Do not compare two copies of the same tree.

---

## Tree layout (paths are relative to `$FUCHSIA_ROOT` unless noted)

```
sdk/lib/driver/                        DFv2 driver APIs
  component/cpp/                         DriverBase2, AddChild, node_add_args, node_offers
  incoming/cpp/                          fdf::Namespace, Connect<Service>
  platform-device/cpp/                   fdf::PDev, Connect, MapMmio
  mmio/cpp/                              MmioBuffer
sdk/fidl/                              FIDL protocol definitions
  fuchsia.hardware.platform.device/      PDev FIDL
  fuchsia.hardware.platform.bus/         Platform bus FIDL
  fuchsia.driver.framework/             Node, Offer, NodeAddArgs
  fuchsia.component.decl/               OfferService, NameMapping
sdk/lib/component/incoming/cpp/        component::kDefaultInstance, MakeServiceMemberPath
src/devices/bus/drivers/platform/      Platform bus implementation (platform-device.cc is key)
src/devices/bind/                      Bind libraries (fuchsia.platform, etc.)
src/devices/board/drivers/             Board drivers (real examples to compare against)
src/devices/serial/drivers/            Serial drivers
src/devices/i2c/drivers/               I2C drivers (good DFv2 examples)
vendor/<vendor>/                       Vendor-specific code (Fuchsia's //vendor/...), if present —
                                         mirrors the layout above (e.g. vendor/<vendor>/src/devices/)
```

If the target board uses **vendor-specific drivers**, the relevant board/driver code may live under
`$FUCHSIA_ROOT/vendor/` (Fuchsia's `//vendor/...`) rather than the public `src/devices/` tree. Check for
a `vendor/` directory first (`test -d "$FUCHSIA_ROOT/vendor"`); if present, treat
`vendor/<vendor>/src/devices/` as an additional search root for examples and implementations.

---

## Search rules for the subagent

- **Always use `rg` (ripgrep), never `grep -r`** — the Fuchsia tree is huge and `grep -r` will time out
  or produce overwhelming output.
- ripgrep uses Rust regex, where `|` is alternation and `\|` matches a *literal* pipe. Write alternations
  unescaped, and escape regex metacharacters like `.`:
  ```bash
  rg -l "PDev::Connect|pdev\.MapMmio|kFragmentName" "$FUCHSIA_ROOT/src/devices/"
  ```
- Pass real, already-resolved absolute paths to `rg`/`find` — never `$HOME`, `~`, or unverified command
  substitution inside the search itself.
- Search within `$FUCHSIA_ROOT/sdk/` for API questions; within `$FUCHSIA_ROOT/src/devices/` for
  implementation/example questions. Filter by path when a search returns too much.
- For a board with **vendor-specific drivers**, also search `$FUCHSIA_ROOT/vendor/*/src/devices/`
  (`//vendor/...`) — the closest working example may be the vendor's own driver, not a public one.

---

## Investigation strategy

The subagent should follow this order — it finds the answer faster than reading frameworks top-down:

1. **Working example first.** Find a real, non-composite, in-tree driver that does what we need.
   Compare it against our driver. The difference IS the answer. Good hunting grounds:
   - `src/devices/i2c/drivers/` (I2C device drivers using PDev)
   - `src/devices/spi/drivers/` (SPI drivers)
   - `src/devices/board/drivers/vim3/` or `nelson/` or `sherlock/` (real board drivers)
   - `vendor/*/src/devices/` if the board is vendor-specific — the vendor's own driver is often the
     closest match, since it targets the same SoC family
   - Search: `rg -l "PDev::Connect|pdev\.MapMmio|kFragmentName" "$FUCHSIA_ROOT/src/devices/"`
     (add `"$FUCHSIA_ROOT/vendor/"` to the search roots when a `vendor/` tree is present)

2. **Trace the implementation.** Start from the API the driver calls, follow it into the framework
   implementation, understand what paths/instance names are created.

3. **Cross-check with FIDL/CML.** Verify that the offer/use routing in CML files matches what the
   implementation creates. Check relevant `.shard.cml` files.

4. **Synthesize.** Don't just report code — answer: what is the correct pattern, and specifically what
   does our driver need to change?

---

## How to write the subagent prompt

Include all of these:

1. **The specific question** — quote the exact error message or the specific API call in question.
2. **What we already know / have tried** — files already read, hypotheses already ruled out, so the
   agent doesn't waste turns re-reading them.
3. **Our driver's current code** — paste the relevant 5–15 lines so the agent can compare against
   working examples without asking.
4. **The resolved paths** — substitute the actual `$FUCHSIA_ROOT` and `$WORKTREE` values, and state which
   tree holds our driver vs. the examples.
5. **The concrete deliverable** — e.g. "tell me the correct instance name to pass to `PDev::Connect`" or
   "tell me what CML changes are needed" — not "investigate how routing works." Ask for source locations
   as `path:line`.
6. **A research-only instruction** — append the literal line `Do research only; do not write or edit any
   code or files.` to the end of the prompt.

### Worked example of a subagent prompt

```
Our serial driver fails at runtime with ZX_ERR_PEER_CLOSED the first time it calls
PDev::MapMmio(0). It compiles and binds fine; the channel just closes on first use.

Already ruled out: the MMIO index is correct (matches the board driver's resource
order), and the bind rules match (the driver does bind and start). I've read our
driver's Start() and its meta/*.cml but not the platform-bus implementation.

Trees:
  FUCHSIA_ROOT = /home/me/src/fuchsia            (SDK, framework, examples)
  WORKTREE     = /home/me/src/fuchsia/.claude/worktrees/rpi5-port   (OUR driver + CML)
Read our driver and our CML from WORKTREE; read framework/SDK and any example driver
from FUCHSIA_ROOT. Verify each path exists before reasoning about it.

Our driver's connect/map code (WORKTREE/src/devices/serial/drivers/rpi5-uart/uart.cc):

  zx::result pdev = incoming()->Connect<fuchsia_hardware_platform_device::Service::Device>();
  if (pdev.is_error()) return pdev.take_error();
  fdf::PDev dev{std::move(pdev.value())};
  zx::result mmio = dev.MapMmio(0);     // <-- ZX_ERR_PEER_CLOSED here

Question: why does the channel close, and what is the DFv2-correct connect/map
sequence for a non-composite PDev driver? Find an in-tree non-composite driver under
FUCHSIA_ROOT/src/devices/ that uses fdf::PDev + MapMmio successfully and compare.

Deliverable:
  1. The root cause, in one or two sentences.
  2. The exact corrected code for our connect/map sequence.
  3. Any CML or bind changes our driver needs, with the exact text to add.
  4. The 2-3 most authoritative source locations as path:line.

Do research only; do not write or edit any code or files.
```

---

## Launching the subagent

Use `Agent` with:
- `subagent_type: "general-purpose"`
- **Omit the `model` parameter** so the subagent inherits the session's model (the default). This
  task demands multi-file reasoning across a large tree — it needs the session's full-capability
  model; do not pass a cheaper/faster tier.

```
Agent({
  description: "Fuchsia source investigation: <one-line question>",
  subagent_type: "general-purpose",
  prompt: "<full investigation prompt per guidelines above, with resolved paths substituted>"
})
```

---

## What to do with the result

Synthesize the agent's findings into:
1. **The answer** — what the correct pattern is.
2. **The fix** — specifically what changes to make in our driver.
3. **Key sources** — the 2-3 most authoritative files as `path:line`.

Before acting on the result, **spot-check the cited locations** (open one or two and confirm they say
what the agent claims). A subagent working over a tree this large occasionally cites
plausible-but-wrong files; a quick verify is cheap insurance.

Do not relay the agent's raw output verbatim — distill it.
