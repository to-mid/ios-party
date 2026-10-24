/* Synthetic protocol fixtures, never a capture or a board-layout whitelist. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "chiron_dsx.h"

static uint8_t registers[CHIRON_DSX_PAGES][256], page;
static int reads, selectors, short_read, failed_select, validator_result;
static int layout_case;
static struct {
    uint32_t type, finger, x, y;
} events[1000];
static unsigned count;

static int
select_page(void *context, uint8_t slave, uint8_t value)
{
    assert(context == registers && slave == 0x20 && value < CHIRON_DSX_PAGES);
    selectors++;
    page = value;
    return failed_select ? -1 : 0;
}

static int
read_reg(void *context, uint8_t slave, uint8_t reg, uint8_t *out, size_t size)
{
    assert(context == registers && slave == 0x20 && size <= 0xffu - reg);
    reads++;
    memcpy(out, &registers[page][reg], size);
    return short_read ? (int)size - 1 : (int)size;
}

static int
validate(void *context, const struct chiron_dsx_functions *functions,
    struct chiron_dsx_layout *layout)
{
    assert(context == registers && functions->f12.number == 0x12);
    *layout = (struct chiron_dsx_layout){
        .f12 = functions->f12, .data1 = functions->f12.data + 1,
        .max_x = 1000, .max_y = 2000, .contacts = 3, .object_bytes = 8
    };
    switch (layout_case) {
    case 1: layout->contacts = 0; break;
    case 2: layout->contacts = 11; break;
    case 3: layout->object_bytes = 7; break;
    case 4: layout->max_x = 0; break;
    case 5: layout->max_y = 0; break;
    case 6: layout->data1 = 0x1f0; break;
    case 7: layout->data1 = 0x151; layout->f12.query++; break;
    case 8: layout->data1 = 0x251; break;
    case 9: layout->data1 = functions->f12.data - 1; break;
    case 10: layout->flip = 4; break;
    case 11: layout->flip = 3; break;
    case 12: layout->contacts = 10; layout->max_x = 65535; layout->max_y = 65535; break;
    }
    return validator_result;
}

static void
post(void *context, uint32_t type, uint32_t finger, uint32_t x, uint32_t y)
{
    assert(context == events && count < 1000 && x <= 65535 && y <= 65535);
    events[count].type = type;
    events[count].finger = finger;
    events[count].x = x;
    events[count].y = y;
    count++;
}

static void
setup(struct chiron_dsx *device)
{
    static const uint8_t f01[] = {0x10, 0x20, 0x30, 0x40, 1, 1};
    static const uint8_t f12[] = {0x10, 0x20, 0x30, 0x50, 1, 0x12};
    struct chiron_dsx_bus bus = {registers, select_page, read_reg, validate};
    memset(registers, 0, sizeof(registers));
    memcpy(registers[0] + 0xe9, f01, 6);
    memcpy(registers[1] + 0xe9, f12, 6);
    count = 0;
    reads = selectors = short_read = failed_select = validator_result = layout_case = 0;
    chiron_dsx_init(device, &bus, post, events);
}

static void
object(uint8_t *frame, unsigned slot, uint32_t status, uint32_t x, uint32_t y)
{
    uint8_t *out = frame + slot * 8;
    out[0] = (uint8_t)status;
    out[1] = (uint8_t)x;
    out[2] = (uint8_t)(x >> 8);
    out[3] = (uint8_t)y;
    out[4] = (uint8_t)(y >> 8);
}

static void
event(unsigned at, uint32_t type, uint32_t finger, uint32_t x, uint32_t y)
{
    assert(at < count);
    assert(events[at].type == type && events[at].finger == finger);
    assert(events[at].x == x && events[at].y == y);
}

static void
discovery(void)
{
    struct chiron_dsx d;
    setup(&d);
    assert(chiron_dsx_discover(&d) == 0 && d.discovered && !d.ready);
    assert(d.functions.f01.query == 0x10 && d.functions.f12.query == 0x110);
    assert(d.functions.f12.data == 0x150 && selectors == reads && reads <= 5 * 39);
    assert(chiron_dsx_start(&d) == 0 && d.ready);
    setup(&d); d.bus.validate_layout = NULL;
    assert(chiron_dsx_start(&d) == CHIRON_DSX_UNVERIFIED_LAYOUT);
    int before = reads;
    assert(chiron_dsx_poll(&d) == CHIRON_DSX_UNVERIFIED_LAYOUT && reads == before);
    setup(&d); d.bus.read = NULL;
    assert(chiron_dsx_start(&d) == CHIRON_DSX_NO_TRANSPORT && !reads && !selectors);
    setup(&d); d.bus.select_page = NULL;
    assert(chiron_dsx_start(&d) == CHIRON_DSX_NO_TRANSPORT && !reads);
    setup(&d); short_read = 1;
    assert(chiron_dsx_start(&d) == CHIRON_DSX_IO && !d.ready);
    setup(&d); failed_select = 1;
    assert(chiron_dsx_start(&d) == CHIRON_DSX_IO && !reads);
    setup(&d); memset(registers[1] + 0xe9, 0, 6);
    assert(chiron_dsx_start(&d) == CHIRON_DSX_UNKNOWN);
    setup(&d); memset(registers[0] + 0xe9, 0, 6);
    assert(chiron_dsx_start(&d) == CHIRON_DSX_UNKNOWN);
    setup(&d); memset(registers[2], 0xff, 256);
    assert(chiron_dsx_start(&d) == CHIRON_DSX_UNKNOWN);
    setup(&d); memcpy(registers[1] + 0xe3, registers[1] + 0xe9, 6);
    assert(chiron_dsx_start(&d) == CHIRON_DSX_UNKNOWN);
    setup(&d); registers[1][0xed] = 0x21;
    assert(chiron_dsx_start(&d) == CHIRON_DSX_UNKNOWN);
    setup(&d); registers[1][0xe9] = 0xff;
    assert(chiron_dsx_start(&d) == CHIRON_DSX_UNKNOWN);
    setup(&d);
    for (int reg = 0xe9; reg >= 5; reg -= 6) registers[2][reg + 5] = 0x54;
    assert(chiron_dsx_start(&d) == CHIRON_DSX_UNKNOWN);
    setup(&d); d.post = NULL;
    assert(chiron_dsx_start(&d) == CHIRON_DSX_INVALID && !reads);
    setup(&d); validator_result = CHIRON_DSX_UNVERIFIED_LAYOUT;
    assert(chiron_dsx_start(&d) == CHIRON_DSX_UNVERIFIED_LAYOUT && !d.ready);
    for (int i = 1; i <= 10; i++) {
        setup(&d); layout_case = i;
        assert(chiron_dsx_start(&d) == CHIRON_DSX_UNVERIFIED_LAYOUT && !d.ready);
    }
    /* Unknown functions do not shift the six-byte PDT walk. */
    setup(&d);
    memcpy(registers[1] + 0xe3, registers[1] + 0xe9, 6);
    registers[1][0xee] = 0x54;
    assert(chiron_dsx_start(&d) == 0);
}

