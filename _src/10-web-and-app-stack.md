<div class="cover" markdown="1">
<div class="mod">Module 10 · Web & Application Stack</div>

# Web & Application Stack Troubleshooting: Apache, Nginx, App Servers, TLS and Logs

<p>How to work out why a web request fails, from the load balancer through Apache or Nginx to Gunicorn, Tomcat or Node and on to the database. It covers HTTP status codes as clues, reverse proxy headers, certificate renewals, log analysis with awk and grep, deploy mistakes, and the SELinux rules that sit between every layer.</p>
</div>

## What you'll learn

- How to validate and inspect **Apache httpd** (`apachectl configtest`, `httpd -S`, vhosts, logs, `mod_ssl`), and what **403, 404, 500 and 503** usually mean on a real server
- How to validate **Nginx** (`nginx -t`, `nginx -T`), read upstream errors, and separate the causes of **502 and 504**: backend down, socket permissions, SELinux (`httpd_can_network_connect`) and timeouts
- How **reverse proxy headers** work, why every access log line shows the load balancer's IP, and how to fix it with `mod_remoteip` or the Nginx `realip` module
- How to renew **TLS certificates** with certbot, check the chain, and avoid permission and `cert_t` label problems
- How to run **application services** under systemd: Java/Tomcat heap, Python Gunicorn/uWSGI behind a unix socket, Node with pm2 or systemd, and environment variables and secrets
- How to debug **app-to-database connectivity**: firewall, SELinux booleans, `pg_hba.conf` and `bind-address`
- How to answer real questions from **access logs** with awk and grep (top IPs, status codes, slow requests, date ranges, rotated `.gz` files)
- How to avoid **deploy mistakes** (wrong owner or label, `rsync -X`, `git pull` as root), run **cron** jobs for apps, and choose between logrotate's **copytruncate** and **postrotate reload**
- Four break-fix labs: an Nginx to Gunicorn 502, an Apache 403 on a new DocumentRoot, log extraction across rotated logs, and an app that can't reach its database

**Why it matters in production:** most user-visible outages show up as an HTTP status code in front of someone's face. The web tier is where several layers meet: Unix permissions, SELinux, firewalld, systemd and application config. A 502 tells you which hop failed, not why. Engineers who know the clue each code gives, and where to read the matching log line, fix in ten minutes what others spend an evening on.

## 1. How a request flows, and where it breaks

Before touching a config file, draw the path. A typical production request on a RHEL server looks like this:

```text
Client ──HTTPS──> Load balancer (10.0.0.5) ──HTTP/HTTPS──> Nginx or Apache (web01, 10.0.0.10)
                                                              │  proxy_pass / ProxyPass
                                                              ▼
                                 App server: Gunicorn (unix socket) / Tomcat :8080 / Node :3000
                                                              │
                                                              ▼
                                                PostgreSQL / MariaDB (db01, 10.0.0.20)
```

Every arrow is a place where something can say no: a firewall, a Unix permission, an SELinux policy rule, a timeout or a process that isn't running. The HTTP status code tells you **which hop gave up**:

| Code | Who generated it | First thought |
|---|---|---|
| **403 Forbidden** | The web server itself | Config (`Require`, `deny`), Unix permissions, or SELinux label on the files |
| **404 Not Found** | Web server or app | Wrong vhost, wrong DocumentRoot/`root`, the file isn't where you think, or the app's routing |
| **500 Internal Server Error** | Web server (bad `.htaccess`, CGI) or the app | Read the error log or the app log; it's almost always a stack trace or a config syntax error |
| **502 Bad Gateway** | The proxy (Nginx; Apache for bad responses) | The proxy could not get a valid response from the backend: down, refused, permission denied |
| **503 Service Unavailable** | Apache proxy, a load balancer, or the app | Apache: backend connect failed. LB: no healthy members. App: maintenance mode or overload |
| **504 Gateway Timeout** | The proxy | The backend accepted the connection but did not answer in time |

:::concept
**Work from the outside in, then from the inside out.** First reproduce with `curl` from outside to get the exact code and headers. Then go to the server and test each hop directly: the web server on localhost, then the backend directly (TCP port or unix socket), then the database from the app host. The first hop that fails is your problem; everything after it is noise.
:::

The single most useful command for this is `curl` with a write-out format:

```bash
curl -sk -o /dev/null -w '%{http_code} %{time_connect} %{time_starttransfer} %{time_total}\n' https://app.example.com/health
```

```text
502 0.004121 0.006874 0.006951
```

`time_connect` is the TCP handshake, `time_starttransfer` is time to first byte. A 502 in 7 ms means the proxy failed instantly (refused or permission denied). A 504 at exactly 60 seconds means you hit a proxy timeout. To test a specific server behind a load balancer without changing DNS, use `--resolve`:

```bash
curl -sv --resolve app.example.com:443:10.0.0.10 https://app.example.com/ -o /dev/null
```

## 2. Apache httpd

### Layout and the commands you use every day

On RHEL 9 the package is `httpd`; on Ubuntu/Debian it is `apache2` with `apache2ctl`, `/etc/apache2/sites-available` and `a2ensite`.

| Item | RHEL / Rocky / Alma | Ubuntu / Debian |
|---|---|---|
| Main config | `/etc/httpd/conf/httpd.conf` | `/etc/apache2/apache2.conf` |
| Extra config, vhosts | `/etc/httpd/conf.d/*.conf` | `/etc/apache2/sites-enabled/` |
| Modules | `/etc/httpd/conf.modules.d/` | `a2enmod`, `/etc/apache2/mods-enabled/` |
| Logs | `/var/log/httpd/access_log`, `error_log` | `/var/log/apache2/access.log`, `error.log` |
| Run-as user | `apache` | `www-data` |
| Default DocumentRoot | `/var/www/html` | `/var/www/html` |

```bash
apachectl configtest          # same as: httpd -t
httpd -S                      # parsed vhost table: which vhost answers which name/port
httpd -M | grep -E 'ssl|proxy|remoteip'   # loaded modules
systemctl reload httpd        # graceful: re-reads config, keeps serving
journalctl -u httpd -n 50 --no-pager
```

```text
Syntax OK
```

`httpd -S` is the command people forget. It shows how Apache actually parsed your virtual hosts:

```text
VirtualHost configuration:
*:443                  is a NameVirtualHost
         default server www.example.com (/etc/httpd/conf.d/ssl.conf:40)
         port 443 namevhost www.example.com (/etc/httpd/conf.d/ssl.conf:40)
         port 443 namevhost app.example.com (/etc/httpd/conf.d/app.conf:12)
*:80                   app.example.com (/etc/httpd/conf.d/app.conf:1)
ServerRoot: "/etc/httpd"
Main DocumentRoot: "/var/www/html"
Main ErrorLog: "/etc/httpd/logs/error_log"
...
```

:::warn
**The first vhost on an IP:port is the default.** Any request whose `Host` header doesn't match a `ServerName` or `ServerAlias` goes to the first vhost listed for that port. If users hitting `https://app.example.com` see someone else's site or a 404, check `httpd -S` for a missing or misspelled `ServerName`, or a vhost bound to a different `IP:port` pattern than the others.
:::

Always run `configtest` **before** `reload` or `restart`. A `restart` with a broken config stops Apache and fails to start it again; a `reload` with a broken config fails and leaves the old config running, which is less harmful but still confusing.

### A typical vhost

```text
<VirtualHost *:80>
    ServerName app.example.com
    DocumentRoot /srv/www/app
    ErrorLog  /var/log/httpd/app_error.log
    CustomLog /var/log/httpd/app_access.log combined

    <Directory /srv/www/app>
        Options -Indexes +FollowSymLinks
        AllowOverride None
        Require all granted
    </Directory>
</VirtualHost>
```

### Reading the status codes on Apache

**403 Forbidden.** Apache 2.4 has three independent gates, and any one of them produces a 403. The error log tells you which:

| `error_log` message | Gate | Fix |
|---|---|---|
| `AH01630: client denied by server configuration: /srv/www/app/` | Config: no `Require all granted` for that path (httpd.conf denies `/` by default) | Add a `<Directory>` block with `Require all granted` |
| `AH00035: access to /index.html denied ... because search permissions are missing on a component of the path` | Unix: a parent directory lacks `x` for `apache` | `chmod o+x` (or group) on each parent; check with `namei -l` |
| `(13)Permission denied: AH00132: file permissions deny server access: /srv/www/app/index.html` | Unix permissions on the file, **or SELinux** | Check mode/owner; if they look fine, check the SELinux label |
| `AH01276: Cannot serve directory /srv/www/app/: No matching DirectoryIndex ... and forbidden by Options directive` | No `index.html` and `-Indexes` | Deploy the index file, or fix `DirectoryIndex` |

:::concept
**SELinux and web content.** Apache (and Nginx, and PHP-FPM) run in the SELinux domain `httpd_t`. That domain may read files labeled `httpd_sys_content_t`, write to `httpd_sys_rw_content_t`, and execute scripts labeled `httpd_sys_script_exec_t`. Content under `/var/www` gets the right label automatically. Content anywhere else (`/srv/www`, `/data/site`, `/opt/myapp/public`) gets whatever its parent has, often `var_t`, `default_t` or `usr_t`, and Apache is denied even with mode 777. The error log shows a plain `(13)Permission denied`; the **audit log** shows the real reason.
:::

```bash
ls -Zd /srv/www/app /srv/www/app/index.html
ausearch -m AVC -ts recent -c httpd
```

```text
system_u:object_r:var_t:s0 /srv/www/app
unconfined_u:object_r:var_t:s0 /srv/www/app/index.html
----
type=AVC msg=audit(1795600000.123:412): avc:  denied  { read } for  pid=2210 comm="httpd"
  name="index.html" dev="dm-0" ino=134 scontext=system_u:system_r:httpd_t:s0
  tcontext=unconfined_u:object_r:var_t:s0 tclass=file permissive=0
```

The fix is a **persistent** file context rule plus `restorecon`, never `chcon` alone (a relabel or `restorecon` would undo `chcon`) and never `setenforce 0`:

```bash
semanage fcontext -a -t httpd_sys_content_t '/srv/www(/.*)?'
restorecon -Rv /srv/www
```

If the app must write somewhere (uploads, cache), label only that directory as `httpd_sys_rw_content_t`:

```bash
semanage fcontext -a -t httpd_sys_rw_content_t '/srv/www/app/uploads(/.*)?'
restorecon -Rv /srv/www/app/uploads
```

**404 Not Found.** Check which vhost answered (`httpd -S`, and the access log file the request landed in), then the path Apache tried. The error log usually does not log plain 404s; the access log does. Compare the request path with `DocumentRoot` and any `Alias`. Remember Linux paths are case-sensitive, so `/Images/logo.png` and `/images/logo.png` are different files.

