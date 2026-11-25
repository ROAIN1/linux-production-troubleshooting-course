<div class="cover" markdown="1">
<div class="mod">Module 09 · Boot Problems & Recovery</div>

# Boot Problems and Recovery: GRUB2, initramfs, Rescue Modes and Getting a Dead Server Back

<p>What happens between power-on and the login prompt, how to break into each stage when it goes wrong, and the exact recovery procedures for the failures you will meet on call: lost root passwords, bad fstab entries, broken kernels, full /boot, kernel panics, slow boots and cloud VMs you cannot reach.</p>
</div>

## What you'll learn

- The **boot chain** on RHEL 9: firmware (BIOS/UEFI) → **GRUB2** → **kernel + initramfs** → **systemd** → **targets**, and which failure belongs to which stage
- Managing GRUB2 with **grubby** (`--default-kernel`, `--set-default`, `--args`), BLS entries, and the correct **grub2-mkconfig** path on BIOS vs UEFI (and why RHEL 9 changed it)
- Editing the kernel line at the GRUB menu: **rd.break**, **systemd.unit=rescue.target**, **systemd.unit=emergency.target**, **init=/bin/bash**
- **Resetting the root password** on RHEL 9 with `rd.break`, and why `touch /.autorelabel` is not optional on an SELinux system
- **Emergency mode**: why a bad `/etc/fstab` or a damaged filesystem drops you there, and how to get out safely
- Rebuilding the initramfs with **dracut**, booting an **older kernel** after a bad update, and rescuing a **full /boot**
- **Kernel panics** and **kdump** basics
- Recovering **cloud VMs** you cannot SSH into: AWS EC2 Serial Console, Azure Serial Console and `az vm repair`, OCI console connections, and the attach-disk-to-rescue-VM + `chroot` method
- **Filesystem repair** with `xfs_repair` and `e2fsck`, and slow-boot analysis with **systemd-analyze blame / critical-chain**
- Four break-fix labs: root password reset with relabel, bad fstab recovery, previous kernel as default, and slow boot analysis

**Why it matters in production:** a server that does not boot is the one outage where your usual tools (SSH, monitoring agents, config management) are all gone at once. The engineer who knows the boot chain and has practised `rd.break`, emergency mode and the rescue-VM method recovers in fifteen minutes; the one who has not ends up restoring from backup and explaining the data loss.

## 1. The boot process, stage by stage

Every boot problem is easier once you can say **which stage failed**. The screen output almost always tells you, if you know what each stage looks like.

```text
 Power on
   │
   ▼
 1. Firmware (BIOS or UEFI)      POST, finds a boot device
   │                             BIOS: reads MBR / boot sector
   │                             UEFI: runs \EFI\<vendor>\shimx64.efi from the ESP
   ▼
 2. GRUB2 boot loader            reads grub.cfg + BLS entries in /boot/loader/entries/
   │                             shows menu, loads vmlinuz + initramfs into RAM
   ▼
 3. Kernel                       decompresses, detects hardware, mounts initramfs as /
   │
   ▼
 4. initramfs (dracut)           loads storage drivers, assembles LVM/RAID/LUKS,
   │                             finds root= device, mounts it read-only on /sysroot,
   │                             switch_root to the real root
   ▼
 5. systemd (PID 1)              loads SELinux policy, reads units, mounts /etc/fstab,
   │                             starts services toward the default target
   ▼
 6. Target reached               multi-user.target (servers) or graphical.target
                                 login prompt / sshd accepting connections
```

| Stage | Lives in | Typical failure you see | Where you fix it |
|---|---|---|---|
| Firmware | NVRAM / BIOS setup | "No bootable device", PXE boot loop, wrong boot order | Firmware menu, `efibootmgr`, cloud: boot volume attachment |
| GRUB2 | `/boot`, ESP at `/boot/efi` | `grub>` or `grub rescue>` prompt, "error: file not found" | Rescue media or rescue VM, reinstall GRUB packages, `grub2-mkconfig` |
| Kernel | `/boot/vmlinuz-*` | Kernel panic, "VFS: Unable to mount root fs" | Boot older kernel, reinstall kernel, rebuild initramfs |
| initramfs | `/boot/initramfs-*.img` | `dracut:/#` emergency shell, "/dev/mapper/rhel-root does not exist" | `dracut -f`, fix `root=` / `rd.lvm.lv=` args, add drivers |
| systemd | `/etc/fstab`, unit files | "You are in emergency mode", "A start job is running for..." | Fix fstab, fix filesystem, fix or mask unit |
| Target | Services | Boots but app or sshd not up | Normal service troubleshooting (Modules 03–04) |

:::concept
**Two root filesystems.** During boot there are two "/" directories. First the kernel unpacks the **initramfs** (a small cpio archive) into RAM and runs from it. Its job is to find and mount the **real** root filesystem at `/sysroot`, then `switch_root` into it and hand control to `/usr/lib/systemd/systemd` on the real disk. `rd.break` stops you **between** these two worlds: you are in the initramfs, with the real root mounted read-only at `/sysroot`. Understanding this makes the root password reset procedure obvious instead of a memorised spell.
:::

### BIOS or UEFI? Check before you touch GRUB

```bash
[ -d /sys/firmware/efi ] && echo "UEFI boot" || echo "Legacy BIOS boot"
```

```text
UEFI boot
```

On UEFI systems, look at the firmware boot entries:

```bash
efibootmgr -v
```

```text
BootCurrent: 0004
BootOrder: 0004,0001,0000
Boot0000* UiApp
Boot0001* UEFI Misc Device
Boot0004* Rocky Linux   HD(1,GPT,3c7a...,0x800,0x12c000)/File(\EFI\rocky\shimx64.efi)
```

