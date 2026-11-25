<div class="cover" markdown="1">
<div class="mod">Module 05 · Performance Troubleshooting</div>

# Performance Troubleshooting: CPU, Memory, Disk I/O, Limits and the OOM Killer

<p>How to turn "the server is slow" into a measured bottleneck in minutes: the USE method, load average, vmstat, iostat, sar, memory pressure and the OOM killer, file descriptor limits, CPU steal, and how to contain a misbehaving process with systemd and cgroups instead of rebooting.</p>
</div>

## What you'll learn

- The **USE method** (Utilization, Saturation, Errors) as a checklist for every resource, so you stop guessing
- What **load average** really counts on Linux (runnable **and** D-state tasks) and why a load of 40 can mean a stuck NFS mount, not a busy CPU
- How to read `uptime`, `top`/`htop` (`us sy ni id wa hi si st`), `vmstat`, `mpstat`, `pidstat`, `iostat -xz` (`r_await`, `w_await`, `aqu-sz`, `%util`) and `sar` history from **sysstat**
- `free -h`: why "free" is small on a healthy server and "available" is the number that matters; page cache, memory pressure, PSI, swap thrashing
- The **OOM killer**: reading its log lines, `oom_score_adj`, cgroup memory limits, systemd `MemoryMax=` and `MemoryHigh=`
- **ulimits** and "Too many open files": why `/etc/security/limits.conf` does not apply to services and how `LimitNOFILE=` fixes it
- Process states (`R S D Z T I`), zombies, CPU **steal** in VMs, `strace` basics, `perf top`, network saturation with `sar -n DEV` and `ss -s`, and Java/Python memory leaks at a high level
- Where **SELinux** shows up in performance work: `deny_ptrace` blocking `strace`, `httpd_setrlimit` for web servers raising their own limits, and AVC floods that burn CPU
- Four break-fix labs: a CPU hog contained with `CPUQuota=`, a memory leak that gets OOM-killed, a "Too many open files" fix with a systemd drop-in, and a disk I/O bottleneck found with `fio` and `iostat`

**Why it matters in production:** "It's slow" is the most common and least specific ticket you will ever get. Engineers who reboot or "add more RAM" without measuring fix nothing and lose the evidence. An engineer who can say "the disk queue is saturated at 98% with a 140 ms write latency, caused by the backup job in `backup.service`, and I've capped it at 50 MB/s" ends the incident and writes a useful postmortem.

## 1. A method before commands: USE

The worst performance investigation is the one where you run every tool you know and stare at numbers. Use a method. Brendan Gregg's **USE method** says: for every **resource**, check **U**tilization, **S**aturation and **E**rrors.

| Term | Meaning | Example |
|---|---|---|
| **Utilization** | How busy the resource was over an interval, as a percentage | CPU 95% busy; disk busy 80% of the time |
| **Saturation** | Extra work that could not be served and is **waiting** (a queue) | Run queue longer than CPU count; disk queue depth of 30; swapping |
| **Errors** | Error events, which are often cheaper to check than the others | NIC drops, disk I/O errors in `dmesg`, OOM kills, `EMFILE` |

Apply it to the four big resources, plus software limits:

| Resource | Utilization | Saturation | Errors |
|---|---|---|---|
| **CPU** | `mpstat -P ALL 1`, `top` (`us`+`sy`), `sar -u` | `vmstat 1` column `r` > CPU count, load average, `sar -q`, PSI `cpu` | Rare; machine check events in `dmesg` |
| **Memory** | `free -h` (`available`), `sar -r` | Swapping (`vmstat` `si`/`so`), major faults, PSI `memory`, page scanning (`sar -B`) | OOM kills in `dmesg`/`journalctl -k`, allocation failures |
| **Disk** | `iostat -xz 1` `%util` | `aqu-sz`, `r_await`/`w_await`, `vmstat` `b` and `wa`, PSI `io` | `dmesg` I/O errors, `smartctl`, filesystem going read-only |
| **Network** | `sar -n DEV 1` (`rxkB/s`, `txkB/s`, `%ifutil`) | `ss -s` and `ss -tn` queues, `nstat` retransmits | `sar -n EDEV 1`, `ip -s link` drops/errors |
| **Software limits** | `/proc/PID/fd` count vs limit, thread count, connection pool use | Threads waiting on locks, accept queue overflows | `EMFILE`, `EAGAIN`, "Too many open files" in app logs |

:::concept
**Utilization tells you a resource is busy. Saturation tells you it is hurting.** A CPU at 100% with a run queue of 2 on a 4-core box is fine; a disk at 40% utilization with a 200 ms average wait is a problem. Always look for the **queue**, not just the percentage.
:::

### The first 60 seconds

When you land on a slow server, run these in order before forming an opinion. Each takes a few seconds and together they cover every row of the USE table.

```bash
uptime                  # load averages: trend up or down?
dmesg -T | tail -30     # OOM kills, I/O errors, hung tasks (or: journalctl -k -n 50)
vmstat 1 5              # run queue, blocked, swap, iowait, steal
mpstat -P ALL 1 3       # one CPU pegged, or all of them?
pidstat 1 3             # which process is using CPU
iostat -xz 1 3          # disk latency and queue
free -h                 # available memory, swap use
sar -n DEV 1 3          # network throughput per interface
top                     # overview; press 1 for per-CPU, P/M to sort
```

:::tip
Write down the time you started and paste each command's output into the incident channel as you go. The numbers you capture **during** the incident are the evidence. After a reboot they are gone, except what `sar` and the journal kept.
:::

## 2. Load average explained

```bash
uptime
```

```text
 10:42:17 up 41 days,  3:12,  2 users,  load average: 7.91, 4.12, 1.86
```

The three numbers are exponentially damped moving averages over **1, 5 and 15 minutes**. Reading them left to right tells you the trend: here load is climbing fast (1-minute much higher than 15-minute), so something started a few minutes ago.

### What load actually counts on Linux

On most Unix systems load average counts tasks that are **running or waiting to run on a CPU**. Linux also counts tasks in **uninterruptible sleep (D state)**, which are usually waiting on disk or network storage I/O. So on Linux:

**load = runnable tasks (R) + uninterruptible tasks (D), averaged**

That single design choice causes most load-average confusion:

| What you see | Likely meaning | Confirm with |
|---|---|---|
| Load ≈ CPU count, `us`+`sy` high, `vmstat r` high | CPU-bound: the box is busy computing | `mpstat`, `pidstat 1` |
| Load high, CPU mostly **idle**, `wa` high, `vmstat b` high | Tasks stuck waiting on I/O (slow disk, saturated SAN) | `iostat -xz 1`, `pidstat -d 1` |
| Load very high (50, 200), CPU idle, `wa` may even be low | Many tasks in **D state** on a hung NFS/iSCSI/CIFS mount | `ps -eo state,pid,cmd \| grep '^D'`, `dmesg` "hung task" |

To judge whether a load figure is high, compare it with the number of CPUs:

```bash
nproc
grep -c ^processor /proc/cpuinfo
```

A load of 7.9 on a 16-CPU server is half-busy; on a 2-CPU VM it means about six tasks are always waiting.

:::story
Think of a supermarket. The **CPUs are the checkouts**. Load average is the number of shoppers either being served or standing in line. Linux also counts the shoppers who are **stuck waiting for a price check** (D state): they are not using the checkout, but they are not leaving either. A long queue with idle cashiers means the price-check desk (storage) is the problem, not the number of checkouts.
:::

:::prod
**The load of 312 with an idle CPU.** Monitoring paged at 03:00: load average 312 on a 4-vCPU application server. The on-call engineer saw `top` showing 97% idle and assumed a monitoring bug. `ps -eo state,pid,cmd | grep ^D` listed 300 `df` and `ls` processes, all stuck. `dmesg -T` showed `nfs: server 10.0.0.20 not responding, still trying`. A cron job running `df` every minute against a hung NFS mount had piled up D-state processes all night. **Root cause:** the NFS server had failed over and the client was hard-mounted with no `timeo` tuning. **Fix:** restore the NFS server; the stuck processes completed by themselves. **Prevention:** monitoring checks with timeouts (`timeout 10 df /mnt/share`), alerting on D-state count, and a reviewed mount options policy for NFS.
:::

### D-state processes

```bash
ps -eo state,pid,ppid,wchan:32,cmd | awk '$1=="D"'
```

```text
D   4812     1 rpc_wait_bit_killable          df -h
D   4877     1 folio_wait_bit_common          /opt/myapp/bin/indexer
```

The `wchan` column shows the kernel function the task is sleeping in; `rpc_*` and `nfs_*` point at NFS, `folio_wait_*`, `io_schedule` and `blk_*` point at block I/O. You cannot `kill -9` a process in D state: the signal is delivered only when the I/O completes. Fix the storage, and the processes go away.

## 3. Reading top and htop

```bash
top
```

```text
top - 10:42:20 up 41 days,  3:12,  2 users,  load average: 7.91, 4.12, 1.86
Tasks: 231 total,   3 running, 228 sleeping,   0 stopped,   0 zombie
%Cpu(s): 62.4 us, 11.2 sy,  0.0 ni, 21.9 id,  3.1 wa,  0.0 hi,  0.6 si,  0.8 st
MiB Mem :   7681.3 total,    312.6 free,   2140.2 used,   5520.4 buff/cache
MiB Swap:   2048.0 total,   2048.0 free,      0.0 used.   5291.7 avail Mem

    PID USER      PR  NI    VIRT    RES    SHR S  %CPU  %MEM     TIME+ COMMAND
   2231 myapp     20   0 4210536   1.2g  38412 S 187.4  16.1 812:44.12 java
   9910 root      20   0  237652  10220   8100 R  24.9   0.1   0:03.20 gzip
```

