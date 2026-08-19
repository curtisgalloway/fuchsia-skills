---
name: fuchsia-boot-test-ci
description: >-
  Turn a Fuchsia boot on real hardware into a trustworthy machine-readable pass/fail verdict. Use
  when building or debugging a CI runner that boots a board and reads test results over serial:
  choosing a test image, driving runtests over the console shell, parsing the SUMMARY and runtests
  markers, and diagnosing INCOMPLETE, false-FAIL, or stale-verdict runs. Complements
  fuchsia-hardware-bench (the physical bench); this skill covers what happens after the board
  boots.
---
<!--
SPDX-FileCopyrightText: 2026 Curtis Galloway
SPDX-License-Identifier: Apache-2.0
-->

# Fuchsia boot-test CI: capturing a verdict you can trust

Booting the board is the easy half. The hard half is knowing that the PASS you printed reflects
*this* boot, of *this* image, and that a FAIL means the software is broken rather than the harness.
Every rule below was paid for with a voided run.

Companion skills:
- **`fuchsia-hardware-bench`** — the physical bench: power, HDMI capture, serial-HID keyboard,
  netboot, BIOS, gigaboot/fastboot. Read that first if the board isn't booting yet.
- **`fuchsia-source`** — for any question about what Fuchsia code actually does. Don't guess.

---

## 1. First: which image family did you boot?

The single most expensive mistake is assuming a booted test image runs tests. **Two families
behave oppositely.**

| Family | Examples | Behavior | How you capture |
| --- | --- | --- | --- |
| **Standalone ZBI boot test** | `boot-libc-unittests.eng`, `core-tests.eng`, `kernel-unittests-boot-test` | Autoruns on boot, prints a fixed success marker, powers off | Passive: watch for the marker |
| **`bringup_with_tests.<board>`** | the `runtests` image | **Runs nothing.** Boots to a console shell and idles indefinitely | Active: type `runtests` at the shell |

`bringup_with_tests` has **no `zircon.autorun.boot`** in its ZBI cmdline. Upstream infra drives it
remotely over netsvc; if you just boot it and wait, a completely healthy board looks exactly like a
hang. Verify what any image will do *before* wiring a runner around it:

```sh
# from a Fuchsia checkout: out/<build>/host_x64/zbi
zbi -tv <fuchsia.zbi> | grep -A 14 CMDLINE
```

Look for `zircon.autorun.boot`. If it's absent but `console.shell=true` is present, you must drive
the shell. A useful corollary: that same dump tells you the console (`kernel.serial=...`), which is
what you must capture on.

List the test binaries the image actually carries, so you can run a fast subset before committing
to a multi-hour sweep:

```sh
zbi -tv <fuchsia.zbi> | grep -oE 'test/[A-Za-z0-9_.-]+' | sort -u
```

## 2. Driving `runtests` over the console shell

With `console.shell=true`, the serial console is a shell. The sequence:

1. Wait for **`Bootup completed.`** in the log.
2. Poke for the prompt — send an empty line, wait for `$`, retry a few times. The prompt may
   already have scrolled past before you started watching.
3. Send `runtests -i <per-test-secs> --all`, or explicit paths for a subset:
   `runtests -i 300 /boot/test/<name> /boot/test/<name>`.
   Bare `runtests` discovers nothing on these images — `--all` or explicit `/boot/test/` paths.
4. Wait for completion (see the next section — this is subtler than it looks).

**Always pass `-i <secs>`.** Without a per-test timeout a single hung test wedges the console and
takes the whole suite with it, and you learn nothing. With it, the hang is reported as one failed
test. (A `c11-condvar` test is a known offender.)

## 3. Completion is `SUMMARY` **plus** the prompt — never `SUMMARY` alone

Individual test binaries emit **their own nested** `SUMMARY: Ran N tests: M failed` lines partway
through a suite. A runner that stops at the first match can report a *false FAIL* from a nested
line — observed with a runtests self-test printing `SUMMARY: Ran 2 tests: 1 failed`.

Wait until the trailing shell prompt returns, then let the **last** SUMMARY in the window win:

```python
def suite_finished(window: str) -> bool:
    if "SUMMARY: Ran " not in window:
        return False
    lines = [ln for ln in window.splitlines() if ln.strip()]
    return bool(lines) and bool(PROMPT_LINE_RE.search(lines[-1]))
```

The same trap has a passive-capture form: on standalone boot-test images the overall `SUMMARY` can
print **tens of seconds before** the authoritative success marker. Treating SUMMARY as
end-of-capture truncates the log just short of the marker and reports INCOMPLETE on a healthy run.
Only the success marker should end a capture immediately; anything else should start a settle
window and keep reading.

## 4. Parsing: precedence, and phrasings that bite

Signatures worth matching:

