/* Deterministic register model. These values are synthetic, not a handoff. */
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "chiron_native.h"

static struct {
    uint32_t regs[0x410 / 4], reads, writes, barriers, delays;
    uint8_t tx[16], rx[84], page;
    size_t tx_bytes, rx_bytes, rx_at;
    uint64_t elapsed_us;
    int owned, scenario, running, layout_case, sensor_status, bitmap;
    unsigned transactions, sensor_reads, bitmap_reads;
} m;
static const struct chiron_dsx_functions functions = {
    {0x10, 0x30, 0x31, 0x32, 1, 0},
    {0x110, 0x140, 0x141, 0x160, 0x12, 0}
};

static int
sensor_read(void *context, uint16_t address, uint8_t *out, size_t length)
{
    assert(context == &m);
    m.sensor_reads++;
    memset(out, 0, length);
    if (m.layout_case == 1)
        return (int)length - 1;
    if (address == functions.f01.data && length == 1) {
        out[0] = (uint8_t)m.sensor_status;
    } else if (address == functions.f01.control && length == 1) {
        out[0] = m.layout_case == 2 ? 0x81 : 0x80;
    } else if (address == functions.f01.query && length == 21) {
        out[0] = m.layout_case == 3 ? 2 : 1;
    } else if (address == functions.f01.query + 18 && length == 3) {
        uint32_t id = CHIRON_DSX_MEASURED_BUILD;
        out[0] = (uint8_t)id;
        out[1] = (uint8_t)(id >> 8);
        out[2] = (uint8_t)(id >> 16);
        if (m.layout_case == 4) out[0]++;
    } else if (address == functions.f12.query + 4 && length == 1) {
        out[0] = m.layout_case == 5 ? 4 : m.layout_case == 6 ? 10 : 5;
    } else if (address == functions.f12.query + 5) {
        uint8_t data[] = {10, 1, 1, 0x90, 0x10};
        assert(length == 5);
        if (m.layout_case == 7) data[2] = 0; /* No Ctrl8 (wedge sensor). */
        if (m.layout_case == 8) data[4] = 0; /* No Ctrl28. */
        if (m.layout_case == 9) data[0] = 65;
        if (m.layout_case == 10) data[0] = 0;
        if (m.layout_case == 11) data[0] = 7; /* Truncated Query6. */
        memcpy(out, data, length);
    } else if (address == functions.f12.query + 6) {
        uint8_t data[] = {1, 1, 14, 1, 3, 1, 2, 3, 1, 1};
        assert(length <= sizeof(data));
        if (m.layout_case == 12) data[7] = 0x83; /* Unbounded/missing group. */
        if (m.layout_case == 13) data[7] = 0x23; /* Unknown Ctrl23 subpacket5. */
        if (m.layout_case == 14) data[7] = 1; /* Missing mandatory subpacket1. */
        if (m.layout_case == 15) data[6] = 0; /* Extended target size refused. */
        if (m.layout_case == 16) data[6] = 4; /* Size disagrees with bitmap. */
        if (m.layout_case == 17 || m.layout_case == 18) {
            data[6] = 3;
            data[7] = 7;
        }
        memcpy(out, data, length);
    } else if (address == functions.f12.query + 7 && length == 1) {
        out[0] = m.layout_case >= 29 ? 3 :
            m.layout_case == 19 ? 1 : m.layout_case == 20 ? 6 : 2;
    } else if (address == functions.f12.query + 8) {
        assert(length == 2 || length == 3);
        out[1] = m.layout_case == 21 ? 1 : m.layout_case == 22 ? 2 : 3;
        if (length == 3) out[2] = 0x80;
    } else if (address == functions.f12.control + 1) {
        assert(length == 14);
        out[0] = 0xe8; out[1] = 3; /* inclusive X=1000 */
        out[2] = 0xd0; out[3] = 7; /* inclusive Y=2000 */
        if (m.layout_case == 23) out[0] = out[1] = 0;
    } else if (address == functions.f12.control + 2) {
        assert(length == 3);
        out[2] = m.layout_case == 24 ? 2 : 0;
    } else if (address == functions.f12.control + 3) {
        assert(length == 2 || length == 3);
        out[0] = m.layout_case == 25 ? 0 : 1;
        out[1] = m.layout_case == 26 ? 0 : m.layout_case == 27 ? 11 :
            m.layout_case == 31 ? 10 : 2;
        if (m.layout_case == 18) out[2] = 4; /* Palm remapped as finger. */
    } else if (address == functions.f12.control + 4 && length == 1) {
        out[0] = m.layout_case == 28 ? 0x1f : 0xff;
    } else if (address == functions.f12.data + 1 && (length == 16 || length == 80)) {
        out[0] = 1;
        out[1] = 0xf4; out[2] = 1;
        out[3] = 0xe8; out[4] = 3; /* finger0 at 500/1000 */
        if (m.layout_case == 32) m.sensor_status = 0x80;
    } else if (address == functions.f12.data + 2 && (length == 1 || length == 2)) {
        out[0] = (uint8_t)m.bitmap;
        if (m.layout_case == 30 && ++m.bitmap_reads % 2 == 0) out[0] ^= 1;
    } else if (length == 6 && (address & 0xff) >= 5 &&
        ((address & 0xff) <= 0xe9)) {
        if (address == 0xe9) {
            uint8_t entry[] = {0x10, 0x30, 0x31, 0x32, 1, 1};
            memcpy(out, entry, 6);
        } else if (address == 0x1e9) {
            uint8_t entry[] = {0x10, 0x40, 0x41, 0x60, 1, 0x12};
            memcpy(out, entry, 6);
        }
    } else {
        assert(!"unexpected sensor window");
    }
    return (int)length;
}