**500 Internal Server Error.** On Apache itself, the classic cause is an invalid directive in `.htaccess` (`AllowOverride All` is set and someone added a directive whose module isn't loaded):

```text
[core:alert] /srv/www/app/.htaccess: Invalid command 'RewriteEngine', perhaps misspelled or defined by a module not included in the server configuration
```

Otherwise a 500 comes from the application (PHP fatal error, Python exception). Read the app's own log or the PHP-FPM log (`/var/log/php-fpm/www-error.log` on RHEL).

**503 Service Unavailable.** With `mod_proxy`, Apache returns 503 when it **cannot connect** to the backend:

```text
[proxy:error] (111)Connection refused: AH00957: http: attempt to connect to 127.0.0.1:8080 (127.0.0.1:8080) failed
[proxy_http:error] AH01114: HTTP: failed to make connection to backend: 127.0.0.1
```

`(111)Connection refused` means nothing is listening. `(13)Permission denied` in the same message means SELinux blocked `httpd_t` from connecting to that port (see section 3).

:::tip
After a backend failure, `mod_proxy` puts the worker into an error state and does not retry it for **60 seconds** by default (`retry=60`). So you start the backend, reload the page, and still get 503 for up to a minute. For a single-backend `ProxyPass`, set `retry=0` (for example `ProxyPass "/" "http://127.0.0.1:8080/" retry=0`) so recovery is immediate.
:::

### mod_ssl

```bash
dnf install -y mod_ssl
```

This adds `/etc/httpd/conf.d/ssl.conf` with `Listen 443` and a default vhost using a self-signed `localhost.crt`. Your own TLS vhost needs:

```text
<VirtualHost *:443>
    ServerName app.example.com
    SSLEngine on
    SSLCertificateFile    /etc/letsencrypt/live/app.example.com/fullchain.pem
    SSLCertificateKeyFile /etc/letsencrypt/live/app.example.com/privkey.pem
    ...
</VirtualHost>
```

Since Apache 2.4.8, `SSLCertificateFile` can contain the leaf certificate followed by the intermediates, so point it at the **full chain**. `SSLCertificateChainFile` is deprecated. Common mod_ssl startup failures, all visible in `journalctl -u httpd` and `/var/log/httpd/ssl_error_log`:

- `AH02565: Certificate and private key ... do not match`: wrong key file for the certificate.
- `AH02312: Fatal error initialising mod_ssl` combined with `Permission denied` on the key: Unix permissions or an SELinux label on the certificate or key (section 5).
- `Address already in use: AH00072: make_sock: could not bind to address [::]:443`: two `Listen 443` lines, or Nginx already owns the port (`ss -tlnp 'sport = :443'`).

## 3. Nginx and the 502/504 family

### Commands

```bash
nginx -t                      # syntax check plus a test open of certs and log files
nginx -T | less               # dump the full effective config, including all includes
systemctl reload nginx        # graceful reload (same as nginx -s reload)
tail -f /var/log/nginx/error.log
```

```text
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful
```

On RHEL, Nginx runs as user `nginx` and, like Apache, in the SELinux domain **`httpd_t`**. Every SELinux rule in this module about Apache applies equally to Nginx. Site configs live in `/etc/nginx/conf.d/*.conf`. On Ubuntu the user is `www-data` and sites live in `/etc/nginx/sites-available` with symlinks in `sites-enabled`.

### A reverse proxy server block

```text
upstream myapp {
    server unix:/run/myapp/myapp.sock;      # or: server 127.0.0.1:8000;
}

server {
    listen 80;
    server_name app.example.com;

    location / {
        proxy_pass http://myapp;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 5s;
        proxy_read_timeout    60s;
    }
}
```

### 502 Bad Gateway: read the error log, the errno tells you everything

Nginx writes the exact system call error in brackets. Learn these four lines and you have most 502s solved:

| `error.log` line (abridged) | Meaning | Next check |
|---|---|---|
| `connect() to unix:/run/myapp/myapp.sock failed (2: No such file or directory)` | Socket file doesn't exist: app not running, or path mismatch | `systemctl status myapp`, `ls -l /run/myapp/` |
| `connect() failed (111: Connection refused) while connecting to upstream ... upstream: "http://127.0.0.1:8000/"` | Nothing listening on that port, or the socket file is stale | `ss -tlnp 'sport = :8000'`, app status and logs |
| `connect() to unix:/run/myapp/myapp.sock failed (13: Permission denied)` | Unix permissions on the socket or a parent dir, **or SELinux** | `namei -l` on the socket path, then `ausearch -m AVC` |
| `connect() to 127.0.0.1:8000 failed (13: Permission denied) while connecting to upstream` | On a TCP port, this is **almost always SELinux** | `getsebool httpd_can_network_connect` |
| `upstream prematurely closed connection while reading response header` | Backend accepted then died or was killed (crash, OOM, worker timeout) | App log, `journalctl -u myapp`, `dmesg` for OOM kills |

:::concept
**Why SELinux blocks a proxy.** The `httpd_t` domain may connect only to ports with certain labels, such as `http_port_t` (80, 443, 8008, 8009, 8443, 9000 and others). Check with `semanage port -l | grep -w http_port_t`. Proxying to an app on port 8000, 3000 or 5000 is denied until you either set the boolean **`httpd_can_network_connect`** (allows `httpd_t` to connect to any TCP port) or label the backend port as `http_port_t` with `semanage port -a -t http_port_t -p tcp 8000`. The boolean is the standard answer for a reverse proxy; the port label is narrower if you prefer least privilege. If the port is already assigned to another type, `semanage port -a` fails and you use `-m` instead.
:::

```bash
getsebool httpd_can_network_connect
ausearch -m AVC -ts recent -c nginx | audit2why
```

```text
httpd_can_network_connect --> off

type=AVC msg=audit(1795601234.501:877): avc:  denied  { name_connect } for  pid=1734 comm="nginx"
  dest=8000 scontext=system_u:system_r:httpd_t:s0 tcontext=system_u:object_r:soundd_port_t:s0
  tclass=tcp_socket permissive=0

        Was caused by:
        One of the following booleans was set incorrectly.
        Description:
        Allow httpd to can network connect

        Allow access by executing:
        # setsebool -P httpd_can_network_connect 1
```

```bash
setsebool -P httpd_can_network_connect 1     # -P makes it survive a reboot
```

:::warn
Do not "test" with `setenforce 0` and then forget. If you need to confirm SELinux is the cause, use `setenforce 0`, retry once, then **immediately** `setenforce 1`, and apply the real fix (boolean, port label or file context). A server left in permissive mode is a compliance finding and loses the protection that stops a web shell from reading `/etc/shadow`-adjacent secrets or connecting out.
:::

Other booleans you meet on web servers:

| Boolean | Allows `httpd_t` to |
|---|---|
| `httpd_can_network_connect` | Connect to any TCP port (reverse proxy to apps, external APIs from PHP) |
| `httpd_can_network_connect_db` | Connect to database ports (PostgreSQL, MySQL) |
| `httpd_can_network_relay` | Act as a relay/forward proxy to HTTP-type ports |
| `httpd_can_sendmail` | Send mail (PHP `mail()`) |
| `httpd_use_nfs` / `httpd_use_cifs` | Read content stored on NFS / CIFS mounts |
| `httpd_enable_homedirs` | Serve `~user/public_html` |

List them all with `getsebool -a | grep ^httpd_` and see descriptions with `semanage boolean -l | grep httpd_`.

### 504 Gateway Timeout

504 means the TCP or socket connection succeeded but the backend did not send a response header within `proxy_read_timeout` (default **60s**). The log line is:

```text
upstream timed out (110: Connection timed out) while reading response header from upstream, client: 192.0.2.44, server: app.example.com, request: "GET /reports/export HTTP/1.1", upstream: "http://unix:/run/myapp/myapp.sock:/reports/export"
```

Causes, in order of frequency: a slow database query, all app workers busy (so the request queues), a call to an external API that hangs, or a backend that's stuck (deadlock, GC pause). If it says `while connecting to upstream` instead, the connection itself timed out (`proxy_connect_timeout`), which points to a network or firewall DROP on a remote backend.

:::warn
**Raising the timeout is rarely the fix.** If `/reports/export` takes three minutes, raising `proxy_read_timeout` to 300s only moves the 504 to the load balancer (which has its own idle timeout, often 60s) or to Gunicorn's worker timeout (default **30s**, after which Gunicorn kills the worker and Nginx logs `upstream prematurely closed connection`). Long jobs belong in a background queue with a "your export is ready" response.
:::

Apache's equivalents are `ProxyTimeout` (defaults to `Timeout`, which is 60s on RHEL) and, for a bad or empty backend response, a **502** with `AH01102: error reading status line from remote server`.

### Testing the backend directly

Skip Nginx and talk to the app:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/health
curl -s --unix-socket /run/myapp/myapp.sock http://localhost/health
ss -xlp | grep myapp          # unix sockets in LISTEN state, with owning process
ss -tlnp 'sport = :8000'      # TCP listener on 8000
```

If `curl` works as root but Nginx gets 502, the backend is fine and the problem is **between** Nginx and the backend: permissions, SELinux or a wrong path in the config. Remember root bypasses Unix permission checks but not SELinux, and your shell runs as `unconfined_t`, not `httpd_t`, so root's `curl` succeeding proves nothing about what Nginx is allowed to do.

## 4. Reverse proxy headers and the missing client IP

### The symptom

Every line in the access log shows the same client IP, the load balancer's:

```text
10.0.0.5 - - [25/Nov/2026:09:14:02 +0000] "GET /login HTTP/1.1" 200 5120 "-" "Mozilla/5.0 ..."
10.0.0.5 - - [25/Nov/2026:09:14:02 +0000] "POST /login HTTP/1.1" 302 0 "-" "curl/8.5.0"
```

Your security team asks "which IP brute-forced `/login`?" and the log can't answer. Rate limits and `Require ip` rules apply to the load balancer, so one abuser either blocks everyone or nobody.

:::story
A load balancer is like a hotel reception forwarding letters. The envelope that reaches your room always says "From: Reception". The guest's real name is written on a note inside, the `X-Forwarded-For` header. Your server must be told to open the envelope and read the note, but **only for letters coming from reception**, because anyone can write a note claiming to be someone else.
:::

### How the headers work

| Header | Set by | Contains |
|---|---|---|
| `X-Forwarded-For` | Each proxy appends the address it received the connection from | `client, proxy1, proxy2` (leftmost is the original claim) |
| `X-Real-IP` | Commonly set by Nginx (`$remote_addr`) | A single IP |
| `X-Forwarded-Proto` | The TLS-terminating proxy | `https` or `http` (apps use it to build redirect URLs) |
| `Forwarded` | RFC 7239 standard form | `for=192.0.2.44;proto=https` (less widely used) |

The leftmost value can be forged by the client. The trustworthy value is the rightmost one **added by a proxy you trust**. That is why both Apache and Nginx want you to declare which proxies are trusted.

### Fix on Apache: mod_remoteip

`mod_remoteip` is loaded by default on RHEL 9 (`httpd -M | grep remoteip`). Create `/etc/httpd/conf.d/remoteip.conf`:

```text
RemoteIPHeader X-Forwarded-For
RemoteIPInternalProxy 10.0.0.5 10.0.0.6

# %a is the client IP after mod_remoteip; %h would still be the LB
LogFormat "%a %l %u %t \"%r\" %>s %b \"%{Referer}i\" \"%{User-Agent}i\" %D" combined_real
```

Then use `CustomLog /var/log/httpd/app_access.log combined_real` in the vhost. `RemoteIPInternalProxy` is for private-address proxies; use `RemoteIPTrustedProxy` if your proxies have public addresses. The `%D` at the end logs the request duration in microseconds, which you'll want in section 9.

### Fix on Nginx: realip module

The RHEL Nginx build includes `ngx_http_realip_module`. In the `http` or `server` block:

```text
set_real_ip_from 10.0.0.5;
set_real_ip_from 10.0.0.6;
real_ip_header   X-Forwarded-For;
real_ip_recursive on;

log_format main_rt '$remote_addr - $remote_user [$time_local] "$request" '
                   '$status $body_bytes_sent "$http_referer" "$http_user_agent" '
                   'rt=$request_time urt=$upstream_response_time';
access_log /var/log/nginx/access.log main_rt;
```

With realip active, `$remote_addr` becomes the real client and `allow`/`deny` and `limit_req` work per client. `real_ip_recursive on` skips past all trusted proxies in a chain.

:::warn
Never trust `X-Forwarded-For` from everyone (`set_real_ip_from 0.0.0.0/0` or `RemoteIPTrustedProxy 0.0.0.0/0`). Any client could then send `X-Forwarded-For: 127.0.0.1` and bypass IP-based restrictions on admin pages. List only your load balancers' addresses or subnet.
:::

If the app behind Nginx also needs the real IP (Django, Flask, Rails), it must trust the header too, for example Werkzeug's `ProxyFix` or Gunicorn's `--forwarded-allow-ips` (default `127.0.0.1`). An app that generates `http://` redirects behind a TLS load balancer is missing `X-Forwarded-Proto` handling.

## 5. TLS certificate renewal

### Check what's actually served

```bash
echo | openssl s_client -connect app.example.com:443 -servername app.example.com 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
```

```text
subject=CN = app.example.com
issuer=C = US, O = Let's Encrypt, CN = R11
notBefore=Sep  1 03:12:44 2026 GMT
notAfter=Nov 30 03:12:43 2026 GMT
```

Always pass `-servername`; without SNI you may see the default vhost's certificate and draw the wrong conclusion. To see the whole chain the server sends, add `-showcerts`; the `Verify return code: 0 (ok)` line at the end tells you whether a client with the system trust store accepts it.

```bash
openssl x509 -in /etc/letsencrypt/live/app.example.com/cert.pem -noout -enddate
openssl x509 -checkend 1209600 -noout -in /etc/letsencrypt/live/app.example.com/cert.pem \
  && echo "valid for 14+ days" || echo "EXPIRES WITHIN 14 DAYS"
```

### certbot

certbot comes from **EPEL** on RHEL 9 (`dnf install epel-release` on Rocky/Alma, then `dnf install certbot python3-certbot-nginx` or `python3-certbot-apache`). On Ubuntu it's in the main archive or a snap.

```bash
certbot certificates                      # what certbot manages and when each expires
certbot renew --dry-run                   # test renewal against the staging server
systemctl list-timers | grep -i certbot   # the renewal timer
```

The EPEL package ships `certbot-renew.timer`; check that it is **enabled**. The snap and Debian packages use their own timer names (`snap.certbot.renew.timer`, `certbot.timer`). The timer name differs between packaging methods, so list timers rather than assuming.

Renewal failing is the classic "worked for 90 days, then broke" incident. Common causes:

| Symptom in `/var/log/letsencrypt/letsencrypt.log` | Cause | Fix |
|---|---|---|
| `Timeout during connect (likely firewall problem)` | Port 80 blocked for HTTP-01 validation | `firewall-cmd --permanent --add-service=http && firewall-cmd --reload`; check LB/security group |
| `Invalid response from http://app.example.com/.well-known/acme-challenge/...: 404` | A redirect or `location` rule swallows `/.well-known/` | Exempt `/.well-known/acme-challenge/` from redirects/auth |
| Renewal succeeded, but browsers still show the old cert | The web server was never reloaded | Add a deploy hook (below) |
| `Permission denied` from httpd/nginx on startup after renewal | Wrong mode or SELinux label on a copied cert/key | Fix permissions and label (below) |

The renewed certificate is useless until the web server reloads. Add a deploy hook that runs only when a certificate actually changes:

```bash
certbot renew --deploy-hook "systemctl reload nginx"
```

Or drop an executable script into `/etc/letsencrypt/renewal-hooks/deploy/` so every renewal triggers it.

### Chain problems

Serve `fullchain.pem` (leaf + intermediate), not `cert.pem` (leaf only). Browsers often hide the mistake because they cache intermediates; `curl`, Java clients and mobile apps fail with `unable to get local issuer certificate`. Check that the key matches the certificate:

```bash
openssl x509 -in fullchain.pem -noout -pubkey | sha256sum
openssl pkey -in privkey.pem -pubout | sha256sum
```

The two hashes must be identical.

### Permissions and SELinux labels on certificates

Private keys should be `0600` (or `0640` with a dedicated group), owned by root. Apache and Nginx read keys as root during startup before dropping privileges, so root ownership is fine. Java and Node apps that terminate TLS themselves run as an unprivileged user and need group read access.

SELinux lets `httpd_t` read certificates labeled **`cert_t`** (the label on `/etc/pki/tls`, and on `/etc/letsencrypt` in current policy; verify with `ls -Zd /etc/letsencrypt`). The trap is a certificate someone created in their home directory and **moved** into place:

```bash
ls -Z /etc/pki/tls/private/app.key
```

```text
unconfined_u:object_r:user_home_t:s0 /etc/pki/tls/private/app.key
```

`mv` keeps the original label (`user_home_t`); `cp` creates a new file that inherits the destination's label. Nginx then fails to start with `cannot load certificate key ... Permission denied`. Fix it with `restorecon`:

```bash
restorecon -Rv /etc/pki/tls
```

If you keep certificates in a custom directory, give it a rule first:

```bash
semanage fcontext -a -t cert_t '/etc/ssl/myapp(/.*)?'
restorecon -Rv /etc/ssl/myapp
```

:::prod
**The Saturday expiry.** Symptom: on a Saturday morning, the mobile app fails for all users with TLS errors; browsers show "Your connection is not private". The on-call engineer runs `openssl s_client` and sees `notAfter` was two hours ago. `certbot certificates` shows a valid certificate renewed 29 days earlier. Root cause: certbot renewed on time, but Nginx was never reloaded, so it kept serving the old certificate from memory. The original setup used a cron job with `certbot renew` and no hook, and the server had not been restarted in 60 days. Fix: `systemctl reload nginx`. Prevention: a deploy hook in `/etc/letsencrypt/renewal-hooks/deploy/`, and an external expiry check (monitoring probe or a daily `openssl x509 -checkend` job) that alerts at 14 days, measuring the certificate **as served**, not the file on disk.
:::

## 6. Application services under systemd

### Java and Tomcat

Tomcat isn't packaged in RHEL 9 base repositories; most sites install it from the upstream tarball under `/opt/tomcat` or use a vendor build, with a systemd unit. Memory settings go in `CATALINA_OPTS` (used only when starting the server), either in `bin/setenv.sh` or in the unit:

```ini
[Service]
User=tomcat
Environment="CATALINA_OPTS=-Xms1g -Xmx2g -XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=/var/log/tomcat"
```

| Symptom | Where | Meaning |
|---|---|---|
| `java.lang.OutOfMemoryError: Java heap space` in `catalina.out` | App log | Heap (`-Xmx`) too small, or a memory leak |
| Process vanishes, `Out of memory: Killed process 4120 (java)` in `dmesg` | Kernel | The **whole JVM** (heap + metaspace + threads + native) exceeded RAM or a cgroup `MemoryMax=`; `-Xmx` too large for the box |
| Requests hang for seconds at a time, CPU high | `jstat` | Constant full GC: heap nearly full |

```bash
jcmd $(pgrep -f catalina) GC.heap_info      # heap used/committed per region
jstat -gcutil $(pgrep -f catalina) 1000 5   # GC utilisation every second, 5 samples
```

`jcmd` and `jstat` must run as the same user as the JVM (`sudo -u tomcat jcmd ...`) and come with the JDK, not the JRE-only packages. Leave headroom: on a 4 GB VM, `-Xmx2g` to `-Xmx2560m` is reasonable; `-Xmx4g` guarantees an OOM kill eventually.

### Python: Gunicorn or uWSGI behind a unix socket

A unix socket is faster than TCP on localhost and isn't reachable from the network, but it adds a file with **an owner, a mode and an SELinux label** that Nginx must pass. A unit that gets all three right:

```ini
# /etc/systemd/system/myapp.service
[Unit]
Description=myapp (gunicorn)
After=network.target

[Service]
User=myapp
Group=nginx
WorkingDirectory=/opt/myapp
RuntimeDirectory=myapp
RuntimeDirectoryMode=0750
EnvironmentFile=/etc/myapp/myapp.env
ExecStart=/opt/myapp/venv/bin/gunicorn --workers 3 --timeout 30 \
          --umask 007 --bind unix:/run/myapp/myapp.sock wsgi:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

What each piece does:

- `RuntimeDirectory=myapp` makes systemd create `/run/myapp` owned by `myapp:nginx` at start and remove it at stop. `/run` is a tmpfs, so a directory you `mkdir` by hand disappears at reboot.
- `RuntimeDirectoryMode=0750` lets group `nginx` enter the directory.
- `--umask 007` makes the socket `srwxrwx---`, so group `nginx` can write to it (connecting to a unix socket requires **write** permission on the socket file).

Then the SELinux side. `httpd_t` needs permission to write to the socket file. Give the socket directory the web runtime label:

```bash
semanage fcontext -a -t httpd_var_run_t '/run/myapp(/.*)?'
restorecon -Rv /run/myapp
```

systemd applies the file context rules when it creates the `RuntimeDirectory`, and the socket Gunicorn creates inside inherits the directory's type, so this survives restarts and reboots. Lab 10-A walks through the whole failure.

:::tip
Keep sockets out of home directories. Home directories are usually `0700` (Nginx can't traverse them) and labeled `user_home_dir_t`/`user_home_t` (SELinux denies `httpd_t`). Even with `httpd_enable_homedirs` on, it's the wrong place for a production socket. Use `/run/<app>/`.
:::

uWSGI is the same story with different flags: `socket = /run/myapp/myapp.sock`, `chmod-socket = 660`, `chown-socket = myapp:nginx`, and Nginx uses `uwsgi_pass unix:/run/myapp/myapp.sock;` with `include uwsgi_params;` instead of `proxy_pass`.

Gunicorn worker timeouts look like this in `journalctl -u myapp`:

```text
[CRITICAL] WORKER TIMEOUT (pid:3127)
[INFO] Worker exiting (pid: 3127)
```

Nginx logs the matching `upstream prematurely closed connection`, and the user sees 502. Find the slow request rather than just raising `--timeout`.

### Node: pm2 or plain systemd

pm2 is a Node process manager; `pm2 startup systemd` prints a command that generates a `pm2-<user>.service` unit, and `pm2 save` stores the process list that unit resurrects at boot. Two common failures: someone ran `pm2 start` as root, so the app runs as root and the per-user unit starts an empty list; or someone forgot `pm2 save` after changes, so a reboot brings back the old process list.

Many teams skip pm2 and use systemd directly, which gives one source of truth, journald logging and cgroup limits:

```ini
[Service]
User=myapp
WorkingDirectory=/opt/myapp
Environment=NODE_ENV=production PORT=3000
ExecStart=/usr/bin/node /opt/myapp/server.js
Restart=on-failure
MemoryMax=1G
```

Services started by systemd from binaries in `/opt` or `/usr/bin/node` usually run as the SELinux domain `unconfined_service_t` (check with `ps -eZ | grep node`), so SELinux rarely blocks the app itself. It does block **Nginx or Apache connecting to the app's port**, which is the `httpd_can_network_connect` case from section 3. A Node app can't bind to ports below 1024 as a normal user; put Nginx in front rather than running Node as root.

### Environment variables and secrets in systemd

| Method | Good for | Watch out |
|---|---|---|
| `Environment=KEY=value` in the unit | Non-secret settings | **Any local user can read it** with `systemctl show myapp -p Environment`, and unit files are world-readable |
| `EnvironmentFile=/etc/myapp/myapp.env` | Secrets in a root-owned `0600` file | Contents aren't shown by `systemctl show`; still visible in `/proc/<pid>/environ` to root and the service user |
| `LoadCredential=dbpass:/etc/myapp/dbpass` (systemd 247+, so RHEL 9) | Secrets the app reads from a file in `$CREDENTIALS_DIRECTORY` | App must read the file, not an env var |

The file format for `EnvironmentFile` is `KEY=value` per line, no `export`, no shell expansion. A leading `-` (`EnvironmentFile=-/etc/myapp/myapp.env`) makes a missing file non-fatal, which also hides a typo in the path, so leave it off for files that must exist.

The most common env-var incident: "it works when I run it by hand but not as a service". Your shell has variables from `~/.bashrc` that systemd never reads. Compare:

```bash
systemctl show myapp -p Environment -p EnvironmentFiles
cat /proc/$(systemctl show -p MainPID --value myapp)/environ | tr '\0' '\n'
```

After editing a unit or drop-in, run `systemctl daemon-reload` then `systemctl restart myapp`. Use `systemctl edit myapp` for overrides so a package update doesn't overwrite your changes.

## 7. Database connectivity from app servers

When an app reports it can't reach the database, test four layers in order: name resolution and routing, the network path (firewall), the DB server's listen address and auth rules, and SELinux on the **app** side.

```bash
getent hosts db01.example.com
nc -zv 10.0.0.20 5432                         # nmap-ncat; or:
timeout 3 bash -c '</dev/tcp/10.0.0.20/5432' && echo open || echo closed
```

The error message tells you which layer:

| Error from the client | Meaning | Layer |
|---|---|---|
| `Connection refused` | The host answered: nothing listening on that address:port | DB is down, or bound to `127.0.0.1` only |
| `No route to host` | On RHEL, usually **firewalld's default reject** on the DB host (ICMP host-prohibited) | DB host firewall |
| Connection hangs, then `timed out` | Packets dropped silently | Network ACL, cloud security group, or a DROP rule |
| `no pg_hba.conf entry for host "10.0.0.10", user "myapp", database "mydb"` | Reached PostgreSQL; auth rules reject this source | `pg_hba.conf` |
| `Host '10.0.0.10' is not allowed to connect to this MariaDB server` | MariaDB has no grant for that user@host | `GRANT`/`CREATE USER` |
| `Permission denied` (errno 13) from a PHP app, but `psql` from a shell works | **SELinux** on the app server | `httpd_can_network_connect_db` |

**On the DB server:**

```bash
ss -tlnp 'sport = :5432'
```

```text
State  Recv-Q Send-Q  Local Address:Port  Peer Address:Port Process
LISTEN 0      200         127.0.0.1:5432       0.0.0.0:*     users:(("postgres",pid=911,fd=6))
```

Listening on `127.0.0.1` only. PostgreSQL defaults to `listen_addresses = 'localhost'`; set it in `postgresql.conf` (on RHEL, `/var/lib/pgsql/data/postgresql.conf`), add a `pg_hba.conf` line, and restart:

```text
# postgresql.conf
listen_addresses = '10.0.0.20'

# pg_hba.conf  (TYPE DATABASE USER ADDRESS METHOD)
host    mydb    myapp    10.0.0.10/32    scram-sha-256
```

`listen_addresses` needs a restart; `pg_hba.conf` changes need only `systemctl reload postgresql`. For MariaDB/MySQL, the equivalent is `bind-address` in `/etc/my.cnf.d/mariadb-server.cnf` (Ubuntu: `/etc/mysql/mariadb.conf.d/50-server.cnf` or `/etc/mysql/mysql.conf.d/mysqld.cnf`), and a user created for the app host's address.

Open the firewall to the app servers only, not the world:

```bash
firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="10.0.0.10/32" service name="postgresql" accept'
firewall-cmd --reload
```

On Ubuntu, `ufw allow from 10.0.0.10 to any port 5432 proto tcp`.

**On the app server:** if the app runs inside `httpd_t` (PHP under mod_php or PHP-FPM, which on RHEL runs as `httpd_t`), SELinux blocks outbound database connections by default:

```bash
getsebool httpd_can_network_connect_db
setsebool -P httpd_can_network_connect_db 1
```

This boolean covers the standard database ports (labeled `postgresql_port_t`, `mysqld_port_t` and similar). If your DB listens on a non-standard port, either label that port (`semanage port -a -t postgresql_port_t -p tcp 6432`) or use `httpd_can_network_connect`. Apps running as `unconfined_service_t` (most Gunicorn, Node and Tomcat units) don't need this boolean. If you are unsure, check the domain with `ps -eZ` and read the AVC.

## 8. Troubleshooting runbook

| Symptom | Check (command) | Likely cause | Fix |
|---|---|---|---|
| Web server won't start after a change | `apachectl configtest` / `nginx -t`; `journalctl -u httpd -n 50` | Syntax error, missing file, port in use | Fix the line named in the error; `ss -tlnp` for port conflicts |
| 403 on a new site | `tail error_log`; `namei -l <path>`; `ls -Z` | Missing `Require all granted`, no `x` on parents, wrong SELinux type | `<Directory>` block; `chmod o+x`; `semanage fcontext -t httpd_sys_content_t` + `restorecon` |
| Wrong site or 404 for a hostname | `httpd -S`; `nginx -T \| grep server_name` | No matching `ServerName`, default vhost answers | Add/fix `ServerName`/`server_name` |
| 502, `(2: No such file or directory)` | `systemctl status myapp`; `ls -l /run/myapp` | App down or socket path mismatch | Start app; align paths |
| 502, `(111: Connection refused)` | `ss -tlnp 'sport = :8000'` | Nothing listening | Start/fix app; check bind address |
| 502, `(13: Permission denied)` on socket | `namei -l /run/myapp/myapp.sock`; `ausearch -m AVC -ts recent` | Socket mode/group, or SELinux label | `Group=nginx`, `--umask 007`; `httpd_var_run_t` label |
| 502/503, `(13: Permission denied)` to TCP port | `getsebool httpd_can_network_connect` | SELinux blocks proxy connection | `setsebool -P httpd_can_network_connect 1` |
| 504 after exactly 60 s | Nginx error log `upstream timed out`; app log | Slow query, workers busy, hung external call | Fix slow path; tune workers; queue long jobs |
| All log lines show LB IP | `head access_log` | No realip/remoteip config | `mod_remoteip` + `%a`; Nginx `set_real_ip_from` |
| Old certificate still served after renewal | `openssl s_client ... \| openssl x509 -noout -dates` | Web server not reloaded | `systemctl reload`; certbot deploy hook |
| Web server fails on cert/key `Permission denied` | `ls -lZ` on cert and key | Key moved from home dir (`user_home_t`), wrong mode | `restorecon -Rv /etc/pki/tls`; `chmod 600` |
| App works by hand, fails as service | `systemctl show -p Environment`; `/proc/<pid>/environ` | Env vars only in your shell | `EnvironmentFile=`; `daemon-reload` |
| App can't reach DB | `nc -zv db 5432`; `ss -tlnp` on DB; `getsebool httpd_can_network_connect_db` | Firewall, bind address, `pg_hba.conf`, SELinux | Open port for source; fix bind/HBA; set boolean |
| Files 403/500 right after a deploy | `ls -lZ /srv/www/app`; `git -C ... log -1` | Deployed as root, labels copied with `rsync -X`/`mv` | `chown`, `restorecon -Rv` in the deploy script |
| Log file keeps growing after rotation | `lsof +L1`; `ls -l /var/log/myapp` | App holds the old (deleted/renamed) file open | `postrotate` reload, or `copytruncate` |

## 9. Log analysis with awk and grep

### Know your fields

The Apache `combined` format, which Nginx's default `main` format closely matches:

```text
192.0.2.44 - - [25/Nov/2026:10:15:32 +0000] "GET /api/orders?id=7 HTTP/1.1" 200 1532 "-" "Mozilla/5.0 ..."
$1         $2 $3 $4                   $5     $6   $7                $8        $9  $10
```

Splitting on whitespace, `$1` is the client IP, `$4` is `[date:time`, `$6` is `"METHOD`, `$7` is the path, and `$9` is the status code. The user agent contains spaces, so its field numbers vary; don't rely on fields after `$10`.

### The everyday one-liners

Top 10 client IPs:

```bash
awk '{print $1}' /var/log/httpd/access_log | sort | uniq -c | sort -rn | head
```

```text
  18234 10.0.0.5
   4120 192.0.2.44
    988 198.51.100.17
```

(If the top IP is your load balancer, go back to section 4.)

Count by status code:

```bash
awk '{print $9}' /var/log/nginx/access.log | sort | uniq -c | sort -rn
```

```text
  91342 200
   3012 304
    844 404
    127 502
     14 499
```

`499` is Nginx-specific: the client closed the connection before the response was sent, typically because it gave up waiting. A rise in 499 alongside slow responses means users or the LB are timing out.

All 5xx requests, with the top failing paths:

```bash
awk '$9 ~ /^5/ {print $9, $7}' /var/log/nginx/access.log | sort | uniq -c | sort -rn | head
```

Requests per minute (to spot a spike):

```bash
awk '{print substr($4, 2, 17)}' /var/log/httpd/access_log | uniq -c | tail -20
```

```text
    212 25/Nov/2026:10:14
    208 25/Nov/2026:10:15
   1874 25/Nov/2026:10:16
```

Slow requests need a duration field. With the Apache `%D` (microseconds) as the last field:

```bash
awk '$NF > 2000000 {print $NF/1000000 "s", $7}' /var/log/httpd/app_access.log | sort -rn | head
```

With the Nginx `rt=$request_time` format from section 4:

```bash
grep -o 'rt=[0-9.]* .*' /var/log/nginx/access.log >/dev/null   # confirm the field exists first
awk '{for (i=1;i<=NF;i++) if ($i ~ /^rt=/) {t=substr($i,4); if (t+0 > 2) print t, $7}}' \
    /var/log/nginx/access.log | sort -rn | head
```

### Date ranges

For a single day or hour, a fixed-string grep is fastest:

```bash
grep -F '[25/Nov/2026:10:' /var/log/httpd/access_log          # 10:00 to 10:59
grep -E '\[25/Nov/2026:(09|10):' /var/log/httpd/access_log     # 09:00 to 10:59
```

For a range across days, grep patterns get ugly, so convert the timestamp to a sortable number in awk:

```bash
awk -v from=2026112208 -v to=2026112418 '
BEGIN { split("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec", m, " ")
        for (i = 1; i <= 12; i++) mon[m[i]] = sprintf("%02d", i) }
{ split(substr($4, 2), t, "[/:]")          # t[1]=25 t[2]=Nov t[3]=2026 t[4]=10
  key = t[3] mon[t[2]] t[1] t[4]           # 2026112510
  if (key >= from && key <= to) print }' /var/log/httpd/access_log
```

The key is `YYYYMMDDHH`, so `from=2026112208 to=2026112418` means 22 Nov 08:00 through 24 Nov 18:59. Add `t[5]` for minutes if you need them. Timestamps are in the server's local time zone as logged (`+0000` here); if an incident report says "14:00 CET", convert first.

### Rotated and compressed logs

On RHEL, logrotate's global `dateext` produces names like `access_log-20261123`. Whether old files are compressed depends on the per-service config (the Nginx package's logrotate file enables `compress` with `delaycompress`; the httpd one relies on the global setting, which leaves compression commented out on RHEL 9). On Ubuntu you see `access.log.1`, `access.log.2.gz`. Your commands must handle both plain and gzipped files:

```bash
zgrep -h ' /export/report.csv ' /var/log/nginx/access.log*      # zgrep reads plain and .gz
zcat -f /var/log/nginx/access.log-* /var/log/nginx/access.log | awk '...'
```

`zcat -f` passes uncompressed files through unchanged, so one pipeline handles a mix. `-h` suppresses the filename prefix that `zgrep` adds when given several files. Order matters if you want chronological output: dateext names sort correctly with a shell glob; Ubuntu's numbered names don't (`.10` sorts before `.2`), so list them with `ls -tr` or `sort -V` and reverse.

:::tip
Before running a heavy pipeline on a busy production web server, copy the logs elsewhere or wrap the command with `nice -n 19 ionice -c3` so your analysis doesn't become the next incident. For anything you do weekly, ship logs to a central system instead.
:::

## 10. Deploy problems

Many "the site broke after the release" incidents are not code bugs but file ownership and labels.

**Wrong owner or mode.** A developer runs `sudo git pull` in `/opt/myapp`. Every changed file is now owned by root, so the app (running as `myapp`) can't write to `cache/` or `uploads/` and returns 500. Newer git also refuses to operate in a repository owned by someone else:

```text
fatal: detected dubious ownership in repository at '/opt/myapp'
```

(This check was added in git 2.35.2.) Don't silence it with `safe.directory='*'`. Run deploys as the owning user:

```bash
sudo -u myapp git -C /opt/myapp pull --ff-only
```

Also keep `.git` out of the DocumentRoot, or deny it explicitly; a readable `/.git/config` leaks repository URLs and sometimes credentials.

**Wrong SELinux labels.** The rules:

| Operation | Label the new file gets |
|---|---|
| Create a file, `cp` (without `--preserve=context`), `rsync` without `-X`, `git checkout` | Inherited from the destination directory (usually correct) |
| `mv` within a filesystem | **Keeps its old label** (often wrong: `user_home_t`, `tmp_t`) |
| `rsync -X`, `rsync -aX`, `cp -a`, `tar --selinux` extract | **Copies the source's label** (wrong if the source was a home dir or another server with different rules) |

`rsync -X` preserves extended attributes, and the SELinux label is an extended attribute. That is exactly what you want for a backup and exactly what you don't want when the source is `/home/deploy/build/`. Whatever tool you use, end every deploy with:

```bash
restorecon -Rv /srv/www/app
chown -R myapp:myapp /opt/myapp     # if your layout calls for it
```

`restorecon` uses the `semanage fcontext` rules, so it's always correct and idempotent. Put it in the deploy script or Ansible role, not in a runbook someone has to remember.

:::prod
**The deploy that 403'd the homepage.** Symptom: immediately after a Friday release, the marketing site returns 403 for every page; the previous release worked. What the engineer saw: `error_log` full of `(13)Permission denied: AH00132`; file modes were `644`, owner `apache`. Root cause: the new pipeline built the site in `/home/deploy/build`, then ran `mv build/* /var/www/html/`, so every file kept `user_home_t`. `ausearch -m AVC -ts today` showed the `httpd_t` read denials on `user_home_t`. Fix: `restorecon -Rv /var/www/html`, site back in seconds. Prevention: the pipeline now uses `rsync -a` (no `-X`) into the docroot followed by `restorecon -R`, and a smoke test `curl -f` against the vhost before the job reports success.
:::

## 11. Cron for application tasks

Apps often need scheduled jobs: cleanup, reports, queue flushes. Common failures:

- **Wrong user.** A job in root's crontab creates files the app can't touch. Use `crontab -u myapp -e`, or `/etc/cron.d/myapp` with a user field:

```text
# /etc/cron.d/myapp
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin
MAILTO=ops@example.com
*/15 * * * * myapp cd /opt/myapp && flock -n /run/lock/myapp-cleanup.lock ./venv/bin/python manage.py cleanup >> /var/log/myapp/cron.log 2>&1
```

- **Minimal environment.** Cron doesn't read your `.bashrc` and has a short `PATH`. Use full paths and load what you need explicitly (`set -a; . /etc/myapp/myapp.env; set +a` in a wrapper script if the env file must be shared; that file must be readable by the job's user).
- **`%` characters.** In a crontab, `%` means newline; `date +%F` must be written `date +\%F`.
- **Overlapping runs.** A job every 5 minutes that sometimes takes 7 minutes piles up. `flock -n` skips the run if the previous one still holds the lock.
- **Silent failure.** Redirect output to a log and set `MAILTO`, or you'll never know. Check `/var/log/cron` (RHEL) or `journalctl -u crond`; on Ubuntu `grep CRON /var/log/syslog`.

