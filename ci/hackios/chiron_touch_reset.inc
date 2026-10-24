/*
 * Explicit loader-only reset release; L6 writes require a separate build/arg.
 * VREG ABI: QcomPkg/Include/Protocol/EFIPmicVreg.h, pinned in the manifest.
 * GPIO/timing: pinned msm8998 pinctrl and chiron DSX board sources.
 */
struct chiron_reset_vreg_status {
    uint32_t pull_down, mode, pin_control, enabled;
    uint8_t ok;
};

struct chiron_reset_vreg {
    uint64_t revision;
    void *control, *set_level;
    efi_status (EFIAPI *get_level)(uint32_t, uint32_t, uint32_t *);
    void *set_mode, *multiphase;
    efi_status (EFIAPI *get_status)(uint32_t, uint32_t, struct chiron_reset_vreg_status *);
    efi_status (EFIAPI *set_level_uv)(uint32_t, uint32_t, uint32_t);
};

_Static_assert(sizeof(struct chiron_reset_vreg_status) == 20, "VREG status ABI");
_Static_assert(offsetof(struct chiron_reset_vreg, get_status) == 48, "VREG status slot");
_Static_assert(offsetof(struct chiron_reset_vreg, set_level_uv) == 56, "VREG UV slot");

static int
chiron_inspect_vreg_abi(void)
{
    static const efi_guid guid = {
        0x22d38d3d, 0xe8b6, 0x4f8f, {0x9c, 0x26, 0xbc, 0xeb, 7, 0xd6, 0xcb, 0x68}
    };
    struct chiron_reset_vreg *vreg = NULL;
    print("CHIRON L6 ABI: read-only callback snapshot; no kernel start\n");
    efi_status status = BS->locate_protocol(&guid, NULL, (void **)&vreg);
    if (EFI_ERROR(status) || !vreg) {
        print("CHIRON L6 ABI: locate failed status=%x\n", (uint64_t)status);
        return 0;
    }
    print("CHIRON L6 ABI: protocol=%x revision=%x\n",
        (uint64_t)(uintptr_t)vreg, vreg->revision);
    if (vreg->revision != 0x10005)
        return 0;
    uint64_t level = (uintptr_t)vreg->get_level;
    uint64_t status_fn = (uintptr_t)vreg->get_status;
    uint64_t set_mv = (uintptr_t)vreg->set_level, set_uv = (uintptr_t)vreg->set_level_uv;
    print("CHIRON L6 ABI: GetLevel=%x GetStatus=%x\n", level, status_fn);
    print("CHIRON L6 ABI: SetLevel=%x SetLevelUv=%x\n", set_mv, set_uv);
    /* Compare addresses only. No VREG callback is invoked by this snapshot. */
    uint64_t base = level >= 0x21a8 ? level - 0x21a8 : 0;
    int match = base && status_fn == base + 0x22ec &&
        set_mv == base + 0x219c && set_uv == base + 0x2118;
    print("CHIRON L6 ABI: reference_profile=%d inferred_base=%x\n", (uint64_t)match, base);
    print("CHIRON L6 ABI: snapshot complete; PMIC and reset untouched\n");
    return match;
}

#ifndef CHIRON_TOUCH_L6_PREPARE
#define CHIRON_TOUCH_L6_PREPARE 0
#endif

static uint32_t
chiron_reset_read(uint64_t address)
{
    return *(volatile uint32_t *)(uintptr_t)address;
}

static void
chiron_reset_write(uint64_t address, uint32_t value)
{
    *(volatile uint32_t *)(uintptr_t)address = value;
}

static void
chiron_reset_barrier(void)
{
#if defined(__aarch64__)
    __asm__ volatile ("dsb sy" ::: "memory");
#else
    __sync_synchronize();
#endif
}

static int
chiron_l6_call_marker(int returned)
{
    if (!SCREEN.base || SCREEN.width != 1080 || SCREEN.height != 2160 ||
        SCREEN.stride != 4320)
        return 0;
    /* Publish without console, USB or PMIC calls, including cached framebuffers. */
    for (uint32_t y = SCREEN.height - 64; y < SCREEN.height; y++) {
        uint64_t start = SCREEN.base + (uint64_t)y * SCREEN.stride +
            (SCREEN.width - 64) * 4;
        volatile uint32_t *pixels = (volatile uint32_t *)(uintptr_t)start;
        for (unsigned x = 0; x < 64; x++)
            pixels[x] = returned ? 0x0000ff00 : 0x00ff0000;
        chiron_reset_barrier();
        clean_dcache(start, start + 64 * 4);
    }
    return 1;
}

