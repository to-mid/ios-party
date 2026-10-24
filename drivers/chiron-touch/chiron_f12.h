#ifndef CHIRON_F12_H
#define CHIRON_F12_H

#include "chiron_dsx.h"

#define CHIRON_DSX_MEASURED_BUILD 2676394u

struct chiron_f12_reader {
    void *context;
    /* Exact absolute-register read, returns byte count or a negative error. */
    int (*read)(void *, uint16_t, uint8_t *, size_t);
    uint8_t flip;
};

/* Read-only validation of a restricted profile of the pinned vendor F12 ABI. */
int chiron_f12_validate(void *, const struct chiron_dsx_functions *,
    struct chiron_dsx_layout *);

#endif