A systemd timer (`myapp-cleanup.timer` plus a `.service`) is often better: logs go to the journal, the job gets the same `User=`, `EnvironmentFile=` and limits as the app, `Persistent=true` catches up after downtime, and a run can't overlap itself because a service unit is already active.

## 12. logrotate: copytruncate vs postrotate reload

Rotation renames `app.log` to `app.log-20261125`. A process that opened `app.log` still holds a file descriptor to the same inode, so it keeps writing to the **renamed** file and the new `app.log` stays empty. If old files are then deleted, the disk space is never freed (`lsof +L1` shows deleted-but-open files). There are two ways to deal with that:

| | `create` + `postrotate` reload | `copytruncate` |
|---|---|---|
| How | Rename the file, create a new empty one, then signal the app to reopen its logs | Copy the file's contents, then truncate the original in place |
| Needs | App that reopens logs on a signal (Apache, Nginx `USR1`, most daemons) | Nothing from the app |
| Risk | None if the reload works | **Lines written between copy and truncate are lost**; if the app doesn't open the file with `O_APPEND`, it keeps writing at its old offset and the truncated file becomes sparse with a block of null bytes at the start |
| Use for | Web servers and anything that supports reopen | Apps that can't reopen logs (some Java and Node apps writing directly to a file) |

