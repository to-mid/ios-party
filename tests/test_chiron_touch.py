"""Execute the C DSX core and apply bounded overlays to real pinned contexts."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


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


class NativeTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("cc")
        if not compiler:
            raise unittest.SkipTest("host C compiler unavailable")
        cls.tmp = tempfile.TemporaryDirectory(prefix="chiron-touch-qup-")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.program = Path(cls.tmp.name) / "native-test"
        subprocess.run([
            compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
            "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
            "-I", str(touch.DRIVER), str(OVERLAY / "chiron_touch_native_host.c"),
            *[str(touch.DRIVER / name) for name in (
                "chiron_dsx.c", "chiron_qup.c", "chiron_f12.c", "chiron_native.c", "chiron_board.c")],
            "-o", str(cls.program),
        ], check=True, capture_output=True, text=True)

    def run_case(self, number):
        result = subprocess.run([str(self.program), str(number)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_handoff_refusal_has_no_mmio_writes(self):
        self.run_case(0)

    def test_real_tag_stream_counts_packing_and_fifo_capacity(self):
        self.run_case(1)

    def test_error_timeout_short_fifo_poison_without_partial_data(self):
        self.run_case(2)

    def test_vendor_f12_query_control_layout_and_unknown_profile_refusal(self):
        self.run_case(3)

    def test_qup_f12_discovery_to_existing_hid_producer_and_reset_release(self):
        self.run_case(4)

    def test_data15_releases_stale_objects_and_refuses_torn_snapshots(self):
        self.run_case(5)

    def test_elapsed_deadlines_survive_oversleep_stalled_or_regressing_clocks(self):
        self.run_case(6)

    def test_board_identity_exact_clock_pin_irq_masks_and_read_only_refusal(self):
        self.run_case(7)

    def test_retained_board_ready_path_and_clock_loss_without_unsafe_cleanup(self):
        self.run_case(8)

    def test_measured_reset_initialization_readback_and_no_recovery_during_poll(self):
        self.run_case(9)

    def test_capture_observes_actual_reads_without_extra_transactions_or_events(self):
        self.run_case(10)

    def test_ten_slot_single_finger_uses_vendor_prefix_with_32_byte_fifo(self):
        self.run_case(11)


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
        self.assertTrue(result["native_i2c_transport_implemented"])
        self.assertTrue(result["f12_layout_validator_implemented"])
        self.assertFalse(result["native_i2c_hardware_enabled"])
        self.assertTrue(result["board_probe_implemented"])
        self.assertEqual(result["read_only_probe_boot_arg"], "chiron_touch_probe=1")
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

    def test_xnu_adapter_identity_sleep_and_synchronous_read_only_probe(self):
        prefix = self.copy_contexts(XNU)
        touch.kernel_overlay(self.folder)
        compiler = shutil.which("cc")
        if not compiler:
            self.skipTest("host C compiler unavailable")
        includes = self.folder / "host"
        for name in ("kern/clock.h", "kern/debug.h", "kern/sched_prim.h", "kern/thread.h", "libkern/libkern.h",
                     "mach/mach_types.h", "pexpert/pexpert.h", "pexpert/device_tree.h",
                     "sys/redwood_input.h", "sys/systm.h"):
            dest = includes / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("")
        unit = self.folder / "adapter.c"
        unit.write_text(r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <setjmp.h>
#define CHIRON_PORT 1
#define KERN_SUCCESS 0
#define NSEC_PER_MSEC 1000000
#define NSEC_PER_USEC 1000
#define THREAD_UNINT 2
#define THREAD_WAITING -1
#define THREAD_TIMED_OUT 1
#define THREAD_CONTINUE_NULL NULL
#define REDWOOD_TOUCH_DOWN 1
#define REDWOOD_TOUCH_MOVE 2
#define REDWOOD_TOUCH_UP 3
typedef int kern_return_t;
typedef int wait_result_t;
typedef void *thread_t;
typedef void *event_t;
typedef uintptr_t vm_offset_t;
typedef uintptr_t vm_size_t;
typedef void *DTEntry;
#define kSuccess 0
static int maps, valid_platform, probe_mode, clock_mode, pinmux_mode, panics;
static jmp_buf panic_env;
static const char *panic_format;
static uint32_t gcc_words[0xb0000/4], gpio_words[0x3000/4];
static uint32_t gic_words[0x10000/4], qup_words[0x600/4];
static uint32_t gcc_before[0xb0000/4], gpio_before[0x3000/4];
static uint32_t gic_before[0x10000/4], qup_before[0x600/4];
static int SecureDTLookupEntry(void *root, const char *path, DTEntry *entry) {
    (void)root;
    if (!valid_platform) return -1;
    *entry = (void *)path; return 0;
}
static int SecureDTGetProperty(DTEntry entry, const char *name, const void **data, unsigned *size) {
    const char *path = entry;
    static const char type[] = "chiron";
    static const uint64_t ranges[] = {0, 0x01000000, 0xff000000};
    static const uint64_t gic[] = {0x16a00000, 0x10000, 0x16b00000, 0x100000};
    static const uint32_t proof[] = {1, 1, 0xfe, 1}, rotation = 0;
    if (!strcmp(path, "/arm-io") && !strcmp(name, "device_type")) {
        *data = type; *size = sizeof(type);
    } else if (!strcmp(path, "/arm-io") && !strcmp(name, "ranges")) {
        *data = ranges; *size = sizeof(ranges);
    } else if (!strcmp(path, "/arm-io/gic") && !strcmp(name, "reg")) {
        *data = gic; *size = sizeof(gic);
    } else if (!strcmp(path, "/chosen") && !strcmp(name, "display-rotation")) {
        *data = &rotation; *size = sizeof(rotation);
    } else if (!strcmp(path, "/chosen") && !strcmp(name, "chiron-touch-handoff")) {
        *data = proof; *size = sizeof(proof);
    } else return -1;
    return 0;
}
vm_offset_t ml_io_map(vm_offset_t physical, vm_size_t size) {
    assert(valid_platform);
    maps++;
    if (physical == 0x00100000 && size == sizeof(gcc_words)) return (uintptr_t)gcc_words;
    if (physical == 0x03d57000 && size == sizeof(gpio_words)) return (uintptr_t)gpio_words;
    if (physical == 0x17a00000 && size == sizeof(gic_words)) return (uintptr_t)gic_words;
    if (physical == 0x0c179000 && size == sizeof(qup_words)) return (uintptr_t)qup_words;
    assert(0); return 0;
}
static void panic(const char *format, ...) {
    panic_format = format; panics++; longjmp(panic_env, 1);
}
static uint64_t mach_absolute_time(void) { return 1000000; }
static void absolutetime_to_nanoseconds(uint64_t ticks, uint64_t *ns) { *ns = ticks; }
static int enabled, starts, posts, waits, blocks, terminations, waiting_result = THREAD_WAITING;
static event_t wait_event;
static bool PE_parse_boot_argn(const char *name, void *out, int size) {
    assert(size == 4);
    if (!strcmp(name, "chiron_touch_probe")) {
        *(uint32_t *)out = (uint32_t)probe_mode; return probe_mode != 0;
    }
    if (!strcmp(name, "chiron_touch_clock_probe")) {
        *(uint32_t *)out = (uint32_t)clock_mode; return clock_mode != 0;
    }
    if (!strcmp(name, "chiron_touch_pinmux_probe")) {
        *(uint32_t *)out = (uint32_t)pinmux_mode; return pinmux_mode != 0;
    }
    assert(!strcmp(name, "chiron_touch"));
    *(uint32_t *)out = (uint32_t)enabled; return enabled != 0;
}
static int kernel_thread_start(void (*fn)(void *, wait_result_t), void *arg, thread_t *t) {
    (void)fn; (void)arg; (void)t; starts++; return 1;
}
static void thread_deallocate(thread_t t) { (void)t; }
static thread_t current_thread(void) { return NULL; }
kern_return_t thread_terminate(thread_t t) { (void)t; terminations++; return 0; }
static wait_result_t assert_wait_timeout(event_t event, int mode, uint32_t n, uint32_t scale) {
    assert(event != NULL && mode == THREAD_UNINT && n == 20 && scale == NSEC_PER_MSEC);
    wait_event = event; waits++;
    return waiting_result;
}
static wait_result_t thread_block(void *continuation) {
    assert(continuation == NULL && wait_event != NULL && waiting_result == THREAD_WAITING);
    blocks++;
    return THREAD_TIMED_OUT;
}
static void redwood_touch_post(uint32_t t, uint32_t f, uint32_t x, uint32_t y) {
    (void)t; (void)f; (void)x; (void)y; posts++;
}
void redwood_tl(const char *format, ...) {
    va_list args; va_start(args, format); vprintf(format, args); va_end(args);
}
#include "redwood_goodix.c"
int main(void) {
    for (enabled = 0; enabled < 4; enabled++) redwood_goodix_init();
    assert(starts == 0 && posts == 0 && waits == 0 && blocks == 0 && maps == 0);
    chiron_touch_sleep();
    assert(waits == 1 && blocks == 1 && wait_event == (event_t)&chiron_touch);
    waiting_result = THREAD_TIMED_OUT;
    chiron_touch_sleep();
    assert(waits == 2 && blocks == 1);
    chiron_clock_delay_us(NULL, 1); /* Stalled host counter must still return. */
    assert(waits == 2 && blocks == 1);
    enabled = 1; probe_mode = 1; clock_mode = 1;
    if (!setjmp(panic_env)) {
        redwood_goodix_init();
        assert(!"probe must stop before GUI");
    }
    assert(panics == 1 && maps == 0 && starts == 0);
    assert(strstr(panic_format, "CHIRON TOUCH PROBE READ ONLY"));
    enabled = 0; /* Photo payload has no chiron_touch=1; probe must still run. */
    valid_platform = 1;
    gcc_words[0x52004/4] = 1u << 17;
    gcc_words[0x21008/4] = 1;
    gcc_words[0x21024/4] = 1;
    gpio_words[0] = gpio_words[0x1000/4] = 12;
    gpio_words[0x2000/4] = 512;
    gpio_words[0x2004/4] = 3;
    qup_words[4/4] = 0x14;
    qup_words[0x404/4] = 3u << 26;
    qup_words[0xc/4] = 0x12345678; /* Opening/resetting QUP would overwrite this. */
    memcpy(gcc_before, gcc_words, sizeof(gcc_words));
    memcpy(gpio_before, gpio_words, sizeof(gpio_words));
    memcpy(gic_before, gic_words, sizeof(gic_words));
    memcpy(qup_before, qup_words, sizeof(qup_words));
    if (!setjmp(panic_env)) {
        redwood_goodix_init();
        assert(!"ready probe must still stop without transport");
    }
    assert(panics == 2 && maps == 4 && starts == 0 && posts == 0);
    assert(chiron_board_report.issue == 0);
    assert(!memcmp(gcc_before, gcc_words, sizeof(gcc_words)));
    assert(!memcmp(gpio_before, gpio_words, sizeof(gpio_words)));
    assert(!memcmp(gic_before, gic_words, sizeof(gic_words)));
    assert(!memcmp(qup_before, qup_words, sizeof(qup_words)));
    probe_mode = 0; valid_platform = 0;
    if (!setjmp(panic_env)) {
        redwood_goodix_init();
        assert(!"clock probe must stop without a valid platform");
    }
    assert(panics == 3 && maps == 4 && starts == 0);
    assert(strstr(panic_format, "CHIRON TOUCH CLOCK PROBE"));
    assert(chiron_clock_report.changed == 0 && !chiron_clock_writes_allowed);
    valid_platform = 1;
    if (!setjmp(panic_env)) {
        redwood_goodix_init();
        assert(!"already-on clock probe must stop before starting touch");
    }
    assert(panics == 4 && maps == 4 && starts == 0 && posts == 0);
    assert(chiron_clock_report.result == 0 && chiron_clock_report.changed == 0);
    assert(!chiron_clock_writes_allowed);
    assert(!memcmp(gcc_before, gcc_words, sizeof(gcc_words)));
    assert(!memcmp(gpio_before, gpio_words, sizeof(gpio_words)));
    assert(!memcmp(gic_before, gic_words, sizeof(gic_words)));
    assert(!memcmp(qup_before, qup_words, sizeof(qup_words)));
    assert(chiron_clock_write32(NULL, CHIRON_GCC_BASE + 0x52004, 0) == -1);
    chiron_clock_writes_allowed = 1;
    assert(chiron_clock_write32(NULL, CHIRON_GPIO_BASE, 0) == -1);
    assert(chiron_clock_write32(NULL, CHIRON_GCC_BASE + 0x52004, 1u << 17) == 0);
    chiron_clock_writes_allowed = 0;
    assert(!memcmp(gpio_before, gpio_words, sizeof(gpio_words)));
    clock_mode = 0; pinmux_mode = 1;
    gpio_words[0] = 0x81; gpio_words[0x1000/4] = 0x42;
    gpio_words[0x2000/4] = 0x203; gpio_words[0x2004/4] = 0;
    memcpy(gpio_before, gpio_words, sizeof(gpio_words));
    if (!setjmp(panic_env)) { redwood_goodix_init(); assert(0); }
    assert(strstr(panic_format, "CHIRON TOUCH PINMUX PROBE"));
    assert(starts == 0 && posts == 0);
    assert(chiron_board_report.issue == CB_RESET);
    assert(chiron_board_report.failed_register == CB_RESET_LEVEL);
    gpio_before[0] |= 12; gpio_before[0x1000/4] |= 12;
    assert(!memcmp(gpio_before, gpio_words, sizeof(gpio_words)));
    assert(!memcmp(gcc_before, gcc_words, sizeof(gcc_words)));
    assert(!memcmp(gic_before, gic_words, sizeof(gic_words)));
    assert(!memcmp(qup_before, qup_words, sizeof(qup_words)));
    if (!setjmp(panic_env)) { redwood_goodix_init(); assert(0); }
    assert(!memcmp(gpio_before, gpio_words, sizeof(gpio_words)));
    /* Neither pin may be changed if its peer has an unknown mux or OE. */
    for (unsigned mode = 0; mode < 2; mode++) {
        gpio_words[0] = 0; gpio_words[0x1000/4] = mode ? 512 : 4;
        memcpy(gpio_before, gpio_words, sizeof(gpio_words));
        if (!setjmp(panic_env)) { redwood_goodix_init(); assert(0); }
        assert(!memcmp(gpio_before, gpio_words, sizeof(gpio_words)));
    }
    gcc_words[0x17000/4] = 1;
    if (!setjmp(panic_env)) { redwood_goodix_init(); assert(0); }
    assert(chiron_clock_report.result != 0);
    assert(!memcmp(gpio_before, gpio_words, sizeof(gpio_words)));
    valid_platform = 0;
    int previous_maps = maps;
    if (!setjmp(panic_env)) { redwood_goodix_init(); assert(0); }
    assert(maps == previous_maps && starts == 0 && posts == 0);
    /* Exercise the active worker, not just the diagnostic entry point. */
    valid_platform = 1; pinmux_mode = 0; enabled = 1;
    gcc_words[0x17000/4] = 0;
    gpio_words[0] = 0x81; gpio_words[0x1000/4] = 0x42;
    gpio_words[0x2004/4] = 3;
    qup_words[0] = 0x12345678;
    qup_words[8/4] = 3; /* Unsupported FIFO geometry stops before QUP writes. */
    memcpy(qup_before, qup_words, sizeof(qup_words));
    redwood_goodix_init();
    assert(starts == 1 && gpio_words[0] == 0x81);
    chiron_touch_thread(NULL, 0);
    assert(terminations == 1 && posts == 0);
    assert(gpio_words[0] == 0x8d && gpio_words[0x1000/4] == 0x4e);
    assert(gpio_words[0x2000/4] == 0x203 && gpio_words[0x2004/4] == 3);
    assert(!memcmp(qup_before, qup_words, sizeof(qup_words)));
    assert(!memcmp(gcc_before, gcc_words, sizeof(gcc_words)));
    assert(!memcmp(gic_before, gic_words, sizeof(gic_words)));
    /* Active mode also refuses an unknown peer mux before changing either pin. */
    gpio_words[0] = 0; gpio_words[0x1000/4] = 4;
    memcpy(gpio_before, gpio_words, sizeof(gpio_words));
    chiron_touch_thread(NULL, 0);
    assert(terminations == 2 && chiron_board_report.issue == CB_PINMUX);
    assert(!memcmp(gpio_before, gpio_words, sizeof(gpio_words)));
    assert(!memcmp(qup_before, qup_words, sizeof(qup_words)));
    /* The asynchronous read-only mode must retain passive pins. */
    enabled = 2;
    redwood_goodix_init();
    chiron_touch_thread(NULL, 0);
    assert(starts == 2 && terminations == 3 && posts == 0);
    assert(!memcmp(gpio_before, gpio_words, sizeof(gpio_words)));
    assert(!memcmp(qup_before, qup_words, sizeof(qup_words)));
    chiron_native.reader.refusal = (struct chiron_f12_refusal){
        .field = "F01.Control0.sleep", .address = 0x31, .bytes = 1,
        .expected = "(value & 0x03) == 0 (awake)"
    };
    chiron_layout_refusal_report();
    chiron_touch.layout.contacts = 1;
    uint8_t raw[8] = {1};
    chiron_capture_report(&chiron_touch, raw, sizeof(raw), 1, 1, 0, 15, 0);
    assert(chiron_capture_records == 1);
    chiron_capture_report(&chiron_touch, raw, sizeof(raw), 1, 1, 0, 15, 0);
    assert(chiron_capture_records == 1); /* Five-second rate limit on the stalled host clock. */
    for (unsigned i = 0; i < 100; i++)
        chiron_capture_report(&chiron_touch, raw, sizeof(raw), 1, 1, 0, 15, -5);
    assert(chiron_capture_records == 64);
    return 0;
}
""")
        program = self.folder / "adapter"
        subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                        "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                        "-I", str(includes), "-I", str(self.folder / prefix),
                        str(unit), "-o", str(program)], check=True, capture_output=True, text=True)
        result = subprocess.run([str(program)], capture_output=True, text=True, check=True)
        self.assertIn("platform refused before MMIO", result.stdout)
        self.assertIn("hardware untouched", result.stdout)
        self.assertIn("read-only, no transport or worker", result.stdout)
        self.assertIn("F12 refusal field=F01.Control0.sleep address=31 actual=00 "
                      "expected=(value & 0x03) == 0 (awake); sensor configuration write attempts=0",
                      result.stdout)
        self.assertIn("chiron touch: raw=0100000000000000", result.stdout)


