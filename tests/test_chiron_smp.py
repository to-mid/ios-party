"""Execute SMP admission logic, kernel guards, and real fixed-source overlays."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
OVERLAYS = ROOT / "ci/hackios"
sys.path.insert(0, str(OVERLAYS))
import apply_chiron_smp as smp


LOADER_HOST = r"""
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
#define PSCI_AFFINITY_INFO 0xc4000004u
static struct {
    int psci_hvc;
    uint64_t gic_dist, gic_redist;
    uint32_t virtual_timer_intid;
} platform = { 0, 0x17a00000, 0x17b00000, 19 }, *PLAT = &platform;
static uint64_t typers[8];
static int64_t states[8], version;
static int queries, reads;
static void print(const char *format, ...) { (void)format; }
static int starts_with(const char *text, const char *prefix) {
    return strncmp(text, prefix, strlen(prefix)) == 0;
}
static int64_t psci_call(uint64_t fn, uint64_t a, uint64_t b, uint64_t c) {
    assert(b == 0 && c == 0);
    if (fn == 0x84000000) { assert(a == 0); return version; }
    assert(fn == PSCI_AFFINITY_INFO);  /* A preflight must never issue CPU_ON. */
    queries++;
    for (int i = 0; i < 8; i++)
        if (a == (uint64_t)(i < 4 ? i : i + 252)) return states[i];
    assert(0); return -1;
}
static uint64_t read_typer(uint64_t offset) {
    assert(offset < 0x100000 && offset % 0x20000 == 0);
    reads++;
    return typers[offset / 0x20000];
}
"""

LOADER_MAIN = r"""
static const uint64_t image[4] = { 0, CHIRON_SMP_ABI_MAGIC, CHIRON_SMP_ABI_CHECK, 0 };
static const uint64_t no_abi[4] = { 0 };
static void reset(void) {
    queries = reads = 0; version = 0x10000;
    for (int i = 0; i < 8; i++) {
        states[i] = 1;
        typers[i] = (uint64_t)(i < 4 ? i : i + 252) << 32;
    }
    typers[7] |= 16;
}
static int prepare(const char *args, uint64_t boot, uint64_t kernel,
                   const uint64_t *blob, int *count) {
    uint64_t out[10] = {0};
    out[8] = 0xabc; out[9] = 0xdef;
    int result = chiron_smp_prepare(args, boot, kernel, (uintptr_t)blob,
        (uintptr_t)(blob + 4), out, count);
    assert(out[8] == 0xabc && out[9] == 0xdef);
    if (result) for (int i = 0; i < *count; i++) assert(out[i] == chiron_smp_mpidrs[i]);
    else assert(*count == 0);
    return result;
}
int main(void) {
    const char *bad[] = {
        "cpus=0", "cpus=3", "cpus=5", "cpus=7", "cpus=9", "cpus=16", "cpus=01",
        "cpus=-1", "cpus=2x", "cpus=", "cpus=2 cpus=2", "cpus=1 cpus=8",
        "cpumask=255", "cpus=2 cpumask=3", "bootcpu=0", "bootcpu=4",
        "cpus=2\tcpus=8", "x\ncpus=2", "cpus=2\r", "cpus=0x2",
        "cpus", "cpus =2", "cpus=8 cpus", "cpumask", "bootcpu"
    };
    int count;
    for (unsigned i = 0; i < sizeof(bad)/sizeof(bad[0]); i++)
        assert(!chiron_smp_parse(bad[i], &count));
    assert(chiron_smp_parse("debug=8 xcpus=8 cpus_hint=8", &count) && count == 1);
    assert(chiron_smp_parse("   ", &count) && count == 1);
    for (int n = 1; n <= 8; n *= 2) {
        char args[32]; snprintf(args, sizeof(args), "cpus=%d", n);
        reset();
        assert(prepare(args, 0, 0, image, &count) == (n <= CHIRON_SMP_LIMIT));
        if (n <= CHIRON_SMP_LIMIT) {
            assert(count == n && queries == (n == 1 ? 0 : n - 1));
            assert(reads == (n == 1 ? 0 : 8));
        }
    }
    reset();
    assert(prepare("", 0, 0, no_abi, &count) && count == 1 && queries == 0 && reads == 0);
    assert(!prepare("", 1, 1, image, &count));
    assert(!prepare("", 0x100000000ULL, 0, image, &count));
    assert(!prepare("", 0, 256, image, &count));
    assert(!chiron_smp_kernel_capable(16, 8));
    assert(!chiron_smp_kernel_capable((uintptr_t)image + 1, (uintptr_t)(image + 4)));
    if (CHIRON_SMP_LIMIT == 1) return 0;
    assert(!prepare("cpus=2", 0, 0, no_abi, &count));
    for (int64_t state = -9; state <= 2; state++) {
        reset(); states[1] = state;
        assert(prepare("cpus=2", 0, 0, image, &count) == (state == 1));
    }
    const int64_t versions[] = { -1, 0, 1, 2, 0x10000, 0x10003, 0x20000 };
    for (unsigned i = 0; i < sizeof(versions)/sizeof(versions[0]); i++) {
        reset(); version = versions[i];
        assert(prepare("cpus=2", 0, 0, image, &count) ==
            (version == 2 || version == 0x10000 || version == 0x10003));
    }
    reset(); platform.psci_hvc = 1;
    assert(!prepare("cpus=2", 0, 0, image, &count)); platform.psci_hvc = 0;
    reset(); typers[1] = 16;  /* duplicate, and LAST before the required target */
    assert(!prepare("cpus=2", 0, 0, image, &count));
    reset(); typers[0] |= 2; /* GICv4 frame stride not supported */
    assert(!prepare("cpus=2", 0, 0, image, &count));
    reset(); typers[1] = ((uint64_t)0xdead << 32) | 16;
    assert(!prepare("cpus=2", 0, 0, image, &count));
    reset();
    /* Find affinity rather than assuming redistributor order equals CPU order. */
    for (int i = 0; i < 8; i++) typers[7 - i] = chiron_smp_mpidrs[i] << 32;
    typers[7] |= 16;
    assert(prepare("cpus=2", 0, 0, image, &count));
    if (CHIRON_SMP_LIMIT == 8) {
        reset(); states[4] = 0;
        assert(!prepare("cpus=8", 0, 0, image, &count));
        assert(queries == 4 && reads == 0);
    }
    return 0;
}
"""

KERNEL_HOST = r"""
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <setjmp.h>
typedef int DTEntry;
typedef struct { uint32_t phys_id; unsigned cluster_core_id, die_cluster_id; } cpu_t;
typedef struct { uint32_t num_cpus; cpu_t *cpus; cpu_t *boot_cpu; } ml_topology_info_t;
static cpu_t cpus[8];
static ml_topology_info_t topology = { 0, cpus, cpus };
static uint32_t psci_smp_hvc, selected, present, property_size = 4;
static int64_t state = 1;
static jmp_buf panic_env;
static int can_panic, queries;
#define kSuccess 0
#define PSCI_AFFINITY_INFO 0xc4000004u
#define PSCI_AFFINITY_OFF 1
static void panic(const char *message, ...) {
    (void)message;
    assert(can_panic); longjmp(panic_env, 1);
}
static int SecureDTLookupEntry(void *root, const char *path, DTEntry *entry) {
    (void)root; (void)path; *entry = 1; return 0;
}
static int SecureDTGetProperty(DTEntry entry, const char *name, const void **p, unsigned *size) {
    (void)entry; (void)name; *p = &selected; *size = property_size; return present ? 0 : -1;
}
static const ml_topology_info_t *ml_get_topology_info(void) { return &topology; }
static int64_t psci_call(uint64_t fn, uint64_t a, uint64_t b, uint64_t c) {
    assert(fn == PSCI_AFFINITY_INFO && a == cpus[1].phys_id && b == 0 && c == 0);
    queries++; return state;
}
"""

KERNEL_MAIN = r"""
static void validate_bad(void) {
    can_panic = 1;
    if (setjmp(panic_env) == 0) { chiron_smp_validate_topology(); assert(0); }
    can_panic = 0;
}
static void start_bad(int cpu, uint32_t phys_id) {
    can_panic = 1;
    if (setjmp(panic_env) == 0) { chiron_smp_check_start(cpu, phys_id); assert(0); }
    can_panic = 0;
}
int main(void) {
    for (int i = 0; i < 8; i++) {
        cpus[i].phys_id = i < 4 ? (uint32_t)i : (uint32_t)i + 252;
        cpus[i].cluster_core_id = i % 4; cpus[i].die_cluster_id = i / 4;
    }
    chiron_smp_validate_topology(); assert(chiron_smp_expected_cpus == 0);
    present = 1;
    for (selected = 1; selected <= 8; selected *= 2) {
        topology.num_cpus = selected;
        chiron_smp_validate_topology(); assert(chiron_smp_expected_cpus == selected);
    }
    selected = 3; topology.num_cpus = 3; validate_bad();
    selected = 2; topology.num_cpus = 2; property_size = 8; validate_bad(); property_size = 4;
    topology.num_cpus = 1; validate_bad(); topology.num_cpus = 2;
    topology.boot_cpu = cpus + 1; validate_bad(); topology.boot_cpu = cpus;
    psci_smp_hvc = 1; validate_bad(); psci_smp_hvc = 0;
    cpus[1].phys_id = 256; validate_bad(); cpus[1].phys_id = 1;
    cpus[1].die_cluster_id = 1; validate_bad(); cpus[1].die_cluster_id = 0;
    chiron_smp_validate_topology();
    chiron_smp_check_start(1, 1); assert(queries == 1);
    start_bad(-1, 1); start_bad(0, 0); start_bad(2, 2); start_bad(8, 259); start_bad(1, 256);
    assert(queries == 1);
    for (state = -9; state <= 2; state++) if (state != 1) start_bad(1, 1);
    return 0;
}
"""


class HostTests(unittest.TestCase):
    def compile_and_run(self, text, flags=(), expected_returncode=0):
        compiler = shutil.which("cc")
        if not compiler:
            self.skipTest("host C compiler unavailable")
        with tempfile.TemporaryDirectory(prefix="chiron-smp-host-") as directory:
            source = Path(directory) / "test.c"
            program = Path(directory) / "test"
            source.write_text(text)
            result = subprocess.run(
                [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", *flags,
                 str(source), "-o", str(program)], capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(program)], capture_output=True, text=True)
            self.assertEqual(result.returncode, expected_returncode, result.stdout + result.stderr)

    def test_actual_loader_admission_for_all_build_limits(self):
        include = (OVERLAYS / "chiron_smp_loader.inc").read_text()
        # Substitute only the hardware MMIO read. All admission code runs unchanged.
        read = "return *(volatile uint64_t *)(uintptr_t)(PLAT->gic_redist + offset + 8);"
        self.assertEqual(include.count(read), 1)
        include = include.replace(read, "return read_typer(offset);")
        for count in smp.CPU_COUNTS:
            with self.subTest(limit=count):
                self.compile_and_run(LOADER_HOST + include + LOADER_MAIN,
                                     [f"-DCHIRON_SMP_LIMIT={count}"])

    def test_actual_kernel_topology_and_cpu_on_guards(self):
        self.compile_and_run(KERNEL_HOST + (OVERLAYS / "chiron_smp_kernel.inc").read_text()
                             + KERNEL_MAIN)

    def test_supported_api_counts(self):
        for count in (True, 0, 3, 6, 16, "2", None):
            with self.subTest(count=count):
                with self.assertRaises(ValueError):
                    smp.command_line(count)
        for count in smp.CPU_COUNTS:
            self.assertEqual(smp.command_line(count), f"cpus={count}")


class FixedSourceTests(unittest.TestCase):
    """Optional local integration checks, never modify the shared source snapshots."""

    compile_and_run = HostTests.compile_and_run

    @classmethod
    def setUpClass(cls):
        cls.loader_root = Path(os.environ.get(
            "CHIRON_SMP_TEST_SOURCE", "/private/tmp/chiron-hackios-reuse/kernel"))
        cls.xnu_root = Path(os.environ.get("CHIRON_SMP_TEST_XNU", "/private/tmp/chiron-port-xnu"))
        if not (cls.loader_root / "xnuloader/loader.c").is_file():
            raise unittest.SkipTest("fixed HackiOS source unavailable; host logic tests still run")
        cls.compiler = next((str(path) for path in (
            Path("/usr/local/opt/llvm/bin/clang"),
            Path("/usr/local/Cellar/llvm/21.1.4/bin/clang"),
            Path(shutil.which("clang") or "/missing"),
        ) if path.is_file()), None)
        import apply_chiron
        import apply_chiron_usb
        import apply_chiron_memory
        cls.base = staticmethod(apply_chiron.loader_overlay)
        cls.usb = staticmethod(apply_chiron_usb.apply)
        cls.memory = staticmethod(apply_chiron_memory.apply)

    def loader_tree(self, folder, usb, memory):
        target = folder / "xnuloader"
        target.mkdir()
        for name in ("loader.c", "efi.h", "identity.h"):
            shutil.copy2(self.loader_root / "xnuloader" / name, target / name)
        profile = json.loads((OVERLAYS / "chiron-profile.json").read_text())
        self.base(folder, profile)
        if usb:
            self.usb(folder)
        if memory:
            self.memory(folder)
        return target

    def test_full_loader_arm64_compilation_and_composability(self):
        if not self.compiler:
            self.skipTest("ARM64 clang unavailable")
        for usb, memory in ((False, False), (True, False), (True, True)):
            for count in smp.CPU_COUNTS:
                with self.subTest(usb=usb, memory=memory, count=count):
                    with tempfile.TemporaryDirectory(prefix="chiron-smp-loader-") as directory:
                        root = Path(directory)
                        target = self.loader_tree(root, usb, memory)
                        baseline = (target / "loader.c").read_text()
                        report = smp.loader_overlay(root, count)
                        json.dumps(report)
                        self.assertFalse(report["hardware_observed"])
                        self.assertEqual(report["mpidrs"], list(smp.MPIDRS[:count]))
                        self.assertIn(".cores = 1", baseline)
                        result = subprocess.run([
                            self.compiler, "--target=aarch64-unknown-windows", "-ffreestanding",
                            "-fno-builtin", "-fno-stack-protector", "-fshort-wchar",
                            "-mgeneral-regs-only", "-O1", "-Wall", "-Wextra",
                            "-Wno-unused-parameter", "-Werror", "-c",
                            str(target / "loader.c"), "-o", str(root / "loader.obj"),
                        ], capture_output=True, text=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertGreater((root / "loader.obj").stat().st_size, 0)
                        with self.assertRaises(ValueError):
                            smp.loader_overlay(root, count)

    def test_two_core_loader_composes_touch_reset_without_relaxing_off_preflight(self):
        if not self.compiler:
            self.skipTest("ARM64 clang unavailable")
        import apply_chiron_touch
        with tempfile.TemporaryDirectory(prefix="chiron-touch-smp2-loader-") as directory:
            root = Path(directory)
            target = self.loader_tree(root, True, True)
            smp.loader_overlay(root, 2)
            apply_chiron_touch.loader_overlay(root, reset_prepare=True)
            text = (target / "loader.c").read_text()
            self.assertIn(".cores = 2", text)
            self.assertLess(text.index("chiron_smp_prepare(pi.cmdline,"),
                            text.index("chiron_touch_loader_owner(ba->commandLine,"))
            owner = (target / "chiron_touch_loader.inc").read_text()
            self.assertIn("state != 1", owner)
            self.assertIn("affinities[7]", owner)
            result = subprocess.run([
                self.compiler, "--target=aarch64-unknown-windows", "-ffreestanding",
                "-fno-builtin", "-fno-stack-protector", "-fshort-wchar",
                "-mgeneral-regs-only", "-O1", "-Wall", "-Wextra",
                "-Wno-unused-parameter", "-Wno-unused-function", "-Werror", "-c",
                str(target / "loader.c"), "-o", str(root / "loader.obj"),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def copy_kernel_inputs(self, root):
        if not (self.xnu_root / "osfmk/arm64/psci_smp.c").is_file():
            self.skipTest("fully applied XNU source unavailable")
        paths = (
            "pexpert/pexpert/arm64/BCM2837.h", "pexpert/pexpert/arm64/board_config.h",
            "osfmk/arm64/machine_routines.c", "osfmk/arm64/psci_smp.c",
            "osfmk/arm64/gic_v3_irq.c", "osfmk/arm64/start.s", "makedefs/MakeInc.def",
            "osfmk/kern/sched_clutch.c",
        )
        for relative in paths:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.xnu_root / relative, destination)

    def test_kernel_overlay_atomic_context_checks_and_board_macro_order(self):
        with tempfile.TemporaryDirectory(prefix="chiron-smp-kernel-") as directory:
            root = Path(directory)
            self.copy_kernel_inputs(root)
            start = (root / "osfmk/arm64/start.s").read_bytes()
            report = smp.kernel_overlay(root)
            json.dumps(report)
            self.assertEqual(report["count_source"], "/chosen/chiron-smp-cpu-count")
            self.assertEqual((root / "osfmk/arm64/start.s").read_bytes(), start)
            machine = (root / "osfmk/arm64/machine_routines.c").read_text()
            self.assertEqual(machine.count("#if HAS_CLUSTER || defined(CHIRON_SMP)"), 5)
            with self.assertRaises(ValueError):
                smp.kernel_overlay(root)
            # Preprocess the real board_config header with its real include dependencies.
            # Search the patched pexpert first and the full snapshot second.
            text = r"""
