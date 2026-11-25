<div class="cover" markdown="1">
<div class="mod">Module 06 · SELinux Fundamentals</div>

# SELinux Fundamentals: Contexts, Labels, Booleans, Ports and Reading AVC Denials

<p>What SELinux is protecting you from, how to read the labels on processes, files and ports, and a repeatable method for turning a cryptic "Permission denied" into the one correct fix, without ever reaching for setenforce 0.</p>
</div>

## What you'll learn

- Why **Mandatory Access Control (MAC)** exists on top of normal Unix permissions (**DAC**), told through a compromised web server
- The three **modes** (enforcing, permissive, disabled), how to read and change them, why disabling is a trap, and how to make **one domain** permissive instead of the whole box
- The **targeted policy**, the **security context** `user:role:type:level`, and **type enforcement**: domains for processes, types for files and ports
- How to **view contexts** with `ls -Z`, `ps -eZ`, `id -Z`, `ss -Z` and `semanage port -l`
- **File labeling**: default rules, `restorecon`, `chcon` versus `semanage fcontext`, why `cp` and `mv` behave differently, and full relabels with `/.autorelabel` and `fixfiles`
- **Booleans** and **port labels**: the two policy switches you will touch most often
- The **audit log**: an AVC denial decoded field by field, then `ausearch`, `aureport`, `sealert`, `audit2why` and `audit2allow`, plus `dontaudit` rules and policy **modules**
- How Ubuntu's **AppArmor** compares
- Four break-fix labs: a broken file label, a boolean, a non-standard port, and decoding a real AVC end to end

**Why it matters in production:** on RHEL-family servers, a large share of "the service starts but does not work" and "Permission denied, but the permissions are fine" tickets are SELinux. Engineers who cannot read an AVC either waste hours or turn SELinux off, which removes the one control that stops a compromised service from becoming a compromised server. Engineers who can read one fix the problem in five minutes and leave the box more secure than they found it.

## 1. Why SELinux exists: DAC versus MAC

### Discretionary Access Control (DAC)

Classic Unix permissions are **discretionary**: the owner of a file decides who can read it (`chmod`, `chown`, ACLs), and every process runs with the full rights of the user it runs as. The kernel asks one question: "Does UID 48 have read permission on this inode?" If yes, the read succeeds. It does not care *which program* is running as UID 48 or *why* it wants the file.

That model has two weaknesses that matter on a server:

1. **Root bypasses everything.** A process running as root, or one that escalates to root, ignores DAC completely.
2. **A service account can reach everything its UID can reach.** The `apache` user can read any world-readable file on the system, write to `/tmp` and `/var/tmp`, execute anything with the execute bit, and open outbound connections to anywhere.

### Mandatory Access Control (MAC)

SELinux adds a second, **mandatory** check that runs *after* DAC allows an action. Every process and every object (file, directory, socket, port, device) carries a **label**. A central **policy**, loaded into the kernel and not editable by ordinary users or file owners, says which labels may do what to which other labels. If the policy does not explicitly allow an action, it is denied, even for root.

| | DAC (Unix permissions) | MAC (SELinux) |
|---|---|---|
| Who decides | The file owner | The system policy, set by the administrator |
| Based on | User and group IDs, mode bits, ACLs | Labels (contexts) on subjects and objects |
| Root | Bypasses it | Still constrained if root runs in a confined domain |
| Default | Allow if the bits say so | **Deny** unless the policy has an allow rule |
| Order | Checked first | Checked only if DAC already allowed |

:::concept
SELinux never *grants* access that DAC denies. If `chmod` says no, SELinux is never consulted. That is why the first step in any "Permission denied" diagnosis is still `ls -l` and `namei -l`: you need to know which layer said no.
:::

:::story
**The badge and the building.** DAC is a front-door key: anyone holding a copy of the key gets into every room the key opens. SELinux is a security guard at every internal door with a list that says "kitchen staff may enter the kitchen and the loading dock, nothing else". Steal a kitchen worker's key and you still cannot walk into the server room, because the guard checks your *role*, not your key.
:::

### A compromised httpd, with and without SELinux

A web application at `app.example.com` has a file-upload bug. An attacker uploads a PHP web shell and gets remote code execution as the `apache` user.

**With SELinux disabled**, the attacker's shell, running as `apache`, can:

- read `/etc/passwd`, every world-readable file under `/home`, and any config file with `644` permissions that someone forgot to tighten (database passwords in `/opt/myapp/config.yml`, for example)
- download a cryptominer or a reverse shell into `/tmp` and execute it
- open outbound connections to the attacker's server and scan the internal `10.0.0.0/24` network
- try local privilege escalation exploits; if one works, the box is gone

**With SELinux enforcing**, the same shell runs in the `httpd_t` domain. The targeted policy only lets `httpd_t`:

- read files labeled for web content (`httpd_sys_content_t` and friends), not `user_home_t`, not `admin_home_t`, not your app's config unless you labeled it for httpd
- write only to types labeled writable for httpd (`httpd_sys_rw_content_t`, its log and cache types)
- connect out only to a small set of ports, and only to arbitrary ports if the administrator flipped `httpd_can_network_connect`
- not execute arbitrary binaries it drops in `/tmp`, depending on booleans such as `httpd_execmem` and `httpd_tmp_exec`

Every blocked attempt is logged as an **AVC denial** in `/var/log/audit/audit.log`, which gives your SOC a loud, early signal. SELinux does not fix the upload bug, but it shrinks the blast radius from "the server" to "the web content directory".

:::prod
**The proxy that "worked on staging".** Symptom: after a release, the reverse proxy on a RHEL 9 web tier returned `503 Service Unavailable` for every request. Staging, which had SELinux disabled years ago "to save time", was fine. The engineer saw `AH00957: http: attempt to connect to 127.0.0.1:8000 (127.0.0.1) failed` with `(13)Permission denied` in `/var/log/httpd/error_log`. Root cause: the new config proxied to a local backend on port 8000, and `httpd_t` is not allowed to connect to arbitrary ports unless `httpd_can_network_connect` is on. Fix: `setsebool -P httpd_can_network_connect on`. Prevention: staging re-enabled in enforcing mode (with a full relabel), the boolean added to the Ansible role, and a CI check that greps for AVCs after deployment smoke tests.
:::

## 2. SELinux modes

### The three modes

| Mode | Policy loaded? | Denies? | Logs AVCs? | Use it for |
|---|---|---|---|---|
| **Enforcing** | Yes | Yes | Yes | Production. Always. |
| **Permissive** | Yes | No | Yes, as if it had denied | Short diagnosis windows, initial relabel after re-enabling, developing new policy |
| **Disabled** | No | No | No | Almost never. See below. |

In permissive mode SELinux still computes every decision and logs what it *would* have denied, with `permissive=1` at the end of the AVC record. Files created while permissive still get correct labels. In disabled mode the kernel does not label anything, so new files have no labels at all.

### Checking the mode

```bash
getenforce
```

```text
Enforcing
```

```bash
sestatus
```

```text
SELinux status:                 enabled
SELinuxfs mount:                /sys/fs/selinux
SELinux root directory:         /etc/selinux
Loaded policy name:             targeted
Current mode:                   enforcing
Mode from config file:          enforcing
Policy MLS status:              enabled
Policy deny_unknown status:     allowed
Memory protection checking:     actual (secure)
Max kernel policy version:      33
```

How to read it: **Current mode** is what the kernel is doing right now. **Mode from config file** is what it will do after the next boot. If they differ, someone ran `setenforce` and the box will change behaviour at the next reboot, which is a classic source of "it broke after patching" incidents. **Loaded policy name** should be `targeted` on almost every RHEL server.

### Changing the mode at runtime

```bash
setenforce 0     # permissive, until reboot or setenforce 1
setenforce 1     # back to enforcing
```

`setenforce` only switches between enforcing and permissive. It cannot enable SELinux on a system booted with it disabled (`setenforce: SELinux is disabled`), and it does not survive a reboot.

:::warn
`setenforce 0` is a **diagnostic**, never a fix. The legitimate use is: reproduce the problem, run `setenforce 0`, retry once. If the problem disappears, you have proved it is SELinux, so run `setenforce 1` immediately and go read the AVC. Leaving a production box permissive "until we have time" means every service on it is unconfined, and it will still be permissive six months later. A better diagnostic that leaves everything else protected is per-domain permissive, below.
:::

### The persistent setting: /etc/selinux/config

```ini
# /etc/selinux/config
SELINUX=enforcing
SELINUXTYPE=targeted
```

`SELINUX=` accepts `enforcing`, `permissive` or `disabled`. `SELINUXTYPE=` names the policy directory under `/etc/selinux/` (`targeted`, or `mls` on specialised systems).

:::warn
**RHEL 9 changed what "disabled" means.** On RHEL 9 and its rebuilds, `SELINUX=disabled` in `/etc/selinux/config` no longer fully disables SELinux at boot: the kernel starts with SELinux enabled but no policy loaded. Fully disabling requires the kernel argument `selinux=0` (for example `grubby --update-kernel ALL --args selinux=0`). On RHEL 8 the config file alone still worked. If you inherit a box with `selinux=0` on the kernel command line, check `cat /proc/cmdline` and remove it with `grubby --update-kernel ALL --remove-args selinux` as part of re-enabling.
:::

