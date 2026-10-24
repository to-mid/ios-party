/* SPDX-License-Identifier: GPL-2.0-only */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "chiron_clock.h"

#define VOTE (1u << 17)
#define OFF 0x80000000u
#define DIRTY 0x10u
#define MAX_TRACE 20000u

struct event { char kind; unsigned reg; uint32_t value; };
struct fixture {
    uint32_t v[6], initial[6], active_cfg, handoff[4];
    int identity, owner, reads, writes, barriers, delays, properties;
    int fail_read, fail_write, failed_write_applies, ignore_write;
    int lose_after_writes, fail_property, pending, pending_reads, update_after_reads;
    int stuck_ahb, stuck_branch, stuck_root, stuck_update, stuck_disable;
    int regress_ahb, regress_qup, ahb_on_reads, qup_on_reads;
    int barrier_due, advance_mode, now_calls, external_ahb, mutate_vote;
    uint64_t now;
    struct event trace[MAX_TRACE];
    unsigned events;
};

static void
record(struct fixture *f, char kind, unsigned reg, uint32_t value)
{
    assert(f->events < MAX_TRACE);
    f->trace[f->events++] = (struct event){kind, reg, value};
}

static void
ready_callback(struct fixture *f)
{
    assert(!f->barrier_due);
}

static int
property(void *ctx, const char *path, const char *name, const void **data, size_t *size)
{
    struct fixture *f = ctx;
    static const char type[] = "chiron";
    static const uint64_t ranges[] = {0, 0x01000000, 0xff000000};
    static const uint64_t gic[] = {0x16a00000, 0x10000, 0x16b00000, 0x100000};
    static const uint32_t rotation = 0;
    ready_callback(f);
    if (!f->identity || ++f->properties == f->fail_property)
        return -1;
    if (!strcmp(path, "/arm-io") && !strcmp(name, "device_type")) {
        *data = type; *size = sizeof(type);
    } else if (!strcmp(path, "/arm-io") && !strcmp(name, "ranges")) {
        *data = ranges; *size = sizeof(ranges);
    } else if (!strcmp(path, "/arm-io/gic") && !strcmp(name, "reg")) {
        *data = gic; *size = sizeof(gic);
    } else if (!strcmp(path, "/chosen") && !strcmp(name, "display-rotation")) {
        *data = &rotation; *size = sizeof(rotation);
    } else if (!strcmp(path, "/chosen") && !strcmp(name, "chiron-touch-handoff")) {
        if (!f->owner || (f->lose_after_writes >= 0 && f->writes >= f->lose_after_writes))
            return -1;
        *data = f->handoff; *size = sizeof(f->handoff);
    } else {
        assert(0);
        return -1;
    }
    return 0;
}

static unsigned
reg_index(uint64_t address)
{
    for (unsigned i = 0; i <= CB_ROOT_CONFIG; i++)
        if (address == chiron_board_register_address(i))
            return i;
    /* Clock module must never read GPIO/GIC/QUP. */
    assert(0);
    return 0;
}

static int
read32(void *ctx, uint64_t address, uint32_t *value)
{
    struct fixture *f = ctx;
    unsigned reg = reg_index(address);
    ready_callback(f);
    assert(f->identity && f->owner);
    ++f->reads;
    if (f->reads == f->fail_read) {
        record(f, 'E', reg, 0);
        return -1;
    }
    if (reg == CB_ROOT_COMMAND && f->pending && !f->stuck_update &&
        ++f->pending_reads >= f->update_after_reads) {
        f->pending = 0;
        f->v[reg] &= ~(0xf0u | 1u);
        f->active_cfg = f->v[CB_ROOT_CONFIG];
    }
    if (reg == CB_AHB_CLOCK && !(f->v[reg] & OFF) &&
        ++f->ahb_on_reads >= 2 && f->regress_ahb)
        f->v[reg] |= OFF;
    if (reg == CB_QUP_CLOCK && !(f->v[reg] & OFF) &&
        ++f->qup_on_reads >= 3 && f->regress_qup) {
        f->v[reg] |= OFF;
        f->v[CB_ROOT_COMMAND] |= OFF;
    }
    *value = f->v[reg];
    record(f, 'R', reg, *value);
    return 0;
}

