"""Execute the C DSX core and apply bounded overlays to real pinned contexts."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "ci/hackios"
SPEC = importlib.util.spec_from_file_location("chiron_touch_overlay", OVERLAY / "apply_chiron_touch.py")
touch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(touch)
MANIFEST = json.loads(touch.MANIFEST.read_text())
PINNED = Path(os.environ.get("CHIRON_TOUCH_HACKIOS_SOURCE", "/private/tmp/chiron-hackios-reuse/kernel"))
XNU = Path(os.environ.get("CHIRON_TOUCH_XNU_SOURCE", "/private/tmp/chiron-port-xnu"))


class NativeProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("cc")
        if not compiler:
            raise unittest.SkipTest("host C compiler unavailable")
        cls.tmp = tempfile.TemporaryDirectory(prefix="chiron-touch-native-")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.program = Path(cls.tmp.name) / "touch-test"
        subprocess.run([
            compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
            "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
            "-I", str(touch.DRIVER), str(OVERLAY / "chiron_touch_host.c"),
            str(touch.DRIVER / "chiron_dsx.c"), "-o", str(cls.program),
        ], check=True, capture_output=True, text=True)

    def run_case(self, number):
        result = subprocess.run([str(self.program), str(number)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pdt_discovery_and_refusal(self):
        self.run_case(0)

    def test_multicontact_motion_release_and_inclusive_coordinates(self):
        self.run_case(1)

    def test_malformed_frames_never_partially_publish(self):
        self.run_case(2)

    def test_poll_errors_release_and_stop_without_retrying_hardware(self):
        self.run_case(3)


class OverlayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="chiron-touch-overlay-")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def copy_contexts(self, upstream, source=False):
        prefix = Path("xnu/files/bsd/dev" if source else "bsd/dev")
        if not (upstream / prefix / "redwood_input.c").exists():
            self.skipTest(f"pinned source unavailable: {upstream}")
        for name, expected in MANIFEST["pinned_hackios_files"].items():
            data = (upstream / prefix / name).read_bytes()
            self.assertEqual(touch.digest(data), expected)
            dest = self.folder / prefix / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        if not source:
            make = self.folder / "makedefs/MakeInc.def"
            make.parent.mkdir()
            make.write_bytes((upstream / "makedefs/MakeInc.def").read_bytes())
        return prefix

    def test_source_overlay_on_pinned_hackios(self):
        prefix = self.copy_contexts(PINNED, source=True)
        before = (self.folder / prefix / "redwood_input.c").read_bytes()
        result = touch.apply(source=self.folder)
        self.assertFalse(result["hardware_touch_available"])
        self.assertFalse(result["native_i2c_transport_implemented"])
        self.assertEqual(result["kind"], "source")
        self.assertEqual((self.folder / prefix / "redwood_input.c").read_bytes(), before)
        self.assertEqual(set(result["result"]), {str(prefix / name) for name in touch.COPIES})
        for path, sha in result["result"].items():
            self.assertEqual(touch.digest((self.folder / path).read_bytes()), sha)
        with self.assertRaisesRegex(ValueError, "pinned touch source changed"):
            touch.apply(source=self.folder)

    def test_kernel_overlay_on_fully_applied_xnu(self):
        self.copy_contexts(XNU)
        result = touch.kernel_overlay(self.folder)
        self.assertTrue(result["kernel_rebuild_required"])
        self.assertEqual(result["kind"], "kernel")
        self.assertIn('chiron_touch=1', result["boot_arg"])
        self.assertIn("redwood_goodix_init", (self.folder / "bsd/dev/redwood_goodix.c").read_text())
        self.assertIn("CHIRON_PORT", (self.folder / "bsd/dev/redwood_goodix.c").read_text())

    def test_changed_source_fails_before_writes(self):
        prefix = self.copy_contexts(PINNED, source=True)
        path = self.folder / prefix / "redwood_input.c"
        path.write_text(path.read_text() + "\n/* changed */\n")
        original = (self.folder / prefix / "redwood_goodix.c").read_bytes()
        with self.assertRaisesRegex(ValueError, "pinned touch source changed"):
            touch.source_overlay(self.folder)
        self.assertEqual((self.folder / prefix / "redwood_goodix.c").read_bytes(), original)
        self.assertFalse((self.folder / prefix / "chiron_dsx.h").exists())

    def test_existing_include_fails_before_writes(self):
        prefix = self.copy_contexts(PINNED, source=True)
        (self.folder / prefix / "chiron_dsx.h").write_text("unrelated source\n")
        original = (self.folder / prefix / "redwood_goodix.c").read_bytes()
        with self.assertRaisesRegex(ValueError, "existing touch overlay destination"):
            touch.source_overlay(self.folder)
        self.assertEqual((self.folder / prefix / "redwood_goodix.c").read_bytes(), original)

    def test_requires_chiron_build_and_unambiguous_target(self):
        self.copy_contexts(XNU)
        (self.folder / "makedefs/MakeInc.def").write_text("not a chiron build\n")
        with self.assertRaisesRegex(ValueError, "CHIRON_PORT"):
            touch.kernel_overlay(self.folder)
        for kwargs in ({}, {"source": self.folder, "tree": self.folder}):
            with self.assertRaisesRegex(ValueError, "exactly one"):
                touch.apply(**kwargs)

    def test_overlay_hash_manifest(self):
        self.assertEqual(set(MANIFEST["overlay_files"]), set(touch.COPIES.values()))
        for name, expected in MANIFEST["overlay_files"].items():
            self.assertEqual(touch.digest((touch.DRIVER / name).read_bytes()), expected)

    def test_xnu_adapter_host_syntax_and_disabled_transport(self):
        prefix = self.copy_contexts(XNU)
        touch.kernel_overlay(self.folder)
        compiler = shutil.which("cc")
        if not compiler:
            self.skipTest("host C compiler unavailable")
        includes = self.folder / "host"
        for name in ("kern/clock.h", "kern/thread.h", "libkern/libkern.h",
                     "mach/mach_types.h", "pexpert/pexpert.h", "sys/redwood_input.h", "sys/systm.h"):
            dest = includes / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("")
        unit = self.folder / "adapter.c"
        unit.write_text(r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define CHIRON_PORT 1
#define KERN_SUCCESS 0
#define NSEC_PER_MSEC 1000000
#define REDWOOD_TOUCH_DOWN 1
#define REDWOOD_TOUCH_MOVE 2
#define REDWOOD_TOUCH_UP 3
typedef int kern_return_t;
typedef int wait_result_t;
typedef void *thread_t;
static int enabled, starts, posts, waits;
static bool PE_parse_boot_argn(const char *name, void *out, int size) {
    assert(strcmp(name, "chiron_touch") == 0 && size == 4);
    *(uint32_t *)out = (uint32_t)enabled;
    return enabled != 0;
}
static int kernel_thread_start(void (*fn)(void *, wait_result_t), void *arg, thread_t *t) {
    (void)fn; (void)arg; (void)t; starts++; return 1;
}
static void thread_deallocate(thread_t t) { (void)t; }
static thread_t current_thread(void) { return NULL; }
kern_return_t thread_terminate(thread_t t) { (void)t; return 0; }
static void delay_for_interval(int n, int scale) { (void)n; (void)scale; waits++; }
static void redwood_touch_post(uint32_t t, uint32_t f, uint32_t x, uint32_t y) {
    (void)t; (void)f; (void)x; (void)y; posts++;
}
#include "redwood_goodix.c"
int main(void) {
    for (enabled = 0; enabled < 4; enabled++) redwood_goodix_init();
    assert(starts == 0 && posts == 0 && waits == 0);
    return 0;
}
""")
        program = self.folder / "adapter"
        subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                        "-I", str(includes), "-I", str(self.folder / prefix),
                        str(unit), "-o", str(program)], check=True, capture_output=True, text=True)
        result = subprocess.run([str(program)], capture_output=True, text=True, check=True)
        self.assertIn("BLOCKED", result.stdout)
        self.assertIn("hardware untouched", result.stdout)