### Why disabling is bad, and what re-enabling costs

Disabling SELinux does three kinds of damage:

1. **Every confined service becomes unconfined.** The compromised-httpd story above becomes the "without SELinux" version.
2. **Labels rot.** While disabled, every new or rewritten file is created without a label. Package updates, log rotation, config management runs and application deployments all create unlabeled files.
3. **Coming back is expensive.** When you re-enable, those files either have no label or the wrong one, and confined services will be denied access to them. You must relabel the whole filesystem before enforcing.

The safe re-enable procedure:

```bash
# 1. Set permissive (not enforcing) in the config, remove selinux=0 if present
sed -i 's/^SELINUX=.*/SELINUX=permissive/' /etc/selinux/config
grubby --update-kernel ALL --remove-args selinux

# 2. Request a full relabel on the next boot
touch /.autorelabel        # or: fixfiles -F onboot

# 3. Reboot; the relabel runs early in boot, then the system reboots again
reboot
```

After it comes back, run the workload for a while in permissive, check `ausearch -m AVC -ts today` for denials and fix them properly, then set `SELINUX=enforcing` and `setenforce 1`.

:::tip
A full relabel walks every file on every SELinux-capable local filesystem. On a server with tens of millions of small files (mail spools, build caches, image stores) this can take an hour or more, during which the box is not serving. Schedule it in a maintenance window and warn the change board. This cost is the best argument for never disabling SELinux in the first place.
:::

### Per-domain permissive: the precise diagnostic

Instead of making the whole box permissive, make **one domain** permissive. Everything else stays enforced.

```bash
semanage permissive -a httpd_t        # httpd_t is permissive, rest enforcing
semanage permissive -l                # list permissive domains
semanage permissive -d httpd_t        # back to enforcing for httpd_t
```

```text
Customized Permissive Types

httpd_t

Builtin Permissive Types

```

This is persistent across reboots (it is a small policy module), so treat it like `setenforce 0`: a timed diagnostic with a ticket to remove it. AVCs from that domain will show `permissive=1`.

## 3. The targeted policy and security contexts

### The targeted policy

RHEL ships the **targeted** policy. "Targeted" means that network-facing and system services (httpd, sshd, named, postfix, mariadb, nginx, samba, containers and hundreds more) run in their own **confined domains**, while interactive users and anything without a specific policy run in **unconfined** domains (`unconfined_t`, `initrc_t`, `unconfined_service_t`) that SELinux barely restricts.

So when you log in as root and `cat` a file, SELinux rarely stops you. When `httpd` tries to read the same file, it might. That asymmetry is why "it works when I run it by hand" is such a common SELinux symptom.

:::concept
A daemon started from a binary that has no specific policy, such as `/opt/myapp/bin/server` launched by a systemd unit, usually runs as `unconfined_service_t`. It works, but it has no SELinux protection. Confining your own apps needs a custom policy module, which is beyond this module; knowing that it is unconfined is not.
:::

### The security context: user:role:type:level

Every process and object has a context with four colon-separated fields:

```text
system_u:object_r:httpd_sys_content_t:s0
   |        |             |            |
  user     role          type        level
```

| Field | Examples | What it means in the targeted policy |
|---|---|---|
| **SELinux user** | `system_u`, `unconfined_u`, `staff_u`, `user_u` | An SELinux identity (not a Linux user). Linux logins are mapped to SELinux users; see `semanage login -l`. System processes and files use `system_u`. |
| **Role** | `object_r`, `system_r`, `unconfined_r` | Controls which domains a user may enter (RBAC). Files always have `object_r`. Daemons have `system_r`. |
| **Type** | `httpd_t`, `httpd_sys_content_t`, `http_port_t` | **The field that matters 95% of the time.** For processes it is called a **domain**. |
| **Level** | `s0`, `s0-s0:c0.c1023`, `s0:c123,c456` | MLS/MCS sensitivity and categories. In targeted policy almost everything is `s0`. Categories isolate containers and VMs from each other (sVirt). |

### Type enforcement

**Type enforcement (TE)** is the core rule set. Rules look like this:

```text
allow httpd_t httpd_sys_content_t:file { read open getattr };
allow httpd_t http_port_t:tcp_socket name_bind;
```

Read the first as: "processes in domain `httpd_t` may read, open and get attributes of objects of class `file` that have type `httpd_sys_content_t`". There is no rule letting `httpd_t` read `user_home_t` files, so that access is denied.

- **Processes** run in **domains** (types ending in `_t` assigned to processes, such as `httpd_t`, `sshd_t`, `mysqld_t`).
- **Files and directories** have **types** describing what they are (`httpd_sys_content_t`, `etc_t`, `var_log_t`, `user_home_t`).
- **Ports** have **types** too (`http_port_t`, `ssh_port_t`, `mysqld_port_t`).

A process gets its domain through a **domain transition**: systemd (`init_t`) executes `/usr/sbin/httpd`, which is labeled `httpd_exec_t`, and a policy rule says "when `init_t` executes `httpd_exec_t`, the new process runs as `httpd_t`". This is why the label on the *binary* matters: copy a daemon binary to a new path without the right `_exec_t` label and it will start in the wrong domain.

:::tip
Want to know whether a rule exists without waiting for a denial? Install `setools-console` and ask the policy directly: `sesearch --allow -s httpd_t -t user_home_t -c file`. No output means no allow rule. `seinfo -t | grep httpd` lists types.
:::

## 4. Viewing contexts

Most core tools take a `-Z` flag that adds the context column.

### Files: ls -Z

```bash
ls -lZ /var/www/html/
```

```text
-rw-r--r--. 1 root root unconfined_u:object_r:httpd_sys_content_t:s0 612 Nov 25 10:02 index.html
-rw-r--r--. 1 root root unconfined_u:object_r:admin_home_t:s0          45 Nov 25 10:05 report.html
```

The trailing dot in `-rw-r--r--.` means "this file has an SELinux context and no other ACL". A `+` would mean a POSIX ACL. Here `report.html` has `admin_home_t`, which means it was created in `/root` and then *moved* here. Apache will get `403 Forbidden` on it. You can spot this in one glance once you know to look.

`ls -dZ /var/www/html` shows the directory's own label.

### Processes: ps -eZ

```bash
ps -eZ | grep -E 'httpd|sshd'
```

```text
system_u:system_r:httpd_t:s0       1187 ?        00:00:00 httpd
system_u:system_r:httpd_t:s0       1188 ?        00:00:00 httpd
system_u:system_r:sshd_t:s0-s0:c0.c1023 1022 ? 00:00:00 sshd
```

If a daemon shows `unconfined_service_t` or `initrc_t` where you expected a specific domain, its binary is mislabeled or it was started in an unusual way.

### Your own shell: id -Z

```bash
id -Z
```

```text
unconfined_u:unconfined_r:unconfined_t:s0-s0:c0.c1023
```

That is a normal admin login on the targeted policy: unconfined. This is why running the service's command by hand as root "works" while the service fails.

### Sockets and ports: ss -Z, netstat -Z, semanage port -l

```bash
ss -tlnpZ
```

```text
State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process
LISTEN 0      511          0.0.0.0:80        0.0.0.0:*     users:(("httpd",pid=1187,proc_ctx=system_u:system_r:httpd_t:s0,fd=4),...)
LISTEN 0      128          0.0.0.0:22        0.0.0.0:*     users:(("sshd",pid=1022,proc_ctx=system_u:system_r:sshd_t:s0-s0:c0.c1023,fd=3))
```

