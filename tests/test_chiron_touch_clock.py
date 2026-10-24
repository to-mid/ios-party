"""Clock-only MMIO simulation; never opens USB or accesses physical hardware."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "drivers/chiron-touch"
HOST = ROOT / "ci/hackios/chiron_touch_clock_host.c"


class ClockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("cc")
        if not compiler:
            raise unittest.SkipTest("host C compiler unavailable")
        cls.tmp = tempfile.TemporaryDirectory(prefix="chiron-touch-clock-")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.program = Path(cls.tmp.name) / "clock"
        result = subprocess.run([
            compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
            "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
            "-I", str(DRIVER), str(HOST), str(DRIVER / "chiron_board.c"),
            str(DRIVER / "chiron_clock.c"), "-o", str(cls.program),
        ], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def run_case(self, case):
        result = subprocess.run([str(self.program), str(case)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_captured_values_order_barriers_whitelist_and_idempotence(self):
        self.run_case(0)

    def test_preexisting_clocks_unrelated_bits_and_shared_votes_survive(self):
        self.run_case(1)

    def test_identity_ownership_and_unknown_profiles_refuse_without_writes(self):
        self.run_case(2)

    def test_each_success_path_read_and_write_failure_and_ignored_writes(self):
        self.run_case(3)

    def test_halts_update_timeout_and_regression(self):
        self.run_case(4)

    def test_elapsed_iteration_deadlines_stalled_regressing_and_overslept_time(self):
        self.run_case(5)

    def test_ownership_rechecked_and_loss_is_sticky_during_cleanup(self):
        self.run_case(6)

    def test_cleanup_write_failure_and_halt_timeout_report_remaining_changes(self):
        self.run_case(7)

    def test_arm64_darwin_freestanding_syntax(self):
        compiler = shutil.which("clang")
        if not compiler:
            self.skipTest("clang unavailable")
        for name in ("chiron_clock.c", "chiron_board.c"):
            result = subprocess.run([
                compiler, "--target=arm64-apple-darwin", "-ffreestanding",
                "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsyntax-only",
                "-I", str(DRIVER), str(DRIVER / name),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pinned_source_evidence(self):
        manifest = json.loads((ROOT / "ci/hackios/chiron_touch_sources.json").read_text())
        paths = [
            Path("/private/tmp/chiron-clock-gcc-8998.c"),
            Path("/private/tmp/chiron-clock-local2.c"),
            Path("/private/tmp/chiron-msm-clocks-hwio-8998.h"),
        ]
        if not all(path.exists() for path in paths):
            self.skipTest("pinned source evidence unavailable")
        for path in paths:
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                             manifest["local_evidence_sha256"][str(path)])
        gcc, local, hwio = [path.read_text() for path in paths]
        self.assertIn("F(  19200000,    cxo_clk_src,    1,    0,     0)", gcc)
        self.assertIn(".en_mask = BIT(17)", gcc)
        self.assertIn("#define CMD_RCGR_ROOT_ENABLE_BIT\tBIT(1)", local)
        self.assertIn("#define RCG_FORCE_DISABLE_DELAY_US\t100", local)
        self.assertIn("GCC_BLSP1_QUP5_I2C_APPS_CMD_RCGR", hwio)
        # QUP5 uses the local HID path, not the optional force-root paths.
        root = gcc.split("static struct rcg_clk blsp1_qup5_i2c_apps_clk_src = {", 1)[1]
        root = root.split("\n};", 1)[0]
        self.assertIn(".set_rate = set_rate_hid", root)
        for field in ("non_local_children", "non_local_control_timeout", "force_enable_rcgr"):
            self.assertNotIn(field, root)


if __name__ == "__main__":
    unittest.main()
