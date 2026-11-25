<div class="cover" markdown="1">
<div class="mod">Module 04 · Networking Troubleshooting</div>

# Networking Troubleshooting: From the Link Light to the TLS Handshake

<p>A layered, evidence-driven method for "it can't connect" tickets on RHEL 9: interfaces, routes, NetworkManager, DNS, listening sockets, firewalld, SELinux port labels, cloud security groups and TLS certificates, with four break-fix labs.</p>
</div>

## What you'll learn

- A **layered approach** that turns "the network is broken" into one specific failing layer: link, IP, route, DNS, port, application, TLS
- Reading the state of a host with **`ip addr`, `ip link`, `ip route`** and **`ip route get`**
- Managing interfaces with **NetworkManager**: `nmcli` connections, static IPs, DNS servers, `nmtui`, and why hand-edits to **`/etc/resolv.conf`** disappear
- Name resolution end to end: **`/etc/hosts`**, `nsswitch.conf`, **`dig`**, **`nslookup`** and **`getent hosts`**, and why they can disagree
- Path testing with **`ping`, `tracepath`** and **`mtr`**, including MTU problems
- Sockets and connectivity: **`ss -tulpn`** (127.0.0.1 versus 0.0.0.0), **`curl -v`**, **`nc`/`ncat`**, and a first look at **`tcpdump`**
- TLS diagnosis with **`openssl s_client`**: expiry dates, incomplete chains, SNI
- **firewalld** zones, services, ports, runtime versus `--permanent`, rich rules, and the nftables view underneath; a note on **ufw**
- **SELinux and ports**: `semanage port -l/-a/-m`, `http_port_t`, `ssh_port_t`, `name_bind` and `name_connect` denials
- **Cloud security groups and NSGs** as the cause you cannot see from inside the VM
- Four break-fix labs: app works locally but not remotely, DNS resolution fails, nginx on a non-standard port blocked by SELinux, and an expired or incomplete TLS certificate

**Why it matters in production:** most "the app is down" pages are really "the app cannot be reached", and the reachability problem can sit in any of seven layers on two or three different machines. Engineers who guess (restart the service, flush the firewall, disable SELinux) waste the first hour of an outage and often create a security incident on top of it. Engineers who walk the layers in order usually find the cause in ten minutes and fix it without weakening the host.

## 1. The layered approach

When a connection fails, the error message the user reports is almost never the cause. "Site can't be reached", "timeout", "503" and "SSL error" can each come from several layers. The fix is to test the layers **in order, from the bottom up**, and stop at the first one that fails. Everything above a broken layer will also look broken, so fixing a higher layer first is wasted effort.

| # | Layer | Question you are answering | First command |
|---|---|---|---|
| 1 | **Link** | Is the interface up and does it have carrier? | `ip -br link` |
| 2 | **IP** | Does the interface have the right address and prefix? | `ip -br addr` |
| 3 | **Route** | Does the kernel know how to reach the destination, and via which gateway? | `ip route get <dest-ip>` |
| 4 | **DNS** | Does the name resolve to the address you expect? | `getent hosts <name>` and `dig <name>` |
| 5 | **Port** | Is something listening, on the right address, and is the path (firewall, SELinux, security group) open? | `ss -tulpn`, `nc -zv <ip> <port>` |
| 6 | **App** | Does the application answer correctly once connected? | `curl -v http://<ip>:<port>/` |
| 7 | **TLS** | Is the certificate valid, unexpired, for this name, with a complete chain? | `openssl s_client -connect <host>:443 -servername <host>` |

:::concept
**Test from both ends.** Most networking problems involve a client, a server and the path between them. Run the checks on the server first (is it listening? on which address?), then on the client (can I resolve it? can I reach the port?). A test that succeeds on the server with `curl localhost` and fails from the client tells you the problem is in the path or the bind address, not the application.
:::

:::story
Think of a parcel delivery. The van must start (link), the house must have a street number (IP), the driver needs a route to the street (routing), the address book must turn "Alex's office" into a street number (DNS), someone must be at the door and the gate must be unlocked (port, firewall, security group), the person must accept the parcel (application), and they must check the courier's ID badge (TLS). If the van will not start, there is no point in asking why nobody answered the door.
:::

### Read the error message as a clue

The client's error already narrows the layer. Learn these four by heart.

| Client sees | What happened on the wire | Usual cause |
|---|---|---|
| **Connection refused** | The SYN reached the host; the kernel replied with RST because nothing listens on that address and port | Service down, or bound to 127.0.0.1 only, or listening on a different port |
| **No route to host** (with a working ping) | An ICMP "administratively prohibited" reply came back | **firewalld** rejecting the port (its default zone target rejects with an ICMP error) |
| **Connection timed out** | The SYN got no answer at all | Packet silently **dropped**: cloud security group or NSG, a `drop` zone, an upstream firewall, wrong route, host down |
| **Name or service not known** / `Could not resolve host` | No connection attempted | DNS or `/etc/hosts` |
| **SSL certificate problem** | TCP worked, TLS handshake failed verification | Expired, wrong name, incomplete chain, untrusted CA |

:::tip
"Refused" is good news: it proves the whole path to the host works and the problem is on the server itself. "Timed out" means look **outside** the host first: security groups, upstream firewalls, routing.
:::

## 2. Link, IP and routing with `ip`

The `ip` command (package `iproute`) replaces the old `ifconfig`, `route` and `netstat` tools, which are not installed by default on RHEL 9. Use the `-br` (brief) flag for a fast overview and `-c` for colour.

### Link layer

```bash
ip -br link
```

```text
lo               UNKNOWN        00:00:00:00:00:00 <LOOPBACK,UP,LOWER_UP>
ens192           UP             00:50:56:aa:bb:cc <BROADCAST,MULTICAST,UP,LOWER_UP>
ens224           DOWN           00:50:56:aa:bb:dd <NO-CARRIER,BROADCAST,MULTICAST,UP>
```

How to read it: `UP` in the flags means the interface is administratively enabled; `LOWER_UP` means the physical or virtual link has carrier. `ens224` above is enabled but has `NO-CARRIER`: the cable is unplugged, the switch port is down, or in a VM the virtual NIC is disconnected in the hypervisor. No amount of IP configuration will fix that.

```bash
ip -s link show ens192          # packet, error and drop counters
ethtool ens192                  # speed, duplex, "Link detected: yes" (package ethtool)
```

Rising `errors` or `dropped` counters in `ip -s link` point to a bad cable, a duplex mismatch, or a NIC ring buffer overflowing under load.

### IP layer

```bash
ip -br addr
```

```text
lo               UNKNOWN        127.0.0.1/8 ::1/128
ens192           UP             10.0.0.20/24 fe80::250:56ff:feaa:bbcc/64
```

Check three things: the address is the one you expect, the **prefix** is right (a `/32` or `/16` where you expected `/24` breaks local delivery in subtle ways), and there is no duplicate address on the network (`arping -D -I ens192 10.0.0.20` from package `iputils` reports a duplicate if another host answers).

### Routing layer

```bash
ip route
```

```text
default via 10.0.0.1 dev ens192 proto static metric 100
10.0.0.0/24 dev ens192 proto kernel scope link src 10.0.0.20 metric 100
```

The most useful routing command is `ip route get`, which asks the kernel exactly which route, interface and source address it would use for a destination:

```bash
ip route get 192.0.2.50
```

```text
192.0.2.50 via 10.0.0.1 dev ens192 src 10.0.0.20 uid 0
    cache
```

On multi-homed hosts (a backup network, a storage network, a VPN interface) this is how you catch traffic leaving through the wrong interface with the wrong source address, which the remote firewall then drops.

:::warn
`ip addr add` and `ip route add` change the **running** kernel only. They vanish on reboot and NetworkManager may remove them the next time it reapplies the connection. Use them for testing; make permanent changes with `nmcli`.
:::

## 3. NetworkManager and `nmcli`

On RHEL 9 every interface is managed by **NetworkManager**. Connection profiles are stored as keyfiles in `/etc/NetworkManager/system-connections/*.nmconnection`. The legacy `ifcfg-*` files in `/etc/sysconfig/network-scripts/` are deprecated in RHEL 9 (still read if present, but new profiles are keyfiles). The `network` service from RHEL 7 is gone.

Two terms matter: a **device** is the actual interface (`ens192`); a **connection** is a profile of settings that can be applied to a device. A device can have several profiles, one active at a time. Profile names often differ from device names (for example a profile called `System eth0` or `Wired connection 1`), which trips people up in scripts.

```bash
nmcli device status
nmcli connection show
nmcli connection show --active
```

```text
DEVICE  TYPE      STATE                   CONNECTION
ens192  ethernet  connected               ens192
ens224  ethernet  disconnected            --
lo      loopback  connected (externally)  lo
```

### Inspect a connection

```bash
nmcli -f ipv4 connection show ens192     # profile settings (what you configured)
nmcli device show ens192                 # live state (what is applied now)
```

Compare the two when something looks wrong: the profile is what will be applied next time; the device view is what is running now.

### Set a static IP, gateway and DNS

```bash
nmcli connection modify ens192 \
  ipv4.method manual \
  ipv4.addresses 10.0.0.20/24 \
  ipv4.gateway 10.0.0.1 \
  ipv4.dns "10.0.0.2 10.0.0.3" \
  ipv4.dns-search example.com
nmcli connection up ens192
```

`nmcli connection modify` only edits the profile; `nmcli connection up` (re)applies it. Use `+ipv4.dns 10.0.0.4` to append and `-ipv4.dns 10.0.0.4` to remove a single value. Back to DHCP:

```bash
nmcli connection modify ens192 ipv4.method auto ipv4.addresses "" ipv4.gateway "" ipv4.dns ""
nmcli connection up ens192
```

