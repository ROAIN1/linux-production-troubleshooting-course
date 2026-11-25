<div class="cover" markdown="1">
<div class="mod">Module 01 · Linux Essentials for Production</div>

# Linux Essentials for Production: Files, Users, Permissions, Processes and Packages

<p>The everyday foundations every on-call engineer leans on: where things live, how to search and read them quickly, who is allowed to touch them, what is running, which package put a file there, and a repeatable method for working out why something broke. Includes a first look at SELinux.</p>
</div>

## What you'll learn

- The **filesystem hierarchy** and what really lives in `/etc`, `/var`, `/opt`, `/proc`, `/sys` and `/run`
- Essential commands **done right**: `find`, `grep`, `awk`, `sed`, `xargs`, `less`, `tail -f`, `head`, with the flags that matter under pressure
- **Users and groups**: `/etc/passwd`, `/etc/shadow`, `useradd`/`usermod`, and proper **service accounts** with `nologin`
- **Permissions**: numeric and symbolic `chmod`, `umask`, **setuid/setgid/sticky**, `chown`, and why `chmod 777` so often fixes nothing
- **ACLs** with `getfacl`/`setfacl`, and **file attributes** with `lsattr`/`chattr +i`
- **Processes**: `ps`, `pgrep`, signals and `kill`, `nice`/`renice`
- **Package management** for troubleshooting: which package owns a file, `rpm -V` integrity checks, `dnf history undo`, and the `apt`/`dpkg` equivalents
- The **troubleshooting method**: observe, hypothesise, test, fix, verify, document
- A **first look at SELinux**: `getenforce`, `ls -Z`, and how to recognise an SELinux denial
- Four break-fix labs: permission denied despite `chmod 777`, wrong ownership after a copy, `rpm -V` finding tampered files, and a user who can't `sudo`

**Why it matters in production:** most real incidents are not exotic kernel bugs. They are a file in the wrong place, a directory one level up with the wrong mode, a service account that can't write its log, a config someone "just tweaked", or a package update nobody wrote down. The engineers who fix things fast are the ones who know these fundamentals cold and check them in the right order.

## 1. The filesystem hierarchy: where things live

Linux follows the **Filesystem Hierarchy Standard (FHS)**. Once you know the layout, you stop guessing where a config, a log or a runtime file is. On RHEL 9 (and Rocky/Alma 9), `/bin`, `/sbin` and `/lib` are symlinks into `/usr` (the "usrmerge"), so `/bin/ls` and `/usr/bin/ls` are the same file.

| Path | What lives there | Production notes |
|---|---|---|
| `/etc` | Host-specific **configuration**: `/etc/ssh/sshd_config`, `/etc/fstab`, `/etc/sudoers`, `/etc/httpd/`, `/etc/systemd/system/` | Text files, small, precious. Back it up and track changes (etckeeper, Ansible, Git). Never put data or logs here. |
| `/var` | **Variable** data that grows: `/var/log` (logs), `/var/lib` (state: databases, `rpm`, containers), `/var/cache`, `/var/spool` (mail, cron, print queues), `/var/tmp` | The classic "disk full" location. Often a separate filesystem on servers so a runaway log cannot fill `/`. |
| `/opt` | Self-contained **third-party** or in-house software: `/opt/myapp/bin`, `/opt/myapp/conf` | Not managed by the OS package manager unless you packaged it. Ownership and SELinux labels here are your responsibility. |
| `/usr` | Read-only, distribution-managed programs and libraries: `/usr/bin`, `/usr/lib64`, `/usr/share` | Managed by `rpm`. If something here changed and you didn't update a package, investigate (Section 9). |
| `/usr/local` | Locally built software installed outside the package manager | Same idea as `/opt`, but split across `bin`, `lib`, `etc`. |
| `/srv` | Data **served** by this host (web roots, FTP, Git) | Some teams use it; RHEL's default SELinux policy expects web content in `/var/www`. |
| `/home`, `/root` | User home directories; `/root` is root's home | Not a place for service data. Files created here get home-directory SELinux labels. |
| `/tmp`, `/var/tmp` | Temporary files, world-writable with the **sticky bit** | On RHEL, `systemd-tmpfiles` cleans `/tmp` (default 10 days) and `/var/tmp` (default 30 days). Services with `PrivateTmp=yes` see their own private `/tmp`. |
| `/proc` | A **virtual** filesystem: the kernel's view of processes and kernel state | Nothing on disk. `/proc/<PID>/` per process, `/proc/meminfo`, `/proc/sys/` (tunables). |
| `/sys` | Virtual **sysfs**: devices, drivers, block queues, kernel subsystems | `/sys/block/sda/queue/scheduler`, `/sys/class/net/eth0/`, `/sys/fs/selinux/`. |
| `/run` | A **tmpfs** (RAM) for runtime state since boot: PID files, sockets, lock files | Wiped at every boot. `/var/run` is a symlink to `/run`. If a service expects a directory in `/run`, it must be recreated at boot (`RuntimeDirectory=` or `tmpfiles.d`). |
| `/boot` | Kernels, initramfs images, boot loader config | Small partition. A full `/boot` breaks kernel updates. |
| `/dev` | Device nodes (`/dev/sda`, `/dev/null`, `/dev/mapper/...`), managed by udev | Also a virtual filesystem (devtmpfs). |

:::concept
**Three kinds of "file" you will meet.** Real files on disk (`/etc`, `/var`, `/usr`), files in RAM that vanish at reboot (`/run`, `/dev/shm`, and `/tmp` if mounted as tmpfs), and **virtual files** generated on the fly by the kernel (`/proc`, `/sys`). A virtual file shows size 0 in `ls -l` but has content when you `cat` it. You can't "fix" a value in `/proc/sys` permanently by editing it; you change it live with `sysctl -w` and persist it in `/etc/sysctl.d/*.conf`.
:::

### Reading /proc and /sys for troubleshooting

```bash
cat /proc/loadavg                    # 1, 5, 15-minute load, running/total tasks, last PID
grep -E 'MemTotal|MemAvailable' /proc/meminfo
cat /proc/1234/status | grep -E 'State|VmRSS|Threads'
tr '\0' ' ' < /proc/1234/cmdline; echo    # the exact command line, NUL-separated
ls -l /proc/1234/cwd /proc/1234/exe       # working directory and binary of the process
ls /proc/1234/fd | wc -l                  # open file descriptors (leak hunting)
cat /proc/sys/fs/file-max                 # same value as: sysctl fs.file-max
cat /sys/block/sda/queue/scheduler        # I/O scheduler, active one in [brackets]
```

```text
0.42 0.37 0.31 2/387 48211
MemTotal:        3735092 kB
MemAvailable:    2911340 kB
State:	S (sleeping)
VmRSS:	   84212 kB
Threads:	12
/opt/myapp/bin/myapp --config /opt/myapp/conf/app.yaml
lrwxrwxrwx. 1 myapp myapp 0 Nov 25 10:02 /proc/1234/cwd -> /opt/myapp
lrwxrwxrwx. 1 myapp myapp 0 Nov 25 10:02 /proc/1234/exe -> /opt/myapp/bin/myapp
214
```

`/proc/<PID>/exe` is extremely useful when a binary was replaced or deleted while running: `ls -l` then shows `(deleted)` after the path, which tells you the process is still running the old code and needs a restart after an upgrade.

:::tip
Before you edit any file under `/etc`, take a timestamped copy: `cp -a /etc/ssh/sshd_config /etc/ssh/sshd_config.$(date +%F-%H%M)`. `cp -a` keeps owner, mode, timestamps and extended attributes. Thirty seconds now saves you an hour of "what was it before?" at 3 a.m.
:::

## 2. Shells and essential commands done right

You will spend most of an incident reading files and filtering output. The difference between a junior and a senior on a bad night is often just knowing the right flags.

### find: locate files by name, age, size, owner and permissions

```bash
find /var/log -type f -name '*.log' -size +100M          # big logs
find /var/log -type f -mtime +30                         # modified more than 30 days ago
find /opt/myapp -mmin -60                                # changed in the last hour ("what changed?")
find /opt/myapp ! -user myapp                            # anything NOT owned by the service user
find / -xdev -type f -perm -4000 2>/dev/null             # setuid files on the root filesystem only
find / -xdev \( -nouser -o -nogroup \) 2>/dev/null       # files whose UID/GID has no name
find /srv/data -type d -perm -o+w                        # world-writable directories
find /tmp -type f -name 'core.*' -delete                 # delete matches (test without -delete first)
```

- `-xdev` stays on one filesystem, so you don't wander into `/proc`, NFS mounts or other disks.
- `-mtime +30` means "more than 30 full days"; `-mtime -1` means "within the last 24 hours".
- `-perm -4000` means "at least these bits set"; `-perm /o+w` means "any of these bits set".
- Always run `find` **without** `-delete` or `-exec rm` first and read the list.

### grep: search inside files

```bash
grep -rn 'listen' /etc/nginx/                  # recursive, with line numbers
grep -ri 'error' /var/log/myapp/app.log | tail -20
grep -E 'timeout|refused|denied' app.log       # extended regex, alternatives
grep -v '^#' /etc/ssh/sshd_config | grep -v '^$'   # config without comments and blank lines
grep -c 'HTTP/1.1" 500' access.log             # count matching lines
grep -l 'db_host' -r /opt/myapp/conf           # only list files that match
grep -A3 -B2 'OutOfMemory' app.log             # 3 lines after, 2 before each match
grep -F '[ERROR]' app.log                      # fixed string: brackets are not regex
grep -rn --include='*.conf' 'ssl' /etc/httpd/  # only search certain file names
```