The httpd and Nginx packages already do the right thing (`postrotate` with `systemctl reload httpd` or `kill -USR1` on the Nginx master). For your own app:

```text
# /etc/logrotate.d/myapp
/var/log/myapp/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 0640 myapp myapp
    sharedscripts
    postrotate
        systemctl kill -s HUP myapp.service >/dev/null 2>&1 || true
    endscript
}
```

`sharedscripts` runs `postrotate` once for all matched files rather than once per file. Test without changing anything, then force a rotation:

```bash
logrotate -d /etc/logrotate.d/myapp       # debug/dry run: shows what it would do
logrotate -vf /etc/logrotate.d/myapp      # force a real rotation, verbose
```

Two logrotate errors you'll meet: `error: skipping "/var/log/myapp/app.log" because parent directory has insecure permissions` (the directory is group- or world-writable; add `su myapp myapp` to the stanza or fix the directory mode), and permission denials when logs live in a custom place like `/opt/myapp/logs`. logrotate runs confined as `logrotate_t`, which can manage `var_log_t` files but not arbitrary labels. Keep logs under `/var/log/myapp`, or label the custom directory:

```bash
semanage fcontext -a -t var_log_t '/opt/myapp/logs(/.*)?'
restorecon -Rv /opt/myapp/logs
```