`BootCurrent` is the entry that booted this time. The path shows the vendor directory: `\EFI\redhat\` on RHEL, `\EFI\rocky\` on Rocky, `\EFI\almalinux\` on AlmaLinux. You will need that directory name later.

### Where the boot pieces live

| Path | What it is |
|---|---|
| `/boot/vmlinuz-<version>` | Kernel image |
| `/boot/initramfs-<version>.img` | Initial RAM filesystem built by dracut for that kernel |
| `/boot/loader/entries/*.conf` | **BLS** (Boot Loader Specification) entries, one file per installed kernel |
| `/boot/grub2/grub.cfg` | Main GRUB2 configuration (generated, do not hand-edit) |
| `/boot/grub2/grubenv` | GRUB environment block: `saved_entry`, on RHEL 8 also `kernelopts` |
| `/boot/efi/EFI/<vendor>/` | EFI System Partition (FAT32): shim, `grubx64.efi`, and on RHEL 9 a small stub `grub.cfg` |
| `/etc/default/grub` | Input settings for `grub2-mkconfig` (timeout, `GRUB_CMDLINE_LINUX`) |
| `/etc/kernel/cmdline` | Default kernel arguments used when new kernels are installed (RHEL 9) |

## 2. GRUB2 on RHEL 9: BLS entries and grubby

Since RHEL 8, each kernel has its own **BLS snippet** in `/boot/loader/entries/` instead of a hard-coded `menuentry` block in `grub.cfg`. `grub.cfg` contains a `blscfg` command that reads those snippets at boot. The practical consequence: **you rarely need to run `grub2-mkconfig`**. Installing a kernel creates its BLS file, and `grubby` edits the BLS files and the saved default directly.

```bash
ls /boot/loader/entries/
cat /boot/loader/entries/*5.14.0-427.13.1*.conf
```

```text
a1b2c3d4e5f6...-5.14.0-362.24.1.el9_3.x86_64.conf
a1b2c3d4e5f6...-5.14.0-427.13.1.el9_4.x86_64.conf
a1b2c3d4e5f6...-0-rescue.conf

title Rocky Linux (5.14.0-427.13.1.el9_4.x86_64) 9.4 (Blue Onyx)
version 5.14.0-427.13.1.el9_4.x86_64
linux /vmlinuz-5.14.0-427.13.1.el9_4.x86_64
initrd /initramfs-5.14.0-427.13.1.el9_4.x86_64.img $tuned_initrd
options root=/dev/mapper/rl-root ro crashkernel=1G-4G:192M,4G-64G:256M,64G-:512M resume=/dev/mapper/rl-swap rd.lvm.lv=rl/root rd.lvm.lv=rl/swap rhgb quiet $tuned_params
grub_users $grub_users
grub_arg --unrestricted
grub_class rocky
```

### grubby: the commands you actually use

```bash
grubby --default-kernel            # which kernel boots next time
grubby --default-index             # its position in the menu (0 = top)
grubby --info=ALL                  # every entry with index, kernel, args
grubby --info=DEFAULT              # just the default entry
```

```text
/boot/vmlinuz-5.14.0-427.13.1.el9_4.x86_64

index=0
kernel="/boot/vmlinuz-5.14.0-427.13.1.el9_4.x86_64"
args="ro crashkernel=1G-4G:192M,4G-64G:256M,64G-:512M resume=/dev/mapper/rl-swap rd.lvm.lv=rl/root rd.lvm.lv=rl/swap rhgb quiet"
root="/dev/mapper/rl-root"
initrd="/boot/initramfs-5.14.0-427.13.1.el9_4.x86_64.img"
title="Rocky Linux (5.14.0-427.13.1.el9_4.x86_64) 9.4 (Blue Onyx)"
id="a1b2c3d4e5f6...-5.14.0-427.13.1.el9_4.x86_64"
```

Change the default kernel (persistent):

```bash
grubby --set-default=/boot/vmlinuz-5.14.0-362.24.1.el9_3.x86_64
grubby --set-default-index=1        # alternative: by menu position
grubby --default-kernel             # always verify
```

Boot a different entry **once only** (next reboot only, then back to the saved default). This is the safe way to test a new kernel on a remote box:

```bash
grub2-reboot 1          # menu index or entry title/id; requires GRUB_DEFAULT=saved
reboot
```

### Kernel arguments: add, remove, verify

```bash
# Add to every installed kernel
grubby --update-kernel=ALL --args="console=ttyS0,115200n8"

# Remove from every kernel
grubby --update-kernel=ALL --remove-args="rhgb quiet"

# Only the default kernel
grubby --update-kernel=DEFAULT --args="systemd.log_level=debug"

# What did the running kernel boot with?
cat /proc/cmdline
```

`/proc/cmdline` is the only truth about the **current** boot. The BLS files are what the **next** boot will use.

:::warn
**Version-dependent detail.** On RHEL 9 the arguments live in each BLS entry's `options` line, and `/etc/kernel/cmdline` is used as the template for kernels installed later. On RHEL 8 the BLS files reference `$kernelopts`, stored in `/boot/grub2/grubenv`. If you edit `GRUB_CMDLINE_LINUX` in `/etc/default/grub` and run `grub2-mkconfig`, older RHEL 9 minors did **not** push the change into existing BLS entries; from RHEL 9.3 onward use `grub2-mkconfig -o /boot/grub2/grub.cfg --update-bls-cmdline`. `grubby --update-kernel=ALL` works on all of them, which is why it is the recommended tool.
:::

### grub2-mkconfig: the path matters

When you do need to regenerate `grub.cfg` (for example after changing `GRUB_TIMEOUT` or `GRUB_TERMINAL` in `/etc/default/grub`), the output path depends on the release and firmware:

| Release | Firmware | Command |
|---|---|---|
| RHEL/Rocky/Alma 7 and 8 | BIOS | `grub2-mkconfig -o /boot/grub2/grub.cfg` |
| RHEL/Rocky/Alma 7 and 8 | UEFI | `grub2-mkconfig -o /boot/efi/EFI/<vendor>/grub.cfg` |
| **RHEL/Rocky/Alma 9** | **BIOS and UEFI** | `grub2-mkconfig -o /boot/grub2/grub.cfg` |
| Ubuntu / Debian | Both | `update-grub` (wrapper for `grub-mkconfig -o /boot/grub/grub.cfg`) |

:::warn
**Do not overwrite the EFI stub on RHEL 9.** On RHEL 9 UEFI systems, `/boot/efi/EFI/<vendor>/grub.cfg` is a three-line stub that finds the `/boot` filesystem by UUID and loads `/boot/grub2/grub.cfg`. Old runbooks written for RHEL 8 say to write to the EFI path. Doing that on RHEL 9 replaces the stub with a full config and can leave the system booting an unexpected configuration, or fail on the next update. If it already happened, restore the stub from the package (reinstalling `grub2-common` / `grub2-efi-x64` is the usual route; check the Red Hat documentation for your minor release). Check before you write:

```bash
cat /boot/efi/EFI/rocky/grub.cfg
```

```text
search --no-floppy --fs-uuid --set=dev 6f0e5a2b-...
set prefix=($dev)/grub2
export $prefix
configfile $prefix/grub.cfg
```
:::

### Make the GRUB menu reachable

Cloud images and some installs set a 0–1 second timeout or hide the menu. On a box you may need to recover, give yourself time to press a key:

```bash
grep -E 'GRUB_TIMEOUT|GRUB_TERMINAL|GRUB_SERIAL' /etc/default/grub
sed -i 's/^GRUB_TIMEOUT=.*/GRUB_TIMEOUT=5/' /etc/default/grub
grub2-mkconfig -o /boot/grub2/grub.cfg           # RHEL 9, BIOS or UEFI
grub2-editenv - unset menu_auto_hide             # if the menu auto-hides
```

:::tip
For cloud VMs and headless servers, make sure the GRUB menu and the kernel both talk to the serial port: `GRUB_TERMINAL="serial console"`, `GRUB_SERIAL_COMMAND="serial --speed=115200"`, and `console=tty0 console=ttyS0,115200n8` in the kernel args. Major cloud images ship this already. A custom image built without it gives you a serial console that shows nothing at exactly the moment you need it.
:::

## 3. Interrupting the boot: editing the kernel line

At the GRUB menu, highlight an entry and press **e**. You get an editor showing the entry. Find the line that starts with `linux` (it may wrap across several screen lines), move to the end with **End** or **Ctrl+E**, add your argument, and press **Ctrl+X** to boot it. The change applies to **this boot only**; nothing is written to disk.

```text
load_video
set gfxpayload=keep
insmod gzio
linux ($root)/vmlinuz-5.14.0-427.13.1.el9_4.x86_64 root=/dev/mapper/rl-root ro crashkernel=1G-4G:192M,4G-64G:256M,64G-:512M resume=/dev/mapper/rl-swap rd.lvm.lv=rl/root rd.lvm.lv=rl/swap rhgb quiet rd.break
initrd ($root)/initramfs-5.14.0-427.13.1.el9_4.x86_64.img $tuned_initrd
```

Also remove `rhgb quiet` while you are there. `rhgb` is the graphical splash and `quiet` hides kernel messages; you want to **see** what fails.

| Argument | Where it stops | Root filesystem | Password needed? | Use it for |
|---|---|---|---|---|
| `rd.break` | In the initramfs, just before `switch_root` | Real root mounted **read-only** at `/sysroot` | **No** | Root password reset, fixing files when systemd cannot start |
| `rd.break=pre-mount` | In the initramfs, **before** root is mounted | Not mounted | No | Running `xfs_repair` on the root device |
| `systemd.unit=emergency.target` (or just `emergency`) | Very early in systemd: only root mounted, nothing else | **Read-only** `/` | Root password (unless root is locked, see below) | Bad fstab, broken units, filesystem problems on non-root mounts |
| `systemd.unit=rescue.target` (or `rescue`, `single`, `1`) | systemd single-user: local filesystems mounted, no network services | Read-write, all fstab mounts | Root password | Maintenance that needs all local filesystems, but no network/users |
| `init=/bin/bash` | Kernel runs bash as PID 1 instead of systemd | Read-only (because of `ro`) | **No** | Last resort when systemd itself is broken |
| `enforcing=0` | Normal boot with SELinux permissive | — | — | One-boot **diagnostic** only, to test if SELinux causes the failure |
| `systemd.debug-shell=1` | Normal boot plus a root shell on **tty9** | — | No | Debugging a hang late in boot (disable afterwards, it is an open root shell) |

:::concept
**Rescue vs emergency.** Think of `emergency.target` as "systemd started, but trust nothing": only `/` is mounted, read-only, and almost no units run. `rescue.target` is "single-user mode": `local-fs.target` is reached, so **all fstab filesystems must mount**. That is why a bad fstab line breaks rescue mode too, and why emergency mode is the one you want for fstab problems.
:::

:::warn
**"Cannot open access to console, the root account is locked."** Many RHEL 9 installs, and nearly every cloud image, lock the root password and rely on a sudo user. Emergency and rescue mode need the root password, so on those systems they print this message and refuse. Your options are `rd.break` (no password needed), `init=/bin/bash`, or the rescue-VM method in section 8. Many teams deliberately set a strong root password, stored in a vault, on every server precisely so that the serial console and emergency mode work during an incident.
:::

:::tip
**On VMs with several consoles, the shell appears on the last `console=`.** If the kernel line has `console=ttyS0,115200n8 console=tty0`, the `rd.break` or emergency shell appears on the VGA console (`tty0`), not the serial console you are watching. When working through a serial console, make sure `console=ttyS0,...` is the **last** console argument for this boot.
:::

## 4. Resetting the root password on RHEL 9 (and why SELinux cares)

This is the procedure every Linux admin is expected to know cold. On RHEL 9:

1. Reboot and interrupt GRUB (press a key or **Esc** when the menu appears).
2. Highlight the kernel you normally boot and press **e**.
3. At the end of the `linux` line, remove `rhgb quiet` and append `rd.break`. Press **Ctrl+X**.
4. You land in the initramfs shell:

```text
Generating "/run/initramfs/rdsosreport.txt"

Entering emergency mode. Exit the shell to continue.
Type "journalctl" to view system logs.
You might want to save "/run/initramfs/rdsosreport.txt" to a USB stick or /boot
after mounting them and attach it to a bug report.

Press Enter for maintenance
(or press Control-D to continue):

switch_root:/#
```

5. Remount the real root read-write, chroot into it, change the password, and request a relabel:

```bash
mount -o remount,rw /sysroot
chroot /sysroot
passwd root
touch /.autorelabel
exit
exit
```

6. The first `exit` leaves the chroot, the second leaves the initramfs shell and boot continues. Early in the boot, systemd sees `/.autorelabel`, runs **selinux-autorelabel**, relabels every file on every SELinux-capable filesystem, and **reboots again automatically**. This can take from under a minute on a small VM to a long time on a server with millions of files. Do not interrupt it.
7. Log in as root with the new password.

### Why `touch /.autorelabel` matters

Inside the `rd.break` shell you are running from the initramfs. **No SELinux policy is loaded yet**: systemd loads it from the real root after `switch_root`. So when `passwd` runs inside the chroot, it writes a new `/etc/shadow` (it writes a temporary file and renames it over the old one) and that new file gets **no proper SELinux label**. It should be `shadow_t`; instead it ends up unlabeled.

When the system boots in enforcing mode, the processes that check passwords (`login`, `sshd` via PAM, `unix_chkpwd`, `sudo`) are confined domains that are allowed to read `shadow_t`, not `unlabeled_t`. Result: **nobody can log in**, including root with the password you just set, and you see AVC denials if you can get to a shell at all.

```text
type=AVC msg=audit(1732521600.123:245): avc:  denied  { read } for  pid=1123 comm="unix_chkpwd"
  name="shadow" dev="dm-0" ino=33652 scontext=system_u:system_r:chkpwd_t:s0
  tcontext=system_u:object_r:unlabeled_t:s0 tclass=file permissive=0
