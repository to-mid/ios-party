/*
 * Replaces only redwood_goodix.c in an explicitly selected chiron scratch tree.
 * Keep the existing init symbol so the shared input queue needs no new ABI.
 */
#if !defined(CHIRON_PORT)
#error "The chiron touch overlay requires the CHIRON_PORT kernel build."
#endif

#include <kern/clock.h>
#include <kern/sched_prim.h>
#include <kern/thread.h>
#include <libkern/libkern.h>
#include <mach/mach_types.h>
#include <pexpert/pexpert.h>
#include <sys/redwood_input.h>
#include <sys/systm.h>

#include "chiron_dsx.h"
#include "chiron_touch_core.inc"
#include "chiron_touch_qup.inc"
#include "chiron_touch_f12.inc"
#include "chiron_touch_native.inc"

_Static_assert(CHIRON_DSX_DOWN == REDWOOD_TOUCH_DOWN, "touch DOWN ABI");
_Static_assert(CHIRON_DSX_MOVE == REDWOOD_TOUCH_MOVE, "touch MOVE ABI");
_Static_assert(CHIRON_DSX_UP == REDWOOD_TOUCH_UP, "touch UP ABI");

extern kern_return_t thread_terminate(thread_t thread);

static struct chiron_dsx chiron_touch;

static void
chiron_touch_sleep(void)
{
    /* This worker holds no lock; always use a scheduler wait, never a spin. */
    if (assert_wait_timeout((event_t)&chiron_touch, THREAD_UNINT, 20,
        NSEC_PER_MSEC) == THREAD_WAITING)
        (void)thread_block(THREAD_CONTINUE_NULL);
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
    (void)bus;
    /*
     * FIFO transport and F12 validator are compiled above. BLOCKED: there is
     * no board handoff provider proving post-EBS clocks, pinmux, rails/reset,
     * exclusive QUP ownership, masked IRQ and coordinate orientation.
     * Do not fabricate chiron_qup_io or map QUP/GCC/TLMM here.
     */
    return CHIRON_DSX_NO_TRANSPORT;
}

static void
chiron_touch_thread(void *parameter, wait_result_t result)
{
    int status;
    (void)parameter;
    (void)result;
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
    struct chiron_dsx_bus bus = {0};
    uint32_t enabled = 0;
    thread_t thread;
    int status;
    if (!PE_parse_boot_argn("chiron_touch", &enabled, sizeof(enabled)) || enabled != 1)
        return;
    status = chiron_native_transport(&bus);
    if (status) {
        printf("chiron touch: BLOCKED: post-EBS clocks/pins/power/ownership and orientation "
            "handoff unverified; native QUP5 FIFO and F12 validator compiled; "
            "bus 5 addr 0x20, reset GPIO89, IRQ GPIO125; "
            "hardware untouched\n");
        return;
    }
    chiron_dsx_init(&chiron_touch, &bus, chiron_post, NULL);
    if (kernel_thread_start(chiron_touch_thread, NULL, &thread) == KERN_SUCCESS)
        thread_deallocate(thread);
    else
        printf("chiron touch: failed to start polling thread\n");
}
