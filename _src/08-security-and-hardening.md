<div class="cover" markdown="1">
<div class="mod">Module 08 · Security & Access Troubleshooting</div>

# Security and Access Troubleshooting: SSH, sudo, PAM, auditd, Patching and Hardening

<p>Why users can't log in, why sudo says no, who changed that file at 3 a.m., and how to close vulnerability scan findings properly, without breaking access and without ever turning SELinux off.</p>
</div>

## What you'll learn

- How SSH authentication really works on RHEL 9, and how to read **`sshd -t`**, **`sshd -T`**, **`ssh -vvv`** and the server journal to find out why a login fails
- The permission and **SELinux label** rules for `~/.ssh` and `authorized_keys` (`ssh_home_t`), and why a perfectly correct key gets refused
- **`sshd_config.d` drop-ins**, the "first value wins" rule, `PermitRootLogin`, `PasswordAuthentication`, and the "host key has changed" warning
- **System-wide crypto policies** (`update-crypto-policies`, DEFAULT / FUTURE / LEGACY, subpolicies) and how to remediate weak KEX, cipher and MAC findings
- **sudo**: `visudo`, `/etc/sudoers.d`, the `wheel` group, `sudo -l`, and the real risks of `NOPASSWD` and shell-escaping commands
- **PAM** basics with `authselect`, account lockout with **`pam_faillock`** and `faillock --user`, and password aging with **`chage`**
- **auditd**: watch rules with `auditctl -w`, persistent rules in `/etc/audit/rules.d`, and answering "who changed this?" with `ausearch -k`
- **Patching**: `dnf updateinfo`, security-only updates, `needs-restarting -r`, and kernel updates
- **File integrity** with `rpm -Va` and AIDE, cutting attack surface (services and ports), fail2ban, and closing scanner findings such as weak SSH ciphers and plain HTTP on port 80
- A sensible, CIS-style **hardening checklist**, and four break-fix labs

**Why it matters in production:** security problems arrive on-call in two disguises. The first is "I can't get in" (a key refused, an account locked, sudo denied) and it blocks people doing their jobs right now. The second is "the auditors / the scanner say we're exposed", and it comes with a deadline. In both cases, the fast wrong fix (chmod 777, `PermitRootLogin yes`, `NOPASSWD: ALL`, `setenforce 0`) creates the next incident. This module teaches the right fix, quickly.

## 1. The security troubleshooting mindset

Security tooling on Linux is designed to **fail closed and say little to the client**. When sshd refuses a key, the client only sees `Permission denied (publickey)`. When PAM locks an account, the user just sees "wrong password". The real reason is almost always written on the **server**, in one of three places:

| Where | What it records | How to read it |
|---|---|---|
| systemd journal / `/var/log/secure` | sshd, sudo, PAM, su messages | `journalctl -u sshd`, `journalctl _COMM=sudo`, `tail -f /var/log/secure` |
| Audit log `/var/log/audit/audit.log` | SELinux denials (AVC), audit watch hits, logins | `ausearch -m AVC -ts recent`, `ausearch -k <key> -i` |
| The config the daemon actually loaded | Effective settings after all includes | `sshd -T`, `sudo -l`, `authselect current`, `update-crypto-policies --show` |

:::concept
**Client says little, server says everything.** Never troubleshoot an access problem only from the client side. Get a second session on the server (console, an existing root shell, or another admin account) and watch the server log **while** the user retries: `journalctl -fu sshd` in one window, `ausearch -m AVC -ts recent` in another. Ninety percent of SSH tickets are solved in the first minute this way.
:::

:::warn
**Always keep a way back in.** Before you change sshd, PAM, sudoers, crypto policies or the firewall on a remote server, keep an existing root session open and do not close it until a **new** login works. Have the out-of-band console (iLO/iDRAC, hypervisor console, cloud serial console) ready. Most "we locked ourselves out" outages come from closing the last working session too early.
:::

:::story
Think of the server as a building with a security desk. The visitor (client) only hears "no entry". The guard's logbook (journal, audit log) says exactly why: badge expired (account expired), wrong floor (AllowUsers), badge in a dodgy wallet (bad file permissions), or the building's master rules (SELinux) forbid the guard from even looking at that wallet. You read the logbook, not the visitor's face.
:::

## 2. SSH: how authentication works and where it breaks

### 2.1 The moving parts

On RHEL 9 the SSH server is OpenSSH (`openssh-server`, unit `sshd.service`). A login goes through these stages, and each can fail independently:

1. **TCP connect** to port 22 (firewalld, SELinux port label if non-standard, sshd listening).
2. **Key exchange (KEX)**: client and server must share at least one KEX algorithm, cipher, MAC and host key type. Crypto policies decide the server's list.
3. **Host key verification** on the client (`~/.ssh/known_hosts`).
4. **User authentication**: publickey, password (through PAM), GSSAPI (Kerberos), keyboard-interactive.
5. **Account checks** through PAM: account locked, expired, `pam_faillock`, `AllowUsers`/`AllowGroups`, `nologin` shell.
6. **Session setup**: shell, home directory, SELinux user context.

### 2.2 Checking and reading the server config

```bash
sshd -t                      # syntax check, silent if OK
sshd -T | less               # dump the EFFECTIVE configuration (needs root)
sshd -T | grep -Ei 'permitrootlogin|passwordauthentication|pubkeyauthentication|kexalgorithms|ciphers|macs'
sshd -T -C user=alice,host=app.example.com,addr=10.0.0.25   # effective config for a specific connection (Match blocks)
```

```text
permitrootlogin without-password
pubkeyauthentication yes
passwordauthentication yes
kexalgorithms curve25519-sha256,curve25519-sha256@libssh.org,ecdh-sha2-nistp256,...
ciphers aes256-gcm@openssh.com,chacha20-poly1305@openssh.com,aes256-ctr,aes128-gcm@openssh.com,aes128-ctr
macs hmac-sha2-256-etm@openssh.com,hmac-sha1-etm@openssh.com,umac-128-etm@openssh.com,...
```

`sshd -T` prints `without-password`, which is the older synonym of `prohibit-password`: root may log in with a key but not a password. The exact algorithm lists depend on your RHEL minor version and crypto policy, so treat the output above as an example, not a reference.

:::tip
`sshd -t` before **every** `systemctl restart sshd` or `reload`. If the config is broken, the restart fails, and on some setups you have just killed the listener you were going to use to fix it. Existing sessions survive an sshd restart, but new ones won't. Make it a reflex: `sshd -t && systemctl reload sshd`.
:::

### 2.3 Drop-ins and the "first value wins" rule

RHEL 9's `/etc/ssh/sshd_config` starts with:

```text
Include /etc/ssh/sshd_config.d/*.conf
```

Files are read in lexical order (`01-...`, `40-...`, `50-redhat.conf`, `50-cloud-init.conf`), and then the rest of the main file. For most keywords **the first value sshd reads wins**; later lines for the same keyword are ignored. That surprises people who are used to "last one wins".

```bash
ls -l /etc/ssh/sshd_config.d/
grep -R -n -i passwordauthentication /etc/ssh/sshd_config /etc/ssh/sshd_config.d/
```

```text
-rw-------. 1 root root  141 Mar  2 10:11 01-permitrootlogin.conf
-rw-------. 1 root root  719 Mar  2 10:11 50-redhat.conf
-rw-------. 1 root root   26 Mar  2 10:15 50-cloud-init.conf
/etc/ssh/sshd_config:#PasswordAuthentication yes
/etc/ssh/sshd_config.d/50-cloud-init.conf:PasswordAuthentication no
```

Here someone edited the main file to `PasswordAuthentication yes`, restarted sshd, and nothing changed, because `50-cloud-init.conf` was read first and said `no`. The correct practice is to put your site settings in an **early-sorting drop-in** such as `/etc/ssh/sshd_config.d/10-site.conf`, and always confirm with `sshd -T`.

:::concept
On RHEL 9, `50-redhat.conf` also contains `Include /etc/crypto-policies/back-ends/opensshserver.config`, which is how the system crypto policy sets `Ciphers`, `MACs`, `KexAlgorithms` and friends. Because the first value wins, a drop-in that sets `KexAlgorithms` must sort **before** `50-redhat.conf` (for example `40-crypto-override.conf`) or it will be silently ignored. Verify the result with `sshd -T`, never by reading files.
:::

### 2.4 `PermitRootLogin` and `PasswordAuthentication`

| Setting | Values | Production recommendation |
|---|---|---|
| `PermitRootLogin` | `yes`, `prohibit-password` (`without-password`), `forced-commands-only`, `no` | `no` for interactive servers (admins log in as themselves and use sudo); `prohibit-password` only where automation truly needs root with a key |
| `PasswordAuthentication` | `yes` / `no` | `no` on internet-facing and most internal servers once keys are deployed |
| `KbdInteractiveAuthentication` | `yes` / `no` | `no` unless you use PAM-based OTP prompts |
| `PubkeyAuthentication` | `yes` / `no` | `yes` |
| `AllowGroups` / `AllowUsers` | lists | Restrict SSH to e.g. `AllowGroups sshusers wheel` |
| `MaxAuthTries` | number (default 6) | 3–4; also reduces lockout noise |
| `LoginGraceTime` | seconds (default 120) | 30–60 |
| `ClientAliveInterval` / `ClientAliveCountMax` | seconds / count | Set per your idle-timeout policy |

RHEL 9's packaged default for `PermitRootLogin` is `prohibit-password`. The installer adds `01-permitrootlogin.conf` with `PermitRootLogin yes` if you ticked "Allow root SSH login with password" during installation, which is a common audit finding on servers built by hand.

```ini
# /etc/ssh/sshd_config.d/10-site.conf
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
MaxAuthTries 4
LoginGraceTime 45
AllowGroups sshusers wheel
```

```bash
sshd -t && systemctl reload sshd
sshd -T | grep -Ei 'permitrootlogin|passwordauth|allowgroups'
```

:::warn
Before setting `PasswordAuthentication no` or `AllowGroups`, **test a key login for at least one admin account in a new session**. Note also that `AllowGroups` applies to root too: if root isn't in a listed group, root can't log in even from the console-adjacent jump host you rely on. That can be exactly what you want, but decide it deliberately.
:::