static int
write32(void *ctx, uint64_t address, uint32_t value)
{
    struct fixture *f = ctx;
    unsigned reg = reg_index(address);
    ready_callback(f);
    /* Strict physical write whitelist: BCR and AHB CBCR are read-only. */
    assert(address == UINT64_C(0x152004) || address == UINT64_C(0x121024) ||
        address == UINT64_C(0x121020) || address == UINT64_C(0x121008));
    assert(f->identity && f->owner && f->handoff[0] == 1 &&
        f->handoff[1] == 1 && f->handoff[2] == 0xfe && f->handoff[3] == 1);
    assert(f->lose_after_writes < 0 || f->writes < f->lose_after_writes);
    ++f->writes;
    f->barrier_due = 1;
    record(f, 'W', reg, value);
    int fail = f->writes == f->fail_write;
    if ((fail && !f->failed_write_applies) || f->writes == f->ignore_write)
        return fail ? -1 : 0;
    uint32_t previous = f->v[reg];
    if (reg == CB_AHB_VOTE) {
        assert(((previous ^ value) & ~VOTE) == 0);
        f->v[reg] = value;
        if (f->mutate_vote)
            f->v[reg] |= 1u << 30; /* An unrelated voter changes between RMWs. */
        if (((value & VOTE) && !f->stuck_ahb) || f->external_ahb)
            f->v[CB_AHB_CLOCK] &= ~0xf0000000u;
        else
            f->v[CB_AHB_CLOCK] |= OFF;
    } else if (reg == CB_ROOT_CONFIG) {
        assert(((previous ^ value) & ~0x71fu) == 0);
        assert(!(f->v[CB_QUP_CLOCK] & 3u));
        assert(!(f->v[CB_ROOT_COMMAND] & 3u));
        f->v[reg] = value;
        f->v[CB_ROOT_COMMAND] |= DIRTY; /* Dirty shadow until UPDATE latches. */
    } else if (reg == CB_ROOT_COMMAND) {
        assert(value & 1u);
        assert(!(value & 2u)); /* No force-root writes for this source. */
        assert(((previous ^ value) & ~1u) == 0);
        f->v[reg] |= 1u;
        f->pending = 1;
        f->pending_reads = 0;
    } else {
        assert(((previous ^ value) & ~1u) == 0);
        f->v[reg] = (previous & OFF) | (value & ~OFF);
        if (value & 1u) {
            assert(f->v[CB_AHB_VOTE] & VOTE);
            assert(!(f->v[CB_AHB_CLOCK] & OFF));
            assert(!(f->v[CB_ROOT_COMMAND] & (0xf0u | 3u)));
            assert((f->active_cfg & 0x371fu) == 1);
            if (!f->stuck_branch) {
                f->v[reg] &= ~OFF;
                if (!f->stuck_root)
                    f->v[CB_ROOT_COMMAND] &= ~OFF;
            }
        } else if (!f->stuck_disable) {
            f->v[reg] |= OFF;
            f->v[CB_ROOT_COMMAND] |= OFF;
        }
    }
    return fail ? -1 : 0;
}

static void
barrier(void *ctx)
{
    struct fixture *f = ctx;
    assert(f->barrier_due);
    f->barrier_due = 0;
    ++f->barriers;
    record(f, 'B', 0, 0);
}

static uint64_t
now_us(void *ctx)
{
    struct fixture *f = ctx;
    ready_callback(f);
    ++f->now_calls;
    if (f->advance_mode == 2 && f->now_calls > 2)
        return f->now - 1; /* A regression during a wait. */
    return f->now;
}

static void
delay_us(void *ctx, uint32_t us)
{
    struct fixture *f = ctx;
    ready_callback(f);
    assert(us == 1);
    ++f->delays;
    if (f->advance_mode != 1)
        f->now += f->advance_mode == 3 ? 10000 : us;
    record(f, 'D', 0, us);
}

static void
reset(struct fixture *f)
{
    *f = (struct fixture){.identity = 1, .owner = 1,
        .lose_after_writes = -1, .now = 1000, .update_after_reads = 2,
        .handoff = {1, 1, 0xfe, 1},
        /* Actual 2026-10-24 USB observation, including unrelated bits. */
        .v = {0, 0x80889d80u, 0x8000c220u, 0x80000000u, 0x80000000u, 0}};
    memcpy(f->initial, f->v, sizeof(f->v));
}