| Field | Meaning | When it matters |
|---|---|---|
| `us` | User-space CPU time (your application code) | High = application is computing; profile the app |
| `sy` | Kernel CPU time (system calls, networking, filesystem) | High `sy` = heavy syscalls, context switching, lock contention, or packet processing |
| `ni` | User time of **niced** (low priority) processes | Batch jobs running with `nice` |
| `id` | Idle | |
| `wa` | **I/O wait**: CPU idle **while** there is outstanding disk I/O | A hint, not a measurement of disk load; confirm with `iostat` |
| `hi` / `si` | Hardware / software interrupt handling | High `si` on network-heavy hosts (packet processing) |
| `st` | **Steal**: time the hypervisor gave your vCPU to someone else | Above a few percent on a VM = noisy neighbour or overcommitted host |

Process columns: `VIRT` is virtual address space (mostly meaningless for sizing), `RES` is resident memory actually in RAM, `SHR` is the shared part of `RES`, `S` is the state. `%CPU` is per single CPU, so 187% means nearly two full cores.

Useful keys in `top`: `1` per-CPU view, `P` sort by CPU, `M` sort by memory, `c` full command line, `H` show threads, `k` kill, `f` choose fields. `htop` (from EPEL on RHEL) shows the same data with colour bars; the CPU bar colours map to user, system, nice, steal and so on (press `F1` for the legend).

:::warn
**`wa` is misleading on multi-core systems.** It only counts CPUs that are idle **and** have I/O pending. A busy CPU with a process waiting on disk shows as `us`, not `wa`. A server can have a terrible disk problem and 2% `wa`. Never conclude "disk is fine" from `wa` alone; use `iostat -xz`.
:::

### Batch mode for evidence

```bash
top -b -n 1 -o %MEM | head -20      # one snapshot, sorted by memory
top -b -d 5 -n 12 > /tmp/top-$(date +%F-%H%M).txt   # one minute of 5-second samples
```

## 4. vmstat, mpstat and pidstat

### vmstat: the whole system on one line

```bash
vmstat 1 5
```

```text
procs -----------memory---------- ---swap-- -----io---- -system-- ------cpu-----
 r  b   swpd   free   buff  cache   si   so    bi    bo   in   cs us sy id wa st
 9  0      0 318012   4204 5650220    0    0     2    41 3120 5210 72 12 15  0  1
11  0      0 316880   4204 5650228    0    0     0    16 3388 5498 81 13  5  0  1
 2  6      0 312004   4204 5652100    0    0  8120 51200 2240 3100 12  6 40 42  0
```

| Column | Meaning | Red flag |
|---|---|---|
| `r` | Runnable tasks (running + waiting for CPU) | Consistently > number of CPUs = CPU saturation |
| `b` | Tasks blocked in uninterruptible sleep (D) | Consistently > 0 = I/O or storage waits |
| `swpd` | Swap in use (KiB) | Non-zero alone is fine; growing is not |
| `free`, `buff`, `cache` | Free memory, buffers, page cache | Low `free` is normal; see section 6 |
| `si` / `so` | Swap in / swap out per second | Sustained non-zero `si` **and** `so` = thrashing |
| `bi` / `bo` | Blocks read / written per second | Context for disk activity |
| `in` / `cs` | Interrupts / context switches per second | Very high `cs` with high `sy` = contention |
| `us sy id wa st` | CPU split, same as `top` | |

The first line is an average **since boot**: ignore it. In the sample, lines 1–2 show a CPU-bound system (`r` 9–11, `us` 72–81) and line 3 shows a switch to I/O-bound (`b` 6, `wa` 42, `bo` 51200).

`vmstat -w 1` gives wider columns that don't overflow on big-memory servers; `vmstat -s` prints counters; `vmstat -d` prints disk statistics. (Newer procps-ng releases add a `gu` guest column; RHEL 9's procps-ng 3.3.x does not.)

### mpstat: per-CPU breakdown

```bash
mpstat -P ALL 1 3
```

```text
10:44:01 AM  CPU    %usr   %nice    %sys %iowait    %irq   %soft  %steal  %guest  %gnice   %idle
10:44:02 AM  all   26.12    0.00    2.01    0.00    0.00    0.25    0.00    0.00    0.00   71.61
10:44:02 AM    0  100.00    0.00    0.00    0.00    0.00    0.00    0.00    0.00    0.00    0.00
10:44:02 AM    1    3.03    0.00    4.04    0.00    0.00    1.01    0.00    0.00    0.00   91.92
10:44:02 AM    2    1.00    0.00    2.00    0.00    0.00    0.00    0.00    0.00    0.00   97.00
10:44:02 AM    3    0.99    0.00    1.98    0.00    0.00    0.00    0.00    0.00    0.00   97.03
```

The `all` line says 72% idle, which looks healthy. The per-CPU lines show **CPU 0 pegged at 100%**: a single-threaded process is maxed out. Adding CPUs will not help; the process needs to be parallelised or made more efficient. One CPU with high `%soft` on a network server often means all NIC interrupts land on one core (check `/proc/interrupts` and `irqbalance`).

### pidstat: per-process over time

`top` is a snapshot; `pidstat` gives you per-interval, per-process numbers you can paste into a ticket.

```bash
pidstat 1 3            # CPU per process
pidstat -u -p 2231 1   # one PID
pidstat -t -p 2231 1   # per thread (find the hot thread in a JVM)
pidstat -r 1           # memory: minflt/s majflt/s VSZ RSS %MEM
pidstat -d 1           # disk: kB_rd/s kB_wr/s kB_ccwr/s iodelay
pidstat -w 1           # context switches: cswch/s nvcswch/s
```

```text
10:45:11 AM   UID       PID    %usr %system  %guest   %wait    %CPU   CPU  Command
10:45:12 AM   995      2231  171.00   16.00    0.00    2.00  187.00     3  java
10:45:12 AM     0      9910   24.00    1.00    0.00   61.00   25.00     1  gzip
```

`%wait` is time the task was runnable but waiting for a CPU: a direct per-process saturation measure. In `pidstat -d`, `iodelay` is clock ticks spent blocked on block I/O, which points straight at the process suffering from (or causing) slow disk. High `nvcswch/s` (non-voluntary context switches) means the process keeps getting preempted, another sign of CPU saturation.

:::tip
`mpstat`, `pidstat`, `iostat` and `sar` all come from the **sysstat** package: `dnf install sysstat` (RHEL family) or `apt install sysstat` (Ubuntu/Debian). Install it on every server in your build, not during an incident.
:::

## 5. Disk I/O: iostat -xz

```bash
iostat -xz 1 3
```

```text
Device            r/s     rkB/s   rrqm/s  %rrqm r_await rareq-sz     w/s     wkB/s   wrqm/s  %wrqm w_await wareq-sz  aqu-sz  %util
nvme0n1          4.00     64.00     0.00   0.00    0.45    16.00   12.00    480.00     3.00  20.00    0.80    40.00    0.01   0.90
vdb             80.00   1280.00     0.00   0.00   38.20    16.00 1420.00   5680.00     0.00   0.00  142.60     4.00  205.40  99.80
```

`-x` gives extended statistics, `-z` hides idle devices. Add `-m` for MB, `-t` for timestamps, `-p ALL` for partitions, or name devices (`iostat -xz vdb 1`). Column names here are from sysstat 12 (RHEL 9). Older sysstat (RHEL 7) shows a combined `await` and a `svctm` column; `svctm` was removed because it was never reliable.

| Column | Meaning | How to read it |
|---|---|---|
| `r/s`, `w/s` | Read / write requests per second (IOPS) after merging | Compare with the device or cloud volume's IOPS limit |
| `rkB/s`, `wkB/s` | Throughput | Compare with the throughput limit |
| `rareq-sz`, `wareq-sz` | Average request size (KB) | 4 KB = small random I/O; 512 KB+ = large sequential |
| `r_await`, `w_await` | Average time per request **including queue time** (ms) | The latency your application feels |
| `aqu-sz` | Average queue length | Sustained high values = saturation |
| `%util` | Percent of time the device had at least one request in flight | See warning below |

Rough latency expectations: NVMe well under 1 ms, SATA SSD and good cloud SSD volumes around 1–5 ms, spinning disks 5–15 ms. In the sample, `vdb` is doing 1,420 small (4 KB) writes per second with a 142 ms average wait and a queue of 205: badly saturated. A cloud volume capped at, say, 1,500 IOPS looks exactly like this.

