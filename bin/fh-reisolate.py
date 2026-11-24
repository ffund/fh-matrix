#!/usr/bin/env python3
"""fh-reisolate.py <numa-node> [--apply]

Rewrite the kernel command line in /etc/default/grub so that the isolated cores are on the given NUMA node.
The image isolates nine physical cores (and their hyperthread siblings) on node 0 of a d740. When the testbed
puts the fronthaul link on a NIC attached to the other socket, ProtO-RU's lower PHY runs late under load.

Keeps the three lowest cores of the node for housekeeping, isolates the next nine with their siblings, and
merges the result into the LAST GRUB_CMDLINE_LINUX line, which is the one Emulab writes for this node.
Prints the new line; with --apply also writes the file and runs update-grub. Reboot afterwards.
"""
import re, subprocess, sys
node = int(sys.argv[1]); apply = '--apply' in sys.argv
def cl(s):
    o = []
    for p in s.strip().split(','):
        if p: a, _, b = p.partition('-'); o += range(int(a), int(b or a) + 1)
    return o
cpus = cl(open('/sys/devices/system/node/node%d/cpulist' % node).read())
ncpu = len(cl(open('/sys/devices/system/cpu/present').read()))
sib = lambda c: cl(open('/sys/devices/system/cpu/cpu%d/topology/thread_siblings_list' % c).read())
prim = [c for c in cpus if c == min(sib(c))]
iso_p = prim[3:12]
iso = sorted(set(x for c in iso_p for x in sib(c)))
hk = [c for c in range(ncpu) if c not in iso]
def rng(l):
    out, i = [], 0
    while i < len(l):
        j = i
        while j + 1 < len(l) and l[j + 1] == l[j] + 1: j += 1
        out.append(str(l[i]) if i == j else '%d-%d' % (l[i], l[j])); i = j + 1
    return ','.join(out)
isol = ','.join(map(str, iso))
lines = open('/etc/default/grub').read().split('\n')
idx = [i for i, l in enumerate(lines) if l.startswith('GRUB_CMDLINE_LINUX=')]
base = re.match(r'GRUB_CMDLINE_LINUX="(.*)"', lines[idx[-1]]).group(1)
keep = [w for w in base.split() if not re.match(r'(isolcpus|nohz_full|rcu_nocbs|irqaffinity)=', w)]
allw = ' '.join(re.match(r'GRUB_CMDLINE_LINUX="(.*)"', lines[i]).group(1) for i in idx).split()
extra = [w for w in allw if w not in keep and not re.match(r'(isolcpus|nohz_full|rcu_nocbs|irqaffinity|console|emulabcnet)=', w)]
extra = list(dict.fromkeys(extra))
new = 'GRUB_CMDLINE_LINUX="%s isolcpus=managed_irq,domain,%s nohz_full=%s rcu_nocbs=%s irqaffinity=%s %s"' % (
    ' '.join(keep), isol, isol, isol, rng(hk), ' '.join(extra))
print(new)
if apply:
    out = [l for i, l in enumerate(lines) if i not in idx[:-1]]
    out[out.index(lines[idx[-1]])] = new
    open('/etc/default/grub', 'w').write('\n'.join(out))
    subprocess.run(['update-grub'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print('grub updated; reboot to apply')
