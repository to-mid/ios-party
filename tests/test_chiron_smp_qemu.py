import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ci/hackios"))
from recheck_chiron_smp import loader_topology, observations


class SmpQemuTests(unittest.TestCase):
    def test_two_cluster_loader_mapping_is_scoped_and_requires_exact_context(self):
        original = "\t\t\t\tuint64_t mpidr = (uint64_t)core << plat->core_shift;"
        patched = loader_topology(original)
        self.assertIn("plat == &platform_qemu_virt", patched)
        self.assertIn("core / 4", patched)
        with self.assertRaises(ValueError):
            loader_topology("changed")
        with self.assertRaises(ValueError):
            loader_topology(original + original)

    def test_registration_alone_does_not_count_as_running(self):
        seen = observations("mcache: 8 CPU(s)\ncluster_id 0 cpu_number 0\ncluster_id 1 cpu_number 4")
        self.assertTrue(seen["both_clusters_registered"])
        self.assertFalse(seen["all_secondary_cpus_online"])
        self.assertFalse(seen["springboard_process"])

    def test_all_seven_secondary_cpus_and_panic_are_independent_observations(self):
        log = "\n".join(f"arm_cpu_init(): cpu {cpu} online" for cpu in range(1, 8))
        self.assertTrue(observations(log)["all_secondary_cpus_online"])
        self.assertFalse(observations(log)["fatal"])
        self.assertTrue(observations(log + "\npanic(cpu 4 caller 0x123)")["fatal"])
        self.assertFalse(observations(log.replace("cpu 7 online", "cpu 6 online"))[
            "all_secondary_cpus_online"])


if __name__ == "__main__":
    unittest.main()
