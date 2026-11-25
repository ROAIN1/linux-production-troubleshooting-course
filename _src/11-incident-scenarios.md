<div class="cover" markdown="1">
<div class="mod">Module 11 · Incident Drills & Final Assessment</div>

# Incident Drills & Final Assessment: Thirty On-Call Scenarios and a Forty-Question Exam

<p>The on-call method that ties the whole course together: a five-minute triage checklist, how to communicate during an incident, templates for incident notes and postmortems, thirty realistic drills with hidden root causes, a forty-question final assessment, and a 30-day practice plan.</p>
</div>

## What you'll learn

- A repeatable **on-call method**: what to do in the first 60 seconds, the first 5 minutes and the first 30 minutes of an incident
- The **five-minute triage checklist** (`uptime`, `dmesg -T`, `journalctl -p err -b`, `df -h`/`df -i`, `free -h`, `top`, `ss -tulpn`, `systemctl --failed`, `getenforce` and `ausearch -m AVC -ts recent`) and how to read each result in seconds
- How to **communicate** during an incident: severity levels, status updates, and who needs to know what
- Ready-to-copy templates for **incident notes** (the live timeline) and a **blameless postmortem**
- **Thirty incident drills** covering every module of the course, including nine SELinux scenarios, each with the root cause, fix and prevention hidden in an answer box so you can test yourself first
- A **forty-question final assessment** in scenario style, with explanations and a pointer to the module to revise
- A **30-day practice plan** to turn this knowledge into reflexes

**Why it matters in production:** the commands in Modules 01–10 only help if you reach for the right one under pressure, at 03:00, with a manager asking for an ETA. Incidents are rarely hard because the fix is exotic; they are hard because the engineer skipped a check, chased the first hypothesis, changed three things at once, or "fixed" SELinux by turning it off. This module drills the method until it becomes habit.

## 1. How to use this module

This module is different from the others. There are no new tools here. Instead it is a **practice arena** for everything you have already learned:

| Part | What it is | How to use it |
|---|---|---|
| **A. On-call method** (Sections 2–5) | Triage checklist, communication, incident notes, postmortem | Read once carefully. Print the checklist and the templates. Use them in every drill. |
| **B. Incident drills** (Section 6) | 30 scenarios: alert, questions, commands, hidden root cause | Read the alert, **stop**, write down your first three hypotheses and the commands you would run, then open the answer. Where possible, reproduce the drill on your lab VM. |
| **C. Final assessment** (Section 7) | 40 multiple-choice scenario questions | Do it in one sitting, without notes, in 60 minutes. Score yourself. Revise the modules listed next to every question you missed. |
| **D. 30-day plan** (Section 8) | Daily practice schedule | Follow it after finishing the course, or alongside your first on-call rotation. |

:::tip
The drills are most valuable when you **do not** look at the answer box until you have committed to a diagnosis. Cover it with your hand or scroll past it. Being wrong in a drill is cheap; being wrong at 03:00 is not.
:::

## 2. Part A: the on-call method

### 2.1 The first 60 seconds: acknowledge, scope, protect

Before you type a single command on the server, do three things.

1. **Acknowledge the alert** in your paging tool so nobody else starts working on it in parallel without knowing, and so the escalation timer stops.
2. **Scope it.** One server or many? One customer or all? Since when? A single question to the reporter ("what exactly do you see, and since what time?") saves more time than any command.
3. **Protect evidence and people.** Do not reboot, do not restart services, do not delete logs yet. A reboot destroys the most valuable evidence you have: the current process table, the open file handles, the kernel ring buffer and the network state. Restart only when the outage cost is clearly bigger than the diagnosis value, and even then capture the triage output first.

:::concept
**Mitigate first, root-cause second.** Your first job on call is to restore service; your second job is to understand why it broke. Those are different activities. Failing over, rolling back a deploy, or restarting a hung service are legitimate mitigations, as long as you capture evidence first and write down exactly what you did. The postmortem is where the deep "why" gets answered.
:::

:::story
An on-call engineer is like a paramedic arriving at an accident. They don't start surgery on the roadside. They check breathing, bleeding and consciousness in a fixed order (the ABC checklist), stabilise the patient, and hand over to the hospital with notes. The five-minute triage checklist is your ABC.
:::

### 2.2 The five-minute triage checklist

Run these commands in this order on any Linux server that is "broken" in a way you don't yet understand. Each one answers one question. Together they take about five minutes and they cover the causes behind most production incidents: resources, crashes, failed services, network listeners and SELinux denials.

| # | Command | Question it answers | Bad signs |
|---|---|---|---|
| 1 | `uptime` | Did it reboot? How loaded is it? | Uptime of minutes (unexpected reboot); load average far above the CPU count |
| 2 | `dmesg -T \| tail -50` | Did the kernel complain? | OOM killer, I/O errors, `EXT4-fs error`, `XFS ... Corruption`, NIC link down, `nfs: server ... not responding`, hung task warnings |
| 3 | `journalctl -p err -b` | What errors were logged since boot? | Repeated service failures, `Permission denied`, segfaults, authentication errors |
| 4 | `df -h` and `df -i` | Is any filesystem full, in bytes or in inodes? | 100% use (or 95%+ and rising), especially `/`, `/var`, `/tmp`, `/boot` |
| 5 | `free -h` | Is memory exhausted? Is swap in heavy use? | `available` near zero; swap heavily used and growing |
| 6 | `top` (or `top -b -n 1 \| head -20`) | Which process is eating CPU or memory? Is CPU waiting on I/O? | One process at 100% per core; high `wa`; high `st` on a VM; many processes in state `D` |
| 7 | `ss -tulpn` | Is the service listening, on the right address and port? | Expected port missing; listening on `127.0.0.1` instead of `0.0.0.0`; wrong process on the port |
| 8 | `systemctl --failed` | Which units have failed? | Any entry at all on a production server |
| 9 | `getenforce` then `ausearch -m AVC -ts recent` | Is SELinux enforcing, and did it deny anything in the last 10 minutes? | `Permissive` or `Disabled` on a server that should be enforcing; any `avc: denied` lines matching the failing service |

Let's walk through what good and bad output looks like.

**1. `uptime`**

```bash
uptime
```

```text
 03:12:44 up 41 days,  6:02,  2 users,  load average: 23.81, 22.94, 18.10
```

Read the three load averages as 1, 5 and 15 minutes. Compare them to the CPU count (`nproc`). A load of 23 on a 4-CPU box means work is queuing. Rising numbers (18 → 22 → 23) mean it is getting worse. Remember from Module 05 that Linux load includes processes in uninterruptible sleep (`D` state, usually waiting on disk or NFS), so high load with idle CPUs points at I/O, not CPU.

**2. `dmesg -T | tail -50`**

```bash
dmesg -T | tail -50
```

```text
[Tue Nov 25 02:58:10 2026] Out of memory: Killed process 2214 (java) total-vm:9124552kB, anon-rss:6021340kB, file-rss:0kB, shmem-rss:0kB, UID:1001 pgtables:12840kB oom_score_adj:0
[Tue Nov 25 03:01:33 2026] nfs: server nfs01.example.com not responding, still trying
```

`-T` prints human-readable timestamps (they can drift after suspend/resume on VMs, so treat them as approximate). On RHEL 9 a normal user may be blocked from `dmesg` (`kernel.dmesg_restrict = 1`), so run it with `sudo` or use `journalctl -k`.

**3. `journalctl -p err -b`**

```bash
journalctl -p err -b --no-pager | tail -40
```

```text
Nov 25 02:58:11 app01 systemd[1]: myapp.service: Main process exited, code=killed, status=9/KILL
Nov 25 02:58:11 app01 systemd[1]: myapp.service: Failed with result 'oom-kill'.
```

`-p err` shows priority `err` and worse (`crit`, `alert`, `emerg`). `-b` limits to the current boot; use `-b -1` for the previous boot when the server has just rebooted unexpectedly (requires a persistent journal, see Module 02).

**4. `df -h` and `df -i`**

```bash
df -hT -x tmpfs -x devtmpfs
df -i -x tmpfs -x devtmpfs
```

```text
Filesystem            Type  Size  Used Avail Use% Mounted on
/dev/mapper/rl-root   xfs    20G   20G   20K 100% /
/dev/mapper/rl-var    xfs    30G   12G   18G  40% /var

Filesystem            Inodes  IUsed   IFree IUse% Mounted on
/dev/mapper/rl-var   15M     15M       0  100% /var
```

Always check both. A filesystem can be full in **bytes** or in **inodes**, and "No space left on device" means either.

**5. `free -h`**

```bash
free -h
```

```text
               total        used        free      shared  buff/cache   available
Mem:            15Gi        14Gi       210Mi       120Mi       700Mi       380Mi
Swap:          4.0Gi       3.9Gi       100Mi
```

Look at **`available`**, not `free`. Here only 380 MiB is available and swap is nearly full: the OOM killer is close, if it has not already fired.

**6. `top`**

```bash
top -b -n 1 -o %CPU | head -20
```

```text
top - 03:13:02 up 41 days,  6:02,  2 users,  load average: 23.81, 22.94, 18.10
Tasks: 312 total,   2 running, 291 sleeping,   0 stopped,   0 zombie
%Cpu(s):  3.1 us,  1.2 sy,  0.0 ni,  8.4 id, 87.1 wa,  0.0 hi,  0.2 si,  0.0 st
```

The CPU line tells the story: `us` (user code), `sy` (kernel), `id` (idle), `wa` (waiting on I/O), `st` (stolen by the hypervisor). Here 87% `wa`: the CPUs are idle because they are waiting for storage. In interactive `top`, press `M` to sort by memory and `P` for CPU, `1` to show each CPU.

**7. `ss -tulpn`**

```bash
ss -tulpn
```

```text
Netid State  Recv-Q Send-Q Local Address:Port  Peer Address:Port Process
tcp   LISTEN 0      511          0.0.0.0:80         0.0.0.0:*     users:(("nginx",pid=1180,fd=6))
tcp   LISTEN 0      128        127.0.0.1:8080       0.0.0.0:*     users:(("java",pid=2290,fd=41))
```

`-t` TCP, `-u` UDP, `-l` listening, `-p` process (needs root), `-n` numeric. Check that every port you expect is present, bound to the right address, and owned by the right process.

**8. `systemctl --failed`**

```bash
systemctl --failed
```

```text
  UNIT            LOAD   ACTIVE SUB    DESCRIPTION
● myapp.service   loaded failed failed My Application
```

Follow up any failed unit with `systemctl status <unit>` and `journalctl -u <unit> -b -n 50`.

**9. `getenforce` and `ausearch -m AVC -ts recent`**

```bash
getenforce
sudo ausearch -m AVC,USER_AVC -ts recent
```

```text
Enforcing
----
time->Tue Nov 25 03:05:40 2026
type=AVC msg=audit(1795575940.112:842): avc:  denied  { name_connect } for  pid=1180 comm="nginx" dest=8080 scontext=system_u:system_r:httpd_t:s0 tcontext=system_u:object_r:http_cache_port_t:s0 tclass=tcp_socket permissive=0
```

`-ts recent` means the last 10 minutes; use `-ts today`, `-ts boot` or `-ts 03:00` for other windows. `<no matches>` means there were no logged denials in that window. That is useful evidence, but not proof SELinux is innocent: some denials are hidden by `dontaudit` rules (Drill 21). If you see a denial, pipe it into `audit2why` (Module 06).

:::warn
Checking SELinux is part of triage, **disabling it is not**. If you want to test whether SELinux is involved, `setenforce 0` for a single reproduction is acceptable as a short diagnostic on a non-critical path, then immediately `setenforce 1` and fix the real cause with a label, a port type, a boolean or a reviewed policy module. Never leave a server permissive, and never set `SELINUX=disabled` in `/etc/selinux/config` as a "fix". `semanage permissive -a httpd_t` (one domain permissive) is a far smaller diagnostic blast radius than `setenforce 0`.
:::

:::tip
On Ubuntu/Debian the triage list is the same except step 9: check AppArmor with `aa-status` and look for `apparmor="DENIED"` in `journalctl -k`. `ausearch` exists only if `auditd` is installed.
:::

### 2.3 A one-shot triage script

Typing nine commands under pressure invites mistakes. Put them in a script that saves everything to a timestamped file, which also becomes evidence for the postmortem. Deploy it to every server with Ansible.

```bash
sudo tee /usr/local/sbin/triage >/dev/null <<'EOF'
#!/bin/bash
# triage - capture first-5-minutes evidence to a file
out=/var/tmp/triage-$(hostname -s)-$(date +%Y%m%d-%H%M%S).txt
{
  echo "### date";            date; timedatectl | grep -E 'Local time|Time zone|synchronized'
  echo "### uptime";          uptime
  echo "### dmesg";           dmesg -T | tail -50
  echo "### journal errors";  journalctl -p err -b --no-pager | tail -60
  echo "### df -h";           df -hT -x tmpfs -x devtmpfs
  echo "### df -i";           df -i -x tmpfs -x devtmpfs
  echo "### free";            free -h
  echo "### top";             top -b -n 1 | head -25
  echo "### listeners";       ss -tulpn
  echo "### failed units";    systemctl --failed --no-pager
  echo "### selinux";         getenforce; ausearch -m AVC,USER_AVC -ts recent 2>&1 | tail -40
} > "$out" 2>&1
echo "Saved to $out"
EOF
sudo chmod 750 /usr/local/sbin/triage
sudo triage
```

It writes to `/var/tmp`, not `/tmp`, because `/var/tmp` survives a reboot. If `/var` itself is full, run it with output redirected to another filesystem or simply run the commands by hand.

### 2.4 From triage result to the right module

Triage tells you **where** to look. Use this table to jump to the deeper diagnosis you learned earlier in the course.

| Triage finding | Next commands | Revise |
|---|---|---|
| Filesystem 100% (bytes) | `du -xh --max-depth=1 /var \| sort -h`, `lsof +L1` | Module 03 |
| Filesystem 100% (inodes) | `find /var -xdev -type f \| cut -d/ -f2-4 \| sort \| uniq -c \| sort -n \| tail` | Module 03 |
| Failed unit | `systemctl status`, `journalctl -u UNIT -b`, `systemd-analyze verify` | Module 02 |
| High load, high `wa` | `vmstat 1 5`, `iostat -xz 1 3`, `pidstat -d 1`, `ps -eo state,pid,cmd \| grep ^D` | Module 05 |
| OOM in `dmesg` | `journalctl -k \| grep -i oom`, `systemd-cgtop`, `ps aux --sort=-rss \| head` | Module 05 |
| Port missing in `ss` | `systemctl status`, app log, `ausearch -m AVC` for `name_bind` | Modules 02, 04, 07 |
| Can't reach remote host | `ping`, `getent hosts`, `dig`, `nc -zv host port`, `ip route get IP`, `firewall-cmd --list-all` | Module 04 |
| `avc: denied` | `audit2why`, `ls -Z`, `semanage fcontext -l`, `semanage port -l`, `getsebool -a` | Modules 06, 07 |
| Login / sudo problems | `journalctl -u sshd`, `/var/log/secure`, `visudo -c`, `faillock --user NAME` | Module 08 |
| Server won't boot | Console, GRUB menu, `journalctl -b -1`, emergency shell | Module 09 |
| 502 / 403 / TLS errors | nginx/httpd error log, `curl -v`, `openssl s_client` | Module 10 |