:::tip
`grep -Ev '^\s*(#|$)' file` is the fastest way to see what a config file actually sets. Many incidents are a setting that "is in the file" but commented out, or present twice with the second line winning.
:::

### awk: columns and simple logic

`awk` splits each line into fields (`$1`, `$2`, ... `$NF` is the last) and lets you filter and compute.

```bash
awk -F: '$3 >= 1000 {print $1, $3, $7}' /etc/passwd          # regular users: name, UID, shell
awk '{print $1}' access.log | sort | uniq -c | sort -rn | head  # top client IPs
awk '$9 >= 500 {c++} END {print c+0, "server errors"}' access.log
df -hP | awk 'NR>1 && $5+0 > 85 {print $6, $5}'               # filesystems over 85% full
ps -eo user,rss | awk 'NR>1 {m[$1]+=$2} END {for (u in m) printf "%-12s %8.1f MB\n", u, m[u]/1024}'
```

```text
alice 1000 /bin/bash
bob 1001 /bin/bash
   1842 10.0.0.15
    911 10.0.0.22
     37 192.0.2.40
/var 91%
```

### sed: print ranges and edit safely

```bash
sed -n '120,160p' /var/log/messages                  # print only lines 120-160
sed -n '/Nov 25 10:0/,/Nov 25 10:1/p' /var/log/messages   # print a time window
sed -i.bak 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i '/^Listen 8080$/d' /etc/httpd/conf/httpd.conf # delete an exact line
```

:::warn
`sed -i` edits in place with no undo. Always use `sed -i.bak` (keeps `file.bak`) on config files, run the same expression **without** `-i` first to preview the result, and then validate the config with the service's own checker (`sshd -t`, `nginx -t`, `apachectl configtest`, `visudo -c`) before restarting.
:::

### xargs: turn output into arguments, safely

```bash
find /var/tmp -name '*.tmp' -mtime +7 -print0 | xargs -0 -r rm -f      # NUL-safe, skip if empty
find /opt/myapp -name '*.sh' -print0 | xargs -0 -n 10 chmod 750       # 10 files per command
cat hosts.txt | xargs -P 4 -I{} ssh {} 'uptime'                       # 4 in parallel
find /etc -name '*.conf' -exec grep -l 'old.example.com' {} +         # the find-only alternative
```

File names with spaces or newlines break a plain `find | xargs`. Use `-print0 | xargs -0`, or `find -exec ... {} +`, which batches arguments like `xargs`. `-r` (GNU) stops `xargs` running the command at all when there is no input.

### less, tail, head: reading logs

```bash
less /var/log/messages          # / search forward, ? back, n next, G end, g start, F follow, q quit
less +F /var/log/secure          # start in follow mode; Ctrl-C to stop following and search
tail -n 100 /var/log/myapp/app.log
tail -f /var/log/myapp/app.log   # follow the open file
tail -F /var/log/myapp/app.log   # follow the NAME: survives log rotation
tail -f app.log | grep --line-buffered ERROR
head -n 20 big.csv
journalctl -u myapp -f           # systemd journal equivalent (Module on logs covers this)
```

:::warn
`tail -f` follows the **file descriptor**. When `logrotate` renames `app.log` to `app.log.1` and creates a new `app.log`, `tail -f` keeps watching the old, now-silent file and you think the app has stopped logging. Use **`tail -F`** (follow by name, retry), which reopens the new file after rotation.
:::

:::story
Think of `tail -f` as watching one particular notebook, and `tail -F` as watching **whatever notebook is on the desk labelled "app.log"**. When someone swaps the notebook, `-f` keeps staring at the old one in the drawer.
:::

### A few shell habits that prevent incidents

- Quote variables: `rm -rf "$DIR"/*` with an empty `$DIR` becomes `rm -rf /*`. Use `set -u` in scripts, or `"${DIR:?}"`, which aborts if empty.
- Use `ls` before `rm`, and `echo` before a destructive loop: `for f in *.log; do echo rm "$f"; done`.
- `command -v foo` and `type foo` show which `foo` will actually run (alias, function, or path). `hash -r` clears the shell's cached paths after moving binaries.
- `sudo -i` gives a full root login environment; `sudo cmd` runs one command with root rights but your environment is filtered.

## 3. Users, groups and service accounts

### /etc/passwd, /etc/shadow and /etc/group

```bash
getent passwd alice myapp      # works with local files AND directory services (SSSD/LDAP/IdM)
getent group wheel
id alice
```

```text
alice:x:1000:1000:Alice Admin:/home/alice:/bin/bash
myapp:x:985:985:My App service account:/opt/myapp:/sbin/nologin
wheel:x:10:alice
uid=1000(alice) gid=1000(alice) groups=1000(alice),10(wheel)
```

| `/etc/passwd` field | Example | Meaning |
|---|---|---|
| 1 | `alice` | Login name |
| 2 | `x` | Password is in `/etc/shadow` |
| 3 | `1000` | **UID**. 0 = root. On RHEL, 1–999 are system accounts; regular users start at 1000 (`/etc/login.defs`) |
| 4 | `1000` | Primary **GID** |
| 5 | `Alice Admin` | Comment (GECOS) |
| 6 | `/home/alice` | Home directory |
| 7 | `/bin/bash` | Login shell (`/sbin/nologin` for service accounts) |

`/etc/shadow` (mode `0000` or `0400`, readable only by root) holds the password hash and ageing data:

```text
alice:$6$Qn1c...trimmed...:20412:0:99999:7:::
```

Fields: name, hash, last change (days since 1970-01-01), min days, max days, warn days, inactive days, expiry date, reserved. A hash starting with `$6$` is SHA-512 (the RHEL 9 default); `$y$` is yescrypt (Debian 11+ and Ubuntu 22.04+ defaults). A hash starting with `!` or `!!` means the account is **locked** or never had a password set. Use `chage -l alice` to read ageing in plain English and `passwd -S alice` for status.

:::warn
Never edit `/etc/passwd`, `/etc/shadow` or `/etc/group` with a normal editor on a live system. Use `useradd`, `usermod`, `groupadd`, `gpasswd`, or `vipw`/`vigr`, which lock the files and avoid corrupting them. A broken `/etc/passwd` line can stop everyone, including root, from logging in.
:::

### Creating and changing users

```bash
useradd -m -c "Bob Ops" -s /bin/bash bob        # -m creates the home dir (RHEL does this by default)
passwd bob
usermod -aG wheel bob                            # ADD bob to wheel, keep existing groups
gpasswd -d bob wheel                             # remove bob from wheel
usermod -L bob ; usermod -U bob                  # lock / unlock password
chage -E 2026-12-31 bob                          # account expires on a date
userdel -r bob                                   # delete user AND home dir (careful)
```

:::warn
**`usermod -G` without `-a` replaces all supplementary groups.** `usermod -G docker bob` silently removes bob from `wheel` and everything else. This is one of the most common "I can't sudo any more" causes. Always type `-aG`.
:::

Group membership is read at login. After `usermod -aG wheel bob`, bob's existing shells and SSH sessions still have the old group list; he must log out and back in (or run `newgrp wheel` in one shell). Compare `id` (current process) with `id bob` (database) to spot this.

### Service accounts done properly

An application should run as its **own unprivileged user**, never as root and never as a person's account.

```bash
useradd -r -d /opt/myapp -s /sbin/nologin -c "My App service account" myapp
install -d -o myapp -g myapp -m 0750 /opt/myapp /opt/myapp/data /var/log/myapp
```

- `-r` creates a **system** account (UID below 1000, no ageing, no home created unless `-m`).
- `-s /sbin/nologin` means nobody can get an interactive shell as this user (Debian/Ubuntu path: `/usr/sbin/nologin`).
- `install -d -o -g -m` creates directories with the right owner and mode in one step: no "forgot to chown" moment.

To test as the service user without giving it a shell:

```bash
sudo -u myapp touch /opt/myapp/data/test        # runs one command as myapp; nologin doesn't block this
sudo -u myapp -s /bin/bash                      # temporary debugging shell (needs sudo rights)
su - myapp
```

```text
This account is currently not available.
```

That last message is `nologin` doing its job, not an error to "fix" by giving the account `/bin/bash`.

## 4. Permissions: modes, umask, special bits and ownership

### Reading ls -l

```bash
ls -l /opt/myapp/conf/app.yaml
ls -ld /opt/myapp/conf
```

```text
-rw-r-----. 1 myapp myapp 1204 Nov 25 09:58 /opt/myapp/conf/app.yaml
drwxr-x---. 2 myapp myapp   24 Nov 25 09:58 /opt/myapp/conf
```

```text
- rw- r-- ---  .   1  myapp  myapp  1204 ...
| |   |   |    |   |    |      |
| |   |   |    |   |    owner  group
| |   |   |    |   link count
| |   |   |    '.' = has an SELinux context, '+' = has an ACL
| |   |   other (everyone else)
| |   group
| owner (user)
type: - file, d directory, l symlink, c/b device, s socket, p pipe
```