:::warn
Running `nmcli connection up` on the interface you are SSH'd in over briefly reapplies the configuration. With a typo in the address or gateway you will lock yourself out. On production hosts, have console access (iLO, iDRAC, hypervisor console, cloud serial console) before you change the management interface, or schedule a rollback: `nmcli` supports a checkpoint with automatic rollback through `nmcli general reload` workflows only via the D-Bus API, so in practice keep a console open.
:::

If you edit a `.nmconnection` file by hand, tell NetworkManager to re-read it:

```bash
chmod 600 /etc/NetworkManager/system-connections/ens192.nmconnection   # NM ignores keyfiles readable by others
nmcli connection reload
nmcli connection up ens192
```

### nmtui

`nmtui` (package `NetworkManager-tui`) is a text menu for editing connections, setting the hostname and activating profiles. It is handy on a console with no copy-paste, and it writes the same keyfiles as `nmcli`. For anything repeatable or scripted (Ansible, runbooks), prefer `nmcli` or the `rhel-system-roles.network` role.

:::tip
On Ubuntu servers, interfaces are usually configured with **netplan** (`/etc/netplan/*.yaml`, applied with `netplan apply` or `netplan try`, which rolls back automatically if you do not confirm). Netplan renders to systemd-networkd on servers and to NetworkManager on desktops.
:::

## 4. Name resolution: `/etc/hosts`, `/etc/resolv.conf` and the tools

### How a name is resolved on RHEL

Applications call the C library (glibc) resolver. glibc reads **`/etc/nsswitch.conf`** to decide the order of sources:

```text
hosts:      files dns myhostname
```

`files` means **`/etc/hosts`** first, then `dns` means the servers listed in **`/etc/resolv.conf`**. (On RHEL 9 `nsswitch.conf` is managed by `authselect`; change it with an authselect profile, not by hand.)

```text
# /etc/hosts
127.0.0.1   localhost localhost.localdomain
::1         localhost localhost.localdomain
10.0.0.30   db01.example.com db01
```

```text
# /etc/resolv.conf
# Generated by NetworkManager
search example.com
nameserver 10.0.0.2
nameserver 10.0.0.3
```

### Who owns `/etc/resolv.conf`

On RHEL 9, **NetworkManager writes `/etc/resolv.conf`** from the DNS settings of the active connections (from DHCP or from `ipv4.dns`). The comment `# Generated by NetworkManager` on the first line tells you so. Any hand edit is overwritten the next time a connection comes up, a DHCP lease renews, or NetworkManager restarts, which can be hours later, long after the change "worked".

The right ways to control DNS:

| Goal | Command |
|---|---|
| Static DNS servers on a static connection | `nmcli con mod ens192 ipv4.dns "10.0.0.2 10.0.0.3"` |
| Use your own DNS servers and ignore the ones DHCP offers | `nmcli con mod ens192 ipv4.ignore-auto-dns yes ipv4.dns "10.0.0.2"` |
| Stop NetworkManager touching `resolv.conf` at all (config management owns it) | Create `/etc/NetworkManager/conf.d/90-dns-none.conf` with `[main]` and `dns=none`, then `systemctl reload NetworkManager` |

```bash
nmcli dev show ens192 | grep -i dns
```

```text
IP4.DNS[1]:                             10.0.0.2
IP4.DNS[2]:                             10.0.0.3
```

:::tip
**Ubuntu and systemd-resolved:** on Ubuntu, `/etc/resolv.conf` is a symlink to a stub file that contains `nameserver 127.0.0.53`, a local caching resolver run by `systemd-resolved`. The real upstream servers are shown by `resolvectl status`. Seeing 127.0.0.53 there is normal, not a misconfiguration. RHEL 9 does not enable systemd-resolved by default.
:::

### The tools, and why they disagree

| Tool | Uses `/etc/hosts`? | Uses nsswitch? | Best for |
|---|---|---|---|
| **`getent hosts name`** | Yes | Yes | "What will my application actually get?" |
| **`dig name`** | No | No | Querying DNS servers directly, seeing TTLs, flags and which server answered |
| **`nslookup name`** | No | No | Quick checks, familiar from Windows; less detail than dig |
| `host name` | No | No | Short one-line answers |
| `ping name` | Yes (via glibc) | Yes | Not a DNS tool, but shows what the resolver returned |

`dig`, `nslookup` and `host` come from package `bind-utils`; `getent` is part of glibc and always present.

```bash
getent hosts app.example.com
dig app.example.com
dig @10.0.0.2 app.example.com +short     # ask one specific server
dig -x 10.0.0.40 +short                  # reverse (PTR) lookup
dig app.example.com +trace               # walk from the root servers down
```

```text
; <<>> DiG 9.16.23-RH <<>> app.example.com
;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 31458
;; flags: qr rd ra; QUERY: 1, ANSWER: 1, AUTHORITY: 0, ADDITIONAL: 1

;; ANSWER SECTION:
app.example.com.        300     IN      A       10.0.0.40

;; Query time: 2 msec
;; SERVER: 10.0.0.2#53(10.0.0.2)
;; WHEN: Wed Nov 25 10:12:41 UTC 2026
;; MSG SIZE  rcvd: 60
```

How to read it: **`status`** is the key field. `NOERROR` with an answer is success. `NXDOMAIN` means the server says the name does not exist (typo, wrong search domain, missing record). `SERVFAIL` means the server could not get an answer (often a broken upstream or DNSSEC validation failure). `;; connection timed out; no servers could be reached` means no DNS server answered at all, which is a network or `resolv.conf` problem, not a record problem. **`SERVER`** shows which resolver actually answered. The number after the name (`300`) is the TTL in seconds: how long caches may keep the answer.

:::concept
If `getent hosts` and `dig` return **different** addresses, there is almost always a stale entry in `/etc/hosts`. Someone pinned the name during a migration and forgot to remove it. The application uses the `/etc/hosts` answer; your `dig` check uses DNS and looks fine. Always test with `getent` when the question is "why does the app connect to the wrong place?".
:::

:::prod
**Symptom:** after a database failover to a new IP, one of six application servers kept connecting to the old primary and threw read-only errors. DNS had been updated, and `dig db.example.com` on that server showed the new IP.

**What the engineer saw:** `getent hosts db.example.com` returned the **old** address. `grep db /etc/hosts` showed a line added by hand two years earlier during a data-centre move.

**Root cause:** `/etc/hosts` is consulted before DNS (`hosts: files dns`), so the stale line overrode the correct DNS record on that one server.

**Fix:** removed the line, restarted the application to drop pooled connections.

**Prevention:** `/etc/hosts` is now managed by Ansible with a template containing only localhost entries, and the compliance scan flags any extra lines.
:::

## 5. Path testing: `ping`, `tracepath`, `mtr`

### ping

```bash
ping -c 4 10.0.0.1                 # gateway first
ping -c 4 192.0.2.50               # then the destination
```

```text
64 bytes from 10.0.0.1: icmp_seq=1 ttl=64 time=0.412 ms
--- 10.0.0.1 ping statistics ---
4 packets transmitted, 4 received, 0% packet loss, time 3060ms
```

Ping tells you ICMP echo works, nothing more. Many cloud networks and corporate firewalls block ICMP, so **a failed ping does not prove the host is down**, and a working ping does not prove the TCP port is open. Use it as one data point.

### MTU problems

A classic production trap: SSH connects but hangs when you run `ls` in a big directory, or HTTPS hangs after the TLS ClientHello, while small requests work. Large packets are being dropped somewhere on the path (VPN tunnels, overlay networks, jumbo-frame mismatches) and the ICMP "fragmentation needed" messages that should fix it are blocked. Test with the "don't fragment" flag:

```bash
ping -c 3 -M do -s 1472 192.0.2.50     # 1472 + 28 bytes of headers = 1500
ping -c 3 -M do -s 1372 192.0.2.50     # smaller: does this one work?
```

`ping: local error: message too long, mtu=1400` or silent loss at 1472 but success at a smaller size points to an MTU problem on the path. `tracepath` discovers the path MTU for you:

### tracepath and mtr

```bash
tracepath -n 192.0.2.50            # in iputils, no root needed, reports pmtu
mtr -n --report -c 20 192.0.2.50   # package mtr; combines ping and traceroute
```

```text
HOST: web01                     Loss%   Snt   Last   Avg  Best  Wrst StDev
  1.|-- 10.0.0.1                 0.0%    20    0.4   0.5   0.3   0.9   0.1
  2.|-- 198.51.100.1             0.0%    20    1.2   1.4   1.0   2.8   0.4
  3.|-- ???                     100.0    20    0.0   0.0   0.0   0.0   0.0
  4.|-- 192.0.2.50               0.0%    20    9.8  10.1   9.6  12.0   0.5
```

How to read it: loss at an intermediate hop that does **not** continue to the final hop (hop 3 above) is just a router that does not answer probes; ignore it. Loss that starts at one hop and **continues to every hop after it** is real loss at or before that hop. `mtr --tcp -P 443` sends TCP SYNs instead of ICMP, which gets through firewalls that drop ICMP.

## 6. Ports and sockets: `ss`, `curl`, `nc`

### `ss -tulpn`: who is listening, and where

```bash
ss -tulpn
```

```text
Netid State   Recv-Q Send-Q  Local Address:Port   Peer Address:Port Process
udp   UNCONN  0      0           127.0.0.1:323         0.0.0.0:*     users:(("chronyd",pid=702,fd=5))
tcp   LISTEN  0      128           0.0.0.0:22          0.0.0.0:*     users:(("sshd",pid=812,fd=3))
tcp   LISTEN  0      511           0.0.0.0:80          0.0.0.0:*     users:(("nginx",pid=1204,fd=6))
tcp   LISTEN  0      5           127.0.0.1:8000        0.0.0.0:*     users:(("python3",pid=2211,fd=3))
tcp   LISTEN  0      4096        127.0.0.1:5432        0.0.0.0:*     users:(("postgres",pid=990,fd=7))
tcp   LISTEN  0      128              [::]:22             [::]:*     users:(("sshd",pid=812,fd=4))
```