`-Z` shows the context of the process owning each socket (it needs `-p`, and root to see other users' processes). On RHEL the `net-tools` `netstat` also accepts `-Z` (`netstat -tlnpZ`), but `ss` is the modern tool.

Ports are not files, so their labels live in policy. List them with `semanage` (package `policycoreutils-python-utils`):

```bash
semanage port -l | grep -w http_port_t
```

```text
http_port_t                    tcp      80, 81, 443, 488, 8008, 8009, 8443, 9000
```

```bash
semanage port -l | grep -w 9090
```

```text
websm_port_t                   tcp      9090
```

Port 9090 already belongs to `websm_port_t` (the Cockpit web console). That matters in section 7.

| Want to see | Command |
|---|---|
| File or directory label | `ls -Z file`, `ls -dZ dir`, `stat -c %C file` |
| Process domain | `ps -eZ`, `ps -Z -p PID`, `ps auxZ` |
| Your shell | `id -Z` |
| Socket owner contexts | `ss -tlnpZ` |
| Port types | `semanage port -l` |
| Linux login to SELinux user mapping | `semanage login -l` |

## 5. File labeling

### Where labels come from

A file's label is stored in the `security.selinux` extended attribute on the inode. It gets set in one of three ways:

1. **Inheritance at creation.** A new file normally gets the type of its parent directory (subject to **type transition** rules, such as "files httpd creates in `/var/log/httpd` get `httpd_log_t`").
2. **Relabeling from the policy's default rules** with `restorecon`, `fixfiles`, or a full autorelabel.
3. **Manual change** with `chcon`.

### The default rules: semanage fcontext -l

The policy contains a database of regular expressions mapping paths to the label they *should* have. It is compiled into `/etc/selinux/targeted/contexts/files/file_contexts` (plus `.local` for your customisations and `.homedirs` for home directories).

```bash
semanage fcontext -l | grep '^/var/www'
```

```text
/var/www(/.*)?                        all files   system_u:object_r:httpd_sys_content_t:s0
/var/www(/.*)?/logs(/.*)?             all files   system_u:object_r:httpd_log_t:s0
/var/www/cgi-bin(/.*)?                all files   system_u:object_r:httpd_sys_script_exec_t:s0
/var/www/html(/.*)?/uploads(/.*)?     all files   system_u:object_r:httpd_sys_rw_content_t:s0
...
```

(Exact entries vary slightly by policy version.) `semanage fcontext -l -C` lists only **your local customisations**, which is the first thing to check on an inherited server.

### What should this file be labeled? matchpathcon and restorecon -n

```bash
matchpathcon /var/www/html/report.html
```

```text
/var/www/html/report.html	system_u:object_r:httpd_sys_content_t:s0
```

`matchpathcon` (package `libselinux-utils`) prints the default label for a path. A more modern equivalent that also tells you whether the current label is wrong is a restorecon **dry run**:

```bash
restorecon -nv /var/www/html/report.html
```

```text
Would relabel /var/www/html/report.html from unconfined_u:object_r:admin_home_t:s0 to unconfined_u:object_r:httpd_sys_content_t:s0
```

### Fixing labels: restorecon

```bash
restorecon -Rv /var/www/html
```

```text
Relabeled /var/www/html/report.html from unconfined_u:object_r:admin_home_t:s0 to unconfined_u:object_r:httpd_sys_content_t:s0
```

`-R` recurses, `-v` prints what changed (if it prints nothing, nothing was wrong), `-n` is dry run, `-F` also resets the user and role fields, not just the type. `restorecon` is always safe: it only ever sets files to what the policy says they should be.

### chcon (temporary) versus semanage fcontext (permanent)

`chcon` writes a label directly onto the file:

```bash
chcon -t httpd_sys_content_t /srv/web/index.html
chcon -R --reference=/var/www/html /srv/web
```

It works immediately, and it is **not persistent** in the way that matters: the next `restorecon`, `fixfiles` or autorelabel puts the file back to whatever the default rules say. Package scripts, Ansible roles and other admins run `restorecon` routinely, so a `chcon` fix tends to vanish weeks later, at night.

The permanent fix is to **change the rule**, then apply it:

```bash
semanage fcontext -a -t httpd_sys_content_t "/srv/web(/.*)?"
restorecon -Rv /srv/web
```

Notes on the syntax:

- The path is a **regular expression**, quoted. `"/srv/web(/.*)?"` matches the directory and everything below it.
- `-a` adds, `-m` modifies an existing local rule, `-d` deletes a local rule.
- For writable areas use the rw type: `semanage fcontext -a -t httpd_sys_rw_content_t "/srv/web/uploads(/.*)?"`.
- **Equivalence rules** say "label this tree exactly like that tree": `semanage fcontext -a -e /var/www /srv/web`. Useful for relocated data directories (`-e /var/lib/mysql /data/mysql`).

:::tip
`semanage fcontext` only edits the rule database; it changes **no file labels**. Forgetting the `restorecon` that follows is the most common "I added the rule and it still fails" mistake. Always run the pair together.
:::

:::tip
If you automate with Ansible: `community.general.sefcontext` (followed by a handler running `restorecon`), `ansible.posix.seboolean` and `community.general.seport` are idempotent equivalents of `semanage fcontext`, `setsebool -P` and `semanage port`. `semanage export > selinux-local.txt` dumps every local customisation on a box, which is a quick way to audit drift between servers.
:::

### cp versus mv: why moved files break

| Operation | Resulting label | Why |
|---|---|---|
| `cp file /var/www/html/` | `httpd_sys_content_t` (correct) | A **new** inode is created in the destination and inherits from the target directory |
| `mv file /var/www/html/` (same filesystem) | **Keeps the old label**, e.g. `admin_home_t` or `user_home_t` | `mv` renames the existing inode; its xattr travels with it |
| `cp -a file /var/www/html/` or `cp --preserve=context` | **Keeps the old label** | You asked it to preserve the context |
| `mv -Z file /var/www/html/` or `cp -Z` | Default label for the destination | `-Z` sets the default context (coreutils 8.22+) |
| `tar -x`, `rsync -a` | Depends: `tar --selinux` / `rsync -X` restore stored labels; otherwise inherit | Check with `ls -Z` after |

The classic incident: someone builds a page in `/root`, runs `mv index.html /var/www/html/`, and the site returns 403. `restorecon -Rv /var/www/html` fixes it. Better habit: deploy with `cp` or `install`, or always run `restorecon` at the end of the deploy script.

### Whole-filesystem relabels: /.autorelabel and fixfiles

| Command | What it does |
|---|---|
| `touch /.autorelabel` then `reboot` | Full relabel of all local filesystems early in the next boot, then an automatic reboot |
| `fixfiles -F onboot` | Same thing, with `-F` forcing user/role reset; writes `/.autorelabel` for you |
| `fixfiles check` | Report what would change, change nothing |
| `fixfiles restore` / `fixfiles relabel` | Relabel now, on a running system (`relabel` may offer to clean `/tmp`; prefer the boot-time method on production) |
| `fixfiles -R httpd restore` | Relabel only the files owned by the `httpd` RPM package |

Reasons to do a full relabel: re-enabling after disabled, restoring a filesystem from a backup that did not keep xattrs, or recovering after someone ran a recursive `chcon` on `/`.

:::prod
**The root password reset that locked everyone out.** Symptom: after an on-call engineer reset a forgotten root password by booting with `rd.break`, nobody could log in at all, not even on the console. Root cause: `/etc/shadow` had been rewritten from the emergency shell without SELinux running, so the new file had no proper label (`unlabeled_t`), and the login process could not read it. Fix: boot again into `rd.break`, `chroot /sysroot`, `touch /.autorelabel`, exit, and let the relabel complete. Prevention: the documented password-reset runbook now ends with `touch /.autorelabel` as a mandatory step (or `load_policy -i` and `restorecon /etc/shadow` before exiting the chroot).
:::

## 6. Booleans

Policy writers know that some behaviours are needed by some sites but not others: should httpd be allowed to talk to a database? to serve user home directories? to use NFS? Rather than force you to write policy, they wrap those rules in **booleans**: named on/off switches.

### Listing booleans

```bash
getsebool -a | grep httpd | head
```

```text
httpd_anon_write --> off
httpd_builtin_scripting --> on
httpd_can_check_spam --> off
httpd_can_connect_ftp --> off
httpd_can_connect_ldap --> off
httpd_can_connect_mythtv --> off
httpd_can_connect_zabbix --> off
httpd_can_network_connect --> off
httpd_can_network_connect_cobbler --> off
httpd_can_network_connect_db --> off
```

`semanage boolean -l` adds the default value and a description, which is far more useful:

```bash
semanage boolean -l | grep httpd_can_network
```

```text
httpd_can_network_connect      (off  ,  off)  Allow httpd to can network connect
httpd_can_network_connect_cobbler (off  ,  off)  Allow httpd to can network connect cobbler
httpd_can_network_connect_db   (off  ,  off)  Allow httpd to can network connect db
httpd_can_network_memcache     (off  ,  off)  Allow httpd to can network memcache
httpd_can_network_relay        (off  ,  off)  Allow httpd to can network relay
```

The pair in brackets is **(current, persistent default)**. `(on , off)` means someone turned it on at runtime without `-P` and it will revert at reboot. `semanage boolean -l -C` lists only booleans changed from the shipped defaults.

### Changing booleans

```bash
setsebool httpd_can_network_connect on       # runtime only, lost at reboot
setsebool -P httpd_can_network_connect on    # runtime AND persistent
getsebool httpd_can_network_connect
```

```text
httpd_can_network_connect --> on
```

Always use `-P` for a real fix. `setsebool -P` rebuilds part of the policy and can take several seconds; that is normal.

### Booleans you will meet in production

| Boolean | Turn it on when | Notes |
|---|---|---|
| `httpd_can_network_connect` | httpd/nginx/php-fpm must connect to arbitrary TCP ports: reverse proxy to an app server, calling external APIs | Broad. If you only need a database, prefer the narrower boolean below. |
| `httpd_can_network_connect_db` | Web app connects to MySQL/MariaDB/PostgreSQL/etc. on their standard ports | Covers database port types only |
| `httpd_enable_homedirs` | Serving `~user/public_html` (UserDir) | Also needs DAC: `chmod 711 ~user` and content labeled `httpd_user_content_t` |
| `httpd_use_nfs` | Document root lives on an NFS mount | NFS files appear as `nfs_t` unless mounted with `context=` |
| `httpd_use_cifs` | Document root on an SMB/CIFS mount | Files appear as `cifs_t` |
| `httpd_can_sendmail` | Web app sends mail via local sendmail/postfix | Common for PHP contact forms |
| `samba_enable_home_dirs` | Samba shares users' home directories | For other shared paths, label them `samba_share_t` instead |
| `samba_export_all_ro` / `samba_export_all_rw` | Samba must share paths you cannot relabel | Very broad; relabeling is better |
| `ftpd_full_access` | vsftpd local users must read/write anywhere DAC allows | Broad; prefer `public_content_rw_t` labels plus `ftpd_anon_write` for anonymous upload areas |
| `ftpd_use_nfs` | FTP root on NFS | |
| `container_manage_cgroup` | A container runs systemd as PID 1 (e.g. `podman run ... /sbin/init`) | Needed for systemd-in-container to manage cgroups |
| `virt_use_nfs` | VM disk images on NFS | |
| `nis_enabled` | Services must bind/connect to arbitrary ports for NIS-style lookups | Very broad; usually a sign of a wrong fix |

:::warn
Booleans like `httpd_can_network_connect` and `ftpd_full_access` open a lot. Before flipping one, ask whether a narrower boolean or a label change solves the problem. `audit2why` (section 8) will often name the exact boolean the policy author intended.
:::

## 7. Port labels

A confined daemon may only `bind` to ports labeled with types its policy allows. Apache may bind `http_port_t`; sshd may bind `ssh_port_t`. Move a service to a non-standard port and it will fail to start, even though nothing else is using the port.

Typical errors:

```text
(13)Permission denied: AH00072: make_sock: could not bind to address [::]:8085
(13)Permission denied: AH00072: make_sock: could not bind to address 0.0.0.0:8085
no listening sockets available, shutting down
```

```text
sshd[2210]: error: Bind to port 2222 on 0.0.0.0 failed: Permission denied.
```

### Adding, modifying and deleting port labels

```bash
semanage port -a -t http_port_t -p tcp 8085      # add a new mapping
semanage port -a -t ssh_port_t  -p tcp 2222      # sshd on 2222
semanage port -l -C                              # list local customisations
```

```text
SELinux Port Type              Proto    Port Number

http_port_t                    tcp      8085
ssh_port_t                     tcp      2222
```

If the port already belongs to another type, `-a` fails and you must use `-m` (modify):

```bash
semanage port -a -t http_port_t -p tcp 9090
```

```text
ValueError: Port tcp/9090 already defined
```

```bash
semanage port -m -t http_port_t -p tcp 9090
```

Think before you do that: you have just told the policy that 9090 is a web port, and Cockpit (`websm_port_t`) will no longer be able to bind it. Picking an unassigned port is cleaner.

```bash
semanage port -d -t http_port_t -p tcp 8085      # remove YOUR mapping
```

You can only delete local customisations. Trying to delete a policy-defined mapping gives an error like `ValueError: Port tcp/80 is defined in policy, cannot be deleted`.

:::tip
SELinux and the firewall are separate layers. A service on a new port usually needs **both** `semanage port -a` (so it can bind) and `firewall-cmd --permanent --add-port=8085/tcp && firewall-cmd --reload` (so clients can reach it). If the service starts but clients time out, it is the firewall; if the service will not start at all with `Permission denied` on bind, it is SELinux.
:::

Port types also govern outbound `name_connect`. A web app connecting to PostgreSQL on 5432 (`postgresql_port_t`) is allowed by `httpd_can_network_connect_db`. If your database listens on a non-standard port, label that port with the database type (`semanage port -a -t postgresql_port_t -p tcp 15432`) rather than enabling the broad `httpd_can_network_connect`.

## 8. The audit log and AVC denials

### Where denials go

When `auditd` is running, SELinux denials are written to **`/var/log/audit/audit.log`**. If `auditd` is not running, they go to the kernel log, visible with `dmesg` or `journalctl -k`. If `setroubleshoot-server` is installed, a human-readable summary also appears in the journal.

`audit.log` is readable only by root. Use `ausearch` rather than `grep` where you can, because it joins related records and can translate numbers into names.

### Anatomy of an AVC denial

```text
type=AVC msg=audit(1764064923.417:1184): avc:  denied  { read } for  pid=1188 comm="httpd" name="report.html" dev="dm-0" ino=17602931 scontext=system_u:system_r:httpd_t:s0 tcontext=unconfined_u:object_r:admin_home_t:s0 tclass=file permissive=0
```

| Field | Value here | Meaning |
|---|---|---|
| `type=AVC` | | An Access Vector Cache decision (kernel). `USER_AVC` is a decision by a userspace object manager such as systemd or dbus. `SELINUX_ERR` is a policy error (for example an invalid transition). |
| `msg=audit(1764064923.417:1184)` | epoch time `.` ms `:` serial | When it happened, and the event serial number that ties related records (`SYSCALL`, `PATH`, `PROCTITLE`) together. `ausearch -i` converts the time. |
| `avc: denied` | | The decision. `granted` appears only for rules marked `auditallow`. |
| `{ read }` | | The **permission(s)** requested: `read`, `write`, `open`, `getattr`, `execute`, `name_bind`, `name_connect`, `create`, `add_name`, `unlink` and so on. |
| `pid=1188 comm="httpd"` | | The process ID and command name that attempted it. |
| `name="report.html"` | | The name of the target object (last path component only). The full path is in the matching `PATH` record when syscall auditing captured it. |
| `dev="dm-0" ino=17602931` | | Device and inode of the target. `find / -xdev -inum 17602931` locates the file when `name` is ambiguous. |
| `scontext=` | `system_u:system_r:httpd_t:s0` | **Source context**: who tried. The type is the domain, `httpd_t`. |
| `tcontext=` | `unconfined_u:object_r:admin_home_t:s0` | **Target context**: what they tried to touch. The type, `admin_home_t`, is the clue: a file from `/root`. |
| `tclass=file` | | The **object class**: `file`, `dir`, `tcp_socket`, `sock_file`, `lnk_file`, `process`, `capability` and many more. |
| `permissive=0` | | 0 means the access was **actually blocked**. 1 means it was logged but allowed (system or domain permissive). |

Read it as a sentence: **"`httpd` running as `httpd_t` was denied `read` on a `file` named `report.html` labeled `admin_home_t`."** Ninety percent of the time, the target type alone tells you the fix: an `admin_home_t` or `user_home_t` file in a web root means "this was moved, run restorecon".

A port denial looks like this:

```text
type=AVC msg=audit(1764065511.902:1307): avc:  denied  { name_bind } for  pid=3341 comm="httpd" src=8085 scontext=system_u:system_r:httpd_t:s0 tcontext=system_u:object_r:unreserved_port_t:s0 tclass=tcp_socket permissive=0
```

`name_bind` on `src=8085`, target `unreserved_port_t`: port 8085 has no specific label, so httpd may not bind it. Fix: `semanage port -a -t http_port_t -p tcp 8085`.

And a boolean-shaped denial:

```text
type=AVC msg=audit(1764066004.118:1342): avc:  denied  { name_connect } for  pid=1190 comm="httpd" dest=8000 scontext=system_u:system_r:httpd_t:s0 tcontext=system_u:object_r:soundd_port_t:s0 tclass=tcp_socket permissive=0
```

`name_connect` to `dest=8000`. Do not be distracted by the target type (8000 happens to be labeled for a sound daemon); the question is "may httpd make outbound connections", which is a boolean.

### ausearch

```bash
ausearch -m AVC -ts recent             # AVCs from the last 10 minutes
ausearch -m AVC,USER_AVC,SELINUX_ERR -ts today -i
ausearch -m AVC -c httpd -ts boot      # only from comm=httpd since boot
ausearch -m AVC -ts 11/25/2026 09:00:00 -te 11/25/2026 10:00:00
```

`-ts` accepts `now`, `recent` (10 minutes ago), `boot`, `today`, `yesterday`, `this-week` or a date and time in your locale's format. `-i` interprets numeric fields (timestamps, UIDs, syscall numbers) into readable text. The output with `-i` begins with a separator line and a `type=PROCTITLE` record showing the full command line, which is often the most useful clue of all.

If `ausearch` prints `<no matches>` but you are sure SELinux is involved, suspect a **dontaudit** rule (section 9).

### aureport

```bash
aureport -a            # AVC report: one line per denial
aureport --avc -ts today
```

```text
AVC Report
===============================================================
# date time comm subj syscall class permission obj result event
===============================================================
1. 11/25/2026 10:02:03 httpd system_u:system_r:httpd_t:s0 257 file read unconfined_u:object_r:admin_home_t:s0 denied 1184
2. 11/25/2026 10:11:51 httpd system_u:system_r:httpd_t:s0 49 tcp_socket name_bind system_u:object_r:unreserved_port_t:s0 denied 1307
```

`aureport -a` is the fastest way to see whether a server has a *pattern* of denials (one service hitting the same wall every five minutes) rather than a one-off.

### setroubleshoot and sealert

`setroubleshoot-server` watches the audit stream, runs each denial through a set of plugins and writes a plain-English explanation with suggested fixes.

```bash
dnf install -y setroubleshoot-server
journalctl -t setroubleshoot --since "1 hour ago"
```

```text
Nov 25 10:02:05 web01 setroubleshoot[3410]: SELinux is preventing /usr/sbin/httpd from read access on the file report.html. For complete SELinux messages run: sealert -l 8c1b2f4e-...
```

```bash
sealert -l 8c1b2f4e-...                    # details for one alert
sealert -a /var/log/audit/audit.log        # analyse the whole log (can be slow)
```

Abridged `sealert` output:

```text
SELinux is preventing /usr/sbin/httpd from read access on the file report.html.

*****  Plugin restorecon (92.2 confidence) suggests   ************************

If you want to fix the label.
/var/www/html/report.html default label should be httpd_sys_content_t.
Then you can run restorecon. The access attempt may have been stopped due to insufficient permissions to access a parent directory in which case try to change the following command accordingly.
Do
# /sbin/restorecon -v /var/www/html/report.html

*****  Plugin catchall (7.83 confidence) suggests   **************************

If you believe that httpd should be allowed read access on the report.html file by default.
Then you should report this as a bug.
You can generate a local policy module to allow this access.
Do
allow this access for now by executing:
# ausearch -c 'httpd' --raw | audit2allow -M my-httpd
# semodule -X 300 -i my-httpd.pp
```

How to read it: plugins are ranked by **confidence**. Take the highest-confidence suggestion that is a label, a boolean or a port. The "catchall" plugin at the bottom always suggests a custom module; that is the last resort, not the default.

:::tip
`setroubleshoot-server` uses CPU and memory analysing every denial. Many teams install it on dev and staging but not on busy production boxes; there you can copy the relevant lines out of `audit.log` and run `sealert -a file` on a workstation, or use `audit2why`, which is lightweight.
:::

### audit2why

`audit2why` (package `policycoreutils-python-utils`) reads AVC records on stdin and tells you **why** each was denied, checking the current policy, including booleans.

```bash
ausearch -m AVC -ts recent | audit2why
```

For the port-8000 proxy denial:

```text
type=AVC msg=audit(1764066004.118:1342): avc:  denied  { name_connect } for  pid=1190 comm="httpd" dest=8000 ...

	Was caused by:
	One of the following booleans was set incorrectly.
	Description:
	Allow httpd to can network connect

	Allow access by executing:
	# setsebool -P httpd_can_network_connect 1
	Description:
	Allow nis to enabled

	Allow access by executing:
	# setsebool -P nis_enabled 1
```

Pick the boolean whose description matches what you actually want (httpd making network connections), not `nis_enabled`, which would open every daemon to arbitrary ports.

For the `admin_home_t` file:

```text
	Was caused by:
		Missing type enforcement (TE) allow rule.

		You can use audit2allow to generate a loadable module to allow this access.
```

"Missing TE allow rule" does **not** mean "write a module". It means no boolean fixes it, so ask: is the **label** wrong (restorecon / fcontext), is the **port** unlabeled (semanage port), or is the application doing something unusual?

### audit2allow, and why blind audit2allow is dangerous

`audit2allow` converts denials into policy rules.

```bash
ausearch -m AVC -ts recent | audit2allow            # print the rules
ausearch -m AVC -c httpd --raw | audit2allow -M my-httpd
```

```text
******************** IMPORTANT ***********************
To make this policy package active, execute:

semodule -i my-httpd.pp
```

`-M my-httpd` writes `my-httpd.te` (readable source) and `my-httpd.pp` (compiled module). Read the `.te` first:

```text
module my-httpd 1.0;

require {
	type httpd_t;
	type admin_home_t;
	class file read;
}

#============= httpd_t ==============
allow httpd_t admin_home_t:file read;
```

That rule lets every process in `httpd_t` read **every file labeled `admin_home_t`**, which is everything in `/root`. You fixed one mislabeled HTML file by giving a web shell read access to root's home directory, SSH config and shell history. That is the danger of piping AVCs straight into `semodule -i`:

- It encodes the **symptom**, not the cause. The correct fix was `restorecon`.
- It is **too broad**: rules are per type, not per file.
- It hides the signal: future attacks against that access will no longer log anything.
- It can grant capabilities or execute permissions you never meant to (`allow httpd_t self:capability dac_override` appears often and is rarely right).
- It accumulates: inherited servers sometimes carry dozens of `my*.pp` modules nobody can explain.

When **is** a custom module correct? When the denial is for a legitimate, application-specific need that no label, boolean or port mapping can express, you have read and understood every line of the `.te`, the rules are as narrow as possible, and the module is named, versioned and stored in source control. Even then, check `audit2allow -w` and booleans first.

:::warn
The safe order of fixes, every time: **1. label** (`restorecon`, `semanage fcontext`) **2. port** (`semanage port`) **3. boolean** (`setsebool -P`) **4. custom module**, reviewed, as a last resort. Never `setenforce 0` and walk away.
:::

## 9. dontaudit rules and policy modules

### dontaudit: the denials you cannot see

The policy contains thousands of **dontaudit** rules that say "deny this, but do not log it". They exist because many programs probe for things they do not need (for example, reading every file descriptor they inherited, or `getattr` on directories while searching a path), and logging those harmless denials would bury the real ones.

Occasionally the denial that breaks your service is one of the silent ones. Symptom: something fails with `Permission denied`, `setenforce 0` makes it work, but `ausearch -m AVC` shows nothing. Temporarily disable dontaudit rules:

```bash
semodule -DB        # -D disable dontaudit, -B rebuild and reload policy
# reproduce the problem
ausearch -m AVC -ts recent
semodule -B         # rebuild normally: dontaudit rules back on
```

Always run `semodule -B` afterwards. With dontaudit off, the audit log fills with noise very quickly.

### Policy modules

The policy is built from **modules**: the base policy plus one module per service, plus anything you add.

```bash
semodule -l | head                  # list installed module names
semodule -lfull | grep -v ' 100 '   # with priorities; non-100 = not from the base policy package
semodule -i my-httpd.pp             # install (default priority 400)
semodule -X 300 -i my-httpd.pp      # install at a specific priority
semodule -r my-httpd                # remove
semodule -d my-httpd                # disable without removing; -e to enable
```

```text
400 my-httpd          pp
200 container         cil
100 abrt              pp
...
```

Priority 100 is the distribution policy; higher priorities override lower ones. Anything at 300 or 400 was added locally. On an inherited server, `semodule -lfull | grep -Ev '^100 '` combined with `semanage export` gives you the complete list of SELinux customisations to review.

## 10. Ubuntu and Debian: AppArmor compared

Ubuntu (and SUSE) ship **AppArmor** instead of SELinux by default. Debian ships AppArmor enabled since Debian 10. The goal is the same, confining services, but the model is different.

| | SELinux (RHEL family) | AppArmor (Ubuntu, Debian) |
|---|---|---|
| Model | **Label-based**: rules refer to types stored in xattrs | **Path-based**: rules refer to file paths |
| Profiles | Compiled policy modules, every process has a domain | Per-program profiles in `/etc/apparmor.d/`; programs without a profile are unconfined |
| Moving a file | Label travels with the inode (`mv` problem) | Access follows the new path, no relabel needed |
| Hard links, bind mounts | Same label regardless of path | Different paths can get different access |
| Status | `getenforce`, `sestatus` | `aa-status` |
| Per-service permissive | `semanage permissive -a httpd_t` | `aa-complain /usr/sbin/nginx` (`aa-enforce` to revert) |
| Denials logged as | `type=AVC ... denied` in `audit.log` | `apparmor="DENIED" operation="open" profile="..." name="/path"` in `kern.log` / `journalctl -k` / `audit.log` |
| Tuning tools | `semanage`, `setsebool`, `audit2allow` | Edit the profile or its `local/` include, `aa-logprof`, `apparmor_parser -r` |

The same rule applies on Ubuntu: put a profile in complain mode briefly to prove AppArmor is the cause, then fix the profile (often a line in `/etc/apparmor.d/local/usr.sbin.<name>`) and return it to enforce. Do not `systemctl disable apparmor`.

## 11. Troubleshooting runbook

| Symptom | Check (command) | Likely cause | Fix |
|---|---|---|---|
| Web server returns 403 for one file or directory; `ls -l` permissions look fine | `ls -Z file`; `restorecon -nv file`; `ausearch -m AVC -c httpd -ts recent` | File moved with `mv` from a home directory (`user_home_t`, `admin_home_t`) | `restorecon -Rv /var/www/html` |
| 403 for an entire custom document root (`/srv/web`, `/data/www`) | `ls -dZ /srv/web`; `semanage fcontext -l -C` | No default rule for the custom path; files are `var_t` or `default_t` | `semanage fcontext -a -t httpd_sys_content_t "/srv/web(/.*)?"` then `restorecon -Rv /srv/web` |
| App can read but not write uploads/cache | `ls -Z` on the directory; AVC with `{ write }` or `{ add_name }` | Directory is read-only type `httpd_sys_content_t` | `semanage fcontext -a -t httpd_sys_rw_content_t "/path(/.*)?"`, `restorecon -Rv /path` |
| Service will not start after port change; `Permission denied` on bind | `journalctl -u SERVICE`; `semanage port -l \| grep -w PORT`; AVC `name_bind` | Port not labeled for the service | `semanage port -a -t TYPE_port_t -p tcp PORT` (or `-m` if already defined) |
| Reverse proxy returns 502/503; `(13)Permission denied` connecting to backend | `getsebool httpd_can_network_connect`; AVC `name_connect` | Boolean off | `setsebool -P httpd_can_network_connect on` (or `_db` for databases) |
| Fix worked yesterday, broken again today | `ls -Z`; `semanage fcontext -l -C`; shell history for `chcon` | `chcon` fix reverted by a `restorecon` or relabel | Replace with `semanage fcontext` + `restorecon` |
| Nobody can log in after password reset from rescue shell | Console login fails; `ls -Z /etc/shadow` from rescue | `/etc/shadow` unlabeled after editing with SELinux off | `touch /.autorelabel` from rescue, reboot |
| `setenforce 0` fixes it, but `ausearch` shows no AVC | `semodule -DB`, reproduce, `ausearch -m AVC -ts recent` | Denial hidden by a dontaudit rule | Fix the revealed denial properly, then `semodule -B` |
| Content on NFS/CIFS mount not served | `mount \| grep nfs`; `ls -Z` shows `nfs_t`/`cifs_t` | Network filesystem labels not readable by httpd | `setsebool -P httpd_use_nfs on` (or `httpd_use_cifs`), or mount with `context="system_u:object_r:httpd_sys_content_t:s0"` |
| Container cannot read bind-mounted host directory | `podman logs`; AVC with `scontext=...container_t` | Host directory not labeled `container_file_t` | Mount with `:Z` (private) or `:z` (shared) volume option, e.g. `-v /srv/data:/data:Z` |
| systemd-in-container fails with cgroup errors | AVC on cgroup files from `container_t` | Boolean off | `setsebool -P container_manage_cgroup on` |
| Lots of denials after re-enabling SELinux | `sestatus`; `aureport -a` | Files created while disabled are unlabeled | Permissive + `touch /.autorelabel` + reboot, then enforcing |
| Custom daemon runs as `unconfined_service_t` | `ps -eZ \| grep NAME` | No policy for that binary | Acceptable but unprotected; consider writing a policy module (`sepolicy generate`) |

## 12. Break-fix labs

All labs assume a **disposable** Rocky Linux 9 or AlmaLinux 9 VM (RHEL 9 with a free developer subscription also works), SELinux enforcing, and a root shell. Prepare once:

```bash
dnf install -y httpd policycoreutils-python-utils setroubleshoot-server setools-console
systemctl enable --now httpd
getenforce        # must print Enforcing
echo '<h1>ok</h1>' > /var/www/html/index.html
curl -s http://localhost/
```

:::lab Lab 06-A — Read contexts, break a label, fix it properly
**Goal:** read contexts on processes and files, cause the classic `mv` label problem, diagnose it from the AVC, and fix it with `restorecon`; then set up a custom document root the permanent way.

**Break it:**

```bash
echo '<h1>quarterly report</h1>' > /root/report.html
mv /root/report.html /var/www/html/
```

**Symptoms:**

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost/report.html
```

```text
403
```

`tail -n2 /var/log/httpd/error_log` shows `(13)Permission denied: ... access to /report.html denied`.

**Diagnose:**

1. Rule out DAC: `ls -l /var/www/html/report.html` shows `-rw-r--r--` owned by root. World-readable, so DAC is fine.
2. Look at the process: `ps -eZ | grep httpd` shows `httpd_t`.
3. Look at the label: `ls -Z /var/www/html/` shows `index.html` as `httpd_sys_content_t` and `report.html` as `admin_home_t`.
4. Confirm with the log: `ausearch -m AVC -ts recent -c httpd` shows `denied { read }` (or `{ getattr }`) with `tcontext=...admin_home_t`.
5. Ask the policy what it should be: `restorecon -nv /var/www/html/report.html` prints `Would relabel ... to ...httpd_sys_content_t`.

**Fix:**

```bash
restorecon -Rv /var/www/html
```

**Verify:** `curl -s http://localhost/report.html` returns the page; `ls -Z` shows `httpd_sys_content_t`.

**Part 2, the permanent way for a custom path:**

```bash
mkdir -p /srv/web && echo '<h1>srv</h1>' > /srv/web/index.html
sed -i 's#DocumentRoot "/var/www/html"#DocumentRoot "/srv/web"#' /etc/httpd/conf/httpd.conf
cat > /etc/httpd/conf.d/srvweb.conf <<'EOF'
<Directory "/srv/web">
    Require all granted
</Directory>
EOF
systemctl restart httpd
curl -s -o /dev/null -w '%{http_code}\n' http://localhost/     # 403
ls -dZ /srv/web                                                # var_t
```

Try the temporary fix and watch it vanish:

```bash
chcon -R -t httpd_sys_content_t /srv/web
curl -s http://localhost/              # works
restorecon -Rv /srv/web                # reverts to var_t
curl -s -o /dev/null -w '%{http_code}\n' http://localhost/     # 403 again
```

Now the permanent fix:

```bash
semanage fcontext -a -t httpd_sys_content_t "/srv/web(/.*)?"
restorecon -Rv /srv/web
curl -s http://localhost/
semanage fcontext -l -C
```

**Clean-up/revert:**

```bash
sed -i 's#DocumentRoot "/srv/web"#DocumentRoot "/var/www/html"#' /etc/httpd/conf/httpd.conf
rm -f /etc/httpd/conf.d/srvweb.conf /var/www/html/report.html
semanage fcontext -d "/srv/web(/.*)?"
rm -rf /srv/web
systemctl restart httpd
```
:::

:::lab Lab 06-B — A reverse proxy blocked by a boolean
**Goal:** reproduce the "503 from the proxy" incident, prove it is a boolean with `audit2why`, and fix it persistently.

**Break it:** start a backend on port 8000 and proxy to it.

```bash
mkdir -p /tmp/backend && echo 'backend ok' > /tmp/backend/index.html
cd /tmp/backend && nohup python3 -m http.server 8000 --bind 127.0.0.1 >/dev/null 2>&1 &
cat > /etc/httpd/conf.d/proxy.conf <<'EOF'
ProxyPass        /app/ http://127.0.0.1:8000/
ProxyPassReverse /app/ http://127.0.0.1:8000/
EOF
systemctl restart httpd
getsebool httpd_can_network_connect     # should be off
```

**Symptoms:**

```bash
curl -s http://127.0.0.1:8000/                                         # backend works directly
curl -s -o /dev/null -w '%{http_code}\n' http://localhost/app/          # 503
grep AH00957 /var/log/httpd/error_log | tail -1
```

```text
[proxy:error] [pid 1190] (13)Permission denied: AH00957: http: attempt to connect to 127.0.0.1:8000 (127.0.0.1:8000) failed
```

**Diagnose:**

1. The backend answers directly from your (unconfined) shell, so the network is fine.
2. `ausearch -m AVC -ts recent -c httpd` shows `denied { name_connect }` with `dest=8000`.
3. `ausearch -m AVC -ts recent -c httpd | audit2why` names `httpd_can_network_connect`.
4. `semanage boolean -l | grep httpd_can_network_connect` shows `(off , off)`.

**Fix:**

```bash
setsebool -P httpd_can_network_connect on
```

**Verify:** `curl -s http://localhost/app/` prints `backend ok`; `semanage boolean -l | grep 'httpd_can_network_connect '` shows `(on , on)`, so it survives reboot.

Discuss: would `httpd_can_network_connect_db` have worked? No: port 8000 is not a database port type. If this backend were PostgreSQL on 5432, the narrower `_db` boolean would be the better choice.

**Clean-up/revert:**

```bash
setsebool -P httpd_can_network_connect off
rm -f /etc/httpd/conf.d/proxy.conf
pkill -f 'http.server 8000'; rm -rf /tmp/backend
systemctl restart httpd
```
:::

:::lab Lab 06-C — Move Apache to a non-standard port
**Goal:** see a bind denial, label the port, open the firewall, and learn the `-a` versus `-m` distinction.

**Break it:**

```bash
semanage port -l | grep -w 8085          # expect no output: unassigned
echo 'Listen 8085' > /etc/httpd/conf.d/port8085.conf
systemctl restart httpd
```

**Symptoms:**

```text
Job for httpd.service failed because the control process exited with error code.
```

```bash
journalctl -u httpd -n 5 --no-pager
```

```text
httpd[3341]: (13)Permission denied: AH00072: make_sock: could not bind to address [::]:8085
httpd[3341]: (13)Permission denied: AH00072: make_sock: could not bind to address 0.0.0.0:8085
httpd[3341]: no listening sockets available, shutting down
```

**Diagnose:**

1. `ss -tlnp | grep 8085` shows nothing else holds the port, so it is not "address already in use".
2. `ausearch -m AVC -ts recent` shows `denied { name_bind } ... src=8085 ... tcontext=system_u:object_r:unreserved_port_t:s0 tclass=tcp_socket`.
3. `semanage port -l | grep -w http_port_t` confirms 8085 is not in the list.

**Fix:**

```bash
semanage port -a -t http_port_t -p tcp 8085
systemctl restart httpd
firewall-cmd --permanent --add-port=8085/tcp && firewall-cmd --reload
```

**Verify:** `ss -tlnpZ | grep 8085` shows httpd in `httpd_t`; `curl -s http://localhost:8085/` returns the page; `semanage port -l -C` lists your mapping.

**Extra:** try `semanage port -a -t http_port_t -p tcp 9090` and read the `already defined` error. Do not run `-m` on 9090 unless you are happy to break Cockpit on this lab VM.

**Clean-up/revert:**

```bash
rm -f /etc/httpd/conf.d/port8085.conf
semanage port -d -t http_port_t -p tcp 8085
firewall-cmd --permanent --remove-port=8085/tcp && firewall-cmd --reload
systemctl restart httpd
```
:::

:::lab Lab 06-D — Decode a real AVC with ausearch, audit2why, sealert and audit2allow
**Goal:** produce a denial, decode every field by hand, compare what each tool suggests, and see exactly why `audit2allow` would have been the wrong fix.

**Break it:**

```bash
echo 'secret-ish' > /var/www/html/notes.html
chcon -t user_home_t /var/www/html/notes.html
curl -s -o /dev/null -w '%{http_code}\n' http://localhost/notes.html     # 403
```

**Symptoms:** 403 for one file; others serve fine.

**Diagnose, step by step:**

1. Raw record: `ausearch -m AVC -ts recent`. Write down, from the record: `comm`, the permission in braces, `name`, the `scontext` type, the `tcontext` type, `tclass`, `permissive`. You should have: httpd, `read` (or `getattr`), `notes.html`, `httpd_t`, `user_home_t`, `file`, `0`.
2. Readable record: `ausearch -m AVC -ts recent -i`. Note the converted timestamp and the `PROCTITLE`/`PATH` records that come with it.
3. Report view: `aureport -a | tail -3`.
4. Why: `ausearch -m AVC -ts recent | audit2why`. It says "Missing type enforcement (TE) allow rule". No boolean helps.
5. setroubleshoot: `journalctl -t setroubleshoot -n 3 --no-pager`, then run the `sealert -l <id>` it prints. The top plugin should be `restorecon` with high confidence.
6. What audit2allow *would* do (do not install it):

```bash
ausearch -m AVC -ts recent -c httpd --raw | audit2allow -m lab06d
```

```text
module lab06d 1.0;

require {
	type httpd_t;
	type user_home_t;
	class file { getattr open read };
}

#============= httpd_t ==============
allow httpd_t user_home_t:file { getattr open read };
```

Read that rule out loud: Apache could now read every file in every user's home directory. That is the opposite of a fix.

**Fix:**

```bash
restorecon -v /var/www/html/notes.html
```

**Verify:** `curl -s http://localhost/notes.html` returns the content; `ls -Z` shows `httpd_sys_content_t`; running `ausearch -m AVC -ts recent` again a minute later shows no new denials.

**Optional, dontaudit:** run `semodule -DB`, `curl` a few pages, and look at how many extra AVCs appear in `ausearch -m AVC -ts recent`. Then `semodule -B` to restore normal logging.

**Clean-up/revert:**

```bash
rm -f /var/www/html/notes.html
semodule -B
semodule -l | grep lab06d || echo "no lab module installed, good"
```
:::

## Command cheat sheet

| Task | Command |
|---|---|
| Current mode | `getenforce` |
| Full status (runtime vs config) | `sestatus` |
| Temporary permissive / enforcing (diagnostic only) | `setenforce 0` / `setenforce 1` |
| Persistent mode | `/etc/selinux/config` (`SELINUX=enforcing`) |
| One domain permissive | `semanage permissive -a httpd_t`, `-l`, `-d httpd_t` |
| File labels | `ls -Z`, `ls -dZ dir` |
| Process domains | `ps -eZ`, `ps auxZ` |
| Your context | `id -Z` |
| Socket owners with contexts | `ss -tlnpZ` |
| Default label for a path | `matchpathcon /path`, `restorecon -nv /path` |
| List file-context rules / local only | `semanage fcontext -l`, `semanage fcontext -l -C` |
| Add a permanent file-context rule | `semanage fcontext -a -t TYPE "/path(/.*)?"` |
| Copy labeling of another tree | `semanage fcontext -a -e /var/www /srv/web` |
| Apply rules to files | `restorecon -Rv /path` |
| Temporary relabel (lost on restorecon) | `chcon -t TYPE file` |
| Full relabel at next boot | `touch /.autorelabel` or `fixfiles -F onboot` |
| List booleans | `getsebool -a`, `semanage boolean -l`, `semanage boolean -l -C` |
| Set a boolean persistently | `setsebool -P NAME on` |
| List port labels / local only | `semanage port -l`, `semanage port -l -C` |
| Label a port | `semanage port -a -t TYPE -p tcp PORT` (`-m` modify, `-d` delete) |
| Recent denials | `ausearch -m AVC -ts recent` (`-i` readable, `-c COMM`) |
| Denial report | `aureport -a` |
| Explain denials | `ausearch -m AVC -ts recent \| audit2why` |
| Human-readable analysis | `sealert -l ID`, `sealert -a /var/log/audit/audit.log` |
| Generate rules (review only) | `audit2allow -m NAME`, `audit2allow -M NAME` |
| Query policy rules | `sesearch --allow -s SRC_t -t TGT_t -c CLASS` |
| Show hidden denials / restore | `semodule -DB` / `semodule -B` |
| Modules | `semodule -l`, `semodule -lfull`, `-i file.pp`, `-r name` |
| Export all local customisations | `semanage export` |
| AppArmor equivalents (Ubuntu) | `aa-status`, `aa-complain`, `aa-enforce` |

:::summary
- SELinux is **mandatory access control** layered on top of DAC: DAC is checked first, then the policy, which denies by default and binds even root in confined domains.
- Run **enforcing** in production. Use `setenforce 0` only as a one-retry diagnostic, prefer `semanage permissive -a DOMAIN_t`, and never disable: re-enabling requires a full relabel. On RHEL 9, fully disabling needs `selinux=0` on the kernel command line.
- A context is `user:role:type:level`; the **type** is what matters. Processes run in **domains**, files and ports have **types**, and **type enforcement** rules connect them.
- **Labels**: check with `ls -Z`, compare with `restorecon -nv`, fix with `restorecon -Rv`. For new paths, `semanage fcontext -a` **then** `restorecon`. `chcon` is temporary. `mv` keeps the old label, `cp` takes the destination's.
- **Booleans** (`setsebool -P`) switch prepared policy on; **port labels** (`semanage port -a/-m`) let services bind or connect to new ports.
- Read the **AVC**: who (`scontext`), did what (`{ perm }`, `tclass`), to what (`tcontext`, `name`), was it enforced (`permissive=0`). Then `audit2why` and `sealert` for suggestions.
- Fix order: **label, port, boolean, reviewed custom module**. Blind `audit2allow` encodes the symptom and over-grants.
- Silent failure that `setenforce 0` fixes: `semodule -DB`, reproduce, then `semodule -B`.
:::

## Self-check questions

<div class="q" markdown="1">

**Q1.** A developer copies a new page into place with `mv /home/dev/promo.html /var/www/html/`. The file is `-rw-r--r--` and owned by root, but Apache returns 403 for it while other pages work. What do you check first?

A. `firewall-cmd --list-all`
B. `ls -Z /var/www/html/promo.html`
C. `getsebool httpd_can_network_connect`
D. `systemctl status httpd`

:::answer
**Answer: B.** `mv` keeps the file's original label (`user_home_t`), which `httpd_t` cannot read. `ls -Z` shows it at a glance, and `restorecon -v` fixes it.

- A is wrong: the firewall would block every page, not one.
- C is wrong: that boolean governs outbound connections, not reading local files.
- D is wrong: httpd is clearly running, since other pages work.
:::
</div>

<div class="q" markdown="1">

**Q2.** You set a new document root, `/data/site`. You run `chcon -R -t httpd_sys_content_t /data/site` and the site works. Two weeks later, after a config-management run, it returns 403 again. What is the correct permanent fix?

A. Run `chcon` again from cron every hour
B. `semanage fcontext -a -t httpd_sys_content_t "/data/site(/.*)?"` followed by `restorecon -Rv /data/site`
C. `setsebool -P httpd_read_user_content on`
D. Set `SELINUX=permissive` in `/etc/selinux/config`

:::answer
**Answer: B.** `chcon` labels are overwritten by any `restorecon` or relabel because they are not in the default rules. Adding a file-context rule and then applying it makes the label survive.

- A is wrong: it papers over the cause and leaves windows of failure.
- C is wrong: that boolean is for user home content, and the files are not `user_home_t`.
- D is wrong: it removes protection for every service on the box.
:::
</div>

<div class="q" markdown="1">

**Q3.** After you change `Listen 80` to `Listen 8085`, `httpd` fails to start with `(13)Permission denied: make_sock: could not bind to address 0.0.0.0:8085`. Nothing else is listening on 8085. What is the fix?

A. `firewall-cmd --permanent --add-port=8085/tcp`
B. `setsebool -P httpd_can_network_connect on`
C. `semanage port -a -t http_port_t -p tcp 8085`
D. Run httpd as root instead of apache

:::answer
**Answer: C.** The AVC is `name_bind` on an `unreserved_port_t` port. Labeling 8085 as `http_port_t` lets `httpd_t` bind it. You will also need the firewall rule for clients, but that is not why the service cannot start.

- A is wrong: firewalld does not stop a local process from binding.
- B is wrong: that boolean covers outbound `name_connect`, not binding.
- D is wrong: httpd already binds as root; SELinux constrains the `httpd_t` domain regardless of UID.
:::
</div>

<div class="q" markdown="1">

**Q4.** A PHP application on RHEL 9 cannot reach its PostgreSQL server at 10.0.0.25:5432. The error log shows `Permission denied` on connect. `audit2why` suggests both `httpd_can_network_connect` and `httpd_can_network_connect_db`. Which do you choose?

A. `httpd_can_network_connect_db`, because it is the narrowest boolean that covers database ports
B. `httpd_can_network_connect`, because it covers more cases
C. Both, to be safe
D. Neither; write a module with `audit2allow -M`

:::answer
**Answer: A.** Least privilege: the `_db` boolean permits connections to database port types only, which is exactly the requirement.

- B is wrong: it allows connections to any port, far broader than needed.
- C is wrong: enabling the broad one makes the narrow one pointless.
- D is wrong: a boolean already expresses the intended policy; custom modules are a last resort.
:::
</div>

<div class="q" markdown="1">

**Q5.** In this record, what was actually denied?
`avc: denied { write } for pid=2201 comm="php-fpm" name="cache" dev="dm-0" ino=5012 scontext=system_u:system_r:httpd_t:s0 tcontext=system_u:object_r:httpd_sys_content_t:s0 tclass=dir permissive=0`

A. php-fpm reading a file called cache
B. php-fpm (in `httpd_t`) writing into the directory `cache`, which is labeled read-only web content; the denial was enforced
C. httpd binding a port called cache
D. Nothing; the access was only logged

:::answer
**Answer: B.** `{ write }`, `tclass=dir`, `name="cache"`, target type `httpd_sys_content_t`, and `permissive=0` means it was blocked. The fix is `semanage fcontext -a -t httpd_sys_rw_content_t "/path/to/cache(/.*)?"` plus `restorecon -Rv`.

- A is wrong: the permission is write and the class is dir.
- C is wrong: port denials have `tclass=tcp_socket` and `name_bind`.
- D is wrong: that would be `permissive=1`.
:::
</div>

<div class="q" markdown="1">

**Q6.** A colleague proposes fixing an AVC with `ausearch -m AVC -ts today --raw | audit2allow -M fix && semodule -i fix.pp`. The generated rule is `allow httpd_t user_home_t:file { read open getattr };`. What is your response?

A. Approve it; audit2allow generates minimal rules
B. Reject it: the rule lets httpd read every file in every user's home directory; the real cause is a mislabeled file that `restorecon` fixes
C. Approve it, but install with priority 100
D. Reject it and set the system to permissive instead

:::answer
**Answer: B.** Rules apply to whole types, not single files, so this over-grants massively and hides future attack attempts. The target type shows a label problem.

- A is wrong: audit2allow writes exactly what was denied, which is a symptom, not a minimal correct policy.
- C is wrong: priority does not change what the rule allows.
- D is wrong: permissive removes all enforcement.
:::
</div>

<div class="q" markdown="1">

**Q7.** `sestatus` shows `Current mode: permissive` and `Mode from config file: enforcing`. What does that tell you?

A. The config file is corrupt
B. Someone ran `setenforce 0`; the box will return to enforcing at the next reboot, possibly breaking things that only "work" now
C. SELinux is disabled
D. The policy failed to load

:::answer
**Answer: B.** Runtime mode differs from the persistent setting, which is the signature of `setenforce`. Find out why, check `ausearch -m AVC` for what would be denied, fix those, and `setenforce 1` in a controlled way.

- A is wrong: a corrupt config would not produce a clean status line.
- C is wrong: disabled shows `SELinux status: disabled`.
- D is wrong: a loaded policy name is shown and a mode is active.
:::
</div>

<div class="q" markdown="1">

**Q8.** A server has had SELinux disabled for two years. You must bring it to enforcing. What is the safe sequence?

A. `setenforce 1`
B. Set `SELINUX=enforcing` and reboot
C. Set `SELINUX=permissive` (and remove any `selinux=0` kernel argument), `touch /.autorelabel`, reboot, review AVCs, then switch to enforcing
D. `restorecon -Rv /var/www` and reboot

:::answer
**Answer: C.** Files created while disabled have no labels. A full relabel in permissive mode, then fixing the AVCs you observe, avoids an outage when you enforce.

- A is wrong: `setenforce` does not work when SELinux is disabled.
- B is wrong: enforcing on an unlabeled filesystem can stop services and even logins.
- D is wrong: it relabels one tree, not the whole system.
:::
</div>

<div class="q" markdown="1">

**Q9.** A service fails with `Permission denied`. `setenforce 0` makes it work, but `ausearch -m AVC -ts recent` returns `<no matches>`. What next?

A. Leave the system permissive; there is nothing to fix
B. `semodule -DB`, `setenforce 1`, reproduce, read the newly visible AVC, fix it, then `semodule -B`
C. Reinstall the selinux-policy package
D. Restart auditd and give up

:::answer
**Answer: B.** The denial is suppressed by a dontaudit rule. Disabling dontaudit temporarily reveals it. Restore normal behaviour with `semodule -B` afterwards.

- A is wrong: leaving the box permissive is never the fix.
- C is wrong: the policy is behaving as designed.
- D is wrong: restarting auditd does not make dontaudit rules log.
:::
</div>

<div class="q" markdown="1">

**Q10.** You suspect SELinux is blocking a new web deployment, but the host also runs a database and an SSH bastion that must stay protected. What is the most precise way to test the hypothesis?

A. `setenforce 0`
B. `semanage permissive -a httpd_t`, test, then `semanage permissive -d httpd_t`
C. `SELINUX=disabled` and reboot
D. `semodule -r apache`

:::answer
**Answer: B.** Only the web server domain becomes permissive; everything else remains enforced, and its AVCs are still logged with `permissive=1`.

- A is wrong: it makes every domain permissive.
- C is wrong: disabling needs a reboot, requires a relabel later and removes all protection.
- D is wrong: removing the module would cause httpd to run unconfined or fail, and is not a diagnostic.
:::
</div>

<div class="q" markdown="1">

**Q11.** A Podman container started with `-v /srv/appdata:/data` gets `Permission denied` reading `/data`, but DAC permissions are fine. The AVC shows `scontext=...container_t...` and `tcontext=...var_t...`. What is the usual fix?

A. Run the container with `--privileged`
B. Mount with `-v /srv/appdata:/data:Z` (or `:z` if shared between containers) so Podman relabels the directory `container_file_t`
C. `setsebool -P container_manage_cgroup on`
D. `chmod -R 777 /srv/appdata`

:::answer
**Answer: B.** Containers run as `container_t` and can only access `container_file_t`. The `:Z`/`:z` options relabel the host directory for that purpose.

- A is wrong: it disables most container isolation, including SELinux separation.
- C is wrong: that boolean is for systemd running inside containers managing cgroups.
- D is wrong: DAC was already fine; SELinux is the layer denying access.
:::
</div>

<div class="q" markdown="1">

**Q12.** On an Ubuntu 22.04 server, nginx cannot read a file after an upgrade. `journalctl -k` shows `apparmor="DENIED" operation="open" profile="/usr/sbin/nginx" name="/srv/www/index.html"`. What is the equivalent of the SELinux approach?

A. `systemctl disable --now apparmor`
B. `aa-complain /usr/sbin/nginx` briefly to confirm, then add the path to the profile (e.g. a `local/` include), reload with `apparmor_parser -r`, and `aa-enforce` again
C. `restorecon -Rv /srv/www`
D. `setenforce 0`

:::answer
**Answer: B.** AppArmor is path-based: you confirm with complain mode, then grant the path in the profile and return to enforce.

- A is wrong: like disabling SELinux, it removes all confinement.
- C is wrong: Ubuntu does not use SELinux labels by default.
- D is wrong: `setenforce` is an SELinux tool and does nothing for AppArmor.
:::
</div>