| Bit | On a file | On a directory |
|---|---|---|
| `r` (4) | Read the content | **List** the names in it |
| `w` (2) | Change the content | **Create, delete, rename** entries in it (needs `x` too) |
| `x` (1) | Execute it | **Enter / traverse** it, and reach anything inside by name |

:::concept
**To open `/srv/app/data/file.txt`, you need `x` on every directory in the path** (`/`, `/srv`, `/srv/app`, `/srv/app/data`) plus the right permission on the file itself. A file can be `777` and still give "Permission denied" if one parent directory is `700` and owned by someone else. Deleting a file depends on the **directory's** `w`, not the file's mode. `namei -l` shows the whole path at once and is the first command to run on any "Permission denied".
:::

```bash
namei -l /srv/app/data/file.txt
```

```text
f: /srv/app/data/file.txt
dr-xr-xr-x root  root  /
drwxr-xr-x root  root  srv
drwx------ root  root  app          <-- no x for anyone but root: this is the blocker
drwxrwxrwx root  root  data
-rwxrwxrwx root  root  file.txt
```

### chmod: numeric and symbolic

| Numeric | Symbolic equivalent | Typical use |
|---|---|---|
| `644` | `u=rw,go=r` | Normal config or web file |
| `640` | `u=rw,g=r,o=` | Config with secrets, readable by the service's group |
| `600` | `u=rw,go=` | Private keys, `~/.ssh/authorized_keys` |
| `755` | `u=rwx,go=rx` | Program or public directory |
| `750` | `u=rwx,g=rx,o=` | Application directory for owner and group only |
| `700` | `u=rwx,go=` | `~/.ssh`, private directories |

```bash
chmod 640 /opt/myapp/conf/app.yaml
chmod g+w,o-rwx /srv/shared
chmod -R u=rwX,g=rX,o= /opt/myapp     # capital X: execute only on directories (and files already executable)
```

The capital `X` is the right way to fix a whole tree: directories get `x` so they can be traversed, but data files don't become executable. `chmod -R 755` on a data tree is a common sloppy fix that marks every file executable.

### umask

The **umask** removes bits from the default mode of new files (`666`) and directories (`777`).

```bash
umask           # show, e.g. 0022
umask -S        # symbolic, e.g. u=rwx,g=rx,o=rx
```

| umask | New file | New directory |
|---|---|---|
| `0022` | `644` | `755` |
| `0002` | `664` | `775` |
| `0027` | `640` | `750` |
| `0077` | `600` | `700` |

On RHEL, root typically has `0022` and regular users with a private group (user name = group name) get `0002`; the exact mechanism (`/etc/login.defs`, `/etc/bashrc`, `/etc/profile`, `pam_umask`) varies between releases, so check with `umask` rather than assuming. For services, systemd sets the umask per unit (`UMask=0027` in the unit file), independent of any shell.

### setuid, setgid and the sticky bit

| Bit | Numeric | On a file | On a directory | Example |
|---|---|---|---|---|
| **setuid** | `4000` | Runs with the **file owner's** UID | (ignored on Linux) | `/usr/bin/passwd` (`-rwsr-xr-x`) |
| **setgid** | `2000` | Runs with the file's group | New files **inherit the directory's group** | Shared project dirs: `chmod 2770 /srv/projects` |
| **sticky** | `1000` | (no effect today) | Only the file's owner (or dir owner/root) can delete it | `/tmp` (`drwxrwxrwt`) |

```bash
ls -ld /tmp /usr/bin/passwd
chmod 2775 /srv/projects        # setgid shared directory
chmod u-s /usr/local/bin/tool   # remove setuid
```

```text
drwxrwxrwt. 18 root root 4096 Nov 25 10:12 /tmp
-rwsr-xr-x.  1 root root 32648 Aug 10  2025 /usr/bin/passwd
```

A capital `S` or `T` (for example `-rwSr--r--`) means the special bit is set but the underlying `x` is not, which is almost always a mistake.

:::warn
An unexpected **setuid-root** binary is a classic privilege-escalation backdoor. Keep a baseline of `find / -xdev -type f -perm -4000` and compare it regularly; compliance scanners (OpenSCAP, CIS benchmarks) check this too. The fix for "the app can't do X" is never `chmod u+s` on a script.
:::

### chown and chgrp

```bash
chown myapp:myapp /opt/myapp/data/app.db
chown -R myapp:myapp /opt/myapp/data
chgrp -R devs /srv/projects
chown --reference=/opt/myapp/conf/app.yaml /opt/myapp/conf/new.yaml   # copy owner from another file
ls -ln /opt/myapp/data          # numeric UID/GID: reveals "orphaned" IDs after copies between hosts
```

Only root can change a file's owner. A regular user can change the group only to a group they belong to. Copying files changes ownership too: `cp` as root creates **root-owned** files; `cp -p`/`cp -a` preserve the original owner. Lab 01-B shows what that does to a service.

## 5. ACLs: when owner/group/other isn't enough

Classic permissions allow exactly one owner and one group. When "the `devs` group can write, and also the `auditor` user can read, and also the `deploy` service can write" is needed, use **POSIX ACLs**. XFS (the RHEL default) and ext4 support them out of the box.

```bash
setfacl -m u:auditor:rx /srv/projects                 # add a user entry
setfacl -m g:deploy:rwx /srv/projects                 # add a group entry
setfacl -d -m g:devs:rwx /srv/projects                # DEFAULT ACL: inherited by new files/dirs
setfacl -R -m u:auditor:rX /srv/projects              # apply recursively (X as in chmod)
getfacl /srv/projects
setfacl -x u:auditor /srv/projects                    # remove one entry
setfacl -b /srv/projects                              # remove all ACL entries
```

```text
# file: srv/projects
# owner: root
# group: devs
# flags: -s-
user::rwx
user:auditor:r-x
group::rwx
group:deploy:rwx
mask::rwx
other::---
default:user::rwx
default:group::rwx
default:group:devs:rwx
default:mask::rwx
default:other::---
```

:::concept
**The ACL mask** is the maximum permission any named user, named group, or the owning group can have. If the mask is `r--`, then `group:deploy:rwx` is effectively read-only and `getfacl` shows `#effective:r--` next to it. Running `chmod g-w` on a file with an ACL **changes the mask**, not the owning group's entry, which surprises people. When an ACL "doesn't work", look at the mask first.
:::

:::warn
Not every tool preserves ACLs. `cp` without `-a` or `--preserve=all` drops them; `tar` needs `--acls` (and `--xattrs --selinux` for the rest); `rsync` needs `-A -X`. Backups that silently lose ACLs restore files nobody can open.
:::

## 6. File attributes: lsattr and chattr

Below permissions and ACLs there is another layer: **filesystem attributes**. The important one is **immutable** (`i`): the file cannot be modified, deleted, renamed or linked, **even by root**, until the attribute is removed.

```bash
lsattr /etc/resolv.conf
chattr +i /etc/resolv.conf      # set immutable (root only)
chattr -i /etc/resolv.conf      # remove it
chattr +a /var/log/audit.trail  # append-only: can add, cannot overwrite or delete
lsattr -d /srv/data             # attributes of the directory itself
```

```text
----i----------------- /etc/resolv.conf
```

(The number of dashes and extra letters, such as `e` for extents on ext4, depend on the filesystem.)

```bash
echo "nameserver 10.0.0.53" >> /etc/resolv.conf
```

```text
-bash: /etc/resolv.conf: Operation not permitted
```

:::tip
Learn to tell the two errors apart. **"Permission denied"** (EACCES) usually means modes, ACLs or SELinux. **"Operation not permitted"** (EPERM), especially when you are root, points at **attributes** (`chattr +i`/`+a`), a missing capability, or a read-only or special filesystem. That one word difference halves your search.
:::

:::prod
**The DNS change that wouldn't stick.** After a DNS migration, three servers kept resolving through the old resolver. NetworkManager logged that it updated DNS, Ansible reported "changed" on every run, yet `/etc/resolv.conf` never changed. The engineer ran `lsattr /etc/resolv.conf` and found `i`: years earlier someone had made it immutable to stop a DHCP client overwriting it, and never wrote that down. **Fix:** `chattr -i`, configure DNS properly through NetworkManager (`nmcli con mod ... ipv4.dns`), and let it manage the file. **Prevention:** an Ansible check that reports any immutable files under `/etc`, and a rule that every "temporary" hack goes in the runbook with an owner and a removal date.
:::

## 7. Processes: ps, pgrep, signals and nice

### Seeing what is running

```bash
ps aux | head                                   # BSD style: all processes, user-oriented
ps -ef --forest | less                          # System V style with a process tree
ps -eo pid,ppid,user,ni,stat,%cpu,%mem,etime,cmd --sort=-%cpu | head -15
ps -o pid,user,cmd -p 1234
pgrep -a myapp                                  # PIDs plus full command lines
pgrep -u myapp -l                               # processes owned by the user myapp
pidof sshd
pstree -p 1234
```

```text
    PID    PPID USER      NI STAT %CPU %MEM     ELAPSED CMD
   1234       1 myapp      0 Ssl  87.2 12.4    03:12:44 /opt/myapp/bin/myapp --config /opt/myapp/conf/app.yaml
   2210       1 mysql      0 Ssl   4.1 22.8  4-02:11:09 /usr/libexec/mysqld --basedir=/usr
    911       1 root       0 Ss    0.3  0.2  4-02:12:01 /usr/sbin/sshd -D
```