static int
prepare(struct fixture *f, struct chiron_clock_report *r)
{
    struct chiron_clock_io io = {
        .board = {.context = f, .property = property, .read32 = read32},
        .write32 = write32, .barrier = barrier, .now_us = now_us, .delay_us = delay_us
    };
    int result = chiron_clock_prepare(&io, r);
    assert(result == r->result);
    assert(f->writes == f->barriers && !f->barrier_due);
    for (unsigned i = 0; i < f->events; i++)
        if (f->trace[i].kind == 'W')
            assert(i + 1 < f->events && f->trace[i + 1].kind == 'B');
    assert(f->delays <= 6 * (int)CHIRON_CLOCK_WAIT_ITERATIONS);
    return result;
}

static void
restored(struct fixture *f, struct chiron_clock_report *r)
{
    assert(r->result < 0 && r->remaining == 0);
    assert(r->rollback == (r->changed ? CC_ROLLBACK_COMPLETE : CC_ROLLBACK_NONE));
    assert(r->cleanup_result == 0);
    assert((f->v[CB_AHB_VOTE] & VOTE) == (f->initial[CB_AHB_VOTE] & VOTE));
    assert((f->v[CB_QUP_CLOCK] & 3u) == (f->initial[CB_QUP_CLOCK] & 3u));
    assert((f->v[CB_ROOT_CONFIG] & 0x371fu) ==
        (f->initial[CB_ROOT_CONFIG] & 0x371fu));
    assert((f->active_cfg & 0x371fu) == (f->initial[CB_ROOT_CONFIG] & 0x371fu));
}

static void
success(void)
{
    struct fixture f;
    struct chiron_clock_report r;
    reset(&f);
    assert(prepare(&f, &r) == 0 && r.step == CC_DONE);
    assert(r.before.observed == 0x3f && r.after.observed == 0x3f);
    assert(!memcmp(r.before.values, f.initial, sizeof(f.initial)));
    assert(f.writes == 4 && r.rollback == CC_ROLLBACK_NONE);
    assert(r.changed == ((1u << CB_AHB_VOTE) | (1u << CB_QUP_CLOCK) |
        (1u << CB_ROOT_CONFIG) | (1u << CB_ROOT_COMMAND)));
    assert(r.remaining == r.changed);
    assert(f.v[CB_AHB_VOTE] == 0x808a9d80u);
    assert(f.v[CB_AHB_CLOCK] == 0xc220u && f.v[CB_QUP_CLOCK] == 1);
    assert(f.v[CB_ROOT_COMMAND] == 0 && f.v[CB_ROOT_CONFIG] == 1);
    const unsigned order[] = {CB_AHB_VOTE, CB_ROOT_CONFIG, CB_ROOT_COMMAND, CB_QUP_CLOCK};
    const uint32_t values[] = {0x808a9d80u, 1, 0x80000011u, 0x80000001u};
    unsigned n = 0;
    int ahb_checked = 0, cfg_checked = 0, update_checked = 0;
    for (unsigned i = 0; i < f.events; i++) {
        struct event e = f.trace[i];
        if (e.kind == 'R' && e.reg == CB_AHB_CLOCK && !(e.value & OFF))
            ahb_checked = 1;
        if (e.kind == 'R' && e.reg == CB_ROOT_CONFIG && (e.value & 0x371fu) == 1)
            cfg_checked = 1;
        if (e.kind == 'R' && e.reg == CB_ROOT_COMMAND &&
            !(e.value & (0xf0u | 1u)) && n >= 3)
            update_checked = 1;
        if (e.kind != 'W')
            continue;
        assert(n < 4 && e.reg == order[n] && e.value == values[n]);
        if (n == 1) assert(ahb_checked);
        if (n == 2) assert(cfg_checked);
        if (n == 3) assert(update_checked);
        n++;
    }
    assert(n == 4);
    int writes = f.writes;
    assert(prepare(&f, &r) == 0 && f.writes == writes && r.changed == 0);
}

