import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ci/hackios"))
from recheck_chiron_smp import loader_topology, observations


class SmpQemuTests(unittest.TestCase):
    def test_two_cluster_loader_mapping_is_scoped_and_requires_exact_context(self):
        original = ("\t\t\t\tuint64_t mpidr = (uint64_t)core << plat->core_shift;\n"
                    "plat->gic_redist - plat->io_base, 0x100000 };\n"
                    "\tif (more_cores) {\n\t\tfor (int distance")
        patched = loader_topology(original)
        self.assertIn("plat == &platform_qemu_virt", patched)
        self.assertIn("core / 4", patched)
        self.assertIn("0x280000", patched)
        self.assertIn("12 unused affinities confirmed OFF", patched)
        with self.assertRaises(ValueError):
            loader_topology("changed")
        with self.assertRaises(ValueError):
            loader_topology(original + original)

    def test_registration_alone_does_not_count_as_running(self):
        seen = observations("mcache: 8 CPU(s)\ncluster_id 0 cpu_number 0\ncluster_id 1 cpu_number 4")
        self.assertTrue(seen["both_clusters_registered"])
        self.assertFalse(seen["all_secondary_cpus_online"])
        self.assertFalse(seen["springboard_process"])

    def test_physical_clusters_share_the_clutch_scheduler_pset(self):
        original = "pset_find(cluster_id=0) returned pset 0\npset_find(cluster_id=1) returned pset 1"
        self.assertFalse(observations(original)["shared_scheduler_pset"])
        self.assertTrue(observations(original.replace("returned pset 1", "returned pset 0"))[
            "shared_scheduler_pset"])

    def test_all_seven_secondary_cpus_and_panic_are_independent_observations(self):
        log = "\n".join(f"arm_cpu_init(): cpu {cpu} online" for cpu in range(1, 8))
        self.assertTrue(observations(log)["all_secondary_cpus_online"])
        self.assertFalse(observations(log)["fatal"])
        self.assertTrue(observations(log + "\npanic(cpu 4 caller 0x123)")["fatal"])
        self.assertFalse(observations(log.replace("cpu 7 online", "cpu 6 online"))[
            "all_secondary_cpus_online"])


if __name__ == "__main__":
    unittest.main()
