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
over the **serial console**: `tools/testing/testrunner/tester.go` (`FuchsiaSerialTester`) waits for
`Bootup completed`, then types one `runtests -i <timeout> <path>` per test (see section 2). If you
just boot it and wait, a completely healthy board looks exactly like a hang. Verify what any image
will do *before* wiring a runner around it:

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

Once the shell is up, `runtests --all -d` (dry run) prints the list the image would actually run,
which beats grepping the ZBI.

For passive boot tests, upstream's own watcher is `//tools/testing/seriallistener`: the
`boot_test()` template in `//build/testing/boot_tests/boot_test.gni` wraps it to wait for the
success marker on the serial log. Read it before writing your own.

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

Other `runtests` flags worth knowing (from its usage text in
`//zircon/system/ulib/runtests-utils/discover-and-run-tests.cc`):

- `-d` — dry run: print the test paths and exit.
- `-n <names>` / `--names` — filter the default globs by basename (comma-separated list).
- `-o <dir>` — also write a JSON `summary.json` (schema:
  `//zircon/system/ulib/runtests-utils/summary-schema.json`). This is only useful if you can read
  files back off the target. Over a serial-only link, the console text is all you get.

In-kernel unit tests are not run by `runtests`. Use **`k ut all`** at the shell. They print their
own `SUMMARY: Ran N test cases: M failed` (see section 4).

### The route that avoids parsing SUMMARY: one test per invocation

This is what upstream infra does. Send one `runtests -i <secs> /boot/test/<name>` per test. Wait
for that test's own `[runtests][PASSED] <path>` or `[runtests][FAILED] <path>` mark, then send the
next one. Start is signaled by `RUNNING TEST: <path>`. The signatures are in
`//tools/testing/runtests/output.go`, and `TestPassed` there matches the named mark. A nested
SUMMARY from inside a test can't be mistaken for the run's result, because nothing reads SUMMARY at
all. A missing mark for the test you just launched means INCOMPLETE, not FAIL. The cost is one
prompt round-trip per test, plus maintaining the test list yourself (`runtests --all -d` gives it
to you).

## 3. Completion is `SUMMARY` **plus** the prompt — never `SUMMARY` alone

Individual test binaries emit **their own nested** `SUMMARY: Ran N tests: M failed` lines partway
through a suite. A runner that stops at the first match can report a *false FAIL* from a nested
line — observed with a runtests self-test printing `SUMMARY: Ran 2 tests: 1 failed`.

Wait until the trailing shell prompt returns, then let the **last** SUMMARY in the window win:

```python
# An idle prompt is a line holding *only* the prompt.
PROMPT_LINE_RE = re.compile(r"(?m)^\$[ \t]*$")

def suite_finished(window: str) -> bool:
    if "SUMMARY: Ran " not in window:
        return False
    lines = [ln for ln in window.splitlines() if ln.strip()]
    return bool(lines) and bool(PROMPT_LINE_RE.search(lines[-1]))
```

**Anchor the prompt to a whole line.** Searching for a bare `$` anywhere was a real defect.
`runtests` streams each test's stdout to the console, so a dollar sign in test output (an
environment dump, a shell test, a `$(...)` in a log line) closes the window early. Whichever SUMMARY
came before it then becomes the verdict. runtests' own fixtures print a nested
`SUMMARY: Ran 2 tests: 1 failed` well before the real summary, so that truncation scores a false
FAIL. The mirror case is a nested zero-failure SUMMARY, then a `$`, then a real failure, which
scores a false PASS.

The same trap has a passive-capture form: on standalone boot-test images the overall `SUMMARY` can
print **tens of seconds before** the success marker. Treating SUMMARY as end-of-capture truncates
the log just short of the marker and reports INCOMPLETE on a healthy run. On a standalone boot-test
image, the success marker can end a capture, because the image shuts down after printing it. On
anything that keeps running afterward, such as a `k ut all` session, the marker is just another
claimant (section 4). Any other signature should start a settle window and keep reading.

## 4. Parsing: precedence, and phrasings that bite

Signatures worth matching:

- per suite: `[runtests][PASSED] <path>` / `[runtests][FAILED] <path>`
- overall: `SUMMARY: Ran <N> tests: <M> failed (<sec> sec)`
- **also seen:** `SUMMARY: Ran 1 test case: 0 failed` — a regex of `Ran\s+\d+\s+tests?:` cannot span
  the ` case`, so a clean run silently parses as INCOMPLETE. Accept an optional ` case`/` cases`.
  That form comes from the kernel's `ut` command (`//zircon/kernel/lib/unittest/console.rs`), which
  lists failures under **`The following test cases failed:`**. runtests uses
  `The following tests failed:`. Match both headers.
- standalone boot tests: a single fixed `***Boot-test-successful!-<base64>***` marker
  (`boot_test_success_string` in `//build/testing/boot_tests/boot_test.gni`). Userboot prints it
  only if the test process exited zero. It is **not** authoritative on its own (see below).

**Precedence: the last claimant printed wins.** Three kinds of line can each claim to be the
run's result, and one capture can contain all three: a SUMMARY line (from `runtests` or `k ut`),
the boot-test success marker, and a failure list or `[runtests][FAILED]` mark. Rank them by
**position**, not kind:

1. Marker printed after every SUMMARY and every failure signal → **PASS**.
2. Otherwise, if any SUMMARY exists, the **last** one decides: PASS iff `failed == 0` and `ran > 0`.
   A failure list printed right after it is that SUMMARY's own detail, not a contradiction.
3. No SUMMARY and no later marker, but a `[runtests][FAILED]` mark or failure-list header → **FAIL**.
4. Anything else → **INCOMPLETE**. That includes a log full of `[runtests][PASSED]` marks with no
   SUMMARY. Those marks are printed once per test binary as it finishes, so a run that panics at
   test 45 of 90 leaves about 44 PASSED marks and nothing else. Scoring that PASS is the worst
   error a parser can make. Keep the marks as diagnostics only.

Both orderings were real bugs:

- **False PASS (marker-wins).** A `k ut all` run on a console-enabled kernel executed the console
  self-test `command_boot_test_success_test`. That test drove the `boot-test-success` shell
  command, which printed the **real** success marker mid-run. `SUMMARY: Ran 149 test cases: 1
  failed` followed. Letting the marker win scored a failing run PASS. Upstream fixed the test
  on 2026-09-21 (fuchsia `7897c4d732d`; `//zircon/kernel/lib/console/tests/mod.rs` now prints
  `***Mock-boot-test-successful!***`). **Trees older than that still emit the real marker.**
  Position precedence handles both.
- **False FAIL (any-SUMMARY-wins).** A standalone boot test printed a per-binary SUMMARY with
  failures partway through, then exited zero and printed the marker last. The marker is later, so
  it decides.

The fixtures make this matter even more, because **`runtests --all` legitimately logs
`[runtests][FAILED]`** from
`/boot/test/sys/runtests-utils-testdata/…` — fixtures deliberately built to fail so runtests can
prove it detects failure. A healthy full sweep therefore contains real FAILED lines and a
`The following tests failed:` header while the overall result is `0 failed`. Curate those paths out
(or keep an expected-fail list) before this gates anything; a runner that ranks per-suite marks
above SUMMARY will report a spurious FAIL forever. Their nested FAILED lines come *before* the real
SUMMARY, so position precedence gets this right.

Paths, signatures and line formats in this skill were verified against fuchsia 5ded3fc08ae,
2026-10-15. Upstream moves, so expect them to drift.

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
