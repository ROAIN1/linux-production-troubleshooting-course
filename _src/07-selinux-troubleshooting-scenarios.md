<div class="cover" markdown="1">
<div class="mod">Module 07 · SELinux in Production</div>

# SELinux in Production: 20 Real Scenarios

<p>A universal SELinux troubleshooting runbook and decision flowchart, then twenty production incidents (web servers, reverse proxies, databases, containers, NFS, Samba, systemd, backups, password resets and custom applications), each with the exact error, the AVC evidence, the diagnosis, the fix, the verification and the prevention.</p>
</div>

## What you'll learn

- How to answer the first question of every incident fast: **is this actually SELinux, or is it permissions, firewall, config or code?**
- A repeatable runbook: **ausearch / journal -> audit2why -> decide: label, boolean, port, or custom module** (or "fix the application")
- How to read an **AVC denial** field by field and turn it into the right fix in under five minutes
- The four correct fixes and when each applies: `semanage fcontext` + `restorecon`, `setsebool -P`, `semanage port`, and a reviewed policy module (`audit2allow -M`, `sepolicy generate`)
- Twenty real scenarios you will meet on RHEL 9 / Rocky / Alma: Apache and nginx 403/502, custom ports, sshd on 2222, systemd 203/EXEC, Tomcat logs, podman/docker volumes, NFS and Samba, logrotate and cron, moved database datadirs, execmem, TLS keys, mail from web apps, restored backups, relabels after password resets, and truly custom applications
- The **anti-patterns** that turn a five-minute fix into a security finding, and the **AppArmor** equivalents for Ubuntu/Debian hosts

**Why it matters in production:** SELinux denials look exactly like ordinary failures. The user sees a 403, a 502, "Permission denied" or `status=203/EXEC`, nothing in the error says "SELinux", and the on-call engineer who does not know the runbook either wastes an hour on `chmod` or switches SELinux off and leaves the server unprotected. Every scenario in this module is fixable in minutes, with SELinux still enforcing, once you know where the evidence is.

## 1. How to think about SELinux denials

SELinux on RHEL runs the **targeted** policy: system services (httpd, nginx, sshd, mysqld, postgresql, smbd, named, container runtimes and hundreds more) run in **confined domains**, while interactive users and most custom services run **unconfined**. Every process has a **type** (its domain, for example `httpd_t`), and every file, directory, port and socket has a **type** too (`httpd_sys_content_t`, `http_port_t`). The policy is a big list of `allow` rules: "domain X may do action Y on type Z". Anything not allowed is denied, and the denial is logged as an **AVC** (Access Vector Cache) message.

:::story
Think of SELinux as a hotel key-card system layered on top of ordinary door locks. Linux permissions (`chmod`, `chown`) are the physical lock: does this key fit? SELinux is the card reader: is this guest's card programmed for this floor? A cleaner (httpd) may enter guest rooms (web content) but not the safe room (shadow passwords), even if someone leaves the door unlocked (`chmod 777`). When access is refused, the card reader writes the attempt to a log at the front desk (the audit log). You do not fix a refused card by ripping out the reader; you reprogram the card (boolean), relabel the door (file context), or register a new floor (port type).
:::

Both checks must pass. **DAC** (owner/group/mode, ACLs) is checked first; if DAC says no, SELinux is never consulted and there is **no AVC**. If DAC says yes, SELinux decides. That ordering gives you the most useful diagnostic rule in this module:

:::concept
**"Permission denied" + no AVC = not SELinux (usually).** Look at ownership, mode, ACLs, mount options (`noexec`, `ro`), systemd sandboxing (`ProtectHome=`, `ProtectSystem=`, `ReadOnlyPaths=`), firewalls and application config. **"Permission denied" + an AVC at the same timestamp = SELinux.** The one exception is a `dontaudit` rule, which silently denies without logging; section 2 shows how to reveal those.
:::

### Reading an AVC denial

```text
type=AVC msg=audit(1764064800.412:1187): avc:  denied  { read } for  pid=2311 comm="httpd" name="index.html" dev="dm-0" ino=3407944 scontext=system_u:system_r:httpd_t:s0 tcontext=unconfined_u:object_r:default_t:s0 tclass=file permissive=0
```

| Field | Value here | What it tells you |
|---|---|---|
| `denied { read }` | read | The permission that was refused (read, write, open, getattr, execute, name_bind, name_connect, execmem...) |
| `comm="httpd"` | httpd | The command name of the process that was refused |
| `name="index.html"` | index.html | The file or directory name (basename only; use `ino` with `find -inum` for the full path, or `ausearch -i`) |
| `scontext=...httpd_t` | httpd_t | **Source** context: the domain of the process. This is "who" |
| `tcontext=...default_t` | default_t | **Target** context: the type of the object. This is "what". `default_t` almost always means "a directory created at `/` that has no rule", such as `/data` |
| `tclass=file` | file | The object class: file, dir, tcp_socket, process, sock_file, lnk_file, capability |
| `permissive=0` | 0 | 0 = the access was actually blocked (enforcing). 1 = logged only (permissive) |

Read it as a sentence: **"httpd_t tried to read a file labelled default_t and was blocked."** Then ask: should httpd read this file? If yes, the file has the wrong label. If the label is right and the access is a legitimate optional feature, a boolean probably exists. If the target is a port, the port needs a type. Only if none of those fit do you consider a custom module.

## 2. The universal SELinux troubleshooting runbook

Use these steps, in this order, for every "it works with SELinux off" ticket.

**Step 1: Confirm the mode and that SELinux is even in play.**

```bash
getenforce
sestatus
```

```text
Enforcing

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

If it says `Permissive` or `Disabled`, SELinux is not blocking anything, so stop looking at it and move on to DAC, firewall and config.

**Step 2: Look for denials at the time of the failure.** Reproduce the failure, then immediately:

```bash
ausearch -m AVC,USER_AVC,SELINUX_ERR,USER_SELINUX_ERR -ts recent -i
```

`-ts recent` means the last 10 minutes; other useful values are `today`, `this-hour`, `boot`, or an explicit time such as `-ts 14:05:00`. `-i` interprets numeric fields (UIDs, syscalls, timestamps) into readable text. Filter by process with `-c httpd` or by domain with `--subject` is not a flag; use `-se httpd_t` (`--context`) instead:

```bash
ausearch -m AVC -ts today -c nginx
ausearch -m AVC -ts today -se httpd_t
```

If the `setroubleshoot-server` package is installed, every denial also produces a human-readable summary in the journal:

```bash
journalctl -t setroubleshoot --since "15 min ago"
sealert -l <alert-id-from-the-journal-line>
sealert -a /var/log/audit/audit.log      # analyse the whole log; slow on big logs
```

```text
SELinux is preventing /usr/sbin/nginx from name_connect access on the tcp_socket port 5000.

*****  Plugin connect_ports (85.9 confidence) suggests   *********************
If you want to allow /usr/sbin/nginx to connect to network port 5000
Then you need to modify the port type.
Do
# semanage port -a -t PORT_TYPE -p tcp 5000
    where PORT_TYPE is one of the following: dns_port_t, http_cache_port_t, http_port_t, ...

*****  Plugin catchall_boolean (7.33 confidence) suggests   ******************
If you want to allow httpd to can network connect
Then you must tell SELinux about this by enabling the 'httpd_can_network_connect' boolean.
Do
setsebool -P httpd_can_network_connect 1
```

:::tip
`setroubleshoot-server` is extremely helpful on a dev or staging box, but it pulls in Python and a D-Bus service and analyses every denial, so some hardened production builds leave it out. `ausearch` + `audit2why` (from `policycoreutils-python-utils`) gives you the same answers with nothing extra running. Install `policycoreutils-python-utils` everywhere; it provides `semanage`, `audit2why` and `audit2allow`.
:::

**Step 3: Ask audit2why what kind of denial it is.**

```bash
ausearch -m AVC -ts recent --raw | audit2why
```

`audit2why` puts every denial in one of a few buckets:

| audit2why says | Meaning | Your fix |
|---|---|---|
| `The boolean X was set incorrectly` / `One of the following booleans was set incorrectly` | Policy already supports this access behind an off-by-default switch | `setsebool -P X 1` (choose the **narrowest** boolean listed) |
| `Missing type enforcement (TE) allow rule` | No rule allows it. Most often the **object has the wrong label**; sometimes the app really needs something new | First check the label (`ls -Z`, `restorecon -nv`); if the label is correct, consider a port type, a different directory, or a custom module |
| `Policy constraint violation` / `Constraint violation` | An MLS/MCS or user/role constraint failed, typical of containers with mismatched categories (`s0:c1,c2`) | Fix the labels/categories (container `:z`/`:Z`), not with `audit2allow` |
| `Unknown - would be allowed by active policy` | The policy now allows it (a boolean or module changed since the denial) | Re-test; the denial is stale |

**Step 4: Decide the fix using the flowchart below.** Then apply it, re-test, and confirm no new AVCs.

**Step 5: If there is still a failure but no AVC, check for silenced denials.** Policy contains `dontaudit` rules for noisy, harmless accesses. Occasionally the real denial is one of them:

```bash
semodule -DB          # rebuild policy with dontaudit rules disabled (more logging)
# reproduce the problem, then:
ausearch -m AVC -ts recent
semodule -B           # rebuild with dontaudit rules back on - do not forget
```

**Step 6 (diagnostic only): prove or disprove SELinux by switching one domain to permissive.** This is far safer than `setenforce 0`, because every other service stays enforcing:

```bash
semanage permissive -a httpd_t      # only httpd_t is permissive; logs denials but allows them
# reproduce: if it now works, SELinux is the cause and ausearch shows every needed access
semanage permissive -d httpd_t      # always remove it afterwards
```

If you must use the global switch for a ten-second test, revert it immediately: `setenforce 0; <test>; setenforce 1`. It is not persistent across reboots and must never be left on.

:::warn
Permissive mode is also how you **collect the full list** of denials: in enforcing mode the app fails at the first denial, so you only ever see one AVC at a time. With the domain permissive, the app runs through, and `ausearch` shows every access it needs in one pass. Use that list to choose labels and booleans, not as input to a blind `audit2allow`.
:::

### Decision flowchart (text form)

```text
Failure: 403 / 502 / Permission denied / 203/EXEC / bind failed
  |
  +-- getenforce = Permissive or Disabled? --yes--> Not SELinux. Check DAC, firewall, config, systemd sandboxing.
  |
  +-- ausearch -m AVC -ts recent shows nothing?
  |      +-- semodule -DB, reproduce, ausearch again (dontaudit). Still nothing --> Not SELinux. semodule -B.
  |
  +-- AVC found. Read scontext / tcontext / tclass / permission.
         |
         +-- tclass = tcp_socket/udp_socket and perm = name_bind -----> PORT: semanage port -a -t <svc>_port_t -p tcp N
         |
         +-- tclass = tcp_socket and perm = name_connect
         |        +-- audit2why names a boolean? --------------------> BOOLEAN: setsebool -P <narrowest boolean> 1
         |        +-- no boolean, fixed backend port -------------> PORT: label the destination port with a type the domain may reach
         |
         +-- tclass = file/dir/lnk_file/sock_file
         |        +-- tcontext is default_t, var_t, user_home_t, user_tmp_t, unlabeled_t, or obviously wrong
         |        |        --> LABEL: semanage fcontext -a -t <type> "<path>(/.*)?" ; restorecon -Rv <path>
         |        |            (or semanage fcontext -a -e <standard path> <new path> for a moved standard dir)
         |        +-- label correct, audit2why names a boolean --------> BOOLEAN
         |
         +-- perm = execmem / execstack / execheap / execmod --------> CODE: fix the app or library first; boolean only if justified
         |
         +-- "constraint violation" or mismatched s0:cX,cY categories -> LABEL with correct categories (containers :z/:Z)
         |
         +-- none of the above, the app is custom and legitimately needs it
                  --> MODULE: audit2allow -M (review .te!) or sepolicy generate for a real domain
