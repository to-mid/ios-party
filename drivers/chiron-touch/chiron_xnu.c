/*
 * Replaces only redwood_goodix.c in an explicitly selected chiron scratch tree.
 * Keep the existing init symbol so the shared input queue needs no new ABI.
 */
#if !defined(CHIRON_PORT)
#error "The chiron touch overlay requires the CHIRON_PORT kernel build."
#endif

#include <kern/clock.h>
#include <kern/debug.h>
#include <kern/sched_prim.h>
#include <kern/thread.h>
#include <libkern/libkern.h>
#include <mach/mach_types.h>
#include <pexpert/pexpert.h>
#include <pexpert/device_tree.h>
#include <sys/redwood_input.h>
#include <sys/systm.h>

#include "chiron_dsx.h"
#include "chiron_touch_core.inc"
#include "chiron_touch_qup.inc"
#include "chiron_touch_f12.inc"
#include "chiron_touch_native.inc"
#include "chiron_touch_board.inc"

_Static_assert(CHIRON_DSX_DOWN == REDWOOD_TOUCH_DOWN, "touch DOWN ABI");
_Static_assert(CHIRON_DSX_MOVE == REDWOOD_TOUCH_MOVE, "touch MOVE ABI");
_Static_assert(CHIRON_DSX_UP == REDWOOD_TOUCH_UP, "touch UP ABI");

extern kern_return_t thread_terminate(thread_t thread);
extern vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);

static struct chiron_dsx chiron_touch;
static struct chiron_native chiron_native;
static struct chiron_board_io chiron_board_io;
static struct chiron_board_report chiron_board_report;
static uint32_t chiron_touch_mode;
static struct {
    uint64_t physical;
    uint32_t size;
    vm_offset_t mapped;
} chiron_regions[] = {
    {CHIRON_GCC_BASE, CHIRON_GCC_SIZE, 0},
    {CHIRON_GPIO_BASE, CHIRON_GPIO_SIZE, 0},
    {CHIRON_GIC_BASE, CHIRON_GIC_SIZE, 0},
    {CHIRON_QUP_BASE, CHIRON_QUP_SIZE, 0}
};

static void
chiron_io_barrier(void *context)
{
    (void)context;
#if defined(__aarch64__)
    __asm__ volatile ("dmb sy" ::: "memory");
#else
    __sync_synchronize(); /* host syntax/contract tests */
#endif
}

static void
chiron_touch_wait(uint32_t interval, uint32_t scale)
{
    /* This worker holds no lock; always use a scheduler wait, never a spin. */
    if (assert_wait_timeout((event_t)&chiron_touch, THREAD_UNINT, interval,
        scale) == THREAD_WAITING)
        (void)thread_block(THREAD_CONTINUE_NULL);
}

static void
chiron_touch_sleep(void)
{
    chiron_touch_wait(20, NSEC_PER_MSEC);
}

static int
chiron_property(void *context, const char *path, const char *name,
    const void **data, size_t *size)
{
    DTEntry entry;
    unsigned int length;
    (void)context;
    if (SecureDTLookupEntry(NULL, path, &entry) != kSuccess ||
        SecureDTGetProperty(entry, name, data, &length) != kSuccess)
        return -1;
    *size = length;
    return 0;
}

static vm_offset_t
chiron_map_register(uint64_t physical)
{
    if (!chiron_board_report.platform_valid || (physical & 3u))
        return 0;
    for (unsigned i = 0; i < sizeof(chiron_regions) / sizeof(chiron_regions[0]); i++) {
        if (physical < chiron_regions[i].physical ||
            physical - chiron_regions[i].physical > chiron_regions[i].size - 4)
            continue;
        if (!chiron_regions[i].mapped)
            chiron_regions[i].mapped = ml_io_map((vm_offset_t)chiron_regions[i].physical,
                chiron_regions[i].size);
        if (!chiron_regions[i].mapped)
            return 0;
        return chiron_regions[i].mapped + (vm_offset_t)(physical - chiron_regions[i].physical);
    }
    return 0;
}

static int
chiron_physical_read(void *context, uint64_t physical, uint32_t *value)
{
    (void)context;
    vm_offset_t address = chiron_map_register(physical);
    if (!address)
        return -1;
    chiron_io_barrier(NULL);
    *value = *(volatile uint32_t *)address;
    chiron_io_barrier(NULL);
    return 0;
}