- per suite: `[runtests][PASSED] <path>` / `[runtests][FAILED] <path>`
- overall: `SUMMARY: Ran <N> tests: <M> failed (<sec> sec)`
- **also seen:** `SUMMARY: Ran 1 test case: 0 failed` — a regex of `Ran\s+\d+\s+tests?:` cannot span
  the ` case`, so a clean run silently parses as INCOMPLETE. Accept an optional ` case`/` cases`.
- standalone boot tests: a single fixed `***Boot-test-successful!-<base64>***` marker, emitted only
  if the test process exited zero. Authoritative on its own.

**Precedence:** the overall SUMMARY wins over per-suite marks. This matters more than it sounds,
because **`runtests --all` legitimately logs `[runtests][FAILED]`** from
`/boot/test/sys/runtests-utils-testdata/…` — fixtures deliberately built to fail so runtests can
prove it detects failure. A healthy full sweep therefore contains real FAILED lines and a
`The following tests failed:` header while the overall result is `0 failed`. Curate those paths out
(or keep an expected-fail list) before this gates anything; a runner that ranks per-suite marks
above SUMMARY will report a spurious FAIL forever.

Distinguish **INCOMPLETE** from FAIL: no evidence either way (boot hang, crash, truncated capture)
is not a test failure. Keep it a separate outcome so the harness can retry instead of filing a
regression.

## 5. Reading the log window without lying to yourself

Three failure modes here each produced a confidently wrong verdict:

**One NUL byte turns your log into a "binary file" and `grep` goes silent.** Serial captures pick
up stray NULs at connect — line noise, or an adapter opening mid-frame. GNU grep then treats the whole
file as binary and, with `-c`, prints **nothing at all**: not a count, not "Binary file matches".
It just exits 1, which reads exactly like "the marker isn't there" — so a passing run is scored
INCOMPLETE because of one byte. Observed 2026-08-19 on a 228,793-byte capture containing a single
NUL:

```sh
$ grep -c "physload" run.log ; echo "exit=$?"     # prints nothing
exit=1
$ grep -a -c "physload" run.log                   # -a = treat binary as text
47
```

`sed`, `head` and Python are unaffected, which makes it worse — the file looks fine when you
inspect it by eye. **Always `grep -a` on serial captures**, and in code read `bytes` and decode
with `errors="replace"` rather than trusting text mode.

The other two:

**Default tails.** A serial-log CLI may default to a short tail (~200 lines). Grepping it for the
success marker returns nothing on a run that *did* pass — the real log was ~12,000 lines with the
marker in the middle. Always request an explicit large line count when searching.

**Rolling logs defeat prefix diffs.** The obvious way to isolate "output since the power-cycle" is
to save the log text and diff:

```python
captured = full[len(baseline):] if full.startswith(baseline) else full   # DON'T
```

The log rolls. Once old lines are dropped, `startswith` is false and the fallback hands back the
**entire** log — so the *previous* boot's SUMMARY or success marker is read as this run's result.
Anchor on a monotonic sequence number instead:

```python
# grab the last seq before the power-cycle, then read only what is newer
last = json.loads(tail_one_line_as_json())["seq"]
window = read_log(since=last, max_lines=200000)
```

Any log source with per-line sequence numbers or timestamps supports this; if yours doesn't, write a
unique sentinel to the console before the run and split on it.

## 6. Verify bench preconditions — exit codes are not enough

Three separate runs were silently voided by preconditions that all *looked* fine. Before trusting a
result, assert the environment, not the return code:

- **A netboot daemon can report success and then die.** `netboot start` returned rc=0 and printed
  "started" while the daemon immediately failed with `bind HTTP port 80: address already in use`
  (another target's daemon held it). Nothing served; the board fell through to disk and re-ran a
  stale image, producing a real-looking PASS for the wrong thing.
- **Config changes don't rebind a live daemon.** After repointing the netboot interface, `start`
  returned rc=**1** "already running" — and the running daemon was still bound to the *old*,
  now-absent interface. **Always stop, then start.**
- **Assert the interface exists and has carrier**
  (`/sys/class/net/<iface>/carrier` == `1`). A missing link is invisible in every log; the only
  symptom is the board skipping netboot suspiciously fast.

Useful tell: a board that skips PXE *instantly* has no link, while one that retries for minutes is
talking to a network where nothing answers. The difference in time-to-fallback is diagnostic.

## 7. Sanity rules

- **Never fabricate a verdict.** If the serial channel isn't configured, exit with a distinct code
  and print the setup command. A runner that guesses is worse than no runner.
- **Verify a suspicious PASS against the raw log** before believing it. `marks +0/-0` with a PASS is
  legitimate for a marker-based boot test (the count fields are unset on that path) but is exactly
  what a parser default would also look like — read the code path or grep the log.
- **Reproduce before calling it a baseline.** Two cold boots with identical counts *and* near
  identical wall-clock is good evidence the harness measures the machine rather than racing it.
- Boot-test images reboot or power off after finishing, so **the reboot itself is not a pass/fail
  signal** — on failure they omit the marker and shut down just the same. Read the console.