#include <pexpert/arm64/board_config.h>
#if MAX_CPUS != 8
#error wrong CPU capacity
#endif
#if defined(CHIRON_PORT)
#if !defined(CHIRON_SMP) || MAX_CPU_CLUSTERS != 2 || MAX_CPU_CLUSTER_PHY_ID != 1
#error SMP macro must be visible before cluster sizing
#endif
#else
#if defined(CHIRON_SMP) || MAX_CPU_CLUSTERS != 1
#error unrelated BCM2837 baseline changed
#endif
#endif
#if HAS_CLUSTER
#error Apple-only cluster instructions must stay disabled
#endif
int main(void) { return 0; }
"""
            for flags in ([], ["-DCHIRON_PORT=1"]):
                self.compile_board(root, text, flags)

    def test_clutch_multicluster_idle_releases_pset_before_caller_relocks(self):
        with tempfile.TemporaryDirectory(prefix="chiron-smp-clutch-") as directory:
            root = Path(directory)
            self.copy_kernel_inputs(root)
            original = (root / "osfmk/kern/sched_clutch.c").read_text()
            smp.kernel_overlay(root)
            patched = (root / "osfmk/kern/sched_clutch.c").read_text()
            def callback(text):
                begin = text.index("static thread_t\nsched_clutch_steal_thread(__unused")
                return text[begin:text.index("\n}\n", begin) + 3]
            host = r"""
