<div class="cover" markdown="1">
<div class="mod">Module 03 · Storage & Filesystems</div>

# Storage and Filesystems: Disks, LVM, fstab, NFS and Full-Disk Incidents

<p>How a disk becomes a mounted filesystem on RHEL 9, how to find out why it is full (or out of inodes, or read-only), how to grow it online without downtime, and how to recover a server that will not boot because of one bad line in /etc/fstab. With SELinux on NFS and on new mount points throughout.</p>
</div>

## What you'll learn

- The storage stack on a RHEL-family server: **block device → partition → LVM → filesystem → mount point**, and the commands that show each layer (`lsblk`, `blkid`, `findmnt`)
- Reading **`df -h` vs `df -i`**, using **`du`** properly, and explaining a **du/df mismatch** caused by deleted-but-open files (`lsof +L1`)
- Finding big files and directories fast, and diagnosing **inode exhaustion**
- Partitioning with **fdisk, gdisk and parted**, and the full **LVM** workflow: create PV/VG/LV, extend an LV and grow the filesystem (**`xfs_growfs` vs `resize2fs`**, **`lvextend -r`**), and growing a **cloud disk** with **`growpart`**
- Why **XFS cannot shrink**, and what that means for your capacity planning
- **/etc/fstab** done right: UUIDs, options, **`nofail`**, testing with `mount -a` and `findmnt --verify`, and recovering a server stuck in **emergency mode**
- **Swap**, mount options such as **`noexec`**, **/tmp and tmpfs**
- **NFS client mounts** (hard vs soft, `nfs-utils`, `showmount`, stale file handles) and **SELinux on NFS** (`nfs_t`, `use_nfs_home_dirs`, `httpd_use_nfs`, the `context=` mount option)
- **Read-only filesystems** after errors, and safe repair with **`xfs_repair`** and **`e2fsck`**
- Four break-fix labs: a disk at 100% from a deleted log file, inode exhaustion, extending the LVM root online, and recovering from a bad fstab entry

**Why it matters in production:** "disk full" is one of the most common pages an on-call engineer gets, and it rarely means what it first looks like. Databases stop accepting writes, logs stop, package updates fail half way, SSH logins hang because the shell cannot write history. The storage layer is also where a careless change (an fstab typo, a shrink attempt, a `soft` NFS mount) turns a routine ticket into an outage or a server that will not boot. Knowing the stack lets you fix these in minutes, online, without guessing.

## 1. The storage stack in one picture

Every filesystem you see in `df` sits on a chain of layers. When something goes wrong, your first job is to work out **which layer** is the problem.

```text
 Physical / virtual disk      /dev/sda, /dev/vda, /dev/nvme0n1, /dev/xvdf
        │
 Partition table (GPT/MBR)    /dev/sda1, /dev/vda2, /dev/nvme0n1p3
        │
 LVM physical volume (PV)     pvcreate /dev/vda3
        │
 LVM volume group (VG)        rl  (Rocky), almalinux (Alma), rhel (RHEL)
        │
 LVM logical volume (LV)      /dev/rl/root  ==  /dev/mapper/rl-root
        │
 Filesystem                   XFS (RHEL default) or ext4
        │
 Mount point                  /, /var, /home, /data  (from /etc/fstab or systemd)
```

Not every server uses every layer. Cloud images often put XFS straight onto a partition with no LVM. A data disk may have a filesystem directly on the whole device with no partition. A RHEL 9 install from the DVD with default settings gives you `/boot` on a plain partition (and `/boot/efi` on UEFI), and `/`, `/home` (on larger disks) and swap as logical volumes in one volume group.

:::concept
**Each layer has its own size, and they must be grown in order, bottom to top.** If a cloud provider grows the disk from 50 GiB to 100 GiB, nothing above it changes automatically. You grow the partition (`growpart`), then the PV (`pvresize`), then the LV (`lvextend`), then the filesystem (`xfs_growfs` or `resize2fs`). Skipping one layer is the number one reason "I resized the disk but df still shows the old size".
:::

## 2. Seeing what you have: lsblk, blkid and findmnt

### lsblk: the tree of block devices

`lsblk` reads from sysfs and shows how devices nest. It is safe, fast, and the first command to run on any storage ticket.

```bash
lsblk
```

```text
NAME        MAJ:MIN RM  SIZE RO TYPE MOUNTPOINTS
sr0          11:0    1 1024M  0 rom
vda         252:0    0   40G  0 disk
├─vda1      252:1    0    1M  0 part
├─vda2      252:2    0    1G  0 part /boot
└─vda3      252:3    0   39G  0 part
  ├─rl-root 253:0    0   35G  0 lvm  /
  └─rl-swap 253:1    0    4G  0 lvm  [SWAP]
vdb         252:16   0   20G  0 disk
```

How to read it: `vda` is a 40 GiB disk with three partitions. `vda1` is a 1 MiB BIOS boot partition (GPT on BIOS systems), `vda2` is `/boot`, and `vda3` is an LVM PV holding the `rl` volume group with two LVs. `vdb` is a brand-new 20 GiB disk with nothing on it: no partitions, no mount point. `RO 1` would mean the device itself is read-only.

Useful variants:

```bash
lsblk -f                         # add filesystem type, label, UUID, free space
lsblk -o NAME,SIZE,TYPE,FSTYPE,UUID,MOUNTPOINTS
lsblk -d -o NAME,SIZE,ROTA,MODEL # disks only; ROTA=1 means spinning disk
```

```text
NAME        FSTYPE      FSVER    LABEL UUID                                   FSAVAIL FSUSE% MOUNTPOINTS
vda
├─vda1
├─vda2      xfs                        5d1c3a0e-2f1b-4f61-9a3e-6b7c1d2e9f10    780.4M    22% /boot
└─vda3      LVM2_member LVM2 001       Yc2kQe-....-3hXv
  ├─rl-root xfs                        0b7e4a52-91c8-4e0a-8f2b-2c6d3a9e7b44     29.1G    17% /
  └─rl-swap swap        1              8f0d2c11-7a5e-4b3c-9d62-1e4f5a6b7c88                  [SWAP]
vdb
```

:::tip
Device names like `/dev/sdb` or `/dev/nvme1n1` are **not stable**. They are assigned in discovery order and can change after a reboot, a new disk, or a cloud instance type change (NVMe ordering is notorious on cloud VMs). Use names only to look; use **UUIDs** (or LVM paths like `/dev/rl/root`) to configure.
:::

### blkid: what is on a device

`blkid` probes a device for a filesystem or other signature and prints its type and identifiers.

```bash
blkid
blkid /dev/vda2
```

```text
/dev/vda2: UUID="5d1c3a0e-2f1b-4f61-9a3e-6b7c1d2e9f10" TYPE="xfs" PARTUUID="a3f1c2d4-02"
```

`UUID` is the filesystem UUID (what fstab uses). `PARTUUID` is the partition's identifier in the partition table, which is different. A blank `blkid` result for a device means there is no recognisable signature: it is empty, or it holds something blkid does not know about. Run `blkid` as root; as a normal user it may show cached or partial results.

:::warn
Before you run `mkfs`, `pvcreate` or `fdisk` on a device, **run `lsblk -f` and `blkid` on it and make sure it is empty**. Formatting the wrong disk because names shifted after a reboot is a classic and unrecoverable mistake. `wipefs /dev/vdb` (without `-a`) lists signatures without removing them.
:::

### findmnt: what is mounted, and how

`findmnt` shows the mount tree with source, type and options. It is easier to read than `mount` and can query a single path.

```bash
findmnt /
findmnt -T /var/log/messages     # which filesystem holds this file?
findmnt -t xfs,ext4,nfs4         # filter by type
```

```text
TARGET SOURCE              FSTYPE OPTIONS
/      /dev/mapper/rl-root xfs    rw,relatime,seclabel,attr2,inode64,logbufs=8,logbsize=32k,noquota
```

The `seclabel` option means the filesystem supports SELinux labels stored in extended attributes, which is what you want for local XFS and ext4. If you see `ro` where you expect `rw`, jump to section 12.

## 3. Where did the space go? df, du and the deleted-file trap

### df -h: blocks per filesystem

```bash
df -h
df -hT          # add the filesystem type
df -h /var      # only the filesystem that holds /var
```

```text
Filesystem          Size  Used Avail Use% Mounted on
devtmpfs            4.0M     0  4.0M   0% /dev
tmpfs               1.8G     0  1.8G   0% /dev/shm
tmpfs               732M  8.6M  724M   2% /run
/dev/mapper/rl-root  35G   35G   20K 100% /
/dev/vda2           960M  210M  751M  22% /boot
tmpfs               366M     0  366M   0% /run/user/1000
```

`df` asks the **filesystem** how many blocks are in use. That number includes everything the filesystem has allocated, whether or not you can still see it in a directory listing. On ext4, about 5% of blocks are reserved for root by default, so `Used + Avail` can be less than `Size` and a filesystem can reach "100%" for normal users while root can still write a little.

### df -i: inodes per filesystem

Every file, directory, symlink and socket uses one **inode**. A filesystem can run out of inodes while it still has plenty of free blocks.

```bash
df -i
```

```text
Filesystem            Inodes   IUsed  IFree IUse% Mounted on
/dev/mapper/rl-root 18348032  412116 17935916    3% /
/dev/vdb1            1310720 1310720       0  100% /data
```

Read the `IUse%` column. 100% here means "No space left on device" errors even if `df -h` shows gigabytes free. Section 4 covers this.

### du: walk the tree and add it up

`du` walks directories and sums the sizes of the files it can see.

```bash
du -sh /var/log                          # one total
du -xh --max-depth=1 / | sort -h         # top-level dirs on the root fs only
du -xh -d1 /var | sort -h | tail -15     # same thing, short options
```

```text
4.0K    /var/adm
...
1.2G    /var/cache
2.9G    /var/lib
27G     /var/log
31G     /var
```

The `-x` flag (`--one-file-system`) is essential: it stops `du` from crossing into other mounted filesystems (like `/proc`, an NFS share or a separate `/home`), so the numbers you add up actually belong to the filesystem that is full. Keep drilling down with `du -xh -d1` one directory at a time until you find the culprit.

### When du and df disagree

Sometimes `df` says `/` is 100% used (35 GiB), but `du -xsh /` adds up to only 9 GiB. Where are the other 26 GiB? The usual causes, in order of likelihood:

| Cause | How to confirm | Fix |
|---|---|---|
| **A deleted file still held open by a process** | `lsof +L1` or `lsof -nP +L1 /` shows `(deleted)` files with big sizes | Restart (or signal) the process, or truncate the file through `/proc/PID/fd/N` |
| Files hidden **under a mount point** (written to `/data` while the disk was not mounted, then a disk mounted over it) | Bind-mount `/` elsewhere and look: `mount --bind / /mnt/rootonly; du -sh /mnt/rootonly/data` | Unmount (or use the bind mount), move or delete the hidden files |
| ext4 reserved blocks (5% by default) | `tune2fs -l /dev/X \| grep -i 'reserved block'` | Usually leave it; for big data-only volumes, `tune2fs -m 1` |
| Snapshots, filesystem metadata, or the XFS log | Small, constant overhead; LVM snapshots show in `lvs` | Remove stale snapshots |