```

`/.autorelabel` tells the next boot to run `fixfiles` and restore the correct label to every file, including `/etc/shadow`.

:::tip
**The faster alternative for big servers.** A full relabel on a host with tens of millions of files can take an hour. Instead of `touch /.autorelabel`, boot once with SELinux permissive and fix only the file you touched:

1. At GRUB, append `rd.break enforcing=0`.
2. Remount, chroot, `passwd root`, exit, exit (no `/.autorelabel`).
3. After boot, log in as root and run:

```bash
restorecon -v /etc/shadow
setenforce 1
getenforce
```

```text
Relabeled /etc/shadow from system_u:object_r:unlabeled_t:s0 to system_u:object_r:shadow_t:s0
Enforcing
```

The `enforcing=0` applies to this one boot only and you switch back to enforcing immediately. This is a controlled diagnostic step, not "turn SELinux off". If you edited anything else in the chroot, `restorecon` those files too, or use the full relabel.
:::

### The init=/bin/bash variant

If `rd.break` is not available (an unusual initramfs, a very old release), append `init=/bin/bash` instead:

```bash
mount -o remount,rw /
passwd root
touch /.autorelabel
sync
exec /sbin/init        # or: echo b > /proc/sysrq-trigger   (immediate reboot)
```

There is no `/sysroot` and no chroot here: the kernel mounted the real root as `/` and ran bash instead of systemd. `reboot` will not work normally because systemd is not running; that is why you `exec /sbin/init` or use the sysrq trigger after `sync`.

:::warn
**Physical and console access is root access.** Anyone who can reach the GRUB menu can do this. Protect production consoles: set a GRUB password (`grub2-setpassword`, which writes `/boot/grub2/user.cfg`), restrict who can open cloud serial consoles with IAM/RBAC, and keep BMC/iLO/iDRAC on a management network. Note that a GRUB password protects **editing** entries; with the default `--unrestricted` class users can still boot the listed entries.
:::

**Ubuntu/Debian:** use the "Advanced options → recovery mode" menu entry (root shell, with a password prompt if root has one), or append `init=/bin/bash` and remount `/` read-write. AppArmor does not label files, so there is no relabel step.

:::prod
**"The password reset worked, but now nobody can log in."** Symptom: after an overnight root password reset on a database server via the hypervisor console, root login fails with "Login incorrect", sshd rejects every key-less login, and `sudo` fails for the DBA. The engineer saw that the password was definitely correct, because they had set it minutes earlier. Root cause: they had followed an old blog post that skipped `touch /.autorelabel`, so `/etc/shadow` was `unlabeled_t` and every password check was denied by SELinux. Fix: rebooted with `rd.break enforcing=0`, logged in, `restorecon -v /etc/shadow`, `setenforce 1`, verified login with SSH from a second terminal before closing the console. Prevention: the team's runbook now includes the relabel step and the reason for it, and a vaulted root password per host so password resets are rare.
:::

## 5. Emergency mode: fstab and filesystem failures

The most common reason a healthy server suddenly lands in emergency mode after a reboot is **/etc/fstab**. Someone added a mount weeks ago, never rebooted, and the first reboot (patching night) fails.

### What it looks like

```text
[ TIME ] Timed out waiting for device /dev/disk/by-uuid/0d4f6a21-9c1e-4b7e-a0f1-3f2b8e6c1d99.
[DEPEND] Dependency failed for /data.
[DEPEND] Dependency failed for Local File Systems.
[DEPEND] Dependency failed for Mark the need to relabel after reboot.
You are in emergency mode. After logging in, type "journalctl -xb" to view
system logs, "systemctl reboot" to reboot, "systemctl default" or "exit"
to boot into default mode.
Give root password for maintenance
(or press Control-D to continue):
```

Read it from the top: a device did not appear within the timeout (default **90 seconds**), so the mount unit `data.mount` failed, so `local-fs.target` failed, and because `local-fs.target` is required by almost everything, systemd stopped and opened the emergency shell.

### Common causes

| Cause | Example | Message you see |
|---|---|---|
| Device does not exist | Disk detached, cloud volume not attached, wrong UUID | "Timed out waiting for device" |
| Device name changed | `/dev/sdb1` became `/dev/sdc1`, `/dev/xvdf` became `/dev/nvme1n1` on Nitro | Timed out, or wrong disk mounted |
| Typo in options or fstype | `defualts`, `xsf` | "wrong fs type, bad option, bad superblock" |
| Mount point missing | `/data` directory does not exist | "mount point does not exist" |
| Filesystem damaged | Unclean shutdown, storage fault | "Structure needs cleaning", fsck failure |
| Network filesystem without `_netdev` | NFS line tried before network is up | Mount failure, timeout |

### Recovering

Log in with the root password. Then:

```bash
journalctl -xb -p err          # what failed and why
systemctl --failed             # which units failed
mount -o remount,rw /          # / is read-only in emergency mode
vi /etc/fstab                  # comment out or fix the bad line
systemctl daemon-reload        # regenerate mount units from fstab
findmnt --verify               # parse and sanity-check fstab
mount -a                       # mount everything; must return silently
systemctl default              # continue to the default target (or reboot)
```

```text
$ findmnt --verify
/data
   [E] unreachable source: UUID=0d4f6a21-9c1e-4b7e-a0f1-3f2b8e6c1d99
   [W] cannot detect on-disk filesystem type

0 parse errors, 1 error, 1 warning
```

:::warn
**Always run `systemctl daemon-reload` after editing fstab.** systemd converts fstab into `.mount` units with `systemd-fstab-generator`. If you edit fstab and run `mount -a` without reloading, you can get "mount: (hint) your fstab has been modified, but systemd still uses the old version" and systemd may even unmount what you just mounted.
:::

### Prevention: write fstab lines that cannot kill the boot

```text
# /etc/fstab
UUID=0d4f6a21-9c1e-4b7e-a0f1-3f2b8e6c1d99  /data   xfs   defaults,nofail,x-systemd.device-timeout=10s  0 0
10.0.0.20:/export/shared                    /shared nfs   defaults,_netdev,nofail                       0 0
```

- **UUIDs or labels**, never `/dev/sdX` names, which change between boots and instance types (`blkid` or `lsblk -f` shows the UUID).
- **`nofail`** for anything the OS itself does not need to boot: the boot continues if it is missing, and the application fails visibly instead of the whole server disappearing.
- **`x-systemd.device-timeout=10s`** so a missing disk costs 10 seconds, not 90.
- **`_netdev`** for network filesystems.
- **Test before you reboot:** `findmnt --verify` and `umount /data && mount -a`. If `mount -a` complains now, the reboot will fail later.
- SELinux on mounts: if a filesystem has no SELinux labels (NFS, CIFS, vfat) and a confined service must read it, use the `context=` mount option, for example `context="system_u:object_r:httpd_sys_content_t:s0"`, or the relevant boolean such as `setsebool -P httpd_use_nfs on`. Do not "fix" access denied on a new mount by switching SELinux off.

:::prod
**The EBS volume that took down the web tier.** Symptom: after a routine kernel patch, three of six EC2 web servers never came back; the load balancer marked them unhealthy and SSH timed out. The engineer opened the EC2 Serial Console on one and saw "Timed out waiting for device /dev/xvdf" followed by "You are in emergency mode". Root cause: months earlier a temporary log volume had been attached as `/dev/xvdf` and mounted via fstab; it was later detached, but the fstab line stayed, and the instances had not rebooted since. On Nitro instances the name would have been wrong anyway (`/dev/nvme1n1`). Fix: root password from the vault, `mount -o remount,rw /`, commented out the line, `systemctl daemon-reload`, `systemctl default`. Prevention: fstab managed by Ansible with UUIDs and `nofail,x-systemd.device-timeout=10s`; a CI check that runs `findmnt --verify`; and a monthly reboot of one canary host per tier so latent boot problems surface on one server instead of half the fleet.
:::

## 6. Filesystem repair

When the failing mount is a damaged filesystem rather than a missing device, you repair it **unmounted**. Running a repair tool on a mounted read-write filesystem can destroy it.

### XFS (RHEL default)

```bash
umount /data                     # must be unmounted
xfs_repair -n /dev/vg_data/lv_data      # -n = no-modify, report only
xfs_repair /dev/vg_data/lv_data         # actual repair
```

If `xfs_repair` refuses because the log is dirty:

```text
ERROR: The filesystem has valuable metadata changes in a log which needs to
be replayed.  Mount the filesystem to replay the log, and unmount it before
re-running xfs_repair.  If you are unable to mount the filesystem, then use
the -L option to destroy the log and attempt a repair.
```

Do exactly what it says: **mount and unmount first** to replay the log. Only if the mount fails, use `xfs_repair -L`, which **zeroes the log and loses the most recent metadata changes**. Take a snapshot of the volume first if you possibly can (cloud snapshot, LVM snapshot, or `dd` image). Files that lose their directory entries end up in `lost+found` on the repaired filesystem.

Note that `fsck.xfs` is a no-op at boot: XFS replays its journal on mount and does not get a boot-time fsck like ext4.

### ext4

```bash
umount /var/lib/app
e2fsck -f /dev/vg_data/lv_app        # -f forces a check even if marked clean
e2fsck -fy /dev/vg_data/lv_app       # -y answers yes to all repairs (unattended)
```

### Repairing the root filesystem

You cannot unmount `/` on a running system. Options, in order of preference:

1. **`rd.break=pre-mount`** at GRUB: you stop in the initramfs before root is mounted. LVM is usually already active because of the `rd.lvm.lv=` arguments. Run `xfs_repair /dev/mapper/rl-root` (check the name with `lvm lvs` or `ls /dev/mapper`), then `exit` to continue booting.
2. **Boot from installation media** → Troubleshooting → Rescue, choose **"3) Skip to shell"** so the system is not mounted, and repair.
3. **Cloud:** attach the root volume to a rescue VM (section 8) and repair it there.

For ext4 root, you can also schedule a check on next boot with the kernel args `fsck.mode=force fsck.repair=yes`.

:::warn
**Find out why the filesystem broke.** Corruption is a symptom. Check `dmesg` and `journalctl -k` for I/O errors, `smartctl -a` on physical disks, RAID controller logs, or cloud volume status checks. Repairing a filesystem on a failing disk just loses more data on the next write.
:::

## 7. Kernels and initramfs: bad updates, dracut, full /boot

### Booting an older kernel after a bad update

RHEL keeps several kernels installed (`installonly_limit=3` in `/etc/dnf/dnf.conf` by default), precisely so you can fall back. If a new kernel panics, hangs, or breaks a driver:

1. At the GRUB menu, choose the **previous** kernel entry (the second line) and boot it.
2. Confirm what you are running: `uname -r`.
3. Make the old kernel the default so an unattended reboot does not bring the bad one back:

```bash
grubby --info=ALL | grep -E '^(index|kernel)'
grubby --set-default=/boot/vmlinuz-5.14.0-362.24.1.el9_3.x86_64
grubby --default-kernel
```

4. Stop the bad kernel from returning on the next `dnf update` while you investigate. Either exclude it temporarily (`dnf update --exclude=kernel*`, or `excludepkgs=kernel*` in the repo/`dnf.conf` with a comment and a ticket number), or use the `versionlock` plugin (`dnf install python3-dnf-plugin-versionlock`, then `dnf versionlock add kernel-5.14.0-362.24.1.el9_3`). Remove the lock once the fix is out.
5. Find the cause in the bad boot's logs: `journalctl -b -1 -p err` (requires a persistent journal, `/var/log/journal` must exist).

### When the initramfs is the problem: dracut

Symptoms of a broken or missing initramfs:

```text
[    2.104311] Kernel panic - not syncing: VFS: Unable to mount root fs on unknown-block(0,0)
```

`unknown-block(0,0)` means the kernel had no block device for root at all: usually the initramfs is missing, truncated, or lacks the storage driver. The other common form is the dracut emergency shell:

```text
[  184.512903] dracut-initqueue[412]: Warning: dracut-initqueue: timeout, still waiting for following initqueue hooks:
[  184.513555] dracut-initqueue[412]: Warning: /lib/dracut/hooks/initqueue/finished/devexists-\x2fdev\x2fmapper\x2frl-root.sh: "if ! grep -q After=remote-fs-pre.target /run/systemd/generator/systemd-cryptsetup@*.service 2>/dev/null; then
[  184.519000] dracut-initqueue[412]: Warning: /dev/mapper/rl-root does not exist
Generating "/run/initramfs/rdsosreport.txt"
dracut:/#
```

Here the initramfs ran but could not find the root device: wrong `root=` or `rd.lvm.lv=` (a renamed volume group), a missing driver, or a missing disk. At the `dracut:/#` prompt, `lvm lvs`, `blkid` and `ls /dev/disk/by-uuid` tell you what the initramfs can see.