:::tip
The best option for apps you control is often neither: log to **stdout/stderr** and let systemd's journal handle storage and rotation (`journalctl -u myapp`). Size limits go in `/etc/systemd/journald.conf` (`SystemMaxUse=`).
:::

## 13. Break-fix labs

All labs use a disposable Rocky Linux 9 or AlmaLinux 9 VM (RHEL 9 with a free developer subscription works too) with SELinux **enforcing** (`getenforce` must print `Enforcing`). Run commands as root. Lab 10-D needs a second VM.

:::lab Lab 10-A — Nginx to Gunicorn over a unix socket returns 502
**Goal:** reproduce the two most common causes of a 502 in front of a Python app, Unix socket permissions and SELinux, diagnose each from the logs, and fix them properly.

**Setup:**

```bash
dnf install -y nginx python3 policycoreutils-python-utils setroubleshoot-server
useradd -r -m -d /opt/myapp -s /sbin/nologin myapp
sudo -u myapp python3 -m venv /opt/myapp/venv
sudo -u myapp /opt/myapp/venv/bin/pip install gunicorn
cat > /opt/myapp/wsgi.py <<'EOF'
def app(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"hello from myapp\n"]
EOF
chown myapp:myapp /opt/myapp/wsgi.py
```

**Break it:** start Gunicorn with a socket only its own user can use, and point Nginx at it.

```bash
cat > /etc/systemd/system/myapp.service <<'EOF'
[Unit]
Description=myapp (gunicorn)
After=network.target

[Service]
User=myapp
Group=myapp
WorkingDirectory=/opt/myapp
RuntimeDirectory=myapp
ExecStart=/opt/myapp/venv/bin/gunicorn --workers 2 --bind unix:/run/myapp/myapp.sock wsgi:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/nginx/conf.d/myapp.conf <<'EOF'
server {
    listen 80 default_server;
    server_name app.example.com;
    location / {
        proxy_pass http://unix:/run/myapp/myapp.sock;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
EOF
systemctl daemon-reload
systemctl enable --now myapp nginx
```

If `nginx -t` complains about a duplicate `default_server`, remove `default_server` from the `server` block in `/etc/nginx/nginx.conf`.

