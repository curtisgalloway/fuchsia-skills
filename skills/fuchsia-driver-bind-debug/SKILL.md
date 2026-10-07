---
name: fuchsia-driver-bind-debug
description: >-
  Diagnose why a Fuchsia DFv2 driver did not bind — a node sits unbound, no driver was selected,
  or the wrong driver matched. An ordered ladder of techniques (ffx driver show for the on-device
  bind bytecode, ffx driver doctor, node properties, log severity, the offline bind debugger, zxdb
  on the match path) with the realistic limits of each — including which rungs are blind to
  composite drivers. Use for "my driver isn't loading / didn't bind"; for a driver that bound but
  failed in start(), or deep source questions, use fuchsia-source.
---

<!--
SPDX-FileCopyrightText: 2026 Curtis Galloway
SPDX-License-Identifier: Apache-2.0
-->

# Fuchsia driver bind debugging — why didn't my driver bind?

A coding agent is bringing up a DFv2 driver and it never starts. There is usually **no error** — an
unmatched node is a normal, expected outcome, so nothing is logged by default. This skill turns that
silence into a diagnosis.

Canonical docs to read alongside this skill:
- **Debug a driver when it fails to load** — https://fuchsia.dev/fuchsia-src/development/sdk/debug-driver-when-it-fails-to-load
- **Driver binding (concepts)** — https://fuchsia.dev/fuchsia-src/concepts/drivers/driver_binding

## First, locate the failure: match vs. start

Binding is two distinct events, and they need different tools. Decide which you have **before**
picking a technique:

| Symptom | Failure | Where it happens | This skill? |
|---|---|---|---|
| Node is **unbound**; no driver selected; nothing logged | **Match** failure (bind rules ≠ node properties) | `driver_index` evaluates bind bytecode against node properties — **no driver is running yet** | ✅ yes |
| Driver **was selected** but `start()` fails (ZX_ERR_*, missing capability, parent not ready) | **Start** failure | the driver's own process | ❌ use `fuchsia-source` |

The critical fact for match failures: **there is no driver process to inspect** — the decision is made
inside `driver_index` against the node's advertised properties. So you debug the *node's properties*
and the *bind rules*, not "the driver."

**Do not use `ffx driver list --loaded` to decide which one you have — it reports match, not start.**
`--loaded` collects `bound_driver_url` from every node and filters the driver list to that set
(`src/devices/bin/driver_tools/src/subcommands/list/mod.rs:32-59`), so a driver whose rules matched
and whose `Start` then failed is still listed as "loaded". Observed on an OptiPlex 7060: `uart16550`
appears in `--loaded` in the *same boot* that logged `'bind' failed: ZX_ERR_INTERNAL` and `The driver
for node UAR1-composite-spec failed to bind`. If your driver shows up in `--loaded`, you have a
**start** failure — cross-check the boot log for `Failed to start driver` and hand off to
`fuchsia-source`.

## Corrections — if you learned the earlier version of this ladder

Revised after a bind-triage run against `core.x64` on a Dell OptiPlex 7060 (four unbound drivers next
to plausible silicon; all four resolved). Three things the earlier ladder got wrong:

1. **`ffx driver show` was missing entirely.** It is now **rung 0**. It returns the driver's
   disassembled bind bytecode *from the target*, which answers both "is this driver even in the
   index?" and "was this node ever a candidate?" in one command.
2. **`ffx driver doctor --node` was rung 0. It is nearly useless for composite drivers**, which is
   nearly every modern driver. The modes that actually diagnose are `--driver` and
   `--composite-node-spec`. Separately, the syntax given earlier
   (`doctor --node <moniker> <driver-url>`) **does not parse** — `doctor` takes no positional
   argument.
3. **The offline bind debugger cannot read composite bind rules at all.** The earlier text implied it
   worked on any driver. See rung 4 for the limitation and the extraction workaround.

Old rungs 1–4 are now rungs 2–5. Nothing was removed.

## The technique ladder — cheapest first, stop when you have the answer

### Rung 0 — `ffx driver show <driver>`: is it in the index, and was this node ever in scope?

```
ffx driver show <driver-name-or-url-substring>     # positional; partial matches allowed
```

The output is the **disassembled bind bytecode as `driver_index` on the target holds it** — one block
per parent node, with primary and optional parents labelled, and (for a driver that did bind) the node
it matched. Accept lists render as `Jump if <key> == <value> to ??` … `Abort` … `Label ??`, with
values in **decimal**.

Two questions this answers that nothing else answers as cheaply:

- **"Is my driver in this image's index, and did its package resolve?"** The bytecode came *from the
  target*. If `show` prints it, `driver_index` holds the driver and has parsed its rules — including
  for `fuchsia-pkg://` drivers. That eliminates "not built into this product" without a rebuild, which
  is usually the first hypothesis and almost never the answer.
- **"Was this node ever a candidate?"** — see the check below.

> **The mis-pairing check — run this before you call anything a bind defect.**
> **If the driver's primary parent carries no `BIND_PCI_*` condition, a PCI node was never in scope
> for it.** The reverse holds too: a primary parent keyed on `fuchsia.acpi.HID` binds an ACPI device,
> not the PCI function that implements the same hardware block.