Rebuild an initramfs once you are booted on a working kernel (or from a chroot):

```bash
dracut -f                                              # rebuild for the running kernel
dracut -f --kver 5.14.0-427.13.1.el9_4.x86_64          # for a specific installed kernel
dracut -f /boot/initramfs-5.14.0-427.13.1.el9_4.x86_64.img 5.14.0-427.13.1.el9_4.x86_64   # explicit form
dracut -f --regenerate-all                             # every installed kernel
lsinitrd /boot/initramfs-$(uname -r).img | less        # inspect contents
lsinitrd /boot/initramfs-$(uname -r).img | grep -E 'nvme|xen-blkfront|virtio'
```

:::warn
**Rebuild for the right kernel.** Inside a chroot on a rescue VM, `uname -r` returns the **rescue VM's** kernel, not the broken system's. Always pass `--kver` with a version from `ls /lib/modules` of the broken system. Also, dracut builds **host-only** images by default: it includes only the drivers this machine uses today. If you are about to move a disk to different hardware (a P2V migration, Xen to Nitro, VMware to KVM), add the drivers (`dracut -f --add-drivers "nvme virtio_blk virtio_scsi" --kver ...`) or build a generic image (`--no-hostonly`).
:::

**Ubuntu/Debian:** `update-initramfs -u -k all` rebuilds, `lsinitramfs` inspects, and `update-grub` regenerates GRUB config.

### Rescue from a full /boot

`/boot` on RHEL is typically a 1 GB partition. Each kernel plus its initramfs uses 80–150 MB, and the rescue image (`vmlinuz-0-rescue-*` and its large non-hostonly initramfs) can be another 100+ MB. If `/boot` fills, a kernel update can install the kernel but **fail to write its initramfs**, and the new entry becomes the default. Everything looks fine until the next reboot, which panics with `unknown-block(0,0)`.

Diagnose:

```bash
df -h /boot
ls -lh /boot
rpm -q kernel
uname -r
grubby --default-kernel
```

```text
Filesystem      Size  Used Avail Use% Mounted on
/dev/sda1       960M  958M  2.0M 100% /boot

kernel-5.14.0-284.30.1.el9_2.x86_64
kernel-5.14.0-362.24.1.el9_3.x86_64
kernel-5.14.0-427.13.1.el9_4.x86_64
5.14.0-362.24.1.el9_3.x86_64
/boot/vmlinuz-5.14.0-427.13.1.el9_4.x86_64
```

Fix, **never removing the running kernel**:

```bash
# Remove old kernels, keep the newest 2 (dnf works out which)
dnf remove --oldinstallonly --setopt installonly_limit=2 kernel

# Or remove a specific old version
dnf remove kernel-core-5.14.0-284.30.1.el9_2.x86_64

# Check the default kernel actually has an initramfs now; rebuild if not
ls -l /boot/initramfs-5.14.0-427.13.1.el9_4.x86_64.img || \
  dracut -f --kver 5.14.0-427.13.1.el9_4.x86_64
lsinitrd /boot/initramfs-5.14.0-427.13.1.el9_4.x86_64.img | head -5
df -h /boot
```

Never delete kernel files from `/boot` with `rm`: the RPM database still thinks they are installed, the BLS entries remain, and the next update gets confused. Use `dnf remove`.

:::tip
Put `df -h /boot` in your patch pre-checks and alert at 70% on `/boot`. In `/etc/dnf/dnf.conf`, `installonly_limit=3` (the default) is right for most servers; lower it to 2 only if `/boot` is small and cannot be grown.
:::

:::prod
**Patched Saturday, dead Sunday.** Symptom: after a weekend patch run, an app server rebooted and sat at "Kernel panic - not syncing: VFS: Unable to mount root fs on unknown-block(0,0)". The patch job had reported success. Root cause: `/boot` was at 100%; the dnf transaction installed the kernel, but the dracut step in the post-install script failed with "No space left on device", a warning the automation did not treat as fatal. The new kernel became the default with no initramfs. Fix: at the GRUB menu, booted the previous kernel; removed the oldest kernel with `dnf remove`; `dracut -f --kver <new version>`; checked with `lsinitrd`; rebooted into the new kernel. Prevention: the Ansible patch role now asserts at least 300 MB free on `/boot` before patching and checks after patching that `/boot/initramfs-<default kernel>.img` exists and is non-empty before it allows a reboot.
:::

## 8. Kernel panics and kdump

A **kernel panic** is the kernel deciding it cannot continue safely. Common production causes: missing root filesystem (above), buggy third-party kernel modules (storage, security agents, GPU drivers), hardware memory errors, and, by configuration, OOM or hung-task panics.

What to capture first: the **last 30 lines of the console**. The `Kernel panic - not syncing:` line gives the reason; the call trace above it shows the modules involved. On a cloud VM, get the serial log (section 9) before rebooting, because the next boot overwrites what you can see.

Make panicking servers reboot themselves instead of hanging forever:

```bash
sysctl kernel.panic                 # seconds before auto-reboot after panic (0 = hang)
echo 'kernel.panic = 10' > /etc/sysctl.d/90-panic.conf
sysctl --system
```

### kdump: capture the crash for root cause analysis

**kdump** reserves a small amount of memory at boot for a second "capture" kernel. When the main kernel panics, it `kexec`s into the capture kernel, which saves the crashed kernel's memory as a `vmcore` and reboots. Without kdump, a one-off panic is usually unexplainable; with it, Red Hat support or your kernel team can find the bug.

```bash
dnf install kexec-tools
cat /proc/cmdline | grep -o 'crashkernel=[^ ]*'
kdumpctl reset-crashkernel --kernel=ALL     # set the recommended crashkernel= value (RHEL 9.1+)
reboot                                      # the reservation only applies after reboot
systemctl enable --now kdump
kdumpctl status
```

```text
kdump: Kdump is operational
```

Configuration is in `/etc/kdump.conf` (default target: `path /var/crash` on the local root filesystem). After a crash you get:

```text
/var/crash/127.0.0.1-2026-11-25-03:14:07/
├── kexec-dmesg.log
├── vmcore
└── vmcore-dmesg.txt
```

Start with `vmcore-dmesg.txt`, the kernel log up to the panic, which often names the module. Deeper analysis uses the `crash` utility with the matching `kernel-debuginfo` package.

:::warn
**Testing kdump crashes the machine.** `echo 1 > /proc/sys/kernel/sysrq; echo c > /proc/sysrq-trigger` triggers an immediate panic. Do it only on a test VM or in a maintenance window. The `crashkernel=auto` value used in RHEL 7 and 8 is deprecated in RHEL 9; use `kdumpctl reset-crashkernel` or the explicit size ranges the installer writes. Size `/var/crash` (or point `kdump.conf` to an NFS/SSH target) for at least the size of the compressed dump, which with the default `makedumpfile` filtering is usually a small fraction of RAM.
:::

**Ubuntu/Debian:** `apt install linux-crashdump` (pulls in `kdump-tools`), check with `kdump-config show`; dumps go to `/var/crash/`.

## 9. Recovering cloud VMs you cannot reach

On a cloud VM there is no keyboard to press **e** on, unless you set up the serial console. You have two tools: **the serial console**, which gives you the GRUB menu and emergency shell over the provider's API, and **the rescue VM method**, which works even when the serial console does not.

### Step 0 (always): look at the console output

Before anything else, read what the VM printed. This is read-only and safe:

| Cloud | How |
|---|---|
| AWS | EC2 console → Actions → Monitor and troubleshoot → **Get system log** / **Get instance screenshot**; CLI: `aws ec2 get-console-output --instance-id i-0abc... --latest --output text` |
| Azure | **Boot diagnostics** blade (screenshot and serial log); must be enabled on the VM (managed boot diagnostics is the default on new VMs) |
| OCI | Instance → **Console connection** / console history capture (`oci compute console-history capture`) |