static int
chiron_reset_option(const char *args, const char *name, size_t length)
{
    int seen = 0;
    while (*args) {
        while (*args == ' ') args++;
        const char *start = args;
        while (*args && *args != ' ') args++;
        if (starts_with(start, name)) {
            if (seen++ || (size_t)(args - start) != length || start[length - 1] != '1')
                return -1;
        }
    }
    return seen;
}

static int
chiron_reset_requested(const char *args)
{
    return chiron_reset_option(args, "chiron_touch_reset=", sizeof("chiron_touch_reset=1") - 1);
}

static int
chiron_reset_power(struct chiron_reset_vreg *vreg, uint32_t l6_uv)
{
    /* LDO enum starts at zero. The original reset probe expects L6=1.808V;
     * the measured runtime DT's separate L6 experiment requests 1.800V.
     * disp_vddts_vreg is active-high GPIO50, NORTH tile, 4 ms startup. */
    static const uint32_t ids[] = {5, 13};
    const uint32_t volts[] = {l6_uv, 1880000};
    int ready = 1;
    for (unsigned i = 0; i < 2; i++) {
        struct chiron_reset_vreg_status state = {0};
        uint32_t uv = 0;
        efi_status level = vreg->get_level(0, ids[i], &uv);
        efi_status status = vreg->get_status(0, ids[i], &state);
        print("chiron reset: L%d level_status=%x status=%x uv=%d enabled=%d ok=%d\n",
            (uint64_t)(ids[i] + 1), (uint64_t)level, (uint64_t)status,
            (uint64_t)uv, (uint64_t)state.enabled, (uint64_t)state.ok);
        if (EFI_ERROR(level) || EFI_ERROR(status) || uv != volts[i] ||
            state.enabled != 1 || state.ok != 1)
            ready = 0;
    }
    uint32_t cfg = chiron_reset_read(0x03932000), io = chiron_reset_read(0x03932004);
    print("chiron reset: GPIO50 cfg=%x io=%x\n", (uint64_t)cfg, (uint64_t)io);
    return ready && (cfg & 0x23cu) == 0x200 && (io & 3u) == 3;
}

static int
chiron_reset_wait(uint32_t us)
{
    uint64_t frequency = READ_SYSREG(CNTFRQ_EL0);
    uint64_t start = READ_SYSREG(CNTVCT_EL0);
    if (frequency != 19200000 || !BS->stall || EFI_ERROR(BS->stall(us)))
        return 0;
    uint64_t end = READ_SYSREG(CNTVCT_EL0);
    return end >= start && end - start >= (uint64_t)us * frequency / 1000000;
}

static int
chiron_reset_prepare_l6(struct chiron_reset_vreg *vreg, const char *args,
    uint64_t boot, uint64_t kernel)
{
    uint32_t uv = 0;
    efi_status status = vreg->get_level(0, 5, &uv);
    print("CHIRON L6 PROBE: BEFORE status=%x uv=%d target_uv=1800000\n",
        (uint64_t)status, (uint64_t)uv);
    if (EFI_ERROR(status) || (uv != 1848000 && uv != 1800000) ||
        !chiron_reset_power(vreg, uv))
        return 0;
    if (uv == 1800000)
        return 1;
    /* Only the captured boot profile may change, with reset held low.
     * No enable/mode changes and no writes to another rail are permitted. */
    uint32_t cfg = chiron_reset_read(0x03d59000), io = chiron_reset_read(0x03d59004);
    if ((cfg & 0x23cu) != 0x200 || (io & 3u) != 0 || !vreg->set_level_uv ||
        chiron_touch_loader_owner(args, boot, kernel) != CHIRON_TOUCH_DIRECT_BOOT_CPU)
        return 0;
    uint32_t current = 0;
    if (EFI_ERROR(vreg->get_level(0, 5, &current)) || current != uv ||
        !chiron_reset_power(vreg, uv) || chiron_reset_read(0x03d59000) != cfg ||
        chiron_reset_read(0x03d59004) != io ||
        chiron_touch_loader_owner(args, boot, kernel) != CHIRON_TOUCH_DIRECT_BOOT_CPU)
        return 0;
    print("CHIRON L6 MARKER: bottom-right RED=calling GREEN=returned\n");
    print("CHIRON L6 TRACE: calling SetLevelUv L6=1800000\n");
    if (!chiron_l6_call_marker(0))
        return 0;
    status = vreg->set_level_uv(0, 5, 1800000);
    if (!chiron_l6_call_marker(1))
        return 0;
    print("CHIRON L6 PROBE: changed=1 set_status=%x; no retry or voltage rollback\n",
        (uint64_t)status);
    if (EFI_ERROR(status) || !chiron_reset_wait(5000))
        return 0;
    int ready = chiron_reset_power(vreg, 1800000) &&
        chiron_reset_read(0x03d59000) == cfg && chiron_reset_read(0x03d59004) == io &&
        chiron_touch_loader_owner(args, boot, kernel) == CHIRON_TOUCH_DIRECT_BOOT_CPU;
    print("CHIRON L6 PROBE: ready=%d changed=1 target_uv=1800000\n", (uint64_t)ready);
    return ready;
}

