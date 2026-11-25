<div class="cover" markdown="1">
<div class="mod">Module 02 · systemd, Services & Logs</div>

# systemd, Services and Logs: Running, Debugging and Scheduling Production Workloads

<p>How systemd starts and supervises everything on a modern RHEL server, how to write a correct service for your own application in /opt, how to read systemctl status and the journal like an on-call engineer, how logs are stored and rotated, and how timers and cron jobs fail in ways that only show up at 3 a.m.</p>
</div>

## What you'll learn

- The **unit types** systemd manages, where unit files live (**/usr/lib/systemd/system** vs **/etc/systemd/system**), and how **drop-ins**, `systemctl edit` and `daemon-reload` fit together
- **Unit file anatomy**: `Type=` (simple, exec, forking, notify, oneshot), `ExecStart`, `User`, `WorkingDirectory`, `Environment` / `EnvironmentFile`, `Restart=` and `RestartSec`, and the dependency directives `After`, `Wants` and `Requires`
- The difference between **enable** and **start**, what **masking** does, and how **targets** replace runlevels
- How to **read `systemctl status`** line by line, including the systemd exit codes such as **203/EXEC**, **200/CHDIR** and **217/USER**
- **journalctl** for real incidents: `-u`, `-b`, `-p err`, `--since`, `-f`, `-o`, persistent journal, disk usage and vacuuming
- Classic log files under **/var/log**, **rsyslog**, and **logrotate** (including the SELinux trap with logs in /opt)
- **systemd timers vs cron**: crontab syntax, the cron environment, `PATH`, and why `%` breaks your job
- Writing a production-grade service for a custom app in **/opt/myapp**, and the **SELinux file context** problems (`user_home_t`, `bin_t`, `restorecon`) that cause **203/EXEC**
- Four break-fix labs: 203/EXEC, a restart loop that hits `start-limit-hit`, a cron job that works by hand but not in cron, and a journal filling the disk

**Why it matters in production:** almost every outage you will touch on a Linux server ends with a service that will not start, will not stay up, or is logging the reason somewhere you have not looked yet. If you can read a unit file, `systemctl status` and `journalctl` fluently, you can usually find the root cause of a broken service in under five minutes. If you cannot, you will restart things and hope.

## 1. What systemd actually does

systemd is PID 1 on every RHEL 7+ system (and on Ubuntu 15.04+, Debian 8+). It is the first userspace process the kernel starts and the parent of every service. It does far more than the old SysV init scripts did:

- **Starts services in parallel** based on declared dependencies, not numbered scripts.
- **Supervises** them: it knows the main PID, captures stdout/stderr, and can restart them when they die.
- **Tracks every process in a cgroup**, so `systemctl stop` kills the whole process tree, including children that double-forked.
- **Owns logging** through `systemd-journald`.
- **Schedules jobs** through timers, mounts filesystems, manages sockets, and more.

:::story
Think of systemd as a theatre stage manager. Each actor (service) has a card (unit file) saying when they go on, who must be on stage before them, what to do if they fall over, and whose name they perform under. The stage manager keeps a notebook (the journal) of everything said on stage. When something goes wrong, you do not ask the actor; you read the card and the notebook.
:::

### Unit types

Everything systemd manages is a **unit**, and the suffix of the unit name tells you its type.

| Unit type | Suffix | What it represents | Example |
|---|---|---|---|
| Service | `.service` | A process or group of processes (a daemon or a one-off task) | `sshd.service`, `myapp.service` |
| Socket | `.socket` | A listening socket that starts a service on first connection | `cockpit.socket` |
| Target | `.target` | A group of units, a synchronisation point (replaces runlevels) | `multi-user.target` |
| Timer | `.timer` | A schedule that activates another unit (usually a service) | `logrotate.timer`, `dnf-makecache.timer` |
| Mount | `.mount` | A mounted filesystem (generated from /etc/fstab or written by hand) | `var-lib-data.mount` |
| Automount | `.automount` | A mount point that mounts on first access | `proc-sys-fs-binfmt_misc.automount` |
| Path | `.path` | Watches a file or directory and starts a unit when it changes | `cups.path` |
| Device | `.device` | A kernel device exposed by udev | `dev-sda.device` |
| Swap | `.swap` | A swap device or file | `dev-mapper-rl\x2dswap.swap` |
| Slice | `.slice` | A cgroup node for resource control | `system.slice`, `user.slice` |
| Scope | `.scope` | Externally created processes grouped by systemd (for example, login sessions) | `session-3.scope` |

```bash
systemctl list-units --type=service --state=running
systemctl list-units --type=service --state=failed
systemctl list-unit-files --type=service
```

```text
UNIT                       LOAD   ACTIVE SUB     DESCRIPTION
auditd.service             loaded active running Security Auditing Service
chronyd.service            loaded active running NTP client/server
crond.service              loaded active running Command Scheduler
firewalld.service          loaded active running firewalld - dynamic firewall daemon
sshd.service               loaded active running OpenSSH server daemon
...
```

**How to read it:** `LOAD` says whether systemd parsed the unit file (`loaded`, `not-found`, `masked`, `error`). `ACTIVE` is the high-level state (`active`, `inactive`, `failed`, `activating`). `SUB` is the type-specific detail (`running`, `exited`, `dead`, `auto-restart`). `systemctl --failed` is the first command to run when you log in to a server someone says is "acting weird".

## 2. Where unit files live, and which one wins

### /usr/lib vs /etc vs /run

| Path | Who writes it | Priority | Rule |
|---|---|---|---|
| `/etc/systemd/system/` | **You** (the admin) | Highest | Your overrides and your own units. Survives package updates. |
| `/run/systemd/system/` | Runtime, generators | Middle | Lost at reboot. Rarely touched by hand. |
| `/usr/lib/systemd/system/` | **Packages** (RPM, deb) | Lowest | Vendor defaults. **Never edit here**: the next `dnf update` overwrites your change. |

On Debian/Ubuntu the vendor path is `/lib/systemd/system/` (which is the same directory as `/usr/lib/systemd/system/` on merged-/usr systems).

If a file with the same name exists in `/etc/systemd/system/` and `/usr/lib/systemd/system/`, the one in **/etc wins completely**. That is a full replacement, which means you stop getting vendor fixes for that unit. Usually you want a **drop-in** instead.

```bash
systemctl cat sshd
```

```text
# /usr/lib/systemd/system/sshd.service
[Unit]
Description=OpenSSH server daemon
Documentation=man:sshd(8) man:sshd_config(5)
After=network.target sshd-keygen.target
Wants=sshd-keygen.target

[Service]
Type=notify
EnvironmentFile=-/etc/sysconfig/sshd
ExecStart=/usr/sbin/sshd -D $OPTIONS
ExecReload=/bin/kill -HUP $MAINPID
KillMode=process
Restart=on-failure
RestartSec=42s

[Install]
WantedBy=multi-user.target

# /etc/systemd/system/sshd.service.d/override.conf
[Service]
RestartSec=5s
```

`systemctl cat` is the single most useful command for understanding a unit: it prints **every file that contributes to it**, in order, with the path as a comment. Exact contents vary by release; trust what `systemctl cat` shows on your box, not a blog post.

### Drop-ins with systemctl edit

A **drop-in** is a small `.conf` file in `/etc/systemd/system/<unit>.d/` that changes only the directives you list. It merges on top of the vendor file.

```bash
sudo systemctl edit myapp.service          # creates /etc/systemd/system/myapp.service.d/override.conf
sudo systemctl edit --full myapp.service   # copies the whole unit to /etc/systemd/system/ for full editing
sudo systemctl revert myapp.service        # removes your drop-ins and /etc copies, back to vendor
```

`systemctl edit` opens an editor, saves the drop-in, and runs `daemon-reload` for you. If you create drop-in files by hand (for example from Ansible), you must run `systemctl daemon-reload` yourself.

:::warn
**List-type directives append, they do not replace.** `ExecStart=`, `Environment=`, `After=` and similar can be specified many times. To *replace* `ExecStart` in a drop-in, first clear it with an empty assignment:

```ini
[Service]
ExecStart=
ExecStart=/opt/myapp/bin/myapp --config /etc/myapp/prod.yaml
```

Without the empty line, a `Type=simple` service fails to load with "Service has more than one ExecStart= setting, which is only allowed for Type=oneshot services."
:::

### daemon-reload

systemd parses unit files into memory. Editing a file on disk changes nothing until you run:

```bash
sudo systemctl daemon-reload
```

If you forget, `systemctl status` warns you:

```text
Warning: The unit file, source configuration file or drop-ins of myapp.service changed on disk. Run 'systemctl daemon-reload' to reload units.
```

`daemon-reload` re-reads the configuration; it does **not** restart running services. After the reload, `systemctl restart myapp` applies the new settings to the process.

## 3. Unit file anatomy

Here is a complete, realistic unit for a custom application. We will build on it all module.

