/* One-shot firmware calculation probe. All shadow SPMI operations fail closed. */
struct chiron_capture_spmi;
typedef uint32_t (EFIAPI *chiron_capture_read_fn)(struct chiron_capture_spmi *,
    uint32_t, uint32_t, uint32_t, uint8_t *, uint32_t, uint32_t *);
typedef uint32_t (EFIAPI *chiron_capture_write_fn)(struct chiron_capture_spmi *,
    uint32_t, uint32_t, uint32_t, uint8_t *, uint32_t);
struct chiron_capture_spmi {
    uint64_t revision;
    chiron_capture_read_fn read;
    chiron_capture_write_fn write;
    uint32_t (EFIAPI *rmw)(struct chiron_capture_spmi *, uint32_t, uint32_t,
        uint32_t, uint32_t, uint32_t, uint8_t *);
    uint32_t (EFIAPI *command)(struct chiron_capture_spmi *, uint32_t, uint32_t, uint32_t);
    chiron_capture_write_fn read_debug, write_debug;
};
_Static_assert(sizeof(struct chiron_capture_spmi) == 56, "SPMI protocol ABI");

static uint8_t chiron_capture_map[65536] __attribute__((aligned(8)));
static uint64_t chiron_capture_map_size, chiron_capture_map_stride;
static uint64_t chiron_capture_write_par;
static struct {
    uint32_t calls, kind, slave, priority, address, length, data;
} chiron_capture;

static uint64_t
chiron_capture_translate_write(uint64_t address)
{
#if defined(__aarch64__)
    uint64_t saved, result, features, pan = 0;
    __asm__ volatile ("mrs %0, id_aa64mmfr1_el1" : "=r" (features));
    if ((features >> 20) & 15)
        __asm__ volatile ("mrs %0, S3_0_C4_C2_3" : "=r" (pan));
    /* S1E1W ignores PAN. Refuse instead of treating it as a full check. */
    if (pan)
        return ~UINT64_C(0);
    __asm__ volatile ("mrs %0, par_el1" : "=r" (saved));
    __asm__ volatile ("at s1e1w, %1\nisb\nmrs %0, par_el1"
        : "=r" (result) : "r" (address) : "memory");
    __asm__ volatile ("msr par_el1, %0" : : "r" (saved) : "memory");
    return result;
#else
    (void)address;
    return UINT64_MAX;
#endif
}

static int
chiron_capture_ram(uint64_t address, uint64_t bytes, int writable)
{
    if (!address || !bytes || address > UINT64_MAX - bytes)
        return 0;
    for (uint64_t off = 0; off < chiron_capture_map_size;
            off += chiron_capture_map_stride) {
        const efi_memory_descriptor *d = (const void *)(chiron_capture_map + off);
        if (d->pages > (UINT64_MAX - d->phys_start) / 4096)
            return 0;
        /* Only loaded firmware/application RAM, never MMIO or free memory. */
        uint64_t forbidden = (1ULL << 63) | (1ULL << 13);
        if (writable)
            forbidden |= 1ULL << 17;
        if (d->type >= 1 && d->type <= 4 && !(d->attribute & forbidden) &&
            address >= d->phys_start &&
            address + bytes <= d->phys_start + d->pages * 4096) {
            /* This firmware reports WP with all four cache capability bits.
             * Require the MMU's actual EL1 write permission without writing
             * data or changing translation/protection state. */
            if (writable) {
                const uint64_t mask = UINT64_C(0x0000fffffffff000);
                uint64_t ends[] = {address, address + bytes - 1};
                for (unsigned i = 0; i < 2; i++) {
                    chiron_capture_write_par = chiron_capture_translate_write(ends[i]);
                    if ((chiron_capture_write_par & 1) ||
                        (chiron_capture_write_par & mask) != (ends[i] & mask))
                        return 0;
                }
            }
            return 1;
        }
    }
    return 0;
}

static uint32_t
chiron_capture_reject(uint32_t kind, uint32_t slave, uint32_t priority,
    uint32_t address, uint8_t *data, uint32_t length)
{
    chiron_capture.calls++;
    if (chiron_capture.calls == 1) {
        chiron_capture.kind = kind;
        chiron_capture.slave = slave;
        chiron_capture.priority = priority;
        chiron_capture.address = address;
        chiron_capture.length = length;
        if (kind == 2 && length > 0 && length <= 4 &&
            chiron_capture_ram((uintptr_t)data, length, 0))
            for (uint32_t i = 0; i < length; i++)
                chiron_capture.data |= (uint32_t)data[i] << (8 * i);
    }
    return 12; /* Nonzero SPMI failure; never forward to firmware. */
}