static void
contacts(void)
{
    struct chiron_dsx d;
    uint8_t frame[24] = {0};
    setup(&d);
    assert(chiron_dsx_start(&d) == 0);
    object(frame, 0, 1, 0, 0);
    object(frame, 2, 1, 1000, 2000);
    assert(chiron_dsx_frame(&d, frame, sizeof(frame)) == 0 && count == 2);
    event(0, CHIRON_DSX_DOWN, 0, 0, 0);
    event(1, CHIRON_DSX_DOWN, 2, 65535, 65535);
    assert(chiron_dsx_frame(&d, frame, sizeof(frame)) == 0 && count == 2);
    object(frame, 2, 1, 500, 1000);
    assert(chiron_dsx_frame(&d, frame, sizeof(frame)) == 0 && count == 3);
    event(2, CHIRON_DSX_MOVE, 2, 32767, 32767);
    object(frame, 0, 0, 65535, 65535); /* UP uses last position, ignores stale payload. */
    object(frame, 1, 1, 250, 500);
    assert(chiron_dsx_frame(&d, frame, sizeof(frame)) == 0 && count == 5);
    event(3, CHIRON_DSX_UP, 0, 0, 0);
    event(4, CHIRON_DSX_DOWN, 1, 16383, 16383);
    memset(frame, 0, sizeof(frame));
    assert(chiron_dsx_frame(&d, frame, sizeof(frame)) == 0 && count == 7);
    event(5, CHIRON_DSX_UP, 1, 16383, 16383);
    event(6, CHIRON_DSX_UP, 2, 32767, 32767);
    chiron_dsx_stop(&d); chiron_dsx_stop(&d);
    assert(count == 7 && !d.ready);
    setup(&d); layout_case = 11;
    assert(chiron_dsx_start(&d) == 0);
    object(frame, 0, 1, 0, 2000);
    assert(chiron_dsx_frame(&d, frame, sizeof(frame)) == 0);
    event(0, CHIRON_DSX_DOWN, 0, 65535, 0);
    /* Maximum inclusive 16-bit coordinates do not overflow normalization. */
    setup(&d); layout_case = 12;
    uint8_t all[80] = {0};
    assert(chiron_dsx_start(&d) == 0);
    for (unsigned i = 0; i < 10; i++) object(all, i, 1, 65535, 65535);
    assert(chiron_dsx_frame(&d, all, sizeof(all)) == 0 && count == 10);
    for (unsigned i = 0; i < 10; i++) event(i, CHIRON_DSX_DOWN, i, 65535, 65535);
    chiron_dsx_stop(&d);
    assert(count == 20);
}