#include <assert.h>
#include <stddef.h>
#ifndef __unused
#define __unused __attribute__((unused))
#endif
#define THREAD_NULL NULL
typedef void *thread_t;
typedef struct pset { unsigned map; int locked; } *processor_set_t;
static void __unused pset_unlock(processor_set_t pset) {
    assert(pset->locked); pset->locked = 0;
}
"""
            main = r"""
int main(void) {
    for (unsigned map = 1; map <= 3; map += 2) {
        struct pset data = { map, 1 };
        processor_set_t pset = &data;
        if ((pset->map & (pset->map - 1)) != 0) {
            assert(sched_clutch_steal_thread(pset) == THREAD_NULL);
            if (pset->locked) return 42; /* caller would recursively lock its pset */
            pset->locked = 1;
        }
        assert(pset->locked);
        pset_unlock(pset);
    }
    return 0;
}
"""
            self.compile_and_run(host + callback(original) + main, expected_returncode=42)
            self.compile_and_run(host + callback(patched) + main, expected_returncode=42)
            self.compile_and_run(host + callback(patched) + main, ["-DCHIRON_SMP=1"])

    def test_clutch_shared_bucket_lock_preserves_both_physical_clusters(self):
        with tempfile.TemporaryDirectory(prefix="chiron-smp-pset-") as directory:
            root = Path(directory)
            self.copy_kernel_inputs(root)
            original = (root / "osfmk/arm64/machine_routines.c").read_text()
            smp.kernel_overlay(root)
            patched = (root / "osfmk/arm64/machine_routines.c").read_text()
            clutch = (root / "osfmk/kern/sched_clutch.c").read_text()
            begin = clutch.index("static void\nsched_clutch_bucket_group_thr_count_inc(",
                                 clutch.index("#else /* CONFIG_SCHED_EDGE */",
                                              clutch.index("sched_clutch_bucket_group_thr_count_inc(")))
            increment = clutch[begin:clutch.index("\n}\n", begin) + 3]
            def lookup(text):
                begin = text.index("\tlck_mtx_lock(&pset_create_lock);")
                begin = text.index("\n", begin) + 1
                return text[begin:text.index("\tkprintf(", begin)]
            host = r"""