| STAT | Meaning | Why you care |
|---|---|---|
| `R` | Running or runnable | Using or waiting for CPU |
| `S` | Interruptible sleep | Normal idle state |
| `D` | **Uninterruptible sleep** (usually I/O) | Can't be killed, even with `-9`. Many `D` processes = storage or NFS trouble; they also raise the load average |
| `Z` | **Zombie**: exited, parent hasn't collected it | Harmless alone; many zombies = buggy parent. Kill or fix the **parent** |
| `T` | Stopped (by a signal or debugger) | Someone pressed Ctrl-Z or sent `SIGSTOP` |
| `s`, `l`, `+`, `<`, `N` | Session leader, multithreaded, foreground, high priority, low priority | Extra detail |

### Signals and kill

| Signal | Number | Default effect | When to use |
|---|---|---|---|
| `SIGHUP` | 1 | Terminate; many daemons treat it as "reload config" | `kill -HUP` / `systemctl reload` for daemons that support it |
| `SIGINT` | 2 | Interrupt (Ctrl-C) | Interactive programs |
| `SIGKILL` | 9 | **Immediate kill**, cannot be caught | Last resort: no cleanup, no flush, possible corruption |
| `SIGTERM` | 15 | Polite "please exit" (the default for `kill`) | Always try this first |
| `SIGSTOP` / `SIGCONT` | 19 / 18 (x86) | Pause / resume | Freeze a runaway process while you investigate |

```bash
kill 1234                 # SIGTERM
kill -TERM 1234           # same, explicit
kill -9 1234              # SIGKILL, only after TERM failed
pkill -u myapp -f 'worker.py'   # match on the full command line
kill -l                   # list all signals
```

:::warn
For anything managed by systemd, use `systemctl stop|restart|reload myapp`, not `kill`. If you kill a service's main process behind systemd's back, its `Restart=` policy may simply start it again, and you'll be confused about why "the kill didn't work".
:::

### nice and renice

Niceness ranges from **-20** (highest priority) to **19** (lowest); the default is 0. Only root can lower a value (raise priority).

```bash
nice -n 10 tar czf /backup/etc.tgz /etc     # run a backup at lower CPU priority
renice -n 15 -p 4321                         # make a running process nicer
ionice -c3 -p 4321                           # idle I/O class (depends on the I/O scheduler)
```

`nice` only matters when the CPU is contended. It does not limit memory or disk usage; for real resource control use systemd's `CPUQuota=`, `MemoryMax=` and `IOWeight=` (cgroups), covered in a later module.

## 8. Package management for troubleshooting

On RHEL 9 the package manager is **dnf** (version 4; `yum` is a compatibility alias), on top of the **rpm** database. In troubleshooting you use it mainly to answer four questions: *what owns this file*, *has it been changed*, *what changed recently*, and *how do I undo that*.

### Which package owns this file? Which package provides this command?

```bash
rpm -qf /usr/sbin/sshd                  # owner of an installed file
rpm -qf $(command -v semanage)
dnf provides '*/bin/semanage'           # which package WOULD provide it (searches repos)
rpm -qi openssh-server                  # info: version, build date, vendor, install date
rpm -ql openssh-server                  # all files in the package
rpm -qc openssh-server                  # config files only
rpm -q --scripts openssh-server         # install/uninstall scriptlets
rpm -qa --last | head                   # most recently installed/updated packages
```

```text
openssh-server-8.7p1-38.el9.x86_64
policycoreutils-python-utils-3.6-2.1.el9.noarch
```

If `rpm -qf` says `file /opt/myapp/bin/myapp is not owned by any package`, that is useful information too: the file came from a manual install, a tarball or a deployment tool.

### Verify files with rpm -V

`rpm -V` compares installed files against the rpm database's record of size, digest, mode, owner, timestamps and more. It prints **only the files that differ**.

```bash
rpm -V openssh-server
rpm -Va 2>/dev/null | grep -v '^.......T'     # whole system, hide timestamp-only changes
```

```text
S.5....T.  c /etc/ssh/sshd_config
.M.......    /usr/bin/passwd
S.5....T.    /usr/bin/tree
missing     /usr/share/doc/tree/README
```

| Position | Letter | Meaning when shown |
|---|---|---|
| 1 | `S` | **Size** differs |
| 2 | `M` | **Mode** (permissions or file type) differs |
| 3 | `5` | **Digest** (content checksum) differs |
| 4 | `D` | Device major/minor number differs |
| 5 | `L` | Symlink target differs |
| 6 | `U` | **User** owner differs |
| 7 | `G` | **Group** owner differs |
| 8 | `T` | Modification **time** differs |
| 9 | `P` | Capabilities differ |

A `.` means that test passed; `?` means the test couldn't run (often permissions). The letter before the path is the file type: `c` config, `d` documentation, `g` ghost, `l` licence, `r` readme.

:::concept
**How to read `rpm -V`:** a `c` file with `S.5....T.` is usually a legitimate admin edit to a config. A **binary or library** with `5` (changed content) and no matching package update is a red flag: a broken manual patch, disk corruption, or tampering. `rpm -V` uses the local rpm database, which an attacker with root can also modify, so for a security investigation compare against a trusted source (known-good host, fresh package from the repo) as well.
:::

### Repairing what rpm -V finds

```bash
dnf reinstall tree                   # restore non-config files of the package to pristine state
rpm --setperms passwd                # reset modes from the rpm database
rpm --setugids passwd                # reset owner/group from the rpm database
rpm --restore passwd                 # owner, group, mode, mtime and caps in one go (rpm 4.11+)
```

For a changed **config file**, don't blindly reinstall. Extract the package's original copy and compare it with yours:

```bash
mkdir -p /root/pristine && cd /root/pristine
dnf download openssh-server
rpm2cpio openssh-server-*.rpm | cpio -idmv ./etc/ssh/sshd_config
diff -u /root/pristine/etc/ssh/sshd_config /etc/ssh/sshd_config
```

When an update ships a new default for a config file you've changed, rpm leaves your file in place and writes the packaged version beside it as `.rpmnew` (for `%config(noreplace)` files), or saves yours as `.rpmsave` and installs the new one (for plain `%config`). After big updates, look for them: `find /etc -name '*.rpmnew' -o -name '*.rpmsave'`.

### What changed recently, and undoing it

```bash
dnf history                    # list transactions
dnf history info 14            # what transaction 14 did, who ran it, the command line
dnf history undo 14            # reverse transaction 14
dnf history rollback 12        # undo everything after transaction 12
dnf history list openssl       # every transaction that touched openssl
```

```text
ID     | Command line              | Date and time    | Action(s)      | Altered
--------------------------------------------------------------------------------
    14 | update -y                 | 2026-11-24 02:00 | Upgrade        |   37
    13 | install tree              | 2026-11-20 10:14 | Install        |    1
    12 | install httpd mod_ssl     | 2026-11-18 15:31 | Install        |    9
```

:::warn
`dnf history undo` of an **upgrade** needs the older package versions to still be available in your repositories (or local cache). On RHEL with a full repo history this usually works; with a mirror that keeps only the latest version, it fails. Kernel packages are installed side by side, so the clean rollback for a bad kernel is to boot the previous entry and set it as default with `grubby --set-default`. Test undo in staging before relying on it.
:::

### Debian and Ubuntu equivalents

| Task | RHEL family | Debian/Ubuntu |
|---|---|---|
| Which installed package owns a file | `rpm -qf /path` | `dpkg -S /path` |
| Which package would provide a file | `dnf provides '*/name'` | `apt-file search name` (install `apt-file`, run `apt-file update`) |
| List files in a package | `rpm -ql pkg` | `dpkg -L pkg` |
| Verify installed files | `rpm -V pkg` / `rpm -Va` | `dpkg --verify pkg` (dpkg 1.17+), or `debsums -c` |
| Package info | `rpm -qi pkg` / `dnf info pkg` | `dpkg -s pkg` / `apt show pkg` |
| Transaction history | `dnf history` | `/var/log/apt/history.log`, `/var/log/dpkg.log` |
| Undo a transaction | `dnf history undo N` | No built-in undo; reinstall specific versions: `apt install pkg=version` |
| Reinstall | `dnf reinstall pkg` | `apt install --reinstall pkg` |

## 9. The troubleshooting method

Commands are tools; the method is what makes you fast and safe. Every scenario in this course follows the same six steps.

| Step | What you do | Questions to ask | Typical commands |
|---|---|---|---|
| **1. Observe** | Collect facts before touching anything | What exactly fails? Exact error text? Since when? For everyone or one user/host? What changed? | `systemctl status`, `journalctl -xe`, `tail -F`, `dnf history`, `last`, `rpm -Va`, `find -mmin` |
| **2. Hypothesise** | List possible causes, most likely and cheapest-to-check first | Which layer: network, permissions, SELinux, config, resources, dependency? | Your experience plus the runbook tables in this course |
| **3. Test** | Check **one** hypothesis at a time with a read-only command | Does this evidence confirm or rule it out? | `namei -l`, `ls -Z`, `ausearch`, `sudo -u svc ...`, `curl -v` |
| **4. Fix** | Make the smallest change that addresses the **root cause** | Is this reversible? Do I have a backup? Is there a change ticket? | `cp -a` backup first, then the fix |
| **5. Verify** | Prove it works the way the user experiences it, and that you didn't break anything else | Does the original symptom go away? Does it survive a restart/reboot? | Repeat the original failing action; restart the service; check logs |
| **6. Document** | Write down symptom, cause, fix and prevention | Would the next on-call engineer find this in 2 minutes? | Ticket, runbook, post-incident review, config management update |

