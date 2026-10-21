#!/usr/bin/env python3
"""Versioned, install-time resource policy. No live retuning or data migration."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil

M = 1024**2
POLICY = Path('/etc/webdesktop-resources/policy.json')


def cpuset_count(text):
    result = set()
    for item in text.strip().split(','):
        if not item:
            continue
        bounds = item.split('-')
        first, last = int(bounds[0]), int(bounds[-1])
        if first < 0 or last < first or last-first > 65536:
            raise ValueError('Invalid cpuset')
        result.update(range(first, last+1))
    return len(result)


def effective(raw):
    """Pure function: all ancestors matter, including quotas above our own group."""
    memory = min([raw['memory_bytes']] + [x for x in raw.get('memory_limits', []) if x is not None])
    cpu = min([raw['cpu_count'], raw.get('affinity_count', raw['cpu_count'])] + raw.get('cpu_limits', []))
    swap = min([raw['swap_bytes']] + [x for x in raw.get('swap_limits', []) if x is not None])
    return dict(memory_bytes=int(memory), cpu_count=round(cpu, 3), swap_bytes=int(swap), disk_free_bytes=raw['disk_free_bytes'])


def detect():
    mem = Path('/proc/meminfo').read_text()
    raw = dict(memory_bytes=int(re.search(r'MemTotal:\s+(\d+)', mem)[1])*1024,
               cpu_count=os.cpu_count() or 1, affinity_count=len(os.sched_getaffinity(0)),
               swap_bytes=int(re.search(r'SwapTotal:\s+(\d+)', mem)[1])*1024,
               disk_free_bytes=shutil.disk_usage('/var/lib').free,
               memory_limits=[], swap_limits=[], cpu_limits=[])
    root = Path('/sys/fs/cgroup')
    name = next((line[3:] for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::')), '/')
    # Some container runtimes expose an already namespace-rooted cgroup tree.
    candidate = root/name.lstrip('/')
    if '..' in Path(name).parts or not candidate.exists():
        candidate = root
    for group in (candidate, *candidate.parents):
        if group != root and root not in group.parents:
            continue
        for field, key in (('memory.max', 'memory_limits'), ('memory.swap.max', 'swap_limits')):
            p = group/field
            if p.exists():
                value = p.read_text().strip()
                raw[key].append(None if value == 'max' else int(value))
        if (group/'cpu.max').exists():
            quota, period = (group/'cpu.max').read_text().split()
            if quota != 'max': raw['cpu_limits'].append(int(quota)/int(period))
        if (group/'cpuset.cpus.effective').exists():
            count = cpuset_count((group/'cpuset.cpus.effective').read_text())
            if count: raw['cpu_limits'].append(count)
        if group == root: break
    return raw


def calculate(raw, overrides=None):
    inputs = effective(raw)
    ram = inputs['memory_bytes']//M
    if ram < 900 or inputs['cpu_count'] < .5:
        raise ValueError('Effective cgroup capacity needs >=900 MiB RAM and >=0.5 CPU')
    rounding = lambda x: int(math.ceil(x/32)*32)
    os_reserve = min(2048, max(192, rounding(ram*.12)))
    control_reserve = 256 if ram <= 1152 else min(1536, max(256, rounding(ram*.16+128)))
    total = ram-os_reserve
    execution = total-control_reserve
    if execution < 480: raise ValueError('Insufficient protected execution headroom')
    workspace = min(16384, max(512, int(round(ram/1024))*512))
    # Measured 10 GiB staging installs need a 3 GiB post-package reserve;
    # larger disks keep 4 GiB. This is free space, not filesystem total size.
    disk_free_mib=inputs['disk_free_bytes']//M
    disk_reserve=3072 if disk_free_mib<12288 else 4096
    disk_cap = max(0, (disk_free_mib-disk_reserve)//256*256)
    workspace = min(workspace, disk_cap)
    if workspace < 512: raise ValueError(f'Insufficient workspace and system disk reserve: free={disk_free_mib} MiB, reserve={disk_reserve} MiB')
    basic = min(512, max(192, rounding(192+(ram-1024)*.06)))
    # Browser can borrow otherwise idle execution memory. The common subtree,
    # not a sum of app reservations, protects the platform's remaining memory.
    browser = execution
    values = dict(execution_memory_mib=execution, browser_memory_mib=browser, workspace_mib=workspace)
    for key, value in (overrides or {}).items():
        if key not in values or type(value) is not int or value < (512 if key=='workspace_mib' else 320 if key=='browser_memory_mib' else 480) or value > values[key]:
            raise ValueError('Override exceeds the computed safe envelope: '+key)
        values[key] = value
    execution = values['execution_memory_mib']
    browser = min(values['browser_memory_mib'], execution)
    swap = inputs['swap_bytes']//M
    total_swap = min(swap, max(640, total//2))
    exec_swap = min(total_swap, max(256, execution//2))
    cpu = inputs['cpu_count']*100
    tasks = min(2048, max(256, int(inputs['cpu_count']*128+ram/8)))
    def limits(mem, sw, tasks_max, **extra):
        return dict(MemoryMax=mem*M, MemoryHigh='infinity', MemorySwapMax=sw*M, TasksMax=tasks_max,
                    MemoryAccounting='yes', CPUAccounting='yes', TasksAccounting='yes', **extra)
    units = {
        'webdesktop.slice': limits(total, total_swap, tasks+160, CPUQuota=f'{cpu*.95:.2f}%', MemoryLow=control_reserve*M),
        'webdesktop-exec.slice': limits(execution, exec_swap, tasks, CPUQuota=f'{cpu*.85:.2f}%', CPUWeight=100),
        'webdesktop-exec-gui.slice': limits(execution, exec_swap, min(tasks, 512)),
        'webdesktop-exec-pty.slice': limits(execution, exec_swap, min(tasks, 256)),
        'webdesktop-staging.service': limits(min(1024, max(384, rounding(ram/4))), min(128,total_swap), 64, MemoryLow=128*M, CPUWeight=1000, OOMScoreAdjust=-800),
        'postgresql@15-main.service': limits(min(768, max(128, rounding(ram*.08))), min(64,total_swap), 64, MemoryLow=48*M, CPUWeight=1000, OOMScoreAdjust=-800),
        'webdesktop-guest.service': limits(min(192, max(96, rounding(ram*.04))), min(32,total_swap), 16, MemoryLow=16*M, CPUWeight=1000, OOMScoreAdjust=-700),
        'webdesktop-gui@.service': limits(basic, min(64,exec_swap), 64, Slice='webdesktop-exec-gui.slice', CPUQuota=f'{cpu*.85:.2f}%', CPUWeight=200, OOMScoreAdjust=400),
        'webdesktop-browser@.service': limits(browser, min(exec_swap,max(192,browser//3)), min(512,max(128,tasks//2)), Slice='webdesktop-exec-gui.slice', CPUQuota=f'{cpu*.85:.2f}%', OOMScoreAdjust=500),
        'webdesktop-pty@.service': limits(min(1024,max(128,rounding(execution*.3))), min(128,exec_swap), min(128,max(32,tasks//8)), Slice='webdesktop-exec-pty.slice', CPUQuota=f'{cpu*.85:.2f}%', OOMScoreAdjust=500),
    }
    units['webdesktop-exec.slice']['MemoryHigh'] = int(execution*.96)*M
    for name in ('webdesktop-guest.service', 'postgresql@15-main.service'): units[name]['Slice']='webdesktop.slice'
    for name in ('webdesktop-gui-lease@.service','webdesktop-pty-lease@.service'):
        units[name]=dict(MemorySwapMax=0, CPUWeight=1000, OOMScoreAdjust=-900)
    return dict(schema=1, policy='adaptive-v1', detected=raw, effective=inputs,
                os_reserve_mib=os_reserve, control_reserve_mib=control_reserve,
                workspace_mib=values['workspace_mib'], system_disk_reserve_mib=disk_reserve, units=units,
                rationale=['limits are shared ceilings, not preallocated memory', 'execution subtree cannot consume control-plane reserve',
                           'CPU percentages are fractions of one CPU, multiplied by effective capacity',
                           'only execution subtree has memory.high; control-plane is not throttled by cache growth'])


def install(policy, directory=Path('/etc/systemd/system')):
    """Fresh config only. Never retune running processes or overwrite unknown files."""
    if os.geteuid() != 0: raise PermissionError('root required')
    POLICY.parent.mkdir(mode=0o755)  # exclusive; existing policy is not an upgrade path
    with POLICY.open('x') as f: json.dump(policy,f,indent=2)
    POLICY.chmod(0o644)
    for name, settings in policy['units'].items():
        if name.endswith('.slice'):
            target=directory/name
            # New names have no unit template; systemd creates parent hierarchy.
            if name.startswith('webdesktop-exec') or name=='webdesktop.slice':
                with target.open('x') as f: f.write('[Slice]\n'+('[Install]\nWantedBy=multi-user.target\n' if name=='webdesktop-exec.slice' else ''))
        target=directory/(name+'.d')/'adaptive.conf'
        target.parent.mkdir(exist_ok=True)
        section='Slice' if name.endswith('.slice') else 'Service'
        with target.open('x') as f:
            f.write('['+section+']\n'+''.join(f'{k}={v}\n' for k,v in settings.items()))
        target.chmod(0o644)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace-mib',type=int); p.add_argument('--execution-memory-mib',type=int); p.add_argument('--browser-memory-mib',type=int)
    a=p.parse_args()
    print(json.dumps(calculate(detect(),{k:v for k,v in vars(a).items() if v is not None}),indent=2))