### Deleted but open: why it happens

When you `rm` a file, you remove a **name** (a directory entry). The data blocks are freed only when the **last name is gone and no process has the file open**. A daemon that opened `/var/log/myapp/app.log` and keeps writing to it will keep the inode, and every block in it, alive after you delete the file. `du` cannot see it (there is no name to walk to), but `df` still counts it.

```bash
lsof +L1                       # open files with a link count below 1 (i.e. deleted)
lsof -nP +L1 / 2>/dev/null     # only on the root filesystem, no DNS/port lookups
```

```text
COMMAND   PID   USER   FD   TYPE DEVICE    SIZE/OFF NLINK     NODE NAME
java     2214 myapp    5w   REG  253,0 27894211584     0  1835127 /var/log/myapp/app.log (deleted)
```

Read it: process `java`, PID 2214, has file descriptor **5** open for **w**riting on a deleted file that is about 26 GiB. `NLINK 0` confirms no names remain. Install `lsof` with `dnf install lsof` if it is missing (it is not always in minimal images). Without `lsof`, you can find the same thing in `/proc`:

```bash
find /proc/[0-9]*/fd -lname '*(deleted)' -printf '%p -> %l\n' 2>/dev/null
```

**Two ways to free the space:**

1. **Restart or reload the process** so it closes the old file and opens a new one. `systemctl restart myapp` is the clean fix. Many daemons reopen their log files on a signal (`systemctl reload`, or `kill -HUP` for some, `kill -USR1` for nginx).
2. **Truncate the file through the process's file descriptor**, when you cannot restart right now:

```bash
: > /proc/2214/fd/5            # truncate to zero bytes; the process keeps writing
# or
truncate -s 0 /proc/2214/fd/5
```

This frees the blocks immediately without restarting anything. The data in that log is gone, so copy what you need first (`cp /proc/2214/fd/5 /some/other/fs/app.log.saved`) if the log matters for the incident.

:::warn
**Do not `rm` a log file that a running process is writing to** to "free space". It frees nothing and hides the file from `du`. To empty a live log, truncate it in place: `truncate -s 0 /var/log/myapp/app.log` (or `: > file`). The real fix is logrotate with the right method: a `postrotate` script that tells the process to reopen its logs, or `copytruncate` for applications that cannot reopen.
:::

:::prod
**The 3 a.m. disk that would not empty.** Symptom: an alert fires, `/` on an app server is at 100%, and the application starts returning HTTP 500 because it cannot write session files. The on-call engineer finds a 24 GiB `app.log` in `/var/log/myapp`, runs `rm` on it, and checks again: still 100%. `du -xsh /` says 8 GiB used out of 35. Root cause: a logrotate config used `create` but no `postrotate` signal, and a previous engineer had deleted the rotated file by hand; the JVM still held it open. `lsof +L1` showed the 24 GiB deleted file on fd 5. Fix: truncated through `/proc/<pid>/fd/5` to stop the bleeding, then a planned restart in the next maintenance window. Prevention: a logrotate rule with `copytruncate` (the app could not reopen logs on signal), plus a disk-usage alert at 80% instead of 95%, and a runbook line: "never `rm` live logs".
:::

### Finding big files and directories fast

```bash
# Biggest directories on the full filesystem only (-x), human sorted
du -xh / 2>/dev/null | sort -rh | head -20

# Files bigger than 500 MiB on the root filesystem only
find / -xdev -type f -size +500M -exec ls -lh {} + 2>/dev/null | sort -k5 -rh | head

# Recently grown files: modified in the last 60 minutes and over 100 MiB
find /var -xdev -type f -mmin -60 -size +100M -ls 2>/dev/null

# The usual suspects
journalctl --disk-usage
dnf clean all            # cached packages under /var/cache/dnf
ls -lh /var/crash /var/lib/systemd/coredump 2>/dev/null
```

```text
Archived and active journals take up 3.9G in the file system.
```

Common space eaters on RHEL servers: application logs under `/var/log`, the systemd journal (`/var/log/journal`), core dumps (`/var/lib/systemd/coredump`, `/var/crash` from kdump), container images (`/var/lib/containers`), old kernels (`dnf` keeps 3 by default via `installonly_limit`), and database files or backups someone wrote to `/` instead of a data volume.

To shrink the journal safely:

```bash
journalctl --vacuum-size=500M      # keep at most 500 MiB of archived journals
journalctl --vacuum-time=14d       # or keep 14 days
```

Then make it permanent with `SystemMaxUse=500M` in `/etc/systemd/journald.conf` (or a drop-in under `/etc/systemd/journald.conf.d/`) and `systemctl restart systemd-journald`.

:::tip
`ncdu` (from EPEL on RHEL, `apt install ncdu` on Ubuntu) gives an interactive, sortable view of `du` and is excellent when you are allowed to install tools. Use `ncdu -x /` to stay on one filesystem. On a server where you cannot install anything, `du -xh -d1 | sort -h`, repeated one level at a time, does the same job.
:::

## 4. Inode exhaustion

### How it looks

The application logs `No space left on device` (errno `ENOSPC`), `touch` fails, but `df -h` shows 40% used. Always check `df -i` next to `df -h`.

```bash
touch /data/test
```

```text
touch: cannot touch '/data/test': No space left on device
```

```bash
df -h /data; df -i /data
```

```text
Filesystem      Size  Used Avail Use% Mounted on
/dev/vdb1        20G  7.9G   11G  42% /data
Filesystem      Inodes   IUsed IFree IUse% Mounted on
/dev/vdb1      1310720 1310720     0  100% /data
```

### Why it happens

- **ext4** fixes the number of inodes at `mkfs` time (by default one inode per 16 KiB of space). Millions of tiny files (session files, mail queue, cache shards, PHP sessions, a runaway job writing one file per event) can exhaust them. You cannot add inodes to an existing ext4 filesystem except by growing it.
- **XFS** allocates inodes dynamically, up to a percentage of the filesystem (`imaxpct`, shown by `xfs_info`). It runs out far less often, but it can still hit `imaxpct` or fail to find contiguous space for a new inode chunk on a very full, fragmented filesystem.

### Finding the directory with millions of files

`du` measures bytes, not file counts. Use `du --inodes` (GNU coreutils 8.22 and later, so RHEL 7+) or `find`:

```bash
du --inodes -x -d1 /data 2>/dev/null | sort -n | tail
# or, older systems:
for d in /data/*; do printf '%s\t%s\n' "$(find "$d" -xdev | wc -l)" "$d"; done | sort -n | tail
```

```text
12       /data/config
3104     /data/uploads
1307588  /data/cache
1310715  /data
```

### Fixing it

1. Delete the junk files. A plain `rm -rf /data/cache/*` can fail with "Argument list too long" when the shell expands millions of names. Use `find` instead:

```bash
find /data/cache -xdev -type f -mtime +2 -delete
# or remove the whole directory tree and recreate it
rm -rf /data/cache && mkdir /data/cache && restorecon -v /data/cache
```

2. Fix whatever created them: a cron cleanup, the application's own expiry, a `systemd-tmpfiles` rule (`/etc/tmpfiles.d/myapp.conf` with an age, such as `d /data/cache 0750 myapp myapp 2d`).
3. If the workload genuinely needs many small files, recreate ext4 with more inodes (`mkfs.ext4 -i 4096` or `-N <count>`) or use XFS.

:::tip
Note the `restorecon` after recreating a directory. A directory created by `mkdir` gets the SELinux label inherited from its parent or set by a policy file-transition rule, which is not always the label your service needs. If the path has a custom rule (`semanage fcontext -l | grep /data/cache`), `restorecon` reapplies it.
:::

## 5. Partitions: fdisk, gdisk and parted

You need a partition when you add a disk for LVM or a plain filesystem and want the layout to be obvious to the next engineer (and to tools that assume disks are partitioned). Use **GPT** for anything new: it supports disks over 2 TiB and up to 128 partitions by default. MBR (DOS) is legacy.

| Tool | Package (RHEL 9) | Use it for |
|---|---|---|
| `fdisk` | util-linux | Interactive; MBR and GPT (modern versions handle GPT fine) |
| `gdisk` / `sgdisk` | gdisk | GPT only; `sgdisk` is scriptable; can convert MBR to GPT |
| `parted` | parted | Scriptable (`parted -s`), MBR and GPT; writes changes **immediately** |
| `growpart` | cloud-utils-growpart | Grow an existing partition into free space after a disk resize |

### Create one GPT partition for LVM with parted

```bash
lsblk -f /dev/vdb                                  # confirm the disk is empty
parted -s /dev/vdb mklabel gpt
parted -s /dev/vdb mkpart primary 1MiB 100%
parted -s /dev/vdb set 1 lvm on
parted -s /dev/vdb print
```

```text
Model: Virtio Block Device (virtblk)
Disk /dev/vdb: 21.5GB
Sector size (logical/physical): 512B/512B
Partition Table: gpt
Disk Flags:

Number  Start   End     Size    File system  Name     Flags
 1      1049kB  21.5GB  21.5GB               primary  lvm
```

Starting at `1MiB` keeps the partition aligned, which matters for performance on SSDs and SAN storage.

### The same with fdisk (interactive)

```bash
fdisk /dev/vdb
# g  -> create a new empty GPT partition table
# n  -> new partition, accept defaults for number, first and last sector
# t  -> change type; enter "lvm" (or type 44 / 8e00-equivalent in the list)
# p  -> print to check
# w  -> write and exit
```

With `gdisk`, the flow is similar (`o` new GPT, `n` new partition, type code `8e00` for Linux LVM, `w` write).

After changing a partition table on a disk that is in use, the kernel may keep the old table. Tell it to re-read:

```bash
partprobe /dev/vdb      # from the parted package
# or
partx -u /dev/vdb       # util-linux
```

:::warn
`parted` commands take effect **immediately**, unlike `fdisk`, which only writes on `w`. A `parted -s /dev/sda mklabel gpt` on the wrong disk destroys the partition table at once. Always double-check the device name against `lsblk -f` output from the same minute.
:::

On Ubuntu and Debian the same tools exist (`apt install gdisk parted cloud-guest-utils`, the latter provides `growpart`).

## 6. LVM: create, extend, and grow online

LVM lets you pool disks into a volume group and carve logical volumes out of it. Its big production value: you can **grow volumes online**, add disks to a group, and move data between disks while the system is running.

### The vocabulary

| Term | What it is | Show it |
|---|---|---|
| **PV** (physical volume) | A disk or partition initialised for LVM | `pvs`, `pvdisplay` |
| **VG** (volume group) | A pool of PVs; space is handed out in extents (4 MiB by default) | `vgs`, `vgdisplay` |
| **LV** (logical volume) | A virtual block device cut from a VG; holds a filesystem or swap | `lvs`, `lvdisplay` |