**Symptoms:**

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost/
```

```text
502
```

**Diagnose:**

1. Is the app up? `systemctl status myapp` shows `active (running)`. Test the backend directly, bypassing Nginx:

```bash
curl -s --unix-socket /run/myapp/myapp.sock http://localhost/
```

```text
hello from myapp
```

The app works. The problem is between Nginx and the socket.

2. Read the Nginx error log:

```bash
tail -n 3 /var/log/nginx/error.log
```

```text
... [crit] 2301#2301: *1 connect() to unix:/run/myapp/myapp.sock failed (13: Permission denied) while connecting to upstream, client: ::1, server: app.example.com, request: "GET / HTTP/1.1", upstream: "http://unix:/run/myapp/myapp.sock:/", host: "localhost"
```

3. Check Unix permissions along the whole path:

```bash
namei -l /run/myapp/myapp.sock
```

```text
f: /run/myapp/myapp.sock
dr-xr-xr-x root  root  /
drwxr-xr-x root  root  run
drwxr-xr-x myapp myapp myapp
srwxr-xr-x myapp myapp myapp.sock
```

The directory is traversable, but connecting to a unix socket requires **write** permission on the socket, and user `nginx` is "other" with only `r-x`. (The exact default mode depends on Gunicorn's umask; on some versions it may already be more open.)

**Fix part 1, Unix permissions:** give the socket to group `nginx` and make it group-writable.

```bash
systemctl edit --full myapp
```

Change `Group=myapp` to `Group=nginx`, add `RuntimeDirectoryMode=0750`, and add `--umask 007` to the `ExecStart` line before `--bind`. Then:

```bash
systemctl restart myapp
ls -l /run/myapp/
curl -s -o /dev/null -w '%{http_code}\n' http://localhost/
```

```text
srwxrwx---. 1 myapp nginx 0 Nov 25 10:40 myapp.sock
502
```

Still 502. Check the log again: the same `(13: Permission denied)`. The Unix layer is now correct, so check SELinux.

4. Read the AVC denials:

```bash
ausearch -m AVC -ts recent -c nginx
ls -Zd /run/myapp /run/myapp/myapp.sock
```

```text
type=AVC msg=audit(...): avc:  denied  { write } for  pid=2301 comm="nginx" name="myapp.sock"
  dev="tmpfs" ino=1544 scontext=system_u:system_r:httpd_t:s0
  tcontext=system_u:object_r:var_run_t:s0 tclass=sock_file permissive=0

system_u:object_r:var_run_t:s0 /run/myapp
system_u:object_r:var_run_t:s0 /run/myapp/myapp.sock
```

`httpd_t` (Nginx) may not write to a `var_run_t` socket. Optional confirmation: `setenforce 0`, `curl` returns 200, then **immediately** `setenforce 1`.

**Fix part 2, SELinux:** label the socket directory for the web server, persistently.

```bash
semanage fcontext -a -t httpd_var_run_t '/run/myapp(/.*)?'
systemctl restart myapp            # systemd recreates /run/myapp with the new label
ls -Zd /run/myapp /run/myapp/myapp.sock
curl -s -o /dev/null -w '%{http_code}\n' http://localhost/
```

```text
system_u:object_r:httpd_var_run_t:s0 /run/myapp
system_u:object_r:httpd_var_run_t:s0 /run/myapp/myapp.sock
200
```

If you still get 502, run `ausearch -m AVC -ts recent | audit2why` again. Depending on the policy version, a second denial of the form `{ connectto } ... tcontext=...unconfined_service_t... tclass=unix_stream_socket` may appear. In that case review it with `ausearch -m AVC -ts recent | audit2allow -m myapp_sock`, and only if it contains just that `connectto` rule, install it with `audit2allow -M myapp_sock` and `semodule -i myapp_sock.pp`.

**Verify:** `curl http://localhost/` prints `hello from myapp`; reboot the VM and confirm it still works (this proves `RuntimeDirectory` and the fcontext rule survive a reboot). `ausearch -m AVC -ts recent` shows no new denials.

**Clean-up/revert:**

```bash
systemctl disable --now myapp nginx
rm -f /etc/systemd/system/myapp.service /etc/nginx/conf.d/myapp.conf
systemctl daemon-reload
semanage fcontext -d '/run/myapp(/.*)?'
semodule -r myapp_sock 2>/dev/null
userdel -r myapp
```
:::

:::lab Lab 10-B — Apache returns 403 on a new DocumentRoot
**Goal:** move a site to `/srv/www/site`, hit the three independent 403 gates (config, Unix permissions, SELinux), and fix each one the right way.

**Setup:**

```bash
dnf install -y httpd policycoreutils-python-utils
systemctl enable --now httpd
firewall-cmd --add-service=http
```

**Break it:** build the site as a normal user in a home directory, move it into place, and point a vhost at it.

```bash
useradd -m webdev
sudo -u webdev bash -c 'mkdir -p ~/site && echo "<h1>New site</h1>" > ~/site/index.html && chmod 600 ~/site/index.html'
mkdir -p /srv/www
chmod 700 /srv/www
mv /home/webdev/site /srv/www/site

cat > /etc/httpd/conf.d/site.conf <<'EOF'
<VirtualHost *:80>
    ServerName site.example.com
    DocumentRoot /srv/www/site
    ErrorLog /var/log/httpd/site_error.log
</VirtualHost>
EOF
apachectl configtest && systemctl reload httpd
```

**Symptoms:**

```bash
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: site.example.com' http://localhost/
```

```text
403
```

**Diagnose and fix, one gate at a time:**

1. Config gate. Read the vhost's error log:

```bash
tail -n 2 /var/log/httpd/site_error.log
```

```text
[authz_core:error] [pid 3310] [client ::1:51234] AH01630: client denied by server configuration: /srv/www/site/
```

`httpd.conf` contains `<Directory />` with `Require all denied`; only `/var/www` is granted. Add a `<Directory>` block inside the vhost:

```text
    <Directory /srv/www/site>
        Options -Indexes
        AllowOverride None
        Require all granted
    </Directory>
```

```bash
apachectl configtest && systemctl reload httpd
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: site.example.com' http://localhost/
```

Still `403`. Read the log again.

2. Unix permissions gate:

```text
AH00035: access to /index.html denied (filesystem path '/srv/www/site') because search permissions are missing on a component of the path
```

```bash
namei -l /srv/www/site/index.html
```

```text
f: /srv/www/site/index.html
dr-xr-xr-x root   root   /
drwxr-xr-x root   root   srv
drwx------ root   root   www
drwxr-xr-x webdev webdev site
-rw------- webdev webdev index.html
```

`/srv/www` is `700` and `index.html` is `600`, both unreadable by `apache`. Fix ownership and modes for content (root-owned, world-readable, not writable by the web server):

```bash
chown -R root:root /srv/www
chmod 755 /srv/www /srv/www/site
find /srv/www/site -type f -exec chmod 644 {} +
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: site.example.com' http://localhost/
```

Still `403`.

3. SELinux gate. The error log now shows `(13)Permission denied: ... AH00132: file permissions deny server access`, but the modes are fine, so look at the labels:

```bash
ls -Z /srv/www/site/index.html
ausearch -m AVC -ts recent -c httpd | audit2why
```

```text
unconfined_u:object_r:user_home_t:s0 /srv/www/site/index.html

type=AVC msg=audit(...): avc:  denied  { read } for  pid=3312 comm="httpd" name="index.html"
  scontext=system_u:system_r:httpd_t:s0 tcontext=unconfined_u:object_r:user_home_t:s0 tclass=file
        Was caused by:
                Missing type enforcement (TE) allow rule.
```

`mv` kept the home-directory label. `audit2why` says "missing allow rule", but the right answer is **not** `audit2allow` (that would let Apache read every home directory file on the system). The right answer is the correct label:

```bash
semanage fcontext -a -t httpd_sys_content_t '/srv/www(/.*)?'
restorecon -Rv /srv/www
```

```text
Relabeled /srv/www from unconfined_u:object_r:var_t:s0 to unconfined_u:object_r:httpd_sys_content_t:s0
Relabeled /srv/www/site from unconfined_u:object_r:user_home_dir_t:s0 to unconfined_u:object_r:httpd_sys_content_t:s0
Relabeled /srv/www/site/index.html from unconfined_u:object_r:user_home_t:s0 to unconfined_u:object_r:httpd_sys_content_t:s0
```

**Verify:**

```bash
curl -s -H 'Host: site.example.com' http://localhost/
matchpathcon /srv/www/site/index.html
```

```text
<h1>New site</h1>
/srv/www/site/index.html	system_u:object_r:httpd_sys_content_t:s0
```

`matchpathcon` confirms the policy rule exists, so a future `restorecon` or full relabel keeps the right label.

**Clean-up/revert:**

```bash
rm -f /etc/httpd/conf.d/site.conf && systemctl reload httpd
semanage fcontext -d '/srv/www(/.*)?'
rm -rf /srv/www
userdel -r webdev
firewall-cmd --reload
```
:::

:::lab Lab 10-C — Extract access-log lines for one file and a date range across rotated logs
**Goal:** a customer says "our download of `/files/report.pdf` failed between 22 Nov 08:00 and 24 Nov 18:00". Pull every matching request from current and rotated (plain and gzipped) logs, in order, and summarise them.

**Setup ("break it"):** generate a realistic set of rotated logs in a scratch directory, so the lab doesn't depend on real traffic.

```bash
mkdir -p /root/lab10c && cd /root/lab10c
gen() {  # gen <day> <count>
  for i in $(seq 1 "$2"); do
    h=$(printf '%02d' $((RANDOM % 24))); m=$(printf '%02d' $((RANDOM % 60)))
    ip="192.0.2.$((RANDOM % 20 + 1))"
    case $((RANDOM % 4)) in 0) p=/files/report.pdf;; 1) p=/index.html;; 2) p=/api/orders;; 3) p=/files/report.pdf.bak;; esac
    case $((RANDOM % 10)) in 0) s=500;; 1) s=404;; *) s=200;; esac
    echo "$ip - - [$1/Nov/2026:$h:$m:00 +0000] \"GET $p HTTP/1.1\" $s 2048 \"-\" \"curl/8.5.0\""
  done | sort -t: -k2,3
}
gen 21 200 > access_log-20261122
gen 22 200 > access_log-20261123
gen 23 200 > access_log-20261124
gen 24 200 > access_log-20261125
gen 25 100 > access_log
gzip access_log-20261122 access_log-20261123     # older files compressed, newer ones plain
ls -1
```

```text
access_log
access_log-20261122.gz
access_log-20261123.gz
access_log-20261124
access_log-20261125
```

Note that with `dateext`, a file named `-20261123` holds the day **before** the rotation date (rotation ran early on the 23rd). Don't pick files by name alone; filter on the timestamps inside.

**Symptoms (the naive approach fails):**

```bash
grep '/files/report.pdf' access_log* | wc -l
```

This misses the gzipped files (grep sees binary data), includes `report.pdf.bak`, prefixes each line with a filename and ignores the date range.

**Diagnose / build the right pipeline:**

1. Read every file, compressed or not, in chronological order. With dateext names the glob order is already chronological; the current `access_log` goes last:

```bash
zcat -f access_log-* access_log | wc -l
```

```text
900
```

2. Match the exact path. Field `$7` is the path, so compare it exactly instead of using a regex that also matches `.bak`:

```bash
zcat -f access_log-* access_log | awk '$7 == "/files/report.pdf"' | wc -l
```

3. Add the date range filter (`YYYYMMDDHH` key from section 9):

```bash
zcat -f access_log-* access_log | awk -v from=2026112208 -v to=2026112418 '
BEGIN { split("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec", m, " ")
        for (i = 1; i <= 12; i++) mon[m[i]] = sprintf("%02d", i) }
$7 == "/files/report.pdf" {
  split(substr($4, 2), t, "[/:]")
  key = t[3] mon[t[2]] t[1] t[4]
  if (key >= from && key <= to) print }' > report-pdf-range.log
wc -l report-pdf-range.log
head -3 report-pdf-range.log
tail -1 report-pdf-range.log
```