:::concept
**Change one thing at a time, and undo changes that didn't help.** If you change three things and the problem goes away, you don't know which fixed it, and the other two are now unexplained differences waiting to bite. If a test change (like `chmod 777` or `setenforce 0`) doesn't fix it, **revert it immediately**: it was a diagnostic, not a fix.
:::

:::story
Troubleshooting is like a doctor's visit. The doctor asks questions and takes your temperature (observe), thinks "probably flu, maybe strep" (hypothesise), runs a quick swab (test), prescribes the right medicine (fix), checks in a week later (verify), and writes it in your record (document). A doctor who gave every patient every medicine at once would be dangerous, which is exactly what `chmod -R 777 / ; setenforce 0 ; systemctl restart everything` is.
:::

### "What changed?" is the most powerful question

Most outages follow a change. Before deep analysis, spend two minutes on:

```bash
dnf history | head                         # package changes
rpm -qa --last | head                      # same, from the rpm database
last -n 20 ; lastlog | grep -v Never       # who logged in
find /etc /opt/myapp -mmin -120 -type f    # files changed in the last 2 hours
journalctl --since "2 hours ago" -p warning
grep -h 'COMMAND' /var/log/secure | tail   # sudo commands people ran
```

### A worked example

**Symptom:** after a deploy, `myapp` fails to start: `systemctl status myapp` shows `code=exited, status=1/FAILURE`.

1. **Observe.** `journalctl -u myapp -n 50` shows `open /opt/myapp/conf/app.yaml: permission denied`. `dnf history` shows nothing new; the deploy copied files 20 minutes ago.
2. **Hypothesise.** (a) wrong owner/mode on the file, (b) parent directory blocks traversal, (c) SELinux label, (d) immutable attribute (unlikely: the error is "permission denied", not "not permitted").
3. **Test.** `namei -l /opt/myapp/conf/app.yaml` shows the file is `root:root 600`. Hypothesis (a) confirmed. `ls -Z` shows a normal label; `ausearch -m AVC -ts recent` is empty, so SELinux is ruled out.
4. **Fix.** `chown myapp:myapp /opt/myapp/conf/app.yaml; chmod 640 /opt/myapp/conf/app.yaml`.
5. **Verify.** `systemctl restart myapp && systemctl is-active myapp`, then hit the health endpoint: `curl -fsS http://127.0.0.1:8080/health`.
6. **Document.** Root cause: the deploy script copied as root without setting ownership. Prevention: the deploy now uses `install -o myapp -g myapp -m 0640`.

## 10. A first look at SELinux

On RHEL, standard permissions are only half the access check. **SELinux** (Security-Enhanced Linux) is a **mandatory access control** layer: even if the mode bits say "yes", SELinux policy can still say "no". This is why "I ran `chmod 777` and it still fails" is so common on RHEL. SELinux gets its own deep-dive module later; here is enough to recognise it.

```bash
getenforce
sestatus
ls -Z /var/www/html/index.html
ps -eZ | grep httpd
id -Z
```

```text
Enforcing
SELinux status:                 enabled
SELinuxfs mount:                /sys/fs/selinux
Loaded policy name:             targeted
Current mode:                   enforcing
Mode from config file:          enforcing
...
unconfined_u:object_r:httpd_sys_content_t:s0 /var/www/html/index.html
system_u:system_r:httpd_t:s0      1532 ?        00:00:00 httpd
unconfined_u:unconfined_r:unconfined_t:s0-s0:c0.c1023
```

A **context** has four parts, `user:role:type:level`. In the default **targeted** policy, the part that matters almost every time is the **type**: `httpd_t` (the Apache process) is allowed to read files of type `httpd_sys_content_t`. If a web file has `admin_home_t` or `user_home_t` instead (because it was created in a home directory and then **moved**), Apache gets denied no matter what `chmod` says.

| Mode | Meaning |
|---|---|
| **Enforcing** | Policy is enforced and denials are logged. Production default. |
| **Permissive** | Nothing is blocked, but would-be denials are logged. Used briefly for diagnosis. |
| **Disabled** | SELinux is off. Not supported as a fix; relabelling is needed to turn it back on. |

How to tell whether SELinux is the blocker:

```bash
ausearch -m AVC -ts recent                 # recent denials from the audit log
ausearch -m AVC -ts recent | audit2why     # explain why (from policycoreutils-python-utils)
restorecon -Rv /var/www/html               # reset labels to what policy expects for that path
```

```text
type=AVC msg=audit(1764064231.512:412): avc:  denied  { read } for  pid=1534 comm="httpd"
name="index.html" dev="dm-0" ino=3398211 scontext=system_u:system_r:httpd_t:s0
tcontext=unconfined_u:object_r:admin_home_t:s0 tclass=file permissive=0
```

Read it as: **process** `httpd` (`scontext ... httpd_t`) was **denied** `read` on a **file** labelled `admin_home_t`. The fix is to correct the label, not to weaken the system.

:::warn
**Never "fix" a problem with `setenforce 0` or by setting `SELINUX=disabled`.** It's acceptable as a 60-second **diagnostic** on a test system: `setenforce 0`, retry, `setenforce 1`. If it then works, you've learned SELinux is involved; put it back to Enforcing **immediately** and fix the real cause with `restorecon`, `semanage fcontext`, `semanage port` or `setsebool -P` (all covered in the SELinux module). Turning SELinux off removes a protection layer that has contained many real web-app compromises, and it will fail a compliance audit.
:::

:::tip
**`mv` keeps the source label; `cp` gives the copy the label of the destination directory.** A file created in `/root` and moved into `/var/www/html` keeps `admin_home_t` and is denied. The same file copied gets `httpd_sys_content_t` and works. When files are moved into place, follow with `restorecon -Rv <dir>`.
:::

On Debian and Ubuntu the equivalent mandatory access control is usually **AppArmor**, which uses per-program path-based profiles instead of labels: `aa-status` shows loaded profiles, and denials appear in the kernel log as `apparmor="DENIED"`.

## 11. Troubleshooting runbook: access and file problems

| Symptom | Check (command) | Likely cause | Fix |
|---|---|---|---|
| "Permission denied" although the file is `777` | `namei -l /full/path` | A parent directory lacks `x` for that user | `chmod o+x` (or group) on the parent, or fix ownership; don't open the whole tree |
| "Permission denied" for a service only; works as root | `ls -Z file`, `ausearch -m AVC -ts recent` | SELinux label wrong (often after `mv`) | `restorecon -Rv path`; for custom paths `semanage fcontext -a -t TYPE 'path(/.*)?'` then `restorecon` |
| "Operation not permitted" as root | `lsattr file`, `lsattr -d dir` | Immutable or append-only attribute | `chattr -i file` (and find out who set it and why) |
| Write fails: "Read-only file system" | `findmnt -no OPTIONS /path`, `dmesg | tail` | Mounted `ro`, or remounted read-only after I/O errors | Fix the storage problem first; then `mount -o remount,rw` |
| Script gives "Permission denied" though it is `755` | `findmnt -no OPTIONS $(df --output=target /path | tail -1)` | Filesystem mounted `noexec` (common for `/tmp`) | Run it with `bash script.sh`, or put it somewhere executable |
| Service can't write its log after a deploy | `ls -ln /opt/myapp`, `find /opt/myapp ! -user myapp` | Files copied as root | `chown -R myapp:myapp`; use `install -o -g -m` in the deploy |
| Named user in ACL still can't write | `getfacl file` (look for `#effective:`) | ACL mask restricts it | `setfacl -m m::rwx file` |
| Files show numeric owner `1003` | `ls -ln`, `getent passwd 1003` | Copied/extracted from another host with different UIDs | `chown` to the correct local user; align UIDs via IdM/LDAP |
| `su - svcuser`: "This account is currently not available" | `getent passwd svcuser` | Shell is `/sbin/nologin` (correct for services) | Use `sudo -u svcuser cmd`; don't change the shell |
| "user is not in the sudoers file" | `id user`, `sudo -l -U user`, `visudo -c` | Not in `wheel`, removed by `usermod -G`, or sudoers drop-in ignored | `usermod -aG wheel user`; fix drop-in name/syntax |
| Binary behaves strangely, no recent update | `rpm -Vf /path/to/binary`, `dnf history` | File modified, corrupted, or replaced | `dnf reinstall pkg`; investigate tampering if unexplained |
| Config change "lost" after update | `find /etc -name '*.rpmnew' -o -name '*.rpmsave'` | Package installed new defaults or saved yours aside | Merge your changes into the active file deliberately |
| Process won't die with `kill -9` | `ps -o pid,stat,wchan:30,cmd -p PID` | `D` state: stuck in I/O (often NFS or failing disk) | Fix the underlying I/O problem; may need reboot |

## 12. Break-fix labs