### 2.5 Keys and permissions: the `StrictModes` trap

With `StrictModes yes` (the default), sshd refuses to use `authorized_keys` if the user's home directory, `~/.ssh` or the file itself is writable by anyone other than the user (or root). The client just sees `Permission denied (publickey)`.

| Path | Owner | Required mode | Typical breakage |
|---|---|---|---|
| `/home/alice` | `alice` | Not group- or world-writable (`755` or `700`) | `chmod 775 /home/alice` for a "shared" folder |
| `/home/alice/.ssh` | `alice` | `700` | Created by root, owned by root |
| `/home/alice/.ssh/authorized_keys` | `alice` | `600` (or `644`) | `664` from a umask 002 shell, or owned by root |
| Private key on the client `~/.ssh/id_ed25519` | client user | `600` | Client refuses: "UNPROTECTED PRIVATE KEY FILE!" |

The server tells you exactly what's wrong:

```bash
journalctl -u sshd --since "10 min ago"
```

```text
sshd[4121]: Authentication refused: bad ownership or modes for directory /home/alice/.ssh
sshd[4121]: Connection closed by authenticating user alice 10.0.0.25 port 51544 [preauth]
```

The fix is ownership and modes, never relaxing `StrictModes`:

```bash
chown -R alice:alice /home/alice/.ssh
chmod 700 /home/alice/.ssh
chmod 600 /home/alice/.ssh/authorized_keys
chmod go-w /home/alice
restorecon -Rv /home/alice/.ssh
```

:::tip
Use `ssh-copy-id -i ~/.ssh/id_ed25519.pub alice@app.example.com` to install keys. It creates `~/.ssh` and `authorized_keys` with the correct owner and mode on the remote side, and in the correct SELinux context. Hand-rolled `cat key >> ...` as root is where most permission tickets come from.
:::

### 2.6 SELinux and `~/.ssh`: the `ssh_home_t` label

sshd runs as the SELinux domain `sshd_t`. It is allowed to read `authorized_keys` only if the file carries an SSH-appropriate type, normally **`ssh_home_t`**. Permissions can be perfect and the login still fails if the label is wrong.

```bash
ls -Zd /home/alice/.ssh
ls -Z /home/alice/.ssh/authorized_keys
```

```text
unconfined_u:object_r:ssh_home_t:s0 /home/alice/.ssh
unconfined_u:object_r:user_tmp_t:s0 /home/alice/.ssh/authorized_keys
```

How it happens: an admin builds the file in `/tmp` and **moves** it into place with `mv`. `mv` keeps the original label (`user_tmp_t`), while `cp` creates a new file that inherits the directory's default. Restoring from a tarball without `--selinux`, rsync without `-X`, or creating `.ssh` under a non-standard home path (`/data/home/alice`) cause the same symptom.

```bash
ausearch -m AVC -ts recent -c sshd
```

```text
type=AVC msg=audit(1764064512.311:812): avc:  denied  { read } for  pid=4388 comm="sshd"
  name="authorized_keys" dev="dm-0" ino=3389012
  scontext=system_u:system_r:sshd_t:s0-s0:c0.c1023
  tcontext=unconfined_u:object_r:user_tmp_t:s0 tclass=file permissive=0
```

Read it left to right: **who** (`sshd_t`) was denied **what** (`read`) on **which object** (`authorized_keys`, type `user_tmp_t`). The fix is to put the correct label back:

```bash
restorecon -Rv /home/alice/.ssh
```

```text
Relabeled /home/alice/.ssh/authorized_keys from unconfined_u:object_r:user_tmp_t:s0 to unconfined_u:object_r:ssh_home_t:s0
```

For **non-standard home directories**, teach SELinux that the new path is equivalent to `/home`, then relabel:

```bash
semanage fcontext -a -e /home /data/home
restorecon -Rv /data/home
```

For a **central key location** (for example `AuthorizedKeysFile /etc/ssh/authorized_keys/%u`), label that directory explicitly:

```bash
semanage fcontext -a -t ssh_home_t '/etc/ssh/authorized_keys(/.*)?'
restorecon -Rv /etc/ssh/authorized_keys
```

And for a **non-standard SSH port**, sshd must be allowed to bind it:

```bash
semanage port -a -t ssh_port_t -p tcp 2222
firewall-cmd --permanent --add-port=2222/tcp && firewall-cmd --reload
```

:::warn
**Never "fix" an SSH login with `setenforce 0`.** You may use it for thirty seconds as a *diagnostic* ("does the login work in permissive mode? then it's SELinux"), and you must run `setenforce 1` straight after. The fix is always `restorecon`, `semanage fcontext`, `semanage port` or a boolean. Disabling SELinux removes the protection that stops a compromised sshd or web app from reading every user's files.
:::

:::tip
Some denials are hidden by `dontaudit` rules and never appear as AVCs. If you suspect SELinux but see nothing, run `semodule -DB` (disable dontaudit), retry, check `ausearch -m AVC -ts recent`, then **always** run `semodule -B` to restore normal rules.
:::

**Ubuntu/Debian:** there is no SELinux by default; AppArmor confines a few services, not sshd's file access. Permission (`StrictModes`) problems look identical, and logs go to `/var/log/auth.log` and `journalctl -u ssh` (the unit is `ssh`, not `sshd`).

### 2.7 Client-side debugging with `ssh -vvv`

```bash
ssh -vvv -i ~/.ssh/id_ed25519 alice@app.example.com
```

```text
debug1: Connecting to app.example.com [10.0.0.20] port 22.
debug1: Connection established.
debug1: Remote protocol version 2.0, remote software version OpenSSH_8.7
debug1: kex: algorithm: curve25519-sha256
debug1: Server host key: ssh-ed25519 SHA256:7f3K...Qx0
debug1: Host 'app.example.com' is known and matches the ED25519 host key.
debug1: Authentications that can continue: publickey,gssapi-keyex,gssapi-with-mic
debug1: Offering public key: /home/me/.ssh/id_ed25519 ED25519 SHA256:pL9...a2E explicit
debug3: send packet: type 50
debug2: we did not get a key acceptance
debug1: No more authentication methods to try.
alice@app.example.com: Permission denied (publickey,gssapi-keyex,gssapi-with-mic).
```

How to read it:

| Line | Meaning |
|---|---|
| `Connection established` | Network and firewall are fine |
| `kex: algorithm: ...` | KEX succeeded; if instead you see `no matching key exchange method found`, it's a crypto mismatch |
| `Authentications that can continue: publickey,...` | `password` is missing, so the server has `PasswordAuthentication no` |
| `Offering public key ... ` then no `Server accepts key` | The server did not accept this key: wrong key, wrong user, or the server couldn't read `authorized_keys` (permissions or SELinux). Check the **server** log next |
| `Server accepts key` then failure | Key is fine; the failure is later: PAM account check, locked/expired account, `nologin` shell |
| `Permission denied` after a long pause | Often `UseDNS` or GSSAPI timeouts; try `-o GSSAPIAuthentication=no` to test |

### 2.8 "REMOTE HOST IDENTIFICATION HAS CHANGED"

```text
@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @
@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
IT IS POSSIBLE THAT SOMEONE IS DOING SOMETHING NASTY!
...
Offending ECDSA key in /home/me/.ssh/known_hosts:14
Host key verification failed.
```

Legitimate causes: the server was rebuilt, a VM was redeployed with the same IP, a load balancer sends you to a different backend, or host keys were regenerated. Illegitimate cause: a man-in-the-middle. **Verify before you trust**: get the real fingerprint from the server's console or your config management, not over the same suspicious connection.

```bash
# On the server, via console or a trusted session:
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
# On the client, once verified:
ssh-keygen -R app.example.com
ssh-keygen -R 10.0.0.20
ssh alice@app.example.com     # accept the new key only if the fingerprint matches
```

:::prod
**The rebuilt bastion.** After a bastion host was rebuilt from an image, 40 engineers got the host-key warning and were told in chat "just delete line 14 in known_hosts". Two weeks later, a real ARP-spoofing test by the red team went unnoticed for the same reason: everyone had been trained to click through. **Root cause:** host keys were not preserved across rebuilds. **Fix:** the team now backs up `/etc/ssh/ssh_host_*` keys in their secrets store and restores them on rebuild, and publishes host key fingerprints (or SSHFP DNS records / a managed `ssh_known_hosts` file pushed by Ansible). **Prevention:** a host key change is now a change-managed event, announced with the new fingerprint.
:::

## 3. Crypto policies and weak-algorithm findings

### 3.1 System-wide crypto policies

RHEL 8 and 9 use **crypto-policies** to configure OpenSSH, OpenSSL, GnuTLS, NSS, Java and libkrb5 from one place. You pick a policy level, and every back end is regenerated in `/etc/crypto-policies/back-ends/`.

| Policy | Intent |
|---|---|
| `DEFAULT` | Secure for today; on RHEL 9 it disallows SHA-1 signatures, TLS below 1.2, and short RSA/DH keys |
| `LEGACY` | Maximum compatibility with old systems: re-enables SHA-1, older TLS / DH, some older SSH algorithms. Use only temporarily and deliberately |
| `FUTURE` | Conservative, forward-looking: larger keys (RSA ≥ 3072), stricter hashes; may break connections to older peers |
| `FIPS` | Used when the system is in FIPS mode; set with `fips-mode-setup --enable`, not directly |

```bash
update-crypto-policies --show
update-crypto-policies --set FUTURE
update-crypto-policies --set DEFAULT:NO-SHA1       # base policy + subpolicy
ls /usr/share/crypto-policies/policies/modules/    # shipped subpolicies (.pmod)
```

```text
DEFAULT
```

After changing the policy, restart affected services or, as Red Hat recommends, **reboot** so that every process picks up the new settings. sshd uses the policy on next start.

### 3.2 Custom subpolicies: narrow fixes for scanner findings

Instead of moving the whole server to `FUTURE` (which can break older clients), write a small subpolicy that removes only what the scanner flagged. Subpolicies live in `/etc/crypto-policies/policies/modules/` and end in `.pmod`.

