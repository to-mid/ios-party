"""Exercise the actual loader reset gate with only MMIO/time replaced."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ResetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("cc")
        if not compiler:
            raise unittest.SkipTest("host compiler unavailable")
        cls.tmp = tempfile.TemporaryDirectory(prefix="chiron-reset-")
        cls.addClassCleanup(cls.tmp.cleanup)
        folder = Path(cls.tmp.name)
        helper = (ROOT / "ci/hackios/chiron_touch_reset.inc").read_text()
        read = "return *(volatile uint32_t *)(uintptr_t)address;"
        write = "*(volatile uint32_t *)(uintptr_t)address = value;"
        assert helper.count(read) == helper.count(write) == 1
        (folder / "reset.inc").write_text(helper.replace(read, "return model_read(address);")
                                        .replace(write, "model_write(address, value);"))
        (folder / "host.c").write_text(r"""
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define EFIAPI
#define EFI_ERROR(s) ((s) >> 63)
#define CHIRON_TOUCH_DIRECT_BOOT_CPU 1
typedef uint64_t efi_status;
typedef struct { uint32_t a; uint16_t b, c; uint8_t d[8]; } efi_guid;
static struct {
    efi_status (*stall)(uint64_t);
    efi_status (*locate_protocol)(const efi_guid *, void *, void **);
} services, *BS = &services;
static int owner = 1, reads, writes, stalls, queries, bad_rail = -1;
static int bad_status, short_delay, stall_error, lose_owner, lose_power, stuck;
static int locate_error, change_gpio;
static int l6_build, voltage_writes, set_error, set_ignored, after_set_owner_loss, revert;
static uint32_t l6_uv = 1808000;
#define CHIRON_TOUCH_L6_PREPARE l6_build
static uint32_t gpio50_cfg = 0x203, gpio50_io = 3, cfg = 0x203, io;
static uint64_t ticks, model_frequency = 19200000, delays[4];
static uint32_t framebuffer[1080 * 2160];
static struct {
    uint64_t base;
    uint32_t width, height, stride;
} SCREEN;
static unsigned cleaned_rows;
static void clean_dcache(uint64_t start, uint64_t end) {
    assert(start >= SCREEN.base && end <= SCREEN.base + sizeof(framebuffer));
    assert(end - start == 64 * 4);
    cleaned_rows++;
}
#define CNTFRQ_EL0 0
#define CNTVCT_EL0 1
#define READ_SYSREG(reg) ((reg) == CNTFRQ_EL0 ? model_frequency : ticks)
static int starts_with(const char *a, const char *b) { return !strncmp(a, b, strlen(b)); }
static void print(const char *format, ...) { (void)format; }
static uint32_t chiron_touch_loader_owner(const char *args, uint64_t boot, uint64_t kernel) {
    (void)args; assert(!boot && !kernel); return (uint32_t)owner;
}
static uint32_t model_read(uint64_t address) {
    reads++;
    if (address == 0x03932000) return gpio50_cfg;
    if (address == 0x03932004) return gpio50_io;
    if (address == 0x03d59000) return cfg;
    assert(address == 0x03d59004);
    return io;
}
static void model_write(uint64_t address, uint32_t value) {
    assert(address == 0x03d59004 && value == 2);
    assert(stalls == (voltage_writes ? 2 : 1) && delays[0] == 5000 && owner == 1);
    writes++; io = stuck ? 2 : 3;
}
#include "reset.inc"
static struct chiron_reset_vreg vreg;
static efi_status get_level(uint32_t device, uint32_t id, uint32_t *uv) {
    assert(device == 0 && (id == 5 || id == 13));
    *uv = id == 5 ? l6_uv : 1880000;
    if ((int)id == bad_rail) *uv = 0;
    return 0;
}
static efi_status get_status(uint32_t device, uint32_t id, struct chiron_reset_vreg_status *state) {
    assert(device == 0 && (id == 5 || id == 13));
    queries++;
    state->enabled = 1;
    state->ok = !bad_status && !(lose_power && stalls);
    return 0;
}
static efi_status locate(const efi_guid *guid, void *registration, void **result) {
    assert(guid->a == 0x22d38d3d && !registration);
    *result = &vreg;
    return locate_error ? UINT64_C(0x800000000000000e) : 0;
}
static efi_status stall(uint64_t us) {
    assert(stalls < 4);
    delays[stalls++] = us;
    if (!short_delay) ticks += us * model_frequency / 1000000;
    if (lose_owner) owner = 0;
    if (change_gpio) cfg ^= 0x40;
    if (voltage_writes && after_set_owner_loss) owner = 0;
    if (voltage_writes && revert) l6_uv = 1848000;
    return stall_error ? UINT64_C(0x8000000000000007) : 0;
}
static efi_status set_level_uv(uint32_t device, uint32_t id, uint32_t uv) {
    assert(device == 0 && id == 5 && uv == 1800000);
    assert(l6_uv == 1848000 && io == 0 && cfg == 0x203 && owner == 1);
    assert(gpio50_io == 3 && !stalls && !voltage_writes);
    assert(cleaned_rows == 64);
    assert(framebuffer[2160 * 1080 - 1] == 0x00ff0000);
    voltage_writes++;
    if (!set_ignored) l6_uv = uv;
    return set_error ? UINT64_C(0x8000000000000007) : 0;
}
int main(int argc, char **argv) {
    assert(argc == 2);
    int test = atoi(argv[1]), expected = 0;
    services.stall = stall; services.locate_protocol = locate;
    SCREEN.base = (uintptr_t)framebuffer;
    SCREEN.width = 1080; SCREEN.height = 2160; SCREEN.stride = 4320;
    vreg.revision = 0x10005; vreg.get_level = get_level; vreg.get_status = get_status;
    vreg.set_level_uv = set_level_uv;
    if (test >= 42) {
        vreg.get_level = (efi_status (*)(uint32_t, uint32_t, uint32_t *))(uintptr_t)0x1021a8;
        vreg.get_status = (efi_status (*)(uint32_t, uint32_t,
            struct chiron_reset_vreg_status *))(uintptr_t)0x1022ec;
        vreg.set_level = (void *)(uintptr_t)0x10219c;
        vreg.set_level_uv = (efi_status (*)(uint32_t, uint32_t, uint32_t))(uintptr_t)
            (test == 43 ? 0x10211c : 0x102118);
        if (test == 44) vreg.revision--;
        if (test == 45) locate_error = 1;
        assert(chiron_inspect_vreg_abi() == (test == 42));
        assert(!reads && !writes && !queries && !stalls && !voltage_writes);
        return 0;
    }
    const char *args = "cpus=1 -v chiron_touch_pinmux_probe=1 chiron_touch_reset=1";
    if (test == 0) expected = 1;
    if (test == 1) { io = 3; expected = 1; }
    if (test == 2) owner = 0;
    if (test == 3) vreg.revision--;
    if (test == 4) locate_error = 1;
    if (test == 5) bad_rail = 5;
    if (test == 6) bad_rail = 13;
    if (test == 7) bad_status = 1;
    if (test == 8) gpio50_io = 0;
    if (test == 9) gpio50_cfg = 0;
    if (test == 10) cfg = 0x20f;
    if (test == 11) io = 2;
    if (test == 12) short_delay = 1;
    if (test == 13) stall_error = 1;
    if (test == 14) lose_owner = 1;
    if (test == 15) lose_power = 1;
    if (test == 16) stuck = 1;
    if (test == 17) change_gpio = 1;
    if (test == 18) args = "cpus=1 chiron_touch_pinmux_probe=1";
    if (test == 19) args = "chiron_touch_reset=1 chiron_touch_reset=1";
    if (test == 20) args = "chiron_touch_reset=10";
    if (test == 21) model_frequency = 0;
    if (test >= 22) {
        args = "cpus=1 -v chiron_touch_pinmux_probe=1 chiron_touch_reset=1 chiron_touch_l6=1";
        l6_build = 1; l6_uv = 1848000;
    }
    if (test == 22) expected = 1;
    if (test == 23) { l6_uv = 1800000; expected = 1; }
    if (test == 24) l6_build = 0; /* Old reset image rejects a hidden L6 request. */
    if (test == 25) args = "chiron_touch_reset=1";
    if (test == 26) l6_uv = 1808000;
    if (test == 27) io = 3; /* Never change the rail of a released panel. */
    if (test == 28) cfg = 0;
    if (test == 29) { set_error = 1; set_ignored = 1; }
    if (test == 30) set_ignored = 1;
    if (test == 31) set_error = 1; /* Failed call may have reached hardware. */
    if (test == 32) after_set_owner_loss = 1;
    if (test == 33) bad_rail = 13;
    if (test == 34) bad_status = 1;
    if (test == 35) revert = 1;
    if (test == 36) { l6_uv = 1800000; io = 3; expected = 1; }
    if (test == 37) args = "chiron_touch_reset=1 chiron_touch_l6=1 chiron_touch_l6=1";
    if (test == 38) args = "chiron_touch_reset=1 chiron_touch_l6=10";
    if (test == 39) vreg.set_level_uv = NULL;
    if (test == 40) l6_uv = 1000000;
    if (test == 41) l6_uv = 1900000;
    assert(chiron_reset_prepare(args, 0, 0) == expected);
    assert(writes == (test == 0 || test == 16 || test == 22 || test == 23));
    assert(voltage_writes == (test == 22 || (test >= 29 && test <= 32) || test == 35));
    assert(cleaned_rows == (voltage_writes ? 128u : 0u));
    if (voltage_writes) {
        for (unsigned y = 0; y < 2160; y++)
            for (unsigned x = 0; x < 1080; x++)
                assert(framebuffer[y * 1080 + x] ==
                    (y >= 2160 - 64 && x >= 1080 - 64 ? 0x0000ff00u : 0u));
    }
    if (expected) {
        assert(io == 3 && cfg == 0x203 && delays[stalls - 1] == 200000);
    }
    if (test == 2 || (test >= 18 && test <= 20) || test == 24 || test == 25 ||
        test == 37 || test == 38) assert(reads == 0);
    if (test == 3 || test == 4) assert(reads == 0 && queries == 0);
    assert(gpio50_cfg == (test == 9 ? 0u : 0x203u));
    assert(gpio50_io == (test == 8 ? 0u : 3u));
    return 0;
}
""")
        cls.program = folder / "host"
        result = subprocess.run([
            compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
            "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
            str(folder / "host.c"), "-o", str(cls.program),
        ], text=True, capture_output=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    def test_only_reset_output_changes_after_power_and_delays_pass(self):
        for case in range(46):
            with self.subTest(case=case):
                result = subprocess.run([str(self.program), str(case)], text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_reset_helper_and_protocol_evidence_hashes(self):
        manifest = json.loads((ROOT / "ci/hackios/chiron_touch_sources.json").read_text())
        helper = ROOT / "ci/hackios/chiron_touch_reset.inc"
        self.assertEqual(hashlib.sha256(helper.read_bytes()).hexdigest(),
                         manifest["loader_files"][helper.name])
        path = Path("/private/tmp/chiron-EFIPmicVreg.h")
        if path.exists():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                             manifest["firmware_upstream"]["files"]["EFIPmicVreg.h"])


if __name__ == "__main__":
    unittest.main()