#include <assert.h>
#include <setjmp.h>
#include <stddef.h>
#include <stdint.h>
typedef struct { int locked; } root_t;
struct pset { root_t pset_clutch_root; };
static struct pset pset0, pset1;
typedef struct pset *processor_set_t;
struct bucket { struct { unsigned scct_count; uint64_t scct_timestamp; } scbg_pending_data; };
typedef struct bucket *sched_clutch_bucket_group_t;
static jmp_buf bad_lock;
static void sched_clutch_hierarchy_locked_assert(root_t *root) {
    if (!root->locked) longjmp(bad_lock, 1);
}
static processor_set_t __attribute__((unused)) pset_find(int cluster, void *unused) {
    (void)unused; return cluster ? &pset1 : &pset0;
}
"""
            registration = r"""
static processor_set_t register_cpu(unsigned cluster) {
    struct { unsigned cluster_id; } data = { cluster }, *in_processor_info = &data;
    struct { unsigned cpu_cluster_id; } cpu_data, *this_cpu_datap = &cpu_data;
    processor_set_t pset;
"""
            assignment = "\tthis_cpu_datap->cpu_cluster_id = in_processor_info->cluster_id;\n"
            self.assertIn(assignment, original)
            self.assertIn(assignment, patched)
            main = r"""
    assert(this_cpu_datap->cpu_cluster_id == cluster);
    return pset;
}
int main(void) {
    struct bucket group = {0};
    if (setjmp(bad_lock)) return 42;
    for (unsigned cpu = 0; cpu < 8; cpu++) {
        processor_set_t pset = register_cpu(cpu / 4);
        pset->pset_clutch_root.locked = 1;
        sched_clutch_bucket_group_thr_count_inc(&group, cpu + 1);
        pset->pset_clutch_root.locked = 0;
    }
    assert(group.scbg_pending_data.scct_count == 8);
    return 0;
}
"""
            self.compile_and_run(host + increment + registration +
                                 assignment + lookup(original) + main, expected_returncode=42)
            fixed = (host + increment + registration +
                     assignment + lookup(patched) + main)
            self.compile_and_run(fixed, ["-DCHIRON_SMP=1", "-DCONFIG_SCHED_CLUTCH=1",
                                        "-DCONFIG_SCHED_EDGE=0"])
            self.compile_and_run(fixed, ["-DCONFIG_SCHED_CLUTCH=1"], expected_returncode=42)

    def test_strict_touch_return_never_resumes_firmware_without_off_confirmation(self):
        with tempfile.TemporaryDirectory(prefix="chiron-touch-smp-return-") as directory:
            root = Path(directory)
            self.copy_kernel_inputs(root)
            report = smp.kernel_overlay(root, strict_firmware_return=True)
            self.assertTrue(report["strict_firmware_return"])
            text = (root / "osfmk/arm64/psci_smp.c").read_text()
            self.assertNotIn("state == PSCI_AFFINITY_OFF || state < 0", text)
            begin = text.index("\t\tif (psci_call(PSCI_AFFINITY_INFO, cdp->cpu_phys_id, 0, 0) != PSCI_AFFINITY_OFF)")
            end = text.index("\n\t}\n}", begin)
            gate = text[begin:end]
            self.compile_and_run(r"""