```ini
# /etc/systemd/system/myapp.service
[Unit]
Description=MyApp API server
Documentation=https://app.example.com/docs
After=network-online.target postgresql.service
Wants=network-online.target
Requires=postgresql.service

[Service]
Type=simple
User=myapp
Group=myapp
WorkingDirectory=/opt/myapp
Environment=APP_ENV=production
Environment="JAVA_OPTS=-Xms256m -Xmx512m"
EnvironmentFile=-/etc/myapp/myapp.env
ExecStartPre=/opt/myapp/bin/myapp --check-config
ExecStart=/opt/myapp/bin/myapp --listen 0.0.0.0:8080
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=5s
TimeoutStopSec=30s
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

### The [Unit] section: ordering vs dependency

The most misunderstood part of systemd is that **ordering and dependency are separate**.

| Directive | Kind | Meaning |
|---|---|---|
| `After=X` | Ordering only | If both are being started, start me after X has finished starting. Does **not** pull X in. |
| `Before=X` | Ordering only | The reverse. |
| `Wants=X` | Weak dependency | Starting me also starts X. If X fails, I start anyway. |
| `Requires=X` | Strong dependency | Starting me also starts X. If X fails to start, I am not started; if X is **explicitly stopped or restarted**, I am too. |
| `BindsTo=X` | Stronger | Like Requires, and I also stop if X stops for any reason (for example, a device disappearing). |
| `PartOf=X` | Propagation | Stop and restart of X propagate to me (one direction). |
| `Conflicts=X` | Negative | Starting me stops X, and vice versa. |

:::concept
`Requires=postgresql.service` **without** `After=postgresql.service` starts both **at the same time**. Your app can still race the database and crash on boot. You almost always want the pair: `Wants=` or `Requires=` to pull the unit in, plus `After=` to order it.
:::

**network.target vs network-online.target.** `network.target` only means the network management stack has started; interfaces may have no address yet. If your app connects to a remote host at startup, or binds to a specific IP, use `After=network-online.target` plus `Wants=network-online.target`. Binding to `0.0.0.0` does not need an address to exist, so many servers are fine with `network.target`.

### The [Service] section

#### Type=

| Type | systemd considers the service "started" when... | Use it for |
|---|---|---|
| `simple` (default if `ExecStart` is set) | Immediately after `fork()`, before the binary has even been executed | Foreground apps that do not daemonise. A typo in the binary path still looks like a successful start for a moment, then fails. |
| `exec` | After the binary has been successfully `execve()`d (systemd 240+, so RHEL 9 yes, RHEL 8 no) | Same as simple, but `systemctl start` reports exec failures (such as 203/EXEC) directly. A good default for new units on RHEL 9. |
| `forking` | The `ExecStart` process exits after forking a child (classic daemon) | Legacy daemons that background themselves. Set `PIDFile=` so systemd finds the right main process. |
| `notify` | The service sends `READY=1` via `sd_notify()` | Daemons built with systemd support (sshd, many modern servers). If the app never notifies, the start times out after `TimeoutStartSec` (90 s by default). |
| `oneshot` | The process **exits** successfully | Scripts and setup tasks. Combine with `RemainAfterExit=yes` if the unit should show as `active` afterwards. Often used with timers. |
| `dbus`, `idle` | Bus name acquired / jobs finished | Rare in custom apps. |

:::warn
The most common Type= mistake: a daemon that **forks into the background** is configured as `Type=simple`. systemd sees the main process exit with status 0 and thinks the service has stopped, so it kills the cgroup, taking the real daemon with it. The service shows `inactive (dead)` seconds after a "successful" start. Either run the app in the foreground (most apps have a `--foreground` or `-D` flag) or use `Type=forking` with `PIDFile=`.
:::

#### ExecStart and friends

- `ExecStart=` must be an **absolute path** in practice. (Recent systemd versions search a fixed set of directories for bare names, but do not rely on it.) It is **not run through a shell**: pipes, `>` redirection, `&&` and `*` globs do not work. If you need a shell, write a wrapper script or use `ExecStart=/bin/bash -c '...'`.
- Prefix `-` (as in `ExecStartPre=-/usr/bin/something`) means "ignore a non-zero exit".
- `ExecStartPre=` / `ExecStartPost=` run before and after the main process; a failing `ExecStartPre` aborts the start. Config checks belong here.
- `ExecReload=` defines what `systemctl reload` does. `$MAINPID` is substituted by systemd.
- `ExecStop=` is optional; by default systemd sends `SIGTERM`, waits `TimeoutStopSec` (90 s default), then `SIGKILL`.

#### User, Group, WorkingDirectory

- `User=myapp` runs the process as that account. The user must exist **before** the service starts, or you get `217/USER`. Create service accounts with `useradd --system --no-create-home --shell /sbin/nologin myapp`.
- `DynamicUser=yes` creates a transient user automatically, useful for stateless services.
- `WorkingDirectory=/opt/myapp` sets the current directory. If it does not exist, the service fails with `200/CHDIR`. Prefix with `-` to make it optional.

#### Environment and EnvironmentFile

```ini
Environment=APP_ENV=production
Environment="JAVA_OPTS=-Xms256m -Xmx512m"
EnvironmentFile=-/etc/myapp/myapp.env
```

```text
# /etc/myapp/myapp.env  (KEY=VALUE lines, no "export", no shell expansion)
DB_HOST=10.0.0.21
DB_PORT=5432
LOG_LEVEL=info
```

Key facts:

- Services get a **minimal, clean environment**. Nothing from your login shell, `/etc/profile` or `~/.bashrc` is inherited. "It works when I run it by hand" is very often an environment difference.
- The leading `-` on `EnvironmentFile` means "don't fail if the file is missing".
- Environment files are not shell scripts: `$(command)` and `export` do not work.
- Keep secrets out of the unit file. `systemctl show myapp -p Environment` is readable by any local user, while an `EnvironmentFile` can be `root:root` mode `0600` (systemd reads it as root before dropping privileges). On systemd 247+ (RHEL 9), `LoadCredential=` is a cleaner option for secrets.

#### Restart= and RestartSec

| Restart= value | Restarts when the process... |
|---|---|
| `no` (default) | Never |
| `on-failure` | Exits non-zero, is killed by a signal, times out, or the watchdog fires |
| `on-abnormal` | Is killed by a signal, times out, or the watchdog fires (not on a clean non-zero exit) |
| `on-abort` | Is killed by an uncaught signal |
| `always` | Exits for any reason, even exit 0 |

`RestartSec=` is the delay before restarting (default 100 ms). systemd also applies a **start rate limit**: by default no more than `StartLimitBurst=5` starts within `StartLimitIntervalSec=10s`. Exceed it and the unit enters `failed` with result **`start-limit-hit`**, and systemd stops trying. These two settings belong in the **[Unit]** section, not [Service].

```ini
[Unit]
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Restart=on-failure
RestartSec=10s
```

With these values, a crashing service is retried every 10 seconds, and if it fails 5 times within 5 minutes, systemd gives up and alerts you by leaving it in `failed`. That is usually what you want: a crash loop that hides behind auto-restart forever is worse than a clean failure that monitoring catches.

### The [Install] section

`[Install]` is only read by `systemctl enable`/`disable`. `WantedBy=multi-user.target` means "when enabled, create a symlink so multi-user.target pulls me in at boot".

## 4. Enable vs start, mask, and targets

### enable vs start

| Command | Effect now | Effect at next boot |
|---|---|---|
| `systemctl start myapp` | Starts it | Nothing |
| `systemctl enable myapp` | Nothing (creates symlinks) | Starts it |
| `systemctl enable --now myapp` | Starts it | Starts it |
| `systemctl stop myapp` | Stops it | Nothing |
| `systemctl disable --now myapp` | Stops it | Does not start it |
| `systemctl restart myapp` | Stop then start | Nothing |
| `systemctl reload myapp` | Runs `ExecReload` (if defined) | Nothing |
| `systemctl try-restart myapp` | Restarts only if already running | Nothing |

```bash
sudo systemctl enable --now myapp
```

```text
Created symlink /etc/systemd/system/multi-user.target.wants/myapp.service → /etc/systemd/system/myapp.service.
```

```bash
systemctl is-enabled myapp
systemctl is-active myapp
systemctl is-failed myapp
```

:::prod
**The server that lost its app at reboot.** Symptom: after a kernel patch reboot, the payment API on one node is down; the other three are fine. What the engineer saw: `systemctl status payapi` showed `Loaded: loaded (/etc/systemd/system/payapi.service; disabled; ...)` and `Active: inactive (dead)`. Root cause: during an emergency six weeks earlier, someone deployed the node by hand with `systemctl start` and never ran `enable`. The service ran perfectly until the first reboot. Fix: `systemctl enable --now payapi`. Prevention: Ansible's `systemd` module with `enabled: true` and `state: started`, plus a compliance check that every expected unit reports `enabled` from `systemctl is-enabled`.
:::

### Masked units

`systemctl mask` links the unit to `/dev/null`, so it **cannot be started at all**, not by hand, not as a dependency.

```bash
sudo systemctl mask --now cups
systemctl start cups
```

```text
Failed to start cups.service: Unit cups.service is masked.
```

```bash
sudo systemctl unmask cups
```

Masking is the right tool for services that must never run on a hardened server (for example, conflicting firewall services, `ctrl-alt-del.target` on servers). It is also a common cause of "the service won't start and there's nothing in the logs": `systemctl status` shows `Loaded: masked`.

### Targets

Targets group units and define system states. They replaced SysV runlevels.

| Target | Old runlevel | Meaning |
|---|---|---|
| `poweroff.target` | 0 | Shut down |
| `rescue.target` | 1 | Single-user, basic system, root shell, local filesystems mounted |
| `multi-user.target` | 3 | Full multi-user, networking, no GUI. **Default on servers.** |
| `graphical.target` | 5 | multi-user plus a display manager |
| `reboot.target` | 6 | Reboot |
| `emergency.target` | - | Minimal: root filesystem read-only, almost nothing started |

```bash
systemctl get-default
sudo systemctl set-default multi-user.target
sudo systemctl isolate rescue.target       # switch now (drops network sessions: use the console)
systemctl list-dependencies multi-user.target
```

Boot-time rescue and emergency targets (adding `systemd.unit=rescue.target` on the kernel command line) are covered in the boot module.

## 5. Reading systemctl status like an SRE

```bash
systemctl status myapp
```

```text
× myapp.service - MyApp API server
     Loaded: loaded (/etc/systemd/system/myapp.service; enabled; preset: disabled)
    Drop-In: /etc/systemd/system/myapp.service.d
             └─override.conf
     Active: failed (Result: exit-code) since Tue 2026-11-24 09:14:07 UTC; 2min 3s ago
   Duration: 1.204s
    Process: 4121 ExecStartPre=/opt/myapp/bin/myapp --check-config (code=exited, status=0/SUCCESS)
    Process: 4123 ExecStart=/opt/myapp/bin/myapp --listen 0.0.0.0:8080 (code=exited, status=1/FAILURE)
   Main PID: 4123 (code=exited, status=1/FAILURE)
        CPU: 812ms

