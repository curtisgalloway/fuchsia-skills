---
name: fuchsia-hardware-bench
description: >-
  Drive and provision Fuchsia on real hardware over a fully remote bench (x64 NUC/gigaboot, arm64
  VIM3/Nova/RPi5): HDMI capture, serial-HID keyboard, network-controlled power, gigaboot
  fastboot-over-TCP, and IPv6 link-local ffx. Use when bringing up or reprovisioning a DUT with no
  hands on the box, or when a flash/ffx/fastboot step fails with "bad IPv6 address", "Failed to
  get block device", a Secure Boot Violation, or an unreachable link-local target.
---
<!--
SPDX-FileCopyrightText: 2026 Curtis Galloway
SPDX-License-Identifier: Apache-2.0
-->

# Fuchsia hardware bench: remote bring-up & provisioning

How to control and provision a Fuchsia device with **zero physical access** after the initial
wiring. The hard-won facts here were paid for in full sessions of dead ends — read the gotchas
before improvising.

Companion material:
- Board specifics & step-by-step playbooks: `fuchsia-ci/docs/{nuc11,vim3}-bench-setup.md`.
- The arm64 userspace-fastboot/paver flow (RPi5): `rpi5-bringup/docs/ffx-via-sd-userspace-fastboot-plan.md`
  and `scripts/netflash.sh` — the reference for "flash a disk from a running Fuchsia".
- Source questions: use the `fuchsia-source` skill; never guess gigaboot/paver behavior from memory.

---

## 1. Bench control primitives (paniolo + Openterface)

A remote bench needs three channels: **screen**, **keyboard**, **power** (plus **netboot** for
network boot). Drive them through `paniolo` (lab config in `iac/paniolo/lab.toml`):

- **Screen** — `paniolo video watch <t>` starts the HDMI-capture daemon; `paniolo video shot <t>`
  grabs a PNG, `paniolo video read <t>` OCRs it. Read the PNG directly (you can see it); OCR is a
  fallback. The daemon can freeze on a static frame (e.g. a kernel panic that stops updating the
  framebuffer, or HDMI dropping during a mode switch / power loss) — a byte-identical screenshot
  size across captures is the tell. `paniolo video stop <t> && paniolo video watch <t>` unfreezes it.
- **Keyboard** — an Openterface Mini-KVM exposes a **CH9329** HID chip as a CH340 serial port.
  paniolo's `hid` speaks the hidrig protocol, *not* CH9329, so drive it with the standalone
  `fuchsia-ci/tools/openterface_key.py` (stdlib, termios): single keys, `--type '<string>'`
  (shifted symbols supported), `win`, `--repeat <secs>` (spam a key), `--count N`, `--hold <secs>`.
- **Power** — a network outlet. Two shapes seen: a **Shelly** plug (`paniolo helper shellyplug
  {cycle,on,off,state} -d <ip>`) with full on/off/state, or a **Home Assistant** script
  (`script.claude_power_cycle_device` via a `curl` POST) that only *cycles*. Wire it to the target's
  `power` channel (`cycle_cmd`/`on_cmd`/`off_cmd`/`state_cmd`) and use `paniolo power-cycle <t>`.
- **Netboot** — `paniolo netboot start/stop/logs/status <t>` runs `netbootd` (DHCP+TFTP+HTTP) on a
  USB-Ethernet dongle cabled point-to-point to the DUT. `logs` shows DHCPDISCOVER/OFFER/ACK and TFTP
  RRQ/complete — the authoritative proof of what the DUT actually pulled.

**Trust telemetry over the screen.** The plug's power draw (`shellyplug state` / Shelly RPC `apower`)
tells you if the box is actually running (0 W = soft-off) when the capture is frozen or blank.
**Confirm the outlet is really the DUT's** — a plug that reads 0 W while the machine is demonstrably
running is wired to something else (this exact mislabel cost hours). `paniolo power off` then
querying the relay state (`output:false`) proves the relay opens.

---

## 2. x64 BIOS setup (Intel NUC "Visual BIOS" / AMI Aptio)

Getting in and navigating over a KVM keyboard has two quirks that look like broken input:

- **Keys need a long hold.** The firmware polls USB slowly; a 20 ms tap is dropped. Use
  `openterface_key.py <key> --hold 0.12`. To *enter* setup, hammer across the reboot:
  `openterface_key.py --repeat 45 f2` (F2). Start the hammer **before** the power event.
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
3. **press `f` in the 2-second countdown** — the default; automate it:
   `openterface_key.py --repeat 45 f` running across the power-cycle. Success line on screen:
   **`Fastboot TCP is ready`**.

**Check serial before reaching for video/OCR.** Some firmware redirects the EFI console to the
serial port, in which case the whole gigaboot session — `Gigaboot main`, `Secure Boot: Off`,
`Press f to enter fastboot.`, `Fastboot TCP is ready` — arrives as text on the same channel that
later carries zircon, and no HDMI capture is needed for the bootloader phase at all. Confirmed on
the Dell OptiPlex 7060 (2026-08-19); the Intel NUC11 does **not** do this, which is why the older
guidance here assumes a screen. Cheap to check: capture serial across one PXE boot and look for
`Gigaboot main`.