:::warn
**`%util` near 100% does not always mean the device is full.** It means "busy at least once during each moment". An HDD can only do one thing at a time, so 100% really is the limit. SSDs, NVMe, RAID arrays and SAN LUNs serve many requests in parallel, so they can show 100% `%util` with plenty of headroom. On those, trust **`await` and `aqu-sz`** (and the device's IOPS/throughput limits) over `%util`.
:::

### Which process is doing the I/O?

```bash
pidstat -d 1                    # sysstat, per process
iotop -o                        # dnf install iotop; -o = only active processes
cat /proc/2231/io               # cumulative read_bytes / write_bytes for one PID
```

`iotop` needs task delay accounting for some columns; on recent kernels it may print a warning and suggest `sysctl kernel.task_delayacct=1` (version-dependent).

## 6. Memory: free, page cache and pressure

```bash
free -h
```

```text
               total        used        free      shared  buff/cache   available
Mem:           7.5Gi       2.1Gi       312Mi        24Mi       5.4Gi       5.2Gi
Swap:          2.0Gi          0B       2.0Gi
```

| Column | Meaning |
|---|---|
| `used` | Memory used by processes and the kernel (excluding cache) |
| `free` | Memory not used for **anything** |
| `buff/cache` | Page cache, buffers and reclaimable slab: file data kept in RAM to speed up reads |
| `available` | Kernel's **estimate** of how much memory can be given to new work without swapping, counting reclaimable cache (from `MemAvailable` in `/proc/meminfo`) |

:::concept
**"Free memory is wasted memory."** Linux uses spare RAM as page cache. On a healthy server that has been up for weeks, `free` is small and `buff/cache` is large. That is good. When an application needs memory, the kernel drops clean cache pages. The number to watch is **`available`**. Alert on `available` dropping below about 10% of total, not on `free`.
:::

To see who uses memory:

```bash
ps aux --sort=-rss | head -10          # top resident memory users
pidstat -r 1 5                         # RSS over time: is it growing?
grep -E 'MemTotal|MemAvailable|Dirty|Shmem|Slab|SReclaimable|SUnreclaim|Committed_AS' /proc/meminfo
slabtop -o | head -15                  # kernel slab caches (dentry, inode, xfs_inode)
systemd-cgtop -m                       # memory per systemd unit/slice
```

Watch for memory that `free` hides from you: **`Shmem`** (tmpfs and shared memory: files in `/dev/shm` or a full `/run` count here and cannot be dropped like cache), large **`SUnreclaim`** slab (a kernel leak or a driver), and **`Committed_AS`** far above RAM plus swap (applications have been promised more than exists).

### Memory pressure and PSI

Pressure Stall Information (PSI) tells you directly how much time tasks lose waiting for a resource. It is available on kernels 4.20 and later.

```bash
cat /proc/pressure/memory
cat /proc/pressure/cpu
cat /proc/pressure/io
```

```text
some avg10=12.45 avg60=8.10 avg300=2.03 total=91827364
full avg10=4.20 avg60=2.88 avg300=0.71 total=31122001
```

`some` is the percentage of time at least one task was stalled on memory; `full` is time **all** non-idle tasks were stalled at once (the system did no useful work). Sustained `full` above a few percent means real memory trouble. Each cgroup has its own `memory.pressure` file too, for example `/sys/fs/cgroup/system.slice/myapp.service/memory.pressure`.

On RHEL 8 and some RHEL 9 builds PSI is compiled in but **disabled by default**; if `/proc/pressure` is missing or reading it fails with "Operation not supported", add the `psi=1` kernel argument (`grubby --update-kernel=ALL --args=psi=1`) and reboot. Check your exact kernel before relying on it.

### Swap and thrashing

Some swap use is normal: the kernel moves cold, rarely-touched pages out to make room for cache. **Thrashing** is when the working set does not fit in RAM and the system spends its time moving the same pages in and out.

```bash
vmstat 1                 # si and so both non-zero, every second
sar -W 1                 # pswpin/s pswpout/s
sar -B 1                 # majflt/s, pgscank/s, pgscand/s (page scanning)
cat /proc/sys/vm/swappiness
```

Signs: sustained `si` **and** `so`, rising `majflt/s`, high `wa`, high PSI memory `full`, interactive commands taking seconds to start. The fix is never "add more swap": either reduce memory use (fix the leak, lower JVM heap, fewer worker processes), cap the offender with a cgroup limit, or add RAM. Lowering `vm.swappiness` changes the balance between dropping cache and swapping anonymous memory; it does not create memory.

:::warn
**Don't run `echo 3 > /proc/sys/vm/drop_caches` as a "fix".** It throws away the page cache, makes `free` look nicer for a minute, and then every read goes to disk until the cache is rebuilt. It is a diagnostic or benchmarking tool, not a remedy.
:::

## 7. The OOM killer

When the kernel cannot free enough memory to satisfy an allocation (in the whole system, or inside a cgroup that has hit its limit), it invokes the **out-of-memory (OOM) killer**, which picks a process and sends it `SIGKILL`. The application gets no warning and writes no error of its own; it just disappears. Your job is to find the evidence.

### Finding OOM kills

```bash
journalctl -k --since "-2h" | grep -iE 'out of memory|oom-kill|killed process'
dmesg -T | grep -iE 'oom|killed process'
journalctl -u myapp.service -n 50       # systemd notes the reason
```

A global (whole system) OOM kill looks like this:

```text
kernel: java invoked oom-killer: gfp_mask=0x140cca(GFP_HIGHUSER_MOVABLE|__GFP_COMP), order=0, oom_score_adj=0
kernel: Mem-Info:
kernel: active_anon:1702233 inactive_anon:118921 isolated_anon:0 ...
kernel: oom-kill:constraint=CONSTRAINT_NONE,nodemask=(null),cpuset=/,mems_allowed=0,global_oom,task_memcg=/system.slice/myapp.service,task=java,pid=2231,uid=995
kernel: Out of memory: Killed process 2231 (java) total-vm:9876544kB, anon-rss:6801232kB, file-rss:0kB, shmem-rss:0kB, UID:995 pgtables:14212kB oom_score_adj:0
systemd[1]: myapp.service: A process of this unit has been killed by the OOM killer.
systemd[1]: myapp.service: Main process exited, code=killed, status=9/KILL
systemd[1]: myapp.service: Failed with result 'oom-kill'.
```

A cgroup (limit-based) OOM kill looks like this:

```text
kernel: python3 invoked oom-killer: gfp_mask=0xcc0(GFP_KERNEL), order=0, oom_score_adj=0
kernel: memory: usage 204800kB, limit 204800kB, failcnt 312
kernel: oom-kill:constraint=CONSTRAINT_MEMCG,nodemask=(null),cpuset=/,mems_allowed=0,oom_memcg=/system.slice/leaky.service,task_memcg=/system.slice/leaky.service,task=python3,pid=5120,uid=0
kernel: Memory cgroup out of memory: Killed process 5120 (python3) total-vm:221340kB, anon-rss:202112kB, file-rss:6200kB, shmem-rss:0kB, UID:0 pgtables:460kB oom_score_adj:0
```

How to read it:

| Part | What it tells you |
|---|---|
| `X invoked oom-killer` | The process that **asked** for memory at that moment. Often not the culprit, just unlucky. |
| `constraint=CONSTRAINT_NONE` / `global_oom` | The **whole system** ran out of memory |
| `constraint=CONSTRAINT_MEMCG`, `oom_memcg=` | A **cgroup** hit its limit (systemd `MemoryMax=`, a container limit). The rest of the system may have had plenty. |
| `task_memcg=` | Which systemd unit or container the victim belonged to |
| `Killed process PID (name)` | The **victim** |
| `anon-rss` | Private memory of the victim (heap, stacks): the usual leak indicator |
| `shmem-rss` | Shared memory/tmpfs pages |
| `oom_score_adj` | The victim's adjustment value (see below) |

The process table dump just before the kill (columns `pid uid tgid total_vm rss ... oom_score_adj name`) lists every candidate. If one process's `rss` dwarfs the rest, you have your leak.

### How the victim is chosen: oom_score and oom_score_adj

Each process has `/proc/PID/oom_score` (computed mostly from its memory use) and `/proc/PID/oom_score_adj` (an administrator bias from **-1000 to +1000**). The highest score is killed. `-1000` means "never kill this process".

```bash
for p in $(pgrep -d' ' -f 'java|postgres|sshd'); do
  printf "%-6s %-5s %-6s %s\n" "$p" "$(cat /proc/$p/oom_score)" "$(cat /proc/$p/oom_score_adj)" "$(tr '\0' ' ' < /proc/$p/cmdline | cut -c1-50)"
done
```

Set it the right way, in the unit file, so it survives restarts:

```ini
# systemctl edit postgresql.service
[Service]
OOMScoreAdjust=-500
```

:::warn
Do not set `-1000` on your main application to "protect" it. If the leaking process cannot be killed, the kernel kills everything else (sshd, journald, your monitoring agent) and the box becomes unreachable. Protect critical **infrastructure** (sshd is already adjusted on RHEL) and put a **memory limit** on the application instead.
:::

### Contain memory with cgroups: MemoryMax and MemoryHigh

RHEL 9 (and Ubuntu 22.04+) use **cgroup v2** by default. Every systemd service is its own cgroup, so you can cap it without touching the application.

| Directive | cgroup v2 file | Effect |
|---|---|---|
| `MemoryHigh=` | `memory.high` | Soft limit: above it the kernel **throttles** the cgroup and reclaims aggressively. Slows the service down, does not kill it. |
| `MemoryMax=` | `memory.max` | Hard limit: if reclaim fails, the **cgroup OOM killer** kills a process inside the unit |
| `MemorySwapMax=` | `memory.swap.max` | Limits swap the unit can use; set `0` so a leak fails fast instead of swapping the host into the ground |
| `OOMPolicy=` | (systemd) | What systemd does after an OOM kill in the unit: `stop` (default for services), `continue`, `kill` |

```ini
# /etc/systemd/system/myapp.service.d/memory.conf
[Service]
MemoryHigh=1536M
MemoryMax=2G
MemorySwapMax=0
```

```bash
systemctl daemon-reload
systemctl restart myapp.service
systemctl show myapp.service -p MemoryHigh -p MemoryMax -p MemoryCurrent
cat /sys/fs/cgroup/system.slice/myapp.service/memory.max
cat /sys/fs/cgroup/system.slice/myapp.service/memory.events   # high, max, oom, oom_kill counters
```

`systemctl set-property myapp.service MemoryMax=2G` applies the same change live and writes a persistent drop-in (add `--runtime` for a temporary change). On Fedora and Ubuntu desktops, **systemd-oomd** may kill whole cgroups early based on PSI; on RHEL 9 servers it is not enabled by default. If a unit was killed with `Failed with result 'oom-kill'` and there is no kernel OOM line, check `journalctl -u systemd-oomd`.

:::prod
**The report job that took out the API.** A nightly reporting service and a customer-facing API shared an 8 GB VM. The report query started loading a full year of data into memory. At 02:10 the global OOM killer fired and chose the API's JVM, the largest process, not the report job that caused the pressure. **What the engineer saw:** `journalctl -k` showed `global_oom` and `Killed process ... (java)`; the report job was at 3.1 GB `anon-rss` and climbing in the process table dump. **Fix:** `MemoryMax=3G` and `MemorySwapMax=0` on `report.service`, and the report job was changed to stream rows. **Prevention:** every service on shared hosts gets a memory limit; alerts on `memory.events` `oom_kill` increments.
:::

## 8. ulimits and "Too many open files"

Every socket, pipe, open file and epoll instance uses a **file descriptor**. Each process has a limit (`RLIMIT_NOFILE`); when it reaches it, `open()`, `socket()` and `accept()` fail with `EMFILE`, which applications log as **"Too many open files"**. For a web server or database this looks like random connection failures under load.

```bash
cat /proc/2231/limits | grep -i 'open files'      # the limit the RUNNING process has
ls /proc/2231/fd | wc -l                          # how many it has open right now
ls -l /proc/2231/fd | awk '{print $NF}' | sort | uniq -c | sort -rn | head   # what they are
lsof -p 2231 | wc -l                              # same idea; slower, includes more than fds
cat /proc/sys/fs/file-nr                          # system-wide: allocated  0  max
sysctl fs.file-max fs.nr_open
```

```text
Max open files            1024                 524288               files
```

The two numbers are the **soft** limit (enforced) and the **hard** limit (the ceiling an unprivileged process may raise its soft limit to). If `ls /proc/PID/fd | wc -l` is close to the soft limit, you have found the problem.

### Why limits.conf doesn't fix services

`/etc/security/limits.conf` and `/etc/security/limits.d/*.conf` are applied by **`pam_limits`** when a user **logs in** (SSH, `su`, console). Services started by systemd never go through PAM, so those files have **no effect** on them. `ulimit -n 65536` in your shell only affects that shell and its children.

For a service, set the limit in the unit:

```bash
systemctl edit myapp.service
```

```ini
[Service]
LimitNOFILE=65536
```

```bash
systemctl daemon-reload
systemctl restart myapp.service
systemctl show myapp.service -p LimitNOFILE -p LimitNOFILESoft
grep 'open files' /proc/$(systemctl show -p MainPID --value myapp.service)/limits
```

`LimitNOFILE=65536` sets soft and hard; `LimitNOFILE=16384:65536` sets them separately. On systemd 240 and later (RHEL 9 has systemd 252) the default for services is soft **1024**, hard **524288**, configurable globally with `DefaultLimitNOFILE=` in `/etc/systemd/system.conf`. Many runtimes (Java, Go, nginx with `worker_rlimit_nofile`) raise their own soft limit up to the hard limit at startup.

:::tip
Before raising the limit, check for a **descriptor leak**. If the fd count of a process grows steadily and never falls, raising the limit only delays the outage. `ls -l /proc/PID/fd` showing thousands of sockets in `CLOSE_WAIT` (confirm with `ss -tanp | grep CLOSE-WAIT`) means the application is not closing connections its peers have closed: a code bug.
:::

### SELinux and resource limits

SELinux can block a confined service from changing its own limits. The classic case is nginx or Apache with `worker_rlimit_nofile` (nginx) set: the worker tries `setrlimit()` and SELinux denies it.

```bash
ausearch -m AVC -ts recent -c nginx
```

```text
type=AVC msg=audit(1764062541.112:812): avc:  denied  { setrlimit } for  pid=3410 comm="nginx" scontext=system_u:system_r:httpd_t:s0 tcontext=system_u:system_r:httpd_t:s0 tclass=process permissive=0
```

```bash
ausearch -m AVC -ts recent -c nginx | audit2why     # reports: boolean httpd_setrlimit
setsebool -P httpd_setrlimit on
```

The correct fix is the boolean (persistent with `-P`), plus `LimitNOFILE=` in the unit so the hard limit is high enough. Do not disable SELinux to make the error go away. On Ubuntu, AppArmor profiles rarely restrict rlimits; the systemd fix is the same.

### Other limits that bite

| Limit | Symptom | Where to check / fix |
|---|---|---|
| `LimitNPROC=` / `TasksMax=` | "Resource temporarily unavailable" (`EAGAIN`) on `fork()` or thread creation; Java `unable to create native thread` | `systemctl show -p TasksMax -p TasksCurrent unit`; raise in the unit |
| `kernel.pid_max`, `kernel.threads-max` | System-wide fork failures | `sysctl kernel.pid_max kernel.threads-max` |
| `fs.file-max` | `VFS: file-max limit N reached` in `dmesg` | Rare on modern kernels (the default is very large) |
| `net.core.somaxconn` and the app's `listen()` backlog | Connection resets/timeouts under bursts; `ListenOverflows` in `nstat` | `ss -ltn` (`Recv-Q` vs `Send-Q` on listening sockets) |

## 9. Process states and zombies

```bash
ps -eo state,pid,ppid,user,etime,cmd --sort=state | head
ps -eo state | sort | uniq -c
```

| State | Meaning | Notes |
|---|---|---|
| `R` | Running or runnable | Counts in load |
| `S` | Interruptible sleep (waiting for an event: socket, timer) | Most processes, most of the time |
| `D` | Uninterruptible sleep, usually I/O | Counts in load; cannot be killed until I/O completes |
| `T` / `t` | Stopped (Ctrl-Z, `SIGSTOP`) / stopped by a debugger | A process someone left in `T` will look "hung" |
| `Z` | **Zombie**: exited, but the parent has not collected its exit status | Holds only a PID-table slot |
| `I` | Idle kernel thread | Not counted in load; ignore |

A **zombie** has already exited. It uses no CPU and no memory; you **cannot** kill it, because it is already dead. It disappears when its parent calls `wait()`. A few zombies are harmless. Thousands mean a parent process is buggy and you can run out of PIDs.

```bash
ps -eo stat,pid,ppid,cmd | awk '$1 ~ /^Z/'      # list zombies and their parent PIDs
ps -o pid,cmd -p <PPID>                          # who is the bad parent
```

Fixes, in order: report the bug to the application owner; send the parent `SIGCHLD` (`kill -s SIGCHLD <PPID>`), which sometimes prompts it to reap; restart the parent (its zombies are then re-parented to PID 1, which reaps them immediately). In containers, a missing init process (no `--init`/`tini`) is a common cause.

## 10. CPU steal in virtual machines

`st` (steal) is time your vCPU wanted to run but the hypervisor was running something else: another VM, or the host itself. It shows in `top`, `vmstat`, `mpstat %steal` and `sar -u`.

```bash
sar -u 1 5
mpstat 1 5 | awk 'NR<4 || /all/'
```

| Steal | Interpretation |
|---|---|
| 0–2% | Normal |
| Sustained 5–10% | Noticeable; latency-sensitive apps suffer |
| 10%+ or spikes to 30–50% | The VM is starved: overcommitted host, noisy neighbour, or a cloud burstable instance out of CPU credits |

You cannot fix steal from inside the guest. Fixes are on the platform side: move or resize the VM, change instance family (burstable "T"/"B" types throttle when credits run out), ask the virtualisation team to reduce overcommit, or use dedicated hosts. What you **can** do is prove it: `sar -u` history showing `%steal` rising at the same time as your latency is the evidence the platform team needs.

:::prod
**The slow app that wasn't.** A Java service's p99 latency doubled every weekday afternoon. Profiling showed nothing new; `us` stayed at 40%. `sar -u -f /var/log/sa/sa18` showed `%steal` climbing from 1% to 22% between 13:00 and 17:00. The VM shared an overcommitted hypervisor with a batch analytics cluster. **Fix:** the VM was moved to a different host group. **Prevention:** steal added as a monitored metric with an alert at 5% sustained.
:::

## 11. History with sar (sysstat)

Most performance tickets arrive after the problem has passed: "it was slow at 02:00". `sar` answers that, if sysstat collection was running.

```bash
dnf install -y sysstat
systemctl enable --now sysstat           # RHEL 9: enables sysstat-collect.timer (every 10 min) and sysstat-summary.timer
systemctl list-timers 'sysstat*'
ls /var/log/sa/                          # saDD binary files (and sarDD text summaries)
```

On Ubuntu/Debian, files are in `/var/log/sysstat/`; set `ENABLED="true"` in `/etc/default/sysstat` (on older releases) and enable the `sysstat` service. To sample more often than every 10 minutes, `systemctl edit sysstat-collect.timer` and override `OnCalendar=`. Retention is set by `HISTORY=` in `/etc/sysconfig/sysstat` (Ubuntu: `/etc/sysstat/sysstat`).

```bash
sar -u                                  # today's CPU, 10-minute samples
sar -u -f /var/log/sa/sa24              # the 24th of this month
sar -q -s 01:30:00 -e 03:00:00          # run queue and load, 01:30–03:00
sar -r                                  # memory: kbavail, %memused, kbcommit
sar -B                                  # paging: majflt/s, pgscank/s
sar -W                                  # swapping: pswpin/s pswpout/s
sar -d -p                               # per-disk (-p uses sda/vdb names): await, aqu-sz, %util
sar -n DEV                              # network per interface
sar -n EDEV                             # network errors and drops
sar -A -f /var/log/sa/sa24 > /tmp/sar-24.txt   # everything, for attaching to a ticket
```

```text
01:40:01 AM   runq-sz  plist-sz   ldavg-1   ldavg-5  ldavg-15   blocked
01:50:01 AM         2       612      1.84      1.92      1.88         0
02:00:01 AM         3       640      9.71      5.22      2.83        14
02:10:01 AM         1       622     14.02     11.40      6.97        21
```

Here load jumped at 02:00 while the run queue stayed small and `blocked` went to 14–21: an I/O-bound event, so the next command is `sar -d -p -s 01:50:00 -e 02:30:00`.

:::tip
Because sar samples are 10-minute **averages**, a 30-second spike can almost disappear. Use sar to find the window and the resource, then use live tools (or a higher-resolution agent such as Performance Co-Pilot, `dnf install pcp-zeroconf`, which records at 10-second resolution by default) for detail.
:::

## 12. Network saturation

```bash
sar -n DEV 1 5
```

```text
10:51:02 AM     IFACE   rxpck/s   txpck/s    rxkB/s    txkB/s   rxcmp/s   txcmp/s  rxmcst/s   %ifutil
10:51:03 AM      eth0  81022.00  79310.00 117420.33 116204.12      0.00      0.00      0.00     96.19
10:51:03 AM        lo     12.00     12.00      1.02      1.02      0.00      0.00      0.00      0.00
```

`%ifutil` is computed from the link speed the driver reports (`ethtool eth0 | grep Speed`). On virtual NICs the reported speed may not match the real cloud bandwidth limit, so compare `rxkB/s`/`txkB/s` with the instance's documented network limit. Here ~117 MB/s each way on a 1 Gbit/s link is saturated.

```bash
sar -n EDEV 1 5              # rxerr/s txerr/s rxdrop/s txdrop/s
ip -s link show eth0         # cumulative errors, dropped, overruns
ethtool -S eth0 | grep -iE 'drop|err|miss|fifo'
nstat -az | grep -iE 'Retrans|ListenOverflow|ListenDrops|TCPTimeouts'
ss -s
```

```text
Total: 18342
TCP:   18210 (estab 1820, closed 16180, orphaned 12, timewait 16150)

Transport Total     IP        IPv6
RAW	  1         0         1
UDP	  8         5         3
TCP	  2030      2010      20
INET	  2039      2015      24
FRAG	  0         0         0
```

`ss -s` is a quick summary. 16,150 sockets in `TIME-WAIT` is a sign of many short-lived outbound connections (an application not using connection pooling); thousands of `CLOSE-WAIT` (`ss -tan state close-wait | wc -l`) point at an application not closing sockets. `ss -tin dst 10.0.0.30` shows per-connection RTT, congestion window and retransmits. Network troubleshooting is covered in depth in its own module; for performance, the USE questions are: is the link at its limit, are packets queued or dropped, and are TCP retransmits rising.

## 13. Looking inside a process: strace and perf

### strace basics

`strace` shows the system calls a process makes. It answers "what is it doing right now?" for a process that seems hung or slow.

```bash
strace -f -tt -T -p 2231 -o /tmp/strace.2231.txt   # attach, follow threads, timestamps, time per syscall
strace -c -f -p 2231                               # Ctrl-C after ~10 s: summary table of syscalls
strace -f -e trace=openat,connect -p 2231          # only file opens and connections
strace -f -e trace=%file -o /tmp/s.txt /opt/myapp/bin/start   # start a program under strace
```

What to look for: a call that takes seconds (`<5.003012>` at the end of the line with `-T`) on `connect`, `read` or `futex`; repeated `EMFILE`, `ENOENT` or `EACCES` errors; a loop of the same calls (busy polling). `-c` gives a table of call counts, errors and time, which is the fastest way to see where the time goes.

:::warn
`strace` uses ptrace and can slow a busy process **dramatically** (often 10x or more). On production, attach for seconds, prefer `-c` or a narrow `-e trace=`, and detach (Ctrl-C). For low-overhead tracing, use `perf trace` or bpftrace/bcc tools instead.
:::

**SELinux note:** if `strace -p` fails as root with `Operation not permitted`, check the `deny_ptrace` boolean: `getsebool deny_ptrace`. Hardened systems set it `on` to block ptrace for everyone. Turn it off only for the debugging session (`setsebool deny_ptrace off`, no `-P`) and back on afterwards. On Ubuntu the equivalent control is `kernel.yama.ptrace_scope`.

### perf top

`perf` (`dnf install perf`) samples where CPU time is spent, down to function level, with very low overhead.

```bash
perf top                    # live, system-wide hottest functions
perf top -p 2231            # one process
perf record -g -p 2231 -- sleep 30 && perf report   # 30 s profile with call graphs
```

If `perf top` shows kernel functions such as `_raw_spin_lock` or `copy_user_*`, the time is in the kernel (high `sy`); if it shows hex addresses for a JIT runtime, you need symbol support (for Java, a perf map agent or async-profiler). You don't need to be a perf expert as an on-call engineer; "the CPU is spent in function X of library Y" is often enough to hand the problem to the right team.

### SELinux overhead you might actually see

SELinux itself has small, constant overhead. What does cause CPU spikes is an **AVC denial flood**: a misconfigured service retrying a denied action thousands of times per second makes `auditd` and (if installed) `setroubleshootd` busy. If `top` shows `auditd` or `setroubleshootd` high, run `ausearch -m AVC -ts recent | tail` and fix the denial properly (`audit2why`, then the right `semanage fcontext`/`restorecon`, `semanage port` or `setsebool -P`). Do not stop `auditd` or set SELinux to permissive as the fix.

## 14. Application memory leaks: Java and Python

The OS tools tell you **that** a process is leaking (RSS grows steadily and never falls back, `pidstat -r` over hours, `sar -r` `kbavail` falling day by day until an OOM). The application tools tell you **where**.

### Java

A JVM has a **heap** (sized by `-Xmx`, or by default a fraction of RAM or of the container/cgroup limit on modern JDKs) plus off-heap memory: metaspace, thread stacks, code cache, direct buffers, native libraries. That gives two different failures:

| Failure | Where you see it | Meaning |
|---|---|---|
| `java.lang.OutOfMemoryError: Java heap space` | Application log; the JVM keeps running (or exits if configured) | Heap full: a leak or `-Xmx` too small for the load |
| Kernel or cgroup **OOM kill** of `java` | `journalctl -k`; systemd `oom-kill`; no Java error at all | **Total** process memory (heap + off-heap) exceeded RAM or `MemoryMax=` |

Useful commands, run as the application user (`sudo -u myapp ...`):

```bash
jcmd 2231 GC.heap_info                 # heap regions and use
jstat -gcutil 2231 5s                  # GC: old gen % climbing and full GCs not freeing it = leak
jcmd 2231 VM.native_memory summary     # only if started with -XX:NativeMemoryTracking=summary
jcmd 2231 GC.heap_dump /var/tmp/myapp.hprof   # large file, pauses the JVM; analyse offline
```

Production JVM settings worth having: `-XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=/var/tmp`, `-Xmx` set well below `MemoryMax=` (leave room for off-heap, typically 25% or more), and GC logging (`-Xlog:gc*`).

### Python

CPython rarely returns freed memory to the OS, so RSS stays high after a peak; that is not a leak. A leak is RSS that keeps **climbing** under steady load. Common causes: module-level caches and lists that grow forever, unbounded `functools.lru_cache(maxsize=None)`, reference cycles held by globals, C extensions leaking.

- `tracemalloc` (standard library) snapshots allocations by source line; compare two snapshots minutes apart.
- For gunicorn/uWSGI web workers, `max_requests` (with jitter) recycles workers periodically. It is a mitigation, not a fix, but it stops a slow leak from becoming an outage.
- `pmap -x PID | tail -1` and `/proc/PID/smaps_rollup` show the RSS breakdown for any process.

:::tip
For any leak, the production plan is the same: **contain** (cgroup `MemoryMax=` so it can only kill itself, and an automatic restart policy), **capture** evidence (RSS trend graph, heap dump or tracemalloc output), **hand over** to developers with the data. Raising the memory limit every week is not a plan.
:::

## 15. Containing a noisy process: CPU and I/O controls

### CPU

| Tool | Effect |
|---|---|
| `renice -n 10 -p PID` | Lower priority; only matters when there is contention |
| `CPUQuota=50%` | Hard cap: at most half a CPU (`200%` = two CPUs) per 100 ms period (cgroup `cpu.max`) |
| `CPUWeight=` | Relative share under contention (1–10000, default 100) (cgroup `cpu.weight`) |
| `AllowedCPUs=` | Pin the unit to specific CPUs (cgroup `cpuset.cpus`) |

```bash
systemctl set-property backup.service CPUQuota=50%      # live and persistent (drop-in)
systemctl set-property --runtime backup.service CPUQuota=50%   # live only, until reboot
systemd-run --unit=adhoc-report -p CPUQuota=30% -p MemoryMax=1G /opt/myapp/bin/report.sh
systemd-cgtop                                           # live CPU, memory, I/O per cgroup
cat /sys/fs/cgroup/system.slice/backup.service/cpu.stat # nr_throttled, throttled_usec
```

A high `nr_throttled` count in `cpu.stat` is how you detect that a quota (yours, or a container platform's CPU limit) is slowing a service down.

### I/O

```ini
[Service]
IOWeight=50
IOReadBandwidthMax=/dev/vdb 50M
IOWriteBandwidthMax=/dev/vdb 50M
IOWriteIOPSMax=/dev/vdb 500
```

These map to `io.weight` and `io.max` in cgroup v2. They apply reliably to direct I/O; buffered writes are attributed through cgroup writeback, which depends on the filesystem (XFS and ext4 support it on RHEL 9 kernels). Name the whole disk device (check with `lsblk`), not a partition.

:::warn
Limiting is a containment measure that buys time. Always ask why the process needs that much CPU, memory or I/O in the first place, and record the limit in configuration management (an Ansible role, not a hand edit) so it survives a rebuild.
:::

## 16. Troubleshooting runbook

| Symptom | Check (command) | Likely cause | Fix |
|---|---|---|---|
| High load, CPU busy | `vmstat 1` (`r` > nproc), `pidstat 1`, `mpstat -P ALL 1` | CPU-bound process or too many workers | Find the process; optimise, scale out, or cap with `CPUQuota=` |
| High load, CPU idle | `ps -eo state,pid,wchan:32,cmd \| awk '$1=="D"'`, `dmesg -T` | D-state tasks: hung NFS/iSCSI, failing disk | Fix storage; use timeouts in monitoring scripts |
| One core at 100%, others idle | `mpstat -P ALL 1`, `pidstat -t -p PID 1` | Single-threaded bottleneck, or all IRQs on one CPU (`%soft`) | Parallelise; check `irqbalance` and `/proc/interrupts` |
| High `sy` | `pidstat -w 1`, `perf top`, `strace -c -p PID` | Syscall storm, lock contention, context switching | Fix the hot loop; reduce threads |
| App slow, `st` > 5% | `sar -u`, `mpstat 1` | Hypervisor overcommit, burst credits exhausted | Platform: move/resize VM, change instance type |
| Slow disk / high `wa` | `iostat -xz 1` (`await`, `aqu-sz`), `pidstat -d 1` | Saturated volume, noisy batch job, IOPS cap | Throttle the job (`IOWriteBandwidthMax=`), move it, upgrade volume |
| Process killed, no app error | `journalctl -k \| grep -i 'killed process'`, `journalctl -u unit` | Kernel or cgroup OOM kill | Find the leak; set `MemoryMax=`; size heap properly |
| Everything slow, disk busy, swap used | `vmstat 1` (`si`/`so`), `sar -W`, `/proc/pressure/memory` | Swap thrashing | Reduce memory use, cap offender, add RAM |
| "Too many open files" | `grep 'open files' /proc/PID/limits`, `ls /proc/PID/fd \| wc -l` | Soft limit 1024, or fd leak | `LimitNOFILE=` drop-in; fix leak (`CLOSE_WAIT`) |
| Web server can't raise its limit | `ausearch -m AVC -c nginx` shows `setrlimit` denied | SELinux policy | `setsebool -P httpd_setrlimit on` |
| `fork: Resource temporarily unavailable` | `systemctl show -p TasksMax -p TasksCurrent unit` | `TasksMax=`/`LimitNPROC=` reached, or runaway forking | Raise limit in unit; fix fork loop |
| Many `Z` processes | `ps -eo stat,pid,ppid,cmd \| awk '$1~/^Z/'` | Parent not reaping children | Restart/fix parent; add init in containers |
| NIC at line rate / drops | `sar -n DEV 1`, `sar -n EDEV 1`, `ethtool -S` | Link saturated, ring buffer overflow | Bigger link/instance, rate-limit, tune ring buffers |
| `strace` fails as root | `getsebool deny_ptrace` | SELinux blocks ptrace | `setsebool deny_ptrace off` for the session only, then back on |
| "It was slow at 02:00" | `sar -q -u -r -d -p -f /var/log/sa/saDD -s 01:30:00 -e 03:00:00` | Anything; find the resource | Follow the matching row above |

## 17. Break-fix labs

All labs run on a **disposable** Rocky Linux 9 or AlmaLinux 9 VM (2 vCPUs, 2–4 GB RAM, with a second small data disk if possible). Run them as root (or with `sudo`). SELinux stays **enforcing** throughout.

```bash
dnf install -y sysstat stress-ng fio iotop python3
systemctl enable --now sysstat
getenforce        # Enforcing
nproc; free -h; lsblk
```

`stress-ng` and `fio` are in AppStream on RHEL 9 and its rebuilds; if your mirror does not have `stress-ng`, enable EPEL (`dnf install epel-release`).

:::lab Lab 05-A — Find a CPU hog and cap it with CPUQuota
**Goal:** create a runaway CPU consumer as a systemd service, find it with the USE checks, and limit it to 20% of one CPU without killing it.

**Break it.** Create a service that burns all CPUs:

```bash
cat > /etc/systemd/system/cpuhog.service <<'EOF'
[Unit]
Description=Lab 05-A CPU hog

[Service]
ExecStart=/usr/bin/stress-ng --cpu 0 --cpu-method matrixprod --timeout 0
EOF
systemctl daemon-reload
systemctl start cpuhog.service
```

`--cpu 0` starts one worker per online CPU; `--timeout 0` runs forever.

**Symptoms.** The shell feels sluggish, `uptime` load climbs towards the CPU count, and `top` shows `us` near 100%.

**Diagnose.**

```bash
uptime
vmstat 1 5                      # r >= nproc, us ~ 99, id ~ 0, wa 0
mpstat -P ALL 1 2               # every CPU busy, not just one
pidstat 1 3                     # stress-ng-cpu workers at ~100% each
systemd-cgtop -n 3              # system.slice/cpuhog.service at the top
systemctl status $(ps -o unit= -p $(pgrep -f stress-ng | head -1))
```

The last command maps a PID to its unit (`ps -o unit=`), which is how you find which service owns a process you did not start.

**Fix.** Cap the unit at 20% of one CPU, live:

```bash
systemctl set-property --runtime cpuhog.service CPUQuota=20%
```

**Verify.**

```bash
systemctl show cpuhog.service -p CPUQuotaPerSecUSec      # 200ms
cat /sys/fs/cgroup/system.slice/cpuhog.service/cpu.max   # 20000 100000
pidstat -C stress-ng 1 3        # combined %CPU of the workers ~ 20
grep throttled /sys/fs/cgroup/system.slice/cpuhog.service/cpu.stat   # nr_throttled increasing
vmstat 1 3                      # id back near 90% on a 2-CPU VM
```

To make a cap permanent for a real service, drop `--runtime` (systemd writes a drop-in under `/etc/systemd/system.control/`) or, better, put `CPUQuota=` in a drop-in managed by your configuration tool.

**Clean-up.**

```bash
systemctl stop cpuhog.service
rm -f /etc/systemd/system/cpuhog.service
rm -rf /etc/systemd/system.control/cpuhog.service.d /run/systemd/system.control/cpuhog.service.d
systemctl daemon-reload
```
:::

:::lab Lab 05-B — A memory leak, a cgroup OOM kill, and reading the evidence
**Goal:** run a leaking Python service with a memory limit, watch memory grow, let the cgroup OOM killer kill it, and interpret every log line.

**Break it.** Create a "leaky" application under `/opt/myapp` (the default SELinux label for `/opt` content, `usr_t`, is fine for a script run by systemd):

```bash
mkdir -p /opt/myapp
cat > /opt/myapp/leak.py <<'EOF'
import time
hoard = []
while True:
    hoard.append(bytearray(10 * 1024 * 1024))   # 10 MiB per second, never freed
    print(f"holding {len(hoard) * 10} MiB", flush=True)
    time.sleep(1)
EOF
cat > /etc/systemd/system/leaky.service <<'EOF'
[Unit]
Description=Lab 05-B leaky app

[Service]
ExecStart=/usr/bin/python3 /opt/myapp/leak.py
MemoryMax=200M
MemorySwapMax=0
Restart=no
EOF
systemctl daemon-reload
systemctl start leaky.service
```

**Symptoms.** After about 20 seconds the service is dead. Nothing in the application's own output explains why: the last line is just `holding 190 MiB`.

**Diagnose.** While it runs (start it again if needed), watch memory grow:

```bash
watch -n1 'systemctl show leaky.service -p MemoryCurrent; cat /sys/fs/cgroup/system.slice/leaky.service/memory.events'
pidstat -r -C python3 1          # RSS climbing 10 MB per second
```

After it dies:

```bash
systemctl status leaky.service
journalctl -u leaky.service -n 20 --no-pager
journalctl -k -n 60 --no-pager | grep -iE 'oom|killed process|memory:'
```

Expected key lines:

```text
leaky.service: A process of this unit has been killed by the OOM killer.
leaky.service: Main process exited, code=killed, status=9/KILL
leaky.service: Failed with result 'oom-kill'.
kernel: memory: usage 204800kB, limit 204800kB, failcnt ...
kernel: oom-kill:constraint=CONSTRAINT_MEMCG,...,oom_memcg=/system.slice/leaky.service,task_memcg=/system.slice/leaky.service,task=python3,pid=...
kernel: Memory cgroup out of memory: Killed process ... (python3) total-vm:..., anon-rss:~200000kB, ...
```

Interpret them: `CONSTRAINT_MEMCG` and `Memory cgroup out of memory` mean the **unit's limit** was hit, not the host's RAM (confirm `free -h` shows plenty available). `oom_memcg` names the cgroup, `anon-rss` near the limit shows private heap memory was the cause, and `status=9/KILL` is the `SIGKILL`. `memory.events` shows `oom_kill 1`.

**Fix.** In real life the fix is in the code (stop hoarding) plus a sensible limit and restart policy. For the lab, add a soft limit to see throttling before the kill, and an automatic restart:

```bash
mkdir -p /etc/systemd/system/leaky.service.d
cat > /etc/systemd/system/leaky.service.d/override.conf <<'EOF'
[Service]
MemoryHigh=150M
Restart=on-failure
RestartSec=5
EOF
systemctl daemon-reload
systemctl restart leaky.service
```

**Verify.** `memory.events` now shows the `high` counter rising (the kernel is throttling and reclaiming above 150M) and growth slows before the kill at 200M; `systemctl status` shows the service restarting after each OOM kill (`journalctl -u leaky.service` shows "Scheduled restart job"). Restart-on-OOM keeps a service available while developers fix the leak, at the cost of periodic resets that you must alert on.

**Clean-up.**

```bash
systemctl stop leaky.service
rm -rf /etc/systemd/system/leaky.service /etc/systemd/system/leaky.service.d /opt/myapp/leak.py
systemctl daemon-reload
systemctl reset-failed
```
:::

:::lab Lab 05-C — "Too many open files": fix it with a systemd override
**Goal:** reproduce `EMFILE` in a service, prove that `limits.conf` and `ulimit` don't help, and fix it properly with a `LimitNOFILE=` drop-in.

**Break it.**

```bash
mkdir -p /opt/myapp
cat > /opt/myapp/fdapp.py <<'EOF'
import time, resource
soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
print(f"limits soft={soft} hard={hard}", flush=True)
files = []
try:
    for i in range(4000):
        files.append(open("/dev/null"))
    print(f"opened {len(files)} files OK", flush=True)
except OSError as e:
    print(f"FAILED after {len(files)} files: {e}", flush=True)
time.sleep(3600)
EOF
cat > /etc/systemd/system/fdapp.service <<'EOF'
[Unit]
Description=Lab 05-C fd-hungry app

[Service]
ExecStart=/usr/bin/python3 /opt/myapp/fdapp.py
EOF
systemctl daemon-reload
systemctl start fdapp.service
```

The script deliberately does **not** raise its own soft limit, like many real applications.

**Symptoms.**

```bash
journalctl -u fdapp.service -n 5 --no-pager
```

```text
python3[6102]: limits soft=1024 hard=524288
python3[6102]: FAILED after 1021 files: [Errno 24] Too many open files: '/dev/null'
```

(1021, not 1024, because stdin, stdout and stderr already use three descriptors.)

**Diagnose.**

```bash
PID=$(systemctl show -p MainPID --value fdapp.service)
grep 'open files' /proc/$PID/limits
ls /proc/$PID/fd | wc -l          # ~1024: at the soft limit
ulimit -n                         # your shell's limit; irrelevant to the service
```

Now try the wrong fix, to see why it is wrong:

```bash
echo '* soft nofile 65536' > /etc/security/limits.d/90-lab.conf
systemctl restart fdapp.service
journalctl -u fdapp.service -n 2 --no-pager      # still soft=1024
```

`pam_limits` only applies to login sessions; systemd services never see it.

**Fix.**

```bash
rm -f /etc/security/limits.d/90-lab.conf
systemctl edit fdapp.service
```

In the editor, add between the comment markers, then save:

```ini
[Service]
LimitNOFILE=8192
```

(Non-interactive alternative: create `/etc/systemd/system/fdapp.service.d/override.conf` with the same content, then `systemctl daemon-reload`.)

```bash
systemctl restart fdapp.service
```

**Verify.**

```bash
journalctl -u fdapp.service -n 2 --no-pager      # limits soft=8192 hard=8192 / opened 4000 files OK
systemctl show fdapp.service -p LimitNOFILE -p LimitNOFILESoft
grep 'open files' /proc/$(systemctl show -p MainPID --value fdapp.service)/limits
systemctl cat fdapp.service                       # shows the unit and the drop-in
```

**Clean-up.**

```bash
systemctl stop fdapp.service
rm -rf /etc/systemd/system/fdapp.service /etc/systemd/system/fdapp.service.d /opt/myapp/fdapp.py
systemctl daemon-reload
```
:::

:::lab Lab 05-D — Disk I/O bottleneck with fio, dd and iostat
**Goal:** saturate a disk with random writes, identify the device, the process and the latency, then throttle the job with a cgroup I/O limit.

Use a second data disk if you have one (here `/dev/vdb` mounted on `/data`); otherwise use `/var/tmp` on the root disk and the root disk's device name from `lsblk`. Keep at least 2 GB free.

**Break it.** Start a sustained random-write load as a transient unit so it can be managed like a service:

```bash
mkdir -p /data/fio
systemd-run --unit=iohog fio --name=randwrite --directory=/data/fio --size=1G \
  --rw=randwrite --bs=4k --direct=1 --ioengine=libaio --iodepth=32 --numjobs=2 \
  --runtime=600 --time_based --group_reporting
```

In a second terminal, add a sequential writer with `dd` (direct I/O, bypassing the page cache so you see real device speed):

```bash
dd if=/dev/zero of=/data/dd.test bs=1M count=1024 oflag=direct status=progress
```

**Symptoms.** `dd` is much slower than the disk's normal sequential speed; `ls` on `/data` hesitates; load average rises while CPU is mostly idle.

**Diagnose.**

```bash
uptime
vmstat 1 5                 # b > 0, wa elevated, id high
iostat -xz 1 5             # vdb: high w/s, wareq-sz ~4, w_await rising, aqu-sz high, %util ~100
pidstat -d 1 3             # fio processes with the highest kB_wr/s and iodelay
iotop -o -b -n 3           # same, per thread
cat /proc/pressure/io      # if PSI is enabled: some/full rising
systemd-cgtop -n 3         # iohog.service with high IO columns
```

Read `iostat`: thousands of 4 KB writes per second with `aqu-sz` in the tens and `w_await` far above the device's normal latency mean the device is saturated by small random writes. `dd`'s large sequential writes are stuck in the same queue, which is why they are slow.

**Fix.** Throttle the noisy job, live, without stopping it. Find the whole-disk device with `lsblk` (here `vdb`):

```bash
systemctl set-property --runtime iohog.service "IOWriteIOPSMax=/dev/vdb 300"
```

**Verify.**

```bash
cat /sys/fs/cgroup/system.slice/iohog.service/io.max    # 252:16 rbps=max wbps=max riops=max wiops=300
iostat -xz vdb 1 5          # w/s drops to ~300 plus dd's writes; w_await and aqu-sz fall
dd if=/dev/zero of=/data/dd.test bs=1M count=1024 oflag=direct status=progress   # faster again
```

(The `MAJ:MIN` numbers vary by system; `lsblk -o NAME,MAJ:MIN` shows yours.)

**Clean-up.**

```bash
systemctl stop iohog.service
systemctl reset-failed iohog.service 2>/dev/null
rm -rf /data/fio /data/dd.test
```

In production, the same pattern applies to backup jobs, log compression, database dumps and antivirus scans: give them `IOWeight=` and bandwidth/IOPS limits in their unit files and schedule them away from peak hours.
:::

## Command cheat sheet

| Task | Command |
|---|---|
| Load average and trend | `uptime` |
| CPU count | `nproc` |
| Recent kernel events (OOM, I/O errors) | `dmesg -T \| tail -50` or `journalctl -k -n 50` |
| System-wide CPU/memory/swap/IO each second | `vmstat 1 5` |
| Per-CPU utilisation | `mpstat -P ALL 1 3` |
| Per-process CPU / memory / disk | `pidstat 1`, `pidstat -r 1`, `pidstat -d 1` |
| Per-thread CPU | `pidstat -t -p PID 1`, `top -H -p PID` |
| Disk latency and queue | `iostat -xz 1` |
| Who does disk I/O | `iotop -o`, `pidstat -d 1` |
| Memory overview | `free -h`, `grep MemAvailable /proc/meminfo` |
| Top memory users | `ps aux --sort=-rss \| head` |
| Pressure stall info | `cat /proc/pressure/{cpu,memory,io}` |
| Swap activity | `vmstat 1` (`si`/`so`), `sar -W 1` |
| Find OOM kills | `journalctl -k \| grep -i 'killed process'` |
| OOM score of a process | `cat /proc/PID/oom_score /proc/PID/oom_score_adj` |
| Limit a service's memory | `systemctl set-property unit MemoryMax=2G` |
| Limit a service's CPU | `systemctl set-property unit CPUQuota=50%` |
| Limit a service's disk writes | `systemctl set-property unit "IOWriteBandwidthMax=/dev/vdb 50M"` |
| Resource use per unit | `systemd-cgtop` |
| Which unit owns a PID | `ps -o unit= -p PID` or `systemctl status PID` |
| Open-file limit of a running process | `grep 'open files' /proc/PID/limits` |
| Open fds of a process | `ls /proc/PID/fd \| wc -l` |
| Raise a service's fd limit | `systemctl edit unit` → `[Service]` `LimitNOFILE=65536` |
| D-state processes | `ps -eo state,pid,wchan:32,cmd \| awk '$1=="D"'` |
| Zombies and parents | `ps -eo stat,pid,ppid,cmd \| awk '$1~/^Z/'` |
| Historical CPU, load, memory, disk | `sar -u`, `sar -q`, `sar -r`, `sar -d -p` (`-f /var/log/sa/saDD`) |
| Network throughput / errors | `sar -n DEV 1`, `sar -n EDEV 1` |
| Socket summary | `ss -s` |
| Syscall summary of a process | `strace -c -f -p PID` (seconds only) |
| CPU hot functions | `perf top -p PID` |
| SELinux denials related to limits | `ausearch -m AVC -ts recent \| audit2why` |
| Allow web server to raise rlimits | `setsebool -P httpd_setrlimit on` |

:::summary
- Use **USE**: for each resource, check Utilization, Saturation (queues) and Errors. Saturation is where the pain is.
- Linux **load average** counts runnable **and** D-state tasks. High load with idle CPU means I/O waits or hung storage, not a CPU problem. Compare load with `nproc`.
- `top` fields: `us` app code, `sy` kernel, `wa` idle-while-waiting-for-I/O (a weak signal), `st` stolen by the hypervisor.
- `vmstat`: `r` > CPUs = CPU saturation; `b` > 0 = I/O waits; sustained `si`+`so` = thrashing. Ignore the first line.
- `iostat -xz`: latency is `r_await`/`w_await`, saturation is `aqu-sz`; `%util` is only a hard limit on single-queue devices like HDDs.
- `free -h`: watch **available**, not free. Page cache is good. PSI (`/proc/pressure/*`) measures stall time directly.
- **OOM kills** are logged by the kernel (`Killed process`) and by systemd (`Failed with result 'oom-kill'`). `CONSTRAINT_MEMCG` means a cgroup limit was hit. Contain services with `MemoryHigh=`/`MemoryMax=`/`MemorySwapMax=`; bias victims with `OOMScoreAdjust=`.
- **"Too many open files"**: check `/proc/PID/limits` and `/proc/PID/fd`. `limits.conf` does not apply to systemd services; use `LimitNOFILE=` in a drop-in. Check for fd leaks (`CLOSE_WAIT`).
- Zombies are already dead; fix the parent. D-state processes can't be killed; fix the I/O.
- Steal above ~5% is a platform problem; prove it with `sar -u` history.
- Install and enable **sysstat** everywhere so `sar` can answer "what happened at 02:00".
- `strace` is powerful but slow; attach briefly. `perf top` is low overhead.
- SELinux: `httpd_setrlimit` for web servers raising limits, `deny_ptrace` blocking strace, AVC floods burning CPU. Fix with booleans and proper policy, never by disabling SELinux.
- Contain with cgroups (`CPUQuota=`, `MemoryMax=`, `IOWriteBandwidthMax=`), then find the root cause.
:::

## Self-check questions

<div class="q" markdown="1">

**Q1.** A 4-vCPU server shows load average 38.5. `top` shows 94% idle and 0.5% `wa`. What do you check first?

A. Add more vCPUs, because load is ten times the CPU count
B. List processes in D state and check `dmesg` for storage or NFS errors
C. Restart the application with the highest `%CPU`
D. Increase `vm.swappiness`

:::answer
**Answer: B.** On Linux, load includes tasks in uninterruptible sleep. High load with idle CPUs points to tasks stuck on I/O, typically a hung NFS/iSCSI mount or a failing disk. `ps -eo state,pid,wchan:32,cmd | awk '$1=="D"'` and `dmesg -T` confirm it.

- A is wrong: the CPUs are idle; more of them changes nothing.
- C is wrong: no process is CPU-bound, and restarting won't free D-state tasks.
- D is wrong: nothing indicates a memory problem.
:::
</div>

<div class="q" markdown="1">

**Q2.** A monitoring alert fires because "free memory" on a database server is 180 MB out of 64 GB. `free -h` shows `available` at 41 GB, swap used 0, and `vmstat` shows `si`/`so` at 0. What is the correct response?

A. Drop caches with `echo 3 > /proc/sys/vm/drop_caches`
B. Add swap space immediately
C. Nothing is wrong; change the alert to use `available` (MemAvailable) instead of `free`
D. Restart the database to release memory

:::answer
**Answer: C.** Linux fills spare RAM with page cache, so `free` is naturally low. `available` (41 GB) shows memory that can be reclaimed on demand, and there is no swapping. The alert is measuring the wrong thing.

- A is wrong: dropping caches makes performance worse until the cache refills.
- B is wrong: there is no memory pressure.
- D is wrong: it causes an outage and loses the database's warm cache.
:::
</div>

<div class="q" markdown="1">

**Q3.** A service stopped overnight. `journalctl -u myapp` shows `Failed with result 'oom-kill'`. The kernel log line reads `oom-kill:constraint=CONSTRAINT_MEMCG,...,oom_memcg=/system.slice/myapp.service`. `sar -r` shows the host had 9 GB available at the time. What happened?

A. The host ran out of RAM and the kernel chose myapp at random
B. myapp exceeded its own cgroup memory limit (for example `MemoryMax=`) and was killed inside its cgroup
C. SELinux killed the process for an AVC denial
D. systemd killed it because `TimeoutStopSec=` expired

:::answer
**Answer: B.** `CONSTRAINT_MEMCG` with `oom_memcg` naming the unit means the cgroup's limit was hit, while the host still had memory. Check `systemctl show myapp -p MemoryMax` and investigate why the app grew (a leak, or a limit set too low).

- A is wrong: a global OOM would show `CONSTRAINT_NONE`/`global_oom`, and the host had free memory.
- C is wrong: SELinux denies operations; it does not kill processes for memory.
- D is wrong: a stop timeout produces a different result (`timeout`), not `oom-kill`.
:::
</div>

<div class="q" markdown="1">

**Q4.** An nginx service logs "Too many open files" under load. You added `nginx soft nofile 65536` to `/etc/security/limits.conf` and restarted nginx, but `/proc/<pid>/limits` still shows 1024. Why?

A. limits.conf requires a reboot
B. `pam_limits` applies to login sessions, not to services started by systemd; set `LimitNOFILE=` in a unit drop-in
C. The kernel `fs.file-max` is 1024
D. nginx ignores all resource limits

:::answer
**Answer: B.** systemd services do not pass through PAM, so limits.conf has no effect on them. Use `systemctl edit nginx` with `[Service] LimitNOFILE=65536`, then restart. If nginx raises its own limit via `worker_rlimit_nofile`, also check for an SELinux `setrlimit` denial and enable `httpd_setrlimit`.

- A is wrong: a reboot changes nothing; the file is still not used for services.
- C is wrong: `fs.file-max` is a system-wide limit, very large by default, and would not appear as the per-process limit.
- D is wrong: nginx is subject to its process rlimits like any other process.
:::
</div>

<div class="q" markdown="1">

**Q5.** Users say an application is slow. `iostat -xz 1` on the data volume shows `w/s` 3,000, `wareq-sz` 4 KB, `w_await` 85 ms, `aqu-sz` 120, `%util` 100. CPU is 70% idle. What is the best interpretation?

A. The CPU is the bottleneck
B. The volume is saturated by small random writes; find the process with `pidstat -d 1` and check the volume's IOPS limit
C. `%util` 100% on an SSD always means the drive has failed
D. The network is saturated

:::answer
**Answer: B.** High queue depth and an 85 ms write latency mean requests are waiting: the volume is saturated, probably at its IOPS limit. Find the writer (`pidstat -d`, `iotop -o`) and then throttle, reschedule or upgrade.

- A is wrong: the CPU is mostly idle.
- C is wrong: `%util` 100% indicates busy, not failed; on SSDs it doesn't even prove saturation. Here `await` and `aqu-sz` do.
- D is wrong: nothing points at the network.
:::
</div>

<div class="q" markdown="1">

**Q6.** A VM's application latency doubles every afternoon. `us` and `sy` are moderate, but `top` shows `st` at 18–25% during the slow periods. What is the most appropriate action?

A. Increase the application's thread count
B. Lower the application's nice value
C. Gather `sar -u` history showing `%steal` and escalate to the virtualisation/cloud platform team to move or resize the VM
D. Disable swap

:::answer
**Answer: C.** Steal is CPU time taken by the hypervisor for other guests. It can't be fixed from inside the VM; the fix is on the host side (less overcommit, another host, a non-burstable instance type). `sar` history is the evidence.

- A is wrong: more threads compete for the same stolen CPU.
- B is wrong: priority inside the guest doesn't affect the hypervisor's scheduling.
- D is wrong: swap is unrelated to steal.
:::
</div>

<div class="q" markdown="1">

**Q7.** `ps` shows 4,000 processes in state `Z`, all with the same parent PID, a custom job scheduler. `kill -9` on the zombie PIDs does nothing. What should you do?

A. Reboot immediately
B. Restart or fix the parent process so the zombies are reaped (they are re-parented to PID 1 if the parent exits)
C. Run `kill -9` again with `sudo`
D. Increase `kernel.pid_max` and ignore it

:::answer
**Answer: B.** Zombies are already dead; only their parent's `wait()` (or PID 1 after re-parenting) removes them. Restarting the scheduler clears them, and the bug that doesn't reap children must be fixed.

- A is wrong: it works but is disruptive and unnecessary.
- C is wrong: a zombie cannot receive signals in any useful way, whoever sends them.
- D is wrong: it hides the problem until the PID space fills again.
:::
</div>

<div class="q" markdown="1">

**Q8.** A backup job (`backup.service`) makes the production database slow every night at 01:00. You need an immediate, non-disruptive mitigation tonight. What is the best option?

A. `systemctl set-property backup.service "IOWriteBandwidthMax=/dev/vdb 50M" CPUQuota=50%`
B. `setenforce 0` so SELinux stops slowing the disk
C. `echo 3 > /proc/sys/vm/drop_caches` before 01:00
D. Kill the database's OOM protection with `oom_score_adj=1000`

:::answer
**Answer: A.** cgroup limits via `set-property` cap the backup's disk and CPU use live, without stopping it, and persist as a drop-in. Then look at moving the window, using snapshots, or `IOWeight=`.

- B is wrong: SELinux is not the cause, and disabling it is never the fix.
- C is wrong: dropping caches makes the database slower.
- D is wrong: OOM settings have nothing to do with I/O contention.
:::
</div>

<div class="q" markdown="1">

**Q9.** A Java service is killed regularly. There is no `java.lang.OutOfMemoryError` in the application log, but `journalctl -k` shows `Killed process ... (java) anon-rss:3900000kB`. The unit has `MemoryMax=4G` and the JVM runs with `-Xmx4g`. What is the most likely cause?

A. The heap is too small
B. Heap plus off-heap memory (metaspace, thread stacks, direct buffers) exceeds the 4 GB cgroup limit; set `-Xmx` well below `MemoryMax=`
C. SELinux is blocking the JVM's memory allocations
D. The kernel's `fs.file-max` is too low

:::answer
**Answer: B.** The kernel/cgroup OOM killer counts the whole process, not just the heap. With `-Xmx` equal to `MemoryMax`, off-heap memory pushes the total over the limit before the heap is full, so the JVM never throws its own OutOfMemoryError. Leave headroom (for example `-Xmx3g` with `MemoryMax=4G`) and check for leaks with `jstat`/`jcmd`.

- A is wrong: the heap isn't reporting full; the process is being killed from outside.
- C is wrong: SELinux does not limit memory amounts.
- D is wrong: file limits produce `EMFILE`, not OOM kills.
:::
</div>

<div class="q" markdown="1">

**Q10.** You need to know why the server was slow between 02:00 and 02:30 last Tuesday, the 18th. Nobody was logged in at the time. Which command gives the best first evidence?

A. `top -b -n 1`
B. `sar -q -u -r -d -p -f /var/log/sa/sa18 -s 02:00:00 -e 02:30:00`
C. `iostat -xz 1`
D. `free -h`

:::answer
**Answer: B.** Only `sar` (with sysstat collection enabled) holds history. Load/run queue, CPU, memory and disk for that window show which resource was the problem.

- A, C and D are wrong: they show the current state only, not last Tuesday.
:::
</div>

<div class="q" markdown="1">

**Q11.** You try `strace -p 4410` as root on a RHEL 9 server and get `attach: ptrace(PTRACE_SEIZE, 4410): Operation not permitted`. SELinux is enforcing. What do you check first?

A. Whether the process is a zombie
B. `getsebool deny_ptrace`; if it is on, turn it off temporarily with `setsebool deny_ptrace off`, debug, then turn it back on
C. Run `setenforce 0` permanently
D. Reinstall strace

:::answer
**Answer: B.** The `deny_ptrace` boolean blocks ptrace for all domains, including root, on hardened systems. Toggle it without `-P` for the debugging session only and revert afterwards. (Another process already tracing the target, such as a debugger, can also cause this.)

- A is wrong: a zombie gives a different result, and you would see `Z` in `ps` first.
- C is wrong: never disable SELinux as a fix; the boolean is the targeted control.
- D is wrong: the binary is fine; the kernel denied the operation.
:::
</div>

<div class="q" markdown="1">

**Q12.** `vmstat 1` on a slow server shows: `r` 3, `b` 5, `si` 4,200, `so` 5,800, `wa` 35, `free` 90 MB, and `available` in `free -h` is 2% of RAM. Which statement is correct?

A. The system is CPU-bound; add vCPUs
B. The system is thrashing: the working set doesn't fit in RAM. Find the memory consumer (`ps aux --sort=-rss`), reduce it or cap it with `MemoryMax=`, or add RAM
C. Swap usage is always normal; ignore it
D. Increase swap size to stop the swapping

:::answer
**Answer: B.** Sustained swap-in **and** swap-out with low available memory and high I/O wait is thrashing. The fix is less memory demand (or more RAM), not more swap.

- A is wrong: `r` 3 is not the issue; the CPUs are waiting on swap I/O.
- C is wrong: some swap **use** is normal; continuous swap **traffic** is not.
- D is wrong: more swap lets the system thrash longer; it doesn't make the working set fit.
:::
</div>
