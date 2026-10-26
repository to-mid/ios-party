"""Exercise actual UEFI menu input and the narrow legacy I2C adapter."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MenuTouchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("cc")
        if not compiler:
            raise unittest.SkipTest("host C compiler unavailable")
        cls.temp = tempfile.TemporaryDirectory(prefix="chiron-menu-touch-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.programs = {}
        for name in ("menu", "adapter"):
            output = Path(cls.temp.name) / name
            result = subprocess.run([
                compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                "-I", str(ROOT / "tests/uefi_menu"), "-I", str(ROOT / "ci/uefi"),
                "-I", str(ROOT / "drivers/chiron-touch"),
                str(ROOT / f"tests/uefi_menu/{name}_host.c"), "-o", str(output),
            ], capture_output=True, text=True)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
            cls.programs[name] = output

    def run_program(self, name, *arguments):
        result = subprocess.run([str(self.programs[name]), *map(str, arguments)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_press_move_release_boots_the_touched_row(self):
        self.run_program("menu", 0)

    def test_drag_outside_cancels_release_action(self):
        self.run_program("menu", 1)

    def test_no_input_preserves_default_timeout(self):
        self.run_program("menu", 2)

    def test_transport_error_stops_countdown_and_blocks_stale_touch(self):
        self.run_program("menu", 3)

    def test_close_failure_blocks_boot_until_explicit_retry(self):
        self.run_program("menu", 4)

    def test_keyboard_selection_still_works(self):
        self.run_program("menu", 5)

    def test_touching_blank_area_stops_autoboot_without_selecting(self):
        self.run_program("menu", 6)

    def test_legacy_i2c_counts_errors_power_release_and_multitouch_cancel(self):
        self.run_program("adapter")


if __name__ == "__main__":
    unittest.main()
