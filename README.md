# Linux Production Troubleshooting — Commands, Fixes & Real Scenarios (with SELinux)

A hands-on course for engineers who run Linux servers in production (RHEL 9 / Rocky / AlmaLinux, with Ubuntu notes). Not a certification course: it teaches you how to find out **why** a server is broken, fix it properly, and stop it happening again — including how to fix SELinux problems **without** disabling SELinux.

**Start here:** [`00-start-here.pdf`](00-start-here.pdf), or read everything in one file: [`Linux-Production-Troubleshooting-Complete-Guide.pdf`](Linux-Production-Troubleshooting-Complete-Guide.pdf).

## Who this is for

Beginner-to-intermediate Linux admins, DevOps/SRE engineers and anyone joining an on-call rotation. You need one disposable Rocky/Alma 9 VM (UTM, VirtualBox, VMware or a free cloud VM) — Module 00 shows how to build it and snapshot it.

## Learning plan (6 weeks, about 1 hour a day)

| Week | Modules | Milestone |
|---|---|---|
| 1 | 00–02 | Lab VM ready; read `systemctl status` and `journalctl`; write a service unit |
| 2 | 03–04 | Fix a full disk, extend LVM, recover a bad `fstab`; trace network problems layer by layer |
| 3 | 05 | Find what is slowing a server down; read an OOM kill |
| 4 | 06–07 | SELinux: read any AVC denial and fix it the right way (20 real scenarios) |
| 5 | 08–09 | SSH/sudo/lockout problems; reset a root password; recover an unbootable server |
| 6 | 10–11 | Web/app stack (403/502/504, sockets, TLS), then 30 incident drills and the final assessment |

| Module | Topic |
|---|---|
| 00 | Start here: troubleshooting method, first 5 minutes on a broken server, lab setup |
| 01 | Linux essentials for production |
| 02 | systemd, services & logs |
| 03 | Storage & filesystems |
| 04 | Networking troubleshooting |
| 05 | Performance troubleshooting |
| 06 | SELinux fundamentals |
| 07 | SELinux in production: 20 real scenarios |
| 08 | Security & access troubleshooting |
| 09 | Boot problems & recovery |
| 10 | Web & application stack troubleshooting |
| 11 | Incident drills & final assessment |

## How each module works

Concept → commands (with real output) → **In Production** incident story → troubleshooting runbook table → **break-fix labs** (break your VM on purpose, then diagnose and fix it) → self-check questions.

> Only run the break-fix labs on a disposable lab VM — never on a work or shared server.

## Rebuilding the PDFs

Sources are Markdown in `_src/`. To rebuild (needs Python 3 and Google Chrome):

```bash
python3 -m venv _tools/.venv && _tools/.venv/bin/pip install markdown
_tools/.venv/bin/python _tools/mkpdf.py
```

## Disclaimer

Independent study material. Commands were written for RHEL 9-family systems; always test on a lab machine first.