The first line should be on 22 Nov at 08:xx or later and the last on 24 Nov at 18:xx or earlier.

**Fix / summarise for the customer:**

```bash
awk '{print $9}' report-pdf-range.log | sort | uniq -c | sort -rn          # status codes
awk '$9 >= 500 {print $1}' report-pdf-range.log | sort | uniq -c | sort -rn # who got errors
awk '$9 >= 500 {print substr($4, 2, 14)}' report-pdf-range.log | uniq -c   # errors per hour
```

**Verify:** cross-check one count with a different tool. For example, count matching 22 Nov lines with `zgrep -h` and compare with your awk output for that day only:

```bash
zgrep -hF '[22/Nov/2026:' access_log-* | awk '$7 == "/files/report.pdf"' \
  | grep -cE '\[22/Nov/2026:(0[89]|1[0-9]|2[0-3]):'
awk '/\[22\/Nov\/2026:/' report-pdf-range.log | wc -l
```

The two numbers must match.

**Clean-up/revert:** `rm -rf /root/lab10c`. On a real server, never decompress logs in place under `/var/log`; read them with `zcat`/`zgrep` and write results to your own working directory.
:::

:::lab Lab 10-D — App server can't connect to the database (firewall + SELinux boolean)
**Goal:** a PHP app on `app01` can't reach PostgreSQL on `db01`. Find and fix the DB listen address, the DB host firewall and the SELinux boolean on the app host, in the order a request meets them.

**Setup:** two Rocky/Alma 9 VMs on the same network: `db01` at `10.0.0.20` and `app01` at `10.0.0.10` (adjust to your lab addresses).

On **db01**:

```bash
dnf install -y postgresql-server
postgresql-setup --initdb
systemctl enable --now postgresql
sudo -u postgres psql -c "CREATE USER myapp WITH PASSWORD 'LabOnly-ChangeMe1';"
sudo -u postgres psql -c "CREATE DATABASE mydb OWNER myapp;"
```

On **app01**:

```bash
dnf install -y httpd php php-pgsql postgresql
systemctl enable --now httpd
cat > /var/www/html/dbtest.php <<'EOF'
<?php
$c = @pg_connect("host=10.0.0.20 dbname=mydb user=myapp password=LabOnly-ChangeMe1 connect_timeout=5");
echo $c ? "DB OK\n" : "DB FAIL: " . error_get_last()['message'] . "\n";
EOF
```

**Break it:** nothing to do. The defaults are the break: PostgreSQL listens on localhost only, firewalld on `db01` doesn't allow 5432, and `httpd_can_network_connect_db` is off on `app01`.

**Symptoms (on app01):**

```bash
curl -s http://localhost/dbtest.php
```

```text
DB FAIL: pg_connect(): Unable to connect to PostgreSQL server: connection to server at "10.0.0.20", port 5432 failed: Permission denied ...
```

**Diagnose:**

1. Start from the app host's shell, which isn't confined like httpd:

```bash
nc -zv 10.0.0.20 5432
```

```text
Ncat: No route to host.
```

From the shell the error is different from PHP's. "No route to host" on RHEL usually means firewalld on the target rejected the packet. The PHP error says `Permission denied`, which is a local SELinux denial before a packet ever left. **Two problems at once**, which is why you test each layer separately.

2. On **db01**, check what's listening and the firewall:

```bash
ss -tlnp 'sport = :5432'
firewall-cmd --list-all
```

```text
LISTEN 0  244  127.0.0.1:5432  0.0.0.0:*  users:(("postgres",pid=1022,fd=7))
...
  services: cockpit dhcpv6-client ssh
```

Listening on loopback only, and no `postgresql` service in the zone.

3. On **app01**, check SELinux:

```bash
ausearch -m AVC -ts recent -c php-fpm | audit2why
getsebool httpd_can_network_connect_db
```

```text
type=AVC msg=audit(...): avc:  denied  { name_connect } for  pid=4410 comm="php-fpm" dest=5432
  scontext=system_u:system_r:httpd_t:s0 tcontext=system_u:object_r:postgresql_port_t:s0 tclass=tcp_socket
        Was caused by:
        One of the following booleans was set incorrectly.
        ...
        # setsebool -P httpd_can_network_connect_db 1

httpd_can_network_connect_db --> off
```

On RHEL 9, PHP runs under PHP-FPM, which is in the `httpd_t` domain; if `-c php-fpm` returns nothing, try `-c httpd` or drop the `-c` filter.

**Fix (on db01), listen address and HBA:**

```bash
sed -i "s/^#listen_addresses = 'localhost'/listen_addresses = '10.0.0.20'/" /var/lib/pgsql/data/postgresql.conf
echo "host  mydb  myapp  10.0.0.10/32  scram-sha-256" >> /var/lib/pgsql/data/pg_hba.conf
systemctl restart postgresql
ss -tlnp 'sport = :5432'
```

If `myapp`'s password was set before `password_encryption` was `scram-sha-256` (the default in PostgreSQL 14 and later; RHEL 9's default stream is 13, where the default is `md5`), use `md5` in the HBA line instead, or reset the password after changing `password_encryption`. Check the version with `postgres --version`.

**Fix (on db01), firewall, restricted to the app server:**

```bash
firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="10.0.0.10/32" service name="postgresql" accept'
firewall-cmd --reload
```

Back on **app01**, `nc -zv 10.0.0.20 5432` now prints `Connected`. `curl http://localhost/dbtest.php` still fails with `Permission denied`: the network is fixed, SELinux is not.

**Fix (on app01), SELinux boolean:**

```bash
setsebool -P httpd_can_network_connect_db 1
```

**Verify:**

```bash
curl -s http://localhost/dbtest.php
getsebool httpd_can_network_connect_db
```

```text
DB OK
httpd_can_network_connect_db --> on
```

Also confirm the firewall restriction: from a third host (or db01 itself using a different source IP), `nc -zv 10.0.0.20 5432` should still be rejected.

**Clean-up/revert:**

On app01: `setsebool -P httpd_can_network_connect_db 0; rm -f /var/www/html/dbtest.php`.
On db01: `firewall-cmd --permanent --remove-rich-rule='rule family="ipv4" source address="10.0.0.10/32" service name="postgresql" accept' && firewall-cmd --reload`, remove the `pg_hba.conf` line, restore `listen_addresses`, and `systemctl restart postgresql`. Or simply destroy both VMs.
:::

## Command cheat sheet

| Task | Command |
|---|---|
| Test Apache config | `apachectl configtest` (or `httpd -t`) |
| Show Apache vhost mapping | `httpd -S` |
| List loaded Apache modules | `httpd -M` |
| Test Nginx config | `nginx -t` |
| Dump full Nginx config | `nginx -T` |
| Graceful reload | `systemctl reload httpd` / `systemctl reload nginx` |
| Status code and timing | `curl -s -o /dev/null -w '%{http_code} %{time_total}\n' URL` |
| Test one backend behind an LB | `curl --resolve host:443:10.0.0.10 https://host/` |
| Talk to a unix socket | `curl --unix-socket /run/myapp/myapp.sock http://localhost/` |
| Listening TCP / unix sockets | `ss -tlnp`, `ss -xlp` |
| Permissions along a path | `namei -l /path/to/file` |
| Recent SELinux denials | `ausearch -m AVC -ts recent [-c nginx] \| audit2why` |
| Label web content | `semanage fcontext -a -t httpd_sys_content_t '/srv/www(/.*)?'` then `restorecon -Rv /srv/www` |
| Label a socket directory | `semanage fcontext -a -t httpd_var_run_t '/run/myapp(/.*)?'` |
| Allow proxy to any port | `setsebool -P httpd_can_network_connect 1` |
| Allow httpd to reach DBs | `setsebool -P httpd_can_network_connect_db 1` |
| Label a port for httpd | `semanage port -a -t http_port_t -p tcp 8000` |
| Certificate served, with SNI | `openssl s_client -connect host:443 -servername host \| openssl x509 -noout -dates` |
| Cert expires within 14 days? | `openssl x509 -checkend 1209600 -noout -in cert.pem` |
| Test certbot renewal | `certbot renew --dry-run` |
| Service environment | `systemctl show myapp -p Environment -p EnvironmentFiles` |
| JVM heap | `sudo -u tomcat jcmd <pid> GC.heap_info` |
| TCP port reachable? | `nc -zv 10.0.0.20 5432` |
| Open DB port to one host | `firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="10.0.0.10/32" service name="postgresql" accept'` |
| Top client IPs | `awk '{print $1}' access_log \| sort \| uniq -c \| sort -rn \| head` |
| Status code counts | `awk '{print $9}' access_log \| sort \| uniq -c \| sort -rn` |
| Search rotated logs | `zgrep -h PATTERN /var/log/nginx/access.log*` |
| Read plain and .gz together | `zcat -f access_log-* access_log` |
| Fix labels after deploy | `restorecon -Rv /srv/www/app` |
| Deploy as app user | `sudo -u myapp git -C /opt/myapp pull --ff-only` |
| Dry-run logrotate | `logrotate -d /etc/logrotate.d/myapp` |
| Deleted-but-open files | `lsof +L1` |

:::summary
- Read the **status code as a clue to which hop failed**: 403/404 from the web server, 500 usually from the app, 502 = proxy couldn't get a valid response, 503 (Apache) = couldn't connect, 504 = backend too slow.
- Always `apachectl configtest` / `nginx -t` before reload; use `httpd -S` and `nginx -T` to see the config as parsed.
- Apache 403 has three gates: **config** (`Require all granted`), **Unix permissions** (`namei -l`), **SELinux label** (`httpd_sys_content_t`). The error log message tells you which.
- Nginx errors include the errno: `2` = no socket, `111` = nothing listening, `13` = permission denied (Unix or SELinux), `110` = timeout.
- `httpd_t` (Apache, Nginx, PHP-FPM) needs `httpd_can_network_connect` to proxy to arbitrary ports, `httpd_can_network_connect_db` for databases, and `httpd_var_run_t` on unix socket directories. Fix with `setsebool -P`, `semanage port`, or `semanage fcontext` + `restorecon`, never by disabling SELinux.
- Behind a load balancer, configure **`mod_remoteip` with `%a`** or Nginx **`set_real_ip_from` + `real_ip_header`**, trusting only your LB addresses.
- certbot renewal is only half the job: **reload the web server** via a deploy hook, serve **fullchain.pem**, and keep keys `0600` with the `cert_t` label (`mv` keeps wrong labels; `restorecon` fixes them).
- Run apps under systemd with `RuntimeDirectory=`, the right `Group=` and umask for sockets, and secrets in a `0600` `EnvironmentFile` or `LoadCredential=`, not `Environment=`.
- Database connectivity: listen address, firewall restricted to app hosts, `pg_hba.conf`/grants, then the SELinux boolean. "No route to host" = firewalld reject; "Connection refused" = nothing listening; "Permission denied" = local SELinux.
- awk on `$1`, `$7`, `$9` and a sortable date key answers most log questions; `zcat -f` and `zgrep` cover rotated and compressed files.
- After every deploy: correct owner, `restorecon -R`, and never `rsync -X` or `mv` from a home directory into a docroot.
- logrotate: prefer **create + postrotate reload**; use **copytruncate** only for apps that can't reopen logs, and accept that it can lose lines.
:::

