/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * Read-only subset of synaptics_dsx_force/synaptics_dsx_core.c at the pinned
 * MSM8998 commit. Original driver Copyright (C) 2012-2015 Synaptics Incorporated,
 * Copyright (C) 2018 XiaoMi, Inc. Register parsing is bounds checked here.
 */
#include "chiron_f12.h"

static int
cf_read(struct chiron_f12_reader *reader, uint16_t base, unsigned offset,
    uint8_t *out, size_t length)
{
    if (!reader->read || (base >> 8) >= CHIRON_DSX_PAGES ||
        offset >= 0xffu - (base & 0xffu) ||
        length > 0xffu - (base & 0xffu) - offset)
        return CHIRON_DSX_UNVERIFIED_LAYOUT;
    if (reader->read(reader->context, (uint16_t)(base + offset), out, length) != (int)length)
        return CHIRON_DSX_IO;
    return 0;
}

static unsigned
cf_present(const uint8_t *query5, unsigned control)
{
    return (query5[1 + control / 8] >> (control % 8)) & 1u;
}

static unsigned
cf_offset(const uint8_t *query5, unsigned control)
{
    unsigned result = 0;
    for (unsigned i = 0; i < control; i++)
        result += cf_present(query5, i);
    return result;
}

static int
cf_control23_size(const uint8_t *query5, const uint8_t *structure,
    size_t length, unsigned *size)
{
    size_t at = 0;
    /* Query6: register-size header, then 7-bit subpacket presence groups. */
    for (unsigned reg = 0; reg <= 23; reg++) {
        if (!cf_present(query5, reg))
            continue;
        if (at >= length)
            return CHIRON_DSX_UNVERIFIED_LAYOUT;
        uint8_t bytes = structure[at++];
        if (!bytes) {
            if (length - at < 2 || reg == 23)
                return CHIRON_DSX_UNVERIFIED_LAYOUT;
            at += 2; /* upstream's extended register-size header */
        }
        unsigned groups = 0, packets = 0;
        uint8_t bitmap;
        do {
            if (at >= length)
                return CHIRON_DSX_UNVERIFIED_LAYOUT;
            bitmap = structure[at++];
            if (reg == 23) {
                if (groups || (bitmap & 0x60u))
                    return CHIRON_DSX_UNVERIFIED_LAYOUT;
                packets = bitmap & 0x7fu;
            }
            groups++;
        } while (bitmap & 0x80u);
        if (reg == 23) {
            if ((packets & 3u) != 3u)
                return CHIRON_DSX_UNVERIFIED_LAYOUT;
            *size = 2 + ((packets >> 2) & 1u) + ((packets >> 3) & 1u) +
                ((packets >> 4) & 1u);
            if (bytes != *size)
                return CHIRON_DSX_UNVERIFIED_LAYOUT;
            return 0;
        }
    }
    return CHIRON_DSX_UNVERIFIED_LAYOUT;
}