class MeasuredEvidenceTests(unittest.TestCase):
    def test_measured_files_match_manifest_and_bus_binding(self):
        for relative, expected in MANIFEST["local_evidence_sha256"].items():
            path = ROOT / relative
            if path.exists():
                self.assertEqual(touch.digest(path.read_bytes()), expected, relative)
        bindings = ROOT / "research/artifacts/chiron-live-20261020/i2c-bindings.txt"
        if not bindings.exists():
            self.skipTest("private measured evidence is intentionally not in Git")
        self.assertIn("/sys/bus/i2c/devices/5-0020\ndsx-i2c-force\n"
                      "/sys/bus/i2c/drivers/synaptics_dsi_force", bindings.read_text())

    def test_runtime_tree_gpio_numbers_and_controller_are_independently_measured(self):
        path = ROOT / "research/artifacts/chiron-live-20261020/runtime-tree.tar"
        if not path.exists():
            self.skipTest("private runtime DT capture is intentionally not in Git")
        with tarfile.open(path) as archive:
            def cells(name):
                data = archive.extractfile("./soc/i2c@c179000/" + name).read()
                return struct.unpack(">" + "I" * (len(data) // 4), data)
            self.assertEqual(cells("reg"), (0x0c179000, 0x600))
            self.assertEqual(cells("qcom,i2c-dat")[1:], (87, 0))
            self.assertEqual(cells("qcom,i2c-clk")[1:], (88, 0))
            self.assertEqual(cells("synaptics_dsi_i2c@20/reg"), (0x20,))
            self.assertEqual(cells("synaptics_dsi_i2c@20/synaptics,reset-gpio")[1:], (89, 0))
            self.assertEqual(cells("synaptics_dsi_i2c@20/synaptics,irq-gpio")[1:], (125, 0x2008))
            self.assertEqual(cells("interrupts"), (0, 99, 0))


if __name__ == "__main__":
    unittest.main()