```bash
pvs; vgs; lvs
```

```text
  PV         VG Fmt  Attr PSize   PFree
  /dev/vda3  rl lvm2 a--  <39.00g     0
  VG #PV #LV #SN Attr   VSize   VFree
  rl   1   2   0 wz--n- <39.00g     0
  LV   VG Attr       LSize  Pool Origin Data%  Meta%  Move Log Cpy%Sync Convert
  root rl -wi-ao---- 35.00g
  swap rl -wi-ao----  4.00g
```

`VFree 0` means the volume group has no space left to give. To grow `root`, you must first add space to the VG. The VG name depends on the distribution: `rl` on Rocky, `almalinux` on Alma, `rhel` on RHEL. Check with `vgs` rather than assuming.

### Create a new data volume from scratch

```bash
pvcreate /dev/vdb1
vgcreate vg_data /dev/vdb1
lvcreate -n lv_app -L 10G vg_data        # fixed size
# or: lvcreate -n lv_app -l 100%FREE vg_data
mkfs.xfs /dev/vg_data/lv_app
mkdir -p /srv/app
```

Then add it to fstab (section 8) and mount. The LV is reachable as `/dev/vg_data/lv_app` and `/dev/mapper/vg_data-lv_app`; both point to the same device-mapper node.

### Extend an LV and grow the filesystem

The classic two-step:

```bash
lvextend -L +5G /dev/vg_data/lv_app      # grow the block device
xfs_growfs /srv/app                      # XFS: grow, using the MOUNT POINT
# or, for ext4:
resize2fs /dev/vg_data/lv_app            # ext4: grow, using the DEVICE
```

| | **XFS** | **ext4** |
|---|---|---|
| Grow online (mounted) | Yes, `xfs_growfs <mountpoint>` | Yes, `resize2fs <device>` |
| Shrink | **No, never** | Yes, but **offline only** (unmount, `e2fsck -f`, then `resize2fs` to a smaller size) |
| Argument | Mount point (newer xfsprogs also accept the device if mounted) | Device |
| Default on | RHEL 7, 8, 9 and family | Ubuntu, Debian |

The one-step way, which most engineers should prefer:

```bash
lvextend -r -L +5G /dev/vg_data/lv_app       # -r = --resizefs
lvextend -r -l +100%FREE /dev/rl/root        # use all free space in the VG
```

`-r` makes LVM call the right filesystem tool after extending the LV, so you cannot forget the second step. Note `-L` (absolute or relative size in units) versus `-l` (extents or percentages): `-L 20G` means "make it 20 GiB", `-L +20G` means "add 20 GiB".

```text
  Size of logical volume vg_data/lv_app changed from 10.00 GiB (2560 extents) to 15.00 GiB (3840 extents).
  Logical volume vg_data/lv_app successfully resized.
meta-data=/dev/mapper/vg_data-lv_app isize=512    agcount=4, agsize=655360 blks
...
data blocks changed from 2621440 to 3932160
```

### Adding a disk to grow the root VG

When the VG has no free space, add a new disk (or partition) to it:

```bash
pvcreate /dev/vdc
vgextend rl /dev/vdc
lvextend -r -l +100%FREE /dev/rl/root
df -h /
```

This works online. Lab 03-C walks through it.

:::warn
Spanning a VG across two disks means **losing either disk loses the whole LV**. On a VM or cloud instance, growing the existing disk (next subsection) is usually cleaner than adding a second disk to the root VG. On physical servers, put PVs on RAID, not on single bare disks.
:::

### Cloud disk resize: growpart, then pvresize, then grow

In AWS, Azure, GCP, OpenStack or VMware you usually grow the existing virtual disk in the console or API. The guest sees a bigger disk, but the partition, PV, LV and filesystem keep their old sizes.

```bash
lsblk /dev/nvme0n1
```

```text
NAME          MAJ:MIN RM  SIZE RO TYPE MOUNTPOINTS
nvme0n1       259:0    0  100G  0 disk
├─nvme0n1p1   259:1    0    1M  0 part
├─nvme0n1p2   259:2    0  200M  0 part /boot/efi
├─nvme0n1p3   259:3    0    1G  0 part /boot
└─nvme0n1p4   259:4    0 48.8G  0 part
  └─rl-root   253:0    0 48.8G  0 lvm  /
```

The disk is 100 GiB, but partition 4 is still 48.8 GiB. Grow from the bottom up:

```bash
dnf install -y cloud-utils-growpart       # provides growpart
growpart /dev/nvme0n1 4                   # note the SPACE: disk, then partition number
pvresize /dev/nvme0n1p4                   # only if the partition is an LVM PV
lvextend -r -l +100%FREE /dev/rl/root     # only if LVM; otherwise:
# xfs_growfs /        (XFS directly on the partition)
# resize2fs /dev/nvme0n1p4   (ext4 directly on the partition)
df -h /
```

```text
CHANGED: partition=4 start=2715648 old: size=102336512 end=105052160 new: size=206999519 end=209715167
  Physical volume "/dev/nvme0n1p4" changed
  1 physical volume(s) resized or updated / 0 physical volume(s) not resized
```

`growpart` can only grow the **last** partition into free space that directly follows it. If the partition you need to grow is not the last one (for example swap sits after root), you cannot simply growpart it; you add a new partition in the free space and `vgextend` instead.

If the kernel has not noticed the new disk size at all (`lsblk` still shows the old size), rescan the device: `echo 1 > /sys/class/block/sdb/device/rescan` for SCSI/virtio-scsi disks. NVMe and virtio-blk disks usually update automatically.

### XFS cannot shrink

There is no `xfs_shrinkfs`. Once an XFS filesystem is grown, the only way to make it smaller is to **back up, recreate at the smaller size, and restore**:

```bash
xfsdump -l 0 -f /backup/app.dump /srv/app      # from the xfsdump package
umount /srv/app
lvremove /dev/vg_data/lv_app
lvcreate -n lv_app -L 8G vg_data
mkfs.xfs /dev/vg_data/lv_app
mount /srv/app
xfsrestore -f /backup/app.dump /srv/app
restorecon -Rv /srv/app
```

That means downtime for the data on that volume, and it is impossible for `/` without booting rescue media. The production lesson: **grow in modest steps**. Do not `lvextend -l +100%FREE` a volume "just because" if another volume might need that space next month. Keep some free extents in the VG as a reserve you can hand out in an emergency.

:::warn
`lvreduce` on an LV that holds XFS **will destroy the filesystem** if you go ahead. Recent LVM versions refuse or warn loudly when a filesystem would be truncated, but do not rely on that. If someone asks you to "just shrink /home to give space to /", the answer on XFS is a backup-and-recreate plan with a maintenance window.
:::

## 7. Filesystems and SELinux labels on new mount points

### Making a filesystem

```bash
mkfs.xfs /dev/vg_data/lv_app                 # RHEL default
mkfs.xfs -L appdata /dev/vg_data/lv_app      # with a label
mkfs.ext4 /dev/vg_data/lv_logs               # ext4 when you need offline shrink
xfs_info /srv/app                            # show XFS geometry, imaxpct
tune2fs -l /dev/vg_data/lv_logs | head -40   # show ext4 superblock details
```

On Ubuntu and Debian, ext4 is the default and `mkfs.xfs` needs `apt install xfsprogs`.

### The SELinux label problem nobody expects

When you mount a new filesystem at a path a confined service uses, the files on it need the right SELinux type. A new, empty filesystem's root directory is not automatically labelled for your service, and files copied from elsewhere may keep their old labels.

Typical incident: an engineer moves web content to a new disk mounted at `/srv/web`, points the Apache `DocumentRoot` at it, and every request returns **403 Forbidden** even though permissions are `755`.

```bash
ls -Zd /srv/web /srv/web/index.html
ausearch -m AVC -ts recent | audit2why
```

```text
system_u:object_r:var_t:s0 /srv/web
unconfined_u:object_r:var_t:s0 /srv/web/index.html

type=AVC msg=audit(1732521000.123:845): avc:  denied  { read } for  pid=3011 comm="httpd" name="index.html" dev="dm-2" ino=131 scontext=system_u:system_r:httpd_t:s0 tcontext=unconfined_u:object_r:var_t:s0 tclass=file permissive=0
        Was caused by:
                Missing type enforcement (TE) allow rule.
```

`httpd_t` may read `httpd_sys_content_t`, not `var_t`. The fix is a **persistent file-context rule** plus `restorecon`:

```bash
semanage fcontext -a -t httpd_sys_content_t "/srv/web(/.*)?"
restorecon -Rv /srv/web
ls -Zd /srv/web
```

```text
Relabeled /srv/web from system_u:object_r:var_t:s0 to system_u:object_r:httpd_sys_content_t:s0
Relabeled /srv/web/index.html from unconfined_u:object_r:var_t:s0 to unconfined_u:object_r:httpd_sys_content_t:s0
```

Do **not** use `chcon` as the fix: it works until the next full relabel or `restorecon`, then breaks again. Do **not** `setenforce 0`. If you want to confirm SELinux is the cause, you may switch to permissive for a moment as a diagnostic (`setenforce 0`, test, `setenforce 1` immediately), but the AVC in the audit log already tells you.

:::tip
`cp` creates new files that get the label of the destination directory; `mv` keeps the original label (it moves the inode on the same filesystem, and copies the label across filesystems when it can). That is why content moved with `mv` from a home directory often arrives labelled `user_home_t` and breaks a web server. After any bulk copy or move into a service path, run `restorecon -Rv <path>`. `cp -a` and `rsync -X` also preserve labels, which is good only if the source labels were right.
:::

## 8. /etc/fstab: mounting at boot, safely

### Anatomy of an fstab line

```text
# <device>                                 <mount point> <type> <options>                 <dump> <fsck>
UUID=0b7e4a52-91c8-4e0a-8f2b-2c6d3a9e7b44  /             xfs    defaults                  0      0
UUID=5d1c3a0e-2f1b-4f61-9a3e-6b7c1d2e9f10  /boot         xfs    defaults                  0      0
/dev/mapper/rl-swap                        none          swap   defaults                  0      0
/dev/mapper/vg_data-lv_app                 /srv/app      xfs    defaults,nofail           0      0
UUID=7c19e5d2-3b4a-4c8e-a1f0-9d2b6e4c3a11  /data         ext4   defaults,noatime,nofail   0      2
nfs01.example.com:/export/share            /mnt/share    nfs4   defaults,_netdev,nofail   0      0
```

| Field | Meaning | Notes |
|---|---|---|
| Device | What to mount | Use `UUID=` for partitions and plain disks, `/dev/mapper/vg-lv` or `/dev/vg/lv` for LVM (stable names), `server:/path` for NFS |
| Mount point | Where | Must exist (`mkdir -p`); `none` for swap |
| Type | `xfs`, `ext4`, `nfs`, `nfs4`, `swap`, `tmpfs` | `auto` works but be explicit |
| Options | Comma-separated, no spaces | See table below |
| Dump | Legacy backup flag | `0` |
| fsck order | `0` = never, `1` = root, `2` = others | XFS ignores it (always `0`); ext4 data volumes `2` |