int
chiron_f12_validate(void *context, const struct chiron_dsx_functions *functions,
    struct chiron_dsx_layout *layout)
{
    struct chiron_f12_reader *reader = context;
    uint8_t f01[21], build_id[3], status, device_control, q5_size, q8_size;
    uint8_t q5[9] = {0}, q6[64], q8[5] = {0};
    uint8_t control8[14], control23[5] = {0}, control28, control20[3];
    unsigned control23_size;
    int result;
    *layout = (struct chiron_dsx_layout){0};
    if (!reader || !functions || functions->f01.number != 1 ||
        functions->f12.number != 0x12 || functions->f01.version ||
        functions->f12.version || reader->flip > 3)
        return CHIRON_DSX_UNVERIFIED_LAYOUT;
#define CF_READ(base, offset, data, length) do { \
    result = cf_read(reader, base, offset, data, length); \
    if (result) return result; \
} while (0)
    CF_READ(functions->f01.data, 0, &status, 1);
    CF_READ(functions->f01.control, 0, &device_control, 1);
    if (status != 0 || (device_control & 0x83u) != 0x80u)
        return CHIRON_DSX_UNVERIFIED_LAYOUT;
    CF_READ(functions->f01.query, 0, f01, sizeof(f01));
    /* Upstream reads the build ID separately from the standard query burst. */
    CF_READ(functions->f01.query, 18, build_id, sizeof(build_id));
    uint32_t build = (uint32_t)build_id[0] | (uint32_t)build_id[1] << 8 |
        (uint32_t)build_id[2] << 16;
    if (f01[0] != 1 || build != CHIRON_DSX_MEASURED_BUILD)
        return CHIRON_DSX_UNVERIFIED_LAYOUT;
    CF_READ(functions->f12.query, 4, &q5_size, 1);
    if (q5_size < 5 || q5_size > sizeof(q5))
        return CHIRON_DSX_UNVERIFIED_LAYOUT;
    CF_READ(functions->f12.query, 5, q5, q5_size);
    if (!cf_present(q5, 8) || !cf_present(q5, 20) ||
        !cf_present(q5, 23) || !cf_present(q5, 28))
        return CHIRON_DSX_UNVERIFIED_LAYOUT;
    if (!q5[0] || q5[0] > sizeof(q6))
        return CHIRON_DSX_UNVERIFIED_LAYOUT;
    CF_READ(functions->f12.query, 6, q6, q5[0]);
    result = cf_control23_size(q5, q6, q5[0], &control23_size);
    if (result)
        return result;
    CF_READ(functions->f12.query, 7, &q8_size, 1);
    if (q8_size < 2 || q8_size > sizeof(q8))
        return CHIRON_DSX_UNVERIFIED_LAYOUT;
    CF_READ(functions->f12.query, 8, q8, q8_size);
    if (!(q8[1] & 2u)) /* Data1 must exist; Data0 determines its offset. */
        return CHIRON_DSX_UNVERIFIED_LAYOUT;
    CF_READ(functions->f12.control, cf_offset(q5, 8), control8, sizeof(control8));
    CF_READ(functions->f12.control, cf_offset(q5, 20), control20, sizeof(control20));
    CF_READ(functions->f12.control, cf_offset(q5, 23), control23, control23_size);
    CF_READ(functions->f12.control, cf_offset(q5, 28), &control28, 1);
    /*
     * Vendor enables TYPE/X/Y/Z/WX/WY by writing 0xff to Ctrl28. We only
     * accept it already enabled. Do not rewrite controls to make a device fit.
     * Optional Ctrl23 subpackets are described by Query6. Reject mappings
     * that relabel another object class as a finger.
     */
    if (control28 != 0xff || control20[2] != 0 || !(control23[0] & 1u) ||
        !control23[1] || control23[1] > CHIRON_DSX_CONTACTS)
        return CHIRON_DSX_UNVERIFIED_LAYOUT;
    for (unsigned i = 2; i < control23_size; i++)
        if (control23[i] != 0)
            return CHIRON_DSX_UNVERIFIED_LAYOUT;
    uint16_t max_x = (uint16_t)(control8[0] | (uint16_t)control8[1] << 8);
    uint16_t max_y = (uint16_t)(control8[2] | (uint16_t)control8[3] << 8);
    unsigned offset = q8[1] & 1u;
    if (!max_x || !max_y ||
        offset + (size_t)control23[1] * 8 > 0xffu - (functions->f12.data & 0xffu))
        return CHIRON_DSX_UNVERIFIED_LAYOUT;
    /* Status/ready must also remain valid after the control/query reads. */
    CF_READ(functions->f01.data, 0, &status, 1);
    if (status != 0)
        return CHIRON_DSX_UNVERIFIED_LAYOUT;
    *layout = (struct chiron_dsx_layout){
        .f12 = functions->f12, .data1 = (uint16_t)(functions->f12.data + offset),
        .max_x = max_x, .max_y = max_y, .object_bytes = 8,
        .contacts = control23[1], .flip = reader->flip
    };
    if (q8_size >= 3 && (q8[2] & 0x80u)) {
        unsigned data15_offset = 0;
        for (unsigned i = 0; i < 15; i++)
            data15_offset += (q8[1 + i / 8] >> (i % 8)) & 1u;
        unsigned bytes = (control23[1] + 7) / 8;
        if (data15_offset + bytes > 0xffu - (functions->f12.data & 0xffu))
            return CHIRON_DSX_UNVERIFIED_LAYOUT;
        layout->data15 = (uint16_t)(functions->f12.data + data15_offset);
        layout->data15_bytes = (uint8_t)bytes;
    }
#undef CF_READ
    return 0;
}