Pairing a driver to silicon by the *name of the function* is the trap, and it is expensive: on the
OptiPlex run it produced **two of the four apparent defects**. `intel-thermal` is an ACPI DPTF
participant driver (`Key(fuchsia.acpi.HID) == "INT3403"`), not a driver for Intel's PCH thermal *PCI*
function; `uart16550` is an ACPI/port-I/O driver (`HID == "PNP0501"`) that takes its registers and IRQ
from ACPI, not a driver for a PCI-attached 16550. Neither has a PCI condition anywhere in it, so
neither was ever a candidate for the PCI node it was sitting next to. One `ffx driver show` per
candidate — one command each — caught both before any investigation was spent on them.

`ffx driver show` is also the supported replacement for `ffx driver list -v`, whose own help text now
reads "*[Deprecated] Use `ffx driver show` instead*"
(`src/devices/bin/driver_tools/src/subcommands/list/args.rs`).

### Rung 1 — `ffx driver doctor` — the two modes that diagnose

`ffx driver doctor` is documented as *"Diagnose driver binding issues."* All three selectors are
**options, not positionals** (`src/devices/bin/driver_tools/src/subcommands/doctor/args.rs`):

```
ffx driver doctor --composite-node-spec <spec-name>    # every candidate driver vs. that spec, per-key
ffx driver doctor --driver <url-or-substring>          # that driver fuzzy-matched against all unbound nodes
ffx driver doctor --node <node-moniker>                # see the warning below
```

**`--node` is nearly useless for composites, and every PCI driver in the tree is a composite.** For a
composite candidate it prints only *"This is a composite driver. For a detailed analysis, run
`ffx driver doctor --driver …` or use `--composite-node-spec` …"* and names no mismatch. Measured on
the OptiPlex, for one PCI node: **30 candidate drivers listed, 25 of which produced only that
message.** The 5 that did name a mismatch were the non-composite drivers that were never plausible
anyway. **On x64/PCI, go straight to `--composite-node-spec`.**

`--composite-node-spec` is the mode that pays: it enumerates every candidate driver against that spec
and gives a per-key mismatch, e.g.

```
Potential match: driver fuchsia-boot:///intel-hda#meta/intel-hda.cm
Matching primary node...
  ERROR: Primary node matches no parent in spec. Mismatches against parent 0:
    Unconditional Abort reached in bind rules.
```

**What to pass it, on x64.** The x86 board driver publishes **one composite node spec per PCI
function, named for its BDF with underscores** — `00_1f_3` for `00:1f.3`. So on x64 the *spec*, not
the node, is the unit of triage. The spec's **parent count also tells you whether an ACPI companion
exists**: two parents (PCI plus ACPI, correlated by `fuchsia.BIND_PCI_TOPO`) means an
`optional parent "acpi"` clause has something to bind to; one parent means PCI only, so an ACPI-keyed
condition can never be satisfied there. Enumerate them with:

```
ffx driver composite list -v --only unbound    # -v adds state + bound driver URL
ffx driver composite show <spec-name>          # one spec, in detail
```

### Rung 2 — compare node properties against your bind rules
Still where most match failures resolve — now with rung 0's bytecode in hand, so you are reading two
halves of the same comparison rather than guessing at one. List the unbound nodes and their real
properties, then read them against the driver's rules. ~90% of match failures are a property the
parent never stamped, or a value that differs from what the bind rule assumed.

```
ffx driver list-devices --unbound      # only the nodes that failed to bind (-u)
ffx driver list-devices -v             # all nodes WITH their bind properties (-v / --verbose)
ffx driver dump                        # full node topology for context
```

Read the node's property bag and check **every** bind rule against it — a bind program matches only if
*all* conditions are true. A single missing/renamed property key (very common when a parent driver
stamps children) means no match, silently.

Note the radix mismatch: `list-devices -v` prints property values in hex (`Value 0x00a348`), while
`ffx driver show` prints bytecode operands in decimal (`== 41800`). Convert before concluding they
differ.

### Rung 3 — bump log severity (longitudinal, free)
`driver_manager` and `driver_index` log match decisions at higher verbosity. Raising their severity
turns every match attempt into a log line — useful when the failure is timing/ordering dependent and a
one-shot snapshot misses it. Raise severity on `driver_manager` and `driver_index`, reproduce, read
the log. Prefer this over `fx trace` (see "Why not fx trace" below).

Skip this rung when the answer is static — a wrong DID or an absent ACPI HID does not change between
boots, and the earlier rungs already name it.

### Rung 4 — offline bind debugger (**composite-blind** — read this before reaching for it)
When properties look right but it still won't match, run the driver's **bind program against an exact
set of properties offline**. This names *which bind instruction* failed with no device at all, and
lets you run counterfactuals.

```
bindc debug <rules>.bind -d <device>.dev -i <includes>.bind
```

