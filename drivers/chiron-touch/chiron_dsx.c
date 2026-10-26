#include "chiron_dsx.h"

static int
dsx_read(struct chiron_dsx *device, uint16_t address, uint8_t *data, size_t size)
{
    uint8_t page = (uint8_t)(address >> 8), reg = (uint8_t)address;
    if (!device->bus.read || !device->bus.select_page)
        return CHIRON_DSX_NO_TRANSPORT;
    if (!size || page >= CHIRON_DSX_PAGES || size > 0xffu - reg)
        return CHIRON_DSX_INVALID;
    /* Select every time: do not assume the controller retained its last page. */
    if (device->bus.select_page(device->bus.context, CHIRON_DSX_ADDRESS, page) != 0)
        return CHIRON_DSX_IO;
    if (device->bus.read(device->bus.context, CHIRON_DSX_ADDRESS, reg, data, size)
        != (int)size)
        return CHIRON_DSX_IO;
    return CHIRON_DSX_OK;
}

void
chiron_dsx_init(struct chiron_dsx *device, const struct chiron_dsx_bus *bus,
    chiron_dsx_post post, void *context)
{
    *device = (struct chiron_dsx){0};
    if (bus)
        device->bus = *bus;
    device->post = post;
    device->post_context = context;
}

void
chiron_dsx_stop(struct chiron_dsx *device)
{
    for (unsigned i = 0; i < CHIRON_DSX_CONTACTS; i++) {
        struct chiron_dsx_contact *c = &device->contact[i];
        if (c->down && device->post)
            device->post(device->post_context, CHIRON_DSX_UP, i, c->x, c->y);
        c->down = 0;
    }
    device->ready = 0;
}

int
chiron_dsx_discover(struct chiron_dsx *device)
{
    chiron_dsx_stop(device);
    device->discovered = 0;
    device->functions = (struct chiron_dsx_functions){0};
    if (!device->post)
        return CHIRON_DSX_INVALID;
    for (unsigned page = 0; page < CHIRON_DSX_PAGES; page++) {
        int terminated = 0;
        /* EDK2's six-byte PDT descriptor, descending from 0xe9. */
        for (int reg = 0xe9; reg >= 5; reg -= 6) {
            uint8_t bytes[6];
            int status = dsx_read(device, (uint16_t)((page << 8) | reg),
                bytes, sizeof(bytes));
            struct chiron_dsx_function *function;
            if (status)
                return status;
            if (bytes[5] == 0) {
                terminated = 1;
                break;
            }
            if (bytes[5] == 0xff)
                return CHIRON_DSX_UNKNOWN;
            if (bytes[5] == 1)
                function = &device->functions.f01;
            else if (bytes[5] == 0x12)
                function = &device->functions.f12;
            else
                continue;
            if (function->number || !bytes[0] || !bytes[2] || !bytes[3] ||
                bytes[0] == 0xff || bytes[2] == 0xff || bytes[3] == 0xff ||
                (bytes[4] & 0xf8))
                return CHIRON_DSX_UNKNOWN;
            *function = (struct chiron_dsx_function){
                (uint16_t)((page << 8) | bytes[0]),
                (uint16_t)((page << 8) | bytes[1]),
                (uint16_t)((page << 8) | bytes[2]),
                (uint16_t)((page << 8) | bytes[3]), bytes[5], 0
            };
        }
        if (!terminated)
            return CHIRON_DSX_UNKNOWN;
    }
    if (!device->functions.f01.number || !device->functions.f12.number)
        return CHIRON_DSX_UNKNOWN;
    device->discovered = 1;
    /* Discovery deliberately does not enable reports or assume their layout. */
    return CHIRON_DSX_OK;
}

static int
same_function(const struct chiron_dsx_function *a,
    const struct chiron_dsx_function *b)
{
    return a->query == b->query && a->command == b->command &&
        a->control == b->control && a->data == b->data &&
        a->number == b->number && a->version == b->version;
}