### 2.5 The first 30 minutes: hypothesis discipline

Once triage points you somewhere, follow the loop from Module 00: **hypothesise, test, fix, verify**. Three rules keep you out of trouble:

1. **One change at a time.** If you change the config, restart the service and flip a boolean together, you don't know which one fixed it, and you may have introduced a second problem.
2. **Write every change down before you make it.** The incident notes (Section 4) are your undo list.
3. **Time-box each hypothesis.** If 15 minutes on one theory gives nothing, step back, re-read the triage output, and ask for a second pair of eyes. Escalating is a skill, not a failure.

:::prod
**Symptom:** checkout pages slow, then 502s, at 21:40. **What the engineer saw:** high load on two app servers; they assumed a traffic spike and added a third server. Nothing improved. Forty minutes later a colleague ran the triage script and saw `wa` at 85% and `nfs: server not responding` in `dmesg`. **Root cause:** the shared NFS server had a failing disk; every app server hung on reads of shared assets. **Fix:** fail the NFS export over to the replica; app servers recovered within a minute. **Prevention:** triage before scaling; alert on NFS latency; and the team made "run triage, paste the output into the incident channel" step one of their runbook.
:::

## 3. Communicating during an incident

Technical skill fixes the server. Communication keeps the business calm enough to let you fix it. Most organisations use some variant of these roles and severities.

### 3.1 Severity levels

| Severity | Definition | Example | Response |
|---|---|---|---|
| **SEV1** | Full outage or data loss/security breach for a critical service | Main website down for all users; database corruption | Page immediately, incident commander, updates every 15–30 minutes, management informed |
| **SEV2** | Major degradation or a critical service down for some users | Checkout failing for 20% of requests; one region down | Page on-call, updates every 30–60 minutes |
| **SEV3** | Minor impact or redundancy lost, users mostly unaffected | One of three web nodes down; backups failed once | Handle in working hours or next morning |
| **SEV4** | No user impact; housekeeping | Disk at 80% and trending up | Ticket |

### 3.2 Roles

On a large incident, split the work. The person typing commands should not also be answering the CEO.

| Role | Responsibility |
|---|---|
| **Incident commander (IC)** | Owns the incident: decides priorities, assigns tasks, decides when to escalate, declares it resolved. Does not type commands. |
| **Operations / technical lead** | Diagnoses and fixes. Announces changes before making them. |
| **Communications lead** | Posts status updates to stakeholders and customers on a fixed cadence. |
| **Scribe** | Keeps the timeline in the incident notes. On small incidents the on-call engineer does this too. |

### 3.3 The status update template

Post updates on a predictable cadence, even when there is no news. "No change, still investigating, next update at 03:45" is far better than silence, because silence makes people page you directly.

```text
[SEV2] [INVESTIGATING] Checkout errors on app.example.com
Time: 2026-11-25 03:15 UTC
Impact: About 30% of checkout requests return HTTP 502. Browsing works.
Started: 02:58 UTC (first alert 03:02)
What we know: app01 restarted after running out of memory; app02 is serving alone and overloaded.
What we are doing: Restarting the app on app01 with a memory limit; adding app03 to the pool.
Next update: 03:45 UTC, or sooner if status changes.
Incident commander: on-call SRE (primary)
```

Use a fixed vocabulary for the state: **INVESTIGATING → IDENTIFIED → MONITORING → RESOLVED**. Say what users experience, not what the server does: "checkout fails" means something to a manager; "the JVM was OOM-killed" does not.

:::tip
Write updates in UTC and state the time zone. Distributed teams and logs from different servers become impossible to correlate when people mix local times (Drill 27 shows what happens).
:::

## 4. Incident notes template (the live timeline)

Incident notes are written **during** the incident, as you go. They are raw: times, commands, observations, decisions. Keep a file or a channel thread open from minute one.

```text
INCIDENT NOTES
Incident ID:      INC-2026-1125-01
Title:            Checkout 502 errors
Severity:         SEV2
Status:           Resolved / Monitoring / Investigating
On-call / IC:     (names or roles)
Affected:         app01, app02 (app.example.com checkout)
Detected by:      Monitoring alert "HTTP 5xx > 5%" at 03:02 UTC

TIMELINE (UTC)
03:02  Alert fired. Acknowledged.
03:04  Confirmed with curl: 502 on /checkout, 200 on /.
03:06  Ran triage on app01. uptime 8 min (unexpected restart). dmesg: OOM killed java pid 2214.
03:09  free -h: available 380Mi. myapp RSS growing ~200MB/min.
03:11  DECISION: restart myapp with MemoryMax=6G drop-in to protect the host. (change #1)
03:12  systemctl daemon-reload && systemctl restart myapp
03:14  curl /checkout returns 200. 5xx rate falling.
03:30  5xx rate 0%. Moved to MONITORING.
04:10  Stable 40 minutes. RESOLVED. Follow-up: memory leak ticket APP-123.

CHANGES MADE (undo list)
#1  /etc/systemd/system/myapp.service.d/memory.conf  MemoryMax=6G   (keep / revert?)

EVIDENCE
/var/tmp/triage-app01-20261125-030612.txt
journalctl -u myapp --since 02:30 > /var/tmp/myapp-INC-2026-1125-01.log

OPEN QUESTIONS
- Why did memory grow after the 02:30 deploy?
```

:::warn
Never paste passwords, tokens, private keys or customer personal data into incident notes or chat channels. If a log line contains a secret, redact it before pasting.
:::

## 5. Postmortem template

A postmortem (also called an incident review or RCA) is written **after** the incident, within a few working days, for every SEV1 and SEV2 and for any interesting SEV3. It must be **blameless**: it asks "what in our system allowed this to happen?" not "who messed up?". People who fear blame hide information, and hidden information is how the same incident happens again.

```text
POSTMORTEM: Checkout 502 errors (INC-2026-1125-01)
Date of incident: 2026-11-25   Author: (role)   Status: Draft / Reviewed / Closed

1. SUMMARY (3-4 sentences, readable by a non-engineer)
   For 72 minutes about 30% of checkout requests failed with HTTP 502 because the
   application on app01 repeatedly ran out of memory after a deploy...

2. IMPACT
   Duration: 02:58-04:10 UTC (72 min). Users affected: ~30% of checkout attempts.
   Data loss: none. SLA/SLO impact: 0.05% of monthly error budget consumed.

3. TIMELINE (from incident notes, key events only, UTC)

4. ROOT CAUSE
   The direct cause, then the contributing causes. Use "5 whys":
   Why 502?  -> app01 backend down.  Why down? -> OOM-killed.  Why OOM? -> memory
   leak in release 4.2.  Why not caught? -> load test ran 10 min, leak needs 30 min.
   Why did one node failing hurt users? -> pool of 2, no spare capacity.

5. DETECTION
   How did we find out? How long from start to alert? Could we detect it earlier?

6. RESPONSE
   What went well. What went badly. Where were we lucky.

7. ACTION ITEMS (each with an owner role, a ticket and a due date)
   | Action                                       | Type      | Owner   | Ticket  | Due   |
   | Add MemoryMax to myapp unit via Ansible      | Prevent   | Ops     | OPS-88  | 12-01 |
   | Alert on RSS growth > 100MB/10min            | Detect    | SRE     | OPS-89  | 12-05 |
   | 60-minute soak test in CI                    | Prevent   | Dev     | DEV-301 | 12-15 |
   | Add third app node                           | Mitigate  | Ops     | OPS-90  | 12-10 |

8. LESSONS LEARNED
```

:::concept
A good action item is **specific, owned and verifiable**: "Add `nofail` to all non-root mounts in the Ansible fstab template and add a CI check" is a good action item. "Be more careful with fstab" is not an action item; it is a wish. Prefer actions that change the **system** (automation, validation, alerting) over actions that change **people's behaviour** (training, reminders).
:::

### 5.1 Master troubleshooting runbook

This is the condensed runbook for the whole course. Keep it next to the triage checklist.

| Symptom | Check (command) | Likely cause | Fix |
|---|---|---|---|
| "No space left on device" | `df -h`, `df -i`, `lsof +L1` | Full filesystem, inode exhaustion, deleted-but-open file | Clean up / rotate; truncate or restart the holder; extend LV |
| Service won't start | `systemctl status`, `journalctl -u UNIT -b` | Config syntax, missing file, port in use, SELinux denial | Validate config (`nginx -t`), fix path, free port, fix label/port type |
| Port not reachable remotely | `ss -tulpn`, `firewall-cmd --list-all`, `nc -zv` | Listening on 127.0.0.1, firewall, wrong port | Change bind address; `firewall-cmd --permanent --add-...` and `--reload` |
| Name resolution fails | `getent hosts NAME`, `dig NAME`, `cat /etc/resolv.conf` | Wrong DNS server, NetworkManager overwrote resolv.conf | Set DNS with `nmcli` and reactivate the connection |
| Load high, CPU idle | `top` (`wa`), `vmstat 1`, `iostat -xz 1` | Disk or NFS I/O bottleneck | Find the I/O hog (`pidstat -d`), throttle or reschedule, fix storage |
| Process killed suddenly | `journalctl -k \| grep -i oom` | OOM killer | Limit the culprit (`MemoryMax`), add RAM, fix leak |
| Permission denied, perms look right | `ausearch -m AVC -ts recent`, `ls -Z` | SELinux label, port type or boolean | `semanage fcontext` + `restorecon`, `semanage port`, `setsebool -P` |
| SSH key refused | `journalctl -u sshd`, `ls -ld ~ ~/.ssh`, `ls -Z ~/.ssh` | Bad modes/ownership, wrong context | `chmod 700 ~/.ssh`, `chmod 600 authorized_keys`, `restorecon -Rv ~/.ssh` |
| sudo broken for everyone | `sudo -l` error text | Syntax error in sudoers | `pkexec visudo` or console root; always edit with `visudo` |
| Boot drops to emergency mode | `journalctl -xb`, `systemctl --failed` | Bad `/etc/fstab` entry | Fix fstab, `findmnt --verify`, add `nofail` |
| 502 Bad Gateway | proxy error log, `ss -ltnp`, `curl` backend | Backend down, wrong upstream address, SELinux `httpd_can_network_connect` | Start backend, fix upstream, `setsebool -P` |
| TLS errors for some clients | `openssl s_client -connect HOST:443 -servername HOST` | Expired cert, missing intermediate | Install full chain, renew, monitor expiry |
| Time-related auth/TLS failures | `timedatectl`, `chronyc tracking` | Clock drift, NTP blocked | Fix NTP source, `chronyc makestep` |
| dnf hangs or fails | `ps -ef \| grep -E 'dnf\|rpm'`, `dnf repolist -v` | Lock held, repo/proxy unreachable | Stop the stuck job cleanly, fix repo/proxy, `dnf clean all` |

## 6. Part B: thirty incident drills

Each drill gives you the alert as it would arrive, the questions you should ask, and the commands to run. The root cause, the fix and the prevention are hidden in the answer box. Before you open it, write down:

1. Your top three hypotheses, most likely first.
2. The first three commands you would run, and what output would confirm or rule out each hypothesis.
3. What you would tell stakeholders in your first status update.

The drills are deliberately mixed in order, as a real on-call week would be, and the titles describe the symptom, not the cause. Unless stated otherwise, every server is Rocky/Alma/RHEL 9 with SELinux enforcing.

:::lab Lab 11-A — Turn a drill into a timed game day
**Goal:** practise drills against the clock with a partner (or your future self).

**Break it:** take a snapshot of your lab VM. Your partner picks a drill from this section, reproduces the "break" on the VM without telling you which one, and starts a timer. Working alone, write a script per drill in `~/drills/` (for example `drill-09.sh`), and run one at random with `bash "$(ls ~/drills/*.sh | shuf -n1)"` without reading it.

**Symptoms:** whatever the drill's alert says. Your partner plays the user: "the website is broken".

**Diagnose:** run `triage` (Section 2.3) first, every time, then follow the evidence. Keep incident notes in the Section 4 format.

**Fix:** the smallest change that fixes the root cause. No `setenforce 0`, no `chmod 777`.

**Verify:** prove the user-facing symptom is gone (`curl`, a login, a test job), not just that the service is "active".

**Clean-up/revert:** revert the VM to the snapshot. Record your time to diagnose and time to fix; aim to halve them over the 30-day plan.
:::

### Drill 01 — "/var is 100% full, but du can't find the space"

**Alert / symptom:** `DiskUsage CRITICAL: /var 100% on app01`. The application is logging `No space left on device`. A colleague already deleted a 9 GB file `/var/log/myapp/app.log.1` twenty minutes ago, but `df` still shows 100%.

**Questions to ask:**

- Did anyone delete files while a service had them open?
- Does `du` agree with `df`? If `du` shows much less, where did the space go?
- Is it bytes or inodes?

**Commands to run:**

```bash
df -h /var; df -i /var
sudo du -xh --max-depth=2 /var 2>/dev/null | sort -h | tail -10
sudo lsof +L1 | head
```

```text
COMMAND  PID  USER  FD  TYPE DEVICE   SIZE/OFF NLINK   NODE NAME
java    2290 myapp  5w   REG  253,2 9663676416     0 524301 /var/log/myapp/app.log.1 (deleted)
```

:::answer
**Root cause:** the file was deleted while the Java process still held it open for writing. Deleting removes the directory entry, but the inode and its blocks are freed only when the last open file descriptor closes. `du` walks directories, so it can't see the file; `df` counts blocks, so it still sees them. `lsof +L1` lists open files with a link count below 1, that is, deleted but still open.

**Fix:** either restart the process (`sudo systemctl restart myapp`), or, if a restart is not acceptable, truncate the file through the process's file descriptor:

```bash
sudo truncate -s 0 /proc/2290/fd/5
df -h /var
```

**Prevention:** never `rm` an active log; truncate it (`: > file`) or let logrotate handle it. For apps that keep the file open, use `copytruncate` in the logrotate config or a `postrotate` signal that makes the app reopen its log. Alert at 80% and 90%, not only at 100%. Revise: Module 03.
:::

### Drill 02 — "The website shows 403 Forbidden after the content migration"

