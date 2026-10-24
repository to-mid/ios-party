#ifndef CHIRON_NATIVE_H
#define CHIRON_NATIVE_H

#include "chiron_qup.h"
#include "chiron_f12.h"

struct chiron_native {
    struct chiron_qup qup;
    struct chiron_f12_reader reader;
};

/*
 * Called only by a future audited board handoff provider, never a boot flag.
 * flip is a board calibration input; the current XNU factory does not call it.
 */
int chiron_native_bind(struct chiron_native *, const struct chiron_qup_io *,
    uint8_t flip, struct chiron_dsx_bus *);

#endif