```

The same logic as a table, which is easier to keep open during an incident:

| AVC pattern | Category | Correct tool | Typical example |
|---|---|---|---|
| `{ read open getattr }` on `default_t`, `var_t`, `user_home_t`, `user_tmp_t`, `unlabeled_t` | Wrong file label | `semanage fcontext` + `restorecon` | DocumentRoot in `/data`, files `mv`'d from home |
| `{ write add_name create }` on `httpd_sys_content_t` | Wrong (read-only) label for a writable dir | `semanage fcontext -t httpd_sys_rw_content_t` | Upload and cache directories |
| `{ name_bind }` on `unreserved_port_t` or another service's port | Port not typed for the service | `semanage port -a` (or `-m`) | nginx on 8085, sshd on 2222 |
| `{ name_connect }` from `httpd_t` | Optional feature disabled | `setsebool -P httpd_can_network_connect[_db] 1` | Reverse proxy 502, PHP to remote DB |
| `{ execute }` from `init_t` on `user_home_t` | Binary in the wrong place / label | Move to `/opt` or `/usr/local`, label `bin_t` | systemd 203/EXEC |
| `{ execmem }`, `{ execstack }` | App/library behaviour | Fix code/config; boolean as last resort | PCRE JIT in PHP, old native libs |
| Mismatched MCS categories, `container_t` on host files | Container labelling | `:z` / `:Z` or `container_file_t` | Volume mount Permission denied |
| Custom daemon with its own files, ports and needs | Missing policy | `sepolicy generate` + iterate, or reviewed `audit2allow -M` | In-house agent or daemon |

### Troubleshooting runbook

| Symptom | Check (command) | Likely cause | Fix |
|---|---|---|---|
| Works with `setenforce 0`, fails enforcing | `ausearch -m AVC -ts recent \| audit2why` | Label, boolean, port or missing rule | Per the decision table; then `setenforce 1` |
| Permission denied, no AVC | `ls -l`, `getfacl`, `findmnt -o TARGET,OPTIONS`, `systemctl show -p ProtectHome,ProtectSystem svc` | DAC, mount options, systemd sandbox | Fix ownership/mode/unit; SELinux is innocent |
| Permission denied, no AVC, still suspect SELinux | `semodule -DB`, reproduce, `ausearch` | `dontaudit` rule hiding the denial | Fix per the revealed AVC; `semodule -B` |
| File has "wrong" label after fix | `restorecon -Rvn /path` | No persistent rule; `chcon` used before | `semanage fcontext -a ...` then `restorecon -Rv` |
| `ValueError: Port tcp/N already defined` | `semanage port -l \| grep -w N` | Port already has a type | `semanage port -m -t <type> -p tcp N` |
| `restorecon` changes nothing but label is wrong | `semanage fcontext -l \| grep /path` | Rule order/regex wrong, or a more specific rule wins | Fix the regex; use `semanage fcontext -l -C` to view local rules |
| Process in unexpected domain | `ps -eZ \| grep app`, `ls -Z /path/to/binary` | Binary labelled wrong, so no domain transition | `restorecon` the binary; check its fcontext |
| Behaviour differs manual vs service | `id -Z` (your shell) vs `ps -eZ` (the service) | You test as `unconfined_t`; the service is confined | Reproduce via `systemctl start`, not by hand |

### Five commands to know by heart

```bash
ls -Z /path                          # label of files and directories (also ls -dZ for the dir itself)
ps -eZ | grep name                   # domain of running processes
semanage fcontext -l | grep /path    # what the label SHOULD be (policy rules + local rules)
restorecon -Rvn /path                # dry-run: what would change (-n), verbose (-v)
semanage port -l | grep -w 8085      # which type a port has
```

## 3. Twenty production scenarios

Each scenario follows the same shape: **Symptom** (what users and logs show), **Evidence** (the AVC), **Diagnosis**, **Fix**, **Verify** and **Prevention**. Because this module is scenario-driven, the scenarios are the labs: most include a **Reproduce on a lab VM** block that breaks a disposable Rocky/Alma 9 VM so you can practise the diagnosis, followed by a clean-up. Run the labs as root on a throwaway VM, never on a shared server.

Lab VM preparation (once):

```bash
dnf -y install httpd nginx policycoreutils-python-utils setroubleshoot-server setools-console \
  policycoreutils-devel selinux-policy-devel podman mariadb-server php php-fpm