**Alert / symptom:** after a developer deployed the new marketing site to `/var/www/html` on web01, every page returns `403 Forbidden`. The files are owned by `apache:apache` with mode 644, directories 755. The developer says "permissions are fine, I checked".

**Questions to ask:**

- How exactly were the files put there: `cp`, `mv`, `tar`, `rsync`?
- What does the error log say?
- Is there an AVC denial?

**Commands to run:**

```bash
sudo tail -5 /var/log/httpd/error_log
ls -lZ /var/www/html | head -5
sudo ausearch -m AVC -ts recent | audit2why
```

```text
-rw-r--r--. 1 apache apache unconfined_u:object_r:user_home_t:s0 4210 Nov 25 09:12 index.html
type=AVC msg=audit(...): avc:  denied  { read } for  pid=3011 comm="httpd" name="index.html" ... scontext=system_u:system_r:httpd_t:s0 tcontext=unconfined_u:object_r:user_home_t:s0 tclass=file permissive=0
        Was caused by:
                Missing type enforcement (TE) allow rule.
```

:::answer
**Root cause:** the developer built the site in their home directory and used `mv` to move it into `/var/www/html`. `mv` on the same filesystem keeps the file's existing SELinux label, so the files are still `user_home_t`. The `httpd_t` domain is not allowed to read `user_home_t`. (`cp` would have created new files that inherit the directory's default label, `httpd_sys_content_t`.)

**Fix:** reset the labels to the policy default for that path. No policy change is needed, because `/var/www/html` already has the right rule:

```bash
sudo restorecon -Rv /var/www/html
curl -I http://localhost/
```

**Prevention:** deploy with `cp`, `rsync` or your deployment tool rather than `mv` from a home directory, and add `restorecon -R /var/www/html` as the final step of the deploy playbook. Do **not** use `audit2allow` here: it would generate a rule letting the web server read users' home directories, which is a real security hole. Revise: Modules 06 and 07.
:::

### Drill 03 — "nginx won't start after a config change, and now systemctl refuses to try"

**Alert / symptom:** a teammate edited `/etc/nginx/conf.d/app.conf` and ran `systemctl restart nginx` several times. Now the site is down and `systemctl start nginx` returns immediately with: `Job for nginx.service failed. See "systemctl status nginx.service"`. Status shows `start-limit-hit`.

**Questions to ask:**

- What exactly changed in the config?
- What did the first failure (not the latest) say?
- Has the config been validated?

**Commands to run:**

```bash
systemctl status nginx --no-pager
journalctl -u nginx -b --no-pager | grep -v 'start-limit' | tail -20
sudo nginx -t
```

```text
nginx: [emerg] unexpected "}" in /etc/nginx/conf.d/app.conf:14
nginx: configuration file /etc/nginx/nginx.conf test failed
```

:::answer
**Root cause:** a syntax error (a missing `;` on line 13, so the parser met `}` unexpectedly). Each restart attempt failed. After several failures within the start-limit window (by default 5 starts in 10 seconds, set by `StartLimitBurst` and `StartLimitIntervalSec`), systemd marks the unit `start-limit-hit` and refuses further starts, which hides the original error.

**Fix:**

```bash
sudo vi /etc/nginx/conf.d/app.conf       # add the missing semicolon
sudo nginx -t
sudo systemctl reset-failed nginx
sudo systemctl start nginx
```

**Prevention:** always run `nginx -t` (or `apachectl configtest`, `sshd -t`, `named-checkconf`) before a restart, and prefer `systemctl reload` for config changes: if the new config is invalid, a reload fails while the old worker processes keep serving. In Ansible, use the `validate:` parameter on the template task. Revise: Module 02.
:::

### Drill 04 — "The nightly report didn't arrive, but the script works when I run it"

**Alert / symptom:** the finance team didn't get the 02:00 report. The cron entry is in the `reports` user's crontab. When the engineer runs `/opt/reports/nightly.sh` by hand, it works perfectly.

**Questions to ask:**

- Did cron start the job at all?
- What environment does the script assume (PATH, variables, working directory)?
- Is there anything unusual in the crontab line?

**Commands to run:**

```bash
sudo crontab -l -u reports
sudo grep reports /var/log/cron | tail -5
journalctl -u crond --since yesterday --no-pager | tail
```

```text
0 2 * * * /opt/reports/nightly.sh > /var/tmp/report-$(date +%F).log 2>&1
Nov 25 02:00:01 app01 CROND[44120]: (reports) CMD (/opt/reports/nightly.sh > /var/tmp/report-$(date +)
```

:::answer
**Root cause:** in a crontab, `%` is special: it ends the command and the rest of the line is passed as standard input. The command was cut at `date +`, so the shell got a broken command and the script never ran correctly. The log line shows the truncated command. (Two other classic causes to check in the same drill: cron's minimal `PATH`, usually just `/usr/bin:/bin`, which breaks scripts that call tools in `/usr/local/bin` or `/usr/sbin`; and relative paths that only work from the engineer's current directory.)

**Fix:** escape the percent signs, or move the logic into the script:

```text
0 2 * * * /opt/reports/nightly.sh > /var/tmp/report-$(date +\%F).log 2>&1
```

**Prevention:** keep crontab lines simple (call a script, nothing else); set `PATH=` at the top of the script; use absolute paths; send output somewhere you monitor. Consider a systemd timer instead: failures show up in `systemctl --failed` and `journalctl -u`. Add a "dead man's switch" check that alerts if the report file is missing at 03:00. Revise: Module 02.
:::

### Drill 05 — "502 Bad Gateway: nginx can't reach the app, but curl from the shell works"

**Alert / symptom:** a new Java app listens on `127.0.0.1:8080`. nginx proxies `app.example.com` to it. Every request returns `502 Bad Gateway`. `curl http://127.0.0.1:8080/health` from the shell returns `200 OK`.

**Questions to ask:**

- What does the nginx error log say: refused, timed out, or permission denied?
- Is the backend actually listening where nginx expects?
- If the shell can connect and nginx can't, what is different about nginx?

**Commands to run:**

```bash
sudo tail -3 /var/log/nginx/error.log
ss -ltnp | grep 8080
sudo ausearch -m AVC -ts recent | audit2why
```

```text
connect() to 127.0.0.1:8080 failed (13: Permission denied) while connecting to upstream
avc:  denied  { name_connect } for  pid=1180 comm="nginx" dest=8080 scontext=system_u:system_r:httpd_t:s0 tcontext=system_u:object_r:http_cache_port_t:s0 tclass=tcp_socket permissive=0
        Was caused by:
        The boolean httpd_can_network_connect was set incorrectly.
        Allow access by executing:
        # setsebool -P httpd_can_network_connect 1
```

:::answer
**Root cause:** nginx runs in the `httpd_t` domain. By default SELinux does not let web servers open arbitrary outbound TCP connections, which limits what an attacker can do through a compromised web server. Your shell runs as `unconfined_t`, which is why `curl` works. Error 13 (`Permission denied`) on a `connect()` is the giveaway; a backend that is down gives 111 (`Connection refused`).

**Fix:** enable the boolean that exists for exactly this case, persistently:

```bash
sudo setsebool -P httpd_can_network_connect on
getsebool httpd_can_network_connect
curl -I https://app.example.com/
```

**Prevention:** include the boolean in the web server's Ansible role whenever it proxies to a backend. Always follow what `audit2why` reports: for some destinations a narrower boolean (such as `httpd_can_network_relay` or `httpd_can_network_connect_db`) is enough, and the narrowest one is preferable. Never `setenforce 0`, and don't use `-P`-less `setsebool` (it is lost at reboot). Revise: Module 07.
:::

### Drill 06 — "After the reboot, the app can't resolve anything"

**Alert / symptom:** after patching and rebooting db-proxy01, the app logs `Name or service not known` for `db01.example.com`. Before the reboot it worked. Someone had "fixed" DNS last month by editing `/etc/resolv.conf` by hand.

**Questions to ask:**

- Which nameserver is the system using now?
- Who manages `/etc/resolv.conf` on this host?
- Is the problem DNS itself, or this host's configuration?

**Commands to run:**

```bash
cat /etc/resolv.conf
getent hosts db01.example.com
dig @10.0.0.2 db01.example.com +short
nmcli -g IP4.DNS device show eth0
nmcli -g ipv4.dns,ipv4.ignore-auto-dns connection show eth0
```

```text
# Generated by NetworkManager
nameserver 192.0.2.53
```

:::answer
**Root cause:** NetworkManager owns `/etc/resolv.conf` on RHEL 9. The manual edit lasted only until NetworkManager regenerated the file at boot from the connection profile, which still received an external resolver (`192.0.2.53`) via DHCP. That resolver can't see the internal zone. `dig @10.0.0.2` (the internal DNS server) answers correctly, which proves the DNS service is fine and the client configuration is wrong.

**Fix:** put the setting in the connection profile, where it persists:

```bash
sudo nmcli connection modify eth0 ipv4.dns "10.0.0.2 10.0.0.3" ipv4.ignore-auto-dns yes ipv4.dns-search example.com
sudo nmcli connection up eth0
getent hosts db01.example.com
```

**Prevention:** never hand-edit generated files; look for the "Generated by" header. Manage DNS through `nmcli`/nmstate or the Ansible `community.general.nmcli` module. Test with `getent hosts` (which follows `/etc/nsswitch.conf`, as applications do), not only `dig`. On Ubuntu, `/etc/resolv.conf` is usually managed by systemd-resolved and netplan instead; check with `resolvectl status`. Revise: Module 04.
:::

### Drill 07 — "No space left on device, but df shows 40% used"

**Alert / symptom:** the PHP application on web02 can't create sessions: `failed: No space left on device (28)`. `df -h /var` shows 40% used.

**Questions to ask:**

- If bytes are free, what else can run out?
- Which directory has an unusual number of files?
- What normally cleans that directory?

**Commands to run:**

```bash
df -i /var
sudo find /var -xdev -type f 2>/dev/null | cut -d/ -f2-4 | sort | uniq -c | sort -n | tail -5
sudo ls /var/lib/php/session | wc -l
```

```text
Filesystem           Inodes  IUsed IFree IUse% Mounted on
/dev/mapper/rl-var  7864320 7864320     0  100% /var
5120344 var/lib/php
```

:::answer
**Root cause:** inode exhaustion. Every file uses one inode, regardless of size. Five million tiny session files used every inode in `/var`. The PHP session garbage collector was disabled (`session.gc_probability = 0` is the distribution default on some systems, which expect an external cleanup job) and the cleanup job had been removed during a rebuild.

**Fix:** delete old sessions in a way that doesn't blow the argument list:

```bash
sudo find /var/lib/php/session -type f -mmin +1440 -delete
df -i /var
```

**Prevention:** restore the cleanup (a systemd timer or `systemd-tmpfiles` rule with an age, for example `d /var/lib/php/session 1770 root apache 1d`); move sessions to Redis or memcached for multi-server apps; alert on `df -i` as well as `df -h`. On XFS, inodes are allocated dynamically up to a percentage of space (`imaxpct`), so exhaustion is rarer than on ext4, but it still happens. Revise: Module 03.
:::

### Drill 08 — "The new engineer's SSH key is rejected"

**Alert / symptom:** a new user `alice` has had her public key added to `/home/alice/.ssh/authorized_keys` by a colleague who created the directory as root. `ssh alice@bastion01` asks for a password. The key is definitely correct.

**Questions to ask:**

- What does sshd log for that login attempt?
- Who owns `~alice`, `~alice/.ssh` and `authorized_keys`, and with what modes?
- How was the file created (and so, which SELinux label does it have)?

**Commands to run:**

```bash
sudo journalctl -u sshd --since "-10min" --no-pager | tail
sudo ls -ld /home/alice /home/alice/.ssh /home/alice/.ssh/authorized_keys
sudo ls -Z /home/alice/.ssh/authorized_keys
```

```text
sshd[5120]: Authentication refused: bad ownership or modes for directory /home/alice/.ssh
drwxr-xr-x. 2 root  root  ... /home/alice/.ssh
-rw-rw-r--. 1 root  root  ... /home/alice/.ssh/authorized_keys
```

:::answer
**Root cause:** with `StrictModes yes` (the default), sshd refuses keys if the home directory, `.ssh` or `authorized_keys` is writable by anyone other than the user (or root), or is owned by someone else. The directory and file are owned by root and the file is group-writable. A second, frequent cause on SELinux systems is a wrong label: `authorized_keys` must be `ssh_home_t`; a file copied from `/tmp` can carry `user_tmp_t`, and sshd then can't read it (look for an AVC from `sshd_t`).

**Fix:**

```bash
sudo chown -R alice:alice /home/alice/.ssh
sudo chmod 700 /home/alice/.ssh
sudo chmod 600 /home/alice/.ssh/authorized_keys
sudo restorecon -Rv /home/alice/.ssh
```

**Prevention:** add keys with `ssh-copy-id` or the Ansible `ansible.posix.authorized_key` module, which set ownership and modes correctly. Never "fix" it with `StrictModes no`. Revise: Module 08.
:::

### Drill 09 — "nginx won't start on the new port"

**Alert / symptom:** to run a second site, the team changed `listen 80;` to `listen 8095;` in one server block, opened 8095 in firewalld, and restarted nginx. nginx fails to start. `nginx -t` says the syntax is OK.

**Questions to ask:**

- Is another process already on 8095?
- What is the exact bind error?
- Is the port labelled for web servers in SELinux policy?

**Commands to run:**

```bash
journalctl -u nginx -b --no-pager | tail -5
ss -ltnp | grep 8095
sudo ausearch -m AVC -ts recent
sudo semanage port -l | grep -w http_port_t
```

```text
nginx[7710]: nginx: [emerg] bind() to 0.0.0.0:8095 failed (13: Permission denied)
avc:  denied  { name_bind } for  pid=7710 comm="nginx" src=8095 scontext=system_u:system_r:httpd_t:s0 tcontext=system_u:object_r:unreserved_port_t:s0 tclass=tcp_socket permissive=0
http_port_t                    tcp      80, 81, 443, 488, 8008, 8009, 8443, 9000
```

:::answer
**Root cause:** SELinux controls which ports each domain may bind. `httpd_t` (used by both httpd and nginx) may bind ports labelled `http_port_t`. Port 8095 has no specific label, so it is `unreserved_port_t`, and the bind is denied. Error 13 on `bind()` (rather than 98, "Address already in use") points straight at a permission problem.

**Fix:** label the port for web use:

```bash
sudo semanage port -a -t http_port_t -p tcp 8095
sudo systemctl start nginx
ss -ltnp | grep 8095
```

If `semanage port -a` reports that the port is already defined for another type, either use `-m` (modify) after checking nothing else needs it, or pick a port already in `http_port_t`.