static void
preexisting(void)
{
    struct fixture f;
    struct chiron_clock_report r;
    reset(&f);
    f.v[CB_AHB_VOTE] |= VOTE;
    f.v[CB_AHB_CLOCK] &= ~OFF;
    f.v[CB_ROOT_CONFIG] = f.active_cfg = 0xa5000081u;
    f.v[CB_QUP_CLOCK] |= 0x800u;
    f.external_ahb = 1;
    memcpy(f.initial, f.v, sizeof(f.v));
    assert(prepare(&f, &r) == 0 && f.writes == 1);
    assert(f.v[CB_ROOT_CONFIG] == 0xa5000081u && f.v[CB_QUP_CLOCK] == 0x801u);
    reset(&f);
    f.v[CB_ROOT_CONFIG] = 0xa5000080u;
    f.v[CB_QUP_CLOCK] |= 0x800u;
    assert(prepare(&f, &r) == 0);
    assert(f.v[CB_ROOT_CONFIG] == 0xa5000081u && f.v[CB_QUP_CLOCK] == 0x801u);
    reset(&f);
    f.v[CB_AHB_VOTE] |= VOTE;
    f.v[CB_AHB_CLOCK] &= ~OFF;
    f.stuck_branch = 1;
    memcpy(f.initial, f.v, sizeof(f.v));
    assert(prepare(&f, &r) == CHIRON_CLOCK_TIMEOUT);
    restored(&f, &r);
    for (unsigned i = 0; i < f.events; i++)
        assert(f.trace[i].kind != 'W' || f.trace[i].reg != CB_AHB_VOTE);
    reset(&f);
    f.external_ahb = 1;
    f.v[CB_AHB_CLOCK] &= ~OFF;
    f.mutate_vote = 1;
    f.stuck_branch = 1;
    memcpy(f.initial, f.v, sizeof(f.v));
    assert(prepare(&f, &r) == CHIRON_CLOCK_TIMEOUT);
    restored(&f, &r);
    assert(f.v[CB_AHB_VOTE] == (f.initial[CB_AHB_VOTE] | (1u << 30)));
    assert(!(f.v[CB_AHB_CLOCK] & OFF));
}

static void
refusals(void)
{
    struct fixture f;
    struct chiron_clock_report r;
    reset(&f); f.identity = 0;
    assert(prepare(&f, &r) == CB_PLATFORM && f.reads == 0 && f.writes == 0);
    reset(&f); f.owner = 0;
    assert(prepare(&f, &r) == CB_OWNERSHIP && f.reads == 0 && f.writes == 0);
    for (unsigned i = 0; i < 4; i++) {
        reset(&f); f.handoff[i] = 0;
        assert(prepare(&f, &r) == CB_OWNERSHIP && f.writes == 0 && f.reads == 0);
    }
    const unsigned regs[] = {CB_BLSP_RESET, CB_ROOT_CONFIG, CB_ROOT_CONFIG,
        CB_ROOT_COMMAND, CB_ROOT_COMMAND, CB_ROOT_COMMAND, CB_ROOT_COMMAND,
        CB_ROOT_COMMAND, CB_QUP_CLOCK, CB_QUP_CLOCK, CB_AHB_CLOCK};
    const uint32_t bad[] = {1, 2, 0x2000, OFF | 2, OFF | 1, OFF | 0x10,
        OFF | 4, 0, OFF | 2, 1, 0x4000c220};
    for (unsigned i = 0; i < sizeof(regs) / sizeof(*regs); i++) {
        reset(&f); f.v[regs[i]] = bad[i];
        assert(prepare(&f, &r) == CHIRON_CLOCK_PROFILE && f.writes == 0);
    }
    reset(&f); f.v[CB_AHB_VOTE] |= VOTE; /* Existing vote but halted. */
    assert(prepare(&f, &r) == CHIRON_CLOCK_PROFILE && f.writes == 0);
    assert(chiron_clock_prepare(NULL, &r) == CHIRON_CLOCK_BAD_IO);
    assert(chiron_clock_prepare(NULL, NULL) == CHIRON_CLOCK_BAD_IO);
}

static void
io_failures(void)
{
    struct fixture f;
    struct chiron_clock_report r;
    reset(&f);
    assert(prepare(&f, &r) == 0);
    int reads = f.reads;
    for (int i = 1; i <= reads; i++) {
        reset(&f); f.fail_read = i;
        int result = prepare(&f, &r);
        if (result != CB_READ_FAILED || r.remaining)
            fprintf(stderr, "read failure %d: result=%d step=%d rollback=%d cleanup=%d remain=%x\n",
                i, result, r.step, r.rollback, r.cleanup_result, r.remaining);
        assert(result == CB_READ_FAILED);
        restored(&f, &r);
    }
    for (int applies = 0; applies <= 1; applies++)
        for (int i = 1; i <= 4; i++) {
            reset(&f); f.fail_write = i; f.failed_write_applies = applies;
            assert(prepare(&f, &r) == CHIRON_CLOCK_WRITE);
            restored(&f, &r);
        }
    for (int i = 1; i <= 4; i++) {
        reset(&f); f.ignore_write = i;
        int result = prepare(&f, &r);
        assert(result == (i == 3 ? CHIRON_CLOCK_TIMEOUT : CHIRON_CLOCK_READBACK));
        restored(&f, &r);
    }
}