static uint32_t
chiron_qup_read32(void *context, uint32_t offset)
{
    uint32_t value = UINT32_MAX;
    (void)context;
    if (offset < CHIRON_QUP_SIZE && !(offset & 3u))
        (void)chiron_physical_read(NULL, CHIRON_QUP_BASE + offset, &value);
    return value;
}

static void
chiron_qup_write32(void *context, uint32_t offset, uint32_t value)
{
    (void)context;
    if (offset >= CHIRON_QUP_SIZE || (offset & 3u))
        return;
    vm_offset_t address = chiron_map_register(CHIRON_QUP_BASE + offset);
    if (address)
        *(volatile uint32_t *)address = value;
}

static int
chiron_retained(void *context)
{
    (void)context;
    return chiron_board_retained(&chiron_board_io, &chiron_board_report) == 0;
}

static void
chiron_delay_us(void *context, uint32_t interval)
{
    (void)context;
    chiron_touch_wait(interval, NSEC_PER_USEC);
}

static uint64_t
chiron_now_us(void *context)
{
    uint64_t ns;
    (void)context;
    absolutetime_to_nanoseconds(mach_absolute_time(), &ns);
    return ns / 1000;
}

static void
chiron_probe_report(void)
{
    printf("chiron touch: retained probe status=%d failed=%d observed=%x handoff=%u/%u/%x/%u\n",
        chiron_board_report.issue, chiron_board_report.failed_register,
        (unsigned)chiron_board_report.observed,
        (unsigned)chiron_board_report.handoff[0], (unsigned)chiron_board_report.handoff[1],
        (unsigned)chiron_board_report.handoff[2], (unsigned)chiron_board_report.handoff[3]);
    for (unsigned i = 0; i < CB_REGISTER_COUNT; i++)
        if (chiron_board_report.observed & (1u << i))
            printf("chiron touch: %s [%llx]=%08x\n", chiron_board_register_name(i),
                (unsigned long long)chiron_board_register_address(i),
                (unsigned)chiron_board_report.values[i]);
    if (chiron_board_report.failed_register >= 0) {
        unsigned index = (unsigned)chiron_board_report.failed_register;
        if (chiron_board_report.issue == CB_READ_FAILED)
            printf("chiron touch: cannot map/read prerequisite %s\n",
                chiron_board_register_name(index));
        else
            printf("chiron touch: prerequisite %s requires mask=%08x expected=%08x observed=%08x\n",
                chiron_board_register_name(index), (unsigned)chiron_board_report.expected_mask,
                (unsigned)chiron_board_report.expected_value,
                (unsigned)chiron_board_report.values[index]);
    }
    if (chiron_board_report.issue == CB_OWNERSHIP)
        printf("chiron touch: resident UEFI ownership unconfirmed: direct boot-CPU handoff "
            "and seven other affinities confirmed OFF are required; probe only\n");
}

static void
chiron_post(void *context, uint32_t type, uint32_t finger, uint32_t x, uint32_t y)
{
    (void)context;
    redwood_touch_post(type, finger, x, y);
}

static int
chiron_native_transport(struct chiron_dsx_bus *bus)
{
    int status = chiron_board_probe(&chiron_board_io, &chiron_board_report);
    chiron_probe_report();
    if (chiron_touch_mode == 2)
        printf("chiron touch: read-only probe requested; no controller writes\n");
    if (status || chiron_touch_mode == 2)
        return status ? status : CHIRON_DSX_NO_TRANSPORT;
    struct chiron_qup_io io = {
        NULL, chiron_retained, chiron_qup_read32, chiron_qup_write32,
        chiron_io_barrier, chiron_delay_us, chiron_now_us
    };
    printf("chiron touch: retained registers pass; assuming boot-CPU firmware suspension "
        "excludes UEFI I2C clients (Boot Services remain resident)\n");
    /* Chiron's measured DT has no swap/x-flip/y-flip properties. */
    return chiron_native_bind(&chiron_native, &io, 0, bus);
}