Common options:

| Option | Effect |
|---|---|
| `defaults` | `rw,suid,dev,exec,auto,nouser,async` |
| `nofail` | Do **not** fail the boot if this device is missing or fails to mount |
| `_netdev` | Network filesystem: wait for the network (systemd adds this for nfs/cifs automatically) |
| `x-systemd.device-timeout=10s` | How long to wait for the device to appear (default 90 s) |
| `x-systemd.automount` | Mount on first access instead of at boot |
| `noatime` | Do not update access times on reads (less write I/O) |
| `ro` | Read-only |
| `noexec`, `nosuid`, `nodev` | Hardening; see section 9 |

:::concept
On RHEL 7 and later, **systemd reads fstab** and turns each line into a `.mount` unit (via `systemd-fstab-generator`). A line without `nofail` becomes a hard requirement of `local-fs.target` (or `remote-fs.target` for network mounts). If that mount fails at boot, `local-fs.target` fails, and systemd drops the server into **emergency mode**. That is why one typo in fstab can make a healthy server unbootable.
:::

### Getting the UUID right

```bash
blkid -s UUID -o value /dev/vdb1
```

```text
7c19e5d2-3b4a-4c8e-a1f0-9d2b6e4c3a11
```

Copy and paste it; never retype UUIDs. A useful habit is to append it directly:

```bash
echo "UUID=$(blkid -s UUID -o value /dev/vdb1)  /data  ext4  defaults,noatime,nofail  0 2" >> /etc/fstab
```

### Always test before you reboot

```bash
cp -a /etc/fstab /etc/fstab.bak.$(date +%F)     # back up first
vi /etc/fstab
findmnt --verify --verbose                      # parse and sanity-check fstab
systemctl daemon-reload                         # regenerate mount units from fstab
mount -a                                        # mount everything not yet mounted
findmnt /data
```

```text
/data
   [ ] target exists
   [ ] UUID=7c19e5d2-3b4a-4c8e-a1f0-9d2b6e4c3a11 translated to /dev/vdb1
   [ ] source /dev/vdb1 exists
   [ ] FS type is ext4
0 parse errors, 0 errors, 0 warnings
```

`findmnt --verify` catches unknown UUIDs, missing mount points, unknown filesystem types and bad syntax **without mounting anything**. `mount -a` actually mounts, which proves the options are valid. If `mount -a` prints an error, fix it now, while you still have a shell.

:::warn
After editing fstab on a systemd system, run **`systemctl daemon-reload`**. Without it, `mount -a` may print "mount: (hint) your fstab has been modified, but systemd still uses the old version" and systemd may try to mount the old definition later. And remember: `mount -a` skips filesystems that are **already mounted**, so it does not test a changed line for a mount that is currently in use. Unmount it first (if safe) or test the exact line by hand.
:::

### When a bad fstab stops the boot: emergency mode

A wrong UUID, a disk removed from the VM, a typo in the type field, or an NFS server that does not answer, all without `nofail`, leave you with this on the console after about 90 seconds:

```text
[ TIME ] Timed out waiting for device /dev/disk/by-uuid/7c19e5d2-....
[DEPEND] Dependency failed for /data.
[DEPEND] Dependency failed for Local File Systems.
You are in emergency mode. After logging in, type "journalctl -xb" to view
system logs, "systemctl reboot" to reboot, "systemctl default" or "exit"
to boot into default mode.
Give root password for maintenance
(or press Control-D to continue):
```

You need **console access** (hypervisor console, cloud serial console, iLO/iDRAC). SSH is not running in emergency mode.

**Recovery when you have the root password:**

```bash
journalctl -xb | grep -iE 'fail|timed out' | head     # which mount failed?
mount -o remount,rw /                                  # make sure / is writable
vi /etc/fstab                                          # fix or comment out the bad line, or add nofail
findmnt --verify
systemctl daemon-reload
mount -a
systemctl default                                      # continue booting (or: reboot)
```

**When root is locked** (common on cloud images and on RHEL 9 installs where only an admin user was created), emergency mode prints "Cannot open access to console, the root account is locked" and you cannot log in. Then use `rd.break` to get a shell in the initramfs:

1. Reboot, and at the GRUB menu press `e` on the default entry.
2. On the line starting with `linux`, append `rd.break` (on some cloud images also remove `console=ttyS0...` if you are on a graphical console, or keep it if you are on the serial console). Press **Ctrl+X** to boot.
3. At the `switch_root:/#` prompt:

```bash
mount -o remount,rw /sysroot
chroot /sysroot
vi /etc/fstab            # comment out or fix the bad line, or add nofail
exit                     # leave the chroot
exit                     # continue boot (or: reboot -f)
```

4. After the system is up, check the file's SELinux label: `ls -Z /etc/fstab` should show `etc_t`. If it does not, run `restorecon -v /etc/fstab`. (When you edit files from `rd.break` the SELinux policy is not loaded; editing an existing file in place normally keeps its label, but a new file created there gets no label. That is why password resets from `rd.break` need `touch /.autorelabel`, and fstab edits usually do not.)

:::tip
Add **`nofail`** to every non-essential mount: data volumes, backup disks, NFS shares. The system then boots even if the device is missing, and you can fix the mount over SSH. Do **not** add `nofail` to `/`, `/usr` or `/var` if the system cannot work without them; booting "successfully" without `/var` mounted makes services write into the empty mount-point directory on `/`, which causes the hidden-files du/df mismatch from section 3. Pair `nofail` with `x-systemd.device-timeout=10s` to avoid the 90-second wait.
:::

:::prod
**The disk that was detached for a snapshot.** Symptom: a database VM did not come back after a routine kernel patch reboot; monitoring shows it down, SSH times out. What the engineer saw on the cloud serial console: "Dependency failed for /backup", "You are in emergency mode". Root cause: weeks earlier, a backup disk had been detached from the VM for an offline copy and never reattached; its fstab line had no `nofail`. The server had been running fine because it had not rebooted since. Fix: from the serial console, commented out the `/backup` line, `systemctl daemon-reload`, `systemctl default`; the server finished booting and the database started. Prevention: a team standard that every non-root mount gets `nofail,x-systemd.device-timeout=10s`; a CI check that runs `findmnt --verify` across the fleet (an Ansible ad-hoc task works well); and a pre-reboot checklist step to run `findmnt --verify` and `mount -a`.
:::

## 9. Mount options, /tmp and tmpfs

### Hardening options: noexec, nosuid, nodev

| Option | What it blocks | Typical use |
|---|---|---|
| `noexec` | Executing binaries directly from this filesystem | `/tmp`, `/var/tmp`, `/dev/shm`, upload directories |
| `nosuid` | setuid/setgid bits take no effect | `/tmp`, `/home`, removable media, NFS |
| `nodev` | Device files are not interpreted | Everything except `/` and `/dev` |

CIS and DISA STIG benchmarks ask for `nodev,nosuid,noexec` on `/tmp`, `/var/tmp` and `/dev/shm`. If you automate compliance with Ansible, these are among the first checks.

**What breaks:** installers and tools that unpack a binary into `/tmp` and run it. You see `Permission denied` even as root:

```bash
cp /usr/bin/true /tmp/ && /tmp/true
```

```text
-bash: /tmp/true: Permission denied
```

Diagnosis: `findmnt -T /tmp` shows `noexec`. The confusing part is that `ls -l` shows the execute bit set, and there is **no** SELinux AVC in the audit log, because the kernel's mount check refuses it before SELinux is asked. Fixes, best first: point the tool at a different temp directory (`TMPDIR=/opt/myapp/tmp ./installer`), run the install in a maintenance window with a temporary `mount -o remount,exec /tmp` followed immediately by `mount -o remount,noexec /tmp`, or (rarely) change the policy for that host and document the exception.

:::warn
`noexec` is a speed bump, not a wall. `bash /tmp/script.sh` and `python3 /tmp/script.py` still run, because the interpreter lives on `/usr` and only **reads** the file. Treat `noexec` as one layer, alongside SELinux (which confines what services can execute regardless of mount options) and file permissions.
:::

### /tmp, /var/tmp and tmpfs

| | `/tmp` | `/var/tmp` | `/dev/shm`, `/run` |
|---|---|---|---|
| RHEL 9 default | On the root filesystem (disk); `tmp.mount` exists but is not enabled | On disk | tmpfs (RAM) |
| Cleared at reboot? | No by default on RHEL 9 (cleaned by age) | No | Yes |
| Age cleanup | `systemd-tmpfiles-clean.timer`: 10 days | 30 days | n/a |

`tmpfs` is a filesystem that lives in RAM (and swap). It is fast and is cleared at reboot, but every byte in it uses memory. Fedora, Arch and Debian 13 mount `/tmp` as tmpfs by default; RHEL 9 does not. To make `/tmp` a size-limited tmpfs with hardening options:

```bash
systemctl enable --now tmp.mount          # uses /usr/lib/systemd/system/tmp.mount
systemctl edit tmp.mount                  # drop-in to set options:
```

```ini
[Mount]
Options=mode=1777,strictatime,nosuid,nodev,noexec,size=2G
```

Or with an fstab line:

```text
tmpfs   /tmp   tmpfs   defaults,size=2G,mode=1777,nosuid,nodev,noexec   0 0
```

Production gotcha: a tmpfs `/tmp` that fills up shows as 100% in `df` and **consumes RAM**. A runaway job writing a 6 GiB file to a tmpfs `/tmp` on an 8 GiB server can push the box into swap or wake the OOM killer, which looks like a memory problem rather than a disk problem. Always set `size=`.

`/tmp` and `/var/tmp` must be mode **1777** (sticky bit). If someone runs `chmod 755 /tmp`, many services break in odd ways (SSH agent forwarding, package scripts, database sockets). Fix: `chmod 1777 /tmp && restorecon -v /tmp` (its SELinux type is `tmp_t`).

## 10. Swap

Swap is disk space the kernel uses when RAM is under pressure. It is not a performance feature; it is a buffer that buys time before the OOM killer acts.

```bash
swapon --show
free -h
```

```text
NAME      TYPE      SIZE USED PRIO
/dev/dm-1 partition   4G 1.1G   -2
               total        used        free      shared  buff/cache   available
Mem:           7.7Gi       6.9Gi       142Mi        25Mi       910Mi       577Mi
Swap:          4.0Gi       1.1Gi       2.9Gi
```

### Add swap as a new LV (preferred on LVM systems)

```bash
lvcreate -n swap2 -L 2G rl
mkswap /dev/rl/swap2
swapon /dev/rl/swap2
echo '/dev/mapper/rl-swap2  none  swap  defaults  0 0' >> /etc/fstab
swapon --show
```

### Add a swap file (when there is no free VG space)

```bash
dd if=/dev/zero of=/swapfile bs=1M count=2048 status=progress
chmod 600 /swapfile
mkswap /swapfile
semanage fcontext -a -t swapfile_t '/swapfile'
restorecon -v /swapfile
swapon /swapfile
echo '/swapfile  none  swap  defaults  0 0' >> /etc/fstab
```