static void
setup(void)
{
    memset(&m, 0, sizeof(m));
    m.owned = 1;
    m.bitmap = 1;
    m.regs[CQ_STATE / 4] = 0x14;
    m.regs[CQ_IO_MODES / 4] = (2u << 2) | (2u << 7); /* 128-byte FIFOs */
    m.regs[CQ_STATUS / 4] = 3u << 26;
}

static void
execute_tags(void)
{
    uint32_t output = m.regs[CQ_MX_WRITE / 4], input = m.regs[CQ_MX_READ / 4];
    assert(output == 6 || output == 9);
    assert(m.tx_bytes == ((output + 3) & ~3u));
    m.transactions++;
    assert(m.tx[0] == 0x81 && m.tx[1] == 0x40);
    if (output == 6) {
        assert(input == 0 && m.tx[2] == 0x83 && m.tx[3] == 2);
        assert(m.tx[4] == 0xff && m.tx[5] < 5);
        m.page = m.tx[5];
    } else {
        assert(m.tx[2] == 0x82 && m.tx[3] == 1);
        assert(m.tx[5] == 0x81 && m.tx[6] == 0x41 && m.tx[7] == 0x87);
        assert(input == (uint32_t)m.tx[8] + 2 && input <= 82);
        m.rx[0] = 0x85; m.rx[1] = m.tx[8];
        assert(sensor_read(&m, (uint16_t)((m.page << 8) | m.tx[4]),
            m.rx + 2, m.tx[8]) == m.tx[8]);
        m.rx_bytes = (input + 3) & ~3u;
        if (m.scenario == 5) m.rx_bytes = 0;
        if (m.scenario == 6) m.rx_bytes += 4;
        assert(m.rx_bytes <= sizeof(m.rx));
    }
    if (m.scenario == 1) m.regs[CQ_STATUS / 4] |= 8; /* NACK */
    if (m.scenario == 2) m.regs[CQ_ERROR / 4] |= 4;
    if (m.scenario == 3 || m.scenario == 12 || m.scenario == 13 ||
        m.scenario == 14) return; /* No completion. */
    if (m.scenario == 4) m.regs[CQ_STATUS / 4] |= 0x100;
    if (m.scenario == 8) m.regs[CQ_STATUS / 4] |= 0x10; /* arbitration lost */
    if (m.scenario == 9) m.regs[CQ_STATUS / 4] |= 4; /* bus error */
    if (m.scenario == 10) m.regs[CQ_STATUS / 4] |= 0x40; /* failed */
    if (m.scenario == 11) m.regs[CQ_STATUS / 4] |= 1u << 23; /* bad tag */
    m.regs[CQ_OPERATIONAL / 4] = input ? 0xa00 : 0x100;
}

static uint32_t
read32(void *context, uint32_t offset)
{
    assert(context == &m && offset % 4 == 0 && offset < sizeof(m.regs));
    m.reads++;
    if (offset == CQ_OPERATIONAL)
        return m.regs[offset / 4] | (m.rx_at < m.rx_bytes ? 0x20u : 0);
    if (offset == CQ_IN_FIFO) {
        assert(m.rx_at < m.rx_bytes);
        uint32_t word = 0;
        for (unsigned i = 0; i < 4; i++) word |= (uint32_t)m.rx[m.rx_at++] << (8 * i);
        return word;
    }
    return m.regs[offset / 4];
}