## Self-check questions

<div class="q" markdown="1">

**Q1.** After moving a website to `/data/web`, Apache returns 403. Modes are `755` on directories and `644` on files, there is a `<Directory /data/web>` block with `Require all granted`, and `error_log` shows `(13)Permission denied: AH00132: file permissions deny server access`. What do you check first?

A. Whether the `apache` user's shell is `/sbin/nologin`
B. The SELinux label of the files with `ls -Z` and recent AVCs with `ausearch -m AVC -ts recent`
C. Whether `AllowOverride All` is set
D. Whether port 80 is open in firewalld

:::answer
**Answer: B.** Config and Unix permissions are already correct, so the remaining gate is SELinux. Files outside `/var/www` don't get `httpd_sys_content_t`; fix with `semanage fcontext -a -t httpd_sys_content_t '/data/web(/.*)?'` and `restorecon -Rv /data/web`.

- A is wrong: the login shell doesn't affect file access by the daemon.
- C is wrong: `AllowOverride` controls `.htaccess` processing, and wouldn't produce this error.
- D is wrong: the request reached Apache and was answered with 403, so the network path works.
:::
</div>

<div class="q" markdown="1">

**Q2.** Nginx returns 502 and the error log says `connect() to 127.0.0.1:8000 failed (13: Permission denied) while connecting to upstream`. `curl http://127.0.0.1:8000` as root works. What is the most likely cause and fix?

A. The app is down; restart it
B. SELinux blocks `httpd_t` from connecting to port 8000; `setsebool -P httpd_can_network_connect 1`
C. Nginx's `worker_connections` is too low
D. The firewall blocks port 8000; open it with firewall-cmd

:::answer
**Answer: B.** Permission denied (errno 13) on a TCP connect from Nginx, while the backend answers your shell, is the classic SELinux proxy denial. Confirm with `ausearch -m AVC -c nginx | audit2why`.

- A is wrong: the app answers `curl`, and a down app gives `111: Connection refused`.
- C is wrong: that causes `worker_connections are not enough` errors, not EACCES.
- D is wrong: firewalld doesn't filter loopback traffic, and a firewall would give refused or timeout, not permission denied.
:::
</div>

<div class="q" markdown="1">

**Q3.** A Gunicorn app listens on `/run/myapp/myapp.sock`. Nginx logs `(13: Permission denied)` for that socket. The socket is `srwxr-xr-x myapp myapp` and no AVCs are logged. What is the fix?

A. Run Nginx as root
B. `chmod 777 /run/myapp` and leave it
C. Run the service with `Group=nginx` and `--umask 007` so the socket is group-writable by nginx
D. Switch the socket to `/home/myapp/myapp.sock`

:::answer
**Answer: C.** Connecting to a unix socket needs write permission on the socket file. Making the nginx group own it with mode `770` gives exactly the access needed, and it survives restarts because systemd and Gunicorn recreate it the same way.

- A is wrong: running the web server's workers as root is a serious security regression.
- B is wrong: the directory mode isn't the problem (it's already traversable), and manual changes in `/run` vanish at reboot and restart.
- D is wrong: home directories are usually `0700` and labeled `user_home_t`, which adds both a Unix and an SELinux problem.
:::
</div>

<div class="q" markdown="1">

**Q4.** Users get 504 on `/reports/export` after exactly 60 seconds. Other pages are fast. What should you investigate first?

A. Why that request takes longer than 60 seconds in the app (slow query, external call) using the app log and the Nginx `upstream timed out` line
B. Increase `proxy_read_timeout` to 3600s
C. Restart Nginx
D. Renew the TLS certificate

:::answer
**Answer: A.** 60 seconds matches `proxy_read_timeout`'s default; the backend accepted the connection but didn't answer in time. Find out why that endpoint is slow; long-running work should move to a background job.

- B is wrong as a first step: it hides the problem, and the LB or Gunicorn's worker timeout will cut the request anyway.
- C is wrong: a restart doesn't change backend speed.
- D is wrong: TLS problems fail at the handshake, not after 60 seconds.
:::
</div>

<div class="q" markdown="1">

**Q5.** Every access log line on your Apache servers shows `10.0.0.5`, the load balancer, as the client. Security needs real client IPs. Which change is correct?

A. `RemoteIPHeader X-Forwarded-For`, `RemoteIPInternalProxy 10.0.0.5`, and use `%a` in the `LogFormat`
B. `RemoteIPHeader X-Forwarded-For` and `RemoteIPTrustedProxy 0.0.0.0/0`
C. Turn on `HostnameLookups On`
D. Ask the LB to stop sending `X-Forwarded-For`

:::answer
**Answer: A.** mod_remoteip replaces the client IP with the value from `X-Forwarded-For`, but only for connections from the trusted proxy, and `%a` logs that IP.

- B is wrong: trusting every source lets any client spoof its IP and bypass IP-based rules.
- C is wrong: reverse DNS of the LB's IP still identifies the LB, and lookups slow every request.
- D is wrong: that header is the only place the real client IP exists.
:::
</div>

<div class="q" markdown="1">

**Q6.** certbot's logs show a successful renewal 25 days ago, but browsers report the certificate expired today. What is the most likely cause?

A. The certificate chain is missing
B. The web server was never reloaded after renewal, so it still serves the old certificate from memory
C. SELinux is blocking certbot
D. Port 80 is closed

:::answer
**Answer: B.** Apache and Nginx load certificates at startup or reload. Without a deploy hook such as `systemctl reload nginx`, the renewed files sit on disk unused. Check with `openssl s_client ... | openssl x509 -noout -dates`.

- A is wrong: a missing chain causes trust errors, not expiry, and it would have shown up before.
- C is wrong: renewal succeeded, so certbot wasn't blocked.
- D is wrong: a closed port 80 would make **renewal** fail, and the logs say it succeeded.
:::
</div>

<div class="q" markdown="1">

**Q7.** After someone copied a new private key from their home directory with `mv`, Nginx fails to start with `cannot load certificate key ... Permission denied`. The file is `0600 root:root`. What is the fix?

A. `chmod 644` on the key
B. `restorecon -v` on the key file (it kept `user_home_t`; the directory's rule gives `cert_t`)
C. `setenforce 0`
D. Move the key into `/var/www/html`

:::answer
**Answer: B.** `mv` preserves the SELinux label, so the key is `user_home_t`, which `httpd_t` can't read. `restorecon` applies the policy's label for that path (`cert_t` under `/etc/pki/tls`).

- A is wrong: a world-readable private key is a security issue, and Unix permissions weren't the problem (Nginx's master runs as root).
- C is wrong: disabling enforcement is not a fix.
- D is wrong: putting a private key in the document root could publish it to the internet.
:::
</div>

<div class="q" markdown="1">

**Q8.** A Python service starts fine from your shell but fails under systemd with `KeyError: 'DATABASE_URL'`. Which check is most useful first?

A. `systemctl show myapp -p Environment -p EnvironmentFiles` and the unit's `EnvironmentFile=` path
B. Reinstall Python
C. `setsebool -P httpd_can_network_connect_db 1`
D. Add `export DATABASE_URL=...` to root's `.bashrc`

:::answer
**Answer: A.** Your shell has the variable from your profile; systemd services only get what the unit defines. Put it in a `0600` `EnvironmentFile`, then `daemon-reload` and restart.

- B is wrong: Python works, and the error is a missing variable.
- C is wrong: the app hasn't attempted a connection; it can't even read the URL.
- D is wrong: systemd doesn't read `.bashrc`.
:::
</div>

<div class="q" markdown="1">

**Q9.** A PHP app on Apache can't connect to PostgreSQL on another host and logs `Permission denied`. From the app server's shell, `psql -h 10.0.0.20 -U myapp mydb` connects fine. What do you check?

A. `pg_hba.conf` on the database server
B. `getsebool httpd_can_network_connect_db` on the app server
C. firewalld on the database server
D. `listen_addresses` on the database server

:::answer
**Answer: B.** The shell connection proves the network, firewall, listen address and HBA are fine. Only the PHP process (running in `httpd_t`) is blocked, which is the SELinux boolean `httpd_can_network_connect_db`.

- A, C and D are wrong: each would also break the `psql` test from the same host.
:::
</div>

<div class="q" markdown="1">

**Q10.** You need every request for `/download/app.zip` between 3 and 5 November from Nginx logs that are partly gzipped (`access.log-20261104.gz`) and partly plain. Which approach is correct?

A. `grep app.zip /var/log/nginx/access.log`
B. `zcat -f /var/log/nginx/access.log-* /var/log/nginx/access.log | awk` with an exact match on `$7` and a date-key filter on `$4`
C. `gunzip` all files in `/var/log/nginx` then grep
D. `journalctl -u nginx --since 2026-11-03`

:::answer
**Answer: B.** `zcat -f` reads compressed and plain files in one stream, `$7 == "/download/app.zip"` avoids partial matches, and filtering on the timestamp (not on file names) handles dateext's one-day offset.

- A is wrong: it reads only the current file and matches substrings.
- C is wrong: decompressing in place under `/var/log` wastes disk and interferes with logrotate.
- D is wrong: Nginx access logs go to files, not the journal, by default.
:::
</div>

<div class="q" markdown="1">

**Q11.** After a release, the app returns 500 because it can't write to `/opt/myapp/cache`. `ls -l` shows the changed files are owned by root. The deploy was done with `sudo git pull`. What is the right long-term fix?

A. `chmod -R 777 /opt/myapp`
B. Run the deploy as the app user (`sudo -u myapp git -C /opt/myapp pull`), and fix ownership once with `chown -R`
C. Run the app as root
D. Set `git config --global safe.directory '*'`

:::answer
**Answer: B.** Files should be created by the user that owns the tree. Deploying as that user keeps ownership consistent, and a one-off `chown -R` repairs the damage.

- A is wrong: world-writable code is a security hole.
- C is wrong: running the app as root turns any app bug into full system compromise.
- D is wrong: it only silences git's ownership warning; it doesn't fix the file owners.
:::
</div>

<div class="q" markdown="1">

**Q12.** After logrotate runs, your Java app's `/var/log/myapp/app.log` stays empty and `app.log-20261125` keeps growing. The app can't reopen logs on a signal. What change fixes this with the least code change?

A. Add `copytruncate` to the logrotate stanza, accepting that a few lines may be lost at rotation time
B. Add `postrotate systemctl reload myapp`
C. Delete the rotated file every night
D. Disable logrotate for this app

:::answer
**Answer: A.** The app keeps writing to the renamed file through its open descriptor. `copytruncate` copies and truncates the original in place, so the app keeps writing to the same file. Lines written during the copy can be lost, and the app should open the file in append mode.

- B is wrong: the app doesn't support reopening, so a reload signal won't help (and a restart would interrupt service).
- C is wrong: deleting an open file doesn't free space (`lsof +L1`) and the app keeps writing to it.
- D is wrong: the log would then grow until the disk fills.
:::
</div>