Nov 24 09:14:06 app01 systemd[1]: Starting MyApp API server...
Nov 24 09:14:06 app01 systemd[1]: Started MyApp API server.
Nov 24 09:14:07 app01 myapp[4123]: FATAL: cannot connect to database 10.0.0.21:5432: connection refused
Nov 24 09:14:07 app01 systemd[1]: myapp.service: Main process exited, code=exited, status=1/FAILURE
Nov 24 09:14:07 app01 systemd[1]: myapp.service: Failed with result 'exit-code'.
```

Read it top to bottom:

1. **First line**: the dot or cross (`●` active, `○` inactive, `×` failed on newer systemd) and the description.
2. **Loaded**: which file, `enabled`/`disabled`/`masked`, and the vendor **preset**. If you see `not-found`, the name is wrong or the file is missing.
3. **Drop-In**: there are overrides. Always check them with `systemctl cat`; the cause is often in a forgotten override.
4. **Active**: state, **Result**, and when. Results you will see: `exit-code`, `signal` (killed, for example by the OOM killer with SIGKILL), `timeout`, `core-dump`, `start-limit-hit`, `resources` (systemd could not set something up).
5. **Process / Main PID**: each Exec line with its exit status. Status `1/FAILURE` comes from the app. Status in the **200 range** comes from **systemd itself**, before your code ran.
6. **Log lines**: the last 10 journal lines for this unit. Use `-n 50` or `journalctl -u` for more. Add `-l` (or `--no-pager -l`) if lines are truncated.

### systemd's own exit codes

When the status is a number in the 200s, the app never started. systemd failed while preparing the process.

| Status | Name | Meaning | Usual cause |
|---|---|---|---|
| 200 | CHDIR | Could not change to `WorkingDirectory` | Directory missing, or `User` cannot traverse it |
| 203 | EXEC | Could not execute `ExecStart` | Wrong path, no `+x` bit, bad shebang or CRLF line endings, `noexec` mount, **SELinux label** |
| 204 | MEMORY | Out of memory during setup | Host memory pressure |
| 209 | STDOUT | Could not set up stdout | `StandardOutput=file:` path not writable |
| 214 | SETSCHEDULER | Could not set scheduling | Bad `CPUSchedulingPolicy` |
| 217 | USER | Could not resolve or switch to `User=`/`Group=` | Account does not exist (or not yet, on first boot) |
| 226 | NAMESPACE | Could not set up namespaces | `ReadWritePaths=`/`ProtectSystem=` path missing |
| 243 | CREDENTIALS | Could not set up credentials | `LoadCredential=` file missing |

Other useful commands:

```bash
systemctl show myapp -p ExecMainStatus -p Result -p NRestarts -p MainPID
systemctl list-dependencies myapp
systemd-analyze verify /etc/systemd/system/myapp.service
systemd-analyze blame | head -20          # slowest units at boot
systemd-analyze critical-chain myapp.service
```

`systemd-analyze verify` catches typos such as `Resart=` and unknown sections before they reach production. Put it in your CI or Ansible pipeline.

## 6. journalctl: the first place to look

`systemd-journald` collects kernel messages, early boot messages, everything services write to stdout/stderr, syslog messages and structured metadata (unit, PID, UID, SELinux context, executable path), and stores them in an indexed binary journal.

### The commands you will use every day

```bash
journalctl -u myapp                      # one unit
journalctl -u myapp -n 100 --no-pager    # last 100 lines, no pager (good for pasting into tickets)
journalctl -u myapp -f                   # follow live, like tail -f
journalctl -u myapp -u nginx --since "10 min ago"
journalctl -b                            # this boot
journalctl -b -1                         # previous boot (needs persistent journal)
journalctl --list-boots
journalctl -k                            # kernel messages (dmesg) for this boot
journalctl -p err -b                     # priority err and worse, this boot
journalctl -p warning..err               # a range
journalctl --since "2026-11-24 09:00" --until "2026-11-24 09:30"
journalctl --since yesterday
journalctl _PID=4123                     # by field
journalctl _UID=1001
journalctl /usr/sbin/sshd                # by executable
journalctl -u myapp -g "timeout|refused" # grep pattern (needs journalctl built with PCRE2; works on RHEL 9)
journalctl -xeu myapp                    # jump to end, add explanatory catalog text
```

Priorities, from most to least severe: `emerg` (0), `alert` (1), `crit` (2), `err` (3), `warning` (4), `notice` (5), `info` (6), `debug` (7). `-p err` means **err and everything more severe**.

:::tip
On a failing service, `journalctl -xeu myapp` is the muscle-memory command (it is what systemd itself suggests in its error messages). But `-e` jumps to the end of *all* history for the unit, so on a busy unit use `journalctl -u myapp -b --since "15 min ago"` to see only the relevant attempt.
:::

### Output formats with -o

| `-o` value | Use it for |
|---|---|
| `short` (default) | Syslog-style, human readable |
| `short-iso` / `short-precise` | ISO timestamps / microseconds; good for correlating across hosts |
| `cat` | Message only, no prefix; good for piping app output |
| `verbose` | Every field of every entry, including `_SYSTEMD_UNIT`, `_SELINUX_CONTEXT`, `_CMDLINE` |
| `json` / `json-pretty` | Feeding into tools (`jq`) or log shippers |

```bash
journalctl -u myapp -n 1 -o verbose
```

```text
Tue 2026-11-24 09:14:07.512331 UTC [s=8c1d...;i=4a3f;b=91e2...;m=1b0f...;t=5f1a...;x=2c...]
    PRIORITY=6
    _UID=992
    _GID=992
    _COMM=myapp
    _EXE=/opt/myapp/bin/myapp
    _CMDLINE=/opt/myapp/bin/myapp --listen 0.0.0.0:8080
    _SELINUX_CONTEXT=system_u:system_r:unconfined_service_t:s0
    _SYSTEMD_UNIT=myapp.service
    MESSAGE=FATAL: cannot connect to database 10.0.0.21:5432: connection refused
    _PID=4123
    _HOSTNAME=app01
```

Notice `_SELINUX_CONTEXT`: you can see which domain the process ran in without any other tool.

### Who can read the journal

Root, and members of the `systemd-journal` and `wheel` (RHEL) / `adm` (Ubuntu) groups can read the full system journal. Regular users see only their own user journal. If an on-call engineer says "journalctl shows nothing", check `id` first.

### Volatile vs persistent journal

| Storage | Location | Survives reboot? |
|---|---|---|
| Volatile | `/run/log/journal/` (tmpfs, RAM) | No |
| Persistent | `/var/log/journal/` | Yes |

The `Storage=` setting in `journald.conf` defaults to `auto`: **persistent if `/var/log/journal/` exists, volatile otherwise**. Whether that directory exists out of the box varies by distribution and release (Ubuntu and Fedora create it; on RHEL-family systems check with `ls -d /var/log/journal`; RHEL has historically relied on rsyslog writing `/var/log/messages` for persistence). If `journalctl --list-boots` shows only one boot, your journal is volatile, and you cannot investigate why the server crashed last night.

To make it persistent:

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/00-persistent.conf >/dev/null <<'EOF'
[Journal]
Storage=persistent
SystemMaxUse=1G
SystemKeepFree=2G
MaxRetentionSec=1month
EOF
sudo systemctl restart systemd-journald
journalctl --flush        # move /run journal into /var
```

### Disk usage and vacuuming

```bash
journalctl --disk-usage
```

```text
Archived and active journals take up 3.9G in the file system.
```

```bash
sudo journalctl --vacuum-size=500M     # delete archived files until total is under 500M
sudo journalctl --vacuum-time=14d      # delete archived files older than 14 days
sudo journalctl --vacuum-files=5       # keep at most 5 archived files
sudo journalctl --rotate               # close the active file so it can be vacuumed too
```

Vacuuming only removes **archived** journal files. Run `--rotate` first (or combine: `journalctl --rotate --vacuum-size=500M` on systemd 245+) if the active file is the big one.

Key `journald.conf` limits:

| Setting | Default | Meaning |
|---|---|---|
| `SystemMaxUse=` | 10% of the filesystem, capped at 4G | Total journal size in /var/log/journal |
| `SystemKeepFree=` | 15% of the filesystem, capped at 4G | Always leave this much free |
| `SystemMaxFileSize=` | 1/8 of SystemMaxUse | Size at which a file is rotated |
| `RuntimeMaxUse=` | 10% of /run, capped at 4G | Same for the volatile journal |
| `MaxRetentionSec=` | 0 (no time limit) | Delete entries older than this |
| `RateLimitIntervalSec=` / `RateLimitBurst=` | 30s / 10000 | Per-service message rate limit; excess is dropped with "Suppressed N messages" |

:::warn
The defaults are percentages of the **filesystem holding /var/log**. On a server where `/var` is part of a 500 GB root filesystem, 10% is 4 GB (the cap). On a 20 GB root filesystem with a chatty app, the journal plus rsyslog's copy in `/var/log/messages` can easily eat a quarter of the disk. Set explicit limits on every server through configuration management.
:::

## 7. /var/log, rsyslog and logrotate

### The classic log files

On RHEL, **rsyslog** reads from the journal (the `imjournal` module) and writes traditional text files. Many monitoring and SIEM tools still read these.

| File (RHEL) | Ubuntu/Debian equivalent | Contents |
|---|---|---|
| `/var/log/messages` | `/var/log/syslog` | General system messages (info and above, minus mail/auth/cron) |
| `/var/log/secure` | `/var/log/auth.log` | Authentication: sshd, sudo, PAM |
| `/var/log/cron` | in `/var/log/syslog` (grep CRON) | crond activity |
| `/var/log/maillog` | `/var/log/mail.log` | Mail |
| `/var/log/boot.log` | - | Boot service status messages |
| `/var/log/audit/audit.log` | same (if auditd installed) | **auditd**: SELinux AVC denials, audit rules. Written by auditd, not rsyslog. |
| `/var/log/dnf.log` | `/var/log/apt/history.log` | Package operations |

```bash
grep -i myapp /var/log/messages | tail
tail -f /var/log/secure
```

### rsyslog configuration

```text
# /etc/rsyslog.conf (excerpt)
*.info;mail.none;authpriv.none;cron.none    /var/log/messages
authpriv.*                                  /var/log/secure
cron.*                                      /var/log/cron
```

Each rule is `facility.priority  destination`. Add your own in `/etc/rsyslog.d/*.conf`, for example to forward everything to a central collector:

```text
# /etc/rsyslog.d/50-forward.conf
*.* @@10.0.0.50:514      # @@ = TCP, @ = UDP
```

```bash
rsyslogd -N1                       # validate config syntax
sudo systemctl restart rsyslog
logger -p user.err "test from $(hostname)"   # write a test message
```