```ini
# /etc/crypto-policies/policies/modules/NO-SSH-WEAK.pmod
# Remove CBC ciphers and SHA-1 MACs from SSH only
cipher@SSH = -*-CBC
mac@SSH = -HMAC-SHA1
```

```bash
update-crypto-policies --set DEFAULT:NO-SSH-WEAK
systemctl restart sshd
sshd -T | grep -Ei '^(ciphers|macs|kexalgorithms)'
```

The `@SSH` scope limits the change to OpenSSH; without it the change applies to TLS libraries too. Algorithm names in `.pmod` files use crypto-policies' own vocabulary (`AES-256-CBC`, `HMAC-SHA1`, `DH`, `ECDHE`), not OpenSSH names. Check `man crypto-policies` on your exact minor release, because the supported keywords have grown over RHEL 9 releases.

### 3.3 Alternative: an sshd drop-in

If you need a quick, sshd-only override, or are on a distribution without crypto-policies (Ubuntu/Debian), set the lists explicitly in an early drop-in:

```ini
# /etc/ssh/sshd_config.d/40-crypto-override.conf   (must sort before 50-redhat.conf)
KexAlgorithms curve25519-sha256,curve25519-sha256@libssh.org,ecdh-sha2-nistp256,ecdh-sha2-nistp384,ecdh-sha2-nistp521,diffie-hellman-group16-sha512,diffie-hellman-group18-sha512
Ciphers aes256-gcm@openssh.com,aes128-gcm@openssh.com,chacha20-poly1305@openssh.com,aes256-ctr,aes128-ctr
MACs hmac-sha2-256-etm@openssh.com,hmac-sha2-512-etm@openssh.com,hmac-sha2-256,hmac-sha2-512
```

:::tip
Prefer the crypto-policy route on RHEL: it is supported, survives package updates, and keeps SSH and TLS consistent. The drop-in route is fine, but it means sshd no longer follows the system policy for those keywords, and someone must maintain the list. Document whichever you choose in your hardening standard.
:::

**Verifying from outside** (what the scanner sees):

```bash
nmap -p 22 --script ssh2-enum-algos 10.0.0.20
ssh -Q kex          # algorithms YOUR client supports (not the server's)
ssh -o KexAlgorithms=diffie-hellman-group14-sha1 alice@10.0.0.20   # should now fail
```

```text
Unable to negotiate with 10.0.0.20 port 22: no matching key exchange method found. Their offer: curve25519-sha256,...
```

## 4. Vulnerability scan findings: remediate, don't panic

Network scanners (Nessus, Qualys, OpenVAS/Greenbone, Rapid7 and the like, often run from a Windows-based security console by a separate team) probe your ports and compare what they see with their plugin database. Their output is useful but often **naive about Linux distributions**. Your job is to separate real findings from false positives and close each with evidence.

| Typical finding | Real or not? | Remediation |
|---|---|---|
| SSH weak key exchange algorithms (`diffie-hellman-group1-sha1`, `group14-sha1`, `group-exchange-sha1`) | Real if `nmap ssh2-enum-algos` shows them | Return to `DEFAULT` policy, add a subpolicy, or an sshd drop-in (section 3) |
| SSH CBC mode ciphers enabled | Real if listed | `cipher@SSH = -*-CBC` subpolicy or explicit `Ciphers` |
| SSH weak MACs (`hmac-sha1`, `umac-64`, `hmac-md5`) | Real if listed | `mac@SSH = ...` subpolicy or explicit `MACs` |
| Terrapin (CVE-2023-48795) | Depends on package version and enabled algorithms | Patch `openssh` (fixed builds add strict KEX); as interim, remove `chacha20-poly1305` and `-etm` MACs combined with CBC, per vendor guidance |
| "OpenSSH < X.Y multiple vulnerabilities" based on the banner `OpenSSH_8.7` | **Usually a false positive** on RHEL: Red Hat backports fixes without changing the upstream version number | Show the fix is in your package: `rpm -q --changelog openssh | grep CVE-XXXX-YYYY` or the RHSA/RLSA advisory; ask the scan team to mark it as a backport exception |
| HTTP on port 80 / "web server transmits cleartext credentials" | Real if the app serves pages or logins over HTTP | Redirect 80 to 443 and add HSTS, or close port 80 entirely |
| TLS 1.0/1.1 enabled, weak TLS ciphers | Real if `nmap --script ssl-enum-ciphers -p 443` lists them | `DEFAULT` already disables TLS < 1.2 on RHEL 9; check app-specific overrides in nginx/httpd/Java configs |
| Unused service exposed (rpcbind, cups, telnet, FTP) | Real | Disable the service and close the port (section 11) |

### Port 80: redirect, don't just close (usually)

If users type `http://app.example.com`, closing 80 gives them a connection error; redirecting sends them to HTTPS and satisfies the scanner as long as **nothing but the redirect** is served on 80.

```text
# nginx: /etc/nginx/conf.d/redirect-http.conf
server {
    listen 80;
    server_name app.example.com;
    return 301 https://app.example.com$request_uri;
}
```

```text
# Apache httpd: /etc/httpd/conf.d/redirect-http.conf
<VirtualHost *:80>
    ServerName app.example.com
    Redirect permanent / https://app.example.com/
</VirtualHost>
```

Then add `Strict-Transport-Security: max-age=31536000` on the HTTPS site so browsers stop trying HTTP. Verify:

```bash
nginx -t && systemctl reload nginx        # or: apachectl configtest && systemctl reload httpd
curl -sI http://app.example.com | head -3
```

```text
HTTP/1.1 301 Moved Permanently
Server: nginx
Location: https://app.example.com/
```

:::prod
**The 300-line scan report.** A quarterly scan flagged 300 "critical" findings across 60 RHEL 9 web servers, nearly all titled "OpenSSH < 9.x multiple vulnerabilities" and "Apache httpd < 2.4.x". The junior engineer started building OpenSSH from source to raise the version number, which would have removed the servers from vendor support and patch streams. **Root cause:** banner-version matching against an enterprise distribution that backports fixes. **Fix:** `dnf upgrade --security` across the fleet, then for each CVE the team produced `rpm -q --changelog` evidence and the advisory ID; the scan team marked them as backport exceptions. The genuine findings (CBC ciphers on 6 servers with `LEGACY` policy left from a migration, HTTP login page on 2 servers) were fixed with a subpolicy and a redirect. **Prevention:** an authenticated (credentialed) scan, which checks installed package versions instead of banners, replaced the unauthenticated one.
:::

## 5. sudo: delegation without giving away the server

### 5.1 How sudo decides

sudo reads `/etc/sudoers` and every file in `/etc/sudoers.d/` (via the `#includedir /etc/sudoers.d` or `@includedir` line; both forms work, newer sudo versions prefer `@`). On RHEL 9 the default grants full sudo to the `wheel` group:

```text
%wheel  ALL=(ALL)       ALL
```

Format: `who  where=(as_whom)  what`. The `%` means a group.

```bash
usermod -aG wheel alice          # alice gets full sudo (with her password)
id alice                         # she must log in again for the new group to apply
sudo -l                          # what can I run?
sudo -l -U alice                 # as root: what can alice run?
```

```text
User alice may run the following commands on app01:
    (ALL) ALL
```

**Ubuntu/Debian:** the admin group is `sudo`, not `wheel` (`%sudo ALL=(ALL:ALL) ALL`); logs go to `/var/log/auth.log`.

### 5.2 Always edit with `visudo`

A syntax error in sudoers breaks sudo for **everyone**, and if root has no password and no direct login, you've just lost admin access. `visudo` checks syntax before saving.

```bash
visudo                                   # edit /etc/sudoers safely
visudo -f /etc/sudoers.d/50-appteam      # edit a drop-in safely
visudo -c                                # check every sudoers file
```

```text
/etc/sudoers: parsed OK
/etc/sudoers.d/50-appteam: parsed OK
```

Rules for drop-ins in `/etc/sudoers.d/`:

- Files whose names contain a **dot** (`.`) or end in **`~`** are **ignored**. `appteam.conf` is silently skipped; use `50-appteam`.
- Mode should be `0440`, owned by root. Config management should deploy them with `validate: visudo -cf %s` (Ansible's `copy`/`template` modules support this).

A sensible, least-privilege drop-in:

```text
# /etc/sudoers.d/50-appteam
Cmnd_Alias MYAPP = /usr/bin/systemctl restart myapp.service, \
                   /usr/bin/systemctl status myapp.service, \
                   /usr/bin/journalctl -u myapp.service *
%appteam ALL=(root) MYAPP
```

### 5.3 The real risks

| Pattern | Why it's dangerous | Better |
|---|---|---|
| `alice ALL=(ALL) NOPASSWD: ALL` | Anyone who steals alice's session or key owns the server; no second factor at all | Require password, or limit NOPASSWD to a few specific commands for automation accounts |
| `ALL=(root) /usr/bin/vi /etc/myapp.conf` | `:!sh` in vi gives a root shell | `sudoedit /etc/myapp.conf` (edits as the user, writes as root) |
| `less`, `more`, `man`, `find`, `awk`, `python`, `tar`, `rsync` allowed as root | All have documented shell escapes | Allow wrapper scripts owned by root, not general tools |
| `systemctl *` | `systemctl edit` or a crafted unit can run anything as root | List exact `systemctl restart X.service` commands |
| Wildcards in arguments `/usr/bin/cat /var/log/*` | `cat /var/log/../../etc/shadow` matches | Use specific paths or `journalctl` with fixed units |
| Automation user with `NOPASSWD: ALL` | Often unavoidable (Ansible `become`) | Restrict by `AllowUsers`/`from=` on the key, dedicated key per tool, audit its commands |

:::warn
Check `NOPASSWD` grants on every audit: `grep -R NOPASSWD /etc/sudoers /etc/sudoers.d/`. Each one should have an owner and a reason. "We added it during a migration two years ago" is not a reason.
:::

### 5.4 Troubleshooting sudo

```bash
journalctl _COMM=sudo --since today
grep sudo /var/log/secure | tail
```

```text
sudo[5120]:    alice : user NOT in sudoers ; TTY=pts/1 ; PWD=/home/alice ; USER=root ; COMMAND=/usr/bin/systemctl restart myapp
sudo[5188]:      bob : command not allowed ; TTY=pts/2 ; PWD=/home/bob ; USER=root ; COMMAND=/usr/bin/systemctl restart myapp.service
```

| Message | Cause | Fix |
|---|---|---|
| `user NOT in sudoers` | No rule matches the user or their groups | Add to `wheel` or a drop-in; user must re-login if a group was added |
| `command not allowed` | A rule exists but not for this exact command line | Compare with `sudo -l`: full path, exact arguments (`myapp` vs `myapp.service`) |
| `sudo: /etc/sudoers.d/x is world writable` / `parse error` | Bad mode or syntax | `chmod 0440`, `visudo -c`; recover via `pkexec visudo` or the console in single-user / rescue mode |
| `a password is required` in automation | No `NOPASSWD` for that account | Provide the password via the tool, or a scoped NOPASSWD rule |
| sudo slow (several seconds) | Hostname not resolvable | Fix `/etc/hosts` / DNS for the local hostname |

## 6. PAM basics and account lockout

### 6.1 PAM in one page

**PAM (Pluggable Authentication Modules)** is the library that sshd, login, su, sudo and others call to authenticate users and check accounts. Each service has a file in `/etc/pam.d/`; most include the shared stacks `system-auth` and `password-auth` (sshd uses `password-auth`). Each line is `type control module [args]`, and the four types run in this order: `auth` (who are you), `account` (are you allowed now), `password` (changing secrets), `session` (setup/teardown).

On RHEL 8/9, those shared files are **generated by `authselect`**. Do not hand-edit `/etc/pam.d/system-auth`; your edits are overwritten the next time someone runs `authselect`.

```bash
authselect current
authselect list-features sssd
authselect enable-feature with-faillock
authselect check
```

```text
Profile ID: sssd
Enabled features:
- with-faillock
- with-mkhomedir
```

**Ubuntu/Debian:** `pam-auth-update` manages `/etc/pam.d/common-*`; older releases used `pam_tally2` for lockouts, newer ones ship `pam_faillock`.

### 6.2 `pam_faillock`: lockouts

With the `with-faillock` feature enabled, failed authentications are counted per user, and after `deny` failures the account is locked for `unlock_time` seconds. Settings live in `/etc/security/faillock.conf`:

```ini
# /etc/security/faillock.conf
deny = 5
fail_interval = 900
unlock_time = 900
# even_deny_root     # uncomment only if you have console access planned
# silent
audit
```

Key behaviours:

- While locked, **even the correct password fails**, and the user is not told why (unless `silent` is off and the client shows PAM messages; sshd usually doesn't).
- Public key logins are not counted by `pam_faillock` in the default sshd flow, because publickey auth doesn't go through the PAM `auth` stack. A locked user can often still log in with a key, which surprises people.
- Root is exempt unless `even_deny_root` is set. Setting it without console access is how you lock out the whole team.

```bash
faillock --user alice
```

```text
alice:
When                Type  Source                                           Valid
2026-11-25 09:41:02 RHOST 10.0.0.25                                            V
2026-11-25 09:41:07 RHOST 10.0.0.25                                            V
2026-11-25 09:41:12 RHOST 10.0.0.25                                            V
2026-11-25 09:41:18 RHOST 10.0.0.25                                            V
2026-11-25 09:41:24 RHOST 10.0.0.25                                            V
```

Five valid (`V`) failures within `fail_interval`: alice is locked. The source IP matters: if it's a script or a monitoring host with an old password, unlocking alice is pointless until that is fixed, because she'll be locked again within minutes.

```bash
faillock --user alice --reset
journalctl -u sshd | grep -i faillock
```

```text
sshd[6011]: pam_faillock(sshd:auth): Consecutive login failures for user alice account temporarily locked
```

:::prod
**The service account that kept locking itself.** Every morning at 06:00, the `svc-backup` account was locked and the night's backups failed. The on-call engineer reset it with `faillock --reset` daily for a week. **What `faillock --user svc-backup` showed:** all failures from `10.0.0.41`, an old backup proxy decommissioned "except for one cron job" that still had the previous password. **Root cause:** the password was rotated, the forgotten client kept retrying. **Fix:** remove the cron job on the old proxy, rotate to key-based auth for the account. **Prevention:** service accounts use keys or Kerberos keytabs, not passwords, and an alert fires on any faillock event for service accounts.
:::

### 6.3 Password aging with `chage`

An "account locked" ticket is often **password or account expiry**, which is a different mechanism.

```bash
chage -l alice
```

```text
Last password change                                    : Aug 20, 2026
Password expires                                        : Nov 18, 2026
Password inactive                                       : Dec 18, 2026
Account expires                                         : never
Minimum number of days between password change          : 1
Maximum number of days between password change          : 90
Number of days of warning before password expires       : 7
```

| Task | Command |
|---|---|
| Max 90 days, warn 7 days before | `chage -M 90 -W 7 alice` |
| Force a password change at next login | `chage -d 0 alice` |
| Set an account expiry date (contractor) | `chage -E 2027-03-31 alice` |
| Remove account expiry | `chage -E -1 alice` |
| Quick status (P, L, NP) | `passwd -S alice` |
| Lock / unlock the password | `usermod -L alice` / `usermod -U alice` |
| Defaults for new users | `PASS_MAX_DAYS`, `PASS_MIN_DAYS`, `PASS_WARN_AGE` in `/etc/login.defs` |

What users see: "Your password has expired. You must change your password now" (password expiry: they can usually change it at login, if `PasswordAuthentication`/keyboard-interactive allows), versus "Your account has expired; please contact your system administrator" (account expiry: only an admin can fix it).

:::warn
`usermod -L` / `passwd -l` lock only the **password**. On RHEL with `UsePAM yes`, a user with an SSH key can still log in. To disable an account fully (leaver process), expire it (`chage -E 0 alice` or `usermod -e 1 alice`), remove or disable their keys, and consider setting the shell to `/sbin/nologin`.
:::

## 7. auditd: answering "who changed this?"

The **Linux audit system** records kernel-level events: file access matching watch rules, syscalls, logins, and SELinux denials. Its killer feature is the **`auid` (audit UID, "loginuid")**: the identity a person logged in with, which stays the same after `sudo` or `su`. The file was changed by `uid=root`, but `auid=alice` tells you who that root really was.

### 7.1 Temporary and persistent rules

```bash
auditctl -w /etc/ssh/sshd_config -p wa -k sshd-config     # watch writes and attribute changes
auditctl -l                                               # list loaded rules
auditctl -s                                               # status: enabled, lost events, backlog
```

`-p` permissions: `r` read, `w` write, `x` execute, `a` attribute change (chmod, chown, xattr incl. SELinux label). `-k` sets a search key.

Rules added with `auditctl` are lost at reboot. Make them persistent in `/etc/audit/rules.d/` and load with `augenrules`:

```text
# /etc/audit/rules.d/50-site.rules
-w /etc/ssh/sshd_config -p wa -k sshd-config
-w /etc/ssh/sshd_config.d/ -p wa -k sshd-config
-w /etc/sudoers -p wa -k sudoers
-w /etc/sudoers.d/ -p wa -k sudoers
-w /etc/passwd -p wa -k identity
-w /etc/shadow -p wa -k identity
-w /etc/group -p wa -k identity
-w /opt/myapp/conf/ -p wa -k myapp-conf
```

```bash
augenrules --load
auditctl -l | grep -c -- '-k'
```

:::tip
`systemctl restart auditd` is refused on RHEL (the unit sets `RefuseManualStop=yes`) so that an attacker can't quietly stop auditing. To reload rules use `augenrules --load`; to restart the daemon itself use `service auditd restart`. If your rules end with `-e 2` (immutable), changes need a reboot, which is intended for high-security hosts.
:::

### 7.2 Searching

```bash
ausearch -k sshd-config -i                  # -i interprets numeric IDs into names
ausearch -k sshd-config -i -ts today
ausearch -f /etc/ssh/sshd_config -i -ts yesterday
aureport -f -i --summary                    # file event summary
aureport -au -i                             # authentication report
ausearch -m USER_LOGIN -sv no -i            # failed logins
```

```text
----
type=PROCTITLE msg=audit(11/25/2026 10:42:13.512:1234) : proctitle=vi /etc/ssh/sshd_config
type=PATH msg=audit(11/25/2026 10:42:13.512:1234) : item=1 name=/etc/ssh/sshd_config inode=1835203 ... nametype=CREATE ...
type=CWD msg=audit(11/25/2026 10:42:13.512:1234) : cwd=/home/alice
type=SYSCALL msg=audit(11/25/2026 10:42:13.512:1234) : arch=x86_64 syscall=openat success=yes exit=3 ... ppid=7120 pid=7144
  auid=alice uid=root gid=root euid=root ... tty=pts1 ses=7 comm=vi exe=/usr/bin/vim key=sshd-config
```

Read it as: in login session 7, **alice** (`auid`), working as root (`uid=root`, via sudo), ran `vi` on `/etc/ssh/sshd_config` at 10:42:13 from terminal `pts1`. Cross-check with `journalctl _COMM=sudo` to see the exact sudo command.

:::concept
**Watch directories when editors are involved.** Editors such as vim often write a new file and rename it over the old one. Audit handles watched file paths across this, but watching the **directory** (`-w /opt/myapp/conf/ -p wa`) catches creates, renames and deletes of any file in it, which is what you usually want for config folders.
:::

**Ubuntu/Debian:** `apt install auditd`; the same tools and `/etc/audit/rules.d/`.

## 8. Patching: security updates without surprises

### 8.1 What's pending, and how serious?

```bash
dnf updateinfo summary
dnf updateinfo list --security
dnf updateinfo list --sec-severity=Critical
dnf updateinfo info RHSA-2026:1234            # Rocky: RLSA-..., Alma: ALSA-...
dnf updateinfo list --cve CVE-2024-6387        # is a fix for this CVE pending?
```

```text
Updates Information Summary: available
    5 Security notice(s)
        1 Critical Security notice(s)
        3 Important Security notice(s)
        1 Moderate Security notice(s)
    9 Bugfix notice(s)
    2 Enhancement notice(s)
```

### 8.2 Security-only or full updates

```bash
dnf upgrade --security                    # only packages with security advisories
dnf upgrade --sec-severity=Critical       # only critical ones
dnf upgrade --advisory=RHSA-2026:1234     # one specific advisory
dnf upgrade                               # everything (recommended regularly)
dnf history list | head                   # what changed, when
dnf history undo <ID>                     # roll back a transaction (not for kernels/glibc in practice)
```

:::concept
Security-only updates reduce change during an emergency, but running only them for months makes your servers drift from the tested combination of packages. A common, sane policy: **security-only** for urgent out-of-cycle patching, **full `dnf upgrade`** in the regular monthly window, staged dev → test → prod.
:::

### 8.3 Did the update actually take effect?

Updating a package on disk doesn't change processes already running the old code, and a new kernel does nothing until you boot it.

```bash
needs-restarting -r          # is a reboot needed? exit code 1 = yes
needs-restarting -s          # which services should be restarted
needs-restarting             # processes using deleted/updated files
```

```text
Core libraries or services have been updated since boot-up:
  * kernel
  * openssl-libs

Reboot is required to fully utilize these updates.
More information: https://access.redhat.com/solutions/27943
```

`needs-restarting` comes from `dnf-utils` / `dnf-plugins-core`. Its exit code makes it easy to use in Ansible or a monitoring check.

### 8.4 Kernel updates

```bash
uname -r                                 # running kernel
rpm -q kernel                            # installed kernels
grubby --default-kernel                  # kernel that will boot next
grep installonly_limit /etc/dnf/dnf.conf # how many kernels are kept (default 3)
```

Keep at least one previous kernel (`installonly_limit=3`) so you can pick it from the GRUB menu if the new one fails. Live patching (`kpatch` on RHEL) can apply some critical kernel fixes without a reboot, but you still reboot into the new kernel in the next window.

**Ubuntu/Debian:** `apt list --upgradable`, `unattended-upgrades` for security pockets, and `/var/run/reboot-required` instead of `needs-restarting -r`.

## 9. File integrity: `rpm -Va` and AIDE

### 9.1 `rpm -V`: compare files with the RPM database

```bash
rpm -Va 2>/dev/null | grep -v '^.......T'     # all packages, skip mtime-only changes
rpm -Vf /usr/bin/ssh                          # verify the package that owns this file
rpm -V openssh-server
```

```text
S.5....T.  c /etc/ssh/sshd_config
.M.......    /usr/bin/passwd
S.5....T.    /usr/sbin/sshd
```

| Column | Meaning |
|---|---|
| `S` | Size differs |
| `M` | Mode (permissions or type) differs |
| `5` | Digest (content checksum) differs |
| `D` | Device major/minor mismatch |
| `L` | Symlink target differs |
| `U` / `G` | Owner / group differs |
| `T` | Modification time differs |
| `P` | Capabilities differ |
| `c` (letter before the path) | Config file: changes are **expected** |

Reading the example: the modified `sshd_config` is normal (you configured it). `/usr/bin/passwd` lost its expected mode, which may be someone removing the setuid bit (check before "fixing"). A changed digest on **`/usr/sbin/sshd`**, a binary, is a serious red flag: treat it as a potential compromise, isolate the host, and investigate before reinstalling. Remember that `rpm -V` trusts the local RPM database, which an attacker with root can also modify, so it's a quick check, not proof of integrity.

To restore a damaged non-config file from a trusted repository: `dnf reinstall <package>`. For permission and ownership drift: `rpm --setperms <pkg>` and `rpm --setugids <pkg>`.

**Ubuntu/Debian:** `dpkg --verify` or `debsums -c`.

### 9.2 AIDE: baseline and compare

AIDE builds a database of hashes and attributes (including SELinux labels and ACLs) and compares the system against it later.

```bash
dnf install -y aide
aide --init
mv /var/lib/aide/aide.db.new.gz /var/lib/aide/aide.db.gz
aide --check
```

```text
AIDE found differences between database and filesystem!!
Summary:
  Total number of entries:      48211
  Added entries:                1
  Removed entries:              0
  Changed entries:              2
---------------------------------------------------
Changed entries:
f   ...    .C... : /etc/ssh/sshd_config
f   ...    .C... : /etc/sudoers.d/50-appteam
```

After an approved change, update the baseline (`aide --update`, then move the new database into place). Run `aide --check` daily via a systemd timer or cron, and keep a copy of the database **off the host** (or on read-only media); a baseline that lives on the server it protects can be rewritten by an attacker.

## 10. Reduce the attack surface: services, ports, fail2ban

```bash
ss -tulpn                                         # what listens, and which process
systemctl list-unit-files --type=service --state=enabled
firewall-cmd --list-all
```

```text
Netid State  Local Address:Port  Peer Address:Port Process
udp   UNCONN 0.0.0.0:111         0.0.0.0:*         users:(("rpcbind",pid=812,fd=5))
tcp   LISTEN 0.0.0.0:22          0.0.0.0:*         users:(("sshd",pid=1021,fd=3))
tcp   LISTEN 127.0.0.1:631       0.0.0.0:*         users:(("cupsd",pid=1188,fd=7))
tcp   LISTEN 0.0.0.0:80          0.0.0.0:*         users:(("nginx",pid=1302,fd=6))
tcp   LISTEN 0.0.0.0:443         0.0.0.0:*         users:(("nginx",pid=1302,fd=7))
```

`rpcbind` on a web server that doesn't use NFS, and `cupsd` on a server with no printers, are unnecessary:

```bash
systemctl disable --now rpcbind.service rpcbind.socket cups.service
systemctl mask rpcbind.socket                     # stop anything re-activating it
firewall-cmd --permanent --remove-service=cockpit
firewall-cmd --permanent --remove-service=dhcpv6-client
firewall-cmd --reload
```

Disable the service **and** close the firewall port; either alone leaves a gap (a service bound to localhost is less exposed, but a firewall rule without a service is still an audit finding in some scanners). Socket-activated services need the `.socket` unit disabled or masked too.

**Ubuntu/Debian:** `ufw status verbose`, `ufw delete allow 80/tcp`.

### fail2ban

**fail2ban** (from EPEL on RHEL) watches logs for repeated failures and adds temporary firewall bans per source IP. It reduces brute-force noise against SSH and web logins.

```ini
# /etc/fail2ban/jail.d/sshd.local
[sshd]
enabled  = true
backend  = systemd
maxretry = 5
findtime = 10m
bantime  = 1h
ignoreip = 127.0.0.1/8 10.0.0.0/24
```

```bash
dnf install -y epel-release && dnf install -y fail2ban
systemctl enable --now fail2ban
fail2ban-client status sshd
fail2ban-client set sshd unbanip 192.0.2.50
```

:::tip
Always put your jump hosts and monitoring addresses in `ignoreip`, or the first time someone mistypes a password from the bastion, all admins sharing that NAT IP are banned. fail2ban complements, never replaces, key-only authentication: with `PasswordAuthentication no` there is nothing to brute-force.
:::

## 11. SELinux stays on

Every topic in this module touches SELinux: sshd reading keys, sshd binding ports, auditd recording AVCs, AIDE recording labels, `rpm -V` checking modes. SELinux is the layer that limits the damage when something else (a web app, a service, a misconfigured sudo rule) is compromised.

```bash
getenforce
sestatus
grep ^SELINUX= /etc/selinux/config
```

| Situation | Wrong fix | Right fix |
|---|---|---|
| Key login denied, AVC on `authorized_keys` | `setenforce 0` | `restorecon -Rv ~/.ssh` |
| Home directories in `/data/home` | Disable SELinux | `semanage fcontext -a -e /home /data/home; restorecon -Rv /data/home` |
| sshd on port 2222 won't start | Disable SELinux | `semanage port -a -t ssh_port_t -p tcp 2222` |
| App needs a behaviour SELinux blocks | `SELINUX=disabled` | `getsebool -a | grep <app>`, `setsebool -P`; as last resort a reviewed `audit2allow` module |
| Vendor says "requires SELinux disabled" | Comply blindly | Run in permissive **for that domain only** (`semanage permissive -a <domain_t>`) while you collect AVCs, then write policy; push the vendor |

:::warn
`SELINUX=disabled` in `/etc/selinux/config` is a CIS failure in itself, and re-enabling it later requires a full relabel (`touch /.autorelabel; reboot`) that can take a long time on large filesystems. If you ever switch to permissive to diagnose, write down the time, revert with `setenforce 1`, and use the AVCs collected in that window (`ausearch -m AVC -ts <time> | audit2why`) to build the real fix.
:::

## 12. A sensible hardening checklist (CIS-style, high level)

The **CIS Benchmarks** for RHEL 9 (and DISA STIG) contain hundreds of controls. You don't need to memorise them; you need a short, defensible baseline that you automate (Ansible, or OpenSCAP: `oscap xccdf eval --profile cis ...` with `scap-security-guide`) and verify continuously.

| Area | Control | How to check |
|---|---|---|
| Patching | Monthly full updates, urgent security-only out of cycle; reboot when needed | `dnf updateinfo summary`, `needs-restarting -r` |
| SELinux | Enforcing, targeted policy | `getenforce` |
| SSH | No root login, no passwords, `AllowGroups`, DEFAULT or stricter crypto policy, `MaxAuthTries` ≤ 4 | `sshd -T` |
| Accounts | Unique named accounts, no shared root password use, leavers expired | `chage -l`, `lastlog`, `awk -F: '$3==0' /etc/passwd` (only root) |
| Authentication | `pam_faillock` enabled, password aging/complexity per policy (`pwquality.conf`) | `authselect current`, `faillock` |
| sudo | Named groups, no unexplained `NOPASSWD`, `visudo -c` clean, logging | `grep -R NOPASSWD /etc/sudoers*` |
| Services | Only required services enabled; no telnet, rsh, FTP, TFTP | `systemctl list-unit-files --state=enabled`, `ss -tulpn` |
| Firewall | firewalld enabled, default zone minimal | `firewall-cmd --list-all` |
| Logging & audit | auditd enabled with rules for identity, sudoers, sshd, critical app config; logs shipped off-host | `auditctl -l`, `systemctl is-active auditd rsyslog` |
| Integrity | AIDE baseline and daily check | `aide --check` |
| Crypto | System policy DEFAULT (or FUTURE/FIPS where mandated), no LEGACY leftovers | `update-crypto-policies --show` |
| Filesystems | Separate `/tmp`, `/var`, `/var/log` where feasible; `nodev,nosuid,noexec` on `/tmp` | `findmnt /tmp` |
| Time | chrony synced (audit trails need correct time) | `chronyc tracking` |

:::tip
Apply hardening in **small, reversible, automated steps**, starting on non-production, and run a scan **before and after** so you can prove the improvement. Most hardening outages come from applying a 300-control profile to production in one go and discovering, for example, that `noexec` on `/tmp` breaks an installer, or `AllowGroups` blocks the backup account.
:::

## 13. Troubleshooting runbook

| Symptom | Check (command) | Likely cause | Fix |
|---|---|---|---|
| `Permission denied (publickey)` | `journalctl -u sshd`; look for "bad ownership or modes" | Wrong owner/mode on home, `~/.ssh`, `authorized_keys` | `chown`, `chmod 700 ~/.ssh`, `chmod 600 authorized_keys`, `chmod go-w ~` |
| Key refused, permissions correct | `ausearch -m AVC -ts recent -c sshd`; `ls -Z ~/.ssh` | Wrong SELinux label (`user_tmp_t`, `default_t`, etc.) | `restorecon -Rv ~/.ssh`; `semanage fcontext -a -e /home /newhome` |
| Edited `sshd_config`, no effect | `sshd -T | grep <keyword>`; `grep -R` in `sshd_config.d` | Earlier drop-in wins (first value wins) | Put setting in an early drop-in (`10-site.conf`), reload |
| sshd won't start after change | `sshd -t`; `journalctl -u sshd` | Syntax error, or new port not labelled | Fix syntax; `semanage port -a -t ssh_port_t -p tcp <port>` |
| `no matching key exchange method found` | `ssh -vvv`; `sshd -T | grep kex`; `update-crypto-policies --show` | Client too old for server policy, or vice versa | Upgrade client; per-host client option for legacy devices; avoid global LEGACY |
| Host key changed warning | Server console: `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` | Rebuild/redeploy, or MITM | Verify fingerprint, then `ssh-keygen -R host` |
| Correct password rejected | `faillock --user alice`; `chage -l alice` | Locked by faillock, or expired | `faillock --user alice --reset`; fix the source of failures; `chage` |
| "Your account has expired" | `chage -l alice` | Account expiry date reached | `chage -E -1` or a new date, with approval |
| `user NOT in sudoers` | `sudo -l -U alice`; `id alice` | Not in wheel / drop-in, or not re-logged in | Add group, re-login; check drop-in filename has no dot |
| `command not allowed` | `sudo -l`; compare exact path and args | Rule mismatch | Correct `Cmnd_Alias` (full path, exact args) |
| sudo totally broken | `visudo -c` from root/console | Syntax error or bad mode in sudoers.d | Fix with `visudo -f`; `chmod 0440` |
| Scanner: weak SSH KEX/ciphers/MACs | `nmap --script ssh2-enum-algos -p22 host`; `sshd -T` | LEGACY policy or old overrides | `update-crypto-policies --set DEFAULT[:SUBPOLICY]` or sshd drop-in, restart sshd |
| Scanner: old OpenSSH version | `rpm -q openssh-server`; `rpm -q --changelog openssh | grep CVE-...` | Banner matching vs backported fixes | Patch; provide backport evidence; request credentialed scan |
| Scanner: HTTP on 80 | `curl -sI http://host` | Content served in cleartext | 301 redirect to HTTPS + HSTS, or close port 80 |
| "Who changed this file?" | `ausearch -k <key> -i`; `ausearch -f <file> -i` | Need watch rule first | Add `-w` rule in `/etc/audit/rules.d`, `augenrules --load` |
| Reboot needed after patching? | `needs-restarting -r` | New kernel/glibc/openssl | Schedule reboot; `needs-restarting -s` for services |
| Suspicious binary change | `rpm -Vf /path/binary`; `aide --check` | Tampering or bad manual change | Isolate host, investigate, then `dnf reinstall` or rebuild |

## 14. Break-fix labs

All labs assume a **disposable** Rocky Linux 9 or AlmaLinux 9 VM (RHEL 9 with a developer subscription works too), SELinux enforcing, run as root unless stated. Keep a root console open (hypervisor console) throughout. Labs build on each other: Lab 08-A creates the user `alice` used later.

:::lab Lab 08-A — SSH key login fails: permissions and SELinux label on authorized_keys
**Goal:** reproduce the two most common causes of `Permission denied (publickey)` with a correct key, diagnose each from the server logs, and fix them properly.

**Setup.**

```bash
useradd alice
passwd alice                                 # set a lab password
useradd tester                               # the "client" user on the same VM
su - tester -c "ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519"
mkdir -p /home/alice/.ssh
cp /home/tester/.ssh/id_ed25519.pub /home/alice/.ssh/authorized_keys
chown -R alice:alice /home/alice/.ssh
chmod 700 /home/alice/.ssh && chmod 600 /home/alice/.ssh/authorized_keys
restorecon -Rv /home/alice/.ssh
su - tester -c "ssh -o StrictHostKeyChecking=accept-new alice@localhost id"
```

The last command should print alice's `uid=...` line: the baseline works.

**Break it (part 1: permissions).**

```bash
chmod 777 /home/alice/.ssh
```

**Symptoms.**

```bash
su - tester -c "ssh -o BatchMode=yes alice@localhost id"
```

```text
alice@localhost: Permission denied (publickey,gssapi-keyex,gssapi-with-mic,password).
```

**Diagnose.**

1. Client view: `su - tester -c "ssh -vvv -o BatchMode=yes alice@localhost id" 2>&1 | grep -E 'Offering|accepts|continue'`. The key is offered, never accepted.
2. Server view: `journalctl -u sshd -n 20 --no-pager`. You'll see `Authentication refused: bad ownership or modes for directory /home/alice/.ssh`.

**Fix.**

```bash
chmod 700 /home/alice/.ssh
```

**Verify.** The `ssh ... id` command works again.

**Break it (part 2: SELinux label).** Simulate a key file built in `/tmp` and moved into place (here forced with `chcon` so the result is predictable):

```bash
chcon -t user_tmp_t /home/alice/.ssh/authorized_keys
ls -Z /home/alice/.ssh/authorized_keys
```

**Symptoms.** Same `Permission denied (publickey...)`, but now the permissions are perfect: `ls -la /home/alice/.ssh` shows `drwx------` and `-rw-------`, owned by alice. The sshd journal shows no "bad ownership" line, and may show only `Connection closed by authenticating user alice`.

**Diagnose.**

```bash
ausearch -m AVC -ts recent -c sshd
```

Look for `denied { read }` (or `{ open }`/`{ getattr }`) on `name="authorized_keys"` with `tcontext=...user_tmp_t`. Optionally explain it:

```bash
ausearch -m AVC -ts recent -c sshd | audit2why
```

Optional diagnostic only: `setenforce 0`, retry (it works), then **immediately** `setenforce 1`. This proves SELinux is involved; it is not the fix.

**Fix.**

```bash
restorecon -Rv /home/alice/.ssh
```

**Verify.**

```bash
ls -Z /home/alice/.ssh/authorized_keys        # ssh_home_t
getenforce                                    # Enforcing
su - tester -c "ssh -o BatchMode=yes alice@localhost id"
```

**Clean-up.** Keep `alice` and `tester` for the next labs. If you did the permissive test, confirm `getenforce` says `Enforcing`.
:::

:::lab Lab 08-B — User locked out by pam_faillock
**Goal:** enable faillock through authselect, lock an account with failed SSH password attempts, see that even the right password fails, then diagnose and unlock it.

**Setup.**

```bash
authselect current                            # note the profile and features for clean-up
authselect enable-feature with-faillock
cp /etc/security/faillock.conf /root/faillock.conf.bak
sed -i 's/^# *deny = .*/deny = 3/; s/^# *unlock_time = .*/unlock_time = 600/' /etc/security/faillock.conf
grep -E '^(deny|unlock_time)' /etc/security/faillock.conf
sshd -T | grep -E 'passwordauthentication|kbdinteractive'
```

If `passwordauthentication` is `no` (common on cloud images), add a temporary drop-in for the lab:

```bash
echo 'PasswordAuthentication yes' > /etc/ssh/sshd_config.d/05-lab-password.conf
sshd -t && systemctl reload sshd
```

**Break it.** As `tester`, try to log in as alice with a **wrong** password three times (type anything at the prompt):

```bash
su - tester -c "ssh -o PubkeyAuthentication=no -o PreferredAuthentications=password,keyboard-interactive alice@localhost"
```

Repeat until you've failed three times, then try once more with alice's **correct** password.

**Symptoms.** The correct password is rejected with `Permission denied, please try again.` There is no "account locked" message on the client. Key-based login (Lab 08-A) may still work, which proves the key path skips the counter.

**Diagnose.**

```bash
faillock --user alice
journalctl -u sshd --since "10 min ago" | grep -Ei 'faillock|failed password'
```

You'll see three `V` entries from `RHOST ::1` or `127.0.0.1`, and a `pam_faillock(sshd:auth): Consecutive login failures for user alice account temporarily locked` line. Also rule out the other cause: `chage -l alice` shows nothing expired.

**Fix.** In real life, first find what generated the failures (the source address column). Then unlock:

```bash
faillock --user alice --reset
```

**Verify.** `faillock --user alice` shows no entries, and the correct password works.

**Clean-up.**

```bash
rm -f /etc/ssh/sshd_config.d/05-lab-password.conf
sshd -t && systemctl reload sshd
cp /root/faillock.conf.bak /etc/security/faillock.conf
authselect disable-feature with-faillock      # only if it was NOT enabled before the lab
authselect check
```
:::

:::lab Lab 08-C — Remediate a weak SSH key exchange finding
**Goal:** reproduce a scanner finding ("SSH weak key exchange / SHA-1 algorithms enabled") by switching to the LEGACY policy, detect it the way a scanner would, and remediate it twice: once with crypto-policies and once with an sshd drop-in.

**Setup.**

```bash
dnf install -y nmap
update-crypto-policies --show                  # note the original, usually DEFAULT
nmap -p 22 --script ssh2-enum-algos localhost > /root/ssh-algos-before.txt
```

**Break it.**

```bash
update-crypto-policies --set LEGACY
systemctl restart sshd
```

**Symptoms.**

```bash
nmap -p 22 --script ssh2-enum-algos localhost | sed -n '/kex_algorithms/,/server_host_key/p'
sshd -T | grep -i kexalgorithms
diff <(nmap -p 22 --script ssh2-enum-algos localhost) /root/ssh-algos-before.txt
```

Compared with the baseline, extra SHA-1 based algorithms appear (for example `diffie-hellman-group14-sha1` and `diffie-hellman-group-exchange-sha1` in KEX, `ssh-rsa` in host key algorithms, and possibly CBC ciphers). The exact additions depend on your RHEL 9 minor release; the diff is what matters. This is what a scanner reports as a finding.

**Diagnose.**

1. `update-crypto-policies --show` says `LEGACY`: the cause.
2. `grep -R -i -E 'kexalgorithms|ciphers|macs' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/` shows no explicit override; the lists come from `/etc/crypto-policies/back-ends/opensshserver.config`, included by `50-redhat.conf`.
3. In a real incident, ask why LEGACY was set (often a migration needing an old client) before changing it.

**Fix, option 1 (preferred): crypto-policies.**

```bash
update-crypto-policies --set DEFAULT
systemctl restart sshd
```

**Fix, option 2: sshd drop-in** (when LEGACY must stay for another reason, e.g. an old TLS client). First set LEGACY again to reproduce, then:

```bash
update-crypto-policies --set LEGACY
cat > /etc/ssh/sshd_config.d/40-kex-hardening.conf <<'EOF'
KexAlgorithms curve25519-sha256,curve25519-sha256@libssh.org,ecdh-sha2-nistp256,ecdh-sha2-nistp384,ecdh-sha2-nistp521,diffie-hellman-group16-sha512,diffie-hellman-group18-sha512
EOF
sshd -t && systemctl restart sshd
```

**Verify.**

```bash
sshd -T | grep -i kexalgorithms
nmap -p 22 --script ssh2-enum-algos localhost | sed -n '/kex_algorithms/,/server_host_key/p'
ssh -o KexAlgorithms=diffie-hellman-group14-sha1 -o BatchMode=yes alice@localhost true
```

The SHA-1 KEX algorithms are gone, and the last command fails with `no matching key exchange method found`. Also confirm a normal login still works (Lab 08-A command). Try naming the file `60-kex-hardening.conf` instead and re-run `sshd -T`: the override is ignored because `50-redhat.conf` set `KexAlgorithms` first.

**Clean-up.**

```bash
rm -f /etc/ssh/sshd_config.d/40-kex-hardening.conf /etc/ssh/sshd_config.d/60-kex-hardening.conf
update-crypto-policies --set DEFAULT            # or the original value you noted
systemctl restart sshd
```

Red Hat recommends a reboot after changing the crypto policy so every running service picks it up; on the lab VM, reboot now.
:::

:::lab Lab 08-D — Audit who changed a file with auditd
**Goal:** put a persistent audit watch on an application config directory, have a user change a file through sudo, and prove who did it, even though the change was made as root.

**Setup.**

```bash
mkdir -p /opt/myapp/conf
echo "listen_port=8080" > /opt/myapp/conf/app.conf
echo 'alice ALL=(ALL) ALL' > /etc/sudoers.d/90-lab-alice
chmod 0440 /etc/sudoers.d/90-lab-alice
visudo -c
cat > /etc/audit/rules.d/60-myapp.rules <<'EOF'
-w /opt/myapp/conf/ -p wa -k myapp-conf
EOF
augenrules --load
auditctl -l | grep myapp
```

**Break it.** Log in as alice over SSH (so her login UID is set), become root, and change the file:

```bash
su - tester -c "ssh -t alice@localhost"
# now, as alice on the remote shell:
sudo -i
sed -i 's/8080/9090/' /opt/myapp/conf/app.conf
exit; exit
```

**Symptoms.** The app config changed, `ls -l /opt/myapp/conf/app.conf` shows `root root`, and `/var/log/secure` contains many root sessions. "Who did it?" is not obvious from file ownership.

**Diagnose.**

```bash
ausearch -k myapp-conf -i -ts recent
```

Find the `SYSCALL` record with `comm=sed`. It shows `auid=alice uid=root euid=root`, the terminal, and the session number `ses=N`. Then:

```bash
ausearch -k myapp-conf -i -ts recent | grep -E 'auid=|name=/opt'
aureport -f -i -ts today | grep myapp
journalctl _COMM=sudo --since "30 min ago"
ausearch -m USER_LOGIN -i -ts today | grep alice     # when and from where alice logged in
```

`sed -i` writes a temporary file and renames it, so you'll see records for the temporary name and the rename; the directory watch catches all of them. Combine the evidence: alice logged in via SSH from `localhost` at time T, ran `sudo -i`, and in the same session (`ses=N`) `sed` modified `app.conf`.

**Fix.** There's nothing to "fix" in the system; the outcome is the answer to the incident question. Revert the config if the change was unauthorised:

```bash
sed -i 's/9090/8080/' /opt/myapp/conf/app.conf
```

**Verify.** `ausearch -k myapp-conf -i -ts recent` now also shows **your** revert (with your own `auid`), so the audit trail stays complete.

**Clean-up.**

```bash
rm -f /etc/audit/rules.d/60-myapp.rules /etc/sudoers.d/90-lab-alice
augenrules --load
visudo -c
rm -rf /opt/myapp
userdel -r alice; userdel -r tester            # if you're done with all labs
```
:::

## Command cheat sheet

| Task | Command |
|---|---|
| Test sshd config syntax | `sshd -t` |
| Show effective sshd config | `sshd -T`, `sshd -T -C user=u,host=h,addr=a` |
| Follow sshd log | `journalctl -fu sshd` (Ubuntu: `-u ssh`) |
| Client debug | `ssh -vvv user@host` |
| Fix SSH file permissions | `chmod 700 ~/.ssh; chmod 600 ~/.ssh/authorized_keys; chmod go-w ~` |
| Show / fix SSH SELinux labels | `ls -Z ~/.ssh`, `restorecon -Rv ~/.ssh` |
| Label a new home root | `semanage fcontext -a -e /home /data/home; restorecon -Rv /data/home` |
| Allow sshd on new port | `semanage port -a -t ssh_port_t -p tcp 2222` |
| Recent SELinux denials | `ausearch -m AVC -ts recent`, `... | audit2why` |
| Remove stale host key | `ssh-keygen -R host` |
| Show host key fingerprint | `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` |
| Crypto policy | `update-crypto-policies --show`, `--set DEFAULT[:SUB]` |
| Scan SSH algorithms | `nmap -p22 --script ssh2-enum-algos host` |
| Edit/check sudoers | `visudo`, `visudo -f /etc/sudoers.d/x`, `visudo -c` |
| What may a user sudo | `sudo -l -U alice` |
| PAM profile | `authselect current`, `authselect enable-feature with-faillock` |
| Lockouts | `faillock --user alice`, `faillock --user alice --reset` |
| Password/account aging | `chage -l alice`, `chage -M 90 -W 7`, `chage -d 0`, `chage -E -1` |
| Audit watch (temp) | `auditctl -w /path -p wa -k key` |
| Audit rules (persistent) | `/etc/audit/rules.d/*.rules` + `augenrules --load` |
| Search audit | `ausearch -k key -i`, `ausearch -f file -i`, `aureport -f -i` |
| Pending security updates | `dnf updateinfo summary`, `dnf updateinfo list --security` |
| Security-only update | `dnf upgrade --security`, `--sec-severity=Critical`, `--advisory=ID` |
| Reboot needed? | `needs-restarting -r`, services: `needs-restarting -s` |
| Kernel check | `uname -r`, `rpm -q kernel`, `grubby --default-kernel` |
| Package file integrity | `rpm -Va`, `rpm -Vf /path`, `dnf reinstall pkg` |
| AIDE | `aide --init`, `aide --check`, `aide --update` |
| Listening ports | `ss -tulpn` |
| Disable a service | `systemctl disable --now svc; systemctl mask svc.socket` |
| Firewall | `firewall-cmd --list-all`, `--permanent --remove-service=x` |
| fail2ban | `fail2ban-client status sshd`, `fail2ban-client set sshd unbanip IP` |
| Backport evidence | `rpm -q --changelog openssh | grep CVE-YYYY-NNNN` |

:::summary
- Access problems are diagnosed on the **server**: `journalctl -u sshd`, `/var/log/secure`, `ausearch -m AVC`, `faillock`, `chage -l`. The client only says "denied".
- `sshd -t` before every reload; `sshd -T` to see what sshd really uses. On RHEL 9 drop-ins in `/etc/ssh/sshd_config.d/` are read first and **the first value wins**, including the crypto-policy include in `50-redhat.conf`.
- Key logins need correct **ownership/modes** (`~` not group/world-writable, `~/.ssh` 700, `authorized_keys` 600) **and** the SELinux type **`ssh_home_t`**. Fix labels with `restorecon` and `semanage fcontext`, never `setenforce 0`.
- Production SSH: `PermitRootLogin no`, `PasswordAuthentication no`, `AllowGroups`, and the host key changed warning is verified, not clicked through.
- Weak SSH KEX/cipher/MAC findings: use **crypto-policies** (`DEFAULT`, `FUTURE`, a custom `@SSH` subpolicy) or an early-sorting sshd drop-in; verify with `nmap ssh2-enum-algos`. Banner-based "old OpenSSH" findings on RHEL are usually backports: patch and show evidence.
- sudo: edit with `visudo`, drop-ins in `/etc/sudoers.d` without dots in the name, mode 0440; least privilege; avoid `NOPASSWD: ALL` and shell-escaping commands; use `sudoedit`.
- PAM on RHEL is managed by **authselect**; `pam_faillock` locks after `deny` failures (`faillock --user x --reset` after fixing the source); `chage` controls password and account expiry; `usermod -L` doesn't stop key logins.
- auditd: persistent `-w ... -p wa -k key` rules in `/etc/audit/rules.d`, `augenrules --load`, and `ausearch -k key -i`; the **auid** reveals the real person behind root.
- Patching: `dnf updateinfo`, `dnf upgrade --security`, `needs-restarting -r`, keep old kernels; integrity via `rpm -Va` and AIDE; remove unused services and ports; fail2ban reduces noise.
- Hardening is a small, automated, verified baseline (CIS/OpenSCAP), applied gradually, and **SELinux stays enforcing**.
:::

## Self-check questions

<div class="q" markdown="1">

**Q1.** A user reports `Permission denied (publickey)`. Their public key is in `/home/dev1/.ssh/authorized_keys`. What should you check first?

A. Set `PasswordAuthentication yes` so they can get in another way
B. Watch `journalctl -u sshd` on the server while they retry
C. Regenerate the server's host keys
D. Run `setenforce 0` and ask them to retry

:::answer
**Answer: B.** The client message is deliberately vague; the server journal says why (bad modes, wrong user, `AllowGroups`, etc.).

- A is wrong: it weakens security and doesn't find the cause.
- C is wrong: host keys affect host verification, not user key acceptance.
- D is wrong: disabling SELinux is never the first step, and at most a brief diagnostic after the logs point to SELinux.
:::
</div>

<div class="q" markdown="1">

**Q2.** Permissions on `~/.ssh` (700) and `authorized_keys` (600) are correct and owned by the user, but key login still fails. `ausearch -m AVC -ts recent` shows sshd denied `read` on `authorized_keys` with `tcontext=...user_tmp_t`. What is the correct fix?

A. `chmod 644 authorized_keys`
B. `setsebool -P ssh_sysadm_login on`
C. `restorecon -Rv ~/.ssh`
D. Set `SELINUX=permissive` in `/etc/selinux/config`

:::answer
**Answer: C.** The file has the wrong type (typically after `mv` from `/tmp`); `restorecon` resets it to `ssh_home_t`.

- A is wrong: permissions aren't the problem.
- B is wrong: that boolean concerns SELinux user role logins, not file labels.
- D is wrong: it disables enforcement system-wide instead of fixing one label.
:::
</div>

<div class="q" markdown="1">

**Q3.** You set `PasswordAuthentication no` at the end of `/etc/ssh/sshd_config` and reloaded sshd, but `sshd -T` still shows `passwordauthentication yes`. Why?

A. sshd needs a reboot to read the config
B. An earlier-read drop-in in `/etc/ssh/sshd_config.d/` already set it, and the first value wins
C. `PasswordAuthentication` can only be set by crypto-policies
D. `sshd -T` shows compiled defaults, not the config

:::answer
**Answer: B.** The `Include` at the top of `sshd_config` reads drop-ins first, and for most keywords sshd uses the first value it reads. Put site settings in an early drop-in.

- A is wrong: reload is enough.
- C is wrong: crypto-policies set algorithms, not authentication methods.
- D is wrong: `sshd -T` shows the effective configuration.
:::
</div>

<div class="q" markdown="1">

**Q4.** A scanner flags "SSH weak key exchange algorithms: diffie-hellman-group14-sha1" on a RHEL 9 server. `update-crypto-policies --show` prints `LEGACY`. What is the cleanest remediation if nothing still needs LEGACY?

A. `update-crypto-policies --set DEFAULT` and restart sshd (or reboot)
B. Uninstall OpenSSH and install a newer version from source
C. Add `KexAlgorithms` to the end of `sshd_config`
D. Block port 22 in the firewall

:::answer
**Answer: A.** The LEGACY policy enabled the SHA-1 KEX; returning to DEFAULT removes it consistently for SSH and TLS.

- B is wrong: it leaves vendor support and patch streams.
- C is wrong: at the end of the file it would be ignored because the crypto-policy include in `50-redhat.conf` sets it first.
- D is wrong: you'd lose SSH access rather than fix the finding.
:::
</div>

<div class="q" markdown="1">

**Q5.** A scanner reports "OpenSSH 8.7 < 9.x multiple vulnerabilities" on a fully patched RHEL 9 server based on the SSH banner. What is the right response?

A. Compile the latest upstream OpenSSH
B. Hide the banner with `Banner none`
C. Confirm with `rpm -q --changelog` / advisories that the CVEs are fixed in the installed package and request a backport exception or credentialed scan
D. Disable SSH

:::answer
**Answer: C.** Red Hat backports security fixes without changing the upstream version, so banner-based findings are often false positives; evidence closes them.

- A is wrong: unsupported and creates a patching burden.
- B is wrong: `Banner` sets a pre-login message, not the protocol version string.
- D is wrong: unnecessary and disruptive.
:::
</div>

<div class="q" markdown="1">

**Q6.** You created `/etc/sudoers.d/appteam.conf` granting `%appteam` rights to restart `myapp.service`. `visudo -c` passes, but members still get "user NOT in sudoers". What is the most likely cause?

A. sudo needs a reboot
B. Files in `/etc/sudoers.d` whose names contain a dot are ignored
C. The file must be mode 0777
D. Groups can't be used in sudoers

:::answer
**Answer: B.** sudo skips drop-ins with a `.` in the name or ending in `~`; rename to `50-appteam`. (Also check users re-logged in after being added to the group.)

- A is wrong: sudo reads its config on every invocation.
- C is wrong: drop-ins should be 0440; world-writable files are rejected.
- D is wrong: `%group` syntax is standard.
:::
</div>

<div class="q" markdown="1">

**Q7.** A developer asks for `dev1 ALL=(root) NOPASSWD: /usr/bin/vi /etc/myapp/*.conf` so they can edit configs. What do you recommend?

A. Approve it; it only allows editing config files
B. Grant `NOPASSWD: ALL` to avoid future tickets
C. Use `sudoedit` with a specific file path (and ideally require a password), because vi can spawn a root shell and the wildcard can match unintended paths
D. Add dev1 to `wheel`

:::answer
**Answer: C.** vi allows `:!sh` as root, and argument wildcards are dangerous; `sudoedit` edits a copy as the user and writes back as root.

- A is wrong: it is effectively full root access.
- B is wrong: maximal privilege with no second factor.
- D is wrong: wheel gives full sudo, far beyond the need.
:::
</div>

<div class="q" markdown="1">

**Q8.** A user says their correct password is rejected over SSH, though it worked yesterday. `chage -l` shows nothing expired. What do you check next?

A. `faillock --user <name>` to see if the account is locked by repeated failures
B. `rpm -Va`
C. `update-crypto-policies --show`
D. `needs-restarting -r`

:::answer
**Answer: A.** `pam_faillock` makes even correct passwords fail while the account is locked; the output also shows the source of the failures.

- B is wrong: file integrity isn't related to one user's password.
- C is wrong: crypto mismatches fail before authentication with a negotiation error.
- D is wrong: it reports pending reboots, not authentication.
:::
</div>

<div class="q" markdown="1">

**Q9.** HR asks you to block a leaver's access immediately. You run `usermod -L leaver`. Why is that not enough on RHEL?

A. `usermod -L` deletes the home directory
B. Locking the password doesn't stop SSH key-based logins when sshd uses PAM; also expire the account and remove keys
C. `usermod -L` only works after a reboot
D. It is enough; nothing else is needed

:::answer
**Answer: B.** A locked password still allows public key authentication with `UsePAM yes`. Use `chage -E 0` (or `usermod -e 1`), remove `authorized_keys`, and revoke sudo.

- A is wrong: it doesn't touch the home directory.
- C is wrong: it applies immediately.
- D is wrong: keys remain a valid way in.
:::
</div>

<div class="q" markdown="1">

**Q10.** `/etc/myapp/app.conf` was changed overnight and is owned by root. You need to know which person did it, and you plan ahead for next time. What do you set up?

A. `chattr +i` on the file
B. A persistent audit rule `-w /etc/myapp/ -p wa -k myapp-conf` in `/etc/audit/rules.d/`, loaded with `augenrules --load`, then `ausearch -k myapp-conf -i` to read the `auid`
C. `systemctl restart auditd` after `auditctl -w` without saving the rule
D. `rpm -V myapp`

:::answer
**Answer: B.** A persistent directory watch records every write, and `auid` shows the login identity even when the change was made via sudo as root.

- A is wrong: it prevents changes but doesn't tell you who tried.
- C is wrong: rules added with `auditctl` are lost at restart/reboot, and RHEL refuses `systemctl restart auditd`.
- D is wrong: it shows that a file differs from the package, not who changed it.
:::
</div>

<div class="q" markdown="1">

**Q11.** After applying security updates including a new kernel and `openssl-libs`, how do you determine whether the server needs a reboot?

A. `uname -r` alone
B. `needs-restarting -r` (exit code 1 means a reboot is needed)
C. `dnf updateinfo list --security`
D. `rpm -Va`

:::answer
**Answer: B.** It compares running state with updated core packages (kernel, glibc, openssl, systemd) and is scriptable via its exit code.

- A is wrong: it shows the running kernel but not whether other core libraries need a reboot.
- C is wrong: it lists available advisories, not what's running.
- D is wrong: it verifies files against the RPM database.
:::
</div>

<div class="q" markdown="1">

**Q12.** A vendor's install guide says "set `SELINUX=disabled`" because their agent fails under enforcing mode. What should you do?

A. Disable SELinux on all servers running the agent
B. Leave SELinux enforcing; put only the agent's domain in permissive with `semanage permissive -a`, collect AVCs, fix with labels/booleans or a reviewed policy module, and push the vendor for SELinux support
C. Run `setenforce 0` from a boot script
D. Uninstall `selinux-policy-targeted`

:::answer
**Answer: B.** This keeps the rest of the system protected while you gather the data for a proper fix.

- A is wrong: it removes mandatory access control everywhere, fails CIS, and needs a full relabel to undo.
- C is wrong: it is the same as disabling enforcement, just hidden.
- D is wrong: it breaks the system's policy entirely.
:::
</div>