All labs assume a **disposable Rocky Linux 9 or AlmaLinux 9 VM** (or RHEL 9 with a free developer subscription), with SELinux **Enforcing**, logged in as root (or via `sudo -i`). Take a VM snapshot before you start. Each lab follows: Goal, Break it, Symptoms, Diagnose, Fix, Verify, Clean-up.

:::lab Lab 01-A — "Permission denied" even after chmod 777
**Goal:** practise finding the real blocker behind "Permission denied" when the file mode is already wide open: a parent directory, an immutable attribute, and (preview) an SELinux label.

**Break it, part 1: parent directory.**

```bash
useradd -m labuser
mkdir -p /srv/app/data
echo "hello" > /srv/app/data/file.txt
chmod 777 /srv/app/data /srv/app/data/file.txt
chmod 700 /srv/app
```

**Symptoms:**

```bash
sudo -u labuser cat /srv/app/data/file.txt
```

```text
cat: /srv/app/data/file.txt: Permission denied
```

**Diagnose:**

```bash
ls -l /srv/app/data/file.txt         # as root: -rwxrwxrwx, looks fine
namei -l /srv/app/data/file.txt
```

```text
f: /srv/app/data/file.txt
dr-xr-xr-x root root /
drwxr-xr-x root root srv
drwx------ root root app
drwxrwxrwx root root data
-rwxrwxrwx root root file.txt
```

`app` is `700`, so `labuser` cannot traverse it. The file's mode is irrelevant.

**Fix:** give traversal only, not listing or write, and tighten the file back down.

```bash
chmod 711 /srv/app            # or 755, or better: group ownership + 750
chmod 644 /srv/app/data/file.txt
chmod 755 /srv/app/data
```

**Verify:** `sudo -u labuser cat /srv/app/data/file.txt` prints `hello`.

**Break it, part 2: immutable attribute.**

```bash
chattr +i /srv/app/data/file.txt
echo "more" >> /srv/app/data/file.txt
```

**Symptoms:** even root gets `-bash: /srv/app/data/file.txt: Operation not permitted`. `rm -f` fails the same way.

**Diagnose:** the word is "not permitted", not "denied", and you are root, so check attributes:

```bash
lsattr /srv/app/data/file.txt
```

```text
----i----------------- /srv/app/data/file.txt
```

**Fix and verify:**

```bash
chattr -i /srv/app/data/file.txt
echo "more" >> /srv/app/data/file.txt && tail -1 /srv/app/data/file.txt
```

**Break it, part 3 (SELinux preview): moved web content.**

```bash
dnf install -y httpd
systemctl enable --now httpd
echo "<h1>Lab 01-A</h1>" > /root/index.html
mv /root/index.html /var/www/html/index.html
chmod 777 /var/www/html/index.html
```