:::tip
SELinux note: rsyslog runs as `syslogd_t`. Forwarding to the standard port 514 is allowed. If you forward to a non-standard port such as 6514 with TLS that is already labelled, fine; for an arbitrary port, check `semanage port -l | grep syslog` and add it with `semanage port -a -t syslogd_port_t -p tcp 10514` rather than disabling anything.
:::

### logrotate

logrotate rotates, compresses and deletes text logs. On RHEL 9 it is triggered daily by **`logrotate.timer`** (older releases used `/etc/cron.daily/logrotate`). Check with `systemctl list-timers logrotate.timer`.

```text
# /etc/logrotate.d/myapp
/var/log/myapp/*.log {
    daily
    rotate 14
    maxsize 200M
    missingok
    notifempty
    compress
    delaycompress
    dateext
    create 0640 myapp myapp
    sharedscripts
    postrotate
        /usr/bin/systemctl kill -s HUP myapp.service >/dev/null 2>&1 || true
    endscript
}
```

| Directive | Meaning |
|---|---|
| `daily` / `weekly` / `size 100M` / `maxsize 200M` | When to rotate. `maxsize` rotates early if the file grows past it at the next run. |
| `rotate 14` | Keep 14 old files |
| `compress` + `delaycompress` | gzip old files, but leave the most recent rotated file uncompressed (for apps that still write to it briefly) |
| `create 0640 myapp myapp` | Create a fresh empty file with this mode/owner after moving the old one |
| `copytruncate` | Copy the file then truncate it in place. For apps that cannot reopen their log. Can lose a few lines written during the copy. |
| `postrotate ... endscript` | Command to tell the app to reopen its log (usually SIGHUP) |

```bash
sudo logrotate -d /etc/logrotate.d/myapp     # dry run, debug output, changes nothing
sudo logrotate -vf /etc/logrotate.d/myapp    # force a rotation now, verbose
cat /var/lib/logrotate/logrotate.status      # when each file was last rotated
```

:::prod
**Deleted but still open.** Symptom: `/var` at 100%, `du -sh /var/log/*` adds up to 2 GB on a 40 GB filesystem. What the engineer saw: `lsof +L1` listed a 36 GB `/var/log/myapp/app.log (deleted)` held open by the Java process. Root cause: a teammate "cleaned up" with `rm app.log`. The inode was still open, so the space was never freed and the app kept writing to an invisible file. Fix: `systemctl restart myapp` (or truncate via `: > /proc/<PID>/fd/<N>`). Prevention: a logrotate rule with `maxsize` and a `postrotate` HUP (or `copytruncate`), and a runbook line saying "never `rm` an open log; truncate it with `: > file`".
:::

:::warn
**SELinux and logs in /opt.** If your app writes logs to `/opt/myapp/logs/`, those files are labelled `usr_t`. logrotate runs as `logrotate_t`, which is not allowed to rename and create files there, so rotation silently fails and `ausearch -m AVC -c logrotate` shows denials. Prefer `/var/log/myapp/` (inherits `var_log_t`). If the path cannot change, label it properly:

```bash
sudo semanage fcontext -a -t var_log_t "/opt/myapp/logs(/.*)?"
sudo restorecon -Rv /opt/myapp/logs
```
:::

## 8. Scheduling: cron vs systemd timers

### cron and crontab syntax

On RHEL the daemon is **cronie** (`crond.service`); on Ubuntu/Debian it is `cron.service`.

```text
# ┌──────── minute (0-59)
# │ ┌────── hour (0-23)
# │ │ ┌──── day of month (1-31)
# │ │ │ ┌── month (1-12)
# │ │ │ │ ┌ day of week (0-7, 0 and 7 = Sunday)
# │ │ │ │ │
  30 2 * * 1-5  /opt/myapp/bin/nightly-report.sh
  */15 * * * *  /opt/myapp/bin/healthcheck.sh
  0 0 1 * *     /opt/myapp/bin/monthly-cleanup.sh
  @reboot       /opt/myapp/bin/warm-cache.sh
```

| Where | Format | Notes |
|---|---|---|
| `crontab -e` (per user, stored in `/var/spool/cron/<user>`) | 5 time fields + command | Runs as that user. Edit only with `crontab -e` (it validates). |
| `/etc/crontab` | 5 time fields + **user** + command | System-wide |
| `/etc/cron.d/*` | 5 time fields + **user** + command | Best for packages and config management. File names must not contain dots on Debian (run-parts rule). |
| `/etc/cron.{hourly,daily,weekly,monthly}/` | Executable scripts | Run by `anacron` on RHEL, so they still run after downtime |

```bash
crontab -l                    # list your jobs
sudo crontab -l -u myapp      # list another user's jobs
grep CROND /var/log/cron | tail     # RHEL: did it fire?
journalctl -u crond --since today
```

### The cron environment: why jobs work by hand but not in cron

A cron job does **not** run your login shell. It gets a tiny environment:

| Variable | In your SSH session | In a cron job (cronie defaults) |
|---|---|---|
| `PATH` | `/usr/local/bin:/usr/bin:/usr/local/sbin:/usr/sbin:...` | `/usr/bin:/bin` for user crontabs (`/etc/crontab` sets its own `PATH`) |
| `SHELL` | `/bin/bash` | `/bin/sh` |
| `HOME` | your home | from `/etc/passwd` |
| Profile files | `.bash_profile`, `.bashrc`, `/etc/profile.d/*` loaded | **None loaded** |
| Terminal | yes | no TTY, so anything interactive (sudo prompts, `read`) hangs or fails |
| Current directory | wherever you were | the user's `HOME` |

Classic failures:

1. **Command not found.** The script calls `ss`, `ip`, `semanage` (all in `/usr/sbin`) or a tool in `/usr/local/bin`. Not in cron's `PATH`.
2. **The `%` sign.** In a crontab command, **`%` means newline**; everything after the first unescaped `%` is fed to the command as **stdin**. `date +%F` in a crontab line breaks silently. Escape it as `\%` or move the logic into a script.
3. **Relative paths.** `./backup.sh` or `config.yaml` resolve against `$HOME`, not the script's directory.
4. **Missing variables.** `JAVA_HOME`, proxy settings or `ORACLE_HOME` set in `.bash_profile` are not there.
5. **Bash syntax under `/bin/sh`.** Only an issue for inline crontab commands; scripts with a `#!/bin/bash` shebang are fine.
6. **No output captured.** Output is mailed to the user (`MAILTO`), and on servers without a mail setup it just disappears. Always redirect.

A robust crontab:

```text
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/usr/local/sbin:/usr/sbin
MAILTO=""
30 2 * * * /opt/myapp/bin/nightly-report.sh >> /var/log/myapp/nightly-report.log 2>&1
5 3 * * * tar czf /backup/etc-$(date +\%F).tgz /etc >/dev/null 2>&1
```

And a robust script starts by setting what it needs:

```bash
#!/bin/bash
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/usr/local/sbin:/usr/sbin
cd "$(dirname "$0")"
```

:::tip
To reproduce cron's environment by hand, run your script with an almost empty environment: `env -i HOME=/root PATH=/usr/bin:/bin SHELL=/bin/sh /bin/sh -c '/opt/myapp/bin/nightly-report.sh'`. If it fails there, it will fail in cron.
:::

### systemd timers

A timer is a `.timer` unit that activates a `.service` unit of the same name.

```ini
# /etc/systemd/system/myapp-report.service
[Unit]
Description=MyApp nightly report

[Service]
Type=oneshot
User=myapp
ExecStart=/opt/myapp/bin/nightly-report.sh
```

```ini
# /etc/systemd/system/myapp-report.timer
[Unit]
Description=Run MyApp nightly report

[Timer]
OnCalendar=Mon..Fri *-*-* 02:30:00
Persistent=true
RandomizedDelaySec=5min

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now myapp-report.timer
systemctl list-timers --all
systemd-analyze calendar "Mon..Fri *-*-* 02:30:00"     # check what your expression means
sudo systemctl start myapp-report.service               # run the job now, on demand
journalctl -u myapp-report.service --since today
```

```text
NEXT                        LEFT     LAST                        PASSED  UNIT                 ACTIVATES
Wed 2026-11-25 02:33:41 UTC 14h left Tue 2026-11-24 02:31:12 UTC 9h ago  myapp-report.timer   myapp-report.service
```

| | cron | systemd timer |
|---|---|---|
| Syntax | 5 fields, compact | `OnCalendar=` (readable, testable with `systemd-analyze calendar`), plus `OnBootSec=`, `OnUnitActiveSec=` |
| Logging | Your job must redirect output | stdout/stderr go to the journal automatically, with exit status |
| Missed runs (server off) | Lost (anacron only for daily/weekly/monthly) | `Persistent=true` runs it at next boot |
| Overlapping runs | Possible (needs `flock`) | A running service is not started twice |
| Environment | Minimal, surprises | Minimal too, but explicit and documented in the unit |
| Resource limits, sandboxing, dependencies | No | Yes (`MemoryMax=`, `After=`, `User=`, ...) |
| Failure visibility | Silent unless you build it | Unit goes `failed`; `systemctl --failed` and `OnFailure=` show it |
| Familiarity / portability | Universal | systemd only |

For new production jobs on RHEL 9, prefer timers. Keep cron where a team or tool expects it, but apply the hygiene above. Note `AccuracySec=` defaults to 1 minute, so a timer may fire up to a minute late unless you lower it.

## 9. Writing a service for a custom app in /opt, with SELinux

This is the most common real-world task in this module: a developer hands you a binary or a script and says "make it run as a service".

### Step 1: account, directories, ownership

```bash
sudo useradd --system --no-create-home --shell /sbin/nologin myapp
sudo mkdir -p /opt/myapp/bin /etc/myapp /var/log/myapp /var/lib/myapp
sudo install -m 0755 -o root -g root ./myapp /opt/myapp/bin/myapp
sudo chown myapp:myapp /var/log/myapp /var/lib/myapp
sudo install -m 0600 -o root -g root ./myapp.env /etc/myapp/myapp.env
```

Binaries owned by **root** and not writable by the service account: if the app is compromised, the attacker cannot replace its own binary. Data and logs go to `/var/lib` and `/var/log`, not into `/opt`.

