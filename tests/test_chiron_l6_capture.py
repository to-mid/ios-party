"""Verify the real shadow dispatch rejects every SPMI operation and restores RAM."""

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CaptureTests(unittest.TestCase):
    def test_shadow_dispatch_and_refusal(self):
        compiler = shutil.which("cc")
        if not compiler:
            self.skipTest("host C compiler unavailable")
        helper = (ROOT / "ci/hackios/chiron_l6_capture.inc").read_text()
        assert helper.count("return UINT64_MAX;") == 1
        helper = helper.replace("return UINT64_MAX;", "return model_translation(address);")
        assert helper.count("(uintptr_t)spmi->read") == 1
        helper = helper.replace("(uintptr_t)spmi->read", "((uintptr_t)image + 0x1440)")
        # Host functions do not live at the firmware RVAs. Keep all other code.
        for field, offset in (("get_level", "21a8"), ("get_status", "22ec"),
                              ("set_level_uv", "2118"), ("set_level", "219c")):
            old = f"(uintptr_t)vreg->{field}"
            assert helper.count(old) == 1
            helper = helper.replace(old, f"((uintptr_t)image + 0x{offset})")
        veneer = re.search(r"veneer\[\] = \{([^}]+)", helper).group(1)
        harness = r"""
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
typedef struct { uint32_t a; uint16_t b,c; uint8_t d[8]; } efi_guid;
typedef struct {
    uint32_t type, pad; uint64_t phys_start, virt_start, pages, attribute;
} efi_memory_descriptor;
static struct {
    efi_status (*get_memory_map)(uint64_t *, void *, uint64_t *, uint64_t *, uint32_t *);
    efi_status (*locate_protocol)(const efi_guid *, void *, void **);
    uint64_t (*raise_tpl)(uint64_t);
    void (*restore_tpl)(uint64_t);
} services, *BS = &services;
struct chiron_reset_vreg {
    uint64_t revision;
    void *control, *set_level, *get_level, *set_mode, *multiphase, *get_status;
    efi_status (*set_level_uv)(uint32_t, uint32_t, uint32_t);
};
static uint8_t image[0x33000] __attribute__((aligned(8)));
static unsigned scenario, owner = 1, tpl = 4, physical_calls, calculator_calls, power_calls;
static unsigned channel_reads, aperture_reads, control_reads;
static void print(const char *format, ...) { (void)format; }
static uint32_t chiron_touch_loader_owner(const char *args, uint64_t b, uint64_t k) {
    assert(!b && !k && args); return owner;
}
static int chiron_reset_power(struct chiron_reset_vreg *vreg, uint32_t uv) {
    assert(vreg && uv == 1848000 && tpl <= 8); power_calls++; return scenario != 10;
}
static uint32_t chiron_reset_read(uint64_t address) {
    if (address == 0x0800f804) {
        channel_reads++; return scenario==25 ? 0 : 0x00014500;
    }
    if (address == 0x0800a704) {
        channel_reads++; return scenario==23 ? 2 : 0;
    }
    if (address == 0x09408008 || address == 0x08408008) {
        assert(scenario==24); channel_reads++; aperture_reads++;
        return address==0x09408008 ? 1 : 0;
    }
    assert(address == 0x03d59000 || address == 0x03d59004);
    return address == 0x03d59000 ? 0x203 : scenario == 11 ? 3 : 0;
}
static void chiron_reset_barrier(void) { __sync_synchronize(); }
static uint64_t model_translation(uint64_t address) {
    if (scenario == 7) return 1; /* Actual write permission denied. */
    if (scenario == 21) return address ^ 0x1000; /* Nonidentity translation. */
    return address & UINT64_C(0x0000fffffffff000);
}
#include "capture.inc"
static struct chiron_capture_spmi original, *other;
static struct chiron_reset_vreg vreg;
static struct chiron_capture_spmi *volatile *cache;
static efi_status memory_map(uint64_t *size, void *map, uint64_t *key,
                            uint64_t *stride, uint32_t *version) {
    assert(*size >= sizeof(efi_memory_descriptor));
    efi_memory_descriptor d = {4,0,0,0,UINT64_MAX/4096,0};
    if (scenario == 6) d.type = 7;
    if (scenario == 7) d.attribute = 0x100f;
    if (scenario == 22) d.attribute = UINT64_C(1)<<17;
    if (scenario == 20 || scenario == 21) d.attribute = 0x100f;
    if (scenario == 8) d.attribute = UINT64_C(1)<<63;
    memcpy(map,&d,sizeof(d)); *size = sizeof(d); *key = 0; *stride = sizeof(d); *version = 1;
    if (scenario == 9) *stride = 0;
    return 0;
}
static efi_status locate(const efi_guid *guid, void *registration, void **result) {
    assert(!registration);
    *result = guid->a == 0x22d38d3d ? (void *)&vreg : (void *)&original;
    return 0;
}
static uint64_t raise_tpl(uint64_t next) {
    assert(next >= tpl && (next == 16 || next == 31));
    uint64_t old=tpl; tpl=(unsigned)next;
    if (scenario == 14 && next == 16) *cache = other;
    return old;
}
static void restore_tpl(uint64_t next) {
    assert(next <= tpl);
    if (tpl == 16 && calculator_calls) assert(*cache == &original);
    tpl=(unsigned)next;
}
static uint32_t physical_write(struct chiron_capture_spmi *self, uint32_t a,
                              uint32_t b,uint32_t c,uint8_t *d,uint32_t e) {
    (void)self;(void)a;(void)b;(void)c;(void)d;(void)e; physical_calls++; abort();
}
static uint32_t control_read(struct chiron_capture_spmi *self, uint32_t slave,
                            uint32_t priority,uint32_t reg,uint8_t *data,
                            uint32_t length,uint32_t *count) {
    assert(self==&original && slave==1 && !priority && reg==0x4540 && length==2);
    control_reads++; data[0]=8; data[1]=7; *count=2; return 0;
}
static efi_status calculate(uint32_t device, uint32_t rail, uint32_t uv) {
    assert(!device && rail == 5 && uv == 1800000 && tpl == 16);
    calculator_calls++;
    struct chiron_capture_spmi *s=*cache;
    assert(s != &original);
    uint8_t data[2]={0x54,0x03}, untouched[2]={0xaa,0xbb};
    uint32_t count=77, result;
    if (scenario == 1) result=s->read(s,0,0,0x4540,untouched,2,&count);
    else if (scenario == 2) result=s->rmw(s,0,0,0x4540,1,1,untouched);
    else if (scenario == 3) result=s->command(s,0,0,1);
    else if (scenario == 4) result=s->read_debug(s,0,0,0x4540,untouched,2);
    else if (scenario == 5) result=s->write_debug(s,0,0,0x4540,data,2);
    else result=s->write(s,0,0,0x4540,data,2);
    assert(result == 12 && untouched[0]==0xaa && untouched[1]==0xbb && count==77);
    return scenario == 15 ? 0 : UINT64_C(0x8000000000000007);
}
int main(int argc,char **argv) {
    assert(argc==2); scenario=(unsigned)atoi(argv[1]);
    cache=(void *)(image+0x32030); *cache=&original;
    services.get_memory_map=memory_map; services.locate_protocol=locate;
    services.raise_tpl=raise_tpl; services.restore_tpl=restore_tpl;
    vreg.revision=0x10005; vreg.set_level_uv=calculate;
    original.revision=0x10000; original.write=physical_write;
    static const uint32_t words[] = { VENEER };
    memcpy(image+0x15aa0,words,sizeof(words));
    if (scenario>=23) {
        uint64_t info[4]={0}, device[6]={0};
        uint16_t lut[256]={0}, map[2]={0,0x4001}, hw=2;
        info[2]=1; info[3]=(uintptr_t)device;
        device[0]=0x08000000; device[2]=(uintptr_t)lut;
        device[3]=(uintptr_t)map; device[4]=(uintptr_t)&hw; lut[0x45]=1;
        *(uint64_t *)(void *)(image+0x75e0)=(uintptr_t)info;
        original.write=(chiron_capture_write_fn)(void *)(image+0x14f8);
        original.read=control_read;
        original.rmw=(void *)(image+0x1590); original.command=(void *)(image+0x1630);
        uint64_t size=sizeof(chiron_capture_map),key,stride; uint32_t version;
        assert(!memory_map(&size,chiron_capture_map,&key,&stride,&version));
        chiron_capture_map_size=size; chiron_capture_map_stride=stride;
        chiron_capture.calls=1; chiron_capture.kind=2;
        chiron_capture.slave=1; chiron_capture.address=0x4540;
        chiron_capture_channel(&original);
        assert(channel_reads==(scenario==24 ? 4u : 2u));
        assert(aperture_reads==(scenario==24 ? 2u : 0u));
        assert(control_reads==1);
        assert(!physical_calls && !calculator_calls);
        return 0;
    }
    if (scenario==12) image[0x15aa0]^=1;
    if (scenario==13) tpl=16;
    if (scenario==16) original.revision++;
    if (scenario==17) owner=0;
    if (scenario==18) services.raise_tpl=NULL;
    if (scenario==19) *cache=NULL;
    int result=chiron_capture_l6("cpus=1 chiron_touch_probe=1",0);
    assert(result==(scenario<=5 || scenario==20));
    assert(calculator_calls==(scenario<=5 || scenario==15 || scenario==20));
    assert(!physical_calls && tpl==(scenario==13 ? 16u : 4u));
    assert(*cache==(scenario==14 ? other : scenario==19 ? NULL : &original));
    if (calculator_calls) {
        assert(chiron_capture.calls==1);
        if (scenario==0 || scenario==5) assert(chiron_capture.data==0x354);
    }
    return 0;
}
""".replace("VENEER", veneer)
        with tempfile.TemporaryDirectory(prefix="chiron-l6-capture-") as folder:
            folder = Path(folder)
            (folder / "capture.inc").write_text(helper)
            (folder / "host.c").write_text(harness)
            result = subprocess.run(
                [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                 "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                 str(folder / "host.c"), "-o", str(folder / "host")],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for case in range(26):
                with self.subTest(case=case):
                    result = subprocess.run([str(folder / "host"), str(case)],
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_capture_helper_hash(self):
        manifest = json.loads((ROOT / "ci/hackios/chiron_touch_sources.json").read_text())
        path = ROOT / "ci/hackios/chiron_l6_capture.inc"
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                         manifest["loader_files"][path.name])


if __name__ == "__main__":
    unittest.main()