**Symptoms:**

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1/index.html
```

```text
403
```

`tail /var/log/httpd/error_log` shows `Permission denied` for `/var/www/html/index.html`, although the file is `777` and its parents are `755`.

**Diagnose:**

```bash
namei -l /var/www/html/index.html      # modes all fine
ls -Z /var/www/html/index.html
ausearch -m AVC -ts recent
```

```text
unconfined_u:object_r:admin_home_t:s0 /var/www/html/index.html
type=AVC msg=audit(...): avc:  denied  { read } for  pid=... comm="httpd" name="index.html" ...
scontext=system_u:system_r:httpd_t:s0 tcontext=unconfined_u:object_r:admin_home_t:s0 tclass=file permissive=0
```

The file kept the `admin_home_t` label from `/root` because it was **moved**. `httpd_t` may not read `admin_home_t`.

Optional diagnostic only: `setenforce 0`, re-run the `curl` (you get `200`), then **immediately** `setenforce 1` and confirm with `getenforce`. That proves SELinux is involved; it is not the fix.

**Fix:**

```bash
restorecon -v /var/www/html/index.html
chmod 644 /var/www/html/index.html
```

```text
Relabeled /var/www/html/index.html from unconfined_u:object_r:admin_home_t:s0 to unconfined_u:object_r:httpd_sys_content_t:s0
```

**Verify:** the `curl` returns `200`, `getenforce` says `Enforcing`, and `ls -Z` shows `httpd_sys_content_t`.

**Clean-up:**

```bash
chattr -i /srv/app/data/file.txt 2>/dev/null
rm -rf /srv/app /var/www/html/index.html
systemctl disable --now httpd
userdel -r labuser
```
:::

:::lab Lab 01-B — Wrong ownership after a copy breaks a service
**Goal:** see how copying files as root silently changes ownership, diagnose it from the service user's point of view, and fix it properly.

**Setup: a service account and its directories.**

```bash
useradd -r -d /opt/myapp -s /sbin/nologin myapp
install -d -o myapp -g myapp -m 0750 /opt/myapp /opt/myapp/conf /opt/myapp/data /opt/myapp/logs
sudo -u myapp sh -c 'echo "port: 8080" > /opt/myapp/conf/app.yaml'
sudo -u myapp touch /opt/myapp/logs/app.log
ls -l /opt/myapp/conf /opt/myapp/logs
```

**Break it: a "release" copied in as root.**

```bash
mkdir -p /root/release/conf /root/release/logs
echo "port: 8080
db_host: 10.0.0.20" > /root/release/conf/app.yaml
umask 0077                                     # simulate a hardened root umask
touch /root/release/logs/app.log
cp -r /root/release/* /opt/myapp/
umask 0022
```

**Symptoms:** the app (simulated with `sudo -u myapp`) can no longer read its config or write its log:

```bash
sudo -u myapp cat /opt/myapp/conf/app.yaml
sudo -u myapp sh -c 'echo "started" >> /opt/myapp/logs/app.log'
```

```text
cat: /opt/myapp/conf/app.yaml: Permission denied
sh: line 1: /opt/myapp/logs/app.log: Permission denied
```

**Diagnose:**

```bash
ls -l /opt/myapp/conf/app.yaml /opt/myapp/logs/app.log
find /opt/myapp ! -user myapp -ls
```

```text
-rw-------. 1 root root 31 Nov 25 11:02 /opt/myapp/conf/app.yaml
-rw-------. 1 root root  0 Nov 25 11:02 /opt/myapp/logs/app.log
```

`cp` **overwrote existing files in place**, so it kept... wait: look closely. For an existing destination file, GNU `cp` truncates and rewrites it, keeping the destination's owner and mode. If your output shows `myapp` as owner for the existing files, that is why. In this lab the release files include files the service had not yet created on some setups, and anything newly created by root's `cp` is `root:root` with root's umask applied. Either way, `find ! -user myapp` lists exactly which files are wrong. Also check numeric IDs, which matter when files come from another host:

```bash
ls -ln /opt/myapp/conf
```

**Fix:**

```bash
chown -R myapp:myapp /opt/myapp
find /opt/myapp -type d -exec chmod 0750 {} +
find /opt/myapp -type f -exec chmod 0640 {} +
restorecon -Rv /opt/myapp          # harmless here; good habit after any deploy
```

**Verify:**

```bash
sudo -u myapp cat /opt/myapp/conf/app.yaml
sudo -u myapp sh -c 'echo "started" >> /opt/myapp/logs/app.log' && echo OK
find /opt/myapp ! -user myapp      # should print nothing
```

**Prevent it:** deploy with tools that set ownership explicitly:

```bash
install -o myapp -g myapp -m 0640 /root/release/conf/app.yaml /opt/myapp/conf/app.yaml
rsync -a --chown=myapp:myapp /root/release/ /opt/myapp/      # rsync 3.1+
```

Or, in Ansible, set `owner`, `group` and `mode` on every `copy`/`template` task.

**Clean-up:**

```bash
rm -rf /opt/myapp /root/release
userdel myapp
```
:::

:::lab Lab 01-C — rpm -V finds a modified binary, a changed mode and a tweaked config
**Goal:** use `rpm -V` to detect changes to package-managed files, tell a normal config edit from a suspicious binary change, and restore the originals.

**Setup and break it:**

```bash
dnf install -y tree
cp -a /usr/bin/tree /root/tree.orig
cp -a /etc/chrony.conf /root/chrony.conf.orig
ls -l /usr/bin/passwd                      # note the mode first, normally -rwsr-xr-x

printf '\x00' >> /usr/bin/tree             # "corrupt" a binary by appending a byte
chmod u-s /usr/bin/passwd                  # remove setuid from passwd
echo "# tuned by someone" >> /etc/chrony.conf
```

**Symptoms:** `tree` may still run (an extra trailing byte rarely breaks an ELF binary), but regular users now can't change their passwords:

```bash
useradd -m labuser2 && echo 'Lab-Pass-123!' | passwd --stdin labuser2
sudo -u labuser2 passwd
```

The `passwd` attempt fails with an authentication token manipulation or permission error (exact wording varies), because without setuid it can't update `/etc/shadow`. Press Ctrl-C if it prompts.

**Diagnose:**

```bash
rpm -qf /usr/bin/passwd /usr/bin/tree /etc/chrony.conf
rpm -V passwd tree chrony
```

```text
passwd-0.80-12.el9.x86_64
tree-1.8.0-10.el9.x86_64
chrony-4.5-3.el9.x86_64
.M.......    /usr/bin/passwd
S.5....T.    /usr/bin/tree
S.5....T.  c /etc/chrony.conf
```

Read each line:

- `/usr/bin/passwd`: **M** only. Content is intact; the mode changed.
- `/usr/bin/tree`: **S**ize, **5** digest and **T**ime changed on a **binary** with no `c` flag. Nobody should edit binaries; investigate. Check `dnf history list tree` to confirm no update explains it.
- `/etc/chrony.conf`: `c` = config file. Edits are expected; compare with the original before deciding.

For a system-wide scan:

```bash
rpm -Va 2>/dev/null | grep -v ' c /' | grep '^..5'      # non-config files with changed content
```

**Fix:**

```bash
rpm --setperms passwd          # or: rpm --restore passwd
dnf reinstall -y tree
diff -u /root/chrony.conf.orig /etc/chrony.conf
cp -a /root/chrony.conf.orig /etc/chrony.conf && systemctl restart chronyd
```

**Verify:**

```bash
rpm -V passwd tree chrony && echo "all clean"
ls -l /usr/bin/passwd
```

`rpm -V` prints nothing for clean packages and returns exit code 0, so you should see `all clean` and `-rwsr-xr-x` on `passwd`.

**Clean-up:**

```bash
userdel -r labuser2
dnf remove -y tree
rm -f /root/tree.orig /root/chrony.conf.orig
```
:::

:::lab Lab 01-D — A user can't sudo
**Goal:** diagnose the three most common reasons an admin suddenly loses `sudo`: not in the group, group membership wiped by `usermod -G`, and a sudoers drop-in that is silently ignored.

**Safety first:** keep a **separate root session open** for the whole lab, so a sudoers mistake can't lock you out. Always edit sudoers with `visudo`.

**Setup:**

```bash
useradd -m opsuser
echo 'Lab-Pass-123!' | passwd --stdin opsuser
groupadd developers
```

**Break it, part 1: not in wheel.** Log in as `opsuser` in a new terminal (`ssh opsuser@<vm-ip>` or `su - opsuser`) and run:

```bash
sudo whoami
```

```text
opsuser is not in the sudoers file.  This incident will be reported.
```

(The exact sentence differs between sudo versions.)

**Diagnose** (as root):

```bash
id opsuser
sudo -l -U opsuser
grep -E '^%wheel' /etc/sudoers
```

```text
uid=1001(opsuser) gid=1001(opsuser) groups=1001(opsuser)
User opsuser is not allowed to run sudo on rocky9.
%wheel	ALL=(ALL)	ALL
```

**Fix:** `usermod -aG wheel opsuser`. Then **opsuser must log out and in again**, because group membership is read at login. In the old session, `id` still shows no `wheel`; in a new one it does. `sudo -l -U opsuser` (as root) now lists `(ALL) ALL`.

**Break it, part 2: the missing `-a`.**

```bash
usermod -G developers opsuser       # meant to ADD developers...
id opsuser
```

```text
uid=1001(opsuser) gid=1001(opsuser) groups=1001(opsuser),1002(developers)
```

`wheel` is gone. **Fix:** `usermod -aG wheel opsuser` (and next time, always `-aG`). Check audit trails with `grep usermod /var/log/secure` to see who ran it.

**Break it, part 3: an ignored drop-in.** Remove opsuser from wheel and grant sudo through a file instead, but give it a name sudo skips:

```bash
gpasswd -d opsuser wheel
echo 'opsuser ALL=(ALL) ALL' > /etc/sudoers.d/opsuser.conf
chmod 0440 /etc/sudoers.d/opsuser.conf
visudo -c
```

```text
/etc/sudoers: parsed OK
```

`visudo -c` is happy, yet opsuser (new login) still gets "not in the sudoers file".

**Diagnose:**

```bash
sudo -l -U opsuser
grep -n 'includedir' /etc/sudoers
ls -l /etc/sudoers.d/
```

`/etc/sudoers` contains `#includedir /etc/sudoers.d` (the `#` here is part of the directive, not a comment). sudo **skips files in that directory whose names end in `~` or contain a `.`**, so `opsuser.conf` is never read, and `visudo -c` didn't even mention it.

**Fix:**

```bash
mv /etc/sudoers.d/opsuser.conf /etc/sudoers.d/opsuser
visudo -cf /etc/sudoers.d/opsuser
sudo -l -U opsuser
```

**Verify:** as opsuser in a fresh login, `sudo whoami` prints `root`.

Other causes worth checking in real life: the account is locked or the password expired (`passwd -S opsuser`, `chage -l opsuser`), `pam_faillock` locked it after failed attempts (`faillock --user opsuser`, reset with `faillock --user opsuser --reset`), or the user is defined in IdM/LDAP and SSSD is caching old group data (`sss_cache -E`). On Ubuntu, the admin group is `sudo`, not `wheel`.

**Clean-up:**

```bash
rm -f /etc/sudoers.d/opsuser
userdel -r opsuser
groupdel developers
```
:::

## Command cheat sheet

| Task | Command |
|---|---|
| Show full path permissions | `namei -l /path/to/file` |
| Find recently changed files | `find /etc /opt -mmin -60 -type f` |
| Find files not owned by the service user | `find /opt/myapp ! -user myapp` |
| Find setuid files | `find / -xdev -type f -perm -4000` |
| Config without comments | `grep -Ev '^\s*(#|$)' file` |
| Top IPs in an access log | `awk '{print $1}' access.log \| sort \| uniq -c \| sort -rn \| head` |
| Safe in-place edit | `sed -i.bak 's/old/new/' file` |
| NUL-safe batch delete | `find DIR -name '*.tmp' -print0 \| xargs -0 -r rm -f` |
| Follow a rotating log | `tail -F /var/log/myapp/app.log` |
| User and group info | `getent passwd user`, `id user` |
| Add user to group (keep others) | `usermod -aG wheel user` |
| Create a service account | `useradd -r -d /opt/myapp -s /sbin/nologin myapp` |
| Create dirs with owner/mode | `install -d -o myapp -g myapp -m 0750 DIR` |
| Run as a nologin user | `sudo -u myapp CMD` |
| Recursive sane modes | `chmod -R u=rwX,g=rX,o= DIR` |
| Show umask | `umask`, `umask -S` |
| Set setgid on shared dir | `chmod 2775 DIR` |
| ACL view / add / default / remove | `getfacl F`, `setfacl -m u:U:rx F`, `setfacl -d -m g:G:rwx D`, `setfacl -b F` |
| Attributes | `lsattr F`, `chattr +i F`, `chattr -i F` |
| Top CPU processes | `ps -eo pid,user,stat,%cpu,%mem,cmd --sort=-%cpu \| head` |
| Find processes | `pgrep -a NAME`, `pgrep -u USER -l` |
| Stop politely, then forcefully | `kill PID`, then `kill -9 PID` |
| Lower priority | `nice -n 10 CMD`, `renice -n 15 -p PID` |
| Which package owns a file | `rpm -qf /path` (Debian: `dpkg -S /path`) |
| Which package provides a command | `dnf provides '*/bin/NAME'` |
| Verify a package / all packages | `rpm -V PKG`, `rpm -Va` |
| Restore package files / perms | `dnf reinstall PKG`, `rpm --setperms PKG`, `rpm --restore PKG` |
| Package history and undo | `dnf history`, `dnf history info N`, `dnf history undo N` |
| Find config conflicts after update | `find /etc -name '*.rpmnew' -o -name '*.rpmsave'` |
| Check sudo rights | `sudo -l -U user`, `visudo -c` |
| SELinux mode and labels | `getenforce`, `sestatus`, `ls -Z`, `ps -eZ` |
| SELinux denials | `ausearch -m AVC -ts recent`, `... \| audit2why` |
| Reset SELinux labels | `restorecon -Rv PATH` |

:::summary
- Know the layout: config in **`/etc`**, growing data and logs in **`/var`**, third-party apps in **`/opt`**, runtime state in **`/run`** (wiped at boot), kernel views in **`/proc`** and **`/sys`** (virtual).
- Use `find` with `-xdev`, `-mmin`, `! -user` and `-print0`; `grep -Ev '^\s*(#|$)'` for real config; `awk` for columns; `sed -i.bak` for safe edits; **`tail -F`** for rotating logs.
- Manage accounts with `useradd`/`usermod`/`gpasswd`, never a text editor; **always `usermod -aG`**. Services run as **system accounts with `/sbin/nologin`**; test them with `sudo -u`.
- Access needs **`x` on every parent directory**; `namei -l` shows the whole chain. Use `chmod -R u=rwX,...` rather than `777`. `umask` decides new-file modes. setuid/setgid/sticky are `4/2/1` in the leading digit.
- **ACLs** add named users/groups; the **mask** caps them. `chattr +i` blocks even root: "Operation not permitted" means check `lsattr`.
- Try `SIGTERM` before `SIGKILL`; `D`-state processes can't be killed; use `systemctl` for services.
- `rpm -qf` (owner), `rpm -V` (`S`ize, `M`ode, `5` digest, `U`ser, `G`roup, `T`ime; `c` = config), `dnf reinstall` / `rpm --setperms` (repair), `dnf history undo` (roll back).
- Method: **observe, hypothesise, test, fix, verify, document**. Change one thing at a time and revert diagnostic changes.
- On RHEL, **SELinux** is a second access check. `ls -Z` and `ausearch -m AVC` reveal it; `restorecon` fixes most label problems. `setenforce 0` is a brief diagnostic at most, never the fix.
:::