static int
dsx_validate_layout(struct chiron_dsx *device, struct chiron_dsx_layout *layout)
{
    int status;
    if (!device->bus.validate_layout)
        return CHIRON_DSX_UNVERIFIED_LAYOUT;
    status = device->bus.validate_layout(device->bus.context,
        &device->functions, layout);
    if (status)
        return status;
    if (!same_function(&layout->f12, &device->functions.f12) ||
        layout->object_bytes != CHIRON_DSX_OBJECT_BYTES ||
        !layout->contacts || layout->contacts > CHIRON_DSX_CONTACTS ||
        !layout->max_x || !layout->max_y || layout->flip > 3 ||
        layout->data1 < layout->f12.data ||
        (layout->data1 >> 8) != (layout->f12.data >> 8) ||
        (size_t)layout->contacts * layout->object_bytes > 0xffu - (layout->data1 & 0xff) ||
        (layout->data15_bytes && (layout->data15_bytes != (layout->contacts + 7) / 8 ||
        layout->data15 < layout->f12.data ||
        (layout->data15 >> 8) != (layout->f12.data >> 8) ||
        layout->data15_bytes > 0xffu - (layout->data15 & 0xff))) ||
        (!layout->data15_bytes && layout->data15))
        return CHIRON_DSX_UNVERIFIED_LAYOUT;
    return CHIRON_DSX_OK;
}

int
chiron_dsx_start(struct chiron_dsx *device)
{
    struct chiron_dsx_layout layout = {0};
    int status = chiron_dsx_discover(device);
    if (status)
        return status;
    status = dsx_validate_layout(device, &layout);
    if (status)
        return status;
    device->layout = layout;
    device->ready = 1;
    return CHIRON_DSX_OK;
}

int
chiron_dsx_frame(struct chiron_dsx *device, const uint8_t *frame, size_t size)
{
    struct chiron_dsx_contact next[CHIRON_DSX_CONTACTS] = {{0}};
    const struct chiron_dsx_layout *layout = &device->layout;
    if (!device->ready || !device->post)
        return CHIRON_DSX_UNVERIFIED_LAYOUT;
    if (!frame || size != (size_t)layout->contacts * CHIRON_DSX_OBJECT_BYTES)
        return CHIRON_DSX_BAD_FRAME;
    /* Validate the entire snapshot before changing any state or emitting events. */
    for (unsigned i = 0; i < layout->contacts; i++) {
        const uint8_t *object = frame + i * CHIRON_DSX_OBJECT_BYTES;
        uint32_t x, y;
        if (object[0] == 0)
            continue;
        /* Only an ordinary finger is supported; no palm/glove/pen guesses. */
        if (object[0] != 1)
            return CHIRON_DSX_BAD_FRAME;
        x = (uint32_t)object[1] | (uint32_t)object[2] << 8;
        y = (uint32_t)object[3] | (uint32_t)object[4] << 8;
        if (x > layout->max_x || y > layout->max_y)
            return CHIRON_DSX_BAD_FRAME;
        if (layout->flip & 1)
            x = layout->max_x - x;
        if (layout->flip & 2)
            y = layout->max_y - y;
        next[i].x = x * 65535u / layout->max_x;
        next[i].y = y * 65535u / layout->max_y;
        next[i].down = 1;
    }
    /*
     * Releases first: avoid a transient extra finger during slot turnover.
     * The existing queue counts five held fingers as a loader-return gesture.
     */
    for (unsigned i = 0; i < CHIRON_DSX_CONTACTS; i++) {
        struct chiron_dsx_contact *old = &device->contact[i];
        if (old->down && !next[i].down)
            device->post(device->post_context, CHIRON_DSX_UP, i, old->x, old->y);
    }
    for (unsigned i = 0; i < CHIRON_DSX_CONTACTS; i++) {
        struct chiron_dsx_contact *old = &device->contact[i];
        if (next[i].down) {
            if (!old->down)
                device->post(device->post_context, CHIRON_DSX_DOWN, i,
                    next[i].x, next[i].y);
            else if (next[i].x != old->x || next[i].y != old->y)
                device->post(device->post_context, CHIRON_DSX_MOVE, i,
                    next[i].x, next[i].y);
        }
        *old = next[i];
    }
    return CHIRON_DSX_OK;
}