static void
invalid_frames(void)
{
    struct chiron_dsx d;
    uint8_t frame[24] = {0};
    setup(&d);
    assert(chiron_dsx_start(&d) == 0);
    object(frame, 0, 1, 20, 40);
    assert(chiron_dsx_frame(&d, frame, 24) == 0 && count == 1);
    for (unsigned length = 0; length < 90; length++) {
        if (length == 24) continue;
        assert(chiron_dsx_frame(&d, frame, length) == CHIRON_DSX_BAD_FRAME);
    }
    assert(chiron_dsx_frame(&d, NULL, 24) == CHIRON_DSX_BAD_FRAME);
    object(frame, 0, 1, 500, 1000);
    for (unsigned status = 2; status <= 255; status++) {
        object(frame, 2, status, 50, 50);
        assert(chiron_dsx_frame(&d, frame, 24) == CHIRON_DSX_BAD_FRAME);
        assert(count == 1 && d.contact[0].x == 1310);
    }
    object(frame, 2, 1, 1001, 10);
    assert(chiron_dsx_frame(&d, frame, 24) == CHIRON_DSX_BAD_FRAME && count == 1);
    object(frame, 2, 1, 1000, 2001);
    assert(chiron_dsx_frame(&d, frame, 24) == CHIRON_DSX_BAD_FRAME && count == 1);
    chiron_dsx_stop(&d);
    event(1, CHIRON_DSX_UP, 0, 1310, 1310);
}

static void
polling(void)
{
    struct chiron_dsx d;
    setup(&d);
    assert(chiron_dsx_start(&d) == 0);
    object(registers[1] + 0x51, 0, 1, 500, 1000);
    object(registers[1] + 0x51, 1, 1, 1000, 2000);
    assert(chiron_dsx_poll(&d) == 0 && count == 2);
    short_read = 1;
    assert(chiron_dsx_poll(&d) == CHIRON_DSX_IO && count == 4 && !d.ready);
    event(2, CHIRON_DSX_UP, 0, 32767, 32767);
    event(3, CHIRON_DSX_UP, 1, 65535, 65535);
    int before = reads;
    assert(chiron_dsx_poll(&d) == CHIRON_DSX_UNVERIFIED_LAYOUT && reads == before && count == 4);
    short_read = 0;
    assert(chiron_dsx_start(&d) == 0 && chiron_dsx_poll(&d) == 0 && count == 6);
    object(registers[1] + 0x51, 2, 3, 20, 20); /* Unsupported object releases all on poll. */
    assert(chiron_dsx_poll(&d) == CHIRON_DSX_BAD_FRAME && count == 8 && !d.ready);
    chiron_dsx_stop(&d);
    assert(count == 8);
    setup(&d);
    assert(chiron_dsx_start(&d) == 0 && chiron_dsx_poll(&d) == 0);
    before = reads;
    page = 4; /* External page changes do not redirect a subsequent read. */
    assert(chiron_dsx_poll(&d) == 0 && page == 0 && reads == before + 2);
    object(registers[1] + 0x51, 2, 1, 100, 200);
    assert(chiron_dsx_poll(&d) == 0 && count == 1);
    /* Reprobe releases contacts even if discovering the replacement fails. */
    memset(registers[1] + 0xe9, 0, 6);
    assert(chiron_dsx_start(&d) == CHIRON_DSX_UNKNOWN && count == 2 && !d.ready);
    setup(&d);
    assert(chiron_dsx_start(&d) == 0);
    object(registers[1] + 0x51, 0, 1, 1000, 2000);
    assert(chiron_dsx_poll(&d) == 0 && count == 1);
    layout_case = 11; /* A valid but changed orientation is still a new layout. */
    before = reads;
    assert(chiron_dsx_poll(&d) == CHIRON_DSX_UNVERIFIED_LAYOUT && reads == before);
    event(1, CHIRON_DSX_UP, 0, 65535, 65535);
    assert(!d.ready);
    setup(&d);
    assert(chiron_dsx_start(&d) == 0);
    object(registers[1] + 0x51, 0, 1, 500, 1000);
    assert(chiron_dsx_poll(&d) == 0);
    validator_result = CHIRON_DSX_UNVERIFIED_LAYOUT;
    assert(chiron_dsx_poll(&d) == CHIRON_DSX_UNVERIFIED_LAYOUT && count == 2);
}

int main(int argc, char **argv)
{
    assert(argc == 2);
    switch (atoi(argv[1])) {
    case 0: discovery(); break;
    case 1: contacts(); break;
    case 2: invalid_frames(); break;
    case 3: polling(); break;
    default: assert(0);
    }
    return 0;
}