static void
write32(void *context, uint32_t offset, uint32_t value)
{
    assert(context == &m && offset % 4 == 0 && offset < sizeof(m.regs));
    m.writes++;
    if (offset == CQ_SW_RESET) {
        assert(value == 1);
        m.regs[CQ_STATE / 4] = 0x14;
        return;
    }
    if (offset == CQ_STATE) {
        assert(value == 0 || value == 1 || value == 3);
        unsigned previous = m.regs[CQ_STATE / 4] & 3u;
        m.regs[CQ_STATE / 4] = 0x14 | value;
        if (m.scenario == 7 && value == 3) m.regs[CQ_STATE / 4] = 0;
        if (value == 0) {
            m.tx_bytes = m.rx_bytes = m.rx_at = 0;
            m.regs[CQ_OPERATIONAL / 4] = 0;
        } else if (value == 1 && previous == 3) {
            execute_tags();
        }
        return;
    }
    if (offset == CQ_OPERATIONAL) {
        assert((value & ~0x300u) == 0);
        m.regs[offset / 4] &= ~value;
        return;
    }
    if (offset == CQ_OUT_FIFO) {
        assert((m.regs[CQ_STATE / 4] & 3u) == 3 && m.tx_bytes + 4 <= sizeof(m.tx));
        for (unsigned i = 0; i < 4; i++) m.tx[m.tx_bytes++] = (uint8_t)(value >> (8 * i));
        return;
    }
    if (offset == CQ_CLOCK) {
        assert((m.regs[CQ_STATE / 4] & 3u) == 1 && value == 0x000e001c);
    } else {
        assert((m.regs[CQ_STATE / 4] & 3u) == 0);
    }
    m.regs[offset / 4] = value;
}

static int owned(void *context) { assert(context == &m); return m.owned; }
static void barrier(void *context) { assert(context == &m); m.barriers++; }
static void delay(void *context, uint32_t us) {
    assert(context == &m && (us == 10 || us == 100)); m.delays++;
    if (m.scenario != 13)
        m.elapsed_us += m.scenario == 12 ? 150000 : us;
}
static uint64_t now_us(void *context) {
    assert(context == &m);
    return m.scenario == 14 ? m.elapsed_us - m.delays * 1000u : m.elapsed_us;
}

static struct chiron_qup_io
io(void)
{
    return (struct chiron_qup_io){&m, owned, read32, write32, barrier, delay, now_us};
}

static void
admission(void)
{
    struct chiron_qup q;
    setup();
    struct chiron_qup_io ops = io();
    m.owned = 0;
    assert(chiron_qup_open(&q, &ops) == CHIRON_QUP_BLOCKED && !m.reads && !m.writes);
    ops.handoff_ready = NULL;
    assert(chiron_qup_open(&q, &ops) == CHIRON_QUP_BLOCKED && !m.reads);
    assert(chiron_qup_open(&q, NULL) == CHIRON_QUP_BLOCKED && !m.reads);
    setup(); ops = io(); ops.now_us = NULL;
    assert(chiron_qup_open(&q, &ops) == CHIRON_QUP_BLOCKED && !m.reads && !m.writes);
    setup(); ops = io(); m.regs[CQ_STATE / 4] = 4;
    assert(chiron_qup_open(&q, &ops) == CHIRON_QUP_UNSUPPORTED && !m.writes);
    setup(); m.regs[CQ_IO_MODES / 4] |= 3;
    assert(chiron_qup_open(&q, &ops) == CHIRON_QUP_UNSUPPORTED && !m.writes);
    setup(); m.regs[CQ_STATUS / 4] |= 0x100;
    assert(chiron_qup_open(&q, &ops) == CHIRON_QUP_BUS_ERROR && !m.writes);
    setup(); m.regs[CQ_STATUS / 4] = 0;
    assert(chiron_qup_open(&q, &ops) == CHIRON_QUP_BUS_ERROR && !m.writes);
    setup();
    assert(chiron_qup_open(&q, &ops) == 0);
    unsigned before = m.writes, read_before = m.reads;
    m.owned = 0;
    assert(chiron_qup_select_page(&q, 0x20, 0) == CHIRON_QUP_BLOCKED);
    assert(m.writes == before && m.reads == read_before);
}

