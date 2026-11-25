<div class="cover" markdown="1">
<div class="mod">Module 00 · Start Here</div>

# Linux Production Troubleshooting — Course Guide & Lab Setup

<p>How this course works, the troubleshooting mindset, a 6-week learning plan, and building a safe break-fix lab VM you can wreck and restore as often as you like.</p>
</div>

## 1. Welcome — what this course is (and isn't)

This is **not** a certification course. It's the course I wish I'd had on my first on-call rotation: how to find out *why* a Linux server is broken, fix it properly, and stop it happening again.

You'll learn:

- The **commands** that matter in production, and how to read their output.
- **Real scenarios**: disk full, service won't start, app can't reach the database, 502 Bad Gateway, server won't boot, the OOM killer, locked-out users.
- **SELinux**, properly. Most engineers' answer to SELinux is `setenforce 0`. By the end of this course you'll diagnose an SELinux denial in two minutes and fix it the right way — with a label, a boolean, a port rule or a reviewed policy module — without turning protection off.

The course is written for **RHEL 9 and its rebuilds (Rocky Linux, AlmaLinux)**, which is what most enterprise servers run. Where Ubuntu/Debian differs (apt, AppArmor, ufw) you'll see a short note.

Every module follows the same pattern:

1. **Concept** — how the piece of Linux works.
2. **Commands** — with real output and how to read it.
3. **In Production** — an incident story: the symptom, what the engineer saw, the root cause, the fix, and the prevention.
4. **Troubleshooting runbook** — a table: Symptom → Check → Likely cause → Fix.
5. **Break-fix labs** — you *break* your VM on purpose, then diagnose and fix it.
6. **Self-check questions** — scenario questions with explanations.

:::concept
Blue boxes explain how something works.
:::

:::prod
Purple boxes are real incident stories.
:::

:::tip
Amber boxes are pro tips from production.
:::

:::warn
Red boxes are warnings: commands that can lose data, lock you out, or open a security hole.
:::

Dark **Terminal** boxes are commands. Light boxes are command output or config files.

## 2. The troubleshooting mindset

Good troubleshooters aren't people who have memorised every error. They follow a method:

| Step | What you do | Example |
|---|---|---|
| 1. **Observe** | Get the exact symptom, time and scope. Who is affected? Since when? What changed? | "Users get 502 since 14:05, right after the deploy." |
| 2. **Gather evidence** | Logs, status, metrics — before touching anything | `systemctl status`, `journalctl -u`, `ausearch -m AVC` |
| 3. **Hypothesise** | One likely cause at a time | "The app can't bind its port." |
| 4. **Test** | Prove or disprove it cheaply | `ss -tulpn \| grep 8080` |
| 5. **Fix** | The smallest change that fixes the root cause | `semanage port -a -t http_port_t -p tcp 8080` |
| 6. **Verify** | Confirm the symptom is gone for users, not just for you | `curl -I https://app.example.com` |
| 7. **Document & prevent** | Write it down; automate the check | Runbook entry, monitoring alert, Ansible change |

:::tip
**"What changed?"** is the most powerful question in operations. Check `dnf history`, `last`, `journalctl --since "1 hour ago"`, recent deploys and config management runs before you start guessing.
:::

### The first five minutes on any broken server

```bash
uptime                          # how long up, load average
dmesg -T | tail -20             # kernel errors: disk, OOM, hardware
journalctl -p err -b --no-pager | tail -30   # errors since boot
systemctl --failed              # failed services
df -h; df -i                    # disk space and inodes
free -h                         # memory
top -b -n1 | head -20           # what is using CPU/memory
ss -tulpn                       # what is listening
getenforce; ausearch -m AVC -ts recent 2>/dev/null | tail   # SELinux denials
```

You'll understand every line of this by Module 05, and Module 11 turns it into a full on-call checklist.

## 3. Your 6-week learning plan

Plan for about **1 hour a day**, and do every break-fix lab.

| Week | Modules | Milestone |
|---|---|---|
| **1** | 00, 01, 02 | Lab VM ready; you can read `systemctl status` and `journalctl` and write a service unit |
| **2** | 03, 04 | Fix a full disk, extend LVM, recover a bad `fstab`; trace a network problem layer by layer |
| **3** | 05 | Find what is slowing a server down; read an OOM kill |
| **4** | 06, 07 | SELinux: read any AVC denial and fix it the right way (the most important week) |
| **5** | 08, 09 | SSH/sudo/lockout problems; reset a root password and recover an unbootable server |
| **6** | 10, 11 | Web/app stack troubleshooting, then the 30 incident drills and final assessment |

## 4. Build your break-fix lab (do this once)