Flags: `-t` TCP, `-u` UDP, `-l` listening sockets only, `-p` show the process (needs root to see other users' processes), `-n` numeric ports and addresses (no slow name lookups).

The **Local Address** column is the most important thing on this screen:

| Local Address | Reachable from | Typical meaning |
|---|---|---|
| `0.0.0.0:80` | Any IPv4 address on any interface | Public service, as intended for a web server |
| `[::]:80` or `*:80` | Any IPv6 address, and usually IPv4 too (dual-stack socket) | Same, for IPv6 |
| `127.0.0.1:8000` | **Only this host** | Development default, or deliberately local (database, admin port) |
| `10.0.0.20:8080` | Only connections addressed to that one IP | Bound to one interface on purpose |

A service bound to `127.0.0.1` answers `curl http://localhost:8000` perfectly on the server and gives **Connection refused** from every other host. No firewall rule can fix that; the application's bind or listen setting must change (`--bind 0.0.0.0`, `listen 0.0.0.0:8000`, `bind-address = 0.0.0.0`, `listen_addresses = '*'`). Often binding to localhost is correct for security, and the real fix is a reverse proxy in front.

`Recv-Q` on a `LISTEN` socket is the number of connections waiting in the accept queue; `Send-Q` is the maximum backlog. A Recv-Q that stays near Send-Q means the application is not accepting connections fast enough (overloaded or hung), and new clients will see timeouts.

Other useful `ss` forms:

```bash
ss -tn state established '( dport = :5432 )'   # established connections to PostgreSQL
ss -s                                          # summary counts (many TIME-WAIT? many SYN-RECV?)
ss -tlpn sport = :443                          # who owns port 443
```

### `curl -v`: the application layer, with the TCP and TLS steps visible

```bash
curl -v http://10.0.0.20:8000/
```

```text
*   Trying 10.0.0.20:8000...
* connect to 10.0.0.20 port 8000 failed: Connection refused
* Failed to connect to 10.0.0.20 port 8000: Connection refused
curl: (7) Failed to connect to 10.0.0.20 port 8000: Connection refused
```

The `*` lines are curl's own commentary, `>` lines are what it sent, `<` lines are what came back. Exact wording varies between curl versions. Useful flags:

| Flag | Use |
|---|---|
| `-v` | Show connect, TLS and HTTP headers |
| `-I` | HEAD request: headers only |
| `-k` | Skip certificate verification (diagnostic only; never in scripts or health checks) |
| `--resolve app.example.com:443:10.0.0.40` | Force a name to an IP without touching DNS or `/etc/hosts`; SNI and Host header still use the name |
| `--connect-timeout 5` | Fail fast instead of waiting minutes |
| `-H 'Host: app.example.com'` | Test a virtual host by IP |
| `-w '%{http_code} %{time_connect} %{time_total}\n' -o /dev/null -s` | Timing breakdown for slow requests |

### `nc` / `ncat`: is the TCP port open?

RHEL ships **ncat** (package `nmap-ncat`, command `nc`). It is the quickest way to test a port that does not speak HTTP (databases, SMTP, LDAP, custom protocols).

```bash
nc -zv 10.0.0.30 5432               # -z connect then close, -v verbose
```

```text
Ncat: Version 7.92 ( https://nmap.org/ncat )
Ncat: Connected to 10.0.0.30:5432.
Ncat: 0 bytes sent, 0 bytes received in 0.01 seconds.
```

Or `Ncat: Connection refused.`, `Ncat: No route to host.`, `Ncat: TIMEOUT.`; read them with the table in section 1. You can also start a throwaway listener to test the path independently of the application: `nc -l 9999` on the server and `nc -zv server 9999` from the client (open the port in firewalld first, and remember SELinux does not restrict an unconfined shell user, so this tests path and firewall only).

When no tools are installed at all, bash itself can test a port:

```bash
timeout 3 bash -c '</dev/tcp/10.0.0.30/5432' && echo open || echo closed
```

### `tcpdump`: the final arbiter

When two teams disagree about whether packets arrive, capture them. `tcpdump` (package `tcpdump`) shows the truth on the wire at this host.

```bash
tcpdump -i ens192 -nn 'tcp port 8000'
tcpdump -i ens192 -nn 'host 10.0.0.55 and tcp port 443' -c 50
tcpdump -i any -nn 'udp port 53'                          # watch DNS queries
tcpdump -i ens192 -nn -w /tmp/cap.pcap 'tcp port 443'     # save for Wireshark
```

```text
10:20:01.112 IP 10.0.0.55.51744 > 10.0.0.20.8000: Flags [S], seq 1849…, length 0
10:20:01.112 IP 10.0.0.20.8000 > 10.0.0.55.51744: Flags [R.], seq 0, ack 1849…, length 0
```

How to read it: `[S]` is a SYN (connection attempt), `[S.]` is SYN-ACK (accepted), `[R.]` is a reset (refused), `[.]` is an ACK, `[P.]` carries data, `[F.]` closes. The pattern above (SYN in, RST out) is "nothing listening". **SYNs arriving with no reply at all** means the host received them but something local (firewalld, a full backlog) discarded them; tcpdump sees packets **before** the firewall's input rules on the receive path. **No SYNs arriving at all** means the drop is upstream: a security group, an NSG, a network firewall or a routing problem. That single observation settles many arguments between server and network teams.

:::warn
Packet captures can contain passwords, session cookies and personal data. Capture only what you need (filter by host and port, limit with `-c`), store `.pcap` files somewhere access-controlled, and delete them when the incident is closed.
:::

## 7. Host firewalls: firewalld, nftables, ufw

### firewalld concepts

**firewalld** is the default host firewall on RHEL 9; on RHEL 9 it programs **nftables** in the kernel. It organises rules into **zones**. Each network interface or source address range is bound to one zone, and the zone says which **services** (named port sets such as `ssh`, `http`, `https`) and **ports** are allowed in.

| Zone | Default behaviour |
|---|---|
| `public` | Default zone on RHEL. Allows `ssh`, `dhcpv6-client`, `cockpit`; rejects the rest |
| `internal`, `home`, `work` | More services allowed; for trusted networks |
| `trusted` | Accept everything |
| `drop` | Silently drop everything incoming (clients see **timeouts**) |
| `block` | Reject everything incoming with an ICMP error |

```bash
firewall-cmd --state
firewall-cmd --get-default-zone
firewall-cmd --get-active-zones
firewall-cmd --list-all                     # default zone
firewall-cmd --zone=internal --list-all
```

```text
public (active)
  target: default
  icmp-block-inversion: no
  interfaces: ens192
  sources:
  services: cockpit dhcpv6-client ssh
  ports:
  protocols:
  forward: yes
  masquerade: no
  forward-ports:
  source-ports:
  icmp-blocks:
  rich rules:
```

How to read it: this host allows only SSH, Cockpit and DHCPv6 from the network. A web server on port 80 or an app on 8000 is blocked. `target: default` behaves like reject for unmatched traffic, which is why clients see "No route to host" rather than a timeout.

### Runtime versus permanent

firewalld keeps two configurations: **runtime** (what the kernel is enforcing now) and **permanent** (what is saved on disk and loaded at boot or reload).

| Command | Runtime | Permanent | Typical use |
|---|---|---|---|
| `firewall-cmd --add-service=http` | Changed | Not changed | Test now; lost on reload or reboot |
| `firewall-cmd --permanent --add-service=http` | **Not changed** | Changed | Saved, but not active until `--reload` |
| `firewall-cmd --reload` | Replaced by permanent | Unchanged | Apply saved config; **discards runtime-only changes** |
| `firewall-cmd --runtime-to-permanent` | Unchanged | Replaced by runtime | Save what you tested |

The production-safe pattern is to make the change both ways, or permanent plus reload:

```bash
firewall-cmd --permanent --add-service=http
firewall-cmd --permanent --add-port=8000/tcp
firewall-cmd --reload
firewall-cmd --list-services; firewall-cmd --list-ports
```

:::prod
**Symptom:** after a routine kernel patch and reboot, the monitoring endpoint on TCP 9100 stopped answering from the monitoring server on dozens of hosts. Nothing in the application had changed.

**What the engineer saw:** `ss -tulpn` showed the exporter listening on `0.0.0.0:9100`. From the monitoring server, `nc -zv` returned "No route to host". `firewall-cmd --list-ports` was empty; `firewall-cmd --permanent --list-ports` was also empty.

**Root cause:** months earlier someone opened the port with `firewall-cmd --add-port=9100/tcp` without `--permanent`. It worked until the first reboot.

**Fix:** `firewall-cmd --permanent --zone=public --add-rich-rule=...` limited to the monitoring subnet, then `--reload`.

**Prevention:** firewall rules are now set through Ansible (`ansible.posix.firewalld` with `permanent: true` and `immediate: true`), and a post-reboot smoke test checks every monitored port.
:::

### Rich rules: allow a port from one subnet only

Opening a port to the whole world is rarely what you want for a database or an admin interface. Rich rules add a source condition:

```bash
firewall-cmd --permanent --zone=public --add-rich-rule='rule family="ipv4" source address="10.0.0.0/24" port port="5432" protocol="tcp" accept'
firewall-cmd --reload
firewall-cmd --list-rich-rules
```

Alternatively bind a source range to a more permissive zone: `firewall-cmd --permanent --zone=internal --add-source=10.0.0.0/24` and add the service to `internal`. Traffic from that range is then evaluated by `internal`, everything else by `public`.

To remove anything, repeat the command with `--remove-` instead of `--add-`.

### The nftables and iptables view

```bash
nft list ruleset | less                    # everything the kernel enforces
nft list table inet firewalld              # just firewalld's rules
```

On RHEL 9 the `iptables` command, if installed, is `iptables-nft`, a compatibility layer that writes nftables rules. `iptables -L -n -v` shows only rules created through that layer, **not** firewalld's native nftables rules, so an empty `iptables -L` does not mean "no firewall". Do not mix hand-written `iptables` or `nft` rules with firewalld: firewalld's reload can conflict with or wipe them. If you need raw rules on a firewalld host, use `firewall-cmd --direct` (deprecated) or, better, a policy or rich rule.

:::warn
`systemctl stop firewalld` is a fair **10-second diagnostic** ("does it work with the firewall off?") on a lab or isolated host, followed immediately by `systemctl start firewalld`. It is never the fix. Find the missing service or port and add it.
:::

### ufw on Ubuntu

Ubuntu uses **ufw** (Uncomplicated Firewall), which is inactive by default on many Ubuntu Server images:

```bash
ufw status verbose
ufw allow 8000/tcp
ufw allow from 10.0.0.0/24 to any port 5432 proto tcp
```

ufw has no runtime/permanent split: rules apply immediately and persist.

## 8. SELinux and network ports

SELinux controls not only which files a service may read but also **which ports it may bind to and connect to**. Every TCP and UDP port has a type label; every confined service domain is allowed to bind (`name_bind`) or connect (`name_connect`) only to specific port types.

```bash
semanage port -l | grep -E '^(http_port_t|ssh_port_t|http_cache_port_t)'
```

```text
http_cache_port_t              tcp      8080, 8118, 8123, 10001-10010
http_port_t                    tcp      80, 81, 443, 488, 8008, 8009, 8443, 9000
ssh_port_t                     tcp      22
```

(`semanage` is in package `policycoreutils-python-utils`. Exact port lists can differ slightly between policy versions; always check on your host.)

So nginx or httpd (domain `httpd_t`) can listen on 80, 443, 8443 and the other `http_port_t` ports. Configure nginx to listen on **8090** and it fails to start even as root, because 8090 carries a different type (unlabeled high ports are `unreserved_port_t`) that `httpd_t` may not bind. The same applies to `sshd` (domain `sshd_t`) on port 2222.

### What the failure looks like

```bash
systemctl status nginx
journalctl -u nginx -b --no-pager | tail
```

```text
nginx: [emerg] bind() to 0.0.0.0:8090 failed (13: Permission denied)
```

"Permission denied" while running as root is a strong hint that SELinux, not file permissions, is involved. Confirm with the audit log:

```bash
ausearch -m AVC -ts recent
```

```text
type=AVC msg=audit(1764065000.123:412): avc:  denied  { name_bind } for  pid=3120 comm="nginx" src=8090 scontext=system_u:system_r:httpd_t:s0 tcontext=system_u:object_r:unreserved_port_t:s0 tclass=tcp_socket permissive=0
```

How to read it: the **action** was `name_bind` (bind a listening socket), the **process** was nginx running as `httpd_t`, the **target** was port 8090 labelled `unreserved_port_t`, and `permissive=0` means it was actually blocked.

```bash
ausearch -m AVC -ts recent | audit2why
```

`audit2why` explains that the denial can be allowed by adding the port to a type the domain may bind; `sealert -a /var/log/audit/audit.log` (package `setroubleshoot-server`) gives the same advice in plain language, including the exact `semanage port` command.

### The fix: label the port

```bash
semanage port -a -t http_port_t -p tcp 8090        # add a new label
semanage port -l -C                                # list local customisations only
```

If the port already has a type (for example 8080 is `http_cache_port_t`, or a port used by another product), `-a` fails with "Port tcp/NNNN already defined". Use `-m` to modify it instead, after checking you are not breaking another service that relies on the existing label:

```bash
semanage port -m -t http_port_t -p tcp 8080
```

For sshd on a non-standard port:

```bash
semanage port -a -t ssh_port_t -p tcp 2222
```

The label is persistent and survives reboots and policy updates. Remove it with `semanage port -d -t http_port_t -p tcp 8090`.

:::warn
Never "fix" a port denial with `setenforce 0` or by setting `SELINUX=disabled`. `setenforce 0` is acceptable only as a **temporary diagnostic** on a non-production host to confirm SELinux is the cause, followed immediately by `setenforce 1`. The audit log already tells you the cause, so you rarely need even that. Likewise, avoid `audit2allow -M` for port problems: a custom module that lets `httpd_t` bind any unreserved port is far broader than labelling the one port you need.
:::

### Outbound connections: `name_connect` and booleans

The reverse problem appears with reverse proxies. nginx in front of an application on `127.0.0.1:3000` returns **502 Bad Gateway**, and the nginx error log says:

```text
connect() to 127.0.0.1:3000 failed (13: Permission denied) while connecting to upstream
```

The AVC shows `denied { name_connect } ... scontext=...httpd_t ... tclass=tcp_socket`. By default `httpd_t` may connect only to a small set of port types. The fix is a boolean, not a policy module:

```bash
getsebool httpd_can_network_connect
setsebool -P httpd_can_network_connect on      # -P makes it persistent
```

If the upstream is a database, the narrower `httpd_can_network_connect_db` boolean may be enough. Alternatively label the upstream port as `http_port_t` so the proxy is allowed to connect to it without opening all outbound connections.

:::tip
On Ubuntu and Debian the mandatory access control system is **AppArmor**, whose profiles mostly restrict file paths and capabilities rather than port numbers, so moving nginx to another port usually just works there. If you see "Permission denied" for binding a port below 1024 on Ubuntu, it is the classic non-root privileged-port rule, not AppArmor.
:::

## 9. TLS troubleshooting with `openssl s_client`

Once TCP works, TLS adds its own failures: the certificate is expired, does not match the name, is signed by a CA the client does not trust, or the server does not send the intermediate certificates the client needs to build a chain to a trusted root.

### Connect and look

```bash
openssl s_client -connect app.example.com:443 -servername app.example.com </dev/null
```

`-servername` sends **SNI** (Server Name Indication). A server hosting several sites on one IP picks the certificate by SNI; without it you may get the default site's certificate and chase a "wrong certificate" problem that does not exist for real browsers. `</dev/null` makes `s_client` exit after the handshake instead of waiting for input.

Trimmed output of a healthy server:

```text
CONNECTED(00000003)
depth=2 CN = Example Root CA
verify return:1
depth=1 CN = Example Issuing CA
verify return:1
depth=0 CN = app.example.com
verify return:1
---
Certificate chain
 0 s:CN = app.example.com
   i:CN = Example Issuing CA
 1 s:CN = Example Issuing CA
   i:CN = Example Root CA
---
...
    Verify return code: 0 (ok)
```

How to read it: the **Certificate chain** section lists what the server sent. Certificate 0 is the leaf (`s:` subject, `i:` issuer); each next one should be the issuer of the previous. The server should send the leaf and all intermediates, but not normally the root (clients already have it). The last line, **Verify return code**, is the verdict:

| Verify return code | Meaning | Fix |
|---|---|---|
| `0 (ok)` | Chain valid and trusted | Nothing (still check the name and dates) |
| `10 (certificate has expired)` | A certificate in the chain is past `notAfter` | Renew and deploy the new certificate, reload the service |
| `20 (unable to get local issuer certificate)` | Client cannot find the issuer: intermediate missing or CA not trusted | Serve the full chain, or add the private CA to the client trust store |
| `21 (unable to verify the first certificate)` | Server sent only the leaf; intermediate missing | Configure the full chain file (leaf followed by intermediates) |
| `18` / `19` (self-signed certificate / in chain) | Self-signed or private root not trusted by this client | Trust the CA on the client (`/etc/pki/ca-trust/source/anchors/` then `update-ca-trust`), or use a publicly trusted cert |
| `62 (hostname mismatch)` | Name not in the SAN list (shown when using `-verify_hostname`) | Reissue with the correct Subject Alternative Names |

### Dates, names and the chain from a file or a live server

```bash
# from a live server
echo | openssl s_client -connect app.example.com:443 -servername app.example.com 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates -ext subjectAltName

# from a file
openssl x509 -in /etc/pki/nginx/server.crt -noout -subject -issuer -dates

# will it expire within 30 days? (exit status 1 = yes)
openssl x509 -in /etc/pki/nginx/server.crt -noout -checkend $((30*86400)) && echo OK || echo "EXPIRES SOON"

# show every certificate the server sends
openssl s_client -connect app.example.com:443 -servername app.example.com -showcerts </dev/null

# verify a chain offline
openssl verify -CAfile root.crt -untrusted intermediate.crt server.crt
```

```text
subject=CN = app.example.com
issuer=CN = Example Issuing CA
notBefore=Jan  1 00:00:00 2026 GMT
notAfter=Jan  1 00:00:00 2027 GMT
X509v3 Subject Alternative Name:
    DNS:app.example.com, DNS:www.app.example.com
```

(The `-ext` option needs OpenSSL 1.1.1 or newer; RHEL 9 ships OpenSSL 3.)

Modern clients ignore the Common Name and check only the **Subject Alternative Name** list, so a certificate with the right CN but no matching SAN still fails.

### Why "it works in my browser"

Browsers cache intermediate certificates they have seen before and some fetch missing intermediates automatically (AIA fetching). `curl`, Java, Python, Go and most API clients do not. An incomplete chain therefore often "works for me" in a browser and fails for every machine-to-machine integration. Always test with `openssl s_client` or `curl -v` from a server, not with a browser.

:::prod
**Symptom:** at 00:05 on the 1st of the month, payments from a partner's batch system started failing with "PKIX path building failed". The public website looked fine in browsers.

**What the engineer saw:** `openssl s_client` from a RHEL jump host showed `Verify return code: 21 (unable to verify the first certificate)` and only one certificate in the chain.

**Root cause:** the certificate had been renewed the day before. The engineer copied only the new leaf certificate into the nginx `ssl_certificate` file, replacing the previous file that contained leaf plus intermediate. Browsers coped; the partner's Java client did not.

**Fix:** `cat server.crt intermediate.crt > fullchain.pem`, pointed `ssl_certificate` at it, `nginx -t`, `systemctl reload nginx`.

**Prevention:** renewals are automated (ACME client or internal PKI tooling producing `fullchain.pem`), and monitoring now checks the verify return code and days to expiry from an external host, alerting at 30 and 14 days.
:::

## 10. Cloud security groups and NSGs: the cause you cannot see

On a cloud VM there is usually a second firewall **outside** the guest: AWS **security groups** and network ACLs, Azure **network security groups (NSGs)**, Google Cloud **VPC firewall rules**, or an on-premises network firewall. Nothing inside the VM shows them. `firewall-cmd`, `nft` and `ss` can all look perfect while every packet is dropped before it reaches the NIC.

Clues that the block is outside the VM:

- Clients see **timeouts**, not refused or "no route to host" (cloud security groups drop silently).
- `tcpdump -i <nic> 'tcp port N'` on the server shows **no SYN packets arriving** when a client tries to connect.
- Connections from inside the same subnet or security group work, from elsewhere they do not.
- The problem started after an infrastructure change (new subnet, new load balancer, Terraform apply) rather than an OS change.

What to check, with the people or tools that own it:

| Platform | Inbound control | Notes |
|---|---|---|
| AWS | Security group on the instance ENI, network ACL on the subnet | SGs are stateful; NACLs are stateless (return traffic needs its own rule) |
| Azure | NSG on the NIC and/or the subnet | Both must allow; Network Watcher "IP flow verify" tests a 5-tuple |
| GCP | VPC firewall rules, matched by network tags or service accounts | Implied deny-ingress rule |
| Any | Load balancer listeners and health checks, upstream proxies, on-prem firewalls | A failing health check removes the backend silently |

:::tip
Write the layers into your incident notes as you go: "Link OK, IP OK, route OK, DNS OK, listening on 0.0.0.0:443 OK, firewalld allows https OK, no SYNs seen in tcpdump". That last line is the evidence a network or cloud team needs to act quickly, and it stops the ticket bouncing back to you.
:::

## 11. Troubleshooting runbook

| Symptom | Check (command) | Likely cause | Fix |
|---|---|---|---|
| Interface has no IP | `ip -br link`, `nmcli dev status` | No carrier, connection not activated, DHCP failing | Fix cabling/vNIC; `nmcli con up <name>`; check DHCP server |
| Can reach local subnet but nothing else | `ip route`, `ip route get <ip>` | Missing or wrong default gateway | `nmcli con mod <name> ipv4.gateway 10.0.0.1`, `nmcli con up` |
| `Could not resolve host` | `getent hosts name`, `dig name`, `cat /etc/resolv.conf` | Wrong/unreachable DNS server, missing search domain, NXDOMAIN | Set DNS with `nmcli ... ipv4.dns`; fix the record |
| DNS change to `resolv.conf` keeps reverting | `head -1 /etc/resolv.conf` | NetworkManager regenerates the file | Configure DNS in the connection profile, or `dns=none` if config management owns it |
| App resolves to wrong IP, `dig` is correct | `getent hosts name`, `grep name /etc/hosts` | Stale `/etc/hosts` entry | Remove the entry |
| `curl localhost:PORT` works, remote gets "refused" | `ss -tulpn` | Bound to 127.0.0.1 | Change the app's bind address, or proxy it |
| Remote gets "No route to host", ping works | `firewall-cmd --list-all` | firewalld rejecting the port | `firewall-cmd --permanent --add-port=PORT/tcp` (or service), `--reload` |
| Remote gets timeout | `tcpdump -nn port PORT` on server | Cloud SG/NSG or network firewall dropping; `drop` zone | Fix the external rule; check zone |
| Rule worked until reboot | `firewall-cmd --permanent --list-all` | Rule added runtime-only | Add with `--permanent` and reload, or `--runtime-to-permanent` |
| Service fails to start on new port: "Permission denied" as root | `ausearch -m AVC -ts recent`, `semanage port -l` | SELinux `name_bind` denial | `semanage port -a -t <type> -p tcp PORT` |
| Reverse proxy 502, log shows `(13: Permission denied)` connecting upstream | `ausearch -m AVC -ts recent` | SELinux `name_connect` denial | `setsebool -P httpd_can_network_connect on` |
| SSH or HTTPS hangs on large transfers | `ping -M do -s 1472`, `tracepath` | MTU/PMTUD problem on the path | Lower interface MTU or fix ICMP filtering / tunnel MTU |
| `certificate has expired` | `openssl x509 -noout -dates` | Certificate past `notAfter` | Renew, deploy, reload service |
| Works in browser, API clients fail TLS | `openssl s_client ... -showcerts`, verify code 20/21 | Intermediate not served | Serve full chain (leaf + intermediates) |
| Wrong certificate returned | `openssl s_client` with and without `-servername` | No SNI from client, or wrong `server_name` block | Fix vhost config, ensure client sends SNI |

## 12. Break-fix labs

All labs assume a disposable Rocky Linux 9 or AlmaLinux 9 VM (RHEL 9 with a free developer subscription works too) with SELinux enforcing, firewalld running, root or sudo access, and **console access** (not only SSH) in case you break the network. Labs A and C need a second machine (another VM or your workstation) that can reach the lab VM; we call the lab VM `10.0.0.20` and the client `10.0.0.55`. Substitute your own addresses.

Install the tools once:

```bash
dnf install -y bind-utils nmap-ncat tcpdump mtr nginx policycoreutils-python-utils setroubleshoot-server openssl
getenforce              # must print Enforcing
systemctl is-active firewalld
```

:::lab Lab 04-A — App works locally but not remotely
**Goal:** diagnose an application that answers on the server but not from other hosts, separating two independent causes: the bind address and firewalld.

**Break it:** on the lab VM, start a small web server bound to localhost (running from your shell, so it is unconfined by SELinux; this lab isolates bind and firewall):

```bash
mkdir -p /tmp/lab04a && echo "hello from lab04a" > /tmp/lab04a/index.html
cd /tmp/lab04a && nohup python3 -m http.server 8000 --bind 127.0.0.1 >/tmp/lab04a.log 2>&1 &
```

**Symptoms:**

```bash
curl -s http://127.0.0.1:8000/          # on the VM: "hello from lab04a"
curl -v --connect-timeout 5 http://10.0.0.20:8000/     # on the client
```

```text
* connect to 10.0.0.20 port 8000 failed: Connection refused
```

**Diagnose:**

1. Is it listening, and where? On the VM: `ss -tlpn | grep 8000`. You see `127.0.0.1:8000`. That alone explains "refused": the kernel has no socket for 10.0.0.20:8000.
2. Note what the client error told you: "refused" means the packet reached the host, so the path is fine up to the kernel.

**Fix part 1 (bind address):**

```bash
pkill -f 'http.server 8000'
cd /tmp/lab04a && nohup python3 -m http.server 8000 --bind 0.0.0.0 >/tmp/lab04a.log 2>&1 &
ss -tlpn | grep 8000          # now 0.0.0.0:8000
```

Test again from the client. The error **changes**:

```text
* connect to 10.0.0.20 port 8000 failed: No route to host
```

**Diagnose part 2:**

3. Ping still works from the client (`ping -c2 10.0.0.20`), so "No route to host" is not routing. It is an ICMP reject from firewalld.
4. On the VM: `firewall-cmd --get-active-zones` and `firewall-cmd --list-all`. Port 8000 is not in `ports:` and no service covers it.
5. Optional proof: run `tcpdump -i any -nn 'tcp port 8000'` on the VM while the client retries. You see the SYNs arrive and no SYN-ACK leave.

**Fix part 2 (firewalld), permanent and active:**

```bash
firewall-cmd --permanent --zone=public --add-port=8000/tcp
firewall-cmd --reload
firewall-cmd --list-ports
```

**Verify:** from the client, `curl -s http://10.0.0.20:8000/` returns "hello from lab04a". Then `firewall-cmd --reload` once more and retest; it still works, which proves the rule is permanent.

**Better production variant:** restrict the port to the client subnet only:

```bash
firewall-cmd --permanent --zone=public --remove-port=8000/tcp
firewall-cmd --permanent --zone=public --add-rich-rule='rule family="ipv4" source address="10.0.0.0/24" port port="8000" protocol="tcp" accept'
firewall-cmd --reload
```

**Clean-up/revert:**

```bash
pkill -f 'http.server 8000'
firewall-cmd --permanent --zone=public --remove-rich-rule='rule family="ipv4" source address="10.0.0.0/24" port port="8000" protocol="tcp" accept'
firewall-cmd --permanent --zone=public --remove-port=8000/tcp 2>/dev/null
firewall-cmd --reload
rm -rf /tmp/lab04a /tmp/lab04a.log
```
:::

:::lab Lab 04-B — DNS resolution fails (resolv.conf and NetworkManager)
**Goal:** diagnose a host that cannot resolve names, see why hand-editing `/etc/resolv.conf` does not stick, and fix DNS properly through NetworkManager.

**Before you start:** record the current state so you can restore it.

```bash
CON=$(nmcli -g NAME,DEVICE con show --active | grep -v ':lo$' | head -1 | cut -d: -f1)
echo "Connection: $CON"
nmcli -g ipv4.dns,ipv4.ignore-auto-dns,ipv4.method con show "$CON" | tee /root/lab04b-before.txt
cp /etc/resolv.conf /root/lab04b-resolv.conf.bak
```

**Break it:** point the connection at a DNS server that does not exist (192.0.2.53 is in a documentation-only range) and ignore DHCP-provided DNS:

```bash
nmcli con mod "$CON" ipv4.ignore-auto-dns yes ipv4.dns 192.0.2.53
nmcli con up "$CON"
```

**Symptoms:**

```bash
dnf -q makecache          # "Curl error (6): Couldn't resolve host name"
curl -sI https://www.redhat.com      # "Could not resolve host"
getent hosts www.redhat.com; echo "exit=$?"     # no output, exit=2
```

**Diagnose:**

1. Rule out the lower layers: `ip -br addr` (address present), `ip route` (default route present), `ping -c2 <gateway-ip>` (works). Optionally `ping -c2 1.1.1.1` if your lab allows outbound ICMP.
2. Look at the resolver config: `cat /etc/resolv.conf`. It says `# Generated by NetworkManager` and `nameserver 192.0.2.53`.
3. Query that server directly: `dig @192.0.2.53 www.redhat.com`. Result: `;; connection timed out; no servers could be reached`. The server is unreachable, so the problem is the server choice, not a missing record.
4. Find where it comes from: `nmcli dev show | grep DNS` and `nmcli -g ipv4.dns,ipv4.ignore-auto-dns con show "$CON"`.

**The wrong fix (try it to see why it is wrong):**

```bash
sed -i 's/^nameserver .*/nameserver 10.0.0.2/' /etc/resolv.conf    # use your real DNS server IP
getent hosts www.redhat.com          # works now
nmcli con up "$CON"                  # simulates a DHCP renewal or reboot
cat /etc/resolv.conf                 # back to 192.0.2.53
```

NetworkManager regenerated the file from the connection profile. The hand edit lasted until the next event.

**The right fix:** restore the profile's DNS settings. If the connection originally used DHCP DNS:

```bash
nmcli con mod "$CON" ipv4.dns "" ipv4.ignore-auto-dns no
nmcli con up "$CON"
```

If your environment uses static DNS servers, set them on the profile instead: `nmcli con mod "$CON" ipv4.dns "10.0.0.2 10.0.0.3"`.

**Verify:**

```bash
cat /etc/resolv.conf
getent hosts www.redhat.com
dig www.redhat.com +short
nmcli con up "$CON" && getent hosts www.redhat.com    # survives a reapply
```

**Clean-up/revert:** compare with `/root/lab04b-before.txt`; if anything differs, set the values back with `nmcli con mod` and `nmcli con up`. Then `rm -f /root/lab04b-before.txt /root/lab04b-resolv.conf.bak`.
:::

:::lab Lab 04-C — nginx on a non-standard port blocked by SELinux
**Goal:** move nginx to port 8090, observe the SELinux `name_bind` denial, fix it with `semanage port` (not by disabling SELinux), and open the firewall.

**Before you start:** confirm 8090 has no SELinux label on your policy version (if it does, pick another unlabelled port such as 8095 and use it throughout):

```bash
semanage port -l | grep -w 8090 || echo "8090 unlabelled"
cp /etc/nginx/nginx.conf /root/nginx.conf.lab04c
systemctl enable --now nginx
curl -sI http://127.0.0.1/ | head -1         # HTTP/1.1 200 OK
```

**Break it:** change the default server's listen port from 80 to 8090:

```bash
sed -i -E 's/(listen\s+(\[::\]:)?)80;/\18090;/' /etc/nginx/nginx.conf
grep -n 'listen' /etc/nginx/nginx.conf
nginx -t                                   # syntax is fine
systemctl restart nginx
```

**Symptoms:**

```text
Job for nginx.service failed because the control process exited with error code.
```

```bash
systemctl status nginx --no-pager
journalctl -u nginx -b --no-pager | tail -5
```

```text
nginx: [emerg] bind() to 0.0.0.0:8090 failed (13: Permission denied)
```

**Diagnose:**

1. nginx's master process runs as root, so "Permission denied" on bind is not a Unix permission problem. Suspect SELinux.
2. `ausearch -m AVC -ts recent` shows `denied { name_bind } ... comm="nginx" src=8090 scontext=system_u:system_r:httpd_t:s0 tcontext=system_u:object_r:unreserved_port_t:s0`.
3. `ausearch -m AVC -ts recent | audit2why` and `sealert -a /var/log/audit/audit.log` both point to labelling the port.
4. `semanage port -l | grep http_port_t` confirms 8090 is not in the list.
5. Optional diagnostic only: `setenforce 0; systemctl restart nginx; ss -tlpn | grep 8090; setenforce 1`. It starts in permissive mode, which confirms SELinux. Turn enforcing back on immediately and stop nginx again (`systemctl stop nginx`) so you fix it properly.

**Fix:**

```bash
getenforce                                    # must be Enforcing again
semanage port -a -t http_port_t -p tcp 8090
semanage port -l -C                           # shows your customisation
systemctl restart nginx
ss -tlpn | grep 8090
```

The service now starts. From the client, `curl -v http://10.0.0.20:8090/` gives "No route to host": the next layer up is firewalld. Open it:

```bash
firewall-cmd --permanent --add-port=8090/tcp
firewall-cmd --reload
```

**Verify:** from the client, `curl -sI http://10.0.0.20:8090/ | head -1` returns `HTTP/1.1 200 OK`. `ausearch -m AVC -ts recent` shows no new denials. Reboot the VM if you want to prove the label and firewall rule persist.

**Variant: sshd on port 2222 (do this only with console access).** Add `Port 2222` **below** the existing `Port 22` line in `/etc/ssh/sshd_config` (keep 22 so you cannot lock yourself out), then `sshd -t && systemctl restart sshd`. `journalctl -u sshd` shows `error: Bind to port 2222 on 0.0.0.0 failed: Permission denied.` while port 22 keeps working. Fix with `semanage port -a -t ssh_port_t -p tcp 2222`, `firewall-cmd --permanent --add-port=2222/tcp`, `firewall-cmd --reload`, `systemctl restart sshd`, and test `ssh -p 2222` from the client. Revert by removing the line, `semanage port -d -t ssh_port_t -p tcp 2222`, removing the firewall port and restarting sshd.

**Clean-up/revert:**

```bash
cp /root/nginx.conf.lab04c /etc/nginx/nginx.conf && rm -f /root/nginx.conf.lab04c
semanage port -d -t http_port_t -p tcp 8090
firewall-cmd --permanent --remove-port=8090/tcp && firewall-cmd --reload
systemctl restart nginx          # back on port 80; or: systemctl disable --now nginx
```
:::

:::lab Lab 04-D — TLS certificate expired and chain incomplete
**Goal:** build a small private PKI (root, intermediate, server), serve it with nginx, and diagnose the two most common production TLS failures: a missing intermediate and an expired certificate.

**Set up the PKI** (on the lab VM, in a working directory):

```bash
mkdir -p /root/lab04d && cd /root/lab04d
printf 'basicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n' > ca.ext
printf 'subjectAltName=DNS:app.example.com\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n' > srv.ext

# root CA (self-signed, 10 years)
openssl req -new -newkey rsa:2048 -nodes -keyout root.key -out root.csr -subj "/CN=Lab Root CA"
openssl x509 -req -in root.csr -signkey root.key -days 3650 -extfile ca.ext -out root.crt

# intermediate CA signed by root
openssl req -new -newkey rsa:2048 -nodes -keyout int.key -out int.csr -subj "/CN=Lab Issuing CA"
openssl x509 -req -in int.csr -CA root.crt -CAkey root.key -CAcreateserial -days 1825 -extfile ca.ext -out int.crt

# server certificate signed by the intermediate, valid 1 year
openssl req -new -newkey rsa:2048 -nodes -keyout server.key -out server.csr -subj "/CN=app.example.com"
openssl x509 -req -in server.csr -CA int.crt -CAkey int.key -CAcreateserial -days 365 -extfile srv.ext -out server.crt

openssl verify -CAfile root.crt -untrusted int.crt server.crt     # server.crt: OK
```

**Install the files with the correct SELinux context.** Use `cp`, not `mv`: a copied file takes the label of the target directory (`cert_t`), while a moved file keeps `admin_home_t` from `/root`, which nginx (`httpd_t`) cannot read.

```bash
mkdir -p /etc/pki/nginx/private
cp server.crt /etc/pki/nginx/server.crt          # leaf ONLY: this is the break
cp server.key /etc/pki/nginx/private/server.key
chmod 600 /etc/pki/nginx/private/server.key
restorecon -Rv /etc/pki/nginx
ls -Z /etc/pki/nginx/server.crt                  # ...:cert_t:s0
echo "127.0.0.1 app.example.com" >> /etc/hosts   # lab only; removed in clean-up
```

Create `/etc/nginx/conf.d/lab04d.conf`:

```text
server {
    listen 443 ssl;
    server_name app.example.com;
    ssl_certificate     /etc/pki/nginx/server.crt;
    ssl_certificate_key /etc/pki/nginx/private/server.key;
    location / { return 200 "lab04d ok\n"; }
}
```

```bash
nginx -t && systemctl restart nginx
```

(Port 443 is already `http_port_t`, so no SELinux port change is needed.)

**Break 1 symptoms (incomplete chain):** the client trusts only the root, as a real client trusts only public roots.

```bash
curl -v --cacert /root/lab04d/root.crt https://app.example.com/
```

```text
curl: (60) SSL certificate problem: unable to get local issuer certificate
```

**Diagnose 1:**

```bash
openssl s_client -connect 127.0.0.1:443 -servername app.example.com -CAfile /root/lab04d/root.crt </dev/null 2>/dev/null \
  | grep -E '^ [0-9] |Verify return code'
```

```text
 0 s:CN = app.example.com
   ...
    Verify return code: 21 (unable to verify the first certificate)
```

Only certificate 0 is sent. The client has the root but no way to link the leaf (issued by "Lab Issuing CA") to it.

**Fix 1:** serve leaf followed by intermediate (order matters: leaf first).

```bash
cat /root/lab04d/server.crt /root/lab04d/int.crt > /etc/pki/nginx/fullchain.pem
restorecon -v /etc/pki/nginx/fullchain.pem
sed -i 's#/etc/pki/nginx/server.crt#/etc/pki/nginx/fullchain.pem#' /etc/nginx/conf.d/lab04d.conf
nginx -t && systemctl reload nginx
curl -s --cacert /root/lab04d/root.crt https://app.example.com/     # lab04d ok
```

`s_client` now lists certificates 0 and 1 and `Verify return code: 0 (ok)`.

**Break 2 (expired certificate):** `openssl x509 -req` in OpenSSL 3.0 cannot backdate a certificate, so use `openssl ca`, which accepts explicit start and end dates:

```bash
cd /root/lab04d
mkdir -p newcerts && touch index.txt && echo 1000 > serial
cat > ca.cnf <<'EOF'
[ ca ]
default_ca = labca
[ labca ]
database       = ./index.txt
new_certs_dir  = ./newcerts
serial         = ./serial
default_md     = sha256
policy         = pol_any
unique_subject = no
[ pol_any ]
commonName     = supplied
EOF
openssl ca -config ca.cnf -batch -notext -cert int.crt -keyfile int.key \
  -in server.csr -out server-expired.crt -extfile srv.ext \
  -startdate 240101000000Z -enddate 240201000000Z
cat server-expired.crt int.crt > /etc/pki/nginx/fullchain.pem
restorecon -v /etc/pki/nginx/fullchain.pem
systemctl reload nginx
```

**Symptoms 2:**

```bash
curl -s --cacert /root/lab04d/root.crt https://app.example.com/
```

```text
curl: (60) SSL certificate problem: certificate has expired
```

**Diagnose 2:**

```bash
echo | openssl s_client -connect 127.0.0.1:443 -servername app.example.com -CAfile /root/lab04d/root.crt 2>/dev/null \
  | grep 'Verify return code'
echo | openssl s_client -connect 127.0.0.1:443 -servername app.example.com 2>/dev/null \
  | openssl x509 -noout -subject -dates
openssl x509 -in /etc/pki/nginx/fullchain.pem -noout -checkend 0 || echo "already expired"
```

```text
    Verify return code: 10 (certificate has expired)
notAfter=Feb  1 00:00:00 2024 GMT
```

Note that `openssl x509` reads only the **first** certificate in a bundle, which is the leaf: exactly the one you want to check.

**Fix 2:** deploy a valid certificate (in production: renew through your CA or ACME client), rebuild the full chain, reload.

```bash
cat /root/lab04d/server.crt /root/lab04d/int.crt > /etc/pki/nginx/fullchain.pem
restorecon -v /etc/pki/nginx/fullchain.pem
nginx -t && systemctl reload nginx
```

**Verify:** `curl -s --cacert /root/lab04d/root.crt https://app.example.com/` prints `lab04d ok`, and `s_client` shows `Verify return code: 0 (ok)` with two certificates in the chain. Also try without `-servername` against `127.0.0.1` to see that nginx still returns this certificate only because it is the sole TLS server block; on a host with several sites, SNI decides.

**Clean-up/revert:**

```bash
rm -f /etc/nginx/conf.d/lab04d.conf && systemctl reload nginx
rm -rf /etc/pki/nginx /root/lab04d
sed -i '/app\.example\.com/d' /etc/hosts
```
:::

## 13. Prevention habits

- **Bind deliberately.** Document for every service which address it listens on and why. Databases and admin ports on 127.0.0.1 or a management network; public services on 0.0.0.0 behind a firewall rule.
- **Firewall rules as code.** Every rule permanent, scoped by source where possible, applied by configuration management, and verified after reboot.
- **Never hand-edit `/etc/resolv.conf`** on a NetworkManager host. Put DNS in the connection profile, or set `dns=none` explicitly if another tool owns the file.
- **Keep `/etc/hosts` clean.** Template it; alert on unexpected entries.
- **Label ports with the service.** When you change a port in a config file, the same change (in Ansible, for example) runs `semanage port` and opens the firewall. `community.general.seport` and `ansible.posix.firewalld` make this one play.
- **Monitor certificates from outside.** Check days-to-expiry and the verify result from a host that does not cache intermediates. Alert at 30 and 14 days. Automate renewals and always deploy the full chain.
- **Record the evidence.** For every connectivity incident, note which layer passed and which failed. The pattern across incidents shows you which controls need automation.

## Command cheat sheet

| Task | Command |
|---|---|
| Link state and carrier | `ip -br link`, `ip -s link show ens192`, `ethtool ens192` |
| Addresses | `ip -br addr` |
| Routes and route choice | `ip route`, `ip route get 192.0.2.50` |
| NetworkManager devices and connections | `nmcli dev status`, `nmcli con show --active`, `nmcli dev show ens192` |
| Static IP | `nmcli con mod ens192 ipv4.method manual ipv4.addresses 10.0.0.20/24 ipv4.gateway 10.0.0.1` then `nmcli con up ens192` |
| DNS servers on a connection | `nmcli con mod ens192 ipv4.dns "10.0.0.2 10.0.0.3" ipv4.ignore-auto-dns yes` |
| Re-read hand-edited keyfiles | `nmcli con reload` |
| Text UI | `nmtui` |
| What the app will resolve | `getent hosts name` |
| Query DNS directly | `dig name`, `dig @10.0.0.2 name +short`, `dig -x IP`, `nslookup name` |
| Path and loss | `ping -c4 IP`, `tracepath -n IP`, `mtr -n --report -c 20 IP` |
| MTU test | `ping -M do -s 1472 IP` |
| Listening sockets | `ss -tulpn` |
| HTTP with details | `curl -v URL`, `curl --resolve host:443:IP https://host/` |
| TCP port test | `nc -zv IP PORT`, `timeout 3 bash -c '</dev/tcp/IP/PORT'` |
| Packet capture | `tcpdump -i ens192 -nn 'tcp port 443' -c 50` |
| firewalld state | `firewall-cmd --get-active-zones`, `firewall-cmd --list-all` |
| Open a service or port | `firewall-cmd --permanent --add-service=https`, `--add-port=8000/tcp`, then `--reload` |
| Save runtime rules | `firewall-cmd --runtime-to-permanent` |
| Source-restricted rule | `firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="10.0.0.0/24" port port="5432" protocol="tcp" accept'` |
| Kernel ruleset | `nft list ruleset` |
| Ubuntu firewall | `ufw status verbose`, `ufw allow 8000/tcp` |
| SELinux port labels | `semanage port -l`, `semanage port -l -C` |
| Label a port | `semanage port -a -t http_port_t -p tcp 8090` (`-m` to modify, `-d` to delete) |
| sshd on new port | `semanage port -a -t ssh_port_t -p tcp 2222` |
| Find denials | `ausearch -m AVC -ts recent`, pipe to `audit2why`; `sealert -a /var/log/audit/audit.log` |
| Proxy may connect out | `setsebool -P httpd_can_network_connect on` |
| TLS handshake and chain | `openssl s_client -connect host:443 -servername host -showcerts </dev/null` |
| Cert dates and names | `openssl x509 -in cert.pem -noout -subject -issuer -dates -ext subjectAltName` |
| Expiry check | `openssl x509 -in cert.pem -noout -checkend 2592000` |
| Verify a chain | `openssl verify -CAfile root.crt -untrusted int.crt server.crt` |
| Trust a private CA | copy to `/etc/pki/ca-trust/source/anchors/`, run `update-ca-trust` |

:::summary
- Walk the layers in order: **link, IP, route, DNS, port, app, TLS**. Stop at the first failure; everything above it will look broken too.
- The client error is a clue: **refused** = reached the host, nothing listening there; **no route to host** with working ping = firewalld reject; **timeout** = silent drop, often outside the VM; **could not resolve** = DNS.
- `ip -br link`, `ip -br addr`, `ip route get` cover the bottom three layers in seconds. Make permanent network changes with **nmcli**, not `ip`.
- On RHEL 9 **NetworkManager writes `/etc/resolv.conf`**; configure DNS on the connection profile. `getent hosts` shows what applications see (including `/etc/hosts`); `dig` queries DNS only.
- `ss -tulpn` shows the bind address: **127.0.0.1 is reachable only from the host itself**, and no firewall rule can change that.
- firewalld has **runtime and permanent** configurations. Use `--permanent` plus `--reload` (or `--runtime-to-permanent`); prefer rich rules scoped to a source.
- SELinux labels ports. A confined service on a new port fails with **"Permission denied" as root** and a `name_bind` AVC; fix with **`semanage port -a`** (or `-m`). Outbound proxy denials are `name_connect`; fix with **`setsebool -P httpd_can_network_connect on`**. Never disable SELinux as the fix.
- **tcpdump** settles where packets stop: SYNs not arriving means the block is upstream, such as a **cloud security group or NSG**.
- TLS: check **dates, SAN names, and the chain** with `openssl s_client -servername` and `openssl x509`. Verify code **10** = expired, **20/21** = missing intermediate or untrusted CA. Always deploy **leaf + intermediates**.
:::

## Self-check questions

<div class="q" markdown="1">

**Q1.** A developer says their API "works fine" because `curl http://localhost:5000/health` succeeds on the server, but the load balancer marks the backend as down. The load balancer's health check gets "connection refused". What do you check first?

A. `firewall-cmd --list-all` to see if port 5000 is open
B. `ss -tulpn | grep 5000` to see which address the API is bound to
C. `semanage port -l | grep 5000`
D. The cloud security group rules

:::answer
**Answer: B.** "Connection refused" means the SYN reached the host and the kernel found no listening socket for that address and port. The classic cause when localhost works is a bind to `127.0.0.1`. `ss -tulpn` shows it immediately.

- A is wrong as a first step: firewalld rejecting produces "No route to host", not "refused".
- C is wrong: an SELinux bind denial would stop the service from listening at all, so localhost would fail too.
- D is wrong: security groups drop silently and produce timeouts, not refusals.
:::
</div>

<div class="q" markdown="1">

**Q2.** You added DNS servers by editing `/etc/resolv.conf` on a RHEL 9 server. It worked, but the next morning resolution is broken again and the file contains the old servers. Why, and what is the correct fix?

A. `systemd-resolved` overwrote it; disable systemd-resolved
B. NetworkManager regenerates the file from the connection profile; set DNS with `nmcli con mod <con> ipv4.dns ...` and `nmcli con up <con>`
C. SELinux relabelled the file; run `restorecon /etc/resolv.conf`
D. The file is immutable; run `chattr -i /etc/resolv.conf`

:::answer
**Answer: B.** On RHEL 9 NetworkManager owns `/etc/resolv.conf` (note the "Generated by NetworkManager" header) and rewrites it on connection activation or DHCP renewal. DNS belongs in the connection profile; if another tool must own the file, set `dns=none` in NetworkManager configuration.

- A is wrong: systemd-resolved is not enabled by default on RHEL 9 (it is the Ubuntu default).
- C is wrong: restorecon fixes SELinux labels, not file contents.
- D is wrong: an immutable file could not have been edited or overwritten at all; and `chattr +i` is a hack that breaks NetworkManager.
:::
</div>

<div class="q" markdown="1">

**Q3.** `dig app.example.com` returns 10.0.0.40, but the application on the same server keeps connecting to 10.0.0.99. What is the most likely cause?

A. The DNS TTL has not expired yet
B. A stale entry in `/etc/hosts`
C. The DNS server returned NXDOMAIN
D. The application uses IPv6

:::answer
**Answer: B.** Applications resolve through glibc and `nsswitch.conf` (`hosts: files dns`), so `/etc/hosts` wins. `dig` ignores `/etc/hosts`. Confirm with `getent hosts app.example.com` and `grep app /etc/hosts`.

- A is wrong: the TTL matters for caches, but `dig` on the same server already sees the new address.
- C is wrong: NXDOMAIN would give no address at all.
- D is wrong: 10.0.0.99 is an IPv4 address; IPv6 does not explain it.
:::
</div>

<div class="q" markdown="1">

**Q4.** After changing nginx to `listen 8090`, `systemctl restart nginx` fails and the journal shows `bind() to 0.0.0.0:8090 failed (13: Permission denied)`. `ss` shows nothing else on 8090. What is the correct fix?

A. `setenforce 0` and add it to `/etc/rc.local`
B. Run nginx as root by editing the unit file
C. `semanage port -a -t http_port_t -p tcp 8090`, then restart nginx
D. `chmod 777 /etc/nginx/nginx.conf`

:::answer
**Answer: C.** "Permission denied" on bind while the master process runs as root is an SELinux `name_bind` denial: 8090 is not an `http_port_t` port. Labelling the port is the precise, persistent fix. Confirm first with `ausearch -m AVC -ts recent`.

- A is wrong: it disables SELinux protection for the whole host; never a fix.
- B is wrong: the nginx master already runs as root; the denial is from SELinux, not Unix permissions.
- D is wrong: file permissions are irrelevant to binding a port, and 777 on a config file is a security problem.
:::
</div>

<div class="q" markdown="1">

**Q5.** You run `semanage port -a -t http_port_t -p tcp 8080` and get "Port tcp/8080 already defined". What does this mean and what do you do?

A. The port is in use by another process; stop it
B. 8080 already has an SELinux type (for example `http_cache_port_t`); check whether you can change it and use `semanage port -m -t http_port_t -p tcp 8080`
C. SELinux is disabled; enable it first
D. You must reboot to clear the old label

:::answer
**Answer: B.** `-a` adds a new mapping and fails if the port is already labelled in policy. `-m` modifies the existing mapping. Check first (`semanage port -l | grep -w 8080`) whether another confined service depends on the current type. Also note that some domains may already be allowed to bind the existing type, so read the AVC before changing anything.

- A is wrong: the message concerns policy labels, not running processes.
- C is wrong: semanage works with SELinux enabled; the message is about an existing definition.
- D is wrong: port labels take effect immediately with no reboot.
:::
</div>

<div class="q" markdown="1">

**Q6.** A colleague opened TCP 9100 with `firewall-cmd --add-port=9100/tcp`. It worked, but after a reboot monitoring fails with "No route to host". What happened?

A. The rule was added only to the runtime configuration and was lost at reboot
B. SELinux blocked port 9100 after reboot
C. The interface moved to the `trusted` zone
D. nftables replaced firewalld at boot

:::answer
**Answer: A.** Without `--permanent`, firewalld changes live only in the runtime configuration and disappear on reload or reboot. Add with `--permanent` and `--reload`, or run `firewall-cmd --runtime-to-permanent` after testing.

- B is wrong: an SELinux bind problem would stop the exporter from listening, giving "refused", and would have existed before the reboot too.
- C is wrong: the `trusted` zone accepts everything, so the port would work.
- D is wrong: on RHEL 9 firewalld uses nftables as its backend; they do not replace each other at boot.
:::
</div>

<div class="q" markdown="1">

**Q7.** Clients outside a cloud VM get **timeouts** connecting to port 443. On the VM, `ss` shows nginx on `0.0.0.0:443`, firewalld allows `https`, and `tcpdump -nn 'tcp port 443'` shows no packets at all during a client attempt. Where is the problem most likely?

A. nginx's worker processes are hung
B. SELinux is denying `name_bind` on 443
C. Outside the VM: a cloud security group, NSG, network ACL or upstream firewall
D. The TLS certificate has expired

:::answer
**Answer: C.** If SYNs never reach the NIC, nothing inside the guest can be the cause. Silent drops and timeouts are typical of cloud security groups and NSGs. Hand the evidence (no SYNs seen) to whoever owns those rules.

- A is wrong: a hung application would still let the SYN arrive (visible in tcpdump), and the kernel would complete or queue the handshake.
- B is wrong: 443 is `http_port_t`, and nginx is already listening.
- D is wrong: an expired certificate fails during the TLS handshake, after TCP connects; tcpdump would show traffic.
:::
</div>

<div class="q" markdown="1">

**Q8.** A partner's Java integration fails with a certificate path error against your HTTPS API, but the site opens fine in Chrome. `openssl s_client -servername api.example.com -connect api.example.com:443` shows one certificate in the chain and `Verify return code: 21`. What is the fix?

A. Ask the partner to disable certificate validation
B. Configure the server to send the leaf certificate followed by the intermediate certificate(s)
C. Renew the certificate with a longer validity
D. Add the root CA to the server's own trust store

:::answer
**Answer: B.** Code 21 with only one certificate sent means the intermediate is missing. Browsers often cope by caching or fetching intermediates; most API clients do not. Serve a full chain (leaf first, then intermediates) and reload the web server.

- A is wrong: disabling validation removes the protection TLS provides.
- C is wrong: the certificate is not expired; validity length is irrelevant.
- D is wrong: the server's own trust store affects outbound connections from the server, not what it sends to clients.
:::
</div>

<div class="q" markdown="1">

**Q9.** `openssl s_client -connect 10.0.0.40:443` returns a certificate for `default.example.com`, but users get the correct `shop.example.com` certificate. What explains the difference?

A. The certificate is cached by the browser
B. Your test did not send SNI; add `-servername shop.example.com`
C. The server has two IP addresses
D. TLS 1.3 hides the certificate

:::answer
**Answer: B.** Servers hosting several names on one IP choose the certificate from the SNI value. Without `-servername`, you get the default virtual host's certificate. Always test with the real hostname.

- A is wrong: browsers do not cache server certificates in a way that changes which one the server sends.
- C is wrong: you connected to one specific IP; the difference is SNI, not addressing.
- D is wrong: TLS 1.3 encrypts the certificate on the wire, but `s_client` still displays it.
:::
</div>

<div class="q" markdown="1">

**Q10.** nginx reverse-proxies to an app on `127.0.0.1:3000` and returns 502. The nginx error log says `connect() to 127.0.0.1:3000 failed (13: Permission denied)`. The app is running and `curl 127.0.0.1:3000` works from a shell. What is the best fix?

A. `setsebool -P httpd_can_network_connect on`
B. Bind the app to 0.0.0.0
C. `firewall-cmd --add-port=3000/tcp --permanent`
D. `audit2allow -M nginxfix` and load the module without reviewing it

:::answer
**Answer: A.** The confined `httpd_t` domain is denied `name_connect` to the upstream port. The supported switch for proxies is the `httpd_can_network_connect` boolean, made persistent with `-P`. (Labelling port 3000 as `http_port_t` is a narrower alternative.)

- B is wrong: the connection is local; the bind address is not the issue, and exposing the app widens the attack surface.
- C is wrong: firewalld does not filter loopback traffic, and the error is "Permission denied", which is SELinux.
- D is wrong: blindly generated modules may grant far more than needed; booleans and port labels exist for this case.
:::
</div>

<div class="q" markdown="1">

**Q11.** A server can ping its gateway but `dig www.example.com` prints `;; connection timed out; no servers could be reached`. What does this tell you, and what do you check next?

A. The record does not exist; check the zone file
B. No DNS server answered; check `/etc/resolv.conf` and `nmcli dev show | grep DNS` for wrong or unreachable servers
C. The default route is missing; check `ip route`
D. `/etc/hosts` has a bad entry

:::answer
**Answer: B.** A timeout means no configured resolver answered, which is a resolver selection or reachability problem, not a record problem. Check which servers are configured and where they come from (connection profile, DHCP), then test each with `dig @server`.

- A is wrong: a missing record returns `status: NXDOMAIN` from a server that did answer.
- C is wrong: the gateway is reachable and the question is about DNS; `ip route` is worth a glance but the dig error points to the resolver.
- D is wrong: `dig` does not read `/etc/hosts`.
:::
</div>

<div class="q" markdown="1">

**Q12.** SSH sessions to a server over a new VPN connect fine, but hang as soon as you run a command with a lot of output. Small commands work. What do you test?

A. `ss -tulpn` on the server
B. Path MTU, with `ping -M do -s 1472 <server>` and `tracepath <server>`
C. `semanage port -l | grep ssh`
D. The SSH host key fingerprint

:::answer
**Answer: B.** Small packets get through and large ones vanish: the signature of an MTU problem on the tunnel, usually with blocked ICMP "fragmentation needed" messages. Find the working size with `ping -M do`, then lower the MTU on the interface or tunnel, or fix ICMP filtering.

- A is wrong: sshd is clearly listening, since the session connects.
- C is wrong: SELinux port labels would stop sshd from binding at all, not cause hangs mid-session.
- D is wrong: host key problems stop the connection at login, with a clear warning.
:::
</div>