static int
chiron_reset_prepare(const char *args, uint64_t boot, uint64_t kernel)
{
    static const efi_guid guid = {
        0x22d38d3d, 0xe8b6, 0x4f8f, {0x9c, 0x26, 0xbc, 0xeb, 7, 0xd6, 0xcb, 0x68}
    };
    struct chiron_reset_vreg *vreg = NULL;
    unsigned changed = 0;
    int l6 = chiron_reset_option(args, "chiron_touch_l6=", sizeof("chiron_touch_l6=1") - 1);
    uint32_t l6_uv = l6 == 1 ? 1800000 : 1808000;
    if (chiron_reset_requested(args) != 1 ||
        l6 != CHIRON_TOUCH_L6_PREPARE ||
        chiron_touch_loader_owner(args, boot, kernel) != CHIRON_TOUCH_DIRECT_BOOT_CPU)
        return 0;
    efi_status status = BS->locate_protocol(&guid, NULL, (void **)&vreg);
    print("chiron reset: VREG locate=%x revision=%x\n",
        (uint64_t)status, vreg ? vreg->revision : 0);
    if (EFI_ERROR(status) || !vreg || vreg->revision != 0x10005 ||
        !vreg->get_level || !vreg->get_status ||
        (l6 && !chiron_reset_prepare_l6(vreg, args, boot, kernel)) ||
        !chiron_reset_power(vreg, l6_uv)) {
        print("CHIRON RESET PROBE: supply preflight refused; reset untouched\n");
        return 0;
    }
    uint32_t cfg = chiron_reset_read(0x03d59000), before = chiron_reset_read(0x03d59004);
    print("chiron reset: BEFORE cfg=%x io=%x\n", (uint64_t)cfg, (uint64_t)before);
    if ((cfg & 0x23cu) != 0x200 || ((before & 3u) != 0 && (before & 3u) != 3))
        return 0;
    if (!(before & 3u)) {
        /* Existing low is held for 5 ms; this also covers the 4 ms pullup delay.
         * Never pulse an already released panel or change its mux/pull/drive. */
        if (!chiron_reset_wait(5000) || !chiron_reset_power(vreg, l6_uv) ||
            chiron_touch_loader_owner(args, boot, kernel) != CHIRON_TOUCH_DIRECT_BOOT_CPU ||
            chiron_reset_read(0x03d59000) != cfg ||
            chiron_reset_read(0x03d59004) != before)
            return 0;
        changed = 1;
        chiron_reset_barrier();
        chiron_reset_write(0x03d59004, before | 2u);
        chiron_reset_barrier();
    }
    if (!chiron_reset_wait(200000)) {
        print("CHIRON RESET PROBE: delay failed changed=%d; no retry or reset rollback\n",
            (uint64_t)changed);
        return 0;
    }
    uint32_t after_cfg = chiron_reset_read(0x03d59000), after = chiron_reset_read(0x03d59004);
    int ready = chiron_reset_power(vreg, l6_uv) && after_cfg == cfg && (after & 3u) == 3 &&
        chiron_touch_loader_owner(args, boot, kernel) == CHIRON_TOUCH_DIRECT_BOOT_CPU;
    print("CHIRON RESET PROBE: ready=%d changed=%d AFTER cfg=%x io=%x\n",
        (uint64_t)ready, (uint64_t)changed, (uint64_t)after_cfg, (uint64_t)after);
    return ready;
}