**Stock** gigaboot's fastboot has **no `boot`/ramboot command** (`getvar`, `flash`, `continue`,
`reboot`, `reboot-bootloader`, `reboot-recovery`, `set_active`, `oem gpt-init`, `oem
add-staged-bootloader-file`, `oem efi-*`). With stock gigaboot, x64 test cycles are **flash slot A
→ continue**, not RAM boot.

**With the local `fastboot boot` patch, RAM boot works — and it is the better loop.** Verified on
two machines (NUC11 2026-08-13; Dell OptiPlex 7060 2026-08-19, where a 74 MB
`bringup_with_tests.x64` went over the wire in 4.7 s at 18.8 MB/s and produced the same
`Ran 90 tests: 0 failed` as a disk-provisioned boot, **writing no partition**). Two gotchas that
cost time:

- **Nothing that ships can issue the verb.** `ffx target fastboot` has no `boot` subcommand, and
  AOSP `fastboot` must not be used here at all (below). Use
  `fuchsia-ci/tools/fastboot_tcp.py --addr <ll> --iface <if> boot <zbi>` — a stdlib fastboot-TCP
  client written for exactly this.
- **Chunk the download.** gigaboot puts a 30 s timer around each read, while the download phase
  accumulates across packets and stays silent until the last one. One giant packet works on a 4 MB
  test image and times out on a 74 MB one; `fastboot_tcp.py` sends 1 MB chunks.

**Check the binary before you trust it.** Several similarly-named gigaboot builds accumulate in a
TFTP root and filenames/timestamps lie about which patches they carry. Confirm by content:
`strings -a <efi> | grep "booting downloaded ZBI"` (the `boot` verb) and
`grep "using the disk with a Fuchsia GPT"` (the disk-discovery fallback), and `sha256sum` against
`out/<build>/kernel.efi_x64/fuchsia-efi.efi` to prove which tree built it.

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
     via that file; Rust's `SocketAddr` rejects interface names, hence numeric):
     ```sh
     IDX=$(cat /sys/class/net/<iface>/ifindex)
     printf 'tcp:[%s%%%s]:5554\n' "$DEV_LL" "$IDX" > ~/.fastboot/devices
     T="[$DEV_LL%$IDX]:5554"
     (cd ~/src/fuchsia && scripts/fx ffx -t "$T" target fastboot getvar all)   # or: oem, flash, reboot, continue
     ```
   - Use the **real ffx from a Fuchsia checkout** (`~/src/fuchsia/.jiri_root/bin/ffx` or `scripts/fx
     ffx`), never an agent-downloaded binary (the harness classifier blocks running those).
   - `getvar all` may error on `slot last set active` when the disk has no ABR metadata yet — that's
     cosmetic; individual getvars (`max-download-size`, `current-slot`) still return.

---

## 4. THE BIG GOTCHA: a PXE-booted gigaboot cannot flash the local disk

If `oem gpt-init` / `flash` fails with **`Failed to get block device(-1)`** (and gigaboot printed
**`No matching block device found`** at boot) while the BIOS clearly lists the NVMe and VMD is off —
it is **not** VMD, not a cable, not the disk. gigaboot's `FindEfiGptDevice()` (`src/firmware/gigaboot/cpp/gpt.cc`)
only matches the block device whose EFI device-path is a **prefix of the running image's device
path** (`gEfiLoadedImage->DeviceHandle`) — i.e. **the disk gigaboot itself booted from**. A
network/PXE-booted gigaboot's "boot device" is the NIC, so it finds **no local disk** and refuses to
touch the NVMe. This is by design.

Consequence: the "PXE gigaboot → `oem gpt-init` → flash the internal disk" flow **cannot do a
first-time install** of a bare machine. It only works once gigaboot already lives on that disk's ESP
(then it flashes its own disk). First-time provisioning of a bare x64 disk needs one of:

- **A USB installer** whose ESP has gigaboot + the install images (`fx mkinstaller`); boot it and it
  writes the internal disk. Needs physical USB **or** a KVM with **virtual-media/USB-mass-storage**
  emulation. A plain Openterface Mini-KVM (HDMI + CH9329 HID only) **cannot** do this remotely.
