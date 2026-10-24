#!/usr/bin/env python3
"""Opt-in DSX groundwork overlay for pinned HackiOS/XNU scratch source trees."""

import argparse
import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DRIVER = ROOT / "drivers/chiron-touch"
MANIFEST = HERE / "chiron_touch_sources.json"
COPIES = {
    "redwood_goodix.c": "chiron_xnu.c",
    "chiron_dsx.h": "chiron_dsx.h",
    "chiron_touch_core.inc": "chiron_dsx.c",
    "chiron_qup.h": "chiron_qup.h",
    "chiron_touch_qup.inc": "chiron_qup.c",
    "chiron_f12.h": "chiron_f12.h",
    "chiron_touch_f12.inc": "chiron_f12.c",
    "chiron_native.h": "chiron_native.h",
    "chiron_touch_native.inc": "chiron_native.c",
    "chiron_board.h": "chiron_board.h",
    "chiron_touch_board.inc": "chiron_board.c",
    "chiron_touch_abi.h": "chiron_touch_abi.h",
    "chiron_clock.h": "chiron_clock.h",
    "chiron_touch_clock.inc": "chiron_clock.c",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _overlay(root, prefix, manifest):
    root = Path(root)
    originals = manifest["pinned_hackios_files"]
    # Validate every consumed upstream context before writing anything.
    for name in ("redwood_goodix.c", "redwood_input.c"):
        path = root / prefix / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"missing regular pinned touch source: {path}")
        if digest(path.read_bytes()) != originals[name]:
            raise ValueError(f"pinned touch source changed: {path}; use a fresh tree")
    inputs = {name: (DRIVER / name).read_bytes() for name in COPIES.values()}
    for name, data in inputs.items():
        if digest(data) != manifest["overlay_files"][name]:
            raise ValueError(f"touch overlay manifest mismatch: {name}")
    edits = {str(Path(prefix) / name): inputs[origin] for name, origin in COPIES.items()}
    for relative in edits:
        path = root / relative
        if path.is_symlink() or (path.exists() and path.name != "redwood_goodix.c"):
            raise ValueError(f"refusing existing touch overlay destination: {path}")
    before = digest((root / prefix / "redwood_goodix.c").read_bytes())
    for relative, data in edits.items():
        (root / relative).write_bytes(data)
    return {
        "schema": "chiron-touch-overlay-v1",
        "kind": "source" if str(prefix).startswith("xnu/") else "kernel",
        "status": "experimental-clock-bringup-runtime-unqualified",
        "boot_arg": "chiron_touch=1",
        "read_only_probe_boot_arg": "chiron_touch_probe=1",
        "legacy_async_read_only_boot_arg": "chiron_touch=2",
        "photo_probe_boot_arg": "chiron_touch_probe=1",
        "clock_probe_boot_arg": "chiron_touch_clock_probe=1",
        "clock_prepare_implemented": True,
        "pinmux_prepare_implemented": True,
        "pinmux_probe_boot_arg": "chiron_touch_pinmux_probe=1",
        "clock_probe_writes": "BLSP1-AHB-vote-and-QUP5-XO-clock-only",
        "kernel_rebuild_required": True,
        "hardware_observed": False,
        "hardware_touch_available": False,
        "native_i2c_transport_implemented": True,
        "f12_layout_validator_implemented": True,
        "native_i2c_hardware_enabled": False,
        "board_probe_implemented": True,
        "loader_overlay_required_for_native": True,
        "native_i2c_can_enable_conditionally": True,
        "runtime_enable_policy": "owned-clock-prepare-then-retained-pins-and-QUP",
        "source_commit": manifest["hackios_commit"],
        "protocol_source_commit": manifest["native_upstream"]["commit"],
        "replaced_source_sha256": before,
        "result": {relative: digest(data) for relative, data in edits.items()},
        "manifest_sha256": digest(MANIFEST.read_bytes()),
    }


def apply(source=None, *, tree=None):
    """Apply to a HackiOS repo (source) OR an already patched XNU tree (tree)."""
    if (source is None) == (tree is None):
        raise ValueError("provide exactly one of source or tree")
    manifest = json.loads(MANIFEST.read_text())
    if tree is not None:
        tree = Path(tree)
        make = tree / "makedefs/MakeInc.def"
        if not make.is_file() or "-DCHIRON_PORT=1" not in make.read_text():
            raise ValueError("apply the base CHIRON_PORT kernel overlay first")
        return _overlay(tree, "bsd/dev", manifest)
    return _overlay(source, "xnu/files/bsd/dev", manifest)


