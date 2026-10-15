---
name: fuchsia-hardware-bench
description: >-
  Drive and provision Fuchsia on real hardware over a fully remote bench (x64 NUC/gigaboot, arm64
  VIM3/Nova/RPi5): HDMI capture, serial-HID keyboard, network-controlled power, gigaboot
  fastboot-over-TCP, and IPv6 link-local ffx. Use when bringing up or reprovisioning a DUT with no
  hands on the box, or when a flash/ffx/fastboot step fails with "bad IPv6 address", "Failed to
  get block device", "No matching block device found", a Secure Boot Violation, or an unreachable
  link-local target.
---
<!--
SPDX-FileCopyrightText: 2026 Curtis Galloway
SPDX-License-Identifier: Apache-2.0
-->

# Fuchsia hardware bench: remote bring-up & provisioning

How to control and provision a Fuchsia device with **zero physical access** after the initial
wiring. The hard-won facts here were paid for in full sessions of dead ends — read the gotchas
before improvising.

Gigaboot and ffx facts below were **verified against fuchsia 5ded3fc08ae, 2026-10-15**; upstream
moves, so re-check anything load-bearing. Related skills:
- Source questions: use the `fuchsia-source` skill; never guess gigaboot/paver behavior from memory.
- Turning a boot into a pass/fail verdict over serial: `fuchsia-boot-test-ci`.
- Reading live registers of a node or bound driver once the board is up: Fuchsia's in-tree
  `driver-lab` skill (`//src/devices/skills/driver-lab`, the `lab_proxy` / `driver_lab_rust`
  probing tool).

---

## 1. Bench control primitives (paniolo + a USB KVM)