class LoaderHandoffTests(unittest.TestCase):
    def test_actual_loader_owner_policy_never_takes_over_active_firmware_cpus(self):
        compiler = shutil.which("cc")
        if not compiler:
            self.skipTest("host C compiler unavailable")
        with tempfile.TemporaryDirectory(prefix="chiron-touch-owner-") as folder:
            program = Path(folder) / "owner"
            subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                            "-I", str(touch.DRIVER), "-I", str(OVERLAY),
                            str(OVERLAY / "chiron_touch_loader_host.c"), "-o", str(program)],
                           check=True, capture_output=True, text=True)
            subprocess.run([str(program)], check=True)

    def exercise_loader(self, phone, reset=False, l6=False, abi=False, capture=False):
        if not (PINNED / "xnuloader/loader.c").exists():
            self.skipTest("fixed loader source unavailable")
        base_spec = importlib.util.spec_from_file_location("touch_base", OVERLAY / "apply_chiron.py")
        base = importlib.util.module_from_spec(base_spec)
        base_spec.loader.exec_module(base)
        modules = {}
        with mock.patch.dict(sys.modules, {"apply_chiron": base}):
            for name in ("apply_chiron_usb", "apply_chiron_memory"):
                spec = importlib.util.spec_from_file_location(name, OVERLAY / (name + ".py"))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                modules[name] = module
        with tempfile.TemporaryDirectory(prefix="chiron-touch-loader-") as folder:
            source = Path(folder)
            shutil.copytree(PINNED / "xnuloader", source / "xnuloader")
            profile = json.loads((OVERLAY / "chiron-profile.json").read_text())
            base.loader_overlay(source, profile)
            if phone:
                modules["apply_chiron_usb"].apply(source)
                modules["apply_chiron_memory"].apply(source)
            report = touch.loader_overlay(source, reset_prepare=reset, l6_prepare=l6,
                                          l6_abi=abi, l6_capture=capture)
            self.assertEqual(report["firmware_mode"], "resident-not-exited")
            self.assertEqual(len(report["result"]), 5 if capture else 4 if reset or abi else 3)
            if capture:
                self.assertTrue(report["l6_capture_implemented"])
                self.assertFalse(report["calculator_spmi_hardware_calls"])
            if l6:
                generated = (source / "xnuloader/loader.c").read_text()
                self.assertTrue(report["l6_before_usb"])
                self.assertLess(generated.index("if (!chiron_l6_before_usb(pi.cmdline,"),
                                generated.index("if (have_payload && pi.usb_mode)"))
                self.assertIn("!chiron_reset_prepare(ba->commandLine,", generated)
            if reset and not l6:
                generated = (source / "xnuloader/loader.c").read_text()
                self.assertTrue(report["reset_power_probe_after_usb_start"])
                self.assertTrue(report["reset_retained_android_profile"])
                self.assertLess(generated.index('if (!usb_start())'),
                                generated.index("int power_ready ="))
                self.assertLess(generated.index("int power_ready ="),
                                generated.index("\t\tusb_command_loop();"))
            if abi:
                generated = (source / "xnuloader/loader.c").read_text()
                self.assertTrue(report["kernel_start_disabled"])
                self.assertNotIn("run_kernel(have_cp, cp)", generated)
                self.assertNotIn("run_kernel(0, 0)", generated)
                self.assertIn("L6 ABI snapshot only; kernel disabled", generated)
            with self.assertRaisesRegex(ValueError, "already applied"):
                touch.loader_overlay(source)
            compiler = shutil.which("clang")
            if compiler:
                result = subprocess.run([
                    compiler, "--target=aarch64-unknown-windows", "-ffreestanding",
                    "-fno-builtin", "-fno-stack-protector", "-fshort-wchar",
                    "-mgeneral-regs-only", "-O1", "-Wall", "-Wextra",
                    "-Wno-unused-parameter", "-c", "loader.c", "-o", "loader.obj",
                ], cwd=source / "xnuloader", capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                obj = (source / "xnuloader/loader.obj").read_bytes()
                self.assertGreater(len(obj), 10000)
                self.assertEqual(struct.unpack_from("<H", obj)[0], 0xaa64)  # ARM64 COFF
                linker = os.environ.get("CHIRON_TOUCH_LLD_LINK") or shutil.which("lld-link")
                if linker:
                    result = subprocess.run([
                        linker, "/subsystem:efi_application", "/entry:efi_main",
                        "/nodefaultlib", "/base:0xA1000000", "/out:loader.efi", "loader.obj",
                    ], cwd=source / "xnuloader", capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    image = (source / "xnuloader/loader.efi").read_bytes()
                    pe = struct.unpack_from("<I", image, 0x3c)[0]
                    self.assertEqual(image[pe:pe + 4], b"PE\0\0")
                    self.assertEqual(struct.unpack_from("<H", image, pe + 4)[0], 0xaa64)

    def test_base_loader_overlay_and_full_arm64_compile(self):
        self.exercise_loader(phone=False)

    def test_phone_loader_composes_after_usb_memory_and_full_arm64_compile(self):
        self.exercise_loader(phone=True)

    def test_reset_loader_composes_and_compiles_for_arm64(self):
        self.exercise_loader(phone=True, reset=True)

    def test_l6_loader_composes_and_compiles_for_arm64(self):
        self.exercise_loader(phone=True, reset=True, l6=True)

    def test_read_only_l6_abi_loader_disables_kernel_and_compiles_for_arm64(self):
        self.exercise_loader(phone=True, abi=True)

    def test_l6_capture_disables_kernel_and_compiles_for_arm64(self):
        self.exercise_loader(phone=True, abi=True, capture=True)


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