The output tells you the stage: GRUB errors, a kernel panic, a dracut timeout, or "You are in emergency mode".

### Interactive serial consoles

| Cloud | Requirements | How to connect |
|---|---|---|
| **AWS EC2 Serial Console** | Nitro-based instance; serial console access enabled for the account per region (`aws ec2 enable-serial-console-access`); IAM permission `ec2-instance-connect:SendSerialConsoleSSHPublicKey`; an OS user **with a password** to log in at the console | EC2 console → Connect → EC2 serial console |
| **Azure Serial Console** | Boot diagnostics enabled; RBAC role of at least VM Contributor; a user with a password | Portal → VM → Help → **Serial console**, or `az serial-console connect -n <vm> -g <rg>` (needs the `serial-console` CLI extension) |
| **OCI** | A console connection created with your SSH public key | `oci compute instance-console-connection create`, then the SSH command OCI gives you; supports sending a reboot and catching GRUB |

Once connected, reboot the VM from the console session (or the provider's reboot button) and watch for the GRUB menu. Everything in sections 3–7 now works: press **e**, add `rd.break` or `systemd.unit=emergency.target`, and so on, provided GRUB and the kernel are configured for the serial port (they are on official cloud images).

:::tip
**Prepare before you need it.** Serial consoles need a password-authenticated user, and cloud images have none by default. Decide on a policy in advance: either a break-glass local account with a vaulted password on every VM (and console access limited by IAM/RBAC), or accept that `rd.break` (which needs no password) is your only console route. Enabling AWS account-level serial console access at 3 a.m. during an incident, while learning the IAM policy, costs you half an hour you do not have.
:::

### The rescue VM method (attach the disk to another VM)

When the serial console is unavailable or the system is too broken (GRUB gone, `/boot` corrupt), work on the disk from a healthy VM:

1. **Snapshot the broken VM's OS disk first.** Every step after this is reversible only if you have the snapshot.
2. **Stop** the broken VM and **detach** its OS volume (AWS: detach the EBS root volume; Azure: create a copy of the OS disk from the snapshot; OCI: detach the boot volume).
3. **Attach** it to a rescue VM running the **same OS major version**, in the same AZ/region.
4. Mount it, bind-mount the pseudo-filesystems, and `chroot` in.
5. Fix the problem, exit, unmount, detach, reattach as the root/boot volume, start the VM.

Azure automates steps 1–3 and 5 with the vm-repair extension:

```bash
az extension add --name vm-repair
az vm repair create  -g rg-app -n vm-app01 --repair-username rescueadmin --repair-password '<from-vault>' --verbose
# ... SSH to the repair VM, fix the attached copy ...
az vm repair restore -g rg-app -n vm-app01 --verbose     # swaps the fixed disk back in
```

Mounting the broken disk on the rescue VM is where people get stuck, because the rescue VM was usually built from the **same image**:

```bash
lsblk -f
```

```text
NAME          FSTYPE      LABEL UUID                                   MOUNTPOINT
nvme0n1
├─nvme0n1p1   vfat              7B77-95E7                              /boot/efi
├─nvme0n1p2   xfs               95070429-de61-4430-8ad0-2c0f109d8d50   /boot
└─nvme0n1p3   LVM2_member       ...
  └─rl-root   xfs               4b9c1d...                              /
nvme1n1
├─nvme1n1p1   vfat              7B77-95E7
├─nvme1n1p2   xfs               95070429-de61-4430-8ad0-2c0f109d8d50
└─nvme1n1p3   LVM2_member       ...
```

Two traps visible here:

- **Duplicate XFS UUIDs.** Same image, same filesystem UUID. XFS refuses to mount a second filesystem with a UUID already mounted ("Filesystem has duplicate UUID ... - can't mount"). Mount with `-o nouuid`.
- **Duplicate LVM volume group names.** Both disks have a VG called `rl` (or `rhel`). LVM sees duplicates and will not activate the second one cleanly. Rename the broken disk's VG on import with `vgimportclone`, or, better, use a rescue VM from a **different** image (for example a different distribution, or one built without LVM). If you rename the VG, the broken system's `root=`, `rd.lvm.lv=` and fstab still point at the old name, so you must rename it back before reattaching.

A typical chroot session (non-LVM root partition shown; adjust device names to what `lsblk` shows):

```bash
mkdir -p /mnt/rescue
mount -o nouuid /dev/nvme1n1p4 /mnt/rescue           # the broken root
mount -o nouuid /dev/nvme1n1p2 /mnt/rescue/boot      # its /boot
mount /dev/nvme1n1p1 /mnt/rescue/boot/efi            # its ESP (UEFI only)
for d in dev proc sys run; do mount --bind /$d /mnt/rescue/$d; done
chroot /mnt/rescue /bin/bash

# Now you are "inside" the broken system:
vi /etc/fstab                                        # fix fstab
ls /lib/modules                                      # which kernels it has
dracut -f --kver 5.14.0-427.13.1.el9_4.x86_64        # rebuild its initramfs
grubby --set-default=/boot/vmlinuz-5.14.0-362.24.1.el9_3.x86_64
touch /.autorelabel                                  # SELinux: see below

exit
for d in run sys proc dev; do umount /mnt/rescue/$d; done
umount /mnt/rescue/boot/efi /mnt/rescue/boot /mnt/rescue
```

:::concept
**Why `/.autorelabel` again?** Files you create or replace from the rescue VM are labelled by the **rescue VM's** policy for the path they had there (somewhere under `/mnt`), not by the broken system's file contexts. `vi` writes a new file on save, so your fixed `/etc/fstab` may come back as `mnt_t` or `unlabeled_t` on the repaired system. Many files with wrong labels produce mysterious denials after boot. Either `touch /.autorelabel` before you leave the chroot, or, for one or two files, boot and run `restorecon -Rv /etc` straight away.
:::

### Cloud-specific notes

- **AWS:** the root device name you reattach to must match the instance's root device name (`/dev/xvda` or `/dev/sda1`, shown on the instance's Storage tab). Detached EBS volumes can only attach to instances in the same Availability Zone. On Nitro, every EBS volume appears as `/dev/nvme*`, which is another reason fstab must use UUIDs. AWS also offers the **EC2Rescue for Linux** tool and the `AWSSupport-ExecuteEC2Rescue` Systems Manager runbook that automate the rescue-instance flow.
- **Azure:** you cannot detach a running VM's OS disk; `az vm repair` works on a copy, or you swap the OS disk with `az vm update --os-disk`. Azure Linux Agent (`waagent`) and cloud-init are needed for provisioning but not for the VM to boot.
- **OCI:** detach the boot volume from a stopped instance, attach it to a rescue instance as a block volume (iSCSI or paravirtualized), repair, reattach as the boot volume.

## 10. Slow boots: systemd-analyze

A server that takes five minutes to boot turns a two-minute patch reboot into a page. `systemd-analyze` tells you where the time went.

```bash
systemd-analyze
```

```text
Startup finished in 1.912s (kernel) + 3.405s (initrd) + 1min 38.227s (userspace) = 1min 43.545s
multi-user.target reached after 1min 38.110s in userspace.
```

Kernel and initrd are usually seconds; if they are long, look at firmware, storage drivers or LUKS. **Userspace** is systemd, and that is where most problems are. Then:

```bash
systemd-analyze blame | head -15
```

```text
1min 30.004s slow-demo.service
     6.118s NetworkManager-wait-online.service
     3.502s kdump.service
     1.902s dnf-makecache.service
      812ms firewalld.service
      645ms tuned.service
      402ms sshd-keygen@rsa.service
...
```

`blame` lists every unit by how long it took to start. It is a good first look but misleading on its own: units start **in parallel**, and a slow unit that nothing waits for does not delay the boot. `critical-chain` shows the chain that actually determined when the target was reached:

```bash
systemd-analyze critical-chain
```

```text
The time when unit became active or started is printed after the "@" character.
The time the unit took to start is printed after the "+" character.

multi-user.target @1min 38.110s
└─slow-demo.service @8.101s +1min 30.004s
  └─network-online.target @8.096s
    └─NetworkManager-wait-online.service @1.977s +6.118s
      └─NetworkManager.service @1.903s +71ms
        └─network-pre.target @1.901s
```

Read it bottom up: `@` is when the unit became active, `+` how long it took. Here `slow-demo.service` waited for the network and then spent 90 seconds starting; that is the whole problem. You can also ask about a specific unit (`systemd-analyze critical-chain sshd.service`) or draw everything as an SVG timeline (`systemd-analyze plot > /tmp/boot.svg`).

### Common slow-boot causes

| Pattern in output | Cause | Fix |
|---|---|---|
| "A start job is running for dev-disk-by\x2duuid-..." (1min 30s) | fstab entry for a missing device | Fix fstab; `nofail,x-systemd.device-timeout=10s` |
| `NetworkManager-wait-online.service` takes 30–60 s | Interface waiting for DHCP or a link that never comes up | Fix or remove the unused connection (`nmcli con show`), set `ipv4.may-fail`, or reduce the wait; disable the unit only if nothing needs `network-online.target` |
| `cloud-init.service` slow | Metadata service unreachable, datasource probing | Set the correct `datasource_list` in `/etc/cloud/cloud.cfg.d/` |
| A custom app unit slow | App does real work at start, or `Type=forking/notify` waiting for a readiness signal that never comes | Fix the unit type; move slow work out of the boot path |
| `selinux-autorelabel.service` very long, then extra reboot | Someone left `/.autorelabel`, or SELinux was re-enabled after being disabled | Expected once; investigate if it happens every boot |
| `systemd-udev-settle.service` slow | Legacy unit pulled in by old storage tools | Find what requires it: `systemctl list-dependencies --reverse systemd-udev-settle.service` |

:::tip
`systemd-analyze blame` only covers the **current** boot. To compare against a previous boot, use the journal: `journalctl -b -1 -o short-monotonic | less` shows each message with seconds since boot, so you can see where the gaps are. Make the journal persistent (`mkdir -p /var/log/journal && systemctl restart systemd-journald`) on every server; without it, last boot's evidence vanishes on reboot.
:::