static void
transactions(void)
{
    struct chiron_qup q;
    struct chiron_qup_io ops = io();
    uint8_t out[80];
    setup();
    assert(chiron_qup_open(&q, &ops) == 0);
    assert(q.input_bytes == 128 && q.output_bytes == 128);
    assert(chiron_qup_select_page(&q, 0x20, 1) == 0);
    assert(chiron_qup_read(&q, 0x20, 0x61, out, 16) == 16);
    assert(out[0] == 1 && out[1] == 0xf4 && out[2] == 1);
    assert(m.transactions == 2 && m.barriers == m.writes);
    assert(chiron_qup_read(&q, 0x20, 0x61, out, 80) == 80);
    unsigned before = m.writes;
    assert(chiron_qup_select_page(&q, 0x38, 1) == CHIRON_QUP_UNSUPPORTED);
    assert(chiron_qup_select_page(&q, 0x20, 5) == CHIRON_QUP_UNSUPPORTED);
    assert(chiron_qup_read(&q, 0x20, 0xff, out, 1) == CHIRON_QUP_UNSUPPORTED);
    assert(chiron_qup_read(&q, 0x20, 0, out, 81) == CHIRON_QUP_UNSUPPORTED);
    assert(chiron_qup_read(&q, 0x20, 0, NULL, 1) == CHIRON_QUP_UNSUPPORTED);
    assert(m.writes == before);
    setup(); m.regs[CQ_IO_MODES / 4] = 0; /* Smallest 32-byte FIFO. */
    assert(chiron_qup_open(&q, &ops) == 0 && q.input_bytes == 32);
    before = m.writes;
    assert(chiron_qup_read(&q, 0x20, 0x60, out, 30) == CHIRON_QUP_UNSUPPORTED);
    assert(m.writes == before); /* No snapshot splitting or fallback. */
}

static void
errors(void)
{
    const int expected[] = {0, CHIRON_QUP_NACK, CHIRON_QUP_FIFO_ERROR,
        CHIRON_QUP_TIMEOUT, CHIRON_QUP_TIMEOUT, CHIRON_QUP_FIFO_ERROR,
        CHIRON_QUP_FIFO_ERROR, CHIRON_QUP_TIMEOUT,
        CHIRON_QUP_BUS_ERROR, CHIRON_QUP_BUS_ERROR,
        CHIRON_QUP_BUS_ERROR, CHIRON_QUP_BUS_ERROR};
    for (int scenario = 1; scenario <= 11; scenario++) {
        setup();
        struct chiron_qup q;
        struct chiron_qup_io ops = io();
        uint8_t out[16];
        memset(out, 0xa5, sizeof(out));
        assert(chiron_qup_open(&q, &ops) == 0);
        assert(chiron_qup_select_page(&q, 0x20, 1) == 0);
        m.scenario = scenario;
        assert(chiron_qup_read(&q, 0x20, 0x61, out, 16) == expected[scenario]);
        for (unsigned i = 0; i < sizeof(out); i++) assert(out[i] == 0xa5);
        assert(!q.ready && m.delays <= 6500);
        unsigned before = m.writes;
        assert(chiron_qup_read(&q, 0x20, 0x61, out, 16) == CHIRON_QUP_BLOCKED);
        assert(m.writes == before && m.barriers == m.writes);
    }
}

static void
layout(void)
{
    struct chiron_dsx_layout result;
    struct chiron_f12_reader reader = {&m, sensor_read, 0};
    setup();
    assert(chiron_f12_validate(&reader, &functions, &result) == 0);
    assert(result.contacts == 2 && result.object_bytes == 8 && result.data1 == 0x161);
    assert(result.max_x == 1000 && result.max_y == 2000 && !m.writes);
    for (int mode = 1; mode <= 28; mode++) {
        setup(); m.layout_case = mode;
        int status = chiron_f12_validate(&reader, &functions, &result);
        if (mode == 17 || mode == 22) assert(status == 0);
        else assert(status < 0);
        assert(!m.writes);
    }
    for (unsigned status = 1; status <= 255; status++) {
        setup(); m.sensor_status = (int)status;
        assert(chiron_f12_validate(&reader, &functions, &result) < 0 && !m.writes);
    }
    setup(); reader.flip = 4;
    assert(chiron_f12_validate(&reader, &functions, &result) < 0);
}