## Self-check questions

<div class="q" markdown="1">

**Q1.** A file `/data/reports/q3.csv` is mode `777`, but user `analyst` gets "Permission denied" when running `cat` on it. SELinux shows no denials. What do you check first?

A. Whether the disk is full
B. The permissions of every directory in the path, with `namei -l /data/reports/q3.csv`
C. Whether `analyst` has a valid login shell
D. Whether the file has a setuid bit

:::answer
**Answer: B.** Reading a file requires execute (traverse) permission on every parent directory. `namei -l` shows the full chain in one go.

- A is wrong: a full disk affects writes, not reading an existing file.
- C is wrong: the shell matters for logging in, not for an already-running `cat`.
- D is wrong: setuid on a data file is irrelevant (and a bad idea).
:::
</div>

<div class="q" markdown="1">

**Q2.** As root, `echo test >> /etc/hosts` returns "Operation not permitted". Permissions look normal (`644 root root`). What is the most likely cause?

A. SELinux is in Permissive mode
B. The file has the immutable attribute set
C. root's umask is `0077`
D. The file is owned by the wrong group

:::answer
**Answer: B.** "Operation not permitted" for root, with normal modes, strongly suggests `chattr +i` (or `+a`). Confirm with `lsattr /etc/hosts`.

- A is wrong: Permissive mode doesn't block anything.
- C is wrong: umask only affects newly created files, not appending to an existing one.
- D is wrong: root bypasses ordinary owner/group checks.
:::
</div>

<div class="q" markdown="1">

**Q3.** An engineer runs `usermod -G docker alice` and shortly afterwards alice reports she can no longer use `sudo`. Why?

A. The docker group conflicts with wheel
B. `-G` without `-a` replaced her supplementary groups, removing her from `wheel`
C. alice's password expired
D. sudo caches group membership for 24 hours

:::answer
**Answer: B.** `usermod -G` sets the complete list of supplementary groups. Without `-a` (append), every group not listed, including `wheel`, is removed. The fix is `usermod -aG wheel alice`.

- A is wrong: groups don't conflict like that.
- C is wrong: possible in general, but the timing points directly at the `usermod` command.
- D is wrong: sudo has no 24-hour group cache; credential caching (the timestamp) is about passwords, by default a few minutes.
:::
</div>

<div class="q" markdown="1">

**Q4.** After a deploy, Apache returns 403 for a new page. The file is `644`, parents are `755`, and `ls -Z` shows `admin_home_t`. What is the correct fix?

A. `setenforce 0` and add `SELINUX=permissive` to `/etc/selinux/config`
B. `chmod 777` on the file
C. `restorecon -v` on the file so it gets the label the policy expects for `/var/www/html`
D. Run Apache as root

:::answer
**Answer: C.** The file was probably created in root's home and moved, so it kept `admin_home_t`. `restorecon` applies the correct `httpd_sys_content_t` label.

- A is wrong: disabling enforcement removes protection and is never the fix.
- B is wrong: the modes are already sufficient; SELinux is the blocker.
- D is wrong: it would massively increase risk and still wouldn't change the SELinux type check.
:::
</div>

<div class="q" markdown="1">

**Q5.** `rpm -V openssh-server` prints `S.5....T.  c /etc/ssh/sshd_config`. What does that tell you?

A. The sshd binary has been tampered with
B. The config file's size, content and modification time differ from the packaged version, which is normal if an admin edited it
C. The file is missing
D. The file's owner and group have changed

:::answer
**Answer: B.** `S` = size, `5` = digest, `T` = time; `c` marks a config file, where edits are expected. Review the diff if you are unsure who changed it.

- A is wrong: the line is about the config file, not a binary.
- C is wrong: a missing file shows `missing`.
- D is wrong: owner and group changes show as `U` and `G`.
:::
</div>

<div class="q" markdown="1">

**Q6.** `rpm -Va` reports `S.5....T.    /usr/sbin/sshd` on a server with no recent package updates. What should you do first?

A. Ignore it; binaries change during normal operation
B. Treat it as suspicious: check `dnf history`, compare against a trusted copy, and involve security before simply reinstalling
C. Delete `/usr/sbin/sshd`
D. Run `rpm --setperms openssh-server`

:::answer
**Answer: B.** A binary whose content changed without a package update could be corruption or a compromise. Preserve evidence, compare with a known-good source, and follow your incident process. `dnf reinstall` restores it once you've investigated.

- A is wrong: binaries do not change during normal operation.
- C is wrong: you'd lose evidence and SSH access.
- D is wrong: `--setperms` only fixes modes; the content (`5`) differs.
:::
</div>

<div class="q" markdown="1">

**Q7.** A deployment script copies new files into `/opt/myapp` as root. Afterwards the service, running as user `myapp`, fails with "Permission denied" writing its log. Which command finds every affected file?

A. `find /opt/myapp ! -user myapp`
B. `ls -l /opt`
C. `grep -r myapp /opt/myapp`
D. `rpm -V myapp`

:::answer
**Answer: A.** `! -user myapp` lists everything not owned by the service user, which is exactly what the root copy created. Fix with `chown -R myapp:myapp` and change the deploy to set ownership.

- B is wrong: it shows only the top-level directory.
- C is wrong: it searches file contents, not ownership.
- D is wrong: `/opt/myapp` is not from an rpm package here, and `rpm -V` doesn't help with unpackaged files.
:::
</div>

<div class="q" markdown="1">

**Q8.** You need to debug as the service account `myapp`, whose shell is `/sbin/nologin`. `su - myapp` prints "This account is currently not available." What is the right approach?

A. Change the account's shell to `/bin/bash` permanently
B. Use `sudo -u myapp <command>` (or `sudo -u myapp -s /bin/bash` briefly)
C. Set a password on the account and SSH in
D. Delete and recreate the account with a home directory

:::answer
**Answer: B.** `sudo -u` runs commands as the user without needing a login shell. The nologin shell is a deliberate security control.

- A is wrong: it weakens the account permanently for a temporary need.
- C is wrong: service accounts should not have passwords or interactive logins.
- D is wrong: unnecessary, disruptive, and it may change the UID and orphan files.
:::
</div>

<div class="q" markdown="1">

**Q9.** You add `deploy ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart myapp` to `/etc/sudoers.d/deploy.rules`. `visudo -c` reports OK, but the rule doesn't work. Why?

A. NOPASSWD rules are disabled on RHEL 9
B. sudo ignores files in `/etc/sudoers.d` whose names contain a dot
C. The file must be mode `0777`
D. sudo must be restarted

:::answer
**Answer: B.** Files in the include directory whose names contain `.` or end with `~` are skipped. Rename it to `/etc/sudoers.d/deploy` and check with `sudo -l -U deploy`.

- A is wrong: NOPASSWD works on RHEL 9.
- C is wrong: sudoers files should be `0440`; a world-writable sudoers file is rejected.
- D is wrong: sudo is not a daemon; it reads its config every time it runs.
:::
</div>

<div class="q" markdown="1">

**Q10.** A process is in state `D` and ignores `kill -9`. Load average is climbing. What is the most likely explanation?

A. The process is a zombie waiting for its parent
B. The process is stuck in uninterruptible I/O, often due to storage or NFS problems
C. The process has a nice value of -20
D. SIGKILL was blocked by the application's signal handler

:::answer
**Answer: B.** `D` means uninterruptible sleep, usually waiting for I/O. Signals are delivered only when the kernel call returns, so fix the storage or network path. D-state tasks also count towards the load average.

- A is wrong: zombies show `Z`.
- C is wrong: priority has nothing to do with killability.
- D is wrong: SIGKILL cannot be caught or blocked.
:::
</div>

<div class="q" markdown="1">

**Q11.** A web app broke after last night's maintenance window. Following the troubleshooting method, what is the best first action?

A. Reboot the server
B. Observe: read the exact error and check what changed, for example `dnf history` and recently modified files under `/etc`
C. Set SELinux to Permissive to rule it out
D. Run `chmod -R 755 /var/www`

:::answer
**Answer: B.** Collect facts before changing anything. "What changed?" usually points straight at the cause after a maintenance window, and `dnf history undo` may be the quickest safe fix.

- A is wrong: rebooting destroys evidence and rarely fixes a config or package regression.
- C is wrong: it's a change, not an observation, and it lowers security.
- D is wrong: a random broad change that may create new problems and hides the real cause.
:::
</div>

<div class="q" markdown="1">

**Q12.** A shared directory `/srv/projects` has `group:deploy:rwx` in its ACL, but members of `deploy` can only read. `getfacl` shows `group:deploy:rwx   #effective:r-x`. What fixes it?

A. `chmod 777 /srv/projects`
B. `setfacl -m m::rwx /srv/projects` to raise the ACL mask
C. `chattr -i /srv/projects`
D. `usermod -aG deploy root`

:::answer
**Answer: B.** The ACL mask (here `r-x`) caps the effective rights of named users and groups. Raising the mask restores `rwx` for `deploy`. Someone probably ran `chmod g-w`, which changed the mask.

- A is wrong: it opens the directory to everyone, which is a security problem, even if it changes the mask.
- C is wrong: the immutable attribute would give "Operation not permitted", and the ACL output explains the issue.
- D is wrong: root's group membership is irrelevant to the deploy group's rights.
:::
</div>