static uint32_t EFIAPI
chiron_capture_read(struct chiron_capture_spmi *self, uint32_t slave,
    uint32_t priority, uint32_t address, uint8_t *data, uint32_t length,
    uint32_t *count)
{
    (void)self; (void)data; (void)count;
    return chiron_capture_reject(1, slave, priority, address, NULL, length);
}

static uint32_t EFIAPI
chiron_capture_write(struct chiron_capture_spmi *self, uint32_t slave,
    uint32_t priority, uint32_t address, uint8_t *data, uint32_t length)
{
    (void)self;
    return chiron_capture_reject(2, slave, priority, address, data, length);
}

static uint32_t EFIAPI
chiron_capture_rmw(struct chiron_capture_spmi *self, uint32_t slave,
    uint32_t priority, uint32_t address, uint32_t value, uint32_t mask, uint8_t *data)
{
    (void)self; (void)value; (void)mask; (void)data;
    return chiron_capture_reject(3, slave, priority, address, NULL, 1);
}

static uint32_t EFIAPI
chiron_capture_command(struct chiron_capture_spmi *self, uint32_t slave,
    uint32_t priority, uint32_t command)
{
    (void)self;
    return chiron_capture_reject(4, slave, priority, command, NULL, 0);
}

static uint32_t EFIAPI
chiron_capture_read_debug(struct chiron_capture_spmi *self, uint32_t slave,
    uint32_t priority, uint32_t address, uint8_t *data, uint32_t length)
{
    (void)self; (void)data;
    return chiron_capture_reject(5, slave, priority, address, NULL, length);
}

static void
chiron_capture_channel(struct chiron_capture_spmi *spmi)
{
    uint64_t write = (uintptr_t)spmi->write;
    uint64_t base = write >= 0x14f8 ? write - 0x14f8 : 0;
    if (!base || base > UINT64_MAX - 0x8000 ||
        (uintptr_t)spmi->read != base + 0x1440 ||
        (uintptr_t)spmi->rmw != base + 0x1590 ||
        (uintptr_t)spmi->command != base + 0x1630 ||
        !chiron_capture_ram(base + 0x75e0, 8, 0) ||
        chiron_capture.calls != 1 || chiron_capture.kind != 2 ||
        chiron_capture.slave != 1 || chiron_capture.address != 0x4540)
        return;
    /* Pinned SpmiDxe layout used by its 0x2b20 transaction path. */
    uint64_t info = *(const volatile uint64_t *)(uintptr_t)(base + 0x75e0);
    if (!chiron_capture_ram(info, 32, 0))
        return;
    uint32_t ee = *(const volatile uint8_t *)(uintptr_t)info;
    uint32_t buses = *(const volatile uint8_t *)(uintptr_t)(info + 16);
    uint64_t device = *(const volatile uint64_t *)(uintptr_t)(info + 24);
    if (ee > 7 || buses != 1 || !chiron_capture_ram(device, 48, 0))
        return;
    uint64_t mmio = *(const volatile uint64_t *)(uintptr_t)device;
    uint64_t lut = *(const volatile uint64_t *)(uintptr_t)(device + 16);
    uint64_t map = *(const volatile uint64_t *)(uintptr_t)(device + 24);
    uint64_t hw = *(const volatile uint64_t *)(uintptr_t)(device + 32);
    if (mmio != 0x02000000 || !chiron_capture_ram(hw, 2, 0) ||
        !chiron_capture_ram(lut, 512, 0))
        return;
    uint32_t channels = *(const volatile uint16_t *)(uintptr_t)hw;
    if (!channels || channels > 512 || !chiron_capture_ram(map, channels * 2, 0))
        return;
    uint32_t channel = ((const volatile uint16_t *)(uintptr_t)lut)[0x45];
    for (uint32_t n = 0; n < channels && channel < channels; n++) {
        uint32_t entry = ((const volatile uint16_t *)(uintptr_t)map)[channel];
        if ((entry & 31) != 1) {
            channel = entry >> 5;
            continue;
        }
        /* Linux's v2 arbiter uses cnfg=core+0xf000, PPID at +0x800,
         * ownership at +0x700. Read ownership before any write aperture. */
        uint32_t mapping = chiron_reset_read(mmio + 0xf800 + channel * 4);
        uint32_t owner = chiron_reset_read(mmio + 0xf700 + channel * 4) & 7;
        print("CHIRON L6 CHANNEL: core=%x ee=%d channel=%d mapping=%x owner=%d\n",
            mmio, (uint64_t)ee, (uint64_t)channel, (uint64_t)mapping, (uint64_t)owner);
        if (((mapping >> 16) & 15) != 1 || ((mapping >> 8) & 255) != 0x45)
            return;
        if (owner != ee) {
            print("CHIRON L6 CHANNEL: owner mismatch; write aperture untouched\n");
            return;
        }
        uint64_t offset = (uint64_t)channel * 0x8000 + ee * 0x1000;
        uint32_t read_status = chiron_reset_read(mmio + 0x1400008 + offset);
        uint32_t write_status = chiron_reset_read(mmio + 0x400008 + offset);
        print("CHIRON L6 CHANNEL: read_status=%x write_status=%x; no MMIO writes\n",
            (uint64_t)read_status, (uint64_t)write_status);
        return;
    }
}