static void
elapsed_timeouts(void)
{
    for (int scenario = 12; scenario <= 14; scenario++) {
        setup();
        struct chiron_qup q;
        struct chiron_qup_io ops = io();
        uint8_t out[16];
        memset(out, 0xa5, sizeof(out));
        assert(chiron_qup_open(&q, &ops) == 0);
        assert(chiron_qup_select_page(&q, 0x20, 1) == 0);
        m.scenario = scenario;
        assert(chiron_qup_read(&q, 0x20, 0x61, out, 16) == CHIRON_QUP_TIMEOUT);
        if (scenario == 12) assert(m.delays == 4 && m.elapsed_us == 600000);
        if (scenario == 13) assert(m.delays == 5000 && m.elapsed_us == 0);
        if (scenario == 14) assert(m.delays == 1);
        assert(!q.ready);
        for (unsigned i = 0; i < sizeof(out); i++) assert(out[i] == 0xa5);
    }
}

static unsigned posted;
static void
post(void *context, uint32_t type, uint32_t finger, uint32_t x, uint32_t y)
{
    assert(context == &posted && finger == 0 && x == 32767 && y == 32767);
    assert(type == (posted == 0 ? CHIRON_DSX_DOWN : CHIRON_DSX_UP));
    posted++;
}

static void
pipeline(void)
{
    struct chiron_native native;
    struct chiron_dsx_bus bus;
    struct chiron_dsx device;
    struct chiron_qup_io ops = io();
    setup(); m.owned = 0;
    assert(chiron_native_bind(&native, &ops, 0, &bus) == CHIRON_QUP_BLOCKED);
    assert(!bus.read && !m.reads && !m.writes);
    setup();
    assert(chiron_native_bind(&native, &ops, 0, &bus) == 0);
    posted = 0;
    chiron_dsx_init(&device, &bus, post, &posted);
    assert(chiron_dsx_start(&device) == 0);
    assert(chiron_dsx_poll(&device) == 0 && posted == 1);
    m.sensor_status = 0x80;
    assert(chiron_dsx_poll(&device) == CHIRON_DSX_UNVERIFIED_LAYOUT);
    assert(posted == 2 && !device.ready && m.barriers == m.writes);
}

static void
bitmap_snapshots(void)
{
    struct chiron_native native;
    struct chiron_dsx_bus bus;
    struct chiron_dsx device;
    struct chiron_qup_io ops = io();
    setup(); m.layout_case = 29;
    assert(chiron_native_bind(&native, &ops, 0, &bus) == 0);
    posted = 0;
    chiron_dsx_init(&device, &bus, post, &posted);
    assert(chiron_dsx_start(&device) == 0 && device.layout.data15 == 0x162);
    assert(chiron_dsx_poll(&device) == 0 && posted == 1);
    m.bitmap = 0; /* Data1 still contains an old finger: Data15 must release it. */
    assert(chiron_dsx_poll(&device) == 0 && posted == 2);
    setup(); m.layout_case = 30;
    assert(chiron_native_bind(&native, &ops, 0, &bus) == 0);
    posted = 0;
    chiron_dsx_init(&device, &bus, post, &posted);
    assert(chiron_dsx_start(&device) == 0);
    assert(chiron_dsx_poll(&device) == CHIRON_DSX_BAD_FRAME && !posted && !device.ready);
    setup(); m.layout_case = 31;
    assert(chiron_native_bind(&native, &ops, 0, &bus) == 0);
    posted = 0;
    chiron_dsx_init(&device, &bus, post, &posted);
    assert(chiron_dsx_start(&device) == 0 && device.layout.contacts == 10);
    assert(chiron_dsx_poll(&device) == 0 && posted == 1);
    setup(); m.layout_case = 32;
    assert(chiron_native_bind(&native, &ops, 0, &bus) == 0);
    posted = 0;
    chiron_dsx_init(&device, &bus, post, &posted);
    assert(chiron_dsx_start(&device) == 0);
    assert(chiron_dsx_poll(&device) == CHIRON_DSX_UNVERIFIED_LAYOUT && !posted && !device.ready);
}

int main(int argc, char **argv)
{
    assert(argc == 2);
    switch (atoi(argv[1])) {
    case 0: admission(); break;
    case 1: transactions(); break;
    case 2: errors(); break;
    case 3: layout(); break;
    case 4: pipeline(); break;
    case 5: bitmap_snapshots(); break;
    case 6: elapsed_timeouts(); break;
    default: assert(0);
    }
    return 0;
}