Red Hat's documentation uses `dd` for swap files. `fallocate -l 2G /swapfile` is faster and works on XFS and ext4 with current RHEL 9 kernels, but files with holes (sparse files) are rejected by `swapon`, so `dd` is the safe default. The `swapfile_t` label keeps SELinux happy and is what the policy expects for swap files; a swap file labelled `default_t` or `etc_runtime_t` can produce AVC denials when systemd activates it at boot.

:::tip
High swap **usage** is not by itself a problem; high swap **activity** is. Check `vmstat 1 5` and look at the `si` and `so` columns (swap in, swap out per second). Sustained non-zero values mean the box is thrashing and needs more RAM or less load. Module 02 covers memory pressure in detail.
:::

## 11. NFS client mounts and SELinux on NFS

### Client setup

```bash
dnf install -y nfs-utils          # Ubuntu/Debian: apt install nfs-common
showmount -e nfs01.example.com    # list exports (NFSv3 / mountd)
mkdir -p /mnt/share
mount -t nfs4 nfs01.example.com:/export/share /mnt/share
findmnt -t nfs,nfs4
nfsstat -m                        # actual negotiated options per mount
```

```text
Export list for nfs01.example.com:
/export/share 10.0.0.0/24
/export/home  10.0.0.0/24

/mnt/share from nfs01.example.com:/export/share
 Flags: rw,relatime,vers=4.2,rsize=1048576,wsize=1048576,namlen=255,hard,proto=tcp,timeo=600,retrans=2,sec=sys,clientaddr=10.0.0.21,local_lock=none,addr=10.0.0.10
```

`showmount` talks to `rpc.mountd` on the server, which NFSv3 uses. A server configured for **NFSv4 only** may not answer `showmount` (you get "RPC: Program not registered" or a timeout) even though NFSv4 mounts work fine. In that case, mount the server's pseudo-root to browse: `mount -t nfs4 nfs01.example.com:/ /mnt/tmp`. NFSv4 needs only TCP 2049 through firewalls; NFSv3 also needs rpcbind (111) and mountd ports.

fstab line for a production NFS mount:

```text
nfs01.example.com:/export/share  /mnt/share  nfs4  rw,hard,timeo=600,retrans=2,_netdev,nofail  0 0
```

### hard vs soft

| | **hard** (default) | **soft** |
|---|---|---|
| Server unreachable | Client retries **forever**; processes doing I/O hang in `D` state | After `retrans` retries, the I/O call returns an error (EIO) to the application |
| Data safety | High: writes are not silently dropped | Risk of **silent data corruption** if a write times out and the app does not handle EIO |
| Use for | Anything written to: databases (if you must use NFS), app data, home directories | Read-only, non-critical mounts where a hang is worse than an error |

:::warn
Do not "fix" NFS hangs by switching to `soft`. A hard mount that hangs is telling you the server or network is broken; a soft mount that returns errors to an application mid-write can leave half-written files that nobody notices. Fix the network or server. If you must be able to interrupt a hung process, note that on modern kernels a hard-mount wait can be killed with `SIGKILL` (the old `intr` option is ignored).
:::

### Stale file handle

```text
ls: cannot access '/mnt/share': Stale file handle
```

The client holds a file handle the server no longer recognises: the export was deleted and recreated, the filesystem behind it was replaced or restored, the server's export `fsid` changed, or a failover moved the export to a node with different handles. Fix on the client:

```bash
umount -f /mnt/share || umount -l /mnt/share   # force, then lazy if still busy
mount /mnt/share
```

`umount -l` (lazy) detaches the mount immediately and cleans up when no process uses it any more; processes stuck on the old mount may need restarting. If it keeps happening, the fix is on the server side (stable `fsid=` in `/etc/exports`, or not recreating the underlying filesystem).

Other quick checks when an NFS mount fails or hangs:

```bash
rpcinfo -p nfs01.example.com            # is the server's RPC stack answering? (v3)
nc -zv nfs01.example.com 2049           # is TCP 2049 reachable? (nmap-ncat package)
mount -v -t nfs4 nfs01.example.com:/export/share /mnt/share   # verbose errors
journalctl -k | grep -i nfs             # "server not responding, still trying"
```