#include <assert.h>
#include <setjmp.h>
#define PSCI_AFFINITY_INFO 4
#define PSCI_AFFINITY_OFF 1
static int state, notes, resumed;
static jmp_buf parked;
static int psci_call(int fn,int id,int a,int b) {
    assert(fn==4 && id==1 && !a && !b); return state;
}
static void psci_smp_note(const char *s,int cpu) { (void)s; assert(cpu==1); notes++; }
#define __builtin_arm_wfi() longjmp(parked,1)
static void returning(void) {
    struct {int cpu_phys_id;} data={1},*cdp=&data;
    int cpu=1;
""" + gate + r"""
    resumed++;
}
int main(void) {
    state=1; returning(); assert(resumed==1 && !notes);
    const int rejected[]={0,2,-1,-2};
    for(unsigned i=0;i<4;i++) {
        state=rejected[i];
        if(!setjmp(parked)) {returning(); assert(0);}
        assert(resumed==1 && notes==(int)i+1);
    }
    return 0;
}
""")

    def compile_board(self, root, text, flags):
        compiler = shutil.which("cc")
        if not compiler:
            self.skipTest("host C compiler unavailable")
        result = subprocess.run(
            [compiler, "-E", "-x", "c", "-DARM64_BOARD_CONFIG_BCM2837",
             "-I", str(root / "pexpert"), "-I", str(self.xnu_root / "pexpert"),
             *flags, "-"], input=text, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_xnu_cluster_mapping_and_gic_sgi_fields(self):
        with tempfile.TemporaryDirectory(prefix="chiron-smp-topology-") as directory:
            root = Path(directory)
            self.copy_kernel_inputs(root)
            smp.kernel_overlay(root)
            machine = (root / "osfmk/arm64/machine_routines.c").read_text()
            begin = machine.index("#if HAS_CLUSTER || defined(CHIRON_SMP)\n"
                                  "\t\tuint32_t phys_cluster_id")
            end = machine.index("\n\t\tif (is_boot_cpu)", begin)
            per_cpu = machine[begin:end]
            begin = machine.index("#if HAS_CLUSTER || defined(CHIRON_SMP)\n\t/*\n"
                                  "\t * Build the cluster offset")
            end = machine.index("\n\tassert(topology_info.boot_cpu != NULL);", begin)
            offsets = machine[begin:end]
            # Execute the actual patched topology statements. Only DT reads are doubles.
            prelude = r"""
