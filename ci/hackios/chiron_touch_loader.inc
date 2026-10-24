/*
 * Boot services stay resident. This is a source-backed execution/ownership
 * assumption, NOT an ExitBootServices attestation and NOT a hardware lock.
 * The boot CPU enters XNU directly with interrupts masked; all other measured
 * affinities must be OFF so no CPU can run a retained UEFI touch timer.
 */
#include "chiron_touch_abi.h"

static int starts_with(const char *, const char *);

static int
chiron_touch_loader_requested(const char *args)
{
    int seen = 0, probe_seen = 0, selected = 0;
    while (*args) {
        const char *start;
        while (*args == ' ') args++;
        start = args;
        while (*args && *args != ' ') args++;
        if (starts_with(start, "chiron_touch=")) {
            if (seen++ || args - start != 14 || (start[13] != '1' && start[13] != '2'))
                return 0;
            selected = 1;
        } else if (starts_with(start, "chiron_touch_probe=")) {
            if (probe_seen++ || args - start != 20 || start[19] != '1')
                return 0;
            selected = 1;
        }
    }
    return selected;
}

static uint32_t
chiron_touch_loader_owner(const char *args, uint64_t boot, uint64_t kernel)
{
    static const uint64_t affinities[7] = {1, 2, 3, 256, 257, 258, 259};
    if (!chiron_touch_loader_requested(args) || PLAT != &platform_chiron ||
        boot != 0 || kernel != boot || PLAT->psci_hvc)
        return 0;
    int64_t version = psci_call(0x84000000u, 0, 0, 0);
    if (version < 2 || ((uint64_t)version >> 16) > 1)
        return 0;
    for (unsigned i = 0; i < 7; i++) {
        int64_t state = psci_call(PSCI_AFFINITY_INFO, affinities[i], 0, 0);
        if (state != 1) {
            print("chiron touch: MPIDR %x not confirmed OFF (%d); probe only\n",
                affinities[i], (uint64_t)state);
            return 0;
        }
    }
    return CHIRON_TOUCH_DIRECT_BOOT_CPU;
}