**Prevention:** every "change the port" runbook has three steps: the service config, the firewall (`firewall-cmd --permanent --add-port=8095/tcp`), and the SELinux port type. Put all three in the Ansible role (`community.general.seport`). Revise: Module 07.
:::

### Drill 10 — "The app can't connect to the database since the DB moved to its own server"

**Alert / symptom:** PostgreSQL was moved from app01 (local) to a new server db01 (10.0.0.20). The app now logs `connection refused` for `10.0.0.20:5432`. On db01 itself, `psql -h localhost` works.

**Questions to ask:**

- On db01, which address is PostgreSQL listening on?
- Is a firewall between them, or on db01?
- After the TCP connection works, will authentication work?

**Commands to run:**

```bash
# on app01
nc -zv 10.0.0.20 5432
# on db01
ss -ltnp | grep 5432
sudo firewall-cmd --list-all
sudo -u postgres psql -c 'SHOW listen_addresses;'
```

```text
Ncat: Connection refused.
LISTEN 0  244  127.0.0.1:5432  0.0.0.0:*  users:(("postgres",pid=901,fd=7))
```

:::answer
**Root cause:** PostgreSQL's default `listen_addresses = 'localhost'` means it accepts TCP connections only on the loopback interface. "Connection refused" (an immediate TCP reset) means nothing is listening on that address; a firewall drop would normally show as a timeout instead (firewalld's default rejects with an ICMP "prohibited" message, so `nc` may also report a different error).

**Fix:** on db01, in `postgresql.conf` set `listen_addresses = '10.0.0.20'` (or `'*'`), allow the app subnet in `pg_hba.conf`, open the firewall, and restart:

```text
# pg_hba.conf
host  appdb  appuser  10.0.0.0/24  scram-sha-256
```

```bash
sudo firewall-cmd --permanent --add-service=postgresql
sudo firewall-cmd --reload
sudo systemctl restart postgresql
```

Expect the next error to be `no pg_hba.conf entry for host` if you skip the `pg_hba.conf` step: that one means TCP works and authentication rules are the problem.

**Prevention:** a migration checklist with the four layers: listen address, host firewall, network firewall/security group, database auth rules. Test each with `nc -zv` and a real client login before cut-over. Revise: Modules 04 and 10.
:::

### Drill 11 — "psql works from the shell, but the PHP app can't reach the database"

**Alert / symptom:** a PHP application served by httpd and php-fpm on web03 shows `SQLSTATE[08006] could not connect to server: Permission denied` for `db01.example.com:5432`. From the same server, `psql -h db01.example.com -U appuser appdb` connects fine.

**Questions to ask:**

- Is this a network block (timeout), a refused connection, or a local permission denial?
- Which security context does the PHP process run in, compared with your shell?
- Did anything change on web03 recently (rebuild, new image)?

**Commands to run:**

```bash
ps -eZ | grep -E 'php-fpm|httpd' | head -3
sudo ausearch -m AVC -ts recent | audit2why
getsebool -a | grep httpd_can_network
```

```text
system_u:system_r:httpd_t:s0  2741 ?  00:00:01 php-fpm
avc:  denied  { name_connect } for pid=2741 comm="php-fpm" dest=5432 scontext=system_u:system_r:httpd_t:s0 tcontext=system_u:object_r:postgresql_port_t:s0 tclass=tcp_socket
        The boolean httpd_can_network_connect_db was set incorrectly.
```

:::answer
**Root cause:** web03 was rebuilt from a fresh image; on the old server, someone had enabled the database boolean years ago and nobody recorded it. php-fpm runs in `httpd_t`, which may not connect to database ports (`postgresql_port_t`, `mysqld_port_t` and others) unless `httpd_can_network_connect_db` is on. "Permission denied" (not a timeout or "refused") for an outbound connection is the SELinux signature.

**Fix:**

```bash
sudo setsebool -P httpd_can_network_connect_db on
```

This boolean is narrower than `httpd_can_network_connect`, which allows connections to **any** port: prefer the narrow one.

**Prevention:** after any manual SELinux change, capture it in configuration management. To find what was changed locally on an old server before decommissioning it, run `sudo semanage export` (it lists local booleans, port and fcontext changes) and `semanage boolean -l -C`. Revise: Module 07.
:::

### Drill 12 — "Load average 30, but the CPUs are idle"

**Alert / symptom:** `LoadHigh: load1 = 31 on files01 (8 CPUs)`. Users say the file share and the web UI are slow. `top` shows no process using much CPU.

**Questions to ask:**

- What does the CPU line in `top` say about `wa`?
- Which processes are in state `D`?
- Which process is doing the I/O, and is it scheduled (backup, reindex, updatedb)?

**Commands to run:**

```bash
vmstat 1 5
iostat -xz 1 3          # from the sysstat package
sudo pidstat -d 1 5
ps -eo state,pid,user,cmd | awk '$1=="D"' | head
```

```text
procs -----------memory---------- ---swap-- -----io---- -system-- ------cpu-----
 r  b   swpd   free   buff  cache   si   so    bi    bo   in   cs us sy id wa st
 1 28      0 412000  90012 6100224   0    0 182000   520 2100 3900  2  3  6 89  0
Device   r/s    rkB/s   w/s  wkB/s  r_await w_await aqu-sz  %util
sdb    1480.0 182400.0  12.0  510.0    48.20    9.10  71.40  100.0
UID   PID   kB_rd/s  kB_wr/s Command
  0  6120 176000.00     0.00 tar
```

:::answer
**Root cause:** a backup job (`tar` of the whole share) had been moved from 01:00 to 10:00 and saturated the data disk: `%util` 100%, read latency 48 ms, 28 processes blocked (`b` column) in `D` state. Linux counts `D`-state processes in the load average, so load rises even though the CPUs are idle (`wa` 89%).

**Fix:** mitigate now by lowering the job's I/O priority or stopping it, then reschedule:

```bash
sudo ionice -c 3 -p 6120            # idle class; effective with the BFQ scheduler, limited with mq-deadline/none
sudo renice -n 19 -p 6120
```

A more reliable throttle is to run the job as a systemd unit with `IOReadBandwidthMax=/dev/sdb 50M` (cgroup v2, which RHEL 9 uses by default) and `Nice=19`.

**Prevention:** run backups from a systemd timer with I/O limits, outside business hours; back up from snapshots or a replica rather than the live disk; alert on disk latency (`r_await`/`w_await`), not only load. Revise: Module 05.
:::

### Drill 13 — "The database restarted by itself at 02:58"

**Alert / symptom:** `PostgreSQLDown` fired for 40 seconds at 02:58 on db02, then cleared. The DBA asks why the database restarted. Nobody was logged in. The server also runs a reporting job that starts at 02:45.

**Questions to ask:**

- Was the server rebooted, or only the process?
- Did the kernel kill something?
- Which process was using the most memory at the time, and was it the victim or the cause?

**Commands to run:**

```bash
uptime
journalctl -k -b --no-pager | grep -iE 'out of memory|oom-kill|killed process'
journalctl -u postgresql -b --since 02:40 --no-pager | tail
free -h
```

```text
oom-kill:constraint=CONSTRAINT_NONE,...,task=postgres,pid=1802,uid=26
Out of memory: Killed process 1802 (postgres) total-vm:8601232kB, anon-rss:5210044kB ...
```

:::answer
**Root cause:** the reporting job loaded a large data set into memory, the host ran out of RAM and swap, and the kernel's OOM killer chose the process with the highest "badness" score, which was the PostgreSQL process with the largest RSS. The victim (the database) was not the cause (the report). systemd then restarted PostgreSQL.

**Fix (mitigation and correction):** cap the reporting job and protect the database:

```bash
sudo systemctl edit report.service
```

```ini
[Service]
MemoryMax=2G
```

```bash
sudo systemctl edit postgresql.service
```

```ini
[Service]
OOMScoreAdjust=-800
```

Then `sudo systemctl daemon-reload`. Run the report on a replica if possible.

**Prevention:** never co-locate unbounded batch jobs with a database; set `MemoryMax`/`MemoryHigh` on every non-critical service; alert on `available` memory and on OOM events (`journalctl -k`). Don't disable the OOM killer or set `vm.overcommit_memory` without understanding the consequences. Revise: Module 05.
:::

### Drill 14 — "The server didn't come back after the maintenance reboot"

**Alert / symptom:** app04 was rebooted at 22:00 for patching. It doesn't answer ping on its service IP. On the virtual console you see `You are in emergency mode. After logging in, type "journalctl -xb" to view system logs`. Earlier that day, a colleague added a new data disk.

**Questions to ask:**

- What changed since the last good boot? (fstab, kernel, disk layout)
- Which unit failed?
- Is the root filesystem writable in this shell?

**Commands to run:**

```bash
systemctl --failed
journalctl -xb --no-pager | grep -iE 'fail|timed out|dependency' | head
cat /etc/fstab
lsblk -f
```

```text
● data.mount  loaded failed failed /data
Timed out waiting for device /dev/disk/by-uuid/4c1f2d0e-...
UUID=4c1f2d0e-9a1b-4c55-8b7e-1f3a2b9c0d11  /data  xfs  defaults  0 0
```

:::answer
**Root cause:** the `/etc/fstab` entry for `/data` uses a UUID with a typo (copied by hand). At boot, systemd waited for a device that doesn't exist, the mount timed out, `local-fs.target` failed, and the system dropped to emergency mode.

**Fix:** from the emergency shell (enter the root password; if the root account is locked, use `rd.break` or a rescue image as in Module 09):

```bash
mount -o remount,rw /
blkid /dev/sdb1
vi /etc/fstab                     # correct the UUID; add nofail
findmnt --verify
systemctl daemon-reload
mount -a && systemctl default
```

**Prevention:** after editing fstab, always run `findmnt --verify` and `mount -a` **before** rebooting. Add `nofail` to non-essential data mounts so the server still boots (and is reachable by SSH) if a disk is missing. Template fstab with Ansible's `ansible.posix.mount` module using facts, not hand-typed UUIDs. Revise: Modules 03 and 09.
:::

### Drill 15 — "We opened the port yesterday; today it's closed again"

**Alert / symptom:** yesterday an engineer opened TCP 9100 on mon02 for the metrics exporter and it worked. Today, after a colleague ran `firewall-cmd --reload` to apply an unrelated rule, the monitoring server can't scrape mon02.

**Questions to ask:**

- Was the rule added to the runtime configuration, the permanent configuration, or both?
- Which zone is the interface in?
- Is the exporter still listening on 9100?

**Commands to run:**

```bash
ss -ltnp | grep 9100
sudo firewall-cmd --get-active-zones
sudo firewall-cmd --list-ports
sudo firewall-cmd --permanent --list-ports
```

```text
LISTEN 0  4096  *:9100  *:*  users:(("node_exporter",pid=1440,fd=3))
public
  interfaces: eth0
(no output: empty line)
(no output: empty line)
```

:::answer
**Root cause:** the port was opened with `firewall-cmd --add-port=9100/tcp` (runtime only). `--reload` throws away the runtime configuration and loads the permanent one, which never had the rule. A reboot would have had the same effect.

**Fix:** add it permanently (and to the runtime, or reload):

```bash
sudo firewall-cmd --permanent --zone=public --add-port=9100/tcp
sudo firewall-cmd --reload
sudo firewall-cmd --list-ports
```

If you have built up several runtime changes you want to keep, `firewall-cmd --runtime-to-permanent` saves them all.

**Prevention:** always use `--permanent` followed by `--reload`; manage firewall rules with Ansible (`ansible.posix.firewalld` with `permanent: true, immediate: true`) so the state is declared, not remembered. Restrict the rule to the monitoring server with a rich rule or a dedicated zone with a `source`. On Ubuntu with ufw, rules are persistent by default (`ufw allow from 10.0.0.5 to any port 9100 proto tcp`). Revise: Module 04.
:::

### Drill 16 — "sudo is broken for everyone"

**Alert / symptom:** every admin on app05 gets this when running `sudo`:

```text
>>> /etc/sudoers.d/ops: syntax error near line 3 <<<
sudo: parse error in /etc/sudoers.d/ops near line 3
sudo: no valid sudoers sources found, quitting
```

Root login over SSH is disabled (`PermitRootLogin no`), and nobody knows the root password.

**Questions to ask:**

- Who edited `/etc/sudoers.d/ops`, and with what tool?
- Is there another privileged path: polkit (`pkexec`), the console, a configuration-management agent that runs as root?
- Can the change be reverted from the source (Ansible, Git)?

**Commands to run:**

```bash
cat /etc/sudoers.d/ops
pkexec visudo -c
```

:::answer
**Root cause:** someone edited `/etc/sudoers.d/ops` directly with `vi` instead of `visudo` and left a syntax error (for example `%ops ALL=(ALL) NOPASSWD ALL` with a missing colon). sudo refuses to run with any invalid sudoers source, which locks out everyone who depends on it.

**Fix:** use another route to root:

```bash
pkexec visudo -f /etc/sudoers.d/ops     # polkit; members of wheel can usually authenticate with their own password
visudo -c                                # must say: parsed OK for every file
```

If `pkexec` is unavailable, use the hypervisor/cloud serial console as root, or reboot into `rd.break` (Module 09). If an Ansible run as root is still possible through another account with a working privilege path, push the corrected file.

**Prevention:** always edit with `visudo` (or `visudo -f /etc/sudoers.d/FILE`), which validates before saving. In Ansible, use `validate: /usr/sbin/visudo -cf %s` on the template or copy task, so a broken file is never written. Keep a documented break-glass path (console access with a vaulted root password). Revise: Module 08.
:::

### Drill 17 — "The new document root returns 403"

**Alert / symptom:** the team moved the website to a dedicated volume mounted at `/data/www`, created with `mkdir` and filled with `rsync`. They updated `DocumentRoot` and the `<Directory>` block in httpd's config. httpd starts, but every request gets `403 Forbidden`. Mode bits are 755/644.

**Questions to ask:**

- What label does `/data/www` have, and what should it have?
- Does the policy know about this path?
- Is there an AVC?

**Commands to run:**

```bash
ls -dZ /data /data/www
matchpathcon /data/www/index.html
sudo ausearch -m AVC -ts recent -c httpd | audit2why
```

```text
system_u:object_r:default_t:s0 /data
unconfined_u:object_r:default_t:s0 /data/www
/data/www/index.html    system_u:object_r:default_t:s0
avc:  denied  { getattr } for pid=3120 comm="httpd" path="/data/www/index.html" ... tcontext=unconfined_u:object_r:default_t:s0 tclass=file
```

:::answer
**Root cause:** new top-level directories like `/data` have no specific rule in the file-context database, so they get `default_t`, which confined services can't read. Running `restorecon` alone would not help, because the policy's default for this path **is** `default_t`. You need to add a rule.

**Fix:** teach the policy the new path, then apply it:

```bash
sudo semanage fcontext -a -t httpd_sys_content_t "/data/www(/.*)?"
sudo restorecon -Rv /data/www
curl -I http://localhost/
```

If part of the tree must be writable by the app (uploads, cache), label only that subdirectory `httpd_sys_rw_content_t`.

**Prevention:** use `semanage fcontext` + `restorecon` (persistent, survives relabels) rather than `chcon` (temporary, lost on the next `restorecon` or full relabel). Put the rule in Ansible (`community.general.sefcontext`). An alternative is an equivalence rule: `semanage fcontext -a -e /var/www /data/www`. Revise: Modules 06 and 07.
:::

### Drill 18 — "Address already in use"

**Alert / symptom:** after a deploy, `myapp.service` fails with `java.net.BindException: Address already in use` on port 8080. The previous version was stopped by the deploy script.

**Questions to ask:**

- Which process owns port 8080 right now?
- Is it an old instance that systemd doesn't know about?
- How was the old version started?

**Commands to run:**

```bash
sudo ss -ltnp 'sport = :8080'
ps -o pid,ppid,user,lstart,cmd -p 3399
systemctl status myapp --no-pager
```

```text
LISTEN 0 100 *:8080 *:* users:(("java",pid=3399,fd=45))
  PID  PPID USER   STARTED                  CMD
 3399     1 myapp  Mon Nov 24 16:02:11 2026 java -jar /opt/myapp/myapp-4.1.jar
```

:::answer
**Root cause:** a developer started version 4.1 by hand yesterday with `nohup java -jar ... &` while testing. That process is outside systemd's control (its parent is PID 1, not the service's cgroup), so `systemctl stop myapp` didn't stop it, and it still holds the port. Errno 98 / `EADDRINUSE` means the port is owned by another socket, not a permission problem (that would be errno 13).

**Fix:**

```bash
sudo kill 3399            # SIGTERM first; SIGKILL (-9) only if it ignores TERM
sudo systemctl start myapp
sudo ss -ltnp 'sport = :8080'
```

**Prevention:** run applications only as systemd services; forbid ad-hoc `nohup` starts on production servers. The deploy script should verify the port is free (`ss`) before starting, and the health check should confirm the **new** version answers (for example a `/version` endpoint). Revise: Modules 02 and 04.
:::

### Drill 19 — "We moved SSH to port 2222 and now nobody can log in"

**Alert / symptom:** as part of a hardening ticket, an engineer changed `Port 22` to `Port 2222` in `/etc/ssh/sshd_config` on bastion02 and restarted sshd. The SSH session in which they did this is still open; every new connection fails, on both ports.

**Questions to ask:**

- Is sshd running at all? (If it failed, port 22 is closed too.)
- What does sshd log about the bind?
- Is 2222 allowed in SELinux policy and in the firewall?

**Commands to run (in the still-open session):**

```bash
systemctl status sshd --no-pager | tail -5
sudo ss -ltnp | grep sshd
sudo semanage port -l | grep ssh_port_t
sudo firewall-cmd --list-services --list-ports
```

```text
sshd[8812]: error: Bind to port 2222 on 0.0.0.0 failed: Permission denied.
sshd[8812]: fatal: Cannot bind any address.
ssh_port_t                     tcp      22
```

:::answer
**Root cause:** two layers were missed. SELinux allows `sshd_t` to bind only ports labelled `ssh_port_t` (by default only 22), so sshd failed to start, closing port 22 as well. Even after that is fixed, firewalld allows only the `ssh` service (22/tcp), so 2222 would be blocked.

**Fix:** in the session that is still open (do not close it):

```bash
sudo semanage port -a -t ssh_port_t -p tcp 2222
sudo firewall-cmd --permanent --add-port=2222/tcp
sudo firewall-cmd --reload
sudo sshd -t && sudo systemctl restart sshd
ssh -p 2222 user@bastion02           # test from a NEW terminal before closing the old one
```

The commented hint in RHEL's `sshd_config` describes this exact `semanage` command.

**Prevention:** for any remote-access change, keep the existing session open, validate with `sshd -t`, test a new session before logging out, and have console access ready. Treat "port + firewall + SELinux" as one change in Ansible. Revise: Modules 07 and 08.
:::

### Drill 20 — "df hangs, and so does every shell that touches /mnt/shared"

**Alert / symptom:** on app06, `df -h` never returns; `ls /mnt/shared` hangs and Ctrl+C does nothing. The load average climbs steadily. `/mnt/shared` is an NFSv4 mount from `nfs01.example.com`, which was rebuilt over the weekend.

**Questions to ask:**

- Is the NFS server reachable at all, and on port 2049?
- What does the kernel log say?
- What changed on the server during the rebuild?

**Commands to run:**

```bash
df -h -x nfs4                         # df that skips the hung NFS mount
dmesg -T | grep -i nfs | tail -3
ping -c 2 nfs01.example.com
nc -zv nfs01.example.com 2049
findmnt -t nfs4 -o TARGET,SOURCE,OPTIONS
```

```text
[Tue Nov 25 08:02:11 2026] nfs: server nfs01.example.com not responding, still trying
2 packets transmitted, 2 received, 0% packet loss
Ncat: No route to host.
/mnt/shared nfs01.example.com:/export/shared rw,relatime,vers=4.2,hard,proto=tcp,timeo=600,retrans=2,...
```