#include <assert.h>
#include <stdint.h>
#include <string.h>
#define CHIRON_SMP 1
#define MAX_CPUS 8
#define MAX_CPU_CLUSTERS 2
#define MAX_CPU_CLUSTER_PHY_ID 1
#define MPIDR_CLUSTER_ID(x) (((x) >> 8) & 255)
#define MPIDR_CPU_ID(x) ((x) & 255)
#define MAX(a,b) ((a) > (b) ? (a) : (b))
#define ml_readprop(child, name, fallback) ((void)(child), (void)(name), (fallback))
typedef struct {
    unsigned cpu_id, phys_id, cluster_id, cluster_type, die_cluster_id, cluster_core_id;
} ml_topology_cpu_t;
typedef struct {
    unsigned num_cpus, cluster_id, cluster_type, first_cpu_id;
    uint64_t cpu_mask, acc_IMPL_pa, acc_IMPL_len, cpm_IMPL_pa, cpm_IMPL_len;
} ml_topology_cluster_t;
static struct {
    unsigned num_clusters, max_cluster_id, cluster_types, cluster_type_num_clusters[1];
    ml_topology_cluster_t clusters[2];
} topology_info;
static int64_t cluster_offsets[2], cluster_phys_to_logical[2], cluster_max_cpu_phys_id[2];
static void ml_read_reg_range(int child, const char *name, uint64_t *pa, uint64_t *len) {
    (void)child; (void)name; *pa = *len = 0;
}
static void map_cpu(ml_topology_cpu_t *cpu) {
    int child = 0;
"""
            tail = r"""
}
int main(void) {
    for (int count = 1; count <= 8; count *= 2) {
        memset(&topology_info, 0, sizeof(topology_info));
        for (int i = 0; i < 2; i++) {
            cluster_offsets[i] = cluster_phys_to_logical[i] = -1;
            cluster_max_cpu_phys_id[i] = 0;
        }
        ml_topology_cpu_t cpus[8] = {0};
        for (int i = 0; i < count; i++) {
            cpus[i].cpu_id = i; cpus[i].phys_id = i < 4 ? (unsigned)i : (unsigned)i + 252;
            map_cpu(&cpus[i]);
            assert(cpus[i].cluster_id == (unsigned)i / 4);
            assert(cpus[i].die_cluster_id == (unsigned)i / 4);
            assert(cpus[i].cluster_core_id == (unsigned)i % 4);
        }
        make_offsets();
        assert(topology_info.num_clusters == (count == 8 ? 2u : 1u));
        assert(cluster_offsets[0] == 0 && cluster_offsets[1] == (count == 8 ? 4 : -1));
        for (int i = 0; i < count; i++)
            assert(cluster_offsets[cpus[i].phys_id >> 8] + (cpus[i].phys_id & 255) == i);
        assert(topology_info.clusters[0].cpu_mask == ((1u << (count > 4 ? 4 : count)) - 1));
        if (count == 8) assert(topology_info.clusters[1].cpu_mask == 0xf0);
    }
    return 0;
}
"""
            self.compile_and_run(prelude + per_cpu + "\n}\nstatic void make_offsets(void) {\n"
                                 + offsets + tail)
            gic = (root / "osfmk/arm64/gic_v3_irq.c").read_text()
            begin = gic.index("static void\ngic_send_sgi(")
            end = gic.index("\n}\n", begin) + 3
            sgi_host = r"""