## 11. Troubleshooting runbook

| Symptom | Check (command) | Likely cause | Fix |
|---|---|---|---|
| `grub>` or `grub rescue>` prompt | Console output; from rescue: `ls /boot`, `efibootmgr -v` | Missing/corrupt grub.cfg, `/boot` damaged, wrong boot device | Rescue media or rescue VM; `dnf reinstall grub2-efi-x64 shim-x64` (UEFI) or `grub2-install /dev/sda` (BIOS); regenerate config to `/boot/grub2/grub.cfg` |
| "Kernel panic ... unknown-block(0,0)" after update | Boot older kernel; `ls -l /boot/initramfs-*`; `df -h /boot` | Missing initramfs, often full `/boot` | Free `/boot` with `dnf remove`; `dracut -f --kver <ver>` |
| `dracut:/#` "rl-root does not exist" | At prompt: `lvm lvs`, `blkid`, `cat /proc/cmdline` | Wrong `root=`/`rd.lvm.lv=`, renamed VG, missing driver | Fix args with `grubby`; rebuild initramfs with drivers |
| "You are in emergency mode" | `journalctl -xb -p err`, `systemctl --failed`, `findmnt --verify` | Bad fstab, failed fs | Remount `/` rw, fix fstab, `daemon-reload`, `mount -a` |
| "Cannot open access to console, the root account is locked" | — | Root has no password | `rd.break`, `init=/bin/bash`, or rescue VM |
| Root password reset done, logins still fail | `ls -Z /etc/shadow`; `ausearch -m avc -ts recent` | No relabel after `rd.break` | `restorecon -v /etc/shadow` or `touch /.autorelabel` and reboot |
| "Structure needs cleaning" mounting XFS | `dmesg | tail`; `xfs_repair -n` | Filesystem corruption | Snapshot, unmount, mount/umount to replay log, `xfs_repair` (`-L` last resort) |
| New kernel boots but NIC/storage/agent broken | `uname -r`; `journalctl -b -p err`; `lsmod` | Regression or third-party module not built for new kernel | Boot old kernel, `grubby --set-default`, version-lock, report |
| Boot takes minutes | `systemd-analyze`, `critical-chain`, `blame` | Device timeout, wait-online, slow unit | Fix the unit on the critical chain |
| Cloud VM unreachable after reboot | Get system log / boot diagnostics | Any of the above | Serial console; or snapshot + rescue VM + chroot |
| VM boots, extra reboot every time | `ls -l /.autorelabel`; `journalctl -b -1 -u selinux-autorelabel` | Relabel flag recreated or relabel failing | Find what creates it; check `fixfiles` errors |
| Custom unit fails at boot with "Permission denied" | `ls -Z /etc/systemd/system/<unit>`, `ausearch -m avc -ts boot` | Unit file moved with `mv` from home dir keeps `admin_home_t` | `restorecon -v /etc/systemd/system/<unit>`; `systemctl daemon-reload` |

## 12. Break-fix labs

Run these on a **disposable** Rocky Linux 9 or AlmaLinux 9 VM in a local hypervisor (KVM/virt-manager, VirtualBox, VMware, Hyper-V) where you can see the console and the GRUB menu. A RHEL 9 VM on a free Red Hat Developer subscription works the same way. Take a **snapshot** of the VM before each lab. Cloud VMs work only if you have set up the serial console first. SELinux should be **Enforcing** throughout (`getenforce`).

Before starting, make the GRUB menu visible for 5 seconds:

```bash
sudo sed -i 's/^GRUB_TIMEOUT=.*/GRUB_TIMEOUT=5/' /etc/default/grub
sudo grub2-mkconfig -o /boot/grub2/grub.cfg
sudo grub2-editenv - unset menu_auto_hide
```

:::lab Lab 09-A — Reset the root password with rd.break and SELinux relabel
**Goal:** recover root access without knowing the password, see what happens if you skip the relabel, and fix it properly.

**Break it.** Set the root password to something random you will not know:

```bash
sudo bash -c 'echo "root:$(openssl rand -base64 24)" | chpasswd'
sudo -k
```

**Symptoms.** `su -` fails with "Authentication failure". Pretend you have also lost your sudo user.

**Diagnose.** You need a shell that does not require any password: `rd.break`.

**Fix, part 1 (the wrong way, deliberately).**

1. Reboot. At the GRUB menu, highlight the default entry and press **e**.
2. On the `linux` line, delete `rhgb quiet` and add `rd.break` at the end. Press **Ctrl+X**.
3. At `switch_root:/#` (press Enter if prompted):

```bash
mount -o remount,rw /sysroot
chroot /sysroot
passwd root
ls -Z /etc/shadow
exit
exit
```

Note the label printed by `ls -Z`: it is not `shadow_t`. Do **not** create `/.autorelabel`.

4. When the system finishes booting, try to log in as root at the console with the new password. It fails ("Login incorrect") even though the password is right.

**Fix, part 2 (the right way).**

1. Reboot, press **e**, add `rd.break` again.
2. At the prompt:

```bash
mount -o remount,rw /sysroot
chroot /sysroot
touch /.autorelabel
exit
exit
```

3. Watch the console: the relabel runs ("\*\*\* Warning -- SELinux targeted policy relabel is required." followed by progress), then the system reboots by itself.

**Verify.**

```bash
# log in as root at the console with the new password, then:
ls -Z /etc/shadow
getenforce
ausearch -m avc -ts today | grep shadow | tail -3
```

```text
system_u:object_r:shadow_t:s0 /etc/shadow
Enforcing
```

The `ausearch` output shows the denials from the failed login attempt in part 1, with `tcontext=...unlabeled_t`: that is your evidence for *why* the relabel matters. (If the audit log was itself relabelled and rotated, you may see no lines; that is fine.)

**Clean-up/revert.** Set the root password to your normal lab value, or revert to the VM snapshot. Confirm `/.autorelabel` no longer exists (`ls -l /.autorelabel` should fail; the relabel service deletes it).
:::

:::lab Lab 09-B — Recover from a bad /etc/fstab via emergency mode
**Goal:** reproduce the most common "server did not come back after reboot" incident and recover from it with the emergency shell.

**Prerequisite:** the root account must have a password (from Lab 09-A or `sudo passwd root`), because emergency mode asks for it.

**Break it.** Add an fstab line for a disk that does not exist, the way a forgotten detached volume looks:

```bash
sudo cp /etc/fstab /etc/fstab.bak
sudo mkdir -p /data
echo 'UUID=0d4f6a21-9c1e-4b7e-a0f1-3f2b8e6c1d99  /data  xfs  defaults  0 0' | sudo tee -a /etc/fstab
sudo systemctl daemon-reload
sudo findmnt --verify
sudo reboot
```

`findmnt --verify` already reports the unreachable source. Note that in real life nobody ran it.

**Symptoms.** The boot shows "A start job is running for /dev/disk/by-uuid/0d4f6a21..." counting up to 1min 30s, then "Dependency failed for /data", "Dependency failed for Local File Systems", and "You are in emergency mode". SSH does not work.

**Diagnose.** Enter the root password, then:

```bash
systemctl --failed
journalctl -xb -p err | head -30
findmnt --verify
lsblk -f
```

`systemctl --failed` lists `data.mount`; the journal says the device timed out; `lsblk -f` shows no disk with that UUID.

**Fix.**

```bash
mount -o remount,rw /
sed -i 's|^UUID=0d4f6a21-9c1e-4b7e-a0f1-3f2b8e6c1d99|#&|' /etc/fstab
systemctl daemon-reload
findmnt --verify
mount -a
systemctl default
```

`findmnt --verify` should now report 0 errors, `mount -a` returns silently, and `systemctl default` continues to `multi-user.target`.

**Verify.** Log in normally, then confirm a clean boot and no failed units:

```bash
systemctl --failed
systemctl is-system-running
tail -3 /etc/fstab
```

```text
  UNIT LOAD ACTIVE SUB DESCRIPTION
0 loaded units listed.
running
```

**Extension (prevention).** Change the commented line back to active but add `nofail,x-systemd.device-timeout=10s` to its options and reboot. The boot now waits 10 seconds, logs a failure for `data.mount`, and continues to the login prompt. Check with `systemd-analyze critical-chain` that the boot was not held up.

**Clean-up/revert.**

```bash
sudo cp /etc/fstab.bak /etc/fstab
sudo systemctl daemon-reload
sudo rmdir /data
```
:::

:::lab Lab 09-C — Boot the previous kernel after a bad update and set it as default
**Goal:** simulate a kernel update that leaves the newest kernel unbootable, fall back to the older kernel from GRUB, make it the default, then repair the new kernel properly.

**Prerequisite:** at least **two** kernels installed. Check with `rpm -q kernel`. If you have only one, run `sudo dnf upgrade kernel` (a fresh VM from an older ISO usually has an update available), then reboot into the new one. If no newer kernel exists in the repos, install the next-older one from your distribution's vault repository and adapt the steps.

**Break it.** Make the newest (default) kernel's initramfs disappear, which is exactly what a full `/boot` during an update causes:

```bash
NEW=$(grubby --default-kernel | sed 's|/boot/vmlinuz-||')
echo "Default kernel: $NEW"
sudo mv /boot/initramfs-$NEW.img /root/initramfs-$NEW.img.bak
sudo reboot
```

**Symptoms.** The console shows `Kernel panic - not syncing: VFS: Unable to mount root fs on unknown-block(0,0)` and the VM stops (or reboots in a loop if `kernel.panic` is set).

**Diagnose.** The panic happens before any root filesystem exists, so the problem is the kernel or initramfs, not fstab or services. Reset the VM, and at the GRUB menu, use the arrow keys to select the **second** entry (the older kernel). It boots normally. Then:

```bash
uname -r
grubby --default-kernel
ls -l /boot/initramfs-*
```

`uname -r` shows the older version, the default still points at the new kernel, and the new kernel has no initramfs file.