### Step 2: understand the SELinux labels involved

On RHEL with SELinux enforcing, every file has a type, and the type decides who can do what with it. The default policy has rules for these paths:

```bash
sudo semanage fcontext -l | grep -E '^/opt|^/home/\[\^/\]\+' | head
ls -Z /opt/myapp/bin/myapp
```

| Where the file is | Typical type | Can systemd (`init_t`) execute it? |
|---|---|---|
| `/usr/bin`, `/usr/sbin`, `/usr/local/bin` | `bin_t` | Yes; the service runs as `unconfined_service_t` |
| `/opt/...` (general) | `usr_t` | Yes, also runs as `unconfined_service_t` |
| `/opt/<anything>/bin/...` | `bin_t` (policy has a rule for it) | Yes |
| `/home/<user>/...` | `user_home_t` | **No: AVC denial, 203/EXEC** |
| `/tmp/...` | `user_tmp_t` / `tmp_t` | **No** |
| A file **moved** (`mv`) from home or /tmp into /opt | **keeps** `user_home_t` / `user_tmp_t` | **No, even though it now sits in /opt** |

:::concept
`cp` creates a new file, which gets the default label of the target directory. **`mv` keeps the original label**, because it only renames the inode on the same filesystem. So the developer who builds in `~/build`, runs `sudo mv ~/build/myapp /opt/myapp/bin/`, and then writes a perfect unit file gets `203/EXEC` with "Permission denied", even as root and with mode 0755. The file *looks* right with `ls -l`; only `ls -Z` shows the problem.
:::

The correct, permanent fix is to make the policy's idea of the right label match reality, then apply it:

```bash
# Only needed if your path is not already covered by a default rule:
sudo semanage fcontext -a -t bin_t "/opt/myapp/bin(/.*)?"
# Apply the policy's labels to what is on disk (fixes mv'd files too):
sudo restorecon -Rv /opt/myapp
```

```text
Relabeled /opt/myapp/bin/myapp from unconfined_u:object_r:user_home_t:s0 to unconfined_u:object_r:bin_t:s0
```

`chcon -t bin_t file` also works, but **only until the next relabel** (`restorecon`, a full `/.autorelabel`, or a policy rebuild), which makes the outage come back months later. Use `semanage fcontext` + `restorecon` in production.

:::warn
Do not run your service from `/home`. Even if you label it, home directories have different backup, mount (`noexec` on hardened builds) and permission expectations, and auditors flag services running from user-owned paths. Move it to `/opt/<app>` or `/usr/local/bin`.
:::

On Ubuntu/Debian there is no SELinux by default. AppArmor confines only applications that ship a profile, so a custom binary in `/opt` usually runs unconfined, and 203/EXEC there is nearly always a path, permission, shebang or `noexec` problem.

### Step 3: the unit file

```ini
# /etc/systemd/system/myapp.service
[Unit]
Description=MyApp API server
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=exec
User=myapp
Group=myapp
WorkingDirectory=/var/lib/myapp
EnvironmentFile=/etc/myapp/myapp.env
ExecStart=/opt/myapp/bin/myapp --listen 0.0.0.0:8080 --log-dir /var/log/myapp
Restart=on-failure
RestartSec=5s
TimeoutStopSec=30s
LimitNOFILE=65536
# Light hardening that rarely breaks apps:
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
ProtectHome=yes

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemd-analyze verify /etc/systemd/system/myapp.service
sudo systemctl daemon-reload
sudo systemctl enable --now myapp
systemctl status myapp
ss -ltnp | grep 8080
ps -eZ | grep myapp
```

```text
system_u:system_r:unconfined_service_t:s0 5310 ? 00:00:01 myapp
```

`unconfined_service_t` means "a service started by systemd whose binary has no dedicated SELinux policy". It is not restricted by port labels or file types, which is why a custom app on port 8080 usually works without `semanage port`. The confinement you will fight is for **packaged** daemons (httpd, nginx, postgresql, named), which run in their own domains (`httpd_t`, `postgresql_t`) and need correct port types, file types and booleans. That is covered in the web and SELinux modules.

:::tip
`ProtectHome=yes` and `PrivateTmp=yes` mean the service cannot see `/home` and gets its own private `/tmp`. If the app suddenly cannot find a file "in /tmp" that another process wrote, this is why. It is a feature; move the shared file to `/var/lib/myapp`.
:::

### Diagnosing an SELinux denial for a service

```bash
sudo ausearch -m AVC,USER_AVC -ts recent
sudo ausearch -m AVC -ts recent | audit2why
sudo journalctl -t setroubleshoot --since "10 min ago"   # if setroubleshoot-server is installed
sudo sealert -a /var/log/audit/audit.log                  # human-readable advice (setroubleshoot-server)
```

```text
type=AVC msg=audit(1764062047.311:812): avc:  denied  { execute } for  pid=5122 comm="(myapp)"
  name="myapp" dev="dm-0" ino=1837402 scontext=system_u:system_r:init_t:s0
  tcontext=unconfined_u:object_r:user_home_t:s0 tclass=file permissive=0
```

Read it as: **who** (`scontext` ... `init_t`, systemd preparing to exec), **tried what** (`{ execute }`), **on what** (`name="myapp"`, `tcontext` ... `user_home_t`), **result** (`denied`, `permissive=0`). The `comm="(myapp)"` in parentheses is systemd's forked child before exec. A wrong `tcontext` type means fix the **label** (restorecon), not the policy.

:::warn
`audit2allow -M mypol` would happily generate a module allowing `init_t` to execute `user_home_t`. That is the wrong fix: it weakens policy for every file in every home directory. Use audit2allow only when the labels are correct and the app genuinely needs an access the policy lacks, and read the generated `.te` file before loading it. Likewise, `setenforce 0` is acceptable only as a 60-second diagnostic ("does it start in permissive?"), followed immediately by `setenforce 1`.
:::

## 10. Troubleshooting runbook

| Symptom | Check (command) | Likely cause | Fix |
|---|---|---|---|
| `status=203/EXEC`, "Permission denied" | `ls -lZ /opt/myapp/bin/myapp`; `ausearch -m AVC -ts recent` | SELinux label `user_home_t`/`user_tmp_t` from `mv`, or missing `+x` | `restorecon -Rv /opt/myapp` (with `semanage fcontext` rule if needed); `chmod 0755` |
| `status=203/EXEC`, "No such file or directory" | `head -1 file \| cat -A`; `ls -l` path in `ExecStart` | Wrong path, missing interpreter in shebang, CRLF (`^M`) line endings | Fix path; `dnf install` interpreter; `sed -i 's/\r$//' file` |
| `status=203/EXEC`, "Exec format error" | `file /opt/myapp/bin/myapp` | Script without shebang, wrong architecture binary | Add `#!/bin/bash`; correct build |
| `status=200/CHDIR` | `systemctl cat myapp \| grep WorkingDirectory`; `ls -ld` | Directory missing or not traversable by `User=` | Create it, fix permissions |
| `status=217/USER` | `id myapp` | Service account missing | `useradd --system ...` |
| `Result: start-limit-hit`, "Start request repeated too quickly" | `journalctl -u myapp -b`; `systemctl show myapp -p NRestarts` | App crashing at start; auto-restart exhausted the limit | Fix the underlying error, then `systemctl reset-failed myapp` and start |
| Active for 1 s then `inactive (dead)`, exit 0 | `systemctl cat myapp \| grep Type` | Forking daemon configured as `Type=simple` | Foreground flag or `Type=forking` + `PIDFile=` |
| Start hangs, then `Result: timeout` | `systemctl cat myapp \| grep Type` | `Type=notify` app that never sends READY, or slow start | Correct `Type=`; raise `TimeoutStartSec` |
| Edited unit, nothing changes | `systemctl status myapp` (look for "changed on disk") | No `daemon-reload` | `systemctl daemon-reload && systemctl restart myapp` |
| Service not running after reboot | `systemctl is-enabled myapp` | Started but never enabled | `systemctl enable --now myapp` |
| "Unit ... is masked" | `systemctl status myapp` | Masked | `systemctl unmask myapp` (if it should run) |
| App works by hand, fails in service | `systemctl show myapp -p Environment`; `journalctl -u myapp` | Missing env vars, different user, PrivateTmp, ProtectHome | `EnvironmentFile=`, correct `User=`, adjust paths |
| Cron job "doesn't run" | `grep CROND /var/log/cron`; `crontab -l` | Fires but fails: `PATH`, `%`, relative paths, no output | Full paths, `\%`, set `PATH`, redirect `>> log 2>&1` |
| `journalctl -b -1`: "Specifying boot ID or boot offset has no effect, no persistent journal was found" | `ls -d /var/log/journal` | Volatile journal | `Storage=persistent`, restart journald |
| `/var` full, journal big | `journalctl --disk-usage`; `du -sh /var/log/*` | No size limit, chatty service | `journalctl --vacuum-size=`, set `SystemMaxUse=`, fix the chatty service |
| `/var` full, `du` shows little | `lsof +L1` | Deleted log still held open | Restart or truncate via `/proc/PID/fd/N`; add logrotate |
| Logs not rotating in /opt | `logrotate -d`; `ausearch -m AVC -c logrotate` | `usr_t` label, logrotate_t denied | `semanage fcontext -a -t var_log_t ...` + `restorecon`, or move to /var/log |

## 11. Break-fix labs

All labs use a disposable Rocky Linux 9 or AlmaLinux 9 VM (or a RHEL 9 developer-subscription VM) with SELinux **enforcing** (`getenforce` should print `Enforcing`). Run them as a user with sudo. Do not run them on a shared or production server.

Preparation (once):

```bash
sudo dnf install -y policycoreutils-python-utils setroubleshoot-server lsof
getenforce
```

:::lab Lab 02-A — A service that fails with 203/EXEC
**Goal:** reproduce the most common "my perfect unit file doesn't work" failure, diagnose it from systemd and audit logs, and fix it the right way with SELinux labels rather than disabling anything.

**Break it.** Build a tiny "app" in your home directory, then move it into /opt like a developer would.