#include <assert.h>
#include <stdint.h>
#include <setjmp.h>
#define CHIRON_SMP 1
#define ISB_SY 15
static uint64_t sent;
static jmp_buf fault;
static void panic(const char *message) { (void)message; longjmp(fault, 1); }
#define __builtin_arm_wsr64(name, value) ((void)(name), sent = (value))
#define __builtin_arm_isb(value) ((void)(value))
"""
            sgi_main = r"""
int main(void) {
    for (unsigned i = 0; i < 8; i++) {
        uint32_t mpidr = i < 4 ? i : i + 252;
        for (unsigned sgi = 0; sgi < 2; sgi++) {
            gic_send_sgi(mpidr, sgi);
            assert((sent & 0xffff) == (1u << (i % 4)));
            assert(((sent >> 16) & 255) == i / 4);
            assert(((sent >> 24) & 15) == sgi);
        }
    }
    if (setjmp(fault) == 0) { gic_send_sgi(16, 0); assert(0); }
    if (setjmp(fault) == 0) { gic_send_sgi(0x10000, 0); assert(0); }
    if (setjmp(fault) == 0) { gic_send_sgi(0, 16); assert(0); }
    return 0;
}
"""
            self.compile_and_run(sgi_host + gic[begin:end] + sgi_main)

    def test_late_kernel_context_failure_does_not_publish_partial_edits(self):
        with tempfile.TemporaryDirectory(prefix="chiron-smp-failure-") as directory:
            root = Path(directory)
            self.copy_kernel_inputs(root)
            gic = root / "osfmk/arm64/gic_v3_irq.c"
            gic.write_text(gic.read_text().replace("virtual-timer-intid", "unknown-timer-intid"))
            before = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            with self.assertRaises(ValueError):
                smp.kernel_overlay(root)
            self.assertEqual(before, {str(path): path.read_bytes()
                                      for path in root.rglob("*") if path.is_file()})

    def test_invalid_loader_context_does_not_mutate(self):
        with tempfile.TemporaryDirectory(prefix="chiron-smp-context-") as directory:
            root = Path(directory)
            target = root / "xnuloader"
            target.mkdir()
            shutil.copy2(self.loader_root / "xnuloader/loader.c", target / "loader.c")
            before = (target / "loader.c").read_bytes()
            with self.assertRaises(ValueError):
                smp.loader_overlay(root, 8)
            self.assertEqual((target / "loader.c").read_bytes(), before)
            self.assertFalse((target / "chiron_smp_loader.inc").exists())


if __name__ == "__main__":
    unittest.main()