static void
timeouts(void)
{
    struct fixture f;
    struct chiron_clock_report r;
    for (int i = 0; i < 4; i++) {
        reset(&f);
        if (i == 0) f.stuck_ahb = 1;
        if (i == 1) f.stuck_branch = 1;
        if (i == 2) f.stuck_root = 1;
        if (i == 3) f.stuck_update = 1;
        assert(prepare(&f, &r) == CHIRON_CLOCK_TIMEOUT);
        if (i < 3)
            restored(&f, &r);
        else {
            assert(r.rollback == CC_ROLLBACK_INCOMPLETE && r.cleanup_result == CHIRON_CLOCK_TIMEOUT);
            assert(f.writes == 3 && r.remaining == r.changed);
        }
    }
    reset(&f); f.regress_ahb = 1;
    assert(prepare(&f, &r) < 0); restored(&f, &r);
    reset(&f); f.regress_qup = 1;
    assert(prepare(&f, &r) < 0); restored(&f, &r);
}

static void
time_sources(void)
{
    struct fixture f;
    struct chiron_clock_report r;
    for (int mode = 1; mode <= 3; mode++) {
        reset(&f); f.stuck_ahb = 1; f.advance_mode = mode;
        assert(prepare(&f, &r) == (mode == 2 ? CHIRON_CLOCK_TIMER : CHIRON_CLOCK_TIMEOUT));
        restored(&f, &r);
        if (mode == 1) assert(f.delays == (int)CHIRON_CLOCK_WAIT_ITERATIONS);
        if (mode == 3) assert(f.delays == 1);
    }
    reset(&f); f.advance_mode = 3; f.update_after_reads = 3;
    assert(prepare(&f, &r) == CHIRON_CLOCK_TIMEOUT);
}

static void
ownership_loss(void)
{
    struct fixture f;
    struct chiron_clock_report r;
    for (int i = 0; i <= 4; i++) {
        reset(&f); f.lose_after_writes = i;
        assert(prepare(&f, &r) == CB_OWNERSHIP);
        assert(f.writes == i && r.changed == r.remaining);
        assert(r.rollback == (i ? CC_ROLLBACK_OWNER_LOST : CC_ROLLBACK_NONE));
    }
    reset(&f);
    assert(prepare(&f, &r) == 0);
    int properties = f.properties;
    /* Every property lookup on the success path can fail; a failure after
     * writes must latch owner loss and prevent all cleanup writes. */
    for (int i = 1; i <= properties; i++) {
        reset(&f); f.fail_property = i;
        assert(prepare(&f, &r) < 0);
        if (r.changed)
            assert(r.rollback == CC_ROLLBACK_OWNER_LOST && r.remaining == r.changed);
    }
}

static void
cleanup_failures(void)
{
    struct fixture f;
    struct chiron_clock_report r;
    for (int write = 5; write <= 8; write++) {
        reset(&f); f.stuck_root = 1; f.fail_write = write;
        assert(prepare(&f, &r) == CHIRON_CLOCK_TIMEOUT);
        assert(r.rollback == CC_ROLLBACK_INCOMPLETE && r.remaining);
        assert(r.cleanup_result == CHIRON_CLOCK_WRITE);
        assert(f.writes == write); /* Stop at first uncertain cleanup step. */
    }
    reset(&f); f.stuck_root = 1; f.stuck_disable = 1;
    assert(prepare(&f, &r) == CHIRON_CLOCK_TIMEOUT);
    assert(r.rollback == CC_ROLLBACK_INCOMPLETE && r.cleanup_result == CHIRON_CLOCK_TIMEOUT);
    assert(f.writes == 5 && (r.remaining & (1u << CB_QUP_CLOCK)));
}

int
main(int argc, char **argv)
{
    assert(argc == 2);
    switch (atoi(argv[1])) {
    case 0: success(); break;
    case 1: preexisting(); break;
    case 2: refusals(); break;
    case 3: io_failures(); break;
    case 4: timeouts(); break;
    case 5: time_sources(); break;
    case 6: ownership_loss(); break;
    case 7: cleanup_failures(); break;
    default: assert(0);
    }
    return 0;
}