static void
chiron_touch_probe_stop(void)
{
    chiron_board_io = (struct chiron_board_io){NULL, chiron_property, chiron_physical_read};
    int status = chiron_board_probe(&chiron_board_io, &chiron_board_report);
    chiron_probe_report();
    printf("CHIRON TOUCH PROBE: read-only, no transport or worker; stopping before GUI\n");
    /*
     * Include the snapshot in the panic itself for a photograph. The observed
     * bitmask marks valid cells; unread cells remain zero placeholders.
     */
    panic("CHIRON TOUCH PROBE READ ONLY status=%d bad=%d obs=%x\n"
        "owner=%u/%u/%x/%u\n"
        "GCC bcr=%x vote=%x ahb=%x qup=%x cmd=%x cfg=%x\n"
        "GPIO87=%x GPIO88=%x resetcfg=%x resetio=%x\n"
        "GIC en4=%x QUP state=%x status=%x\n"
        "obs marks valid cells; unread=0. NO CONTROLLER WRITES.",
        status, chiron_board_report.failed_register, (unsigned)chiron_board_report.observed,
        (unsigned)chiron_board_report.handoff[0], (unsigned)chiron_board_report.handoff[1],
        (unsigned)chiron_board_report.handoff[2], (unsigned)chiron_board_report.handoff[3],
        (unsigned)chiron_board_report.values[CB_BLSP_RESET],
        (unsigned)chiron_board_report.values[CB_AHB_VOTE],
        (unsigned)chiron_board_report.values[CB_AHB_CLOCK],
        (unsigned)chiron_board_report.values[CB_QUP_CLOCK],
        (unsigned)chiron_board_report.values[CB_ROOT_COMMAND],
        (unsigned)chiron_board_report.values[CB_ROOT_CONFIG],
        (unsigned)chiron_board_report.values[CB_SDA_MUX],
        (unsigned)chiron_board_report.values[CB_SCL_MUX],
        (unsigned)chiron_board_report.values[CB_RESET_CONFIG],
        (unsigned)chiron_board_report.values[CB_RESET_LEVEL],
        (unsigned)chiron_board_report.values[CB_QUP_IRQ_ENABLE],
        (unsigned)chiron_board_report.values[CB_QUP_STATE],
        (unsigned)chiron_board_report.values[CB_QUP_STATUS]);
}

static void
chiron_touch_thread(void *parameter, wait_result_t result)
{
    int status;
    struct chiron_dsx_bus bus = {0};
    (void)parameter;
    (void)result;
    status = chiron_native_transport(&bus);
    if (status) {
        printf("chiron touch: transport refused (%d); no clock/pin/reset/regulator writes\n", status);
        thread_terminate(current_thread());
        return;
    }
    chiron_dsx_init(&chiron_touch, &bus, chiron_post, NULL);
    status = chiron_dsx_start(&chiron_touch);
    if (status) {
        printf("chiron touch: discovery/layout refused (%d)\n", status);
        thread_terminate(current_thread());
        return;
    }
    printf("chiron touch: verified DSX layout, polling every 20 ms\n");
    for (;;) {
        /* Thread context is required by redwood_touch_post's mutex/queue. */
        status = chiron_dsx_poll(&chiron_touch);
        if (status) {
            chiron_probe_report();
            printf("chiron touch: report failed (%d), all fingers released; stopped\n", status);
            thread_terminate(current_thread());
            return;
        }
        chiron_touch_sleep();
    }
}

void
redwood_goodix_init(void)
{
    uint32_t enabled = 0, probe = 0;
    thread_t thread;
    int status;
    /* Probe takes priority even if the payload also requests normal touch. */
    if (PE_parse_boot_argn("chiron_touch_probe", &probe, sizeof(probe)) && probe == 1) {
        chiron_touch_probe_stop();
        return;
    }
    if (!PE_parse_boot_argn("chiron_touch", &enabled, sizeof(enabled)) ||
        (enabled != 1 && enabled != 2))
        return;
    chiron_touch_mode = enabled;
    chiron_board_io = (struct chiron_board_io){NULL, chiron_property, chiron_physical_read};
    status = chiron_board_identity(&chiron_board_io, &chiron_board_report);
    if (status) {
        printf("chiron touch: platform refused before MMIO (%d), hardware untouched\n", status);
        return;
    }
    if (kernel_thread_start(chiron_touch_thread, NULL, &thread) == KERN_SUCCESS)
        thread_deallocate(thread);
    else
        printf("chiron touch: failed to start polling thread\n");
}