int
chiron_dsx_poll(struct chiron_dsx *device)
{
    uint8_t frame[CHIRON_DSX_CONTACTS * CHIRON_DSX_OBJECT_BYTES] = {0};
    uint8_t raw[sizeof(frame)] = {0}, f01_status = 0;
    uint8_t before[2] = {0}, after[2] = {0};
    struct chiron_dsx_layout now = {0};
    unsigned observed = 0;
    size_t frame_bytes = 0;
    int status;
    if (!device->ready)
        return CHIRON_DSX_UNVERIFIED_LAYOUT;
    status = dsx_validate_layout(device, &now);
    if (!status && (now.data1 != device->layout.data1 ||
        now.max_x != device->layout.max_x || now.max_y != device->layout.max_y ||
        now.contacts != device->layout.contacts || now.flip != device->layout.flip ||
        now.data15 != device->layout.data15 || now.data15_bytes != device->layout.data15_bytes))
        status = CHIRON_DSX_UNVERIFIED_LAYOUT;
    if (!status)
        frame_bytes = (size_t)now.contacts * CHIRON_DSX_OBJECT_BYTES;
    if (!status && now.data15_bytes) {
        status = dsx_read(device, now.data15, before, now.data15_bytes);
        if (!status) {
            observed |= CHIRON_DSX_OBS_BEFORE;
            unsigned mask = (unsigned)before[0] | (unsigned)before[1] << 8;
            if (mask >> now.contacts)
                status = CHIRON_DSX_BAD_FRAME;
            /* Vendor reads only through the highest present object, in one transfer. */
            unsigned objects = 0;
            for (unsigned remaining = mask; remaining; remaining >>= 1)
                objects++;
            frame_bytes = (size_t)objects * CHIRON_DSX_OBJECT_BYTES;
        }
    }
    if (!status && frame_bytes) {
        status = dsx_read(device, device->layout.data1, frame, frame_bytes);
        if (!status) {
            observed |= CHIRON_DSX_OBS_FRAME;
            if (device->observer)
                for (size_t i = 0; i < frame_bytes; i++)
                    raw[i] = frame[i];
        }
    }
    if (!status && now.data15_bytes) {
        status = dsx_read(device, now.data15, after, now.data15_bytes);
        if (!status)
            observed |= CHIRON_DSX_OBS_AFTER;
        unsigned mask = (unsigned)before[0] | (unsigned)before[1] << 8;
        if (!status && (before[0] != after[0] || before[1] != after[1] ||
            mask >> now.contacts))
            status = CHIRON_DSX_BAD_FRAME;
        if (!status)
            for (unsigned i = 0; i < now.contacts; i++)
                if (!(mask & (1u << i)))
                    for (unsigned j = 0; j < CHIRON_DSX_OBJECT_BYTES; j++)
                        frame[i * CHIRON_DSX_OBJECT_BYTES + j] = 0;
    }
    if (!status) {
        status = dsx_read(device, device->functions.f01.data, &f01_status, 1);
        if (!status)
            observed |= CHIRON_DSX_OBS_F01;
        if (!status && f01_status != 0)
            status = CHIRON_DSX_UNVERIFIED_LAYOUT;
    }
    if (!status)
        status = chiron_dsx_frame(device, frame,
            (size_t)device->layout.contacts * CHIRON_DSX_OBJECT_BYTES);
    if (status)
        chiron_dsx_stop(device);
    if (device->observer)
        device->observer(device, raw, (observed & CHIRON_DSX_OBS_FRAME) ?
            frame_bytes : 0,
            (uint16_t)(before[0] | (unsigned)before[1] << 8),
            (uint16_t)(after[0] | (unsigned)after[1] << 8), f01_status, observed, status);
    return status;
}