**`bindc debug` cannot read composite bind rules.** Point it at a composite `.bind` file and it fails
at *parse* time with ``[E027]: Expected 'true' keyword.``, echoing the file back. The gap is in the
tool, not the library: `Command::Debug` calls `compiler::compile_bind` — the non-composite entry point
— even though `compile_bind_composite` sits right next to it (`tools/bindc/src/main.rs:209-216`;
`src/devices/lib/bind/src/compiler/compiler.rs:234,258`), and `offline_debugger::debug_from_str` takes
a single `BindRules` with no composite handling
(`src/devices/lib/bind/src/debugger/offline_debugger.rs:112-124`). Since essentially every modern
PCI/ACPI driver is a composite, **this rung does not work out of the box on the drivers you are most
likely to be debugging.**

**The workaround is hand-extraction, and it is worth the five minutes.** Copy the **primary** parent's
conditions out of the composite `.bind` into a standalone single-parent `.bind`, and write the node's
real properties (from rung 2) into a `.dev` file. Keep both in scratch space — **do not add them to
the tree**. Then `bindc debug` behaves normally and names the failing line:

```
Line 7: Accept statement failed.
	Actual value of `fuchsia.BIND_PCI_DID` was literal 0xa348.
Driver doesn't bind to device.
```

**Caveat on counterfactuals.** Editing the extracted program until it matches is a **match-side**
result only. It says nothing about whether the driver would `start()`, or work, against that silicon —
different PCH generation, different register layout, a DSP BAR the driver knows nothing about, a bus
with nothing attached. Report "adding this DID makes it *match*", never "adding this DID fixes it".

### Rung 5 — zxdb on the live match path
For the dynamic cases the offline tools can't see (composite parents arriving out of order, properties
computed at runtime, readiness races), attach `zxdb` (`ffx debug connect`) to the **`driver_index`**
process — that is where bind bytecode is evaluated; `driver_manager` only owns the topology.

**Know zxdb's real limits before you reach for it (verified against the zxdb source):**
- There is **no gdb-style `dprintf`** / per-breakpoint "print … continue" command list. Don't promise
  one.
- A breakpoint with **`stop = none`** does **not** print on hit — it is a *hit counter* ("Don't stop
  anything. Hit counts will still accumulate."). Use it to answer *"is this code path reached, and how
  often"*, not to dump values.
- **Conditional** and **one-shot** breakpoints exist — set a conditional breakpoint that stops only on
  your node, then inspect the property bag and `continue`.
- zxdb runs **command script files** at startup (`--script-file`) and has an **embedded mode**, so a
  bind-debug session can be scripted and repeated non-interactively.

**Two hard caveats for networked targets (e.g. RPi5 over RP1 Ethernet):**
- The debugger talks to the target over the network. **Never halt any process upstream of your debug
  channel** (netstack, the network driver) — you will saw off the branch you're sitting on.
- The first bind attempt happens during early boot, before the debugger can attach. Don't chase it.
  Bring the system up fully, arm the breakpoint, then **force a rebind** to re-trigger the match on a
  live, network-up system:
  ```
  ffx driver restart <driver-url>     # re-runs binding for that driver's nodes
  ```
  **On a remote target, check what a restart tears down first.** `ffx driver restart` on a bus driver
  takes its whole subtree with it — restarting `bus-pci` on an x64 box drops the NVMe and Ethernet
  drivers under it, and with them your `ffx` connection.

## Why not `fx trace` (yet)

`fx trace` is the natural longitudinal tracer, **but the bind path is not instrumented**: a grep of
`src/devices/bin/driver_manager` for `TRACE_DURATION` / `trace::Start` / `TRACE_INSTANT` returns
nothing (verified). So `fx trace` captures no bind events out of the box — you would have to add trace
points first (a legitimate upstream-style change, not a hack, but not free). Until then, **log
severity (rung 3) is the longitudinal signal**, not tracing.

## Quick reference — verified `ffx driver` subcommands

`show`, `doctor`, `list`, `list-devices` (`-v`, `--unbound`/`-u`), `composite list`/`composite show`,
`dump`, `restart`, `disable`/`enable`, `test-node`, `node`, `list-hosts`, `list-composites`,
`register`, `host`. (Verified present in `src/devices/bin/driver_tools/src/subcommands`.)

Two deprecations to know:
- **`ffx driver list-composite-node-specs` is deprecated** — it prints ``WARNING: This command is
  deprecated. Use `ffx driver composite list` or `ffx driver composite show` instead.``
  (`.../subcommands/list_composite_node_specs/mod.rs:22`). It still works today.
- **`ffx driver list -v` is deprecated** in favour of `ffx driver show`
  (`.../subcommands/list/args.rs`).

## When to hand off
- Need to read the framework internals to understand a match (what property a parent *should* stamp,
  how a composite spec resolves) → **`fuchsia-source`**.
- Board-specific node topology / which leaf device advertises what → **`board-expert`** (in the `driver-lab` repo), when the board has a spec; otherwise **`hardware-investigator`** (also `driver-lab`).
- The driver matched and `start()` is what fails → **`fuchsia-source`** (this skill is match-only).