**Fix, part 1: make the working kernel the default** so an unattended reboot is safe:

```bash
OLD=$(uname -r)
sudo grubby --set-default=/boot/vmlinuz-$OLD
grubby --default-kernel
```

At this point production is stable. In a real incident you would stop here, version-lock the kernel if the new one has a genuine bug, and open a ticket.

**Fix, part 2: repair the new kernel.** (Here the "bug" is the missing initramfs; rebuild it rather than restoring the backup, as you would have to in real life.)

```bash
sudo dracut -f --kver $NEW
lsinitrd /boot/initramfs-$NEW.img | head -5
sudo grub2-reboot "$(grubby --info=/boot/vmlinuz-$NEW | awk -F'"' '/^id=/{print $2}')"
sudo reboot
```

`grub2-reboot` boots the new kernel **once**. If it fails, a reset brings you back to the old default automatically.

**Verify.** After the one-time boot:

```bash
uname -r                 # should be the new kernel
sudo grubby --set-default=/boot/vmlinuz-$(uname -r)
grubby --default-kernel
```

Only now, after the new kernel has proven itself, set it back as the default.

**Clean-up/revert.** `sudo rm /root/initramfs-*.img.bak`. Revert to the VM snapshot if anything is unclear.
:::

:::lab Lab 09-D — Slow boot analysis with systemd-analyze (and an SELinux gotcha)
**Goal:** find what delays the boot using `blame` and `critical-chain`, and meet a real SELinux mistake with custom unit files along the way.

**Break it.** Write a deliberately slow unit in your home directory and move it into place, the way many admins do:

```bash
cat > ~/slow-demo.service <<'EOF'
[Unit]
Description=Slow demo service (simulates an app doing work at boot)
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/sleep 45
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
sudo mv ~/slow-demo.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable slow-demo.service
```

**Symptoms, part 1.** Check the state:

```bash
systemctl status slow-demo.service
ls -Z /etc/systemd/system/slow-demo.service
```

Depending on the policy version, `enable` or the later boot may fail with "Access denied" or "Unit not found", and `ls -Z` shows `unconfined_u:object_r:user_home_t:s0` (or `admin_home_t` if you worked as root in `/root`). `mv` **keeps** the original SELinux label; `cp` or creating the file in place would have given it the correct `systemd_unit_file_t`. Even where it happens to work, a wrong label is a time bomb for later policy updates.

**Fix, part 1.**

```bash
sudo restorecon -v /etc/systemd/system/slow-demo.service
sudo systemctl daemon-reload
sudo systemctl enable slow-demo.service
sudo reboot
```

```text
Relabeled /etc/systemd/system/slow-demo.service from unconfined_u:object_r:user_home_t:s0 to unconfined_u:object_r:systemd_unit_file_t:s0
```

**Symptoms, part 2.** The VM takes noticeably longer to accept SSH. 

**Diagnose.**

```bash
systemd-analyze
systemd-analyze blame | head -5
systemd-analyze critical-chain
systemd-analyze critical-chain sshd.service
```

`systemd-analyze` shows a userspace time around 50 seconds. `blame` puts `slow-demo.service` at the top with ~45 s. `critical-chain` (for `multi-user.target`) shows it on the chain with `+45.0s`. Compare with `critical-chain sshd.service`: sshd itself was ready after a few seconds, so SSH was available early, but `multi-user.target` (and anything ordered after it, such as monitoring that checks "boot finished") waited. Also look at `NetworkManager-wait-online.service` in `blame`: note its time; it is on the chain only because `slow-demo` asked for `network-online.target`.

Optional: `systemd-analyze plot > /tmp/boot.svg`, copy it to your workstation and open it in a browser to see the parallel timeline.

**Fix, part 2.** In real life you would fix the application's start-up work, or remove an unnecessary dependency. Here, disable the demo:

```bash
sudo systemctl disable --now slow-demo.service
sudo reboot
```

**Verify.**

```bash
systemd-analyze
systemd-analyze critical-chain | head -8
```

The userspace time drops back to a few seconds and `slow-demo.service` is gone from the chain.

**Clean-up/revert.**

```bash
sudo rm /etc/systemd/system/slow-demo.service
sudo systemctl daemon-reload
```
:::

## Command cheat sheet

| Task | Command |
|---|---|
| BIOS or UEFI? | `[ -d /sys/firmware/efi ] && echo UEFI \|\| echo BIOS` |
| UEFI boot entries | `efibootmgr -v` |
| Current kernel / args | `uname -r`; `cat /proc/cmdline` |
| Default kernel | `grubby --default-kernel`; `grubby --default-index` |
| List all entries | `grubby --info=ALL` |
| Set default kernel | `grubby --set-default=/boot/vmlinuz-<ver>` |
| Boot an entry once | `grub2-reboot <index\|id>` then `reboot` |
| Add / remove kernel args | `grubby --update-kernel=ALL --args="..."` / `--remove-args="..."` |
| Regenerate GRUB config (RHEL 9, BIOS and UEFI) | `grub2-mkconfig -o /boot/grub2/grub.cfg` |
| Regenerate GRUB config (RHEL 8 UEFI) | `grub2-mkconfig -o /boot/efi/EFI/<vendor>/grub.cfg` |
| Ubuntu GRUB / initramfs | `update-grub`; `update-initramfs -u -k all` |
| GRUB password | `grub2-setpassword` |
| Root password reset | GRUB `e`, add `rd.break`; `mount -o remount,rw /sysroot; chroot /sysroot; passwd root; touch /.autorelabel; exit; exit` |
| Emergency / rescue boot | `systemd.unit=emergency.target` / `systemd.unit=rescue.target` |
| Stop before root mount | `rd.break=pre-mount` |
| Fix single file label | `restorecon -v /etc/shadow` |
| Full relabel on next boot | `touch /.autorelabel` (or `fixfiles -F onboot`) |
| Verify fstab | `findmnt --verify`; `mount -a` |
| After editing fstab | `systemctl daemon-reload` |
| Rebuild initramfs | `dracut -f`; `dracut -f --kver <ver>`; `dracut -f --regenerate-all` |
| Inspect initramfs | `lsinitrd /boot/initramfs-<ver>.img` |
| Remove old kernels | `dnf remove --oldinstallonly --setopt installonly_limit=2 kernel` |
| Lock a kernel version | `dnf versionlock add kernel-<ver>` |
| XFS check / repair | `xfs_repair -n <dev>`; `xfs_repair <dev>`; `-L` last resort |
| ext4 check / repair | `e2fsck -f <dev>`; `e2fsck -fy <dev>` |
| Previous boot's errors | `journalctl -b -1 -p err` |
| kdump | `kdumpctl reset-crashkernel --kernel=ALL`; `systemctl enable --now kdump`; `kdumpctl status` |
| Auto-reboot on panic | `sysctl -w kernel.panic=10` (persist in `/etc/sysctl.d/`) |
| Boot time | `systemd-analyze`; `systemd-analyze blame`; `systemd-analyze critical-chain [unit]`; `systemd-analyze plot > boot.svg` |
| AWS console output | `aws ec2 get-console-output --instance-id <id> --latest --output text` |
| Azure repair VM | `az vm repair create ...` / `az vm repair restore ...` |
| chroot into a mounted disk | `for d in dev proc sys run; do mount --bind /$d /mnt/rescue/$d; done; chroot /mnt/rescue` |
| Mount clone disk with same XFS UUID | `mount -o nouuid <dev> <dir>` |

:::summary
- Know the **boot chain**: firmware → GRUB2 → kernel + initramfs → systemd → target. The console output tells you which stage failed; fix it at that stage.
- On RHEL 9, kernels have **BLS entries** and **grubby** is the tool: `--default-kernel`, `--set-default`, `--update-kernel=ALL --args`. `grub2-mkconfig` writes to **/boot/grub2/grub.cfg on both BIOS and UEFI**; the EFI `grub.cfg` is a stub.
- At GRUB, **e** edits and **Ctrl+X** boots once. `rd.break` stops in the initramfs with no password; `emergency.target` gives read-only `/` with the root password; `rescue.target` needs all local filesystems; `init=/bin/bash` is the last resort.
- **Root password reset**: `rd.break`, remount `/sysroot` rw, `chroot`, `passwd`, **`touch /.autorelabel`**. Without the relabel, `/etc/shadow` is unlabeled and SELinux blocks every login. The fast alternative is `enforcing=0` for one boot plus `restorecon -v /etc/shadow`, then back to enforcing.
- **Emergency mode** is usually **fstab**: remount rw, fix, `daemon-reload`, `findmnt --verify`, `mount -a`. Prevent with UUIDs, `nofail`, `x-systemd.device-timeout=`, `_netdev`, and testing before reboot.
- After a **bad kernel**, boot the previous entry, `grubby --set-default` it, lock the version, then investigate. `unknown-block(0,0)` usually means a missing initramfs, often from a **full /boot**: free space with `dnf remove`, rebuild with `dracut -f --kver`.
- Repair filesystems **unmounted**: `xfs_repair -n` first, `-L` only as a last resort after a snapshot; `e2fsck -f` for ext4.
- Enable **kdump** and `kernel.panic` on servers so panics are recorded and the box reboots itself.
- For **cloud VMs**, read the console log, then use the serial console (prepare a password user in advance) or snapshot + rescue VM + `chroot`, watching for duplicate XFS UUIDs and LVM VG names, and relabel afterwards.
- For slow boots, `systemd-analyze critical-chain` beats `blame`: fix what is on the chain.
:::

## Self-check questions

<div class="q" markdown="1">

**Q1.** You reset the root password on a RHEL 9 server using `rd.break`, `chroot /sysroot` and `passwd`, then rebooted. The system boots, but root login fails with "Login incorrect" and the audit log shows denials with `tcontext=...unlabeled_t` for `unix_chkpwd`. What was missed?

A. The password must be set with `chpasswd`, not `passwd`
B. `touch /.autorelabel` before leaving the chroot
C. `systemctl daemon-reload` inside the chroot
D. `setenforce 0` must be added permanently to `/etc/rc.local`