```bash
mkdir -p ~/build
cat > ~/build/myapp <<'EOF'
#!/bin/bash
echo "myapp starting as $(id -un)"
while true; do echo "myapp heartbeat $(date +%T)"; sleep 30; done
EOF
chmod 0755 ~/build/myapp
sudo mkdir -p /opt/myapp/bin
sudo mv ~/build/myapp /opt/myapp/bin/myapp
sudo useradd --system --no-create-home --shell /sbin/nologin myapp 2>/dev/null || true

sudo tee /etc/systemd/system/myapp.service >/dev/null <<'EOF'
[Unit]
Description=Lab MyApp

[Service]
Type=exec
User=myapp
ExecStart=/opt/myapp/bin/myapp
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl start myapp
```

**Symptoms.**

```text
Job for myapp.service failed because the control process exited with error code.
See "systemctl status myapp.service" and "journalctl -xeu myapp.service" for details.
```

`systemctl status myapp` shows `(code=exited, status=203/EXEC)`. Running `/opt/myapp/bin/myapp` by hand as root works fine, which is what makes this so confusing.

**Diagnose.**

1. Read the journal for the exact error:

   ```bash
   journalctl -u myapp -b --no-pager | tail -5
   ```

   ```text
   myapp.service: Failed to execute /opt/myapp/bin/myapp: Permission denied
   myapp.service: Failed at step EXEC spawning /opt/myapp/bin/myapp: Permission denied
   myapp.service: Main process exited, code=exited, status=203/EXEC
   ```

2. "Permission denied" with mode 0755: check DAC first, then MAC.

   ```bash
   ls -lZ /opt/myapp/bin/myapp
   ```

   ```text
   -rwxr-xr-x. 1 admin admin unconfined_u:object_r:user_home_t:s0 112 Nov 25 10:02 /opt/myapp/bin/myapp
   ```

   Two findings: the file is owned by your user (fix that too), and the type is `user_home_t`.

3. Confirm with the audit log:

   ```bash
   sudo ausearch -m AVC -ts recent | audit2why
   ```

   You see `denied { execute }`, `scontext=system_u:system_r:init_t:s0`, `tcontext=...user_home_t...`, and audit2why explains it is missing a type-enforcement allow rule. The correct reading: the **label is wrong**, not the policy.

4. Optional sanity check: what *should* the label be?

   ```bash
   matchpathcon /opt/myapp/bin/myapp
   ```

   ```text
   /opt/myapp/bin/myapp	system_u:object_r:bin_t:s0
   ```

   (If your path does not match any rule, `matchpathcon` shows `usr_t`, which also works for execution.)

**Fix.**

```bash
sudo semanage fcontext -a -t bin_t "/opt/myapp/bin(/.*)?"   # documents intent; harmless if already covered
sudo restorecon -Rv /opt/myapp
sudo chown root:root /opt/myapp/bin/myapp
sudo systemctl reset-failed myapp
sudo systemctl start myapp
```

**Verify.**

```bash
systemctl status myapp --no-pager
ps -eZ | grep myapp
journalctl -u myapp -n 3 --no-pager
```

The service is `active (running)`, runs as `unconfined_service_t`, and logs "myapp starting as myapp".

**Variations to try** (revert each after): `sudo chmod -x /opt/myapp/bin/myapp` (203/EXEC, Permission denied, no AVC); `sudo sed -i 's/$/\r/' /opt/myapp/bin/myapp` (CRLF: "No such file or directory" because the kernel looks for `/bin/bash\r`); change `ExecStart` to a non-existent path (with `Type=exec` the start fails immediately; with `Type=simple` it briefly "starts").

**Clean-up.** Keep `/opt/myapp` and the user for Lab 02-B, or remove everything:

```bash
sudo systemctl disable --now myapp
sudo rm -f /etc/systemd/system/myapp.service && sudo systemctl daemon-reload
sudo semanage fcontext -d "/opt/myapp/bin(/.*)?"
sudo rm -rf /opt/myapp && sudo userdel myapp
```
:::

:::lab Lab 02-B — A service that restarts in a loop until start-limit-hit
**Goal:** watch auto-restart turn a configuration error into a crash loop, recognise `start-limit-hit`, and recover properly with `reset-failed`.

**Break it.** Make the app require a config file that does not exist.

```bash
sudo tee /opt/myapp/bin/myapp >/dev/null <<'EOF'
#!/bin/bash
CONF=/etc/myapp/myapp.conf
if [ ! -r "$CONF" ]; then echo "FATAL: cannot read $CONF" >&2; exit 1; fi
echo "myapp started with $(wc -l < "$CONF") config lines"
while true; do sleep 30; done
EOF
sudo chmod 0755 /opt/myapp/bin/myapp && sudo restorecon -v /opt/myapp/bin/myapp

sudo mkdir -p /etc/systemd/system/myapp.service.d
sudo tee /etc/systemd/system/myapp.service.d/override.conf >/dev/null <<'EOF'
[Service]
Restart=always
RestartSec=1s
EOF
sudo systemctl daemon-reload
sudo systemctl restart myapp
```

**Symptoms.** Within a few seconds:

```bash
systemctl status myapp --no-pager
```

```text
× myapp.service - Lab MyApp
     Loaded: loaded (/etc/systemd/system/myapp.service; disabled; preset: disabled)
    Drop-In: /etc/systemd/system/myapp.service.d
             └─override.conf
     Active: failed (Result: start-limit-hit) since ...
...
myapp.service: Scheduled restart job, restart counter is at 5.
myapp.service: Start request repeated too quickly.
myapp.service: Failed with result 'start-limit-hit'.
```

Monitoring would have seen the port flap for a few seconds, then go dark. Running `systemctl start myapp` now may fail immediately until the rate-limit window passes, which is how people end up "rebooting to fix it".

**Diagnose.**

1. Do not stare at the last lines; scroll to the **first** failure in this boot:

   ```bash
   journalctl -u myapp -b --no-pager | grep -m3 -E 'FATAL|exited'
   systemctl show myapp -p NRestarts -p Result -p ExecMainStatus
   ```

   ```text
   myapp[6011]: FATAL: cannot read /etc/myapp/myapp.conf
   myapp.service: Main process exited, code=exited, status=1/FAILURE
   ```

2. Note the arithmetic: `RestartSec=1s` × 5 attempts fits easily in the default `StartLimitIntervalSec=10s` with `StartLimitBurst=5`, so systemd gives up after about 5 seconds.

3. Check the drop-in actually applied: `systemctl cat myapp`.

**Fix.** Fix the cause first, then clear the failed state, then start.

```bash
sudo mkdir -p /etc/myapp
echo "listen=0.0.0.0:8080" | sudo tee /etc/myapp/myapp.conf
sudo systemctl reset-failed myapp
sudo systemctl start myapp
```

Then make the restart policy sane for production (replace the lab override):

```bash
sudo tee /etc/systemd/system/myapp.service.d/override.conf >/dev/null <<'EOF'
[Unit]
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Restart=on-failure
RestartSec=10s
EOF
sudo systemctl daemon-reload
sudo systemctl restart myapp
```

**Verify.**

```bash
systemctl is-active myapp
systemctl show myapp -p NRestarts
journalctl -u myapp -n 2 --no-pager
```

`active`, `NRestarts=0` after the restart, and "myapp started with 1 config lines".

**Clean-up.** `sudo rm -rf /etc/systemd/system/myapp.service.d /etc/myapp`, `sudo systemctl daemon-reload`, then the Lab 02-A clean-up commands if you are finished with myapp.
:::

:::lab Lab 02-C — A cron job that works by hand but not in cron
**Goal:** see the cron environment differences bite, find the evidence, and fix the job (and then convert it to a timer).

**Break it.** Create a report script that relies on your interactive `PATH`, and a crontab line with an unescaped `%`.

```bash
sudo mkdir -p /opt/myapp/bin /var/log/myapp
sudo tee /opt/myapp/bin/port-report.sh >/dev/null <<'EOF'
#!/bin/bash
echo "=== port report ==="
ss -ltn | wc -l
EOF
sudo chmod 0755 /opt/myapp/bin/port-report.sh
sudo restorecon -Rv /opt/myapp

sudo /opt/myapp/bin/port-report.sh       # works by hand
```

Note: on RHEL 9 `ss` lives in `/usr/sbin`, and the root user's interactive `PATH` includes it. Now install a root crontab entry (append, don't overwrite existing entries):

```bash
( sudo crontab -l 2>/dev/null; echo '* * * * * /opt/myapp/bin/port-report.sh > /var/log/myapp/report-$(date +%F).log 2>&1' ) | sudo crontab -
```

**Symptoms.** Wait two minutes. `ls /var/log/myapp/` shows no `report-2026-...log` file; nothing appears to happen.

**Diagnose.**

1. Did cron fire the job at all?

   ```bash
   sudo grep CROND /var/log/cron | tail -3
   ```

   ```text
   Nov 25 10:31:01 lab01 CROND[7120]: (root) CMD (/opt/myapp/bin/port-report.sh > /var/log/myapp/report-$(date +)
   ```

   The logged command is **cut off at the `%`**. Everything after it (`F).log 2>&1`) became stdin. The shell sees an unterminated `$(date +` and fails. Cron may try to mail the error to root: check `sudo ls /var/spool/mail/root` or `journalctl -u crond`.

2. Fix only the `%` (escape it) and look again:

   ```bash
   sudo crontab -l | sed 's/%F/\\%F/' | sudo crontab -
   sudo crontab -l
   ```

   After a minute a log file appears. Read it:

   ```bash
   cat /var/log/myapp/report-*.log
   ```

   ```text
   === port report ===
   /opt/myapp/bin/port-report.sh: line 3: ss: command not found
   0
   ```

   The second bug: cron's `PATH` is `/usr/bin:/bin`, and `ss` is in `/usr/sbin`.

3. Reproduce cron's environment by hand to prove it:

   ```bash
   sudo env -i HOME=/root PATH=/usr/bin:/bin SHELL=/bin/sh /opt/myapp/bin/port-report.sh
   ```

**Fix.** Make the script self-sufficient and the crontab explicit.

