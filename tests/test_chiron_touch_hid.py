"""Exercise the real pinned replay producer, including cleanup after write failure."""

import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "touch_hid", ROOT / "ci/hackios/apply_chiron_touch_hid.py")
hid = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hid)
UPSTREAM = Path("/private/tmp/chiron-hackios-reuse/userland/userland/src")


class ReplayTests(unittest.TestCase):
    def test_small_root_repack_changes_only_jobs_and_replay_binary(self):
        baseline = ROOT / "dist/chiron-hackios-39915264473/phone/root.apfs"
        source = UPSTREAM.parents[1]
        if not baseline.is_file() or not (source / "tools/apfs/apfs.py").is_file():
            self.skipTest("local baseline or pinned APFS tools unavailable")
        sys.path.insert(0, str(hid.HERE))
        import cloud
        command = cloud.command

        def compile_or_run(args):
            if args[0] == "host-test-compiler":
                Path(args[-1]).write_bytes(bytes.fromhex("cffaedfe0c00000100000000") + b"test")
            else:
                command(args)

        with tempfile.TemporaryDirectory(prefix="chiron-hid-root-") as folder:
            with mock.patch.object(cloud, "require_runner"), \
                 mock.patch.object(cloud, "command", side_effect=compile_or_run):
                _, proof = hid.rebuild_root(
                    baseline, Path(folder) / "result", source, "host-test-compiler", "sdk", "ld")
            self.assertEqual(proof["logical_readback_changed"], ["/bin/rwtouch", "/etc/rc.jobs"])
            self.assertFalse(proof["runtime_qualified"])

    def test_unknown_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "pinned"):
            hid.replay_source(b"other source")

    def test_single_finite_job_never_consumes_the_queue(self):
        lines = [s for s in (hid.HERE / "chiron_touch_hid.jobs").read_text().splitlines()
                 if s and not s.startswith("#")]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].split(),
                         ["/redwood/bin/rwtouch", "wait", "90", "button", "home", "80",
                          "wait", "5", "swipe", "0.5", "0.985", "0.5", "0.3", "500"])

    def test_actual_producer_releases_after_failed_move_and_has_no_duplicate_up(self):
        if not (UPSTREAM / "rwtouch.c").exists() or not shutil.which("cc"):
            self.skipTest("pinned upstream replay source or host compiler unavailable")
        with tempfile.TemporaryDirectory(prefix="chiron-hid-") as folder:
            p = Path(folder)
            for name, content in {
                "pthread.h": "",
                "mach/mach_time.h": """
#include <stdint.h>
typedef struct { uint32_t numer, denom; } mach_timebase_info_data_t;
static uint64_t mach_absolute_time(void) { static uint64_t t; return t += 1000000; }
static int mach_timebase_info(mach_timebase_info_data_t *t) { t->numer=t->denom=1; return 0; }
""",
                "notify.h": """
#define NOTIFY_STATUS_OK 0
static int notify_post(const char *s) { (void)s; return 0; }
""",
                "sys/qos.h": """
#define QOS_CLASS_USER_INTERACTIVE 1
static int pthread_set_qos_class_self_np(int q, int p) { (void)q; (void)p; return 0; }
""",
            }.items():
                target = p / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            (p / "redwood_input.h").write_bytes((UPSTREAM / "redwood_input.h").read_bytes())
            (p / "replay.c").write_bytes(hid.replay_source((UPSTREAM / "rwtouch.c").read_bytes()))
            (p / "host.c").write_text(r"""
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <unistd.h>
#include <setjmp.h>
#include "redwood_input.h"
static jmp_buf failed;
static int writes, fail_at;
static struct redwood_touch last;
static ssize_t test_write(int fd, const void *data, size_t size) {
    (void)fd; assert(size == sizeof(last)); last=*(const struct redwood_touch *)data;
    writes++; return writes == fail_at ? -1 : (ssize_t)size;
}
static void test_exit(int code) { assert(code == 1); longjmp(failed, 1); }
#define write test_write
#define exit test_exit
#define main replay_main
#include "replay.c"
#undef main
int main(void) {
    post(REDWOOD_TOUCH_DOWN, .8, .5);
    post(REDWOOD_TOUCH_MOVE, .2, .5);
    post(REDWOOD_TOUCH_UP, .2, .5);
    chiron_replay_release(); assert(writes == 3);
    post(REDWOOD_TOUCH_DOWN, .8, .5);
    fail_at = writes + 1;
    if (!setjmp(failed)) { post(REDWOOD_TOUCH_MOVE, .2, .5); assert(0); }
    chiron_replay_release();
    assert(writes == 6 && last.type == REDWOOD_TOUCH_UP && last.finger == 0);
    assert(last.x == 13107 && last.y == 32768 && !chiron_replay_down);
    chiron_replay_release(); assert(writes == 6);
    fail_at = writes + 1;
    if (!setjmp(failed)) { post(REDWOOD_TOUCH_DOWN, .8, .5); assert(0); }
    chiron_replay_release();
    assert(writes == 8 && last.type == REDWOOD_TOUCH_UP && last.x == 52428);
    return 0;
}
""")
            cmd = ["cc", "-std=c11", "-D_DEFAULT_SOURCE", "-Wall", "-Wextra", "-Werror",
                   "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                   "-I", str(p), str(p / "host.c"), "-o", str(p / "test")]
            r = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run([str(p / "test")], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