:::answer
**Root cause:** the rebuilt NFS server came up with firewalld's default `public` zone, which doesn't allow the `nfs` service. Ping works (ICMP), but TCP 2049 is rejected (`No route to host` is how `ncat` reports firewalld's ICMP "host prohibited" reject). The client mount is `hard` (the default and the right choice for data integrity), so every process that touches it waits forever in `D` state, which raises the load and hangs `df`.

**Fix:** on the server:

```bash
sudo firewall-cmd --permanent --add-service=nfs
sudo firewall-cmd --reload
```

The clients recover by themselves within a retry interval. NFSv3 clients would also need `rpc-bind` and `mountd` services opened.

**Prevention:** build NFS servers from the same Ansible role (firewall included) and test from a client before declaring the rebuild done. On clients, use `_netdev` and consider `x-systemd.automount` so boot doesn't block on NFS. Monitor with `df -x nfs4` plus a separate NFS check with a timeout (`timeout 5 stat /mnt/shared`), so the monitoring itself doesn't hang. Revise: Modules 03 and 04.
:::

### Drill 21 — "It works in permissive mode, but there are no AVC denials"

**Alert / symptom:** after the upload feature was added to a PHP app on web04, file uploads fail with a generic application error. An engineer tested `setenforce 0` for one request (and immediately `setenforce 1`): the upload worked. But `ausearch -m AVC -ts today` shows nothing relevant.

**Questions to ask:**

- If SELinux is clearly involved but nothing is logged, what could be suppressing the log?
- Where does the app write uploads, and what is that directory's label?
- Is `auditd` running (otherwise denials go only to the kernel log)?

**Commands to run:**

```bash
systemctl is-active auditd
ls -dZ /var/www/app/uploads
sudo semodule -DB                     # rebuild policy WITHOUT dontaudit rules
# reproduce the upload once, then:
sudo ausearch -m AVC -ts recent | audit2why
sudo semodule -B                      # rebuild policy WITH dontaudit rules again
```

```text
unconfined_u:object_r:httpd_sys_content_t:s0 /var/www/app/uploads
avc:  denied  { write } for  pid=4410 comm="php-fpm" name="uploads" ... tcontext=unconfined_u:object_r:httpd_sys_content_t:s0 tclass=dir permissive=0
```

:::answer
**Root cause:** the uploads directory inherited the read-only web content label `httpd_sys_content_t`. The web server domain is not allowed to write there. In this case the denial did not appear in the audit log: policy contains **dontaudit** rules that suppress logging of some expected, noisy denials, and occasionally one hides the denial you need. (Also check that `auditd` is running; without it, AVCs go to the kernel log and `ausearch` sees nothing.)

**Fix:** label only the writable directory as read-write web content:

```bash
sudo semanage fcontext -a -t httpd_sys_rw_content_t "/var/www/app/uploads(/.*)?"
sudo restorecon -Rv /var/www/app/uploads
```

**Prevention:** remember to run `semodule -B` after `semodule -DB`; leaving dontaudit disabled floods the audit log. Design apps so that writable paths are separate, documented directories, and label them in the deployment role. The "permissive for one request" test was acceptable only because it was reverted immediately; `semanage permissive -a httpd_t` would have limited it to one domain. Revise: Modules 06 and 07.
:::

### Drill 22 — "logrotate fails every night for the app logs on /data"

**Alert / symptom:** `logrotate.service` appears in `systemctl --failed` every morning on app07. The application logs to `/data/logs/myapp/*.log`, and those files are now 40 GB. Running `sudo logrotate -f /etc/logrotate.d/myapp` manually as root works.

**Questions to ask:**

- What does the failing run log?
- What's different between logrotate run by systemd and logrotate run from your root shell?
- What labels do the log files have?

**Commands to run:**

```bash
journalctl -u logrotate --since yesterday --no-pager | tail -5
ls -dZ /data/logs/myapp
sudo ausearch -m AVC -ts yesterday -c logrotate | audit2why | head -20
```

```text
logrotate[9912]: error: error opening /data/logs/myapp/app.log: Permission denied
system_u:object_r:default_t:s0 /data/logs/myapp
avc:  denied  { read } for  pid=9912 comm="logrotate" ... scontext=system_u:system_r:logrotate_t:s0 tcontext=system_u:object_r:default_t:s0 tclass=file
```

:::answer
**Root cause:** when started by `logrotate.timer`, logrotate runs confined in `logrotate_t`, which may manage files labelled as logs (`var_log_t` and related types) but not `default_t`. Run from an interactive root shell, it ran as `unconfined_t`, which is why the manual test "proved" everything was fine. The log directory on the custom `/data` volume never received a log label.

**Fix:**

```bash
sudo semanage fcontext -a -t var_log_t "/data/logs(/.*)?"
sudo restorecon -Rv /data/logs
sudo systemctl start logrotate.service
systemctl status logrotate --no-pager
```

If the application itself runs in a confined domain, check that it can still write to `var_log_t` (or use that domain's own log type, if its policy defines one).

**Prevention:** test services the way they actually run: `systemctl start logrotate.service`, not the binary from your shell. Any data path outside the standard hierarchy needs an fcontext rule in the role that creates it. Revise: Modules 02 and 07.
:::

### Drill 23 — "The web content on the NFS share returns 403"

**Alert / symptom:** to share content across three web servers, the team mounted `nfs01:/export/web` at `/var/www/html/shared`. Files under `shared/` return `403 Forbidden`; everything else on the site works. `restorecon -Rv /var/www/html/shared` changes nothing.

**Questions to ask:**

- What label do files on an NFS mount have?
- Can you relabel files on NFS from the client?
- Is there a boolean for web content on NFS?

**Commands to run:**

```bash
ls -Z /var/www/html/shared | head -3
sudo ausearch -m AVC -ts recent | audit2why
getsebool httpd_use_nfs
```

```text
system_u:object_r:nfs_t:s0 logo.png
avc:  denied  { getattr } for pid=3310 comm="httpd" ... tcontext=system_u:object_r:nfs_t:s0 tclass=file
        The boolean httpd_use_nfs was set incorrectly.
httpd_use_nfs --> off
```

:::answer
**Root cause:** by default, files on an NFS mount don't carry individual SELinux labels on the client; the whole mount appears as `nfs_t`. `restorecon` can't change that. The web server is not allowed to read `nfs_t` unless the `httpd_use_nfs` boolean is on.

**Fix:**

```bash
sudo setsebool -P httpd_use_nfs on
```

An alternative is to mount with an explicit context so the files look like web content: add `context="system_u:object_r:httpd_sys_content_t:s0"` to the mount options in `/etc/fstab`. That is more precise (only this mount, only this type) but every mount needs it.

**Prevention:** document NFS-related booleans in the web role (`httpd_use_nfs`; similarly `use_nfs_home_dirs` for NFS home directories, `virt_use_nfs` for VM images). For Samba/CIFS content the equivalent is `httpd_use_cifs`. Revise: Module 07.
:::

### Drill 24 — "After the certificate renewal, browsers are fine but the API clients fail"

**Alert / symptom:** the TLS certificate for `api.example.com` was renewed yesterday. Browsers show the padlock. Since then, partner integrations (Java and `curl`-based) fail with `unable to get local issuer certificate` or `PKIX path building failed`.

**Questions to ask:**

- What exactly is the server sending in its certificate chain?
- What changed: the leaf, the intermediate CA, the file path in the config?
- Why would browsers succeed when other clients fail?

**Commands to run:**

```bash
openssl s_client -connect api.example.com:443 -servername api.example.com -showcerts </dev/null 2>/dev/null | grep -E ' s:| i:'
openssl s_client -connect api.example.com:443 -servername api.example.com </dev/null 2>/dev/null | grep -E 'Verify return code'
echo | openssl s_client -connect api.example.com:443 -servername api.example.com 2>/dev/null | openssl x509 -noout -subject -issuer -dates
```

```text
 0 s:CN = api.example.com
   i:C = US, O = Example CA, CN = Example Intermediate CA R2
Verify return code: 21 (unable to verify the first certificate)
```

:::answer
**Root cause:** the new certificate file contains only the leaf certificate; the intermediate CA certificate is missing. Browsers often "repair" the chain using cached intermediates or by downloading them (AIA fetching), so they succeed. Most non-browser clients (OpenSSL, Java, Go) do not, and fail to build a path to a trusted root. The chain shows only certificate `0`.

**Fix:** serve the full chain (leaf first, then intermediates, without the root):

```bash
cat api.example.com.crt intermediate.crt | sudo tee /etc/pki/tls/certs/api.example.com-fullchain.crt >/dev/null
sudo restorecon -v /etc/pki/tls/certs/api.example.com-fullchain.crt
# nginx: ssl_certificate /etc/pki/tls/certs/api.example.com-fullchain.crt;
sudo nginx -t && sudo systemctl reload nginx
```

Note the `restorecon`: certificate files created in a home directory and moved into place keep the wrong label, and nginx fails to load them with `Permission denied` (they should be `cert_t`).

**Prevention:** automate renewal with a tool that installs the full chain (an ACME client typically writes `fullchain.pem`); after every renewal, check the chain with `openssl s_client` from a non-browser client; monitor certificate expiry (alert at 30 and 14 days). Revise: Module 10.
:::

### Drill 25 — "502 after the upstream was changed to 'localhost'"

**Alert / symptom:** to "tidy up" the config, someone changed `proxy_pass http://127.0.0.1:8080;` to `proxy_pass http://localhost:8080;` and reloaded nginx. Now about half the requests (or all of them) get `502 Bad Gateway`. SELinux booleans are correct and the backend is up.

**Questions to ask:**

- What error number does nginx log: 13, 111, or 110?
- What does `localhost` resolve to on this host?
- Which addresses does the backend listen on?

**Commands to run:**

```bash
sudo tail -3 /var/log/nginx/error.log
getent ahosts localhost
ss -ltnp | grep 8080
```

```text
connect() failed (111: Connection refused) while connecting to upstream, ... upstream: "http://[::1]:8080/"
::1             STREAM localhost
127.0.0.1       STREAM localhost
LISTEN 0 100 127.0.0.1:8080 0.0.0.0:* users:(("java",pid=2290,fd=41))
```

:::answer
**Root cause:** `localhost` resolves to both `::1` (IPv6) and `127.0.0.1` (IPv4). nginx resolves the name at load time and uses every address it gets, so it tries `[::1]:8080`, where nothing listens (the backend binds only IPv4 `127.0.0.1`). Error 111 (`Connection refused`) means "no listener there", not a permission problem.

**Fix:** be explicit:

```text
proxy_pass http://127.0.0.1:8080;
```

```bash
sudo nginx -t && sudo systemctl reload nginx
```

Alternatively make the backend listen on both stacks, but explicit IPs in proxy configs are clearer.

**Prevention:** read the upstream address in the error log, not just the error text; prefer explicit loopback addresses in reverse-proxy configs; include a synthetic check through the proxy (not only the backend's health check) after every config change. Revise: Modules 04 and 10.
:::

### Drill 26 — "403 Forbidden, no AVC, labels are correct"

**Alert / symptom:** a static site is deployed to `/srv/sites/docs/public` on web05. nginx returns `403 Forbidden`. `ls -Z` shows `httpd_sys_content_t` on the files (fcontext rule added correctly), files are mode 644, and `ausearch -m AVC -ts recent` returns `<no matches>`.

**Questions to ask:**

- The error log says `(13: Permission denied)`. If SELinux isn't denying, what else can?
- Can the nginx user traverse every directory on the path?
- Who created the parent directories, and with what umask?

**Commands to run:**

```bash
sudo tail -2 /var/log/nginx/error.log
namei -l /srv/sites/docs/public/index.html
sudo -u nginx stat /srv/sites/docs/public/index.html
```

```text
open() "/srv/sites/docs/public/index.html" failed (13: Permission denied)
f: /srv/sites/docs/public/index.html
dr-xr-xr-x root   root   /
drwxr-xr-x root   root   srv
drwxr-x--- deploy deploy sites
drwxr-xr-x deploy deploy docs
drwxr-xr-x deploy deploy public
-rw-r--r-- deploy deploy index.html
stat: cannot statx '/srv/sites/docs/public/index.html': Permission denied
```

:::answer
**Root cause:** classic Unix permissions (DAC). `/srv/sites` is mode 750, owned by `deploy:deploy`, so the `nginx` user has no execute (traverse) permission on it and can't reach anything below it, however open the files are. Linux checks DAC first; when DAC denies, SELinux is never consulted, which is why there is no AVC.

**Fix:** give the web server traverse permission on the path, as narrowly as possible:

```bash
sudo chmod 755 /srv/sites                 # or: sudo setfacl -m u:nginx:x /srv/sites
sudo -u nginx stat /srv/sites/docs/public/index.html
curl -I http://localhost/
```

**Prevention:** use `namei -l` whenever "permission denied" looks impossible; test access as the service user (`sudo -u nginx ...`); set directory modes in the deploy role. The lesson for diagnosis: error 13 can be DAC **or** SELinux; `ausearch` tells you which. Revise: Modules 01 and 10.
:::

### Drill 27 — "The logs don't line up: the app says 14:05, the journal says 09:05"

**Alert / symptom:** during a postmortem, the team can't build a timeline. The load balancer logs the outage at 14:05, the application log on app08 at 14:05, but the journal on app08 shows the related errors at 09:05, and app09's journal at 15:05. Everyone is confused.

**Questions to ask:**

- Which time zone is each server configured with?
- Which time zone does each log write in?
- Is the clock itself wrong, or only its presentation?

**Commands to run:**

```bash
timedatectl
date; date -u
journalctl --utc -u myapp --since "2026-11-25 14:00" --until "2026-11-25 14:10" --no-pager | head
```

```text
               Local time: Tue 2026-11-25 09:12:01 EST
           Universal time: Tue 2026-11-25 14:12:01 UTC
                Time zone: America/New_York (EST, -0500)
System clock synchronized: yes
```

:::answer
**Root cause:** the clocks are correct (synchronised); the **time zones** differ. app08 was built with `America/New_York`, app09 with `Europe/Berlin`; the application logs in UTC regardless of the system zone. The journal stores timestamps in UTC internally but displays them in the server's local zone, so the same instant appears as 09:05, 14:05 and 15:05.

**Fix:** for the postmortem, view everything in UTC (`journalctl --utc`, `date -u`). Then standardise:

```bash
sudo timedatectl set-timezone UTC
```

Restart long-running services afterwards, since some read the zone only at start-up.

**Prevention:** set every server to UTC in the base image or Ansible (`community.general.timezone`); configure applications to log ISO 8601 with offsets (`2026-11-25T14:05:12Z`); state the zone in every incident note. Let people's desktops convert to local time, not the servers. Revise: Module 02.
:::

### Drill 28 — "Kerberos logins fail and TLS says the certificate is not yet valid"

**Alert / symptom:** on app10, domain users can't log in (`Clock skew too great` in the SSSD log) and an outbound HTTPS call fails with `certificate is not yet valid`. Other servers are fine.

**Questions to ask:**

- How far off is this server's clock?
- Is chrony running, and which sources can it reach?
- Was anything changed in the network path for NTP (UDP 123)?

**Commands to run:**

```bash
timedatectl
chronyc tracking
chronyc sources -v
sudo grep -E '^(server|pool)' /etc/chrony.conf
```

```text
System clock synchronized: no
Reference ID    : 00000000 ()
Stratum         : 0
System time     : 0.000000000 seconds fast of NTP time
Last offset     : +0.000000000 seconds
MS Name/IP address         Stratum Poll Reach LastRx Last sample
^? 2.rhel.pool.ntp.org           0   10     0     -     +0ns[   +0ns] +/-    0ns
```

:::answer
**Root cause:** chrony on app10 points at the public `rhel.pool.ntp.org` servers (the default config), but a firewall change blocked outbound UDP 123 from the app network weeks ago. `Reach 0` and `^?` mean no source has ever answered. With no time source, the clock drifted until it was more than 5 minutes off (Kerberos's default tolerance), and far enough that a newly issued certificate looked like it was "from the future".

**Fix:** point chrony at the internal NTP servers that are reachable, and step the clock once:

```bash
sudo sed -i 's/^pool .*/server ntp1.example.com iburst\nserver ntp2.example.com iburst/' /etc/chrony.conf
sudo systemctl restart chronyd
chronyc sources
sudo chronyc makestep
chronyc tracking | grep -E 'Reference|System time'
```

**Prevention:** manage `chrony.conf` centrally (the `rhel-system-roles.timesync` role); monitor `chronyc tracking` offset and `Leap status`, alerting when unsynchronised; keep NTP access in the firewall change review checklist. Revise: Modules 02 and 04.
:::

### Drill 29 — "Patching job times out: dnf is waiting for another process"

**Alert / symptom:** the weekly Ansible patch run times out on app11. On the server, `sudo dnf check-update` prints `Waiting for process with pid 1877 to finish.` and never proceeds.

**Questions to ask:**

- What is process 1877, how long has it been running, and what is it waiting for?
- Can this server reach its repositories (directly or through a proxy)?
- Is it safe to stop that process?

**Commands to run:**

```bash
ps -o pid,lstart,etime,cmd -p 1877
sudo cat /proc/1877/wchan; echo
sudo ss -tnp | grep 1877
sudo dnf repolist -v 2>&1 | tail -5     # after the lock is cleared
```

```text
  PID                  STARTED     ELAPSED CMD
 1877 Fri Nov 21 06:00:02 2026  4-03:12:44 /usr/bin/python3 /usr/bin/dnf-automatic /etc/dnf/automatic.conf --timer
ESTAB 0 0 10.0.0.31:51122 192.0.2.80:3128 users:(("dnf-automatic",pid=1877,fd=7))
```

:::answer
**Root cause:** `dnf-automatic` (the update timer) started four days ago and hung on a connection to the old proxy (`192.0.2.80:3128`), which was retired. dnf allows only one instance at a time, so every later dnf run, including Ansible's, waits for its lock. The real problem is the stale proxy setting.

**Fix:** stop the hung job cleanly, fix the proxy, refresh metadata:

```bash
sudo kill 1877                         # TERM first; it was only downloading metadata, not installing
sudo vi /etc/dnf/dnf.conf              # proxy=http://proxy.example.com:3128 (or remove)
sudo dnf clean all
sudo dnf makecache
sudo dnf check-update
```

Check `ps -o cmd -p PID` before killing: if the process is in the middle of an RPM **transaction** (installing packages), wait or investigate instead; killing it can leave half-installed packages. Do not delete lock or rpmdb files by hand; if the RPM database is genuinely damaged, follow the vendor procedure (`rpm --rebuilddb` after a backup of `/var/lib/rpm`).

**Prevention:** manage proxy settings centrally; set `timeout` in `dnf.conf`; monitor the age of the last successful dnf run; add a time limit to the timer's service (`RuntimeMaxSec=`). On Ubuntu the equivalent symptom is `Could not get lock /var/lib/dpkg/lock-frontend`, often held by `unattended-upgrades`. Revise: Modules 01 and 08.
:::

### Drill 30 — "The new cluster node joined, but the others can't talk to it"

**Alert / symptom:** app12 was cloned from app11's VM template to add a node to an application cluster. The app on app12 starts, but the other nodes log `connection refused from 127.0.0.1` / `node app11 already exists`. Requests routed to app12 fail.

**Questions to ask:**

- What hostname does app12 think it has?
- What does its hostname resolve to, locally and in DNS?
- Which address is the application advertising to the cluster?

**Commands to run:**

```bash
hostnamectl
hostname -I
getent hosts "$(hostname)"
cat /etc/hosts
```

```text
 Static hostname: app11.example.com
10.0.0.42
127.0.0.1   localhost localhost.localdomain app11.example.com app11
```

:::answer
**Root cause:** the template still contained app11's identity. The hostname was never changed, and `/etc/hosts` maps the hostname to `127.0.0.1`. Many clustered applications (and Java's `InetAddress.getLocalHost()`) advertise the address their own hostname resolves to, so app12 announced itself as `app11` at `127.0.0.1`, an address the other nodes can't reach and a name that already exists.

**Fix:**

```bash
sudo hostnamectl set-hostname app12.example.com
sudo sed -i '/app11/d' /etc/hosts
echo "10.0.0.42   app12.example.com app12" | sudo tee -a /etc/hosts
getent hosts app12.example.com
sudo systemctl restart myapp
```

Also check other per-host identities a clone carries: SSH host keys, `/etc/machine-id` (should be unique; regenerate with `rm /etc/machine-id && systemd-machine-id-setup` on a fresh clone), and any app node ID file.

**Prevention:** generalise templates before cloning (`virt-sysprep`, or cloud-init that sets the hostname at first boot); never map the real hostname to a loopback address on servers; verify `getent hosts $(hostname)` returns the primary IP as part of the build checks. Revise: Modules 01 and 04.
:::

:::lab Lab 11-B — Reproduce four drills on your VM
**Goal:** reproduce and fix one storage, one service, one networking and one SELinux drill end to end.

**Break it:** on a snapshot of your Rocky/Alma 9 VM with `httpd` installed:

```bash
# Drill 01: deleted-but-open file
sudo bash -c 'python3 -c "import time; f=open(\"/var/tmp/big.log\",\"w\"); f.write(\"x\"*500_000_000); f.flush(); time.sleep(3600)" &'
sleep 3; sudo rm /var/tmp/big.log
# Drill 15: runtime-only firewall rule
sudo firewall-cmd --add-port=9100/tcp
# Drill 02: wrong label via mv
echo '<h1>hello</h1>' > ~/index.html && sudo mv ~/index.html /var/www/html/index.html
sudo systemctl start httpd
```

**Symptoms:** `df` shows 500 MB more used than `du` finds; port 9100 disappears after `sudo firewall-cmd --reload`; `curl -I http://localhost/` returns 403.

**Diagnose:** `lsof +L1`; `firewall-cmd --list-ports` versus `--permanent --list-ports`; `ls -Z /var/www/html` and `ausearch -m AVC -ts recent`.

**Fix:** kill the Python process; add the port with `--permanent` and reload; `restorecon -v /var/www/html/index.html`.

**Verify:** `df` returns to normal; the port survives a reload; `curl` returns 200.

**Clean-up/revert:** revert the VM snapshot, or remove the port and stop httpd.
:::

## 7. Part C: final assessment

Forty scenario questions covering the whole course. Allow yourself **60 minutes**, no notes, no terminal. Then check your answers and note the module next to every question you missed.

| Score | What it means | Next step |
|---|---|---|
| 36–40 | Ready for on-call | Keep sharp with the 30-day plan; mentor someone |
| 28–35 | Solid, with gaps | Revise the listed modules, redo their labs, retake in a week |
| 20–27 | Foundations in place | Work through the 30-day plan fully before taking primary on-call |
| Below 20 | Not yet | Redo Modules 01–07 labs, then retake |

<div class="q" markdown="1">

**Q1.** A server has been "slow since this morning". Before restarting anything, which command gives you, in one line, whether it rebooted recently and how loaded it is?

A. `free -h`
B. `uptime`
C. `ss -tulpn`
D. `systemctl --failed`

:::answer
**Answer: B.** `uptime` shows time since boot and the 1/5/15-minute load averages. A is memory only; C shows listening sockets; D shows failed units. All are useful triage steps, but only `uptime` answers both questions. Revise: Module 01.
:::
</div>

<div class="q" markdown="1">

**Q2.** An application reports `No space left on device`. `df -h` shows 35% used on that filesystem. What do you check next?

A. `free -h`, because the message refers to memory
B. `df -i`, because the filesystem may have run out of inodes
C. `fsck` on the mounted filesystem
D. `du -sh /`, because `df` is often wrong

:::answer
**Answer: B.** "No space left on device" (ENOSPC) is returned when either blocks or inodes run out. A is wrong: it isn't a memory error. C is dangerous: never `fsck` a mounted read-write filesystem. D: `du` and `df` measure different things and `df` is not "wrong". Revise: Module 03.
:::
</div>

<div class="q" markdown="1">

**Q3.** `df` shows `/var` at 100% but `du -sh /var` totals only 12 GB of 30 GB. Which command most likely explains the difference?

A. `lsof +L1`
B. `findmnt --verify`
C. `ls -lZ /var`
D. `journalctl --vacuum-size=100M`

:::answer
**Answer: A.** Deleted files still held open by a process consume blocks that `du` can't see; `lsof +L1` lists them. B checks fstab syntax; C shows labels; D might free journal space but doesn't explain the discrepancy. Revise: Module 03.
:::
</div>

<div class="q" markdown="1">

**Q4.** `systemctl start myapp` fails. Which command shows the service's own log messages from the current boot?

A. `journalctl -u myapp -b`
B. `dmesg | grep myapp`
C. `cat /var/log/messages | tail`
D. `systemctl list-unit-files myapp`

:::answer
**Answer: A.** `-u` filters by unit and `-b` by the current boot. B shows only kernel messages; C may contain the messages mixed with everything else (and depends on rsyslog); D shows enablement state, not logs. Revise: Module 02.
:::
</div>

<div class="q" markdown="1">

**Q5.** After several failed restarts, `systemctl start nginx` fails immediately and status shows `start-limit-hit`. You have fixed the config. What must you do before starting it?

A. Reboot the server
B. `systemctl daemon-reexec`
C. `systemctl reset-failed nginx`
D. `systemctl unmask nginx`

:::answer
**Answer: C.** `reset-failed` clears the failure counter and state so systemd will try again. A works but is unnecessary; B re-executes systemd itself; D applies only to masked units. Revise: Module 02.
:::
</div>

<div class="q" markdown="1">

**Q6.** A cron job works by hand but fails from cron with `command not found`. What is the most likely cause?

A. crond is not running
B. cron runs with a minimal `PATH`
C. SELinux blocks all cron jobs by default
D. The script needs a `.sh` extension

:::answer
**Answer: B.** Cron's environment is minimal (typically `PATH=/usr/bin:/bin`); tools in `/usr/local/bin` or `/usr/sbin` aren't found. A would produce no run at all; C is false; D is irrelevant. Set `PATH` in the script or use absolute paths. Revise: Module 02.
:::
</div>

<div class="q" markdown="1">

**Q7.** You need to see only the error-level and worse messages from the **previous** boot, after an unexpected reboot. Which command?

A. `journalctl -p err -b -1`
B. `journalctl -p err -b`
C. `dmesg -T -l err`
D. `journalctl -k -f`

:::answer
**Answer: A.** `-b -1` selects the previous boot (needs a persistent journal, `/var/log/journal`). B is the current boot; C is the current kernel ring buffer only; D follows live kernel messages. Revise: Module 02.
:::
</div>

<div class="q" markdown="1">

**Q8.** A data disk's fstab entry has a typo. What should have been run after editing `/etc/fstab` to catch it **before** the reboot?

A. `systemctl daemon-reload` only
B. `findmnt --verify` and `mount -a`
C. `dracut -f`
D. `grub2-mkconfig`

:::answer
**Answer: B.** `findmnt --verify` checks fstab syntax and devices; `mount -a` tries the mounts now. A only reloads unit definitions; C rebuilds the initramfs; D rebuilds the GRUB config. Revise: Modules 03 and 09.
:::
</div>

<div class="q" markdown="1">

**Q9.** Which fstab option lets a server finish booting even if a non-critical data disk is missing?

A. `noatime`
B. `defaults`
C. `nofail`
D. `_netdev`

:::answer
**Answer: C.** `nofail` stops a missing device from failing `local-fs.target`. A reduces metadata writes; B is the default option set; D marks a mount as needing the network, which orders it after the network is up but doesn't make it optional. Revise: Module 03.
:::
</div>

<div class="q" markdown="1">

**Q10.** You need to grow the XFS filesystem on `/dev/rl/data` after extending its logical volume. Which is correct?

A. `resize2fs /dev/rl/data`
B. `xfs_growfs /data` (or `lvextend -r` in one step)
C. `xfs_repair -L /dev/rl/data`
D. Unmount and recreate the filesystem

:::answer
**Answer: B.** XFS grows online with `xfs_growfs` on the mount point; `lvextend -r` extends the LV and filesystem together. A is for ext4; C is a destructive repair tool; D destroys data. XFS cannot be shrunk. Revise: Module 03.
:::
</div>

<div class="q" markdown="1">

**Q11.** An app can't resolve `db01.example.com`, but `dig @10.0.0.2 db01.example.com` returns the right IP. What is most likely wrong?

A. The DNS server is down
B. The client's resolver configuration (for example, `/etc/resolv.conf` set by NetworkManager)
C. The firewall on db01
D. The database isn't listening

:::answer
**Answer: B.** The DNS server answers correctly when asked directly, so the client is asking the wrong server or searching the wrong domain. A is disproved by the `dig`; C and D are later layers. Fix with `nmcli connection modify ... ipv4.dns`. Revise: Module 04.
:::
</div>

<div class="q" markdown="1">

**Q12.** A port opened with `firewall-cmd --add-port=8443/tcp` disappears after a reload. Why?

A. firewalld has a bug
B. The rule was added only to the runtime configuration
C. The zone was wrong
D. SELinux removed the rule

:::answer
**Answer: B.** Without `--permanent`, rules exist only at runtime and are discarded on reload or reboot. Use `--permanent` then `--reload`, or `--runtime-to-permanent`. C would have failed immediately, not after reload; D is not something SELinux does. Revise: Module 04.
:::
</div>

<div class="q" markdown="1">

**Q13.** A remote client gets "connection refused" to port 5432. On the server, `ss -ltn` shows `127.0.0.1:5432`. What is the cause?

A. The client's DNS is wrong
B. The database listens only on the loopback interface
C. The SELinux port type is wrong
D. The server is out of memory

:::answer
**Answer: B.** A loopback-only listener can't accept connections from other hosts. Change `listen_addresses` (or the app's bind address), then check firewall and auth rules. A would fail before connecting; C would stop the service binding at all; D is unrelated. Revise: Modules 04 and 10.
:::
</div>

<div class="q" markdown="1">

**Q14.** Which command shows which process owns TCP port 8080?

A. `ss -ltnp 'sport = :8080'`
B. `netstat -r`
C. `ip addr show`
D. `firewall-cmd --list-ports`

:::answer
**Answer: A.** `ss -ltnp` shows listeners with their process (run as root to see all). B shows routes; C shows addresses; D shows firewall rules, not processes. Revise: Module 04.
:::
</div>

<div class="q" markdown="1">

**Q15.** A service fails with `Address already in use` (errno 98). What does this tell you?

A. SELinux blocked the bind
B. Another socket already holds that address and port
C. The firewall blocks the port
D. The port number is above 1024

:::answer
**Answer: B.** Errno 98 (`EADDRINUSE`) means the port is taken, often by an old instance or another service. A SELinux denial gives errno 13 (`Permission denied`); a firewall doesn't affect local binds; D is irrelevant. Find the owner with `ss -ltnp`. Revise: Module 04.
:::
</div>

<div class="q" markdown="1">

**Q16.** Load average is 25 on an 8-CPU server, but `top` shows CPUs 85% `wa`. Where is the bottleneck?

A. CPU
B. Storage or network filesystem I/O
C. Memory bandwidth
D. DNS

:::answer
**Answer: B.** High `wa` means CPUs are idle while waiting for I/O; Linux load counts `D`-state tasks waiting on disk or NFS. Use `iostat -xz 1` and `pidstat -d 1` to find the device and process. A is wrong because the CPUs are idle; C and D don't show as `wa`. Revise: Module 05.
:::
</div>

<div class="q" markdown="1">

**Q17.** A database process was killed overnight. Where do you look first to confirm the OOM killer did it?

A. `journalctl -k` (or `dmesg -T`) for "Out of memory: Killed process"
B. `/var/log/secure`
C. `ausearch -m AVC`
D. `last reboot`

:::answer
**Answer: A.** OOM kills are logged by the kernel. B logs authentication; C logs SELinux denials; D shows reboots, but the server didn't reboot. Revise: Module 05.
:::
</div>

<div class="q" markdown="1">

**Q18.** A batch job keeps triggering the OOM killer, which kills the database instead. What is the best systemd-level fix?

A. Disable swap
B. Set `MemoryMax=` on the batch job's service and `OOMScoreAdjust=` (negative) on the database
C. Set `vm.overcommit_memory=2` immediately on the production server
D. Restart the database every night

:::answer
**Answer: B.** Capping the job contains it in its cgroup; lowering the database's OOM score makes it a less likely victim. A makes OOM more likely; C changes allocation behaviour system-wide and can break applications without careful tuning; D treats the symptom. Revise: Module 05.
:::
</div>

<div class="q" markdown="1">

**Q19.** In `free -h`, which column best shows how much memory new applications can use without swapping?

A. `free`
B. `used`
C. `available`
D. `shared`

:::answer
**Answer: C.** `available` estimates memory usable without swapping, including reclaimable page cache. A ignores cache that can be dropped, so it looks alarmingly low on healthy servers; B and D don't answer the question. Revise: Module 05.
:::
</div>

<div class="q" markdown="1">

**Q20.** Which statement about SELinux modes is correct?

A. Permissive mode blocks and logs denials
B. Enforcing mode logs denials but allows them
C. Permissive mode logs denials but allows them; enforcing mode blocks and logs them
D. Disabled mode logs denials only

:::answer
**Answer: C.** Permissive is for diagnosis: it logs what would be denied. A and B swap the modes; D: disabled does nothing at all, and re-enabling from disabled requires a full relabel. Revise: Module 06.
:::
</div>

<div class="q" markdown="1">

**Q21.** Files moved with `mv` from `/home/dev` into `/var/www/html` give 403, with AVCs showing `user_home_t`. What is the correct fix?

A. `chmod -R 777 /var/www/html`
B. `restorecon -Rv /var/www/html`
C. `audit2allow -M webfix` and load the module
D. `setenforce 0`

:::answer
**Answer: B.** The policy already has the right default for `/var/www/html`; `restorecon` applies it. A is a security hole that won't fix a label problem; C would allow the web server to read home directories; D disables protection. Revise: Modules 06 and 07.
:::
</div>

<div class="q" markdown="1">

**Q22.** You moved a website to `/srv/web`. Which pair of commands labels it correctly and persistently?

A. `chcon -R -t httpd_sys_content_t /srv/web`
B. `semanage fcontext -a -t httpd_sys_content_t "/srv/web(/.*)?"` then `restorecon -Rv /srv/web`
C. `restorecon -Rv /srv/web` only
D. `setsebool -P httpd_read_srv on`

:::answer
**Answer: B.** `semanage fcontext` adds a rule to the policy's file-context database; `restorecon` applies it. A works until the next relabel or `restorecon`; C applies the current default, which for an unknown path doesn't help; D is not a real boolean. Revise: Module 07.
:::
</div>

<div class="q" markdown="1">

**Q23.** nginx fails to start after changing to `listen 8095;` with `bind() ... failed (13: Permission denied)`. What is the fix?

A. `semanage port -a -t http_port_t -p tcp 8095`
B. `firewall-cmd --permanent --add-port=8095/tcp`
C. Run nginx as root
D. `setsebool -P httpd_can_network_connect on`

:::answer
**Answer: A.** The bind is denied because 8095 isn't an `http_port_t` port. B is needed for remote clients but doesn't fix a local bind; C is wrong (the master already starts as root; the denial is SELinux); D governs **outbound** connections, not binding. Revise: Module 07.
:::
</div>

<div class="q" markdown="1">

**Q24.** nginx logs `connect() to 127.0.0.1:8080 failed (13: Permission denied) while connecting to upstream`. `audit2why` points at a boolean. Which is it most likely to be?

A. `httpd_enable_homedirs`
B. `httpd_can_network_connect`
C. `httpd_use_nfs`
D. `allow_ypbind`

:::answer
**Answer: B.** It allows web server domains to make outbound TCP connections. A allows serving `~user/public_html`; C allows reading NFS content; D concerns NIS. Always confirm with `audit2why` and prefer the narrowest boolean it suggests. Revise: Module 07.
:::
</div>

<div class="q" markdown="1">

**Q25.** You must set a boolean so it survives a reboot. Which command?

A. `setsebool httpd_use_nfs 1`
B. `setsebool -P httpd_use_nfs 1`
C. `echo 1 > /sys/fs/selinux/booleans/httpd_use_nfs`
D. `getsebool -P httpd_use_nfs on`

:::answer
**Answer: B.** `-P` writes the value to the policy store. A and C change only the running value; D isn't valid (`getsebool` reads values). Revise: Module 06.
:::
</div>

<div class="q" markdown="1">

**Q26.** SELinux is clearly involved (the action works only in permissive mode for that domain), but `ausearch -m AVC` shows nothing. What do you try?

A. `semodule -DB`, reproduce, check `ausearch`, then `semodule -B`
B. Set `SELINUX=disabled` and reboot
C. `restorecon -R /`
D. `audit2allow -a -M everything`

:::answer
**Answer: A.** `dontaudit` rules can hide denials; `-DB` rebuilds policy without them, and `-B` restores them. Also confirm `auditd` is running. B removes protection and needs a relabel later; C relabels everything blindly; D creates an overly broad module from unrelated denials. Revise: Module 06.
:::
</div>

<div class="q" markdown="1">

**Q27.** Which command explains in plain language why a logged denial happened and whether a boolean would fix it?

A. `sealert -l` or `audit2why`
B. `semanage login -l`
C. `sestatus -b`
D. `seinfo --stats`

:::answer
**Answer: A.** `audit2why` (and `sealert` from setroubleshoot) interpret AVCs and suggest booleans or label fixes. B lists login-to-SELinux-user mappings; C shows status and booleans; D shows policy statistics. Revise: Module 06.
:::
</div>

<div class="q" markdown="1">

**Q28.** When is generating a custom policy module with `audit2allow -M` appropriate?

A. Always, as the first fix for any denial
B. After ruling out labels, port types and booleans, and after reviewing the generated `.te` rules for a legitimate, narrow need
C. Never; custom modules are unsupported
D. Only when SELinux is disabled

:::answer
**Answer: B.** Most denials are fixed with a label, port type or boolean. A custom module is valid for genuine gaps (for example a custom application), but you must read the rules first. A risks granting too much; C is false; D makes no sense. Revise: Modules 06 and 07.
:::
</div>

<div class="q" markdown="1">

**Q29.** Web content on an NFS mount returns 403 with AVCs showing `nfs_t`. `restorecon` changes nothing. What is the fix?

A. `setsebool -P httpd_use_nfs on` (or mount with a `context=` option)
B. `chcon -t httpd_sys_content_t` on the NFS files
C. `semanage fcontext` for the NFS path, then `restorecon`
D. `chmod 755` on the export

:::answer
**Answer: A.** Without labelled NFS, files on the client appear as `nfs_t` and can't be relabelled from the client. The boolean or a mount `context=` solves it. B and C fail for that reason; D is a DAC fix for a MAC problem. Revise: Module 07.
:::
</div>

<div class="q" markdown="1">

**Q30.** You change sshd to listen on port 2222 on RHEL 9. Which three changes are needed?

A. `sshd_config`, `semanage port -a -t ssh_port_t -p tcp 2222`, and a permanent firewalld rule
B. `sshd_config` only
C. `sshd_config` and `setenforce 0`
D. `sshd_config`, `/etc/services`, and a reboot

:::answer
**Answer: A.** The service, the SELinux port type, and the firewall. B leaves sshd unable to bind (and with port 22 gone, you're locked out); C disables protection; D: `/etc/services` doesn't control SELinux or the firewall, and no reboot is needed. Revise: Modules 07 and 08.
:::
</div>

<div class="q" markdown="1">

**Q31.** sshd logs `Authentication refused: bad ownership or modes for directory /home/alice/.ssh`. What is the fix?

A. Set `StrictModes no`
B. `chown -R alice:alice ~alice/.ssh; chmod 700 ~alice/.ssh; chmod 600 ~alice/.ssh/authorized_keys`
C. `chmod 777 ~alice/.ssh`
D. Regenerate the server's host keys

:::answer
**Answer: B.** sshd requires the user's `.ssh` and key file to be owned by the user and not writable by others. A weakens security; C makes the problem worse; D is unrelated to user key authentication. Also run `restorecon -Rv ~alice/.ssh`. Revise: Module 08.
:::
</div>

<div class="q" markdown="1">

**Q32.** Every sudo command fails with `parse error in /etc/sudoers.d/ops`. Root SSH login is disabled. What is the best route to fix it?

A. Delete `/etc/sudoers` with your normal account
B. `pkexec visudo -f /etc/sudoers.d/ops`, or console/`rd.break` if polkit isn't available
C. Wait for sudo to reset itself
D. Reinstall the sudo package with dnf

:::answer
**Answer: B.** polkit's `pkexec` offers an independent path to root for authorised users; otherwise use console or recovery access. A is impossible without root (and destructive); C won't happen; D needs root, which you don't have. Revise: Modules 08 and 09.
:::
</div>

<div class="q" markdown="1">

**Q33.** A user is locked out after too many failed logins on RHEL 9. Which command shows and resets the lock?

A. `passwd -S` and `passwd -u`
B. `faillock --user alice` and `faillock --user alice --reset`
C. `pam_tally2 --reset`
D. `usermod -U alice` only

:::answer
**Answer: B.** RHEL 8 and 9 use `pam_faillock`. A and D deal with a locked **password** (`!` in shadow), a different lock; C is the obsolete tool removed in newer releases. Revise: Module 08.
:::
</div>

<div class="q" markdown="1">

**Q34.** A server stops in emergency mode after a reboot. What is the first command to see why?

A. `journalctl -xb` (and `systemctl --failed`)
B. `fdisk -l`
C. `reboot`
D. `dnf update`

:::answer
**Answer: A.** The boot journal shows which unit failed (often a mount). B lists disks but not the failure; C repeats the problem; D isn't relevant and may not work without network or a writable root. Revise: Module 09.
:::
</div>

<div class="q" markdown="1">

**Q35.** You re-enable SELinux on a server where it had been `disabled` for months. What must happen before or during the next boot to avoid widespread denials?

A. Nothing; labels are maintained while disabled
B. A full relabel, for example `touch /.autorelabel` (with permissive first for a safer transition)
C. `setsebool -P` for all booleans
D. Reinstall the policy package

:::answer
**Answer: B.** Files created while SELinux was disabled have no labels, so the filesystem must be relabelled; switching to permissive first lets you check for remaining denials before enforcing. A is false; C and D don't label files. Revise: Modules 06 and 09.
:::
</div>

<div class="q" markdown="1">

**Q36.** After a cert renewal, browsers work but `curl` and Java clients report `unable to get local issuer certificate`. What is the most likely cause?

A. The private key doesn't match
B. The server isn't sending the intermediate CA certificate
C. The server's clock is wrong
D. TLS 1.3 is disabled

:::answer
**Answer: B.** Browsers can complete the chain themselves; most other clients can't. Serve the full chain. A would stop the server from starting (or break every client); C would affect all clients and give validity errors; D wouldn't cause an issuer error. Revise: Module 10.
:::
</div>

<div class="q" markdown="1">

**Q37.** nginx returns 502 with `connect() failed (111: Connection refused) while connecting to upstream`. What does 111 point at?

A. An SELinux denial
B. Nothing is listening at the upstream address and port nginx used
C. The upstream is too slow (timeout)
D. The client sent a bad request

:::answer
**Answer: B.** 111 (`ECONNREFUSED`) means the connection was actively refused: backend down, wrong port, or wrong address (for example `::1` versus `127.0.0.1`). A gives 13; C gives 110 (`timed out`) and usually a 504; D gives a 4xx. Revise: Module 10.
:::
</div>

<div class="q" markdown="1">

**Q38.** nginx returns 403 for a file with correct SELinux labels, and there are no AVCs. What tool quickly shows the permissions of every directory on the path?

A. `namei -l /path/to/file`
B. `ls -Z /path/to/file`
C. `getfacl -R /`
D. `sestatus`

:::answer
**Answer: A.** A missing execute bit on any parent directory blocks access; `namei -l` shows each component's owner and mode. B shows only labels of the final file; C is far too broad; D shows SELinux status. Revise: Modules 01 and 10.
:::
</div>

<div class="q" markdown="1">

**Q39.** Kerberos logins fail with "Clock skew too great". `chronyc sources` shows `^?` with Reach 0 for every source. What is the likely root cause?

A. The Kerberos server is down
B. chrony can't reach any NTP source (for example UDP 123 blocked), so the clock drifted
C. The time zone is wrong
D. The hardware clock battery is flat

:::answer
**Answer: B.** Reach 0 means no replies were ever received. Fix the source or the firewall, then `chronyc makestep`. A would give a different error; C changes the display, not the UTC clock that Kerberos compares; D affects only the time at boot, and chrony would correct it if it could sync. Revise: Modules 02 and 04.
:::
</div>

<div class="q" markdown="1">

**Q40.** `dnf update` prints `Waiting for process with pid 1877 to finish.` for minutes. What do you do first?

A. Delete the files in `/var/lib/rpm`
B. Inspect PID 1877 (`ps -o pid,etime,cmd -p 1877`) to see what it is and whether it is stuck or mid-transaction
C. `kill -9` every python process
D. Reboot immediately

:::answer
**Answer: B.** Identify the lock holder before acting; a stuck metadata download is safe to stop with SIGTERM, an active RPM transaction is not. A can destroy the RPM database; C is indiscriminate; D may interrupt a transaction and leave packages half-installed. Revise: Modules 01 and 08.
:::
</div>

## 8. Part D: the 30-day practice plan

Knowledge fades; reflexes stay. This plan takes 30–60 minutes a day on your lab VM. Take a snapshot before each session and revert afterwards. Every session starts the same way: **run `triage`, write incident notes, then fix**.

| Day | Focus | Practice | Done when |
|---|---|---|---|
| 1 | Setup | Build a fresh Rocky/Alma 9 VM, install `httpd`, `nginx`, `postgresql-server`, `setroubleshoot-server`, `sysstat`, `policycoreutils-python-utils`; install the `triage` script | `sudo triage` produces a complete file |
| 2 | Triage reflex | Run the checklist by hand five times on a healthy VM; learn what "normal" looks like | You can recite the nine checks in order |
| 3 | Disk | Drills 01 and 07 | You fix both in under 10 minutes |
| 4 | Storage boot risk | Drill 14 (break fstab, recover from emergency mode) | You recover without a rescue image |
| 5 | systemd | Drills 03 and 18; write one service unit from scratch with hardening options | `systemd-analyze verify` is clean |
| 6 | Scheduling | Drill 04; convert the job to a systemd timer | The timer shows in `systemctl list-timers` |
| 7 | Review | Retake the Q1–Q10 block of the final assessment; write a postmortem for Drill 14 | Postmortem has 3 concrete action items |
| 8 | DNS | Drill 06; practise `getent`, `dig`, `nmcli` | DNS survives a reboot |
| 9 | Firewall | Drill 15; build a rich rule allowing one source IP | Rule survives `--reload` |
| 10 | Ports and listeners | Drills 10 and 25 | You can explain errors 13, 98, 110, 111 |
| 11 | Time | Drills 27 and 28 | VM is UTC and `chronyc tracking` is synchronised |
| 12 | Hostname | Drill 30 using a clone of your VM | `getent hosts $(hostname)` returns the real IP |
| 13 | Performance | Drill 12 (generate I/O with `dd` or `fio`), read `vmstat`, `iostat`, `pidstat` | You identify the I/O process in under 2 minutes |
| 14 | Memory | Drill 13 (use `stress-ng --vm` from EPEL, or a small Python script); apply `MemoryMax` | The capped service is OOM-killed, not the database |
| 15 | Review | Q11–Q20 of the final assessment; teach one drill to a colleague | Score 9/10 or better |
| 16 | SELinux basics | Modes, contexts, `ls -Z`, `ps -Z`, `audit2why`; Drill 02 | You fix it without `audit2allow` |
| 17 | SELinux file contexts | Drills 17 and 22 | `semanage fcontext -l -C` shows your rules |
| 18 | SELinux ports | Drills 09 and 19 (keep a second session open) | Both ports work after a reboot |
| 19 | SELinux booleans | Drills 05, 11 and 23 | You pick the narrowest boolean each time |
| 20 | SELinux hidden denials | Drill 21; practise `semodule -DB` / `-B` | dontaudit is back on at the end |
| 21 | SELinux custom module | Write a tiny custom service that writes to an unlabelled path; fix with fcontext, then try `audit2allow -M` and **read** the `.te` file | You can explain each generated rule |
| 22 | Review | Q20–Q30 of the final assessment; export your SELinux changes with `semanage export` | Score 9/10 or better |
| 23 | Access | Drills 08 and 16 (practise `pkexec visudo`) | You recover sudo without the root password |
| 24 | Accounts | Lock a user with `faillock`, unlock, audit `/var/log/secure` | You find the source IP of failed logins |
| 25 | Boot recovery | Reset a forgotten root password with `rd.break`, including the relabel step | VM boots enforcing with no denials |
| 26 | Web stack | Drills 24 and 26 | `openssl s_client` shows `Verify return code: 0 (ok)` |
| 27 | Packages | Drill 29 (simulate with a bad `proxy=` line and a hung `dnf makecache`) | dnf works and the proxy is managed in one place |
| 28 | Automation | Put your fixes into an Ansible role: fcontext, port, boolean, firewall, fstab with `nofail`, sudoers with `validate` | A fresh VM is correct after one playbook run |
| 29 | Game day | Lab 11-A with a partner: five random drills, timed | Average time to fix under 15 minutes |
| 30 | Final | Retake the full 40-question assessment; write your personal runbook (one page) | Score 36+ and a runbook you'd hand to a new hire |

:::tip
Keep a "drill journal": for each session, the drill, your first hypothesis, whether it was right, and your time. After 30 days you will see which areas you still guess at. Those are the areas to revise before your next on-call week.
:::

## 9. Command cheat sheet

| Task | Command |
|---|---|
| Uptime and load | `uptime`, `nproc` |
| Kernel messages | `dmesg -T \| tail -50`, `journalctl -k -b` |
| Errors since boot / previous boot | `journalctl -p err -b`, `journalctl -p err -b -1` |
| Service status and logs | `systemctl status UNIT`, `journalctl -u UNIT -b -n 50` |
| Failed units / reset | `systemctl --failed`, `systemctl reset-failed UNIT` |
| Disk bytes and inodes | `df -hT`, `df -i` |
| Biggest directories | `du -xh --max-depth=1 /var \| sort -h` |
| Deleted but open files | `lsof +L1` |
| Memory | `free -h`, `ps aux --sort=-rss \| head` |
| CPU and I/O | `top`, `vmstat 1 5`, `iostat -xz 1 3`, `pidstat -d 1` |
| Listeners and owners | `ss -tulpn`, `ss -ltnp 'sport = :8080'` |
| Connectivity | `nc -zv HOST PORT`, `curl -v URL`, `ip route get IP` |
| Name resolution | `getent hosts NAME`, `dig NAME`, `nmcli -g IP4.DNS device show eth0` |
| Firewall | `firewall-cmd --list-all`, `firewall-cmd --permanent --add-port=P/tcp && firewall-cmd --reload` |
| SELinux status and denials | `getenforce`, `ausearch -m AVC -ts recent \| audit2why` |
| SELinux labels | `ls -Z`, `semanage fcontext -a -t TYPE "PATH(/.*)?"`, `restorecon -Rv PATH` |
| SELinux ports and booleans | `semanage port -a -t TYPE -p tcp N`, `setsebool -P BOOL on` |
| Hidden denials | `semodule -DB` ... `semodule -B` |
| Local SELinux changes | `semanage export`, `semanage boolean -l -C` |
| Path permissions | `namei -l PATH`, `sudo -u USER stat PATH` |
| sudoers | `visudo -c`, `visudo -f /etc/sudoers.d/FILE`, `pkexec visudo` |
| fstab check | `findmnt --verify`, `mount -a` |
| TLS chain and dates | `openssl s_client -connect H:443 -servername H -showcerts </dev/null` |
| Time | `timedatectl`, `chronyc tracking`, `chronyc sources -v` |
| Hostname | `hostnamectl`, `getent hosts $(hostname)` |
| dnf lock / repos | `ps -o pid,etime,cmd -p PID`, `dnf repolist -v`, `dnf clean all` |

:::summary
- Run the **five-minute triage checklist** before you change anything: uptime, kernel log, journal errors, disk bytes and inodes, memory, top, listeners, failed units, SELinux denials. Save the output; it is your evidence.
- **Mitigate first, root-cause second**, one change at a time, every change written in the incident notes.
- Communicate on a **fixed cadence** in user terms: impact, what you know, what you're doing, next update time, all in UTC.
- Write **blameless postmortems** with specific, owned, verifiable action items that change the system, not people.
- Error numbers are clues: **13** permission (DAC or SELinux; `ausearch` decides), **98** port in use, **111** nothing listening, **110** timeout.
- SELinux fixes are always one of four: **restorecon** (wrong label, correct policy), **semanage fcontext + restorecon** (new path), **semanage port** (new port), **setsebool -P** (optional feature). A reviewed custom module is the rare fifth. `setenforce 0` is a diagnostic, never a fix.
- Most incidents repeat. Turn every fix into **automation** (Ansible validation, fcontext, ports, booleans, `nofail`, firewall `--permanent`) and every surprise into a **monitoring check**.
:::