```bash
sudo tee /opt/myapp/bin/port-report.sh >/dev/null <<'EOF'
#!/bin/bash
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/usr/local/sbin:/usr/sbin
echo "=== port report $(date +%F' '%T) ==="
ss -ltn | wc -l
EOF
sudo crontab -l | grep -v port-report | sudo crontab -
echo '*/5 * * * * /opt/myapp/bin/port-report.sh >> /var/log/myapp/port-report.log 2>&1' | \
  sudo tee /etc/cron.d/myapp-port-report >/dev/null
```

The `%` inside the **script** is fine; only the crontab line treats `%` specially. Files in `/etc/cron.d` need the user field; this version doesn't have one, so add it:

```bash
sudo sed -i 's|\* \* \* \* /opt|* * * * root /opt|' /etc/cron.d/myapp-port-report
cat /etc/cron.d/myapp-port-report
```

```text
*/5 * * * root /opt/myapp/bin/port-report.sh >> /var/log/myapp/port-report.log 2>&1
```

Hmm, check that carefully: the first field must still be `*/5`, then hour, day, month, weekday, user. Correct the file by hand if needed so it reads exactly:

```text
*/5 * * * * root /opt/myapp/bin/port-report.sh >> /var/log/myapp/port-report.log 2>&1
```

**Verify.** Within five minutes `tail /var/log/myapp/port-report.log` shows a dated header and a number, and `grep CROND /var/log/cron | tail -1` shows the full command.

**Stretch: convert it to a timer.**

```bash
sudo tee /etc/systemd/system/port-report.service >/dev/null <<'EOF'
[Unit]
Description=Port report
[Service]
Type=oneshot
ExecStart=/opt/myapp/bin/port-report.sh
EOF
sudo tee /etc/systemd/system/port-report.timer >/dev/null <<'EOF'
[Unit]
Description=Port report every 5 minutes
[Timer]
OnCalendar=*:0/5
Persistent=true
[Install]
WantedBy=timers.target
EOF
sudo rm -f /etc/cron.d/myapp-port-report
sudo systemctl daemon-reload && sudo systemctl enable --now port-report.timer
systemctl list-timers port-report.timer
journalctl -u port-report.service -n 5 --no-pager
```

Output now goes to the journal with exit status, without any redirection.

**Clean-up.**

```bash
sudo systemctl disable --now port-report.timer
sudo rm -f /etc/systemd/system/port-report.{service,timer} /etc/cron.d/myapp-port-report
sudo systemctl daemon-reload
sudo rm -rf /var/log/myapp /opt/myapp/bin/port-report.sh
```
:::

:::lab Lab 02-D — The journal is filling the disk
**Goal:** simulate a chatty service that grows the journal, measure it, reclaim space safely, and put permanent limits in place.

**Break it.** First make sure the journal is persistent (so the lab reflects a real server), then start a noisy service with the per-service rate limit relaxed (as some teams do "to not lose logs").

```bash
sudo mkdir -p /var/log/journal
sudo systemctl restart systemd-journald
journalctl --disk-usage

sudo tee /etc/systemd/system/noisy.service >/dev/null <<'EOF'
[Unit]
Description=Lab noisy logger

[Service]
Type=simple
ExecStart=/bin/bash -c 'while true; do head -c 600 /dev/urandom | base64 -w0; echo; done'
LogRateLimitIntervalSec=0
EOF
sudo systemctl daemon-reload
sudo systemctl start noisy
```

`LogRateLimitIntervalSec=0` disables journald's per-service rate limit for this unit (systemd 240+). Let it run for **two to three minutes only**, watching the disk:

```bash
watch -n 10 'journalctl --disk-usage; df -h /var'
```

Then stop the noise: `sudo systemctl stop noisy`.

**Symptoms.** `journalctl --disk-usage` grows by hundreds of megabytes per minute, `df -h /var` drops, and in a real incident other services start failing with "No space left on device".

**Diagnose.**

1. Where is the space going?

   ```bash
   sudo du -sh /var/log/* 2>/dev/null | sort -h | tail -5
   journalctl --disk-usage
   ```

2. Which unit is producing it? Count lines per unit over the last few minutes:

   ```bash
   journalctl --since "10 min ago" -o json --output-fields=_SYSTEMD_UNIT | \
     jq -r '._SYSTEMD_UNIT // "kernel/other"' | sort | uniq -c | sort -rn | head
   ```

   (If `jq` is not installed: `sudo dnf install -y jq`, or use `journalctl --since "10 min ago" -o short | awk '{print $5}' | sort | uniq -c | sort -rn | head`.)

3. What limits apply right now?

   ```bash
   systemd-analyze cat-config systemd/journald.conf | grep -v '^#' | grep .
   ```

   With only defaults, nothing explicit is set and `SystemMaxUse` is 10% of the filesystem.

**Fix.** Two parts: reclaim space now, and cap it permanently.

```bash
# Now:
sudo journalctl --rotate
sudo journalctl --vacuum-size=200M
journalctl --disk-usage

# Permanently:
sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/10-limits.conf >/dev/null <<'EOF'
[Journal]
SystemMaxUse=500M
SystemKeepFree=1G
MaxRetentionSec=1month
EOF
sudo systemctl restart systemd-journald
```

And fix the real cause: in production, the noisy service is the bug. Remove the `LogRateLimitIntervalSec=0`, lower the app's log level, or ask the developers why it logs on every loop.

Also check rsyslog: the same noise was copied into `/var/log/messages`. `ls -lh /var/log/messages`; if it is large, `sudo logrotate -f /etc/logrotate.conf` rotates it, and compressed old copies can be removed once you have confirmed retention requirements.

**Verify.**

```bash
journalctl --disk-usage
systemd-analyze cat-config systemd/journald.conf | grep -E 'SystemMaxUse|SystemKeepFree'
df -h /var
```

Usage is at or under the vacuum target, and the new limits are in effect for future growth.

**Clean-up.**

```bash
sudo systemctl stop noisy
sudo rm -f /etc/systemd/system/noisy.service && sudo systemctl daemon-reload
# Keep the journald limits; they are good practice. To remove: sudo rm /etc/systemd/journald.conf.d/10-limits.conf
```
:::

## Command cheat sheet

| Task | Command |
|---|---|
| Failed units | `systemctl --failed` |
| Status with more log lines | `systemctl status myapp -n 50 -l --no-pager` |
| Show every file that makes up a unit | `systemctl cat myapp` |
| Show effective property values | `systemctl show myapp -p Type -p User -p Restart -p NRestarts` |
| Override part of a unit | `systemctl edit myapp` |
| Undo all local overrides | `systemctl revert myapp` |
| Reload unit files from disk | `systemctl daemon-reload` |
| Start now and at boot | `systemctl enable --now myapp` |
| Prevent any start | `systemctl mask myapp` / `unmask` |
| Clear a failed / rate-limited state | `systemctl reset-failed myapp` |
| Lint a unit file | `systemd-analyze verify /etc/systemd/system/myapp.service` |
| Boot time by unit | `systemd-analyze blame` |
| Default target | `systemctl get-default` / `set-default multi-user.target` |
| Logs for a unit, this boot | `journalctl -u myapp -b` |
| Follow logs live | `journalctl -u myapp -f` |
| Errors and worse this boot | `journalctl -p err -b` |
| Time window | `journalctl --since "30 min ago" --until "10 min ago"` |
| Previous boot / list boots | `journalctl -b -1` / `journalctl --list-boots` |
| Kernel messages | `journalctl -k` |
| All fields of an entry | `journalctl -u myapp -n 1 -o verbose` |
| Journal size / shrink | `journalctl --disk-usage` / `journalctl --rotate --vacuum-size=500M` |
| Effective journald config | `systemd-analyze cat-config systemd/journald.conf` |
| Test a logrotate rule | `logrotate -d /etc/logrotate.d/myapp` |
| Force rotation | `logrotate -vf /etc/logrotate.d/myapp` |
| Deleted but open files | `lsof +L1` |
| Send a test syslog message | `logger -p user.err "test"` |
| List timers | `systemctl list-timers --all` |
| Check a calendar expression | `systemd-analyze calendar "Mon..Fri *-*-* 02:30"` |
| Edit / list crontab | `crontab -e` / `crontab -l -u myapp` |
| Did cron run it? | `grep CROND /var/log/cron` (RHEL) / `grep CRON /var/log/syslog` (Ubuntu) |
| Run like cron does | `env -i HOME=/root PATH=/usr/bin:/bin /path/script.sh` |
| File label / expected label | `ls -Z file` / `matchpathcon file` |
| Add permanent label rule + apply | `semanage fcontext -a -t bin_t "/opt/myapp/bin(/.*)?"` then `restorecon -Rv /opt/myapp` |
| Recent SELinux denials explained | `ausearch -m AVC -ts recent \| audit2why` |
| Process SELinux domain | `ps -eZ \| grep myapp` |

:::summary
- systemd manages **units**; vendor files live in `/usr/lib/systemd/system`, yours in `/etc/systemd/system`, which wins. Change vendor units with **drop-ins** (`systemctl edit`), and run **`daemon-reload`** after any manual edit.
- **After=** orders; **Wants=/Requires=** pull in. You usually need both. Use `network-online.target` when the app needs a working network at start.
- Pick the right **Type=**: `simple`/`exec` for foreground apps, `forking` with `PIDFile=` for classic daemons, `notify` only if the app calls sd_notify, `oneshot` for scripts and timers.
- Services get a **clean environment**: use `Environment=`/`EnvironmentFile=`, absolute paths, and the right `User=` and `WorkingDirectory=`.
- **enable** is for boot, **start** is for now; `enable --now` does both. **Masked** units cannot start at all.
- In `systemctl status`, a status in the **200s** (203/EXEC, 200/CHDIR, 217/USER) means systemd failed before your code ran. `start-limit-hit` means auto-restart gave up: fix the cause, then **`reset-failed`**.
- `journalctl -u`, `-b`, `-p err`, `--since`, `-f`, `-o verbose` answer most questions. Make the journal **persistent** and set **SystemMaxUse**; use `--vacuum-*` to reclaim space.
- rsyslog writes `/var/log/messages`, `secure` and `cron` on RHEL; **logrotate** keeps text logs bounded; deleted-but-open files show in `lsof +L1`.
- Cron runs with `PATH=/usr/bin:/bin`, `/bin/sh`, no profile and no TTY, and treats **`%` as newline**. Timers log to the journal, catch up with `Persistent=true` and show failures in `systemctl --failed`.
- For apps in **/opt**, SELinux labels matter: `mv` keeps `user_home_t`, which causes **203/EXEC**. Fix with `semanage fcontext` + **`restorecon`**, never by disabling SELinux or with a blanket audit2allow module.
:::