A remote bench needs three channels: **screen**, **keyboard**, **power** (plus **netboot** for
network boot). Drive them through [`paniolo`](https://github.com/curtisgalloway/paniolo), with each
target's channels described in your lab config:

- **Screen** — `paniolo video watch <t>` starts the HDMI-capture daemon; `paniolo video shot <t>`
  grabs a PNG, `paniolo video read <t>` OCRs it. Read the PNG directly (you can see it); OCR is a
  fallback. **A shot can be arbitrarily old, not just one frame behind**: with no HDMI signal (a
  kernel panic that stops the framebuffer, a mode switch, power loss, an OS with no gfxconsole) the
  capture device holds its last frame indefinitely, and two shots in a row are equally stale. Use
  `paniolo video shot <t> --changed-since <hash>` (wait for a frame that differs from one you
  already have) or `--stable` (wait for the picture to settle) instead of trusting a bare shot.
  `paniolo doctor` checks configuration against hardware, **not** whether the video daemon is
  running — check with `paniolo video show <t>`.
- **Keyboard** — a USB KVM such as the Openterface Mini-KVM exposes a **CH9329** HID chip as a
  serial port. paniolo drives it through its `ch9329` helper (it also has `hidrig` and `jetkvm`
  helpers for other injectors). For anything timing-sensitive, put the keystrokes in a **command
  file** and send it in one invocation — `paniolo hid send -t <t> run <file>`, one `key <NAME>` or
  `delay <ms>` per line (key names are adafruit_hid `Keycode` names; see
  `paniolo helper ch9329 -- run --help`). A shell loop of one-shot key commands costs roughly a
  second per key and misses short windows. **Check the run's output**: the CH9329 link can abort
  mid-file (seen while the target re-enumerated USB after a power cycle), and then an unknown
  number of keys were delivered.
- **Power** — a network outlet. Two shapes seen: a **Shelly** plug (`paniolo helper shellyplug
  {cycle,on,off,state} -d <plug>`) with full on/off/state, or a **Home Assistant** script called by
  a `curl` POST that only *cycles*. Wire it to the target's `power` channel
  (`cycle_cmd`/`on_cmd`/`off_cmd`/`state_cmd`) and use `paniolo power-cycle <t>`.
- **Netboot** — `paniolo netboot start/stop/logs/status <t>` runs `netbootd` (DHCP+TFTP+HTTP) on a
  USB-Ethernet dongle cabled point-to-point to the DUT. `logs` shows DHCPDISCOVER/OFFER/ACK and TFTP
  RRQ/complete — the authoritative proof of what the DUT actually pulled. One target at a time per
  dongle: stop the other target's netboot first.

**Trust telemetry over the screen.** The plug's power draw (`shellyplug state` / Shelly RPC `apower`)
tells you if the box is actually running (0 W = soft-off) when the capture is frozen or blank.
**Confirm the outlet is really the DUT's** — a plug that reads 0 W while the machine is demonstrably
running is wired to something else (this exact mislabel cost hours). `paniolo power off` then
querying the relay state (`output:false`) proves the relay opens.

---

## 2. x64 BIOS setup (Intel NUC "Visual BIOS" / AMI Aptio)

Getting in and navigating over a KVM keyboard has two quirks that look like broken input:

- **Keys need a long hold.** The firmware polls USB slowly; a 20 ms tap is dropped. Hold each key
  ~120 ms. To *enter* setup, hammer F2 across the reboot from a command file (e.g. 100 ×
  `key F2` / `delay 300`, ~30 s) and start it **before** the power event.
- **Switching top-level tabs needs the menu bar focused first.** Inside a page, Left/Right does
  nothing. Press **Up** at the top of the item list to raise focus to the tab bar, Left/Right to the
  tab, then **Enter** (not Down — Down drops back into the current page) to activate it. Dropdowns:
  Enter to open, Up/Down to choose, Enter to commit.

Settings that must be right for Fuchsia x64 (all verified on an NUC11TNK, BIOS `TNTGL357`):

| Setting | Where | Value | Why |
| --- | --- | --- | --- |
| **Secure Boot** | Boot → Secure Boot → Secure Boot | **Disabled** | gigaboot is unsigned; ON → red "Secure Boot Violation, Invalid signature detected" and it falls through to the next boot device. |
| **After Power Failure** | Power → Secondary Power Settings | **Power On** | So the network outlet is a real power actuator (plug cycle → the box boots itself). Verify by actually cutting & restoring AC. |
| **Enable VMD controller** | Advanced → STORAGE | **Disabled** | VMD (Intel RST/RAID/Optane) hides the NVMe behind a driver Windows has but gigaboot/Fuchsia don't. OFF = plain NVMe. (Windows won't boot after this — fine if you're wiping it.) |
| UEFI network boot first | Boot | enabled, first | PXE the bootloader. |

`shutdown /r /fw` (reboot-to-firmware) from Windows needs admin (error 1314); a plain `shutdown /r`
+ F2 hammer is the no-admin path.

---

## 3. Talking to gigaboot fastboot-over-TCP (x64)

The x64 boot-test/product bundles carry **gigaboot** in their ESP
(`partitions/bootloader_partitions/0/image/fuchsia.esp.blk` → `EFI/BOOT/BOOTX64.EFI`, ~180 KB).
PXE-serve that file (`paniolo netboot ... boot_file=BOOTX64.EFI`). Entry into fastboot:

1. **`boot_mode=fastboot` in the EFI LoadOptions** (zero-touch; needs an iPXE build with the arg
   embedded — plain PXE can't set LoadOptions), or
2. **ABR one-shot** (`fastboot reboot-bootloader`), or
3. **press `f` in the 2-second countdown** (`Press f to enter fastboot.`) — the default. You cannot
   poll for the window and react in time; send the burst from one command file across the boot.
   On a firmware boot menu, put ENTER and the `f` burst in the **same** file (`f` also moves a
   menu selection, so a burst sent before ENTER changes what you boot). Success line:
   **`Fastboot TCP is ready`**.

**Check serial before reaching for video/OCR.** Some firmware redirects the EFI console to the
serial port, in which case the whole gigaboot session — `Gigaboot main`, `Secure Boot: Off`,
`Press f to enter fastboot.`, `Fastboot TCP is ready` — arrives as text on the same channel that
later carries zircon, and no HDMI capture is needed for the bootloader phase at all. Confirmed on
the Dell OptiPlex 7060 (2026-08-19); the Intel NUC11 does **not** do this. Cheap to check: capture
serial across one PXE boot and look for `Gigaboot main`.

**gigaboot's fastboot verbs** (`src/firmware/gigaboot/cpp/fastboot.cc` command table): `getvar`,
`flash`, **`boot`**, `continue`, `reboot`, `reboot-bootloader`, `reboot-recovery`, `set_active`,
`oem gpt-init`, `oem add-staged-bootloader-file`, `oem efi-*`.

**RAM boot with `boot` is the better test loop.** The `boot` verb landed upstream on 2026-08-19
(`[gigaboot] Add the fastboot boot verb`); a gigaboot built from an older tree lacks it, and then
the loop is **flash slot A → continue**. On success gigaboot answers `OKAY` *before* jumping, then
prints `fastboot: booting downloaded ZBI (<n> bytes) from RAM`. Measured on a Dell OptiPlex 7060: a
74 MB `bringup_with_tests.x64` went over the wire in 4.7 s and produced the same
`Ran 90 tests: 0 failed` as a disk-provisioned boot, **writing no partition**. Two gotchas:

- **No shipping host tool issues the verb.** `ffx target fastboot` has no `boot` subcommand
  (`src/developer/ffx/plugins/target/fastboot/src/args.rs`), and AOSP `fastboot` must not be used
  here at all (below). You need a small fastboot-over-TCP client: the protocol is a `FB01`
  handshake, then 8-byte big-endian length-prefixed packets carrying `download:<hex size>`, the
  data, then `boot`. A stdlib Python client is a page of code.
- **Chunk the download.** gigaboot puts a 30 s timer around each read, while the download phase
  accumulates across packets and stays silent until the last one. One giant packet works on a 4 MB
  test image and times out on a 74 MB one; send ~1 MB chunks.
- **`boot -> OKAY` means accepted, not booted.** Confirm on serial.

### Reaching it — the address dance (this is where hours go)

gigaboot fastboot binds an **IPv6 link-local only**, port **5554**, and advertises `_fastboot._tcp`
via mDNS. To connect:

1. **Host needs an IPv6 link-local on the dongle.** netbootd/link setup often leaves IPv6 off:
   ```sh
   sudo sysctl -w net.ipv6.conf.<iface>.disable_ipv6=0
   sudo ip -6 addr add fe80::1/64 dev <iface> nodad   # ephemeral — re-add after any host reboot
   ```
   Without this, ping6/fastboot to the DUT's `fe80::` silently fail and the neighbor table is empty.
2. **Find the DUT address.** It's the EUI-64 link-local from the NIC MAC (flip bit 0x02 of byte 0,
   insert `ff:fe`), or just read it off the mDNS advert:
   ```sh
   sudo tcpdump -i <iface> -n -e udp port 5353     # shows fe80::… .5353 > ff02::fb PTR …_fastboot._tcp
   ```
   Verify: `ping -6 -c2 'fe80::<dev>%<iface>'` should answer in ~10 ms.
3. **Use a NUMERIC interface scope, and use `ffx` — not AOSP `fastboot`.**
   - AOSP `fastboot -s tcp:[fe80::…%<ifname>]` → `bad IPv6 address` (rejects the name scope); with a
     numeric scope the parser passes but the TCP connection **hangs**, and a hung connection
     **wedges gigaboot's single-connection server** (subsequent probes time out — power-cycle to
     recover). Don't use AOSP fastboot for this.
   - `ffx` works. Point it at `[<addr>%<ifindex>]:5554` (numeric scope; `cat /sys/class/net/<iface>/ifindex`)
     and register the same string in `~/.fastboot/devices` (ffx discovers TCP-fastboot targets only
     via that file — the `fastboot.devices_file` config default; Rust's `SocketAddr` rejects
     interface names, hence numeric):
     ```sh
     IDX=$(cat /sys/class/net/<iface>/ifindex)
     printf 'tcp:[%s%%%s]:5554\n' "$DEV_LL" "$IDX" > ~/.fastboot/devices
     T="[$DEV_LL%$IDX]:5554"
     (cd <fuchsia-checkout> && scripts/fx ffx -t "$T" target fastboot getvar all)   # or: oem, flash, reboot, continue
     ```
   - Use the **real ffx from a Fuchsia checkout** (`.jiri_root/bin/ffx` or `scripts/fx ffx`),
     never an agent-downloaded binary (the harness classifier blocks running those).
   - `getvar all` may error on `slot last set active` when the disk has no ABR metadata yet — that's
     cosmetic; individual getvars (`max-download-size`, `current-slot`) still return.