:::answer
**Answer: B.** In the `rd.break` shell no SELinux policy is loaded, so the new `/etc/shadow` is not labelled `shadow_t`. The relabel (or `restorecon -v /etc/shadow` after a one-time `enforcing=0` boot) fixes it.

- A is wrong: both tools write `/etc/shadow` the same way; the label is the problem.
- C is wrong: systemd is not running in the chroot, and unit files are not involved.
- D is wrong: permanently disabling enforcement hides the problem and removes protection; never the fix.
:::
</div>

<div class="q" markdown="1">

**Q2.** After a patch reboot, a VM shows "Timed out waiting for device /dev/disk/by-uuid/..." then "You are in emergency mode". You log in as root. What is the correct sequence?

A. `fsck -y /` then `reboot`
B. `mount -o remount,rw /`, fix `/etc/fstab`, `systemctl daemon-reload`, `mount -a`, `systemctl default`
C. `setenforce 0` then `systemctl default`
D. `dracut -f` then `reboot`

:::answer
**Answer: B.** The device in an fstab entry is missing. `/` is read-only in emergency mode, so remount it, fix or comment the line, reload so systemd regenerates mount units, test with `mount -a`, then continue.

- A is wrong: the root filesystem is not damaged, and fsck on a mounted root is dangerous.
- C is wrong: SELinux has nothing to do with a missing device.
- D is wrong: the initramfs worked (root was mounted); the failure is later, in systemd's fstab processing.
:::
</div>

<div class="q" markdown="1">

**Q3.** On a RHEL 9 UEFI server you changed `GRUB_TIMEOUT` in `/etc/default/grub`. Which command regenerates the GRUB configuration correctly?

A. `grub2-mkconfig -o /boot/efi/EFI/redhat/grub.cfg`
B. `grub2-mkconfig -o /boot/grub2/grub.cfg`
C. `update-grub`
D. `grub2-install /dev/sda`

:::answer
**Answer: B.** On RHEL 9 the main config is `/boot/grub2/grub.cfg` on both BIOS and UEFI; the EFI file is a stub that loads it.

- A is wrong: that was the RHEL 7/8 UEFI path; on RHEL 9 it overwrites the stub.
- C is wrong: `update-grub` is the Debian/Ubuntu wrapper.
- D is wrong: `grub2-install` writes boot code for BIOS systems; it does not regenerate the config and is not used this way on UEFI.
:::
</div>

<div class="q" markdown="1">

**Q4.** A kernel update last night made the server's network driver fail. You booted the previous kernel from the GRUB menu at the console. What should you do next so an unplanned reboot does not bring the broken kernel back?

A. `rm /boot/vmlinuz-<new>`
B. `grubby --set-default=/boot/vmlinuz-$(uname -r)` and version-lock or exclude the kernel until fixed
C. `dracut -f`
D. Nothing; GRUB remembers the last manual choice by default

:::answer
**Answer: B.** Make the working kernel the saved default and stop updates from reinstating the bad one while you investigate.

- A is wrong: deleting files bypasses RPM and leaves stale BLS entries and package state.
- C is wrong: rebuilding the initramfs of the running kernel does nothing for the default.
- D is wrong: RHEL boots the saved default, which is still the new kernel; a manual menu choice is not saved.
:::
</div>

<div class="q" markdown="1">

**Q5.** A server panics at boot with "VFS: Unable to mount root fs on unknown-block(0,0)" right after a kernel update. The previous kernel boots fine. What do you check first?

A. SELinux booleans
B. Whether `/boot/initramfs-<new>.img` exists and whether `/boot` is full
C. `/etc/fstab` for the data volumes
D. The NetworkManager configuration

:::answer
**Answer: B.** `unknown-block(0,0)` means the kernel had no root device at all, typically because the initramfs is missing or broken; a full `/boot` during the update is the classic cause.

- A is wrong: SELinux policy is not even loaded when this panic happens.
- C is wrong: fstab is processed by systemd much later; data volumes cannot cause a root mount panic.
- D is wrong: networking is irrelevant before root is mounted (unless root is on the network, which is not the case here).
:::
</div>

<div class="q" markdown="1">

**Q6.** You try `systemd.unit=emergency.target` on a cloud image to fix fstab, and the console prints "Cannot open access to console, the root account is locked". What is the quickest way in?

A. Press Ctrl+D repeatedly
B. Use `rd.break` (or `init=/bin/bash`), which does not require a password
C. Use `systemd.unit=rescue.target` instead
D. Add `enforcing=0`

:::answer
**Answer: B.** `rd.break` stops in the initramfs before any login is required; mount `/sysroot` read-write and fix fstab there (then relabel or restorecon what you changed).

- A is wrong: Ctrl+D just continues booting into the same failure.
- C is wrong: rescue mode also needs the root password and also requires fstab mounts to succeed.
- D is wrong: SELinux mode does not affect whether root has a password.
:::
</div>

<div class="q" markdown="1">

**Q7.** An XFS data volume fails to mount with "Structure needs cleaning". `xfs_repair` says the log contains metadata changes that need replaying. What is the right next step?

A. Run `xfs_repair -L` immediately
B. Try to mount and unmount the filesystem to replay the log, then run `xfs_repair`; use `-L` only if mounting fails, after a snapshot
C. Run `e2fsck -fy` on the volume
D. Run `xfs_repair` while the filesystem is mounted read-write

:::answer
**Answer: B.** Replaying the log keeps the latest metadata. `-L` discards it and can lose recent changes, so it is a last resort after a snapshot.

- A is wrong: skipping the log replay throws away data unnecessarily.
- C is wrong: `e2fsck` is for ext2/3/4, not XFS.
- D is wrong: never repair a mounted read-write filesystem.
:::
</div>

<div class="q" markdown="1">

**Q8.** An AWS instance is unreachable after a reboot. The EC2 Serial Console is not enabled and nobody has a console password. You decide to use a rescue instance built from the same AMI. Mounting the broken root fails with "Filesystem has duplicate UUID". What do you do?

A. Run `xfs_admin -U generate` on the rescue instance's own root
B. Mount the broken filesystem with `-o nouuid`
C. Format the broken volume and restore from backup
D. Attach the volume in a different Availability Zone

:::answer
**Answer: B.** Both disks came from the same image, so their XFS UUIDs match; `nouuid` lets you mount the second one temporarily without changing anything.

- A is wrong: changing the rescue root's UUID can break the rescue instance's own fstab and boot.
- C is wrong: destroys data that is almost certainly fine.
- D is wrong: EBS volumes can only attach to instances in the same AZ, and AZ has nothing to do with UUIDs.
:::
</div>

<div class="q" markdown="1">

**Q9.** You fixed `/etc/fstab` on a broken disk from a chroot on an Azure repair VM and swapped the disk back. The VM boots, but several services log SELinux denials on files under `/etc`. Why, and what is the fix?

A. The repair VM uses a different kernel; rebuild the initramfs
B. Files written from the rescue VM got labels based on the rescue VM's paths; run `restorecon -Rv /etc` or `touch /.autorelabel` and reboot
C. Azure disables SELinux on repaired disks; set `SELINUX=enforcing`
D. Run `setsebool -P allow_all on`

:::answer
**Answer: B.** Editing creates new files whose labels come from the rescue system's policy for `/mnt/...`, not the target's contexts. Restore labels or relabel.

- A is wrong: the initramfs does not affect file labels.
- C is wrong: Azure does not change SELinux mode on disks.
- D is wrong: no such boolean exists, and blanket permissions are never the answer.
:::
</div>

<div class="q" markdown="1">

**Q10.** A server takes almost two minutes to boot. `systemd-analyze blame` shows `dnf-makecache.service` at 25 s and `app-warmup.service` at 70 s. `systemd-analyze critical-chain` shows `app-warmup.service` on the chain to `multi-user.target`, but not `dnf-makecache`. Where should you focus?

A. `dnf-makecache.service`, because it appears in blame
B. `app-warmup.service`, because it is on the critical chain
C. The kernel, because blame does not show it
D. Both equally

:::answer
**Answer: B.** Units run in parallel; only those on the critical chain delay the target. `dnf-makecache` runs alongside without holding anything up.

- A is wrong: blame shows duration, not impact on boot completion.
- C is wrong: `systemd-analyze` without arguments shows kernel time separately, and nothing suggests it is slow.
- D is wrong: optimising a unit off the chain does not shorten the boot.
:::
</div>

<div class="q" markdown="1">

**Q11.** You need to boot a newly installed kernel on a remote server to test it, but if it hangs you want a simple reset to return to the current kernel. What do you use?

A. `grubby --set-default` to the new kernel
B. `grub2-reboot <new entry>` then `reboot`
C. Edit `/etc/default/grub` and set `GRUB_DEFAULT=0`
D. `dracut -f --kver <new>`

:::answer
**Answer: B.** `grub2-reboot` sets the entry for the next boot only; after that, the saved default is used again. Set the new kernel as default only after it boots successfully.

- A is wrong: makes the change permanent, so a hang followed by a reset boots the bad kernel again.
- C is wrong: also permanent, and needs a config rebuild.
- D is wrong: rebuilds the initramfs but does not choose what boots.
:::
</div>

<div class="q" markdown="1">

**Q12.** A server panicked twice this month with no clear cause; each time it hung until someone power-cycled it. What should you configure so the next panic is both shorter and diagnosable?

A. `enforcing=0` in the kernel arguments
B. kdump (crashkernel reservation, `kdump` service enabled) and `kernel.panic=10`
C. `systemd.debug-shell=1` permanently
D. Increase `installonly_limit` to 10

:::answer
**Answer: B.** kdump saves a `vmcore` and `vmcore-dmesg.txt` to `/var/crash` for root cause analysis, and `kernel.panic=10` makes the box reboot itself after 10 seconds (kdump reboots after capturing as well).

- A is wrong: SELinux does not cause kernel panics and disabling it removes protection.
- C is wrong: leaves an unauthenticated root shell on tty9 and captures nothing after a panic.
- D is wrong: more installed kernels do not explain or shorten a panic, and fill `/boot`.
:::
</div>
