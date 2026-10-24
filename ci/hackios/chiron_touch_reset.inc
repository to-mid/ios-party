/*
 * Explicit loader-only reset release. PMIC calls are read-only.
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
};

_Static_assert(sizeof(struct chiron_reset_vreg_status) == 20, "VREG status ABI");
_Static_assert(offsetof(struct chiron_reset_vreg, get_status) == 48, "VREG status slot");

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
chiron_reset_requested(const char *args)
{
    int seen = 0;
    while (*args) {
        while (*args == ' ') args++;
        const char *start = args;
        while (*args && *args != ' ') args++;
        if (starts_with(start, "chiron_touch_reset=")) {
            if (seen++ || args - start != sizeof("chiron_touch_reset=1") - 1 ||
                start[sizeof("chiron_touch_reset=1") - 2] != '1')
                return -1;
        }
    }
    return seen;
}

static int
chiron_reset_power(struct chiron_reset_vreg *vreg)
{
    /* LDO enum starts at zero. Linux requires L6=1.808V, L14=1.880V.
     * disp_vddts_vreg is active-high GPIO50, NORTH tile, 4 ms startup. */
    static const uint32_t ids[] = {5, 13}, volts[] = {1808000, 1880000};
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
chiron_reset_prepare(const char *args, uint64_t boot, uint64_t kernel)
{
    static const efi_guid guid = {
        0x22d38d3d, 0xe8b6, 0x4f8f, {0x9c, 0x26, 0xbc, 0xeb, 7, 0xd6, 0xcb, 0x68}
    };
    struct chiron_reset_vreg *vreg = NULL;
    unsigned changed = 0;
    if (chiron_reset_requested(args) != 1 ||
        chiron_touch_loader_owner(args, boot, kernel) != CHIRON_TOUCH_DIRECT_BOOT_CPU)
        return 0;
    efi_status status = BS->locate_protocol(&guid, NULL, (void **)&vreg);
    print("chiron reset: VREG locate=%x revision=%x\n",
        (uint64_t)status, vreg ? vreg->revision : 0);
    if (EFI_ERROR(status) || !vreg || vreg->revision != 0x10005 ||
        !vreg->get_level || !vreg->get_status || !chiron_reset_power(vreg)) {
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
        if (!chiron_reset_wait(5000) || !chiron_reset_power(vreg) ||
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
    int ready = chiron_reset_power(vreg) && after_cfg == cfg && (after & 3u) == 3 &&
        chiron_touch_loader_owner(args, boot, kernel) == CHIRON_TOUCH_DIRECT_BOOT_CPU;
    print("CHIRON RESET PROBE: ready=%d changed=%d AFTER cfg=%x io=%x\n",
        (uint64_t)ready, (uint64_t)changed, (uint64_t)after_cfg, (uint64_t)after);
    return ready;
}