---

## 4. Which disk a PXE-booted gigaboot will flash

If `oem gpt-init` / `flash` fails with **`Failed to get block device(-1)`** and gigaboot printed
**`No matching block device found`** at boot while the BIOS clearly lists the NVMe and VMD is off —
it is **not** VMD, not a cable, not the disk. It is how gigaboot picks its target disk
(`FindEfiGptDevice()`, `src/firmware/gigaboot/cpp/gpt.cc`):

1. The block device whose EFI device path is a prefix of the running image's — **the disk gigaboot
   itself booted from**. A PXE-booted gigaboot's boot device is the NIC, so this finds nothing.
2. **Fallback (upstream since 2026-08-19, `[gigaboot] Find target disk by partitions, not boot
   provenance`):** if exactly **one** disk already carries a **Fuchsia GPT**, use it, printing
   `Image did not boot from a block device; using the disk with a Fuchsia GPT`. With more than one
   it refuses (`Refusing to guess: <n> disks carry a Fuchsia GPT`).
3. Otherwise: `No matching block device found`.

So with a current gigaboot, **PXE gigaboot → flash** works to **reprovision** a machine whose disk
already has a Fuchsia GPT. A gigaboot built before the fallback cannot do even that. What remains
blocked either way is a **first-time install onto a bare disk** (no Fuchsia GPT — e.g. a disk still
holding Windows): gigaboot finds no candidate, prints `No matching block device found`, and refuses
to touch it. That line is also printed — harmlessly — before `Fastboot TCP is ready` on every RAM-boot
cycle of such a machine. First-time provisioning of a bare x64 disk needs one of:

