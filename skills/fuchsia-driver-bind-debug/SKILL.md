---
name: fuchsia-driver-bind-debug
description: >-
  Diagnose why a Fuchsia DFv2 driver did not bind — a node sits unbound, no driver was selected,
  or the wrong driver matched. An ordered ladder of techniques (ffx driver doctor and list-devices
  --unbound, log severity, the offline bind debugger, zxdb on the match path) with the realistic
  limits of each. Use for "my driver isn't loading / didn't bind"; for a driver that bound but
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

## The technique ladder — cheapest first, stop when you have the answer

### Rung 0 — `ffx driver doctor` (purpose-built)
`ffx driver doctor` is documented as *"Diagnose driver binding issues."* Give it the node moniker and
the driver URL; it reports why that driver did not match that node. Start here.

```
ffx driver doctor --node <node-moniker> <driver-url-or-substring>
```

### Rung 1 — compare node properties against your bind rules
This is the single highest-value step. List the unbound nodes and their real properties, then read
them against the driver's bind rules. ~90% of match failures are a property the parent never stamped,
or a value that differs from what the bind rule assumed.

```
ffx driver list-devices --unbound      # only the nodes that failed to bind (-u)
ffx driver list-devices -v             # all nodes WITH their bind properties (-v / --verbose)
ffx driver dump                        # full node topology for context
```

Read the node's property bag and check **every** bind rule against it — a bind program matches only if
*all* conditions are true. A single missing/renamed property key (very common when a parent driver
stamps children) means no match, silently.

### Rung 2 — bump log severity (longitudinal, free)
`driver_manager` and `driver_index` log match decisions at higher verbosity. Raising their severity
turns every match attempt into a log line — useful when the failure is timing/ordering dependent and a
one-shot snapshot misses it. Raise severity on `driver_manager` and `driver_index`, reproduce, read
the log. Prefer this over `fx trace` (see "Why not fx trace" below).

### Rung 3 — offline bind debugger
When properties look right but it still won't match, run the driver's **bind program against an exact
set of properties offline** (the `bindc` test/debug path). This tells you *which bind instruction*
failed without a device at all — ideal for deterministic property sets.

### Rung 4 — zxdb on the live match path
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

## Why not `fx trace` (yet)

`fx trace` is the natural longitudinal tracer, **but the bind path is not instrumented**: a grep of
`src/devices/bin/driver_manager` for `TRACE_DURATION` / `trace::Start` / `TRACE_INSTANT` returns
nothing (verified). So `fx trace` captures no bind events out of the box — you would have to add trace
points first (a legitimate upstream-style change, not a hack, but not free). Until then, **log
severity (rung 2) is the longitudinal signal**, not tracing.

## Quick reference — verified `ffx driver` subcommands

`doctor`, `list-devices` (`-v`, `--unbound`/`-u`), `dump`, `restart`, `disable`/`enable`, `test-node`,
`node`, `show`, `list-hosts`, `list-composites`, `list-composite-node-specs`. (Verified present in
`src/devices/bin/driver_tools/src/subcommands`.)

## When to hand off
- Need to read the framework internals to understand a match (what property a parent *should* stamp,
  how a composite spec resolves) → **`fuchsia-source`**.
- Board-specific node topology / which RP1 leaf advertises what → **`rpi-expert`** (in the `public-skills` repo).
- The driver matched and `start()` is what fails → **`fuchsia-source`** (this skill is match-only).