static int
chiron_capture_l6(const char *args, uint64_t boot)
{
    static const efi_guid vreg_guid = {
        0x22d38d3d, 0xe8b6, 0x4f8f, {0x9c, 0x26, 0xbc, 0xeb, 7, 0xd6, 0xcb, 0x68}
    };
    static const efi_guid spmi_guid = {
        0xfa5f306b, 0xf47d, 0x4ac4, {0xa4, 0x7d, 0x88, 0x2f, 0x82, 4, 0xec, 0x30}
    };
    /* Pinned PmicDxe WriteLong veneer: cached interface at image RVA 0x32030.
     * Refuse a different implementation before redirecting any firmware data. */
    static const uint32_t veneer[] = {
        0xb00000ec, 0x2a0403e8, 0xaa0303e9, 0xf940198d,
        0x2a0203ea, 0x2a0103eb, 0x2a0003ec, 0xb400012d,
        0xf94009a6, 0xaa0d03e0, 0x2a0c03e1, 0x2a0b03e2,
        0x2a0a03e3, 0xaa0903e4, 0x2a0803e5, 0xd61f00c0
    };
    struct chiron_reset_vreg *vreg = NULL;
    struct chiron_capture_spmi *spmi = NULL;
    uint64_t key, stride = 0;
    uint32_t version;
    if (!BS->get_memory_map || !BS->raise_tpl || !BS->restore_tpl ||
        chiron_touch_loader_owner(args, boot, boot) != CHIRON_TOUCH_DIRECT_BOOT_CPU)
        return 0;
    uint64_t (EFIAPI *raise)(uint64_t) = (uint64_t (EFIAPI *)(uint64_t))BS->raise_tpl;
    void (EFIAPI *restore)(uint64_t) = (void (EFIAPI *)(uint64_t))BS->restore_tpl;
    uint64_t previous = raise(31);
    restore(previous);
    if (previous != 4 && previous != 8)
        return 0;
    print("CHIRON L6 CAPTURE: calculator only; all shadow SPMI requests rejected\n");
    chiron_capture_map_size = sizeof(chiron_capture_map);
    if (EFI_ERROR(BS->get_memory_map(&chiron_capture_map_size, chiron_capture_map,
            &key, &stride, &version)) || stride < sizeof(efi_memory_descriptor) ||
        (stride & 7) || chiron_capture_map_size > sizeof(chiron_capture_map) ||
        !chiron_capture_map_size || chiron_capture_map_size % stride) {
        print("CHIRON L6 CAPTURE: refused memory map size=%x stride=%x\n",
            chiron_capture_map_size, stride);
        return 0;
    }
    chiron_capture_map_stride = stride;
    efi_status vreg_status = BS->locate_protocol(&vreg_guid, NULL, (void **)&vreg);
    efi_status spmi_status = BS->locate_protocol(&spmi_guid, NULL, (void **)&spmi);
    print("CHIRON L6 CAPTURE: locate vreg=%x spmi=%x interfaces=%x/%x\n",
        (uint64_t)vreg_status, (uint64_t)spmi_status,
        (uint64_t)(uintptr_t)vreg, (uint64_t)(uintptr_t)spmi);
    if (EFI_ERROR(vreg_status) || EFI_ERROR(spmi_status) ||
        !chiron_capture_ram((uintptr_t)vreg, sizeof(*vreg), 0) ||
        !chiron_capture_ram((uintptr_t)spmi, sizeof(*spmi), 0)) {
        print("CHIRON L6 CAPTURE: refused protocol RAM/lookup\n");
        return 0;
    }
    uint64_t level = (uintptr_t)vreg->get_level;
    uint64_t base = level >= 0x21a8 ? level - 0x21a8 : 0;
    print("CHIRON L6 CAPTURE: base=%x revisions=%x/%x\n",
        base, vreg->revision, spmi->revision);
    if (!base || base > UINT64_MAX - 0x33000 || vreg->revision != 0x10005 ||
        (uintptr_t)vreg->get_status != base + 0x22ec ||
        (uintptr_t)vreg->set_level != base + 0x219c ||
        (uintptr_t)vreg->set_level_uv != base + 0x2118 ||
        spmi->revision != 0x10000 ||
        !chiron_capture_ram(base + 0x15aa0, sizeof(veneer), 0) ||
        !chiron_capture_ram(base + 0x32030, sizeof(spmi), 1)) {
        print("CHIRON L6 CAPTURE: refused profile or veneer/cache RAM\n");
        print("CHIRON L6 CAPTURE: MMU write PAR=%x\n", chiron_capture_write_par);
        for (uint64_t off = 0; off < chiron_capture_map_size; off += stride) {
            const efi_memory_descriptor *d = (const void *)(chiron_capture_map + off);
            if (base >= d->phys_start && base - d->phys_start < d->pages * 4096)
                print("CHIRON L6 CAPTURE: image RAM type=%d attributes=%x\n",
                    (uint64_t)d->type, d->attribute);
        }
        return 0;
    }
    uint64_t cpacr = 0;
#if defined(__aarch64__)
    __asm__ volatile ("mrs %0, cpacr_el1" : "=r" (cpacr));
#endif
    print("CHIRON L6 CAPTURE: MMU write PAR=%x CPACR=%x\n",
        chiron_capture_write_par, cpacr);
    const volatile uint32_t *code = (const void *)(uintptr_t)(base + 0x15aa0);
    for (unsigned i = 0; i < sizeof(veneer) / sizeof(veneer[0]); i++)
        if (code[i] != veneer[i]) {
            print("CHIRON L6 CAPTURE: refused veneer word=%d observed=%x expected=%x\n",
                (uint64_t)i, (uint64_t)code[i], (uint64_t)veneer[i]);
            return 0;
        }
    struct chiron_capture_spmi *volatile *cached =
        (void *)(uintptr_t)(base + 0x32030);
    print("CHIRON L6 CAPTURE: SPMI=%x revision=%x cached=%x write=%x\n",
        (uint64_t)(uintptr_t)spmi, spmi->revision,
        (uint64_t)(uintptr_t)*cached, (uint64_t)(uintptr_t)spmi->write);
    if (*cached != spmi || !chiron_reset_power(vreg, 1848000))
        return 0;
    uint32_t cfg = chiron_reset_read(0x03d59000), io = chiron_reset_read(0x03d59004);
    if ((cfg & 0x23cu) != 0x200 || (io & 3u) != 0 ||
        chiron_touch_loader_owner(args, boot, boot) != CHIRON_TOUCH_DIRECT_BOOT_CPU)
        return 0;
    struct chiron_capture_spmi shadow = {
        0x10000, chiron_capture_read, chiron_capture_write, chiron_capture_rmw,
        chiron_capture_command, chiron_capture_read_debug, chiron_capture_write
    };
    chiron_capture.calls = chiron_capture.kind = chiron_capture.data = 0;
    /* No original SPMI function is present in the shadow. At NOTIFY, firmware
     * notifications cannot use the temporary private PmicDxe dispatch pointer.
     * The pinned calculator/veneer path itself makes no Boot Services calls. */
    previous = raise(16);
    if (*cached != spmi) {
        restore(previous);
        return 0;
    }
    *cached = &shadow;
    chiron_reset_barrier();
    efi_status status = vreg->set_level_uv(0, 5, 1800000);
    *cached = spmi;
    chiron_reset_barrier();
    restore(previous);
    print("CHIRON L6 CAPTURE: returned=%x restored=1 calls=%d kind=%d\n",
        (uint64_t)status, (uint64_t)chiron_capture.calls, (uint64_t)chiron_capture.kind);
    print("CHIRON L6 CAPTURE: slave=%x priority=%d address=%x bytes=%d data_le=%x\n",
        (uint64_t)chiron_capture.slave, (uint64_t)chiron_capture.priority,
        (uint64_t)chiron_capture.address, (uint64_t)chiron_capture.length,
        (uint64_t)chiron_capture.data);
    int unchanged = *cached == spmi && chiron_reset_power(vreg, 1848000) &&
        chiron_reset_read(0x03d59000) == cfg && chiron_reset_read(0x03d59004) == io;
    print("CHIRON L6 CAPTURE: unchanged=%d; no kernel start\n", (uint64_t)unchanged);
    if (unchanged)
        chiron_capture_channel(spmi);
    return unchanged && EFI_ERROR(status) && chiron_capture.calls == 1;
}