getenforce                      # must print Enforcing
```

`httpd` and `nginx` both want port 80; in labs that use one, stop the other (`systemctl stop nginx`).

### Scenario 1: Apache DocumentRoot in /srv or /data returns 403 Forbidden

**Symptom.** You move the site to a data volume and point Apache at it. Browsers get **403 Forbidden**. `/var/log/httpd/error_log` shows:

```text
[core:error] [pid 2311:tid 2340] (13)Permission denied: [client 192.0.2.10:51544] AH00035: access to /index.html denied (filesystem path '/data/www/index.html') because search permissions are missing on a component of the path
```

The engineer checks `ls -l`: everything is `755`/`644`, owned correctly. DAC is fine.

**Evidence.**

```text
type=AVC msg=audit(1764064800.412:1187): avc:  denied  { getattr } for  pid=2311 comm="httpd" path="/data/www/index.html" dev="dm-0" ino=3407944 scontext=system_u:system_r:httpd_t:s0 tcontext=unconfined_u:object_r:default_t:s0 tclass=file permissive=0
```

**Diagnosis.** `httpd_t` may read `httpd_sys_content_t`. A directory created at `/` (like `/data`) gets `default_t`; directories created under `/srv` inherit `var_t`. Neither is readable as web content. `audit2why` says "Missing type enforcement (TE) allow rule": that is the cue to check the label, not to write a module.

```bash
ls -dZ /data/www /var/www/html
```

```text
unconfined_u:object_r:default_t:s0 /data/www
system_u:object_r:httpd_sys_content_t:s0 /var/www/html
```

**Fix.** Add a persistent rule, then apply it:

```bash
semanage fcontext -a -t httpd_sys_content_t "/data/www(/.*)?"
restorecon -Rv /data/www
```

```text
Relabeled /data/www from unconfined_u:object_r:default_t:s0 to unconfined_u:object_r:httpd_sys_content_t:s0
Relabeled /data/www/index.html from unconfined_u:object_r:default_t:s0 to unconfined_u:object_r:httpd_sys_content_t:s0
```

Apache also needs a `<Directory "/data/www">` block with `Require all granted`; without it you get a 403 with `AH01630: client denied by server configuration` and no AVC. That is a config error, not SELinux, and a good example of why you always check for an AVC first.

**Verify.**

```bash
curl -sI http://localhost/ | head -1          # HTTP/1.1 200 OK
ausearch -m AVC -ts recent -c httpd           # <no matches>
```

**Prevention.** Keep web content under `/var/www` when you can. When you can't, put the `semanage fcontext` rule in the same Ansible role that creates the directory (`community.general.sefcontext` module followed by a `restorecon` handler), so a rebuilt server gets the rule too.

**Reproduce on a lab VM.**

```bash
mkdir -p /data/www && echo "hello from data" > /data/www/index.html
sed -i 's#^DocumentRoot "/var/www/html"#DocumentRoot "/data/www"#' /etc/httpd/conf/httpd.conf
printf '<Directory "/data/www">\n  Require all granted\n</Directory>\n' > /etc/httpd/conf.d/data.conf
systemctl restart httpd && curl -s -o /dev/null -w '%{http_code}\n' http://localhost/   # 403
```

Clean-up: restore `DocumentRoot "/var/www/html"`, `rm /etc/httpd/conf.d/data.conf`, `semanage fcontext -d "/data/www(/.*)?"`, `rm -rf /data/www`, restart httpd.

### Scenario 2: Files moved with mv from /home into /var/www keep user_home_t

**Symptom.** A developer builds the site in their home directory and runs `sudo mv ~/site/* /var/www/html/`. The old pages still work; the new ones return **403 Forbidden**. Some images are broken.

**Evidence.**

```text
type=AVC msg=audit(1764065100.220:1201): avc:  denied  { read } for  pid=2315 comm="httpd" name="about.html" dev="dm-0" ino=1050712 scontext=system_u:system_r:httpd_t:s0 tcontext=unconfined_u:object_r:user_home_t:s0 tclass=file permissive=0
```

**Diagnosis.** `mv` within the same filesystem is a rename: the inode, and therefore its security xattr, moves unchanged. The files keep `user_home_t`. `cp` creates new files that inherit the destination label, which is why "it worked when I copied it yesterday".

```bash
ls -Z /var/www/html/
restorecon -Rvn /var/www/html      # dry run shows what is wrong
```

**Fix.** The policy already knows the right label for `/var/www/html`, so no `semanage` is needed:

```bash
restorecon -Rv /var/www/html
```

Do **not** turn on `httpd_enable_homedirs` or `httpd_read_user_content`; those are for serving `~/public_html`, and enabling them lets httpd read users' home content.

**Verify.** `ls -Z /var/www/html` shows `httpd_sys_content_t` everywhere; `curl` returns 200.

**Prevention.** Deploy with `cp`, `install`, `rsync` (without `-X`), or your CI pipeline, not `mv`. If you must `mv`, `mv -Z` resets the moved files to the destination's default context (coreutils 8.22 and later, so all of RHEL 8/9). Add `restorecon -R /var/www/html` as the last step of every deploy script; it is idempotent and cheap.

**Reproduce on a lab VM.**

```bash
echo "about" > /root/about.html          # /root is admin_home_t
mv /root/about.html /var/www/html/
ls -Z /var/www/html/about.html           # admin_home_t
curl -s -o /dev/null -w '%{http_code}\n' http://localhost/about.html     # 403
```

Clean-up: `rm /var/www/html/about.html`.

### Scenario 3: nginx reverse proxy returns 502 Bad Gateway

**Symptom.** nginx fronts a Python (gunicorn) or Node app listening on `127.0.0.1:5000`. `curl http://127.0.0.1:5000` from the shell works. Through nginx, users get **502 Bad Gateway**. `/var/log/nginx/error.log`:

```text
2026/11/25 10:14:03 [crit] 2402#2402: *7 connect() to 127.0.0.1:5000 failed (13: Permission denied) while connecting to upstream, client: 192.0.2.10, server: app.example.com, request: "GET / HTTP/1.1", upstream: "http://127.0.0.1:5000/", host: "app.example.com"
```

With Apache `mod_proxy` the same problem shows as **503 Service Unavailable** and `AH00957: http: attempt to connect to 127.0.0.1:5000 (127.0.0.1:5000) failed` with `(13)Permission denied`.

**Evidence.**

```text
type=AVC msg=audit(1764065643.118:1240): avc:  denied  { name_connect } for  pid=2402 comm="nginx" dest=5000 scontext=system_u:system_r:httpd_t:s0 tcontext=system_u:object_r:commplex_main_port_t:s0 tclass=tcp_socket permissive=0
```

**Diagnosis.** nginx runs in `httpd_t`, the same domain as Apache. By default a web server may accept connections but not open outbound connections to arbitrary ports. The `13: Permission denied` on a TCP `connect()` is the giveaway: a closed port gives `111: Connection refused`, never 13. `audit2why`:

```text
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

Choose the boolean that matches the intent. `nis_enabled` is unrelated and much broader; never pick it just because it is listed.

**Fix.** Three options, narrowest first:

| Option | Command | Allows | Use when |
|---|---|---|---|
| Relay boolean | `setsebool -P httpd_can_network_relay 1` | Connect to HTTP-ish ports (`http_port_t`, `http_cache_port_t`, and similar) | Backend is on 80, 443, 8080, 8443 or similar |
| Retype backend port | `semanage port -m -t http_port_t -p tcp 5000` (`-a` if the port is untyped) | httpd may then reach port 5000 as an HTTP port | Fixed backend port, you want least privilege |
| Any outbound TCP | `setsebool -P httpd_can_network_connect 1` | Connect to any TCP port | Many backends, dynamic ports, upstream APIs on the internet |

Most teams use `httpd_can_network_connect`, and it is acceptable for a reverse proxy. Note that retyping 5000 affects every domain that used `commplex_main_port_t`; check with `semanage port -l | grep -w 5000` first.

```bash
setsebool -P httpd_can_network_connect 1
getsebool httpd_can_network_connect          # httpd_can_network_connect --> on
```

**Verify.** `curl -sI http://app.example.com/` returns the backend's status; no new AVCs for `comm="nginx"`.

**Prevention.** Put the boolean in the same Ansible role that deploys the nginx site (`ansible.posix.seboolean: name=httpd_can_network_connect state=true persistent=true`). If the backend listens on a Unix socket instead (`proxy_pass http://unix:/run/myapp/app.sock`), the denial is `{ connectto }` or `{ write }` on `sock_file`; put the socket in a directory labelled `httpd_var_run_t` or follow the policy that ships with the app.

**Reproduce on a lab VM.**

```bash
python3 -m http.server 5000 --bind 127.0.0.1 &
cat > /etc/nginx/conf.d/proxy.conf <<'EOF'
server { listen 8080; location / { proxy_pass http://127.0.0.1:5000; } }
EOF
systemctl restart nginx; curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/    # 502
```

Clean-up: `setsebool -P httpd_can_network_connect 0`, `rm /etc/nginx/conf.d/proxy.conf`, `kill %1`, restart nginx. (Port 8080 is `http_cache_port_t`, which nginx is allowed to bind.)

### Scenario 4: PHP app cannot connect to a remote database

**Symptom.** A PHP app on the web tier connects to MariaDB on `10.0.0.20:3306`. The CLI test works (`php -r 'new PDO("mysql:host=10.0.0.20;dbname=app","app","...");'` as root). Through the browser the app shows its error page; the PHP-FPM log says:

```text
PHP Fatal error:  Uncaught PDOException: SQLSTATE[HY000] [2002] Permission denied in /var/www/html/app/db.php:12
```

PostgreSQL clients show `could not connect to server: Permission denied`.

**Evidence.**

```text
type=AVC msg=audit(1764066002.651:1302): avc:  denied  { name_connect } for  pid=3011 comm="php-fpm" dest=3306 scontext=system_u:system_r:httpd_t:s0 tcontext=system_u:object_r:mysqld_port_t:s0 tclass=tcp_socket permissive=0
```

**Diagnosis.** `php-fpm` runs in `httpd_t`. The CLI test ran in your `unconfined_t` shell, which is why it worked. That difference (manual test unconfined, service confined) explains a huge share of "works for me" SELinux tickets. The target is `mysqld_port_t`, a database port.

**Fix.** Use the database-specific boolean, which is narrower than `httpd_can_network_connect`:

```bash
setsebool -P httpd_can_network_connect_db 1
```

It covers the standard database port types (MySQL/MariaDB 3306, PostgreSQL 5432, and other well-known DB ports). If your DB listens on a non-standard port such as 3307, either add that port to the DB type on the **web server** (`semanage port -a -t mysqld_port_t -p tcp 3307`) so the boolean covers it, or fall back to `httpd_can_network_connect`.

**Verify.** Reload the page; the app connects. `ausearch -m AVC -ts recent -c php-fpm` shows nothing new.

**Prevention.** Test web apps through the web server (curl the URL), not via the PHP CLI as root. Record required booleans in the app's deployment documentation and role.

### Scenario 5: Web app cannot write its uploads or cache directory

**Symptom.** Users can browse, but uploading an avatar fails. The PHP log shows:

```text
PHP Warning:  move_uploaded_file(/var/www/html/app/uploads/avatar.png): Failed to open stream: Permission denied in /var/www/html/app/upload.php on line 31
```

A Laravel or WordPress install shows "Unable to write cache file" or "Could not create directory". The developer already did `chown -R apache:apache uploads` (or `nginx:nginx`), so DAC allows writing.

**Evidence.**

```text
type=AVC msg=audit(1764066410.018:1355): avc:  denied  { write } for  pid=3015 comm="php-fpm" name="uploads" dev="dm-0" ino=1312877 scontext=system_u:system_r:httpd_t:s0 tcontext=unconfined_u:object_r:httpd_sys_content_t:s0 tclass=dir permissive=0
```

**Diagnosis.** `httpd_sys_content_t` is **read-only** content for the web server. That is deliberate: if an attacker finds a remote code execution in the app, they cannot rewrite the PHP files. Writable paths must be labelled `httpd_sys_rw_content_t`.

**Fix.** Label only the directories that must be writable:

```bash
semanage fcontext -a -t httpd_sys_rw_content_t "/var/www/html/app/uploads(/.*)?"
semanage fcontext -a -t httpd_sys_rw_content_t "/var/www/html/app/cache(/.*)?"
restorecon -Rv /var/www/html/app/uploads /var/www/html/app/cache
```

| Type | httpd can | Use for |
|---|---|---|
| `httpd_sys_content_t` | read | Code, templates, static assets |
| `httpd_sys_rw_content_t` | read, write, create, delete | Uploads, cache, sessions stored in the app tree |
| `httpd_sys_ra_content_t` | read, append | Append-only files, such as app-specific logs |
| `httpd_sys_script_exec_t` | execute as CGI | CGI scripts |
| `httpd_log_t` | append/create logs | `/var/log/httpd`, `/var/log/nginx` |

**Verify.** Upload again; `ls -dZ /var/www/html/app/uploads` shows `httpd_sys_rw_content_t`.

**Prevention.** Do not "fix" this by labelling the whole `/var/www/html` as rw, and do not enable `httpd_unified` (it makes all httpd content types interchangeable, which throws away the code/data separation). Ask developers to keep writable data outside the code tree, ideally under `/var/lib/myapp`, and label that path.

:::prod
**Incident: "uploads broke after the security hardening sprint".** Symptom: avatars and attachments failed on all three web nodes the morning after a patch window. The engineer saw `move_uploaded_file ... Permission denied` and, under pressure, ran `chmod -R 777 uploads`, which changed nothing. Root cause: the hardening playbook had run `restorecon -R /var/www` for the first time; months earlier someone had fixed uploads with `chcon -t httpd_sys_rw_content_t`, which is not persistent, so the relabel reset it to read-only. Fix: `semanage fcontext -a -t httpd_sys_rw_content_t "/var/www/html/app/uploads(/.*)?"` then `restorecon`, and `chmod` reverted to `750`. Prevention: a CI lint that fails any playbook containing `chcon`, and a monthly `restorecon -Rvn /var/www` drift check that alerts on any output.
:::

### Scenario 6: nginx or httpd on a custom port fails to start

**Symptom.** You change `listen 80` to `listen 8085` (or Apache `Listen 8085`). The service fails to start:

```text
nginx: [emerg] bind() to 0.0.0.0:8085 failed (13: Permission denied)
```

```text
(13)Permission denied: AH00072: make_sock: could not bind to address [::]:8085
no listening sockets available, shutting down
AH00015: Unable to open logs
```

`systemctl status nginx` shows `Active: failed (Result: exit-code)`.

**Evidence.**

```text
type=AVC msg=audit(1764066822.333:1401): avc:  denied  { name_bind } for  pid=3302 comm="nginx" src=8085 scontext=system_u:system_r:httpd_t:s0 tcontext=system_u:object_r:unreserved_port_t:s0 tclass=tcp_socket permissive=0
```

**Diagnosis.** A confined service may only bind ports typed for it. `httpd_t` can bind `http_port_t` (80, 81, 443, 488, 8008, 8009, 8443, 9000) and `http_cache_port_t` (8080 and others). Port 8085 is `unreserved_port_t`.

```bash
semanage port -l | grep -E '^http_port_t|^http_cache_port_t'
semanage port -l | grep -w 8085
```

```text
http_cache_port_t              tcp      8080, 8118, 8123, 10001-10010
http_port_t                    tcp      80, 81, 443, 488, 8008, 8009, 8443, 9000
```

**Fix.**

```bash
semanage port -a -t http_port_t -p tcp 8085
firewall-cmd --permanent --add-port=8085/tcp && firewall-cmd --reload
systemctl restart nginx
```

If `semanage port -a` replies `ValueError: Port tcp/8085 already defined`, the port already belongs to another type (8081, for example, is assigned to a different service type in some policy versions). Use `-m` (modify) instead of `-a`, after confirming no other confined service on this host needs that port's original type:

```bash
semanage port -m -t http_port_t -p tcp 8081
```

**Verify.** `ss -tlnp | grep 8085` shows nginx listening; `semanage port -l -C` lists your local customisation.

**Prevention.** Prefer ports that are already typed (8080, 8443) when you have a free choice. When you don't, ship the `semanage port` change in the same change ticket as the config change (`community.general.seport` in Ansible).

**Reproduce on a lab VM.** `sed -i 's/listen       80;/listen       8085;/' /etc/nginx/nginx.conf; systemctl restart nginx`. Clean-up: revert the line, `semanage port -d -t http_port_t -p tcp 8085`.

### Scenario 7: sshd on port 2222 after a config change

**Symptom.** A hardening ticket moves SSH to port 2222. After `systemctl restart sshd`, new connections to 2222 get `Connection refused`. If the engineer also removed `Port 22`, sshd may be **down entirely** and only the existing session is keeping you on the box. Journal:

```text
sshd[4102]: error: Bind to port 2222 on 0.0.0.0 failed: Permission denied.
sshd[4102]: error: Bind to port 2222 on :: failed: Permission denied.
sshd[4102]: fatal: Cannot bind any address.
```

**Evidence.**

```text
type=AVC msg=audit(1764067201.900:1450): avc:  denied  { name_bind } for  pid=4102 comm="sshd" src=2222 scontext=system_u:system_r:sshd_t:s0-s0:c0.c1023 tcontext=system_u:object_r:unreserved_port_t:s0 tclass=tcp_socket permissive=0
```

**Diagnosis.** Same pattern as scenario 6 for `sshd_t`, which may only bind `ssh_port_t` (22 by default). RHEL's own `/etc/ssh/sshd_config` warns about this in the comment above `#Port 22`.

**Fix.** Do it in this order, from a session you will not close:

```bash
semanage port -a -t ssh_port_t -p tcp 2222
firewall-cmd --permanent --add-port=2222/tcp && firewall-cmd --reload
sshd -t                      # syntax check, prints nothing when OK
systemctl restart sshd
ss -tlnp | grep sshd
```

Keep `Port 22` and `Port 2222` both in `sshd_config` until you have logged in on 2222 from a **second** terminal. Only then remove 22 and its firewall rule.

**Verify.** `ssh -p 2222 admin@10.0.0.5` from another host works; `semanage port -l | grep ssh_port_t` shows `tcp 2222, 22`.

**Prevention.** Port changes for any confined daemon are three changes, not one: config, `semanage port`, firewall. Put all three in one Ansible task block, and always keep a console (cloud serial console, iDRAC/iLO, hypervisor console) available when changing SSH.

:::warn
Never "fix" this one with `setenforce 0` to get back in. If you are locked out, use the console, run `semanage port -a -t ssh_port_t -p tcp 2222`, and restart sshd. Leaving SELinux permissive because SSH broke once is the classic way SELinux ends up disabled fleet-wide.
:::

### Scenario 8: Custom app binary fails with 203/EXEC under systemd

**Symptom.** A Go or Rust service binary deployed to `/home/deploy/myapp/bin/myapp`, or copied to `/home/deploy` and then `mv`'d to `/opt/myapp/bin`, fails as a service. It runs fine when you start it by hand.

```text
systemd[1]: Started myapp.service - My App.
(myapp)[5120]: myapp.service: Failed to locate executable /opt/myapp/bin/myapp: Permission denied
(myapp)[5120]: myapp.service: Failed at step EXEC spawning /opt/myapp/bin/myapp: Permission denied
systemd[1]: myapp.service: Main process exited, code=exited, status=203/EXEC
```

**Evidence.**

```text
type=AVC msg=audit(1764067500.712:1502): avc:  denied  { execute } for  pid=5120 comm="(myapp)" name="myapp" dev="dm-0" ino=2097441 scontext=system_u:system_r:init_t:s0 tcontext=unconfined_u:object_r:user_home_t:s0 tclass=file permissive=0
```

**Diagnosis.** systemd (`init_t`) is not allowed to execute files labelled `user_home_t` or `user_tmp_t`, which is exactly what a binary built in a home directory or `/tmp` and then `mv`'d keeps. Running it by hand worked because your shell is `unconfined_t`.

`203/EXEC` has non-SELinux causes too, so check those in the same pass:

| Check | Command | Non-SELinux cause |
|---|---|---|
| Exec bit | `ls -l /opt/myapp/bin/myapp` | Missing `x` |
| Mount options | `findmnt -T /opt/myapp/bin -o TARGET,OPTIONS` | `noexec` on `/home`, `/tmp` or the data volume |
| Script header | `head -1 script.sh` | Missing or wrong `#!` line, CRLF line endings |
| Architecture | `file /opt/myapp/bin/myapp` | arm64 binary on x86_64 |
| Sandbox | `systemctl cat myapp` | `ProtectHome=yes` hides `/home` |

**Fix.** Put service binaries where policy expects executables and give them `bin_t`:

```bash
semanage fcontext -a -t bin_t "/opt/myapp/bin(/.*)?"
restorecon -Rv /opt/myapp
systemctl restart myapp
```

A binary labelled `bin_t` started by systemd runs in `unconfined_service_t`: it is not confined, but it is no longer blocked. If the app must be confined, see scenario 20.

**Verify.**

```bash
systemctl is-active myapp
ps -eZ | grep myapp
```

```text
active
system_u:system_r:unconfined_service_t:s0 5188 ? 00:00:00 myapp
```

**Prevention.** Never run services from `/home`. Install to `/opt/<app>` or `/usr/local/bin` with your package or deployment tool, and finish every deployment with `restorecon -R /opt/<app>`. Building an RPM is even better: RPM sets file contexts at install time automatically.

### Scenario 9: Tomcat cannot write logs to a custom directory

**Symptom.** Tomcat from the RHEL `tomcat` package is configured to log to `/data/logs/tomcat` (a separate volume). Catalina fails to start or the app logs:

```text
java.io.FileNotFoundException: /data/logs/tomcat/catalina.2026-11-25.log (Permission denied)
```

**Evidence.**

```text
type=AVC msg=audit(1764067900.441:1560): avc:  denied  { add_name } for  pid=6014 comm="java" name="catalina.2026-11-25.log" scontext=system_u:system_r:tomcat_t:s0 tcontext=unconfined_u:object_r:default_t:s0 tclass=dir permissive=0
```

**Diagnosis.** The packaged Tomcat runs in its own confined domain, `tomcat_t`, which can write `tomcat_log_t` (the type of `/var/log/tomcat`), not `default_t`. First confirm the domain, because it changes everything:

```bash
ps -eZ | grep java
```

If the Java process shows `unconfined_service_t` (a standalone Spring Boot jar started from `/opt`), SELinux is not the cause: check the `User=` of the unit and the directory's owner and mode instead.

**Fix.** When a new directory replaces a standard one, the cleanest rule is a **file-context equivalence**: "label `/data/logs/tomcat` exactly as if it were `/var/log/tomcat`".

```bash
semanage fcontext -a -e /var/log/tomcat /data/logs/tomcat
restorecon -Rv /data/logs/tomcat
ls -dZ /data/logs/tomcat
```

```text
system_u:object_r:tomcat_log_t:s0 /data/logs/tomcat
```

Equivalence rules follow policy updates automatically: if a future policy changes the type for `/var/log/tomcat`, your custom path follows.

**Verify.** `systemctl restart tomcat`, then `ls -Z /data/logs/tomcat` shows new log files with `tomcat_log_t`.

**Prevention.** Rather than moving the log directory, mount the volume **at** `/var/log/tomcat`, which already has a rule. If you must use a new path, use `-e` rather than guessing the type.

### Scenario 10: Container volume mount gives Permission denied (podman/docker)

**Symptom.** A container runs, but its process cannot read or write the bind-mounted directory:

```bash
podman run --rm -v /srv/appdata:/data registry.access.redhat.com/ubi9/ubi ls /data
```

```text
ls: cannot open directory '/data': Permission denied
```

Even `--user root` inside the container does not help, and the host directory is `777` in desperation.

**Evidence.**

```text
type=AVC msg=audit(1764068300.120:1611): avc:  denied  { read } for  pid=7012 comm="ls" name="appdata" dev="dm-0" ino=1574011 scontext=system_u:system_r:container_t:s0:c214,c731 tcontext=unconfined_u:object_r:var_t:s0 tclass=dir permissive=0
```

**Diagnosis.** Container processes run as `container_t` with a unique pair of **MCS categories** (`s0:c214,c731`). They may only use files labelled `container_file_t`, and for private content, only with matching categories. This isolates containers from the host and from each other.

**Fix.** Use the volume label options, understanding the difference:

| Option | What podman/docker does to the host dir | Use for |
|---|---|---|
| `:z` (lowercase) | Relabels to `container_file_t:s0` (no categories): **shared** | Data shared between several containers |
| `:Z` (uppercase) | Relabels to `container_file_t:s0:cX,cY`: **private** to this container | Data only one container uses |
| none | Leaves label alone | Paths you have labelled yourself |

```bash
podman run --rm -v /srv/appdata:/data:Z registry.access.redhat.com/ubi9/ubi ls /data
```

For a persistent, explicit label (for example, data that several Compose services or Quadlet units share):

```bash
semanage fcontext -a -t container_file_t "/srv/appdata(/.*)?"
restorecon -Rv /srv/appdata
```

Docker on RHEL-family hosts honours the same `:z`/`:Z` flags when SELinux support is enabled in the daemon (`"selinux-enabled": true` in `/etc/docker/daemon.json`).

**Verify.** `ls -dZ /srv/appdata` shows `container_file_t`; the container lists and writes files.

**Prevention.** Never use `:Z` or `:z` on system or shared host paths such as `/home`, `/etc`, `/var`, `/usr` or a user's home: podman will recursively relabel them, and the host services that rely on those labels will break (users unable to log in is a common outcome). Use dedicated data directories. For NFS-backed volumes, the boolean `virt_use_nfs` controls container access. `--security-opt label=disable` turns off separation for that container: use it only as a diagnostic.

:::tip
With **rootless** podman you can hit a second, non-SELinux "Permission denied" on volumes: the container's root maps to your UID, and other container UIDs map into your subordinate range, so files owned by your host UID look owned by root inside. If there is no AVC, look at UID mapping (`podman unshare ls -ln /srv/appdata`, the `:U` volume option, or `podman unshare chown`) instead.
:::

### Scenario 11: Home directories or web content on NFS

**Symptom A (NFS home directories).** Users whose home is NFS-mounted cannot log in with SSH keys; SSH falls back to a password prompt. Users who do log in see:

```text
Could not chdir to home directory /home/alice: Permission denied
```

**Symptom B (web content on NFS).** Apache's DocumentRoot on an NFS share returns 403 for every page.

**Evidence.**

```text
type=AVC msg=audit(1764068702.330:1660): avc:  denied  { read } for  pid=7301 comm="sshd" name="authorized_keys" dev="0:52" ino=40111 scontext=system_u:system_r:sshd_t:s0-s0:c0.c1023 tcontext=system_u:object_r:nfs_t:s0 tclass=file permissive=0
```

```text
type=AVC msg=audit(1764068790.802:1671): avc:  denied  { getattr } for  pid=7330 comm="httpd" path="/mnt/web/index.html" dev="0:53" scontext=system_u:system_r:httpd_t:s0 tcontext=system_u:object_r:nfs_t:s0 tclass=file permissive=0
```

**Diagnosis.** Most NFS mounts do not carry per-file SELinux labels, so every file appears as `nfs_t`. `restorecon` cannot change that (labels are not stored on the server). Policy handles NFS with booleans or with a mount-time context.

**Fix.**

```bash
setsebool -P use_nfs_home_dirs 1     # login/sshd/etc. may use home dirs on NFS
setsebool -P httpd_use_nfs 1         # httpd may serve content from NFS
```

Alternative for web content, when you want a specific type instead of a boolean: mount with a fixed context. In `/etc/fstab`:

```text
nfs01.example.com:/export/web  /mnt/web  nfs  defaults,context="system_u:object_r:httpd_sys_content_t:s0"  0 0
```

The `context=` option labels everything on that mount with one type. It is all-or-nothing per mount; you cannot have rw and ro subdirectories with different types.

CIFS equivalents: `use_samba_home_dirs`, `httpd_use_cifs`.

**Verify.** Re-login with a key; `curl` the NFS-hosted page; `getsebool -a | grep nfs`.

**Prevention.** Bake NFS-related booleans into the base role for any host class that mounts NFS homes or content.

### Scenario 12: Samba share denied

**Symptom.** A new share on `/srv/share` is configured in `smb.conf`, `testparm` is clean, the user exists in `pdbedit -L`, but:

```bash
smbclient //files01/share -U alice -c 'ls'
```

```text
NT_STATUS_ACCESS_DENIED listing \*
```

Windows clients see "You do not have permission to access \\files01\share".

**Evidence.**

```text
type=AVC msg=audit(1764069100.551:1702): avc:  denied  { read } for  pid=8120 comm="smbd" name="share" dev="dm-0" ino=1180122 scontext=system_u:system_r:smbd_t:s0 tcontext=unconfined_u:object_r:var_t:s0 tclass=dir permissive=0
```

**Diagnosis.** `smbd_t` may share files labelled `samba_share_t`. A directory under `/srv` is `var_t`.

**Fix.** For a dedicated share directory, label it:

```bash
semanage fcontext -a -t samba_share_t "/srv/share(/.*)?"
restorecon -Rv /srv/share
```

Use booleans only when the share genuinely must expose existing system paths that you cannot relabel without breaking other services:

| Boolean | Effect | Risk |
|---|---|---|
| `samba_enable_home_dirs` | Share users' home directories | Moderate; normal for `[homes]` |
| `samba_export_all_ro` | smbd may read any file on the system | High: overrides labelling for reads |
| `samba_export_all_rw` | smbd may read and write any file | Very high: last resort only |

If the same directory must be served by both Samba and Apache (or FTP), use the shared types `public_content_t` (read-only) or `public_content_rw_t` (writable; also enable `smbd_anon_write` for Samba writes), not two conflicting labels.

**Verify.** `smbclient ... -c 'ls'` lists files; write a test file with `-c 'put /etc/hostname test.txt'`.

**Prevention.** Create share directories with the label in the same task. Avoid `samba_export_all_*`: an auditor will flag it, and rightly so.

### Scenario 13: A script works by hand but fails under cron, logrotate or a service

**Symptom.** Application logs live on `/data/logs/myapp`. A custom `/etc/logrotate.d/myapp` was tested with `logrotate -f /etc/logrotate.d/myapp` as root and worked. Next morning the logs are not rotated and the disk is filling. `journalctl -u logrotate.service`:

```text
logrotate[9012]: error: error opening /data/logs/myapp/app.log: Permission denied
systemd[1]: logrotate.service: Main process exited, code=exited, status=1/FAILURE
```

**Evidence.**

```text
type=AVC msg=audit(1764115201.004:2001): avc:  denied  { read } for  pid=9012 comm="logrotate" name="myapp" dev="dm-2" ino=131 scontext=system_u:system_r:logrotate_t:s0-s0:c0.c1023 tcontext=unconfined_u:object_r:default_t:s0 tclass=dir permissive=0
```

**Diagnosis.** When you ran it by hand you were `unconfined_t`; when the timer runs it, logrotate transitions into the confined `logrotate_t` domain, which manages files labelled as logs (`var_log_t` and the service log types), not `default_t`. The same "different domain, different result" pattern applies to:

| Run by | Typical domain | Compare with your shell |
|---|---|---|
| You, logged in as root | `unconfined_t` | `id -Z` |
| A unit running a `bin_t` script | `unconfined_service_t` | `ps -eZ` |
| logrotate timer | `logrotate_t` (confined) | |
| A script exec'd from PHP/CGI | `httpd_t` / `httpd_sys_script_t` (confined) | |
| A script exec'd by a confined daemon (e.g. a DB or mail hook) | that daemon's domain or a helper domain | |

```bash
id -Z                       # unconfined_u:unconfined_r:unconfined_t:s0-s0:c0.c1023
```

**Fix.** Label the log directory as logs:

```bash
semanage fcontext -a -t var_log_t "/data/logs(/.*)?"
restorecon -Rv /data/logs
```

**Verify.** Reproduce in the real context, not your shell: `systemctl start logrotate.service`, then `systemctl status logrotate.service` and `ausearch -m AVC -ts recent -c logrotate`.

**Prevention.** Test scheduled jobs by triggering the real unit or timer (`systemctl start <unit>`), or run the script through `systemd-run --wait --pipe /path/script` to get a service context. Do not trust a manual root test for anything a confined daemon will execute.

### Scenario 14: Database datadir moved to /data

**Symptom.** The DBA moves MariaDB from `/var/lib/mysql` to a fast volume at `/data/mysql`, updates `datadir=` in `/etc/my.cnf.d/mariadb-server.cnf`, copies the data with `rsync -a`, and fixes ownership. `systemctl start mariadb` fails:

```text
mariadbd: Can't create/write to file '/data/mysql/aria_log_control' (Errcode: 13 "Permission denied")
[ERROR] InnoDB: Operating system error number 13 in a file operation.
[ERROR] InnoDB: The error means mysqld does not have the access rights to the directory.
```

PostgreSQL moved to `/data/pgsql`:

```text
postgres[9410]: FATAL:  could not open file "global/pg_filenode.map": Permission denied
```

**Evidence.**

```text
type=AVC msg=audit(1764116000.205:2050): avc:  denied  { write } for  pid=9302 comm="mariadbd" name="mysql" dev="dm-3" ino=128 scontext=system_u:system_r:mysqld_t:s0 tcontext=unconfined_u:object_r:default_t:s0 tclass=dir permissive=0
```

**Diagnosis.** `mysqld_t` manages `mysqld_db_t`; `postgresql_t` manages `postgresql_db_t`. The new path has no rule, so it is `default_t`.

**Fix.** Use an equivalence rule so the new tree is labelled exactly like the original, including special subpaths (sockets, logs) the policy distinguishes:

```bash
semanage fcontext -a -e /var/lib/mysql /data/mysql
restorecon -Rv /data/mysql
systemctl start mariadb
```

For PostgreSQL:

```bash
semanage fcontext -a -e /var/lib/pgsql /data/pgsql
restorecon -Rv /data/pgsql
```

The explicit type equivalent (`semanage fcontext -a -t mysqld_db_t "/data/mysql(/.*)?"`) also works; `-e` is preferred because it tracks the full set of rules under the original path. If the DB also moves to a non-standard port, type the port: `semanage port -a -t mysqld_port_t -p tcp 3307` or `-t postgresql_port_t -p tcp 5433`.

Non-SELinux traps in the same change: the MariaDB unit has `ProtectHome=true`, so a datadir under `/home` fails with no AVC; for PostgreSQL, set the new path with a drop-in (`systemctl edit postgresql` -> `Environment=PGDATA=/data/pgsql/data`) rather than editing the vendor unit.

**Verify.** `ls -dZ /data/mysql` shows `mysqld_db_t`; `systemctl is-active mariadb`; `mysql -e 'select @@datadir'`.

**Prevention.** Mount the new volume **at** `/var/lib/mysql` instead of moving the path, when you can. Then no SELinux change is needed at all.

:::lab Lab 07-A — Move a MariaDB datadir and fix it with an equivalence rule
**Goal:** reproduce scenario 14 end to end on a lab VM.

**Break it.**

```bash
systemctl enable --now mariadb && systemctl stop mariadb
mkdir -p /data && rsync -a /var/lib/mysql/ /data/mysql/
sed -i 's#^datadir=.*#datadir=/data/mysql#; s#^socket=.*#socket=/data/mysql/mysql.sock#' /etc/my.cnf.d/mariadb-server.cnf
systemctl start mariadb      # fails
```

**Symptoms:** `systemctl status mariadb` shows failed; `journalctl -u mariadb -n 30` shows errno 13.

**Diagnose:** `ausearch -m AVC -ts recent -c mariadbd`, then `ls -dZ /data/mysql`, then `ausearch -m AVC -ts recent --raw | audit2why`.

**Fix:** `semanage fcontext -a -e /var/lib/mysql /data/mysql; restorecon -Rv /data/mysql; systemctl start mariadb`.

**Verify:** `mysql -S /data/mysql/mysql.sock -e 'select @@datadir'` returns `/data/mysql/`.

**Clean-up:** stop mariadb, restore the original `datadir`/`socket` lines, `semanage fcontext -d /data/mysql`, `rm -rf /data/mysql`.
:::

### Scenario 15: Application behaviour issue: temp files in the web root, execmem, execstack

Some AVCs are not "SELinux being strict"; they are SELinux pointing at something the application should not be doing. Fix the application first.

**Symptom A (writes into the code tree).** A PHP app writes temporary PDF exports into `/var/www/html/app/tmp/` and fails with `Permission denied`. The AVC is `{ write }` on `httpd_sys_content_t`, exactly like scenario 5.

**Diagnosis A.** You could label `tmp/` as `httpd_sys_rw_content_t`, but ask why the app writes inside the web root at all: anything written there may be downloadable by URL, and a writable directory inside a PHP tree is a classic place to drop a web shell. The correct fix is to change the app's config to use the system temp dir (`sys_get_temp_dir()`, which gives `/tmp`; httpd's files there get `httpd_tmp_t` automatically, and `PrivateTmp=yes` in the unit isolates them) or a data directory outside the document root such as `/var/lib/myapp/exports` labelled `httpd_sys_rw_content_t`.

**Symptom B (execmem).** PHP regex-heavy pages fail or log:

```text
PHP Warning:  preg_match(): JIT compilation failed: no more memory in /var/www/html/app/lib/parse.php on line 88
```

**Evidence B.**

```text
type=AVC msg=audit(1764116602.770:2111): avc:  denied  { execmem } for  pid=3015 comm="php-fpm" scontext=system_u:system_r:httpd_t:s0 tcontext=system_u:system_r:httpd_t:s0 tclass=process permissive=0
```

**Diagnosis B.** `execmem` is a request to create memory that is both writable and executable, which JIT compilers need and which memory-corruption exploits love. The PCRE JIT inside PHP triggers it. `audit2why` will suggest `httpd_execmem`.

**Fix B, in order of preference.**

1. Change the application config: `pcre.jit=0` in `/etc/php.ini` (or a file in `/etc/php.d/`), restart `php-fpm`. PHP falls back to the interpreter; for most sites the performance difference is not measurable.
2. If a component truly requires JIT (some language runtimes embedded in Apache), enable `setsebool -P httpd_execmem 1` **and record why**. This boolean weakens protection for every process in `httpd_t`.

**Symptom C (execstack).** A service loading an old vendor `.so` fails with:

```text
error while loading shared libraries: libvendor.so: cannot enable executable stack as shared object requires: Permission denied
```

**Diagnosis C.** The library was linked with an executable-stack flag it almost never needs. Ask the vendor for a build linked with `-z noexecstack`; if you build it yourself, add that linker flag. Tools to clear the flag on an existing binary vary by distribution and version (the old `execstack` utility is not shipped in RHEL 9; recent `patchelf` releases provide `--clear-execstack`), so verify availability before relying on them. Only allow it in policy (a boolean or module granting `execstack`) as a documented exception.

**Prevention.** Add "SELinux denials in staging are bugs" to the definition of done. A developer who sees `execmem`, writes into the code tree, or reads `/etc/shadow` should fix the code, not file a ticket for a policy exception.

### Scenario 16: Python/Node app reading a TLS key from a custom path

**Symptom.** TLS certificates for `app.example.com` are deployed to `/opt/myapp/certs/`. nginx (terminating TLS for the Node app) fails to start:

```text
nginx: [emerg] cannot load certificate key "/opt/myapp/certs/app.key": BIO_new_file() failed (SSL: error:8000000D:system library::Permission denied:calling fopen(/opt/myapp/certs/app.key, r) error:10080002:BIO routines::system lib)
```

A Python app served through Apache `mod_wsgi` (which runs inside `httpd_t`) logs:

```text
PermissionError: [Errno 13] Permission denied: '/opt/myapp/certs/app.key'
```

**Evidence.**

```text
type=AVC msg=audit(1764117001.090:2160): avc:  denied  { read } for  pid=9801 comm="nginx" name="app.key" dev="dm-0" ino=2099110 scontext=system_u:system_r:httpd_t:s0 tcontext=unconfined_u:object_r:usr_t:s0 tclass=file permissive=0
```

**Diagnosis.** Confined services that do TLS (`httpd_t`, `postfix_*`, `dovecot_t`, `slapd_t` and others) can read `cert_t`, the label on `/etc/pki/tls/certs`, `/etc/pki/tls/private` and similar. Files under `/opt` are `usr_t`.

Check the process domain first. If the Python or Node process runs **standalone** under systemd from `/opt` (`ps -eZ` shows `unconfined_service_t`), SELinux is not restricting it at all, and an errno 13 means DAC: the key is usually `0600 root:root` while the unit runs `User=myapp`. Fix with group ownership (`chgrp myapp app.key; chmod 0640 app.key`) or systemd's `LoadCredential=`.

**Fix (confined reader).** Either move the files to the standard location, which already has the right label:

```bash
install -m 0640 -o root -g nginx app.key /etc/pki/tls/private/app.example.com.key
install -m 0644 app.crt /etc/pki/tls/certs/app.example.com.crt
```

or label the custom directory as certificates:

```bash
semanage fcontext -a -t cert_t "/opt/myapp/certs(/.*)?"
restorecon -Rv /opt/myapp/certs
```

**Verify.** `nginx -t` prints `syntax is ok` and `test is successful`; `openssl s_client -connect app.example.com:443 -servername app.example.com </dev/null | head` shows the certificate chain.

**Prevention.** Certificate automation (certbot, an ACME client, or your secrets agent) should write to `/etc/pki/tls/...` or a path with a `cert_t` rule. When renewals copy files from a staging directory with `mv`, run `restorecon` on the target afterwards (renewal is the classic time for this to break, months after go-live).

### Scenario 17: Web app cannot send mail

**Symptom.** Password-reset emails stop arriving. PHP `mail()` returns `false` and nothing appears in `/var/log/maillog`. An app using PHPMailer over SMTP logs:

```text
SMTP ERROR: Failed to connect to server: Permission denied (13)
SMTP Error: Could not connect to SMTP host.
```

**Evidence.**

```text
type=AVC msg=audit(1764117400.334:2201): avc:  denied  { name_connect } for  pid=3015 comm="php-fpm" dest=587 scontext=system_u:system_r:httpd_t:s0 tcontext=system_u:object_r:smtp_port_t:s0 tclass=tcp_socket permissive=0
```

For `mail()` (which executes the local `sendmail` binary) the denial is on executing or transitioning to the mail binary instead.

**Diagnosis.** Sending mail is an optional capability of the web server domain. `audit2why` names the boolean.

**Fix.**

```bash
setsebool -P httpd_can_sendmail 1
```

This covers both executing the local MTA and connecting to SMTP ports (25, 465, 587). If the relay listens on an unusual port, either type it as `smtp_port_t` on this host or use the local Postfix as a relay (preferred: the app talks to localhost:25, Postfix handles TLS and retries).

**Verify.** Trigger a password reset; `tail /var/log/maillog` shows the message queued and relayed.

**Prevention.** Include `httpd_can_sendmail` in the web role only for hosts that need it. Leaving it off elsewhere means a compromised app cannot become a spam relay.

### Scenario 18: Labels wrong after a backup restore or rsync

**Symptom.** After a disaster-recovery rebuild, the web server is restored from backup to a fresh RHEL 9 VM. The site returns 403; uploads fail; a nightly job cannot read its config. Everything matches the old server byte-for-byte.

**Evidence.**

```text
type=AVC msg=audit(1764117801.551:2250): avc:  denied  { read } for  pid=1402 comm="httpd" name="index.html" dev="dm-0" ino=655361 scontext=system_u:system_r:httpd_t:s0 tcontext=system_u:object_r:default_t:s0 tclass=file permissive=0
```

**Diagnosis.** File contexts live in two places: the **xattr on each file**, and the **rules in the policy store** (`semanage fcontext`). A restore can get either wrong:

| How data came back | Result |
|---|---|
| `tar` / `rsync -a` without xattrs into the final path | New files get the label policy assigns to that path. If the new host lacks your custom rules (`/data/www`), that is `default_t` |
| Restored to `/tmp/restore` or a home dir, then `mv`'d into place | Files keep `user_tmp_t` / `user_home_t` (scenario 2) |
| `rsync -aX` / `tar --selinux` from a host whose labels were wrong or set by `chcon` | Wrong labels copied faithfully |
| Backup tool restores as a different domain to a staging path | Labels of the staging path |

The real root cause in DR is almost always the second store: **the rebuilt host never received the `semanage` customisations**.

**Fix.** Restore the customisations, then relabel:

```bash
semanage export > /root/selinux-local.txt      # on the OLD/reference host (or from your backup)
semanage import < /root/selinux-local.txt      # on the NEW host
restorecon -Rv /data/www /var/www /srv
```

`semanage export` includes local fcontext rules, port types, booleans, permissive domains and login mappings, which is precisely what a rebuild loses. Custom policy modules (`.pp` files) are separate; keep their source and reinstall them with `semodule -i`.

**Verify.** `restorecon -Rvn /` (dry run over the whole system) produces no output in the restored paths; the site serves.

**Prevention.** Include `semanage export` output in your configuration backups (it is plain text and diffs well in Git), or better, encode every customisation in Ansible so a rebuild is identical. Add `restorecon -R` on restored paths to the restore runbook.

### Scenario 19: After a root password reset, or after disabling and re-enabling SELinux

**Symptom A (password reset).** Someone resets the root password through the `rd.break` emergency shell, reboots, and now **nobody can log in**, even with the correct new password. Console shows authentication failure; the audit log shows denials on `/etc/shadow`.

**Evidence A.**

```text
type=AVC msg=audit(1764118200.101:88): avc:  denied  { read } for  pid=1033 comm="unix_chkpwd" name="shadow" dev="dm-0" ino=8390012 scontext=system_u:system_r:chkpwd_t:s0-s0:c0.c1023 tcontext=system_u:object_r:unlabeled_t:s0 tclass=file permissive=0
```

**Diagnosis A.** In the `rd.break` shell the SELinux policy is not loaded, so `passwd` writes a **new** `/etc/shadow` file without a label. On boot it is `unlabeled_t`, and the password-checking domains cannot read it. The standard procedure has an extra step precisely for this.

**Fix A.** The correct reset procedure on RHEL 9:

```bash
# At the GRUB menu press e, append  rd.break  to the linux line, Ctrl+x
mount -o remount,rw /sysroot
chroot /sysroot
passwd root
touch /.autorelabel        # relabel the whole FS on next boot (slow on large systems)
exit
exit
```

A faster alternative to the full relabel, inside the chroot after `passwd`: `load_policy -i` then `restorecon -v /etc/shadow`. If you are already stuck in the broken state, boot with `enforcing=0` added to the kernel line once (permissive for that boot only), log in, run `restorecon -v /etc/shadow`, and reboot normally.

**Symptom B (disabled, then re-enabled).** SELinux was disabled for months "temporarily". A compliance scan forces it back on. After setting `SELINUX=enforcing` and rebooting, services fail all over the place, or the system does not boot.

**Diagnosis B.** While disabled, nothing labels new files, so everything created in that period (packages installed, logs, users) has no context (`unlabeled_t`).

**Fix B.** Re-enable in two stages:

```bash
sed -i 's/^SELINUX=.*/SELINUX=permissive/' /etc/selinux/config
grubby --update-kernel ALL --remove-args selinux    # if selinux=0 was set on the kernel line
fixfiles -F onboot                                  # or: touch /.autorelabel
reboot
# after the relabel boot:
ausearch -m AVC -ts boot | audit2why                # review remaining denials while permissive
sed -i 's/^SELINUX=.*/SELINUX=enforcing/' /etc/selinux/config
setenforce 1
```

On RHEL 9, `SELINUX=disabled` in `/etc/selinux/config` no longer fully disables SELinux at boot; disabling requires the `selinux=0` kernel argument. So check the kernel command line (`cat /proc/cmdline`) as well as the config file.

**Verify.** `getenforce` prints `Enforcing`; `ausearch -m AVC -ts boot` is quiet; `restorecon -Rvn /etc /var` is clean.

**Prevention.** Never disable SELinux; use permissive domains for diagnosis. If disabling was unavoidable, plan the relabel as a maintenance window: a full relabel on a multi-terabyte filesystem can take a long time.

### Scenario 20: A truly custom application that needs a policy module

**Symptom.** An in-house monitoring agent, `myagentd`, has been deployed. Security requires it to be **confined**, not `unconfined_service_t`, because it listens on the network. After a first attempt at confinement, it fails with several denials: reading `/proc` entries, binding TCP 9555, writing its spool under `/var/lib/myagent`.

**Evidence.**

```text
type=AVC msg=audit(1764118800.010:3001): avc:  denied  { name_bind } for  pid=10211 comm="myagentd" src=9555 scontext=system_u:system_r:myagent_t:s0 tcontext=system_u:object_r:unreserved_port_t:s0 tclass=tcp_socket permissive=1
type=AVC msg=audit(1764118800.034:3002): avc:  denied  { write } for  pid=10211 comm="myagentd" name="spool" dev="dm-0" ino=917511 scontext=system_u:system_r:myagent_t:s0 tcontext=system_u:object_r:var_lib_t:s0 tclass=dir permissive=1
```

**Diagnosis.** No existing type, boolean or port type fits: the application is new to the policy. This is the one legitimate case for writing policy. There are two levels.

**Option 1: a small local module from denials (`audit2allow -M`).** Suitable when an **existing** confined service needs one or two extra, reviewed permissions that no boolean covers.

```bash
ausearch -m AVC -ts today -c myagentd --raw | audit2allow -M myagent_local
cat myagent_local.te
```

```text
module myagent_local 1.0;