- **A USB installer** whose ESP has gigaboot + the install images (`fx mkinstaller`, under
  `tools/devshell/contrib/`); boot it and it writes the internal disk. Needs physical USB **or** a
  KVM with **virtual-media/USB-mass-storage** emulation. A plain HDMI + CH9329-HID KVM **cannot**
  do this remotely.
- **A netboot → running-Fuchsia → userspace paver** flow: boot an eng/recovery image into RAM whose
  base carries `fastboot-tcp` + `paver` (or netsvc's paver / zedboot), then flash the disk from the
  *running OS* — the transport limitation is gone because the paver runs on the device. This is
  the flow used on arm64 boards with no local fastboot transport; on x64 it needs a
  fastboot-tcp/paver-enabled image.

Decide the provisioning method up front — don't discover this at `gpt-init` time. Note that a failed
`gpt-init` writes nothing, so a not-yet-wiped OS on the disk survives the attempt.

### Disk-free RAM netboot via GRUB — DOES NOT WORK on a UEFI-only x64 box

With gigaboot's `boot` verb (§3) this path is no longer needed on x64; it is kept because it looks
plausible and fails silently. GRUB fetches the legacy x86 boot shim and ZBI over HTTP correctly,
prints its `boot`, and then the *UEFI firmware* reports
`!!!! X64 Exception Type - 06(#UD - Invalid Opcode) !!!!` with `RIP = 0x1000` — **zircon never
executes**. (It was first misreported as working, because there was no serial console to see past
GRUB's handoff.)

Cause, from the shim's own bzImage setup header: `handover_offset = 0`, neither
`XLF_EFI_HANDOVER_32` nor `XLF_EFI_HANDOVER_64` set, and `code32_start = 0` with
`kernel_alignment = 0x1000`. GRUB's EFI `linux` loader has no handover entry to call, and the zero
entry aligned up to 0x1000 is exactly the faulting `RIP`. This is the **legacy BIOS** shim
(`zircon/kernel/arch/x86/phys/boot-shim/legacy-boot-shim.cc`); modern x64 boxes have no CSM. It is
**not fixable by patching the header** — there is no EFI entry code to point the offset at.

Check any shim before trusting this path:
```python
import struct; d = open('linux-x86-boot-shim.bin','rb').read()
print('xloadflags 0x%04x' % struct.unpack_from('<H', d, 0x236)[0])   # bit3 = EFI_HANDOVER_64
print('handover_offset 0x%08x' % struct.unpack_from('<I', d, 0x264)[0])  # 0 = unusable from EFI
```

The GRUB mechanics are still correct for **arm64** boards, and for a Linux image on x64 (an
ordinary distro `vmlinuz` *does* boot this way — only the Fuchsia shim fails):

- Build a standalone GRUB EFI and serve it as the PXE boot file:
  `grub-mkstandalone -O x86_64-efi --modules="http efinet net linux normal echo gfxterm all_video
  configfile part_gpt" -o grubx64.efi boot/grub/grub.cfg=<cfg>`.
- **Fetch over HTTP, not TFTP** — GRUB's built-in TFTP stalls on multi-block transfers; netbootd
  serves the same root on :80.
- **GRUB, not iPXE** — EFI iPXE's `kernel` rejects a legacy bzImage with `Exec format error`.

### Serial is required to observe x64 boot tests

The x64 boot-test bundles set `kernel.serial=legacy` (console = legacy PC **COM1, I/O 0x3F8,
115200 8N1**), and the minimal images bring up **no network**, so there is **no software-only way**
to read pass/fail — this matches upstream, where botanist captures x64 boot tests over **serial**.
Serial is also the only thing that tells you whether a boot path works at all (see the GRUB
correction above). On the NUC11 the port is an internal 1×9 1.25 mm PicoBlade **RS-232** header
(RX/TX/GND = pins 2/3/5; RS-232 level, not TTL; PCB pin-1 silkscreen is wrong — trust the TPS);
capture at 115200 8N1. Expect a `uart16550` bind failure (`'bind' failed: ZX_ERR_INTERNAL`) on such
a boot: the kernel owns COM1 as its debug port, so the driver cannot claim it.

---

## 5. `ffx` to a *running* Fuchsia over the point-to-point link

Once Fuchsia is booted (netstack up), `ffx` connects over SSH→RCS on the same link-local link:

```sh
sudo ip -6 addr add fe80::1/64 dev <iface> nodad          # same host addr; ephemeral
cd <fuchsia-checkout> && scripts/fx ffx target list       # expect RCS:Y over fe80::…%<iface>
scripts/fx ffx target add 'fe80::<dev>%<iface>'            # if mDNS didn't auto-discover
```

The target's own link-local appears in its serial log (`adding addr fe80:... Slaac`). Watch items:
the `fe80::1/64` host address is root-only and **lost on host reboot** (re-add it — it surfaces in
*no* error, RCS just sits at `N`); a stale `~/.fastboot/devices` entry flips the target into
Fastboot state and blocks RCS (remove it + `ffx daemon stop`); stale `ffx target add` records pin
dead addresses (`ffx target remove` + daemon restart).

---

## 6. Reusable snippets

MAC → EUI-64 IPv6 link-local (example MAC is a made-up, locally administered address):
```python
mac=[0x02,0x00,0x00,0x00,0x00,0x01]; b=mac[:]; b[0]^=0x02
print('fe80::%02x%02x:%02x%02x:%02x%02x:%02x%02x'%(b[0],b[1],b[2],0xFF,0xFE,b[3],b[4],b[5]))
```

Probe gigaboot fastboot readiness (expect banner `b'FB01'`):
```python
import socket
ai=socket.getaddrinfo('fe80::<dev>%<ifindex>',5554,socket.AF_INET6,socket.SOCK_STREAM)[0]
s=socket.socket(ai[0],ai[1]); s.settimeout(5); s.connect(ai[4]); print(s.recv(16))
```

Recover a pre-installed Windows OEM key before wiping (firmware MSDM survives the wipe anyway):
`(Get-CimInstance SoftwareLicensingService).OA3xOriginalProductKey` (Windows), or
`sudo tail -c 29 /sys/firmware/acpi/tables/MSDM` from any Linux. Decode from character codepoints,
not OCR, so it's exact.