You need one disposable **Rocky Linux 9** or **AlmaLinux 9** virtual machine (or RHEL 9 with Red Hat's free Developer Subscription). Choose one option:

| Option | Good for | Notes |
|---|---|---|
| **UTM** (Mac, free) | Apple Silicon Macs | Download the *aarch64* (ARM) ISO for M-series Macs |
| **VirtualBox / VMware Workstation** | Intel Macs, Windows, Linux | Use the *x86_64* ISO |
| **Cloud VM** (OCI Always Free, AWS/Azure free tier) | No local resources | Snapshots may cost money; some boot labs need the serial console |
| **Vagrant** | Quick rebuilds | `vagrant init generic/rocky9` (VirtualBox/libvirt) |

Recommended VM size: **2 vCPU, 2–4 GB RAM, 20 GB disk**, plus a **second 5 GB disk** for the storage/LVM labs (Module 03).

:::lab Lab 00-A — Install and prepare the lab VM
1. Install Rocky/Alma 9 with the **Server** or **Minimal Install** profile. Create a normal user and tick **Make this user administrator** (adds it to `wheel`).
2. Log in and update:

```bash
sudo dnf -y update
sudo dnf -y install vim bash-completion tar lsof strace tcpdump bind-utils \
  nmap-ncat sysstat policycoreutils-python-utils setroubleshoot-server \
  setools-console httpd nginx stress-ng
sudo systemctl enable --now sysstat
sudo reboot
```

3. Confirm SELinux is enforcing — it must be for this course:

```bash
getenforce
sestatus | head -5
```

What you should see:

```text
Enforcing
SELinux status:                 enabled
SELinuxfs mount:                /sys/fs/selinux
SELinux root directory:         /etc/selinux
Loaded policy name:             targeted
Current mode:                   enforcing
```

4. Set up SSH from your laptop to the VM with a key (`ssh-copy-id user@vm-ip`) so you can paste commands easily.
:::

:::lab Lab 00-B — Take a snapshot (your undo button)
Before any break-fix lab, take a VM snapshot named `clean-baseline` (UTM: **Clone** or snapshot via disk image; VirtualBox: **Machine → Take Snapshot**; cloud: boot-volume backup).

If a lab goes wrong and you can't fix it, **restore the snapshot** and try again. Breaking things safely is the whole point.
:::

:::warn
**Never run break-fix labs on a work server or anything shared.** Several labs deliberately break booting, SSH, disks and SELinux labels. Use only your disposable lab VM.
:::

## 5. Course map

| Module | Topic | Key skills |
|---|---|---|
| 01 | Linux essentials for production | Filesystem layout, users, permissions, ACLs, processes, packages |
| 02 | systemd, services & logs | Unit files, failing services, journald, cron vs timers, logrotate |
| 03 | Storage & filesystems | Disk full, inodes, deleted-open files, LVM extend, fstab, NFS |
| 04 | Networking | ip, nmcli, DNS, ss, firewalld, TLS checks, tcpdump, SELinux ports |
| 05 | Performance | Load, CPU, memory, OOM killer, I/O, open-file limits |
| 06 | SELinux fundamentals | Modes, contexts, labels, booleans, ports, AVC logs, audit2why |
| 07 | SELinux in production | 20 real scenarios with exact fixes |
| 08 | Security & access | SSH, sudo, PAM lockouts, crypto-policies, auditd, patching |
| 09 | Boot & recovery | GRUB, rescue/emergency mode, root password reset, kernels |
| 10 | Web & app stack | Apache, Nginx, 403/502/504, reverse proxies, sockets, logs |
| 11 | Incident drills & final assessment | 30 drills, on-call checklist, postmortem template, 40 questions |

:::summary
- Follow the method: observe → evidence → hypothesis → test → fix → verify → document.
- Always ask "what changed?"
- Use a disposable Rocky/Alma 9 VM with SELinux **enforcing**, and snapshot it before each lab.
- Never fix SELinux problems by disabling SELinux — this course shows the right fix every time.
:::

## 6. Self-check questions

<div class="q" markdown="1">

**Q1.** A web app started returning errors 10 minutes ago. What should you do first?

A. Restart the server to clear the problem
B. Disable SELinux and the firewall to rule them out
C. Collect evidence: the exact error, logs since the start time, and what changed recently
D. Reinstall the web server package

:::answer
**Answer: C.** Evidence first. Restarting (A) destroys evidence and may not fix anything. Disabling security controls (B) creates new risk and hides the real cause. Reinstalling (D) is a guess, not a diagnosis.
:::
</div>

<div class="q" markdown="1">

**Q2.** Which command quickly shows recent SELinux denials?

A. `getenforce`
B. `ausearch -m AVC -ts recent`
C. `sestatus`
D. `setenforce 0`

:::answer
**Answer: B.** `ausearch -m AVC -ts recent` lists access-vector-cache (AVC) denials from the audit log in the last 10 minutes. `getenforce` and `sestatus` (A, C) show the mode, not denials. `setenforce 0` (D) switches to permissive mode — never a first step.
:::
</div>

**Next:** Module 01, Linux Essentials for Production.
