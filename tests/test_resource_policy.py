import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('resource_policy',Path(__file__).resolve().parents[1]/'webdesktop-deploy/scripts/resource_policy.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)


def host(ram=1024,cpu=1,swap=1024,disk=8):
    return dict(memory_bytes=ram*r.M,cpu_count=cpu,swap_bytes=swap*r.M,disk_free_bytes=disk*1024*r.M)


class ResourcePolicyTests(unittest.TestCase):
    def test_ancestors_bound_memory_cpu_swap(self):
        raw=host(4096,4);raw.update(memory_limits=[None,960*r.M,2048*r.M],cpu_limits=[2,.75],swap_limits=[0])
        result=r.effective(raw)
        self.assertEqual((result['memory_bytes'],result['cpu_count'],result['swap_bytes']),(960*r.M,.75,0))

    def test_affinity_is_capacity(self):
        raw=host(cpu=8);raw['affinity_count']=2
        self.assertEqual(r.effective(raw)['cpu_count'],2)

    def test_cpuset_ranges_overlap(self):
        self.assertEqual(r.cpuset_count('0-3,2,6-7'),6)

    def test_malformed_cpuset_rejected(self):
        for text in ('3-1','-1','0-999999'):
            with self.assertRaises(ValueError):r.cpuset_count(text)

    def test_small_vps_preserves_control_and_os(self):
        p=r.calculate(host(960));u=p['units']
        self.assertEqual(p['os_reserve_mib'],192)
        self.assertEqual(u['webdesktop-exec.slice']['MemoryMax'],512*r.M)
        self.assertEqual(u['webdesktop-browser@.service']['MemoryMax'],u['webdesktop-exec.slice']['MemoryMax'])
        self.assertEqual(u['webdesktop.slice']['MemoryHigh'],'infinity')
        self.assertGreater(u['webdesktop-exec.slice']['MemoryHigh'],448*r.M)

    def test_growing_ram_and_multicore_scale(self):
        plans=[r.calculate(host(m,c)) for m,c in ((1024,1),(2048,2),(4096,4),(8192,8))]
        for small,large in zip(plans,plans[1:]):
            for unit in ('webdesktop.slice','webdesktop-exec.slice','webdesktop-browser@.service'):
                self.assertLess(small['units'][unit]['MemoryMax'],large['units'][unit]['MemoryMax'])
        self.assertEqual(plans[2]['units']['webdesktop.slice']['CPUQuota'],'380.00%')
        self.assertEqual(plans[2]['units']['webdesktop-exec.slice']['CPUQuota'],'340.00%')

    def test_sum_of_child_ceilings_is_not_reservation(self):
        for ram in (960,1024,2048,4096):
            p=r.calculate(host(ram));u=p['units']
            self.assertEqual(u['webdesktop-exec.slice']['MemoryMax']+p['control_reserve_mib']*r.M,u['webdesktop.slice']['MemoryMax'])
            self.assertEqual(u['webdesktop.slice']['MemoryMax']+p['os_reserve_mib']*r.M,ram*r.M)
            for unit in ('webdesktop-browser@.service','webdesktop-gui@.service'):
                self.assertEqual(u[unit]['Slice'],'webdesktop-exec-gui.slice')
            self.assertEqual(u['webdesktop-pty@.service']['Slice'],'webdesktop-exec-pty.slice')

    def test_control_plane_weight_and_oom_priority(self):
        u=r.calculate(host())['units']
        for unit in ('webdesktop-staging.service','webdesktop-guest.service','postgresql@15-main.service'):
            self.assertGreater(u[unit]['CPUWeight'],u['webdesktop-exec.slice']['CPUWeight'])
            self.assertLess(u[unit]['OOMScoreAdjust'],u['webdesktop-pty@.service']['OOMScoreAdjust'])
        self.assertNotIn('Slice',u['webdesktop-gui-lease@.service'])

    def test_no_swap_does_not_invent_swap(self):
        p=r.calculate(host(swap=0))
        self.assertTrue(all(x.get('MemorySwapMax')==0 for x in p['units'].values()))

    def test_effective_swap_not_host_total(self):
        raw=host(swap=4096);raw['swap_limits']=[128*r.M]
        p=r.calculate(raw)
        self.assertLessEqual(p['units']['webdesktop.slice']['MemorySwapMax'],128*r.M)

    def test_safe_overrides_and_rejected_expansion(self):
        p=r.calculate(host(4096),dict(workspace_mib=512,browser_memory_mib=512,execution_memory_mib=1024))
        self.assertEqual(p['workspace_mib'],512)
        self.assertEqual(p['units']['webdesktop-browser@.service']['MemoryMax'],512*r.M)
        for key,val in (('workspace_mib',99999),('execution_memory_mib',99999),('browser_memory_mib',99999),('UID',0),('workspace_mib','512')):
            with self.assertRaises(ValueError):r.calculate(host(),{key:val})

    def test_too_small_effective_memory_or_disk_fails(self):
        for raw in (host(512),host(disk=3),dict(host(),cpu_count=.2)):
            with self.assertRaises(ValueError):r.calculate(raw)

    def test_disk_reserve_boundary_after_packages(self):
        raw=host();raw['disk_free_bytes']=3584*r.M
        self.assertEqual(r.calculate(raw)['workspace_mib'],512)
        raw['disk_free_bytes']-=r.M
        with self.assertRaises(ValueError):r.calculate(raw)

    def test_all_user_limits_remain_finite(self):
        for ram in (960,2048,4096,16384):
            p=r.calculate(host(ram,4,disk=30))
            for name in ('webdesktop-exec.slice','webdesktop-pty@.service','webdesktop-browser@.service','webdesktop-gui@.service'):
                self.assertGreater(p['units'][name]['MemoryMax'],0)
                self.assertLessEqual(p['units'][name]['TasksMax'],2048)
            self.assertLessEqual(p['workspace_mib'],16384)


if __name__=='__main__':unittest.main()