`access denied by server while mounting` almost always means the export does not allow this client (wrong subnet in `/etc/exports`, or the server's view of your IP is a NAT address).

### SELinux on NFS

By default, files on an NFS mount appear with the SELinux type **`nfs_t`**, because classic NFS does not carry per-file labels. Confined services are not allowed to use `nfs_t` unless a boolean says so.

```bash
ls -Zd /mnt/share
getsebool -a | grep nfs
```

```text
system_u:object_r:nfs_t:s0 /mnt/share

httpd_use_nfs --> off
use_nfs_home_dirs --> off
virt_use_nfs --> off
samba_share_nfs --> off
...
```

| Scenario | Symptom | Fix |
|---|---|---|
| Apache or nginx serves content from NFS | 403 Forbidden; AVC `httpd_t` → `nfs_t` | `setsebool -P httpd_use_nfs on` |
| Home directories on NFS (autofs `/home`) | SSH login works with password but key auth fails, or "Could not chdir to home directory" | `setsebool -P use_nfs_home_dirs on` |
| VM disk images on NFS (libvirt) | Guest will not start | `setsebool -P virt_use_nfs on` |
| Samba re-exporting an NFS mount | Shares empty or access denied | `setsebool -P samba_share_nfs on` |

Diagnose with the audit log; `audit2why` usually names the boolean for you:

```bash
ausearch -m AVC -ts recent | audit2why
```

```text
type=AVC msg=audit(1732522100.456:912): avc:  denied  { read } for  pid=4120 comm="httpd" name="index.html" dev="0:52" ino=393218 scontext=system_u:system_r:httpd_t:s0 tcontext=system_u:object_r:nfs_t:s0 tclass=file permissive=0

        Was caused by:
        The boolean httpd_use_nfs was set incorrectly.
        Description:
        Allow httpd to use nfs

        Allow access by executing:
        # setsebool -P httpd_use_nfs 1
```

`-P` makes the boolean persistent across reboots. Without it, the fix disappears at the next boot and the incident comes back.

**`semanage fcontext` and `restorecon` do not work on NFS**: the client cannot store labels on the server's files. When a boolean is too broad (it lets httpd read **every** NFS mount) or none exists for your case, use the **`context=` mount option** to give the whole mount one label:

```text
nfs01.example.com:/export/web  /var/www/html  nfs4  rw,hard,_netdev,nofail,context="system_u:object_r:httpd_sys_content_t:s0"  0 0
```

Now every file on that mount appears as `httpd_sys_content_t`, and Apache can read it with no boolean. Other confined services that would have been allowed `nfs_t` access by their own booleans are now looking at `httpd_sys_content_t` instead, which is the point: a narrower grant.

:::tip
Two `context=` gotchas. First, the kernel shares one superblock for mounts of the same server filesystem, so mounting a second export from the same server filesystem with a **different** `context=` (or one with and one without) can fail with "mount.nfs: an incorrect mount option was specified"; the `nosharecache` option works around it. Second, NFSv4.2 with **labeled NFS** (the `security_label` export option on the server) can carry real per-file SELinux labels from the server; it is less common and needs both sides configured, so check with your storage team before relying on it.
:::

:::prod
**The web farm that went 403 after a migration.** Symptom: after moving static assets from local disk to a shared NFS export so all four web nodes could serve the same files, every image returned 403. Permissions looked fine (`755`/`644`, owned by `apache`). What the engineer saw: `ausearch -m AVC` full of `httpd_t` → `nfs_t` denials. Root cause: the SELinux boolean `httpd_use_nfs` was off, which is the default. Someone suggested `setenforce 0`; the senior on call refused. Fix: `setsebool -P httpd_use_nfs on` through the configuration-management tool on all four nodes, verified, done in ten minutes. Prevention: the booleans went into the Ansible role for web servers, and the migration runbook got an "SELinux" step.
:::

## 12. Read-only filesystems after errors

### How it shows up

Writes fail with **`Read-only file system`** (`EROFS`), even for root:

```bash
touch /var/tmp/x
```

```text
touch: cannot touch '/var/tmp/x': Read-only file system
```

First, find out who made it read-only and why. It is almost always the kernel reacting to an error, not a person.

```bash
findmnt -T /var/tmp -o TARGET,SOURCE,FSTYPE,OPTIONS
dmesg -T | grep -iE 'ext4-fs error|xfs|i/o error|remount|read-only|blk_update_request|nvme|sd[a-z]' | tail -30
journalctl -k -b | grep -iE 'error|read-only' | tail -30
```

Typical ext4 messages:

```text
[Mon Nov 25 02:13:41 2026] EXT4-fs error (device dm-3): ext4_lookup:1855: inode #262401: comm myapp: deleted inode referenced: 262915
[Mon Nov 25 02:13:41 2026] EXT4-fs (dm-3): Remounting filesystem read-only
```

Typical XFS messages (XFS does not remount read-only; it **shuts the filesystem down**, after which nearly every access returns an I/O error):

```text
XFS (dm-2): metadata I/O error in "xfs_imap_to_bp+0x5c/0xb0" at daddr 0x1c40 len 32 error 5
XFS (dm-2): Corruption of in-memory data (0x8) detected at xfs_trans_cancel+0x12a/0x150 (fs/xfs/xfs_trans.c:1097).  Shutting down filesystem.
XFS (dm-2): Please unmount the filesystem and rectify the problem(s)
```

| Cause | Clue in the logs |
|---|---|
| Storage path errors (SAN, multipath, cloud volume hiccup) | `I/O error`, `blk_update_request`, `nvme ... timeout`, `rejecting I/O to offline device` |
| Filesystem corruption | `EXT4-fs error`, `XFS ... Corruption ... detected` |
| Mounted `ro` on purpose | No errors; `ro` in fstab or a recovery shell |
| ext4 `errors=remount-ro` policy | The option in `tune2fs -l` ("Errors behavior: Remount read-only") or fstab |
| Underlying device read-only | `lsblk` shows `RO 1` (snapshot volume, write-protected LUN) |

:::warn
If the logs show **I/O errors from the disk or storage path**, do not start repairing the filesystem yet. Running `xfs_repair` or `fsck` on a failing device can make things worse. Check the hardware or cloud volume (SMART data with `smartctl -a`, multipath status with `multipath -ll`, the cloud provider's volume health), **take a backup or snapshot if you still can**, then repair.
:::

### Repairing XFS

`xfs_repair` must run on an **unmounted** filesystem. For `/`, that means rescue media or the emergency/rescue boot targets; for a data volume, stop the services and unmount.

```bash
systemctl stop myapp
umount /srv/app                         # if busy: fuser -vm /srv/app, or lsof +f -- /srv/app
xfs_repair -n /dev/vg_data/lv_app       # -n: check only, change nothing
xfs_repair /dev/vg_data/lv_app          # repair
mount /srv/app
ls /srv/app/lost+found 2>/dev/null      # orphaned files land here
```

If `xfs_repair` refuses with "ERROR: The filesystem has valuable metadata changes in a log which needs to be replayed", **mount and unmount the filesystem first** so the log is replayed, then run `xfs_repair` again. Only if mounting fails do you use `xfs_repair -L`, which **zeroes the log** and can lose the most recent metadata changes (recently created or deleted files may be wrong). Treat `-L` as a last resort and take a block-level copy or snapshot first if the data matters.

### Repairing ext4

```bash
umount /data
e2fsck -f /dev/vg_data/lv_logs          # -f: force a full check even if marked clean
# answer prompts, or use -y to say yes to all fixes (review afterwards)
mount /data
```

Never run `fsck` or `e2fsck` on a **mounted** filesystem (other than a read-only `-n` check, and even that can show false errors). For the root ext4 filesystem on Ubuntu/Debian, boot with `fsck.mode=force` on the kernel command line, or from rescue media.

After any repair, run `restorecon -Rv` on the affected path if files were recovered into `lost+found` and moved back, so their SELinux labels are correct.

## 13. Troubleshooting runbook

| Symptom | Check (command) | Likely cause | Fix |
|---|---|---|---|
| "No space left on device", `df -h` shows 100% | `du -xh -d1 / \| sort -h`, `find / -xdev -size +500M` | Logs, journal, core dumps, caches, backups on `/` | Clean up or rotate; `journalctl --vacuum-size`; grow the LV |
| `df` 100% but `du` adds up to much less | `lsof -nP +L1` | Deleted file still open by a process | Restart/reload the process or `: > /proc/PID/fd/N` |
| `df` and `du` mismatch, `lsof +L1` empty | `mount --bind / /mnt/x; du -sh /mnt/x/<mountpoint>` | Files hidden under a mount point | Move/delete files under the mount point |
| "No space left on device", `df -h` shows free space | `df -i` | Inode exhaustion | Delete small files with `find -delete`; fix the producer; recreate with more inodes |
| Resized cloud disk, `df` shows old size | `lsblk`, `pvs`, `lvs` | Partition/PV/LV/filesystem not grown | `growpart`, `pvresize`, `lvextend -r` |
| `lvextend` worked, `df` unchanged | `lvs`, `df -h` | Filesystem not grown | `xfs_growfs <mnt>` or `resize2fs <dev>`; use `-r` next time |
| Need to shrink an XFS volume | `findmnt -T <path> -o FSTYPE` | XFS cannot shrink | Backup (`xfsdump`), recreate smaller, restore |
| Server drops to emergency mode at boot | `journalctl -xb \| grep -i fail` on the console | Bad fstab line (wrong UUID, missing disk, typo) without `nofail` | Fix/comment fstab, `daemon-reload`, `systemctl default`; `rd.break` if root locked |
| `mount -a` prints "fstab has been modified" | `systemctl daemon-reload` | systemd still has the old units | Run `systemctl daemon-reload` |
| "Permission denied" running a binary in `/tmp`, no AVC | `findmnt -T /tmp` | `noexec` mount option | Use another `TMPDIR`; temporary remount in a window |
| Web server 403 on a new disk path | `ls -Z`, `ausearch -m AVC -ts recent \| audit2why` | Wrong SELinux type (e.g. `var_t`, `user_home_t`) | `semanage fcontext -a -t httpd_sys_content_t "<path>(/.*)?"`; `restorecon -Rv` |
| Web server 403 on NFS content | `getsebool httpd_use_nfs`, AVC shows `nfs_t` | Boolean off | `setsebool -P httpd_use_nfs on` or `context=` mount option |
| NFS home dirs: key login fails, "Could not chdir" | `getsebool use_nfs_home_dirs` | Boolean off | `setsebool -P use_nfs_home_dirs on` |
| Processes hang in `D` state on an NFS path | `ps -eo pid,stat,cmd \| awk '$2 ~ /D/'`, `journalctl -k \| grep nfs` | Hard mount, server or network down | Fix server/network; do not switch to `soft` |
| "Stale file handle" | `findmnt -t nfs4`, server-side changes | Export recreated or filesystem replaced on server | `umount -f` / `umount -l`, remount; stable `fsid` on server |
| `showmount -e` fails but mount works | `rpcinfo -p server` | NFSv4-only server, no mountd | Mount the v4 pseudo-root to browse exports |
| "Read-only file system" | `dmesg -T \| grep -iE 'error\|read-only'` | Kernel reacted to I/O error or corruption | Check storage first; then `xfs_repair` / `e2fsck -f` unmounted |
| Swap file not activated at boot | `swapon --show`, `ls -Z /swapfile`, AVC | Missing fstab line, holes in file, wrong label | `dd` the file, `semanage fcontext -t swapfile_t`, `restorecon` |

## 14. Break-fix labs

All labs assume a **disposable Rocky Linux 9 or AlmaLinux 9 VM** (RHEL 9 with a free developer subscription works too) with SELinux enforcing, console access through your hypervisor, and a **snapshot taken before you start**. Labs 03-A and 03-B use loop-mounted image files so they cannot hurt your real filesystems. Lab 03-C needs a second virtual disk of at least 5 GiB attached to the VM. Lab 03-D needs console access because it breaks the boot. Run everything as root (`sudo -i`).

:::lab Lab 03-A — Disk at 100% from a deleted file that is still open
**Goal:** reproduce the classic du/df mismatch, find the deleted-but-open file with `lsof +L1`, and free the space without deleting anything else.

**Break it:**

```bash
dnf install -y lsof
truncate -s 300M /root/lab03a.img
mkfs.xfs -q /root/lab03a.img
mkdir -p /mnt/lab03a
mount -o loop /root/lab03a.img /mnt/lab03a
dd if=/dev/zero of=/mnt/lab03a/app.log bs=1M count=260 status=none
tail -f /mnt/lab03a/app.log > /dev/null &      # a "daemon" holding the log open
rm -f /mnt/lab03a/app.log                       # the well-meaning engineer
```

**Symptoms:**

```bash
df -h /mnt/lab03a
du -sh /mnt/lab03a
dd if=/dev/zero of=/mnt/lab03a/new.dat bs=1M count=100
```

`df` shows about 90% or more used, `du` shows almost nothing, and the `dd` fails with "No space left on device" once it hits the limit.

**Diagnose:**

1. Confirm the mismatch: `df` counts the blocks, `du` sees no names.
2. List open, deleted files on that filesystem:

```bash
lsof -nP +L1 /mnt/lab03a
```

```text
COMMAND  PID USER   FD   TYPE DEVICE  SIZE/OFF NLINK NODE NAME
tail    5123 root    3r   REG    7,0 272629760     0  131 /mnt/lab03a/app.log (deleted)
```

3. Note the PID (5123 here) and FD number (3).

**Fix:** pick one.

```bash
# Option 1: truncate through the file descriptor (no restart)
: > /proc/5123/fd/3
# Option 2: stop the process holding the file
kill 5123
```

(Option 1 works here even though `tail` opened the file read-only: you are opening the `/proc` link for writing as root, which reaches the same inode.)

**Verify:**

```bash
rm -f /mnt/lab03a/new.dat
df -h /mnt/lab03a
lsof -nP +L1 /mnt/lab03a
```

Usage drops back to a few percent, and `lsof` shows nothing (or a 0-byte deleted file if you used option 1 and `tail` is still running).

**Clean-up:**

```bash
kill %1 2>/dev/null; pkill -f 'tail -f /mnt/lab03a' 2>/dev/null
umount /mnt/lab03a
rm -f /root/lab03a.img
rmdir /mnt/lab03a
```
:::

:::lab Lab 03-B — Inode exhaustion from millions of small files
**Goal:** fill an ext4 filesystem's inode table while blocks are still free, find the directory responsible, and clean it up safely.

**Break it:** a small ext4 filesystem with deliberately few inodes, so you do not need literally millions of files (the diagnosis is identical).

```bash
truncate -s 200M /root/lab03b.img
mkfs.ext4 -q -N 20000 /root/lab03b.img          # only ~20,000 inodes
mkdir -p /mnt/lab03b
mount -o loop /root/lab03b.img /mnt/lab03b
mkdir -p /mnt/lab03b/{config,sessions}
echo "setting=1" > /mnt/lab03b/config/app.conf
cd /mnt/lab03b/sessions && seq 1 25000 | xargs touch 2>/dev/null; cd /
```

**Symptoms:**

```bash
touch /mnt/lab03b/config/new.conf
df -h /mnt/lab03b
df -i /mnt/lab03b
```

```text
touch: cannot touch '/mnt/lab03b/config/new.conf': No space left on device
Filesystem      Size  Used Avail Use% Mounted on
/dev/loop0      182M   28K  168M   1% /mnt/lab03b
Filesystem     Inodes IUsed IFree IUse% Mounted on
/dev/loop0      20480 20480     0  100% /mnt/lab03b
```

(Your exact inode count may be slightly higher than 20,000 because `mkfs.ext4` rounds up per block group.)

**Diagnose:**

```bash
du --inodes -x -d1 /mnt/lab03b | sort -n
ls /mnt/lab03b/sessions | wc -l
```

`sessions` holds almost every inode.

**Fix:** delete with `find`, not a shell glob, then add a rule so it cannot happen again.

```bash
rm -f /mnt/lab03b/sessions/*          # try it: on real servers with millions of files this
                                      # fails with "Argument list too long"
find /mnt/lab03b/sessions -xdev -type f -delete
```

A prevention rule for a real path would look like this (systemd-tmpfiles deletes files older than the age field):

```text
# /etc/tmpfiles.d/myapp-sessions.conf
d /srv/myapp/sessions 0750 myapp myapp 1d
```

**Verify:**

```bash
df -i /mnt/lab03b
touch /mnt/lab03b/config/new.conf && echo "writes work again"
```

**Clean-up:**

```bash
umount /mnt/lab03b
rm -f /root/lab03b.img
rmdir /mnt/lab03b
```
:::

:::lab Lab 03-C — Extend the LVM root filesystem online
**Goal:** the root filesystem is nearly full and the VG has no free extents. Add a disk, extend the VG and the root LV, and grow XFS, all without a reboot or downtime.

**Prerequisites:** take a VM snapshot now. Attach a new, empty virtual disk (5 GiB or more) in your hypervisor. The clean-up depends on the snapshot, for a reason you will discover in the last step.

**Break it:** fill `/` close to full (leave a safety margin).

```bash
df -h /
AVAIL_MB=$(df -m --output=avail / | tail -1)
fallocate -l $(( (AVAIL_MB - 300) ))M /var/tmp/ballast.bin
df -h /
```

**Symptoms:** `df -h /` shows 98–99% used. `vgs` shows `VFree` 0 (or nearly). On a real server, `dnf update` would now fail with "Disk Requirements: At least XMB more space needed".

**Diagnose:**

```bash
lsblk
vgs; lvs
findmnt -n -o SOURCE,FSTYPE /
```

Identify: the new disk (an empty `disk` with no children, for example `vdb` or `sdb`), the VG name (`rl`, `almalinux` or `rhel`), and the filesystem type (`xfs`).

**Fix:** replace `/dev/vdb` and the VG name with yours.

```bash
NEWDISK=/dev/vdb
VG=$(lvs --noheadings -o vg_name "$(findmnt -n -o SOURCE /)" | tr -d ' ')
echo "Adding $NEWDISK to VG $VG"
lsblk -f "$NEWDISK"                        # must show no FSTYPE, no children
pvcreate "$NEWDISK"
vgextend "$VG" "$NEWDISK"
vgs "$VG"
lvextend -r -l +100%FREE "/dev/$VG/root"
```

```text
  Physical volume "/dev/vdb" successfully created.
  Volume group "rl" successfully extended
  VG #PV #LV #SN Attr   VSize  VFree
  rl   2   2   0 wz--n- 43.99g <5.00g
  Size of logical volume rl/root changed from 35.00 GiB (8960 extents) to 39.99 GiB (10239 extents).
  Logical volume rl/root successfully resized.
...
data blocks changed from 9175040 to 10484736
```

(The root LV may not be called `root` on every image; check `lvs`.)

**Verify:**

```bash
df -h /
lsblk
pvs
```

`/` now has about 5 GiB more space, and the new disk shows the root LV as a child. No services restarted.

**Clean-up / revert:** first delete the ballast: `rm -f /var/tmp/ballast.bin`. Now try to undo the extension and note why you cannot:

```bash
pvmove /dev/vdb      # fails: no free extents elsewhere to move data to
```

XFS cannot shrink, so the root LV can never give those extents back, and the root filesystem now **depends on both disks**. Removing `/dev/vdb` would destroy `/`. The only clean revert is to **restore the VM snapshot** you took at the start (and then detach the extra disk). That is the production lesson: growing is easy and permanent; plan it.
:::

:::lab Lab 03-D — Recover from a bad fstab entry (emergency mode)
**Goal:** break the boot with a wrong UUID in `/etc/fstab`, recover from emergency mode on the console, and then make the entry safe with `nofail`.

**Prerequisites:** VM snapshot, console access (not just SSH), and a root password. If root has no password, set one for this lab: `passwd root`. (A second path for locked root is at the end.)

**Break it:**

```bash
cp -a /etc/fstab /root/fstab.good
mkdir -p /data
echo "UUID=11111111-2222-3333-4444-555555555555  /data  xfs  defaults  0 0" >> /etc/fstab
systemctl daemon-reload
systemctl reboot
```

**Symptoms:** on the console, the boot pauses about 90 seconds on "A start job is running for /dev/disk/by-uuid/1111...", then:

```text
[DEPEND] Dependency failed for /data.
[DEPEND] Dependency failed for Local File Systems.
You are in emergency mode. ...
Give root password for maintenance
(or press Control-D to continue):
```

SSH does not connect.

**Diagnose:** log in as root on the console.

```bash
journalctl -xb -p err | head -20
systemctl --failed
findmnt --verify
```

`findmnt --verify` flags the line directly:

```text
/data
   [E] unreachable source: UUID=11111111-2222-3333-4444-555555555555: No such file or directory
```

**Fix:**

```bash
mount -o remount,rw /
sed -i '\|^UUID=11111111-2222-3333-4444-555555555555|s|^|#|' /etc/fstab   # comment it out
findmnt --verify
systemctl daemon-reload
systemctl default
```

The boot continues to the normal target. SSH works again.

**Verify and prevent:**

```bash
systemctl is-system-running        # "running" (or "degraded" if something else failed)
ls -Z /etc/fstab                   # etc_t
```

Now put the line back **with** protection and confirm the system tolerates the missing device:

```bash
sed -i 's|^#\(UUID=11111111-2222-3333-4444-555555555555.*\)defaults|\1defaults,nofail,x-systemd.device-timeout=5s|' /etc/fstab
grep 11111111 /etc/fstab
systemctl daemon-reload
systemctl reboot
```

The server boots normally after a short wait; `systemctl status data.mount` shows the mount failed, but the system is up and reachable by SSH.

**Locked-root variant (optional):** with root locked, emergency mode cannot be entered. Reboot, press `e` in GRUB, append `rd.break` to the `linux` line, press Ctrl+X, then:

```bash
mount -o remount,rw /sysroot
chroot /sysroot
sed -i '\|^UUID=11111111|s|^|#|' /etc/fstab
exit
reboot -f
```

After boot, run `restorecon -v /etc/fstab` to be sure the label is correct.

**Clean-up:**

```bash
cp -a /root/fstab.good /etc/fstab
restorecon -v /etc/fstab
systemctl daemon-reload
rmdir /data
findmnt --verify
```

Or restore the VM snapshot.
:::

## Command cheat sheet

| Task | Command |
|---|---|
| Show block devices as a tree, with filesystems | `lsblk -f` |
| Show UUID and type of a device | `blkid /dev/vdb1`, `blkid -s UUID -o value /dev/vdb1` |
| Which filesystem holds a path, with options | `findmnt -T /path` |
| Space per filesystem / inodes per filesystem | `df -hT` / `df -i` |
| Biggest directories on one filesystem | `du -xh -d1 / \| sort -h` |
| Directories with the most files | `du --inodes -x -d1 /path \| sort -n` |
| Files over 500 MiB | `find / -xdev -type f -size +500M -exec ls -lh {} +` |
| Deleted but open files | `lsof -nP +L1` |
| Free space held by a deleted open file | `: > /proc/PID/fd/N` or restart the process |
| Shrink the journal | `journalctl --vacuum-size=500M` |
| New GPT partition for LVM | `parted -s /dev/vdb mklabel gpt mkpart primary 1MiB 100% set 1 lvm on` |
| Re-read a partition table | `partprobe /dev/vdb` |
| LVM status | `pvs`, `vgs`, `lvs` |
| Create PV, VG, LV | `pvcreate /dev/vdb1`, `vgcreate vg_data /dev/vdb1`, `lvcreate -n lv_app -L 10G vg_data` |
| Add a disk to a VG | `vgextend rl /dev/vdc` |
| Extend LV and filesystem in one step | `lvextend -r -L +5G /dev/vg/lv` |
| Grow XFS / grow ext4 | `xfs_growfs /mountpoint` / `resize2fs /dev/vg/lv` |
| Grow a partition after a cloud disk resize | `growpart /dev/nvme0n1 4`, then `pvresize /dev/nvme0n1p4` |
| Check fstab without mounting | `findmnt --verify --verbose` |
| Apply fstab changes | `systemctl daemon-reload && mount -a` |
| Remount root read-write in emergency | `mount -o remount,rw /` |
| Show / add swap | `swapon --show`, `mkswap /dev/vg/lv && swapon /dev/vg/lv` |
| NFS exports / mount / options | `showmount -e server`, `mount -t nfs4 server:/export /mnt`, `nfsstat -m` |
| Clear a stale NFS mount | `umount -f /mnt/x \|\| umount -l /mnt/x; mount /mnt/x` |
| SELinux NFS booleans | `getsebool -a \| grep nfs`, `setsebool -P httpd_use_nfs on` |
| Persistent file label + apply | `semanage fcontext -a -t httpd_sys_content_t "/srv/web(/.*)?"; restorecon -Rv /srv/web` |
| Why was access denied | `ausearch -m AVC -ts recent \| audit2why` |
| Find why a filesystem went read-only | `dmesg -T \| grep -iE 'error\|read-only'` |
| Check / repair XFS (unmounted) | `xfs_repair -n /dev/X`, `xfs_repair /dev/X` |
| Check / repair ext4 (unmounted) | `e2fsck -f /dev/X` |
| Who is using a mount point | `fuser -vm /mnt/x`, `lsof +f -- /mnt/x` |

:::summary
- Storage is a stack: **disk → partition → PV → VG → LV → filesystem → mount**. Grow it **bottom-up**, and use `lsblk -f` to see every layer.
- `df` counts allocated blocks; `du` walks names. When they disagree, check **`lsof +L1`** for deleted-but-open files and look for files hidden under mount points. Free space from a deleted open file by restarting the process or truncating `/proc/PID/fd/N`.
- "No space left on device" with free blocks means **inodes**: check `df -i`, find the directory with `du --inodes`, delete with `find -delete`.
- **`lvextend -r`** grows the LV and the filesystem together. XFS grows with `xfs_growfs <mountpoint>`, ext4 with `resize2fs <device>`. For cloud disk resizes: **`growpart` → `pvresize` → `lvextend -r`**.
- **XFS cannot shrink.** Grow in steps and keep a VG reserve.
- In **fstab**, use UUIDs or LVM paths, add **`nofail`** to non-essential mounts, and test with **`findmnt --verify`**, `systemctl daemon-reload` and `mount -a` before rebooting. A bad line without `nofail` drops the box into **emergency mode**; fix it from the console, or via `rd.break` if root is locked.
- `noexec` on `/tmp` blocks direct execution without an AVC; tmpfs uses RAM, so always set `size=`.
- New mount points and moved content need correct **SELinux labels**: `semanage fcontext` + `restorecon`, never `chcon` as the permanent fix and never `setenforce 0`.
- NFS: use **`hard`** mounts, `_netdev,nofail` in fstab, and fix stale handles by remounting. SELinux sees NFS as **`nfs_t`**; enable the specific boolean (`httpd_use_nfs`, `use_nfs_home_dirs`) with `-P`, or narrow it with the **`context=`** mount option.
- A **read-only** filesystem is the kernel protecting your data. Read `dmesg` first, rule out storage I/O errors, then repair **unmounted** with `xfs_repair` (log replay before `-L`) or `e2fsck -f`.
:::

## Self-check questions

<div class="q" markdown="1">

**Q1.** `/` on an application server is at 100% in `df -h`, but `du -xsh /` reports only 11 GiB of a 40 GiB filesystem. An engineer already deleted a 20 GiB log file an hour ago. What do you check first?

A. `df -i /` for inode exhaustion
B. `lsof -nP +L1` for deleted files still held open
C. `xfs_repair -n` on the root LV
D. `vgs` for free extents in the volume group

:::answer
**Answer: B.** A big gap between `df` and `du`, right after a large file was deleted, is the textbook sign of a deleted file still open by a process. `lsof +L1` lists it with its PID and FD, and you free the space by restarting the process or truncating `/proc/PID/fd/N`.

- A is wrong: inode exhaustion shows as ENOSPC with free blocks; here the blocks themselves are used.
- C is wrong: nothing suggests corruption, and `xfs_repair` needs the filesystem unmounted.
- D is wrong: free VG space lets you grow the volume, but does not explain where the space went.
:::
</div>

<div class="q" markdown="1">

**Q2.** A batch job fails with "No space left on device" writing to `/data`, yet `df -h /data` shows 55% used. What is the most likely cause and the next command?

A. A quota on the user; run `repquota -a`
B. The filesystem is read-only; run `findmnt -T /data`
C. Inode exhaustion; run `df -i /data`
D. The LV is too small; run `lvextend -r -L +10G`

:::answer
**Answer: C.** ENOSPC with plenty of free blocks most often means the filesystem has run out of inodes, usually from millions of small files. `df -i` confirms it, then `du --inodes -x -d1` finds the directory.

- A is wrong: quotas are possible but rarer, and they usually give "Disk quota exceeded" (EDQUOT), not ENOSPC.
- B is wrong: a read-only filesystem gives "Read-only file system", not ENOSPC.
- D is wrong: adding blocks does not help an ext4 inode shortage much and skips the diagnosis. On ext4 it would add some inodes, but the real fix is cleaning up and stopping whatever is producing the files.
:::
</div>

<div class="q" markdown="1">

**Q3.** The cloud team grew a VM's disk from 50 GiB to 100 GiB. `lsblk` shows the disk `nvme0n1` at 100G, but the root LV on partition `nvme0n1p3` and `df -h /` still show 48 GiB. Which sequence fixes it online?

A. `xfs_growfs /` only
B. `growpart /dev/nvme0n1 3`, `pvresize /dev/nvme0n1p3`, `lvextend -r -l +100%FREE /dev/rl/root`
C. `resize2fs /dev/rl/root`, then reboot
D. `pvcreate /dev/nvme0n1`, then `vgextend rl /dev/nvme0n1`

:::answer
**Answer: B.** Grow bottom-up: the partition into the new space, then the PV to fill the partition, then the LV and filesystem together with `lvextend -r`. All of this works while mounted.

- A is wrong: the filesystem cannot grow beyond the LV, which cannot grow beyond the PV and partition.
- C is wrong: `resize2fs` is for ext4 (RHEL root is XFS), and it skips the lower layers; no reboot is needed.
- D is wrong: `pvcreate` on the whole disk would overwrite the partition table and destroy the system.
:::
</div>

<div class="q" markdown="1">

**Q4.** A colleague asks you to shrink `/home` (XFS on LVM, 200 GiB, 30 GiB used) by 100 GiB to give the space to `/var`. What is the correct answer?

A. `lvreduce -r -L -100G /dev/rhel/home`
B. `xfs_growfs -D` with a smaller size, then `lvreduce`
C. XFS cannot shrink; back up `/home` (for example with `xfsdump`), recreate a smaller LV and filesystem, restore, relabel
D. Unmount `/home`, run `xfs_repair`, then `lvreduce`

:::answer
**Answer: C.** XFS supports growing only. Shrinking means backup, `lvremove`/`lvcreate` at the new size, `mkfs.xfs`, restore, and `restorecon -Rv /home`, in a maintenance window.

- A is wrong: `lvreduce -r` cannot shrink XFS; without `-r`, reducing the LV would truncate and destroy the filesystem.
- B is wrong: `xfs_growfs` cannot set a smaller size.
- D is wrong: `xfs_repair` checks consistency; it does not make a filesystem shrinkable.
:::
</div>

<div class="q" markdown="1">

**Q5.** After a planned reboot, a server shows "You are in emergency mode" on the console and SSH is down. The previous change was adding a new data disk to `/etc/fstab`. What is the best recovery path?

A. Reinstall the OS from the latest image
B. Log in on the console, `mount -o remount,rw /`, fix or comment out the new fstab line (or add `nofail`), `systemctl daemon-reload`, then `systemctl default`
C. Press Ctrl+D repeatedly until it boots
D. Boot with `selinux=0` to skip the failing mount

:::answer
**Answer: B.** A failed mount without `nofail` fails `local-fs.target`, which triggers emergency mode. Fix fstab from the console, reload systemd's generated units, and continue the boot. If root is locked, use `rd.break` and `chroot /sysroot`.

- A is wrong: massive overkill for a one-line config error.
- C is wrong: continuing just retries the same failing mount.
- D is wrong: SELinux has nothing to do with a missing device, and disabling it is never the fix.
:::
</div>

<div class="q" markdown="1">

**Q6.** You edited `/etc/fstab` to change mount options for `/data`, ran `mount -a`, saw no errors, and assumed the change was tested. Why might the next reboot still fail?

A. `mount -a` only tests NFS entries
B. `mount -a` skips filesystems that are already mounted, so the changed line was never actually used; and without `systemctl daemon-reload`, systemd still had the old unit
C. `mount -a` requires a reboot to take effect
D. fstab changes are ignored on systemd systems

:::answer
**Answer: B.** `mount -a` mounts only what is not mounted yet. To test a changed line, run `findmnt --verify`, `systemctl daemon-reload`, then unmount and remount that filesystem (if safe) or test the options by hand on a spare mount point.

- A is wrong: `mount -a` handles all `auto` entries.
- C is wrong: `mount -a` takes effect immediately for what it mounts.
- D is wrong: systemd generates mount units from fstab, so changes matter; they are picked up after `daemon-reload` or a reboot.
:::
</div>

<div class="q" markdown="1">

**Q7.** Apache returns 403 for content you moved from `/root/site` into a new XFS volume mounted at `/srv/web` with `mv`. Permissions are correct and `DocumentRoot` is right. What is the correct fix?

A. `setenforce 0` and add `SELINUX=permissive` to `/etc/selinux/config`
B. `chcon -R -t httpd_sys_content_t /srv/web`
C. `semanage fcontext -a -t httpd_sys_content_t "/srv/web(/.*)?"` then `restorecon -Rv /srv/web`
D. `chmod -R 777 /srv/web`

:::answer
**Answer: C.** `mv` kept the old labels (such as `admin_home_t`), and `/srv/web` has no rule saying it holds web content. A persistent fcontext rule plus `restorecon` fixes it now and survives relabels. Confirm with `ausearch -m AVC -ts recent | audit2why` and `ls -Z`.

- A is wrong: disabling SELinux removes a security layer to hide a labelling mistake.
- B is wrong: `chcon` works only until the next `restorecon` or relabel.
- D is wrong: SELinux denials are not about Unix permissions, and 777 creates a new security problem.
:::
</div>

<div class="q" markdown="1">

**Q8.** Four web servers serve static files from an NFS share mounted at `/var/www/html/assets`. Every asset returns 403 and the audit log shows `httpd_t` denied `read` on `nfs_t`. What is the most appropriate fix?

A. `restorecon -Rv /var/www/html/assets`
B. `setsebool -P httpd_use_nfs on` (or mount the share with `context="system_u:object_r:httpd_sys_content_t:s0"`)
C. `semanage fcontext -a -t httpd_sys_content_t "/var/www/html/assets(/.*)?"`
D. Switch the mount to `soft`

:::answer
**Answer: B.** NFS files appear as `nfs_t`, and httpd may read them only when `httpd_use_nfs` is on. The `context=` mount option is the narrower alternative: it labels just that mount as web content.

- A and C are wrong: file-context rules and `restorecon` cannot store labels on NFS files; the client has no per-file labels to set.
- D is wrong: hard/soft controls retry behaviour on server failure, not access control.
:::
</div>

<div class="q" markdown="1">

**Q9.** Users report that `ls /mnt/share` returns "Stale file handle" after the storage team restored the export from a backup overnight. What do you do on the client?

A. Run `xfs_repair` on `/mnt/share`
B. `umount -f /mnt/share` (or `umount -l` if busy), then `mount /mnt/share`
C. Change the mount to `soft` and wait
D. Reboot the NFS server

:::answer
**Answer: B.** Restoring or recreating the exported filesystem invalidates the file handles the client cached. Unmount (forcing or lazily if needed) and remount to get fresh handles. Restart processes that were stuck on the old mount.

- A is wrong: you cannot run `xfs_repair` on an NFS mount, and the filesystem is not corrupt.
- C is wrong: soft mounts do not refresh stale handles and add data-corruption risk.
- D is wrong: the server is fine; the client holds handles the server no longer recognises.
:::
</div>

<div class="q" markdown="1">

**Q10.** An application writes fail with "Read-only file system" on `/srv/app` (XFS). `dmesg` shows repeated `blk_update_request: I/O error, dev sdc` lines followed by "XFS (dm-4): ... Shutting down filesystem". What do you do first?

A. Run `xfs_repair -L` immediately
B. `mount -o remount,rw /srv/app`
C. Investigate the storage path (disk, SAN, multipath, cloud volume health) and take a copy or snapshot if possible, before any filesystem repair
D. `setenforce 0` and retry

:::answer
**Answer: C.** The I/O errors come from the block device: the filesystem shut down to protect itself. Repairing on a failing device can make things worse, so fix or replace the storage path and protect the data first. Then unmount and run `xfs_repair -n`, and `xfs_repair` (mounting first to replay the log if it asks).

- A is wrong: `-L` zeroes the log and is a last resort, never the first step.
- B is wrong: a shut-down XFS cannot be remounted rw, and doing so would only hide a failing disk.
- D is wrong: SELinux is unrelated to I/O errors.
:::
</div>

<div class="q" markdown="1">

**Q11.** A vendor installer fails with `Permission denied` when it tries to run a helper it extracted into `/tmp`, even as root. The file has the execute bit, and `ausearch -m AVC` shows nothing. What do you check?

A. `findmnt -T /tmp` for the `noexec` option
B. `getsebool -a | grep exec`
C. `df -i /tmp`
D. `chmod 1777 /tmp`

:::answer
**Answer: A.** `noexec` blocks direct execution at the mount level, before SELinux is consulted, so no AVC appears. Point the installer at another temp directory with `TMPDIR`, or temporarily remount `/tmp` with `exec` in a controlled window and revert immediately.

- B is wrong: there is no AVC, so a boolean is unlikely to be the problem.
- C is wrong: inode exhaustion gives "No space left on device".
- D is wrong: `1777` is already the correct mode for `/tmp` and has nothing to do with execution.
:::
</div>

<div class="q" markdown="1">

**Q12.** The root VG has no free extents and you need 10 GiB more on `/` today, with no downtime. You have attached a new 20 GiB virtual disk, `/dev/sdb`. Which sequence is correct?

A. `mkfs.xfs /dev/sdb`, then add `/dev/sdb` to fstab as `/`
B. `pvcreate /dev/sdb`, `vgextend rhel /dev/sdb`, `lvextend -r -L +10G /dev/rhel/root`
C. `lvextend -L +10G /dev/rhel/root`, then `resize2fs /dev/rhel/root`
D. `growpart /dev/sdb 1`, then `xfs_growfs /`

:::answer
**Answer: B.** Initialise the disk as a PV, add it to the root VG, and extend the root LV with `-r` so XFS grows in the same step. Keeping the remaining 10 GiB free in the VG gives you a reserve, which matters because XFS cannot shrink later.

- A is wrong: you cannot replace a running root filesystem by remounting a blank disk over it.
- C is wrong: the VG has no free extents, so `lvextend` fails; and `resize2fs` is for ext4, not XFS.
- D is wrong: `/dev/sdb` has no partition to grow, and the disk is not part of the VG.
:::
</div>
