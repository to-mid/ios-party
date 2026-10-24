#include <assert.h>
#include <stdint.h>
#include <string.h>

static struct { int psci_hvc; } platform_chiron, other_platform, *PLAT = &platform_chiron;
static int queries;
static int64_t version = 0x10000, states[7];
#define PSCI_AFFINITY_INFO 0xc4000004u
static void print(const char *format, ...) { (void)format; }
static int starts_with(const char *a, const char *b) { return !strncmp(a, b, strlen(b)); }
static int64_t psci_call(uint64_t function, uint64_t affinity, uint64_t a, uint64_t b)
{
    static const uint64_t ids[] = {1, 2, 3, 256, 257, 258, 259};
    assert(a == 0 && b == 0);
    queries++;
    if (function == 0x84000000u) { assert(affinity == 0); return version; }
    assert(function == PSCI_AFFINITY_INFO); /* Never CPU_ON/OFF or hardware writes. */
    for (unsigned i = 0; i < 7; i++) if (ids[i] == affinity) return states[i];
    assert(0);
    return -1;
}
#include "chiron_touch_loader.inc"

int main(void)
{
    for (unsigned i = 0; i < 7; i++) states[i] = 1;
    const char *off[] = {"", "xchiron_touch=1", "chiron_touch=0", "chiron_touch=10",
        "chiron_touch=1x", "chiron_touch=1 chiron_touch=2", "chiron_touch=1 chiron_touch=1"};
    for (unsigned i = 0; i < sizeof(off) / sizeof(off[0]); i++) {
        queries = 0;
        assert(!chiron_touch_loader_owner(off[i], 0, 0) && queries == 0);
    }
    assert(chiron_touch_loader_owner("cpus=1 chiron_touch=1", 0, 0) == 1);
    assert(queries == 8);
    queries = 0;
    assert(chiron_touch_loader_owner("chiron_touch=2", 0, 0) == 1 && queries == 8);
    assert(chiron_touch_loader_owner("chiron_touch_probe=1", 0, 0) == 1);
    assert(chiron_touch_loader_owner("cpus=1 epoch=1792812345 -v chiron_touch_probe=1", 0, 0) == 1);
    assert(chiron_touch_loader_owner("cpus=1 epoch=1792812345 -v chiron_touch_clock_probe=1", 0, 0) == 1);
    assert(chiron_touch_loader_owner("cpus=1 epoch=1792812345 -v chiron_touch_pinmux_probe=1", 0, 0) == 1);
    assert(!chiron_touch_loader_owner("chiron_touch_pinmux_probe=10", 0, 0));
    assert(!chiron_touch_loader_owner("chiron_touch_pinmux_probe=1 chiron_touch_pinmux_probe=1", 0, 0));
    assert(!chiron_touch_loader_owner("chiron_touch_clock_probe=10", 0, 0));
    assert(!chiron_touch_loader_owner("chiron_touch_clock_probe=1 chiron_touch_clock_probe=1", 0, 0));
    assert(chiron_touch_loader_owner("chiron_touch=1 chiron_touch_probe=1", 0, 0) == 1);
    assert(!chiron_touch_loader_owner("chiron_touch_probe=10", 0, 0));
    for (unsigned i = 0; i < 7; i++) {
        for (int64_t state = -1; state <= 2; state++) {
            states[i] = state;
            assert(chiron_touch_loader_owner("chiron_touch=1", 0, 0) == (state == 1));
        }
        states[i] = 1;
    }
    queries = 0;
    assert(!chiron_touch_loader_owner("chiron_touch=1", 1, 1));
    assert(!chiron_touch_loader_owner("chiron_touch=1", UINT64_C(0x100000000), 0));
    assert(!chiron_touch_loader_owner("chiron_touch=1", 0, 1) && !queries);
    platform_chiron.psci_hvc = 1;
    assert(!chiron_touch_loader_owner("chiron_touch=1", 0, 0) && !queries);
    platform_chiron.psci_hvc = 0; PLAT = &other_platform;
    assert(!chiron_touch_loader_owner("chiron_touch=1", 0, 0) && !queries);
    PLAT = &platform_chiron;
    version = -1; assert(!chiron_touch_loader_owner("chiron_touch=1", 0, 0));
    version = 0x20000; assert(!chiron_touch_loader_owner("chiron_touch=1", 0, 0));
    version = 2; assert(chiron_touch_loader_owner("chiron_touch=1", 0, 0) == 1);
    return 0;
}