## Self-check questions

<div class="q" markdown="1">

**Q1.** A developer moved a freshly built binary from their home directory into `/opt/myapp/bin/` with `sudo mv`. The unit file is correct, the file is `0755`, and running it by hand as root works. `systemctl start myapp` fails with `status=203/EXEC` and "Permission denied". What do you check first?

A. Whether the binary is linked against the right glibc version
B. `ls -Z` on the binary and recent AVC denials with `ausearch -m AVC -ts recent`
C. Whether `multi-user.target` is the default target
D. Whether the journal is persistent

:::answer
**Answer: B.** `mv` preserves the original SELinux label (`user_home_t`), and `init_t` is not allowed to execute it, giving 203/EXEC with Permission denied even with correct mode bits. Fix with `restorecon -Rv /opt/myapp` (plus a `semanage fcontext` rule if the path is not covered).

- A is wrong: a library problem would let exec succeed and then fail with an app error, or show "error while loading shared libraries".
- C is wrong: the default target affects boot, not a manual start.
- D is wrong: persistence affects log history, not whether a binary can execute.
:::
</div>

<div class="q" markdown="1">

**Q2.** You change `RestartSec=` in `/etc/systemd/system/myapp.service` with `vi` and run `systemctl restart myapp`. `systemctl show myapp -p RestartUSec` still shows the old value. Why?

A. RestartSec can only be set in a drop-in
B. You did not run `systemctl daemon-reload` after editing the file
C. The service must be disabled and re-enabled
D. RestartSec only applies after a reboot

:::answer
**Answer: B.** systemd keeps parsed units in memory; edits on disk are ignored until `daemon-reload`. `systemctl status` also warns "changed on disk".

- A is wrong: any directive can go in the main unit or in a drop-in.
- C is wrong: enable/disable only manage the boot symlinks.
- D is wrong: a reload plus restart applies it immediately.
:::
</div>

<div class="q" markdown="1">

**Q3.** A service crashes on start because of a bad config. After you fix the config, `systemctl start myapp` still fails and status shows `Result: start-limit-hit`. What is the correct next step?

A. Reboot the server
B. Set `Restart=no` permanently
C. Run `systemctl reset-failed myapp`, then start it
D. Run `systemctl unmask myapp`

:::answer
**Answer: C.** The unit exceeded `StartLimitBurst` within `StartLimitIntervalSec`. `reset-failed` clears the failed state and the start-rate counter so the next start is allowed (alternatively, wait out the interval).

- A is wrong: a reboot works, but it is an unnecessary outage for every other service.
- B is wrong: it removes useful self-healing and does not clear the current state.
- D is wrong: the unit is not masked; status would say `Loaded: masked`.
:::
</div>

<div class="q" markdown="1">

**Q4.** A unit has `Requires=postgresql.service` but no `After=`. On boot the app sometimes logs "connection refused" to the database and exits. What is wrong?

A. `Requires=` should be `Wants=`
B. Requires pulls PostgreSQL in but does not order it, so both start in parallel; add `After=postgresql.service`
C. PostgreSQL must be masked
D. The app needs `Type=oneshot`

:::answer
**Answer: B.** Dependency and ordering are separate in systemd. Without `After=`, both units start at the same time and the app races the database.

- A is wrong: Wants is weaker and also does not order.
- C is wrong: masking would stop PostgreSQL from starting at all.
- D is wrong: oneshot is for tasks that exit, not long-running servers.
:::
</div>

<div class="q" markdown="1">

**Q5.** A legacy daemon's unit uses `Type=simple`. `systemctl start` succeeds, but two seconds later the unit is `inactive (dead)` with `status=0/SUCCESS` and the daemon is gone. What is the most likely cause?

A. SELinux denied the daemon network access
B. The daemon forks into the background; systemd sees the main process exit and cleans up the cgroup
C. The journal is full
D. The unit is not enabled

:::answer
**Answer: B.** With `Type=simple` the first process is the main process. When a classic daemon forks and the parent exits 0, systemd considers the service finished and stops the rest. Run it in the foreground or use `Type=forking` with `PIDFile=`.

- A is wrong: an SELinux denial would produce an error and usually a non-zero exit, and AVCs in the audit log.
- C is wrong: a full journal does not stop services.
- D is wrong: enablement only matters at boot.
:::
</div>

<div class="q" markdown="1">

**Q6.** After a crash and automatic reboot, you run `journalctl -b -1` and get "Specifying boot ID or boot offset has no effect, no persistent journal was found." What does this tell you and what do you change?

A. The kernel did not log anything; enable kdump
B. The journal is volatile (stored in /run); create `/var/log/journal` or set `Storage=persistent` and restart journald
C. journalctl needs root; use sudo
D. The previous boot was too long ago; increase `MaxRetentionSec`

:::answer
**Answer: B.** Without `/var/log/journal` (and with the default `Storage=auto`) the journal lives in tmpfs and is lost at reboot. Make it persistent so the next incident can be investigated; meanwhile check `/var/log/messages` from rsyslog.

- A is wrong: kdump is useful for kernel panics but does not fix journal persistence.
- C is wrong: the message is about storage, not permissions.
- D is wrong: no previous boot exists in the journal at all.
:::
</div>

<div class="q" markdown="1">

**Q7.** A script runs fine from your SSH session, but in cron it logs `semanage: command not found`. What is the cause?

A. cron runs jobs with SELinux disabled
B. cron's default `PATH` is `/usr/bin:/bin`, which does not include `/usr/sbin`
C. cron cannot run scripts as root
D. The script needs `#!/bin/sh` instead of `#!/bin/bash`

:::answer
**Answer: B.** User crontabs get a minimal `PATH`. Use full paths, or set `PATH` at the top of the crontab or inside the script.

- A is wrong: cron jobs run under SELinux as normal.
- C is wrong: root's crontab runs as root.
- D is wrong: the shebang does not change the PATH lookup for `semanage`.
:::
</div>

<div class="q" markdown="1">

**Q8.** This crontab line never creates its output file: `0 1 * * * /usr/bin/tar czf /backup/etc-$(date +%F).tgz /etc`. Why?

A. tar cannot write to /backup from cron
B. In a crontab, `%` means newline; the command is cut at the first `%`. Escape it as `\%`
C. `$(...)` is not allowed in cron
D. cron does not support the hour field value 1

:::answer
**Answer: B.** Unescaped `%` ends the command and the rest becomes stdin, so the shell sees an incomplete command substitution. `/var/log/cron` shows the truncated command.

- A is wrong: permissions are not the issue in the example.
- C is wrong: command substitution works because cron runs the line through `/bin/sh`.
- D is wrong: 1 is a valid hour.
:::
</div>

<div class="q" markdown="1">

**Q9.** `/var` is 100% full, but `du -sh /var/*` accounts for only 30% of it. What do you run next?

A. `journalctl --vacuum-size=100M`
B. `lsof +L1` to find deleted files still held open by a process
C. `restorecon -Rv /var`
D. `systemctl daemon-reload`

:::answer
**Answer: B.** A large log deleted with `rm` while a process still has it open keeps using space but is invisible to `du`. `lsof +L1` shows it; restart the process or truncate via `/proc/PID/fd/N`.

- A is wrong: it may free some space, but the discrepancy between `df` and `du` points to open deleted files, not the journal (which `du` would see).
- C is wrong: labels do not consume space.
- D is wrong: unrelated to disk usage.
:::
</div>

<div class="q" markdown="1">

**Q10.** To permanently change only the `ExecStart` of a vendor unit `foo.service`, which is the correct approach?

A. Edit `/usr/lib/systemd/system/foo.service` directly
B. `systemctl edit foo` and add `ExecStart=` (empty) followed by the new `ExecStart=` line
C. `systemctl edit foo` and add only the new `ExecStart=` line
D. `chcon` the binary

:::answer
**Answer: B.** A drop-in in `/etc` survives package updates. `ExecStart` is a list setting, so you must clear it with an empty assignment before setting the new value.

- A is wrong: the next package update overwrites your change.
- C is wrong: for non-oneshot services, two ExecStart lines make the unit fail to load.
- D is wrong: SELinux labels are unrelated to which command runs.
:::
</div>

<div class="q" markdown="1">

**Q11.** logrotate rotates everything in `/var/log` but never rotates `/opt/myapp/logs/app.log`. `logrotate -d` shows the rule is parsed. `ausearch -m AVC -c logrotate` shows denials on `usr_t` files. What is the best fix?

A. `setenforce 0` in the logrotate service
B. `audit2allow -a -M logrotfix && semodule -i logrotfix.pp`
C. `semanage fcontext -a -t var_log_t "/opt/myapp/logs(/.*)?"` then `restorecon -Rv /opt/myapp/logs` (or move logs to /var/log/myapp)
D. `chmod 777 /opt/myapp/logs`

:::answer
**Answer: C.** The files carry the wrong type for log files. Giving them `var_log_t` with a persistent rule lets logrotate's domain manage them as intended.

- A is wrong: never disable SELinux as a fix.
- B is wrong: it broadens logrotate's access to all `usr_t` files instead of fixing the label.
- D is wrong: DAC permissions are not the problem, and 777 creates a new security issue.
:::
</div>

<div class="q" markdown="1">

**Q12.** A nightly job must run even if the server was powered off at its scheduled time, its output must be in the journal, and a failure must show in `systemctl --failed`. What do you implement?

A. A root crontab entry with `MAILTO`
B. A `Type=oneshot` service plus a `.timer` with `OnCalendar=` and `Persistent=true`
C. An `@reboot` cron job
D. A `Type=simple` service with `Restart=always`

:::answer
**Answer: B.** A timer with `Persistent=true` catches up on missed runs at boot; the oneshot service logs to the journal and enters `failed` on a non-zero exit.

- A is wrong: cron skips missed runs and does not integrate with the journal or `--failed`.
- C is wrong: it runs only at boot, not nightly.
- D is wrong: that is a long-running service, and `always` would rerun the job in a loop.
:::
</div>