- **A netboot → running-Fuchsia → userspace paver** flow: boot an eng/recovery image into RAM whose
  base carries `fastboot-tcp` + `paver` (or netsvc's paver / zedboot), then flash the disk from the
  *running OS* — the transport limitation is gone because the paver runs on the device. This is
  exactly what the RPi5 bench does (`rpi5-bringup/docs/ffx-via-sd-userspace-fastboot-plan.md`,
  `scripts/netflash.sh`); adapt it for x64 (a fastboot-tcp/paver-enabled x64 image).

Decide the provisioning method up front — don't discover this at `gpt-init` time. Note that a failed
`gpt-init` writes nothing, so a not-yet-wiped OS on the disk survives the attempt.

### Disk-free RAM netboot of an x64 build — DOES NOT WORK on a UEFI-only x64 box

> **Correction (supersedes an earlier "verified working path" claim here).** On arm64 this pattern
> is fine. On a UEFI-only x64 machine it is **closed**, and the original claim was only ever
> verified as far as GRUB *handing off* — there was no serial console at the time to see what
> happened next. With serial attached, the truth is: **zircon never executes.** GRUB fetches the
> shim and ZBI over HTTP correctly, prints its `boot`, and then the *UEFI firmware* reports
> `!!!! X64 Exception Type - 06(#UD - Invalid Opcode) !!!!` with `RIP = 0x1000`.
>
> Cause, from the shim's own bzImage setup header: `handover_offset = 0`, neither
> `XLF_EFI_HANDOVER_32` nor `XLF_EFI_HANDOVER_64` set, and `code32_start = 0` with
> `kernel_alignment = 0x1000`. GRUB's EFI `linux` loader has no handover entry to call, and the
> zero entry aligned up to 0x1000 is exactly the faulting `RIP`. This is the **legacy BIOS** shim;
> modern x64 boxes have no CSM. It is **not fixable by patching the header** — there is no EFI
> entry code to point the offset at. Use gigaboot (Fuchsia's UEFI loader) instead; changing the
> image on such a box means reprovisioning the disk.
>
> Check any shim before trusting this path:
> ```python
> import struct; d = open('linux-x86-boot-shim.bin','rb').read()
> print('xloadflags 0x%04x' % struct.unpack_from('<H', d, 0x236)[0])   # bit3 = EFI_HANDOVER_64
> print('handover_offset 0x%08x' % struct.unpack_from('<I', d, 0x264)[0])  # 0 = unusable from EFI
> ```

The mechanics below are still correct for **arm64** boards, and for the Linux-installer variant of
this flow on x64 (an ordinary distro `vmlinuz` *does* boot this way — only the Fuchsia shim fails):

- Build a standalone GRUB EFI and serve it as the PXE boot file:
  `grub-mkstandalone -O x86_64-efi --modules="http efinet net linux normal echo gfxterm all_video
  configfile part_gpt" -o grubx64.efi boot/grub/grub.cfg=<cfg>`. The embedded cfg does:
  `set gfxpayload=keep; linux (http,<server>)/linux-x86-boot-shim.bin kernel.bypass-debuglog=true;
  initrd (http,<server>)/fuchsia.zbi; boot`.
- **Fetch over HTTP, not TFTP** — GRUB's built-in TFTP stalls on multi-block transfers; netbootd
  serves the same root on :80.
- **GRUB, not iPXE** — EFI iPXE's `kernel` rejects the Fuchsia shim with `Exec format error` (it's a
  valid bzImage but EFI iPXE can't boot a legacy bzImage). Confirmed correct against the shim source
  (`legacy-boot-shim.cc` takes the ramdisk/initrd as the input ZBI).

**But you cannot see the output without serial.** The x64 boot-test bundles set
`kernel.serial=legacy` (console = legacy PC **COM1, I/O 0x3F8, 115200 8N1**), and the minimal
`linux-x86-boot-shim` does **not** synthesize a framebuffer ZBI item — so zircon has **no gfxconsole**
on this path (the HDMI screen stays frozen on GRUB's last frame; that's expected, not a hang). These
minimal images also bring up **no network** (no netsvc traffic), so there is **no software-only way**
to read pass/fail. This is by design and matches upstream — botanist captures x64 boot-tests over
**serial**. So on any of these paths a **serial console is required to observe/verify the tests** —
and, as the correction above shows, serial is also the only thing that can tell you whether a boot
path works at all. On the NUC11 that's an internal 1×9 1.25 mm PicoBlade **RS-232** header
(RX/TX/GND = pins 2/3/5; RS-232 level, not TTL; PCB pin-1 silkscreen is wrong — trust the TPS);
capture at 115200 8N1. (`gigaboot` *does* pass a framebuffer, so a disk-installed gigaboot boot would
have a gfxconsole — but that's the disk path blocked above.)

---

## 5. `ffx` to a *running* Fuchsia over the point-to-point link

Once Fuchsia is booted (netstack up), `ffx` connects over SSH→RCS on the same link-local link:

```sh
sudo ip -6 addr add fe80::1/64 dev <iface> nodad          # same host addr; ephemeral
cd ~/src/fuchsia && scripts/fx ffx target list             # expect RCS:Y over fe80::…%<iface>
scripts/fx ffx target add 'fe80::<dev>%<iface>'            # if mDNS didn't auto-discover
```

Watch items (from the RPi5 bench): the `fe80::1/64` host address is root-only and **lost on host
reboot** (re-add it — it surfaces in *no* error, RCS just sits at `N`); a stale `~/.fastboot/devices`
entry flips the target into Fastboot state and blocks RCS (remove it + `ffx daemon stop`); stale
`ffx target add` records pin dead addresses (`ffx target remove` + daemon restart).

---

## 6. Reusable snippets

MAC → EUI-64 IPv6 link-local:
```python
mac=[0x54,0xB2,0x03,0xF0,0xB5,0x5C]; b=mac[:]; b[0]^=0x02
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