def kernel_overlay(tree):
    return apply(tree=tree)


def source_overlay(source):
    return apply(source=source)


def loader_overlay(source):
    """Add identity-scoped ownership metadata; no EBS or hardware reconfiguration."""
    source = Path(source)
    path = source / "xnuloader/loader.c"
    text = path.read_text()
    if 'static const struct platform platform_chiron = {' not in text:
        raise ValueError("apply the base chiron loader overlay first")
    if "chiron_touch_loader.inc" in text:
        raise ValueError("touch loader overlay already applied")

    def replace(old, new):
        nonlocal text
        if text.count(old) != 1:
            raise ValueError(f"touch loader context changed: {old[:80]}")
        text = text.replace(old, new, 1)

    replace("static int64_t\nrun_on_other_cpu(",
            '#include "chiron_touch_loader.inc"\n\nstatic int64_t\nrun_on_other_cpu(')
    replace("\tuint64_t cntfrq = READ_SYSREG(CNTFRQ_EL0);",
            "\tuint64_t cntfrq = READ_SYSREG(CNTFRQ_EL0);\n"
            "\tuint32_t chiron_touch_handoff[4] = {CHIRON_TOUCH_HANDOFF_VERSION, 0, 0,\n"
            "\t    CHIRON_TOUCH_FIRMWARE_RESIDENT};\n"
            "\tif (plat == &platform_chiron) {\n"
            "\t\tchiron_touch_handoff[1] = chiron_touch_loader_owner(ba->commandLine,\n"
            "\t\t    READ_SYSREG(MPIDR_EL1) & 0xff00ffffffULL, kernel_mpidr);\n"
            "\t\tif (chiron_touch_handoff[1]) chiron_touch_handoff[2] = CHIRON_TOUCH_OTHER_CPUS_OFF;\n"
            "\t}\n")
    replace('\tdt_begin(&w, "chosen");',
            '\tdt_begin(&w, "chosen");\n'
            '\tif (plat == &platform_chiron)\n'
            '\t\tdt_prop(&w, "chiron-touch-handoff", chiron_touch_handoff, sizeof(chiron_touch_handoff));')
    replace("\t\t\tjump_to_kernel(entry_va + slide, ba_pa);",
            "\t\t\tif (plat == &platform_chiron && chiron_touch_handoff[1] &&\n"
            "\t\t\t    chiron_touch_loader_owner(ba->commandLine,\n"
            "\t\t\t        READ_SYSREG(MPIDR_EL1) & 0xff00ffffffULL, kernel_mpidr) !=\n"
            "\t\t\t    CHIRON_TOUCH_DIRECT_BOOT_CPU) {\n"
            '\t\t\t\tprint("chiron touch: CPU ownership changed before direct handoff; refusing boot\\n");\n'
            "\t\t\t\treturn 0;\n\t\t\t}\n"
            "\t\t\tjump_to_kernel(entry_va + slide, ba_pa);")
    manifest = json.loads(MANIFEST.read_text())
    helper = (HERE / "chiron_touch_loader.inc").read_bytes()
    abi = (DRIVER / "chiron_touch_abi.h").read_bytes()
    if digest(helper) != manifest["loader_files"]["chiron_touch_loader.inc"] or \
            digest(abi) != manifest["overlay_files"]["chiron_touch_abi.h"]:
        raise ValueError("touch loader manifest mismatch")
    edits = {"xnuloader/loader.c": text.encode(),
             "xnuloader/chiron_touch_loader.inc": helper,
             "xnuloader/chiron_touch_abi.h": abi}
    for relative in edits:
        target = source / relative
        if target.is_symlink() or (target.exists() and target != path):
            raise ValueError(f"refusing existing touch loader destination: {target}")
    for relative, data in edits.items():
        (source / relative).write_bytes(data)
    return {"schema": "chiron-touch-overlay-v1", "kind": "loader",
            "board_probe_implemented": True, "photo_probe_boot_arg": "chiron_touch_probe=1",
            "clock_probe_boot_arg": "chiron_touch_clock_probe=1",
            "firmware_mode": "resident-not-exited",
            "ownership_assumption": "same-boot-cpu-direct-entry-and-seven-other-affinities-OFF",
            "hardware_observed": False, "runtime_qualified": False,
            "result": {name: digest(data) for name, data in edits.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("source", "kernel", "loader"), nargs="?", default="kernel")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = {"source": source_overlay, "kernel": kernel_overlay,
              "loader": loader_overlay}[args.kind](args.source)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2) + "\n")
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