require {
	type myagent_t;
	type var_lib_t;
	class dir { add_name write };
}

#============= myagent_t ==============
allow myagent_t var_lib_t:dir { add_name write };
```

**Review the `.te` before installing.** This rule lets the agent write into **every** `var_lib_t` directory, not just its own. The right answer is a dedicated type for `/var/lib/myagent`, which `audit2allow` cannot invent. Red flags in a generated `.te`: rules on `default_t`, `var_t`, `etc_t`, `shadow_t`, `user_home_t`, `unlabeled_t`, `file_type` attributes, `execmem`, or capabilities like `sys_admin`/`dac_override`. Any of those means "fix a label or the app", not "allow it".

When the rules are acceptable:

```bash
semodule -i myagent_local.pp
semodule -l | grep myagent_local
```

To remove it later: `semodule -r myagent_local`. Use `audit2allow -R` to generate rules using reference-policy interfaces (more readable and maintainable) when `selinux-policy-devel` is installed.

**Option 2: a real policy for the app (`sepolicy generate`).** For a new daemon, generate a proper domain with its own types:

```bash
dnf -y install policycoreutils-devel selinux-policy-devel
mkdir ~/myagent-policy && cd ~/myagent-policy
sepolicy generate --init -n myagent /opt/myagent/bin/myagentd
ls
```

```text
myagent.fc  myagent.if  myagent.sh  myagent.te  myagent_selinux.spec
```

| File | Contains |
|---|---|
| `myagent.te` | Type enforcement: `myagent_t`, `myagent_exec_t`, rules; starts with `permissive myagent_t;` |
| `myagent.fc` | File contexts: binary -> `myagent_exec_t`, plus paths you add (e.g. `/var/lib/myagent(/.*)?` -> `myagent_var_lib_t`) |
| `myagent.if` | Interfaces other modules can call |
| `myagent.sh` | Builds the module, installs it, runs `restorecon`, and can build an RPM |
| `myagent_selinux.spec` | RPM spec for shipping the policy with the app |

The workflow:

1. Edit `myagent.te` and `myagent.fc` to declare types for the app's own data (`myagent_var_lib_t`), logs and port; add a port type with `semanage port -a -t myagent_port_t -p tcp 9555` after declaring `myagent_port_t` in the `.te`.
2. Run `./myagent.sh` to build and install; restart the service; confirm `ps -eZ` shows `myagent_t`.
3. The domain is **permissive** (only this domain), so the agent works while denials are logged. Exercise every feature.
4. `ausearch -m AVC -ts today -c myagentd --raw | audit2allow -R` to get interface-based suggestions; add the reasonable ones to the `.te`; rebuild.
5. Repeat until the log is quiet under real load; then remove `permissive myagent_t;`, rebuild, and run enforcing.
6. Ship the `.te/.fc/.if` in Git and the policy RPM with the app.

**Verify.** `semodule -l | grep myagent`; `ps -eZ | grep myagentd` shows `myagent_t`; `semanage permissive -l` does not list it once you finish; `ausearch -m AVC -c myagentd -ts today` is empty under load.

**Prevention.** Treat policy as code: version it, review it like any other change, and test it in staging. A module named after the app (`myagent`) must not clash with a module the distribution ships; check `semodule -l` first.

:::warn
`audit2allow -a -M fix && semodule -i fix.pp` ("allow everything that was ever denied") is how servers end up with policy that lets httpd read home directories or write `/etc`. It turns every past misconfiguration, and every attacker's probe that happened to be logged, into a permanent rule. Always filter by process and time, always read the `.te`, and always prefer a label, boolean or port over a module.
:::

:::lab Lab 07-B — Build a confined domain with sepolicy generate
**Goal:** take a small custom service from `unconfined_service_t` to its own enforcing domain.

**Break it (create the "custom app").**

```bash
mkdir -p /opt/myagent/bin /var/lib/myagent
cat > /opt/myagent/bin/myagentd <<'EOF'
#!/bin/bash
while true; do date >> /var/lib/myagent/heartbeat; sleep 5; done
EOF
chmod 755 /opt/myagent/bin/myagentd
cat > /etc/systemd/system/myagent.service <<'EOF'
[Unit]
Description=Lab agent
[Service]
ExecStart=/opt/myagent/bin/myagentd
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && systemctl start myagent && ps -eZ | grep myagentd
```

**Symptoms:** the agent runs, but as `unconfined_service_t`, which fails the security requirement.

**Diagnose / Fix:** `cd /root && sepolicy generate --init -n myagent /opt/myagent/bin/myagentd`, add `/var/lib/myagent(/.*)?  gen_context(system_u:object_r:myagent_var_lib_t,s0)` to `myagent.fc` if the generator did not, run `./myagent.sh`, `restorecon -Rv /opt/myagent /var/lib/myagent`, restart the service. Collect denials with `ausearch -m AVC -ts recent -c myagentd --raw | audit2allow -R`, add them, rebuild, then remove the `permissive` line and rebuild.

**Verify:** `ps -eZ | grep myagentd` shows `myagent_t`; heartbeat file keeps growing; no AVCs while enforcing.

**Clean-up:** `systemctl disable --now myagent`, `semodule -r myagent`, `rm -rf /opt/myagent /var/lib/myagent /etc/systemd/system/myagent.service`, `systemctl daemon-reload`.
:::

:::lab Lab 07-C — Find the hidden denial (dontaudit) and the wrong boolean
**Goal:** practise the full runbook on scenario 3 and learn not to pick the first boolean `audit2why` prints.

**Break it:** reproduce scenario 3 (nginx proxying to a Python server on port 5000).

**Diagnose:** `ausearch -m AVC -ts recent --raw | audit2why`. Note that both `httpd_can_network_connect` and `nis_enabled` are offered. Run `semanage boolean -l | grep -E 'httpd_can_network_connect |nis_enabled'` and read both descriptions. Then try `semodule -DB`, reload the page, and see how many extra (normally silenced) denials appear; run `semodule -B` afterwards.

**Fix:** the narrowest option that works: retype port 5000 (`semanage port -m -t http_port_t -p tcp 5000`) or `setsebool -P httpd_can_network_connect 1`.

**Verify:** 200 through nginx, no AVCs.

**Clean-up:** `semanage port -d -t http_port_t -p tcp 5000` (or `setsebool -P httpd_can_network_connect 0`), remove the nginx config.
:::

## 4. Anti-patterns that fail audits and cause the next outage

| Anti-pattern | Why people do it | Why it is wrong | Do this instead |
|---|---|---|---|
| `setenforce 0` "for now", or `SELINUX=disabled` | It makes the error go away | Removes a whole layer of protection from every service, and is a finding in CIS, STIG and PCI-DSS reviews; re-enabling later requires a relabel (scenario 19) | Use `semanage permissive -a <domain>` for diagnosis, then apply the real fix and remove it |
| `chcon -t ...` in production | It is one command and works immediately | Not persistent: the next `restorecon`, relabel, or policy update silently reverts it (the scenario 5 incident) | `semanage fcontext -a` + `restorecon` |
| `chmod -R 777` | "Permission denied" sounds like permissions | Does nothing against an AVC, and opens the files to every local user | Read the AVC; fix the label; keep DAC tight |
| `audit2allow -a -M fix` installed without reading | It is in many blog posts | Converts wrong labels into permanent, broad allow rules (scenario 20) | Filter by `-c` and time, read the `.te`, prefer label/boolean/port |
| Turning on every boolean `audit2why` lists | Wants a guaranteed fix | Booleans like `nis_enabled`, `httpd_unified` or `samba_export_all_rw` are very broad | Pick the narrowest boolean matching the intent |
| `semanage port -m` on a port another confined service uses | Quick way past "already defined" | Breaks the other service's ability to bind or connect | Check `semanage port -l` and the host's services first |
| `:Z` on `/home`, `/etc` or `/var` volume mounts | Container "just needs access" | Relabels host system paths and breaks the host | Dedicated data dirs labelled `container_file_t` |
| Testing as root in a shell and declaring it fixed | Fast feedback | Your shell is unconfined; the service is not | Test through the real service, URL or timer |

:::prod
**Incident: "SELinux was off on half the fleet".** A compliance scan found 40% of web servers in permissive mode. Investigation showed a single runbook page from years earlier: "If the site 403s after deploy, run `setenforce 0`". Engineers had also added `SELINUX=permissive` to the image to stop pages. Root cause: deploys used `mv` from a build directory (scenario 2). Fix: deploy script switched to `rsync` plus `restorecon -R /var/www/html`, fleet moved back to enforcing in waves with a permissive-domain canary first. Prevention: Ansible asserts `ansible_selinux.status == 'enabled'` and `mode == 'enforcing'` on every run and fails the play otherwise, and the runbook page now links to this module.
:::

## 5. Ubuntu and Debian: AppArmor quick equivalents

Ubuntu uses **AppArmor** by default instead of SELinux. AppArmor confines programs by **path-based profiles** (`/etc/apparmor.d/usr.sbin.mysqld`) rather than labels, so there is no `restorecon` step: rules follow file paths. Fewer services are confined out of the box (MySQL, CUPS, tcpdump, some snap and container profiles; nginx and Apache are usually **not** confined by default).

| Task | SELinux (RHEL family) | AppArmor (Ubuntu/Debian) |
|---|---|---|
| Is it on? | `getenforce`, `sestatus` | `aa-status` (package `apparmor-utils`) |
| Find denials | `ausearch -m AVC -ts recent` | `journalctl -k \| grep 'apparmor="DENIED"'` or `/var/log/kern.log`, `/var/log/audit/audit.log` if auditd is installed |
| Explain a denial | `audit2why` | Read the log line: `operation`, `profile`, `name`, `requested_mask` |
| Diagnose one service without disabling all | `semanage permissive -a httpd_t` | `aa-complain /etc/apparmor.d/usr.sbin.mysqld`, then `aa-enforce` to revert |
| Allow a new data dir (e.g. moved MySQL datadir) | `semanage fcontext -a -e /var/lib/mysql /data/mysql` + `restorecon` | Add to `/etc/apparmor.d/local/usr.sbin.mysqld`: `/data/mysql/ r,` and `/data/mysql/** rwk,` then `apparmor_parser -r /etc/apparmor.d/usr.sbin.mysqld` |
| Custom port | `semanage port -a` | Usually nothing: AppArmor network rules are coarse (by family/type, not port number) |
| Generate rules from denials | `audit2allow -M` (review!) | `aa-logprof` (interactive; review each suggestion) |
| Confine a new app | `sepolicy generate` | `aa-genprof /opt/myapp/bin/myapp` |
| Container volumes | `:z` / `:Z`, `container_file_t` | Docker's `docker-default` profile; volume access is normally DAC only |

Example denial on Ubuntu after moving the MySQL datadir:

```text
audit: type=1400 audit(1764119000.123:55): apparmor="DENIED" operation="open" profile="/usr/sbin/mysqld" name="/data/mysql/ibdata1" pid=2201 comm="mysqld" requested_mask="rw" denied_mask="rw" fsuid=27 ouid=27
```

Same rule as SELinux: never "fix" it by disabling AppArmor (`systemctl disable apparmor` or `aa-teardown`); put the local override in `/etc/apparmor.d/local/` so package updates keep it.

## Command cheat sheet

| Command | What it does |
|---|---|
| `getenforce` / `sestatus` | Show current mode and policy |
| `setenforce 0` / `setenforce 1` | Temporary global permissive/enforcing (diagnostic only, revert immediately) |
| `semanage permissive -a httpd_t` / `-d` | Make one domain permissive / undo |
| `ausearch -m AVC,USER_AVC -ts recent -i` | Recent denials, interpreted |
| `ausearch -m AVC -ts today -c nginx` | Denials for one command |
| `ausearch -m AVC -ts recent --raw \| audit2why` | Explain why each denial happened |
| `journalctl -t setroubleshoot` / `sealert -l ID` | Human-readable analysis (setroubleshoot-server) |
| `semodule -DB` / `semodule -B` | Disable / re-enable dontaudit rules |
| `ls -Z`, `ls -dZ`, `ps -eZ`, `id -Z` | Labels of files, dirs, processes, your shell |
| `semanage fcontext -a -t TYPE "/path(/.*)?"` | Add a persistent file-context rule |
| `semanage fcontext -a -e /std/path /new/path` | Label new path like a standard one (equivalence) |
| `semanage fcontext -d "/path(/.*)?"` | Delete a local rule |
| `semanage fcontext -l -C` | List local customisations only |
| `restorecon -Rv /path` / `restorecon -Rvn /path` | Apply rules / dry run |
| `getsebool -a \| grep httpd` / `semanage boolean -l` | List booleans (with descriptions) |
| `setsebool -P name 1` | Set a boolean persistently |
| `semanage port -l \| grep -w 8085` | Show a port's type |
| `semanage port -a -t http_port_t -p tcp 8085` / `-m` / `-d` | Add / modify / delete a port type |
| `audit2allow -M name` / `audit2allow -R` | Build a module / interface-based suggestions |
| `semodule -i name.pp` / `-l` / `-r name` | Install / list / remove modules |
| `sepolicy generate --init -n app /path/bin` | Scaffold a confined domain for a daemon |
| `semanage export` / `semanage import` | Back up / restore all local customisations |
| `touch /.autorelabel` / `fixfiles -F onboot` | Full relabel at next boot |
| `podman run -v /host:/ctr:Z` | Private container volume label (`:z` shared) |
| `aa-status`, `aa-complain`, `aa-enforce`, `apparmor_parser -r` | AppArmor equivalents |

:::summary
- Every SELinux ticket follows the same runbook: confirm enforcing, find the AVC (`ausearch -m AVC -ts recent`), classify it (`audit2why`), then fix with a **label**, a **boolean**, a **port type**, or (rarely) a **reviewed module**. No AVC usually means it is not SELinux; check `dontaudit` with `semodule -DB` before concluding.
- Most production denials are **wrong labels**: new directories (`default_t`, `var_t`), files moved with `mv`, restores onto hosts without the custom rules. Fix with `semanage fcontext` (or `-e` equivalence for moved standard dirs) plus `restorecon`, never `chcon`.
- Web servers (`httpd_t`, including nginx and php-fpm) need booleans for outbound connections (`httpd_can_network_connect`, `httpd_can_network_connect_db`, `httpd_can_sendmail`), NFS (`httpd_use_nfs`) and `httpd_sys_rw_content_t` for writable dirs. Pick the narrowest boolean.
- Confined daemons bind only typed ports: custom ports need `semanage port -a` (or `-m` if already defined) plus a firewall rule.
- systemd 203/EXEC from a home-built binary is `init_t` refusing `user_home_t`: install to `/opt` or `/usr/local` with `bin_t`.
- Containers need `container_file_t` with matching categories: `:z` shared, `:Z` private, never on system paths.
- Some AVCs (`execmem`, writes into the code tree) are app bugs to fix in code. Custom apps get real policy via `sepolicy generate`, built in a permissive domain and then enforced.
- After `rd.break` password resets or re-enabling SELinux, relabel. Back up `semanage export` so rebuilt hosts get your customisations.
- Never leave `setenforce 0`, `chmod 777` or blind `audit2allow` as the fix. On Ubuntu, the AppArmor equivalents are `aa-status`, `aa-complain`, and local overrides in `/etc/apparmor.d/local/`.
:::

## Self-check questions

<div class="q" markdown="1">

**Q1.** nginx returns 502 Bad Gateway. The error log shows `connect() to 127.0.0.1:5000 failed (13: Permission denied) while connecting to upstream`. `curl 127.0.0.1:5000` from your shell works. What do you check first?

A. Whether the backend is listening, with `ss -tlnp`
B. `ausearch -m AVC -ts recent -c nginx`, expecting a `name_connect` denial
C. The file permissions on `/etc/nginx/nginx.conf`
D. Increase `proxy_connect_timeout`

:::answer
**Answer: B.** Errno 13 on a TCP connect is a policy denial, not a network problem; a missing backend gives 111 (connection refused). The AVC will show `httpd_t` denied `name_connect`, fixed with `httpd_can_network_connect` or a port type.

- A is wrong: your curl already proved the backend listens.
- C is wrong: nginx parsed its config fine; the failure is at connect time.
- D is wrong: a timeout setting does not change a permission denial.
:::
</div>

<div class="q" markdown="1">

**Q2.** A developer ran `sudo mv ~/build/* /var/www/html/`. New pages return 403, old ones work. `ls -l` permissions look correct. What is the fix?

A. `chmod -R 755 /var/www/html`
B. `setsebool -P httpd_enable_homedirs 1`
C. `restorecon -Rv /var/www/html`
D. `chcon -R -t httpd_sys_rw_content_t /var/www/html`

:::answer
**Answer: C.** `mv` keeps the original `user_home_t` label; the policy already has the correct rule for `/var/www/html`, so `restorecon` fixes it.

- A is wrong: DAC is already fine.
- B is wrong: that boolean is for serving `~/public_html` and widens access to home content.
- D is wrong: `chcon` is not persistent and rw is the wrong type for static content.
:::
</div>

<div class="q" markdown="1">

**Q3.** After changing `Listen 80` to `Listen 8085`, httpd fails with `(13)Permission denied: AH00072: make_sock: could not bind to address [::]:8085`. What is the correct fix?

A. `semanage port -a -t http_port_t -p tcp 8085` and open the firewall
B. `setsebool -P httpd_can_network_connect 1`
C. Run httpd as root
D. `semanage fcontext -a -t http_port_t 8085`

:::answer
**Answer: A.** Binding is `name_bind`, controlled by port types; 8085 must become `http_port_t`, and the firewall must allow it.

- B is wrong: that boolean governs outbound connections, not binding.
- C is wrong: httpd already starts as root; the denial is SELinux, not a privileged-port issue.
- D is wrong: `fcontext` is for files, not ports.
:::
</div>

<div class="q" markdown="1">

**Q4.** `semanage port -a -t http_port_t -p tcp 8081` fails with `ValueError: Port tcp/8081 already defined`. What do you do?

A. Use `setenforce 0`
B. Check `semanage port -l | grep -w 8081`, confirm no local service needs the existing type, then use `semanage port -m -t http_port_t -p tcp 8081`
C. Delete the port type with `semodule -r`
D. Edit `/etc/services`

:::answer
**Answer: B.** The port already has a type; `-m` modifies it, but only after checking that nothing else on the host depends on the original type.

- A is wrong: disabling enforcement is never the fix.
- C is wrong: `semodule -r` removes policy modules, and removing a base port definition is not possible this way.
- D is wrong: `/etc/services` has no effect on SELinux port types.
:::
</div>

<div class="q" markdown="1">

**Q5.** A systemd service fails with `status=203/EXEC`. The binary is at `/home/deploy/app/bin/app`, and it runs fine by hand. An AVC shows `init_t` denied `execute` on `user_home_t`. Best fix?

A. `chmod +x` the binary
B. Install the binary to `/opt/app/bin`, add `semanage fcontext -a -t bin_t "/opt/app/bin(/.*)?"`, `restorecon -Rv /opt/app`, update `ExecStart`
C. `audit2allow -a -M fix && semodule -i fix.pp`
D. Add `User=root` to the unit

:::answer
**Answer: B.** Service binaries belong outside home directories with an executable label; then systemd may execute them.

- A is wrong: the exec bit is not the problem (the AVC proves SELinux blocked it).
- C is wrong: it would let init execute any home-directory file.
- D is wrong: the denial is about the domain, not the UID.
:::
</div>

<div class="q" markdown="1">

**Q6.** You moved PostgreSQL's data to `/data/pgsql` and it fails with `could not open file "global/pg_filenode.map": Permission denied`. Ownership is `postgres:postgres` and mode is `700`. Which command set is best?

A. `chcon -R -t postgresql_db_t /data/pgsql`
B. `semanage fcontext -a -e /var/lib/pgsql /data/pgsql` then `restorecon -Rv /data/pgsql`
C. `chmod -R 777 /data/pgsql`
D. `setsebool -P postgresql_can_rsync 1`

:::answer
**Answer: B.** An equivalence rule labels the new tree exactly like the standard datadir and persists across relabels.

- A is wrong: it works until the next `restorecon` or relabel, then breaks.
- C is wrong: DAC is not the problem, and 777 is insecure (PostgreSQL also refuses to start with a group/world-accessible data dir).
- D is wrong: that boolean is unrelated to the data directory label.
:::
</div>

<div class="q" markdown="1">

**Q7.** A podman container cannot read a bind-mounted `/srv/reports`, which only this container uses. The AVC shows `container_t:s0:c12,c88` denied on `var_t`. What do you do?

A. Mount with `-v /srv/reports:/reports:Z`
B. Mount `/srv` with `:Z` so all subdirectories work
C. `chmod 777 /srv/reports`
D. Run with `--privileged`

:::answer
**Answer: A.** `:Z` relabels the directory to `container_file_t` with this container's private categories.

- B is wrong: relabelling all of `/srv` can break other services that rely on its labels.
- C is wrong: DAC does not override an SELinux denial.
- D is wrong: privileged mode removes isolation wholesale; it is not a volume fix.
:::
</div>

<div class="q" markdown="1">

**Q8.** A logrotate config works when you run `logrotate -f` as root, but the nightly run fails with Permission denied on `/data/logs/app`. Why?

A. The timer runs logrotate as a non-root user
B. Your shell runs `unconfined_t`; the timer runs logrotate in the confined `logrotate_t`, which cannot touch `default_t` files
C. logrotate does not support paths outside `/var/log`
D. The disk is full

:::answer
**Answer: B.** Manual tests run unconfined; scheduled runs transition into a confined domain. Label `/data/logs` as `var_log_t`.

- A is wrong: logrotate runs as root from the timer.
- C is wrong: logrotate supports any path; SELinux is restricting it.
- D is wrong: a full disk gives ENOSPC, not a permission error with an AVC.
:::
</div>

<div class="q" markdown="1">

**Q9.** A PHP app logs `preg_match(): JIT compilation failed` and the AVC shows `{ execmem }` for `httpd_t`. What is the preferred fix?

A. `setsebool -P httpd_execmem 1` on every web server
B. Set `pcre.jit=0` in PHP's configuration and restart php-fpm
C. `semanage permissive -a httpd_t` permanently
D. Relabel `/var/www/html` as `httpd_sys_script_exec_t`

:::answer
**Answer: B.** Disabling the PCRE JIT removes the need for writable-executable memory without weakening the whole web domain.

- A is wrong as the default: it weakens protection for all httpd processes; use it only when justified and documented.
- C is wrong: a permanently permissive domain is SELinux off for that service.
- D is wrong: file labels do not affect `execmem`, a process permission.
:::
</div>

<div class="q" markdown="1">

**Q10.** After a DR rebuild, restored web content in `/data/www` returns 403 with `default_t` in the AVC. On the old server it worked. What was most likely missed, and how do you prevent it?

A. The backup lost the file permissions; restore with `tar -p`
B. The custom `semanage fcontext` rules were not on the new host; import them (`semanage export`/`import` or Ansible) and run `restorecon`
C. The new host has a different kernel
D. httpd must be reinstalled

:::answer
**Answer: B.** Label rules live in the local policy store, not with the data. A rebuilt host lacks them until you import or re-apply them.

- A is wrong: the AVC shows a label problem, not DAC.
- C is wrong: kernel version does not cause `default_t`.
- D is wrong: reinstalling httpd does not add rules for `/data/www`.
:::
</div>

<div class="q" markdown="1">

**Q11.** A colleague reset the root password via `rd.break` and now nobody can log in. What was skipped?

A. `grub2-mkconfig`
B. `touch /.autorelabel` (or `load_policy -i; restorecon /etc/shadow`) before rebooting
C. `systemctl daemon-reload`
D. `passwd -u root`

:::answer
**Answer: B.** `passwd` in the emergency shell wrote `/etc/shadow` without a label; the login stack cannot read `unlabeled_t`. Relabel it.

- A is wrong: GRUB config is unaffected.
- C is wrong: systemd unit files did not change.
- D is wrong: the account was not locked; the file was unreadable.
:::
</div>

<div class="q" markdown="1">

**Q12.** A custom daemon must be confined. You have a dozen AVCs. A colleague proposes `audit2allow -a -M mydaemon && semodule -i mydaemon.pp`. What is the better approach?

A. Exactly that; it is the standard method
B. Disable SELinux for that daemon with `setenforce 0`
C. Use `sepolicy generate --init` to create a dedicated domain and file types, run it permissive-per-domain, add reviewed rules (`audit2allow -R`), then remove `permissive` and enforce
D. Run the daemon from `/home` so it is unconfined

:::answer
**Answer: C.** A generated policy gives the app its own types, so rules are narrow, versionable and reviewable; the permissive-domain phase collects all needs without affecting other services.

- A is wrong: `-a` pulls in every denial ever logged, and generated rules are installed unreviewed.
- B is wrong: `setenforce 0` affects the whole system and is not a fix.
- D is wrong: it would not even run from systemd (scenario 8), and avoids rather than meets the requirement.
:::
</div>
