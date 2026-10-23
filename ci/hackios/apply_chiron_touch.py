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
        "status": "blocked-native-i2c-and-layout-evidence",
        "boot_arg": "chiron_touch=1",
        "kernel_rebuild_required": True,
        "hardware_observed": False,
        "hardware_touch_available": False,
        "native_i2c_transport_implemented": False,
        "source_commit": manifest["hackios_commit"],
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("source", "kernel"), nargs="?", default="kernel")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = (source_overlay(args.source) if args.kind == "source"
              else kernel_overlay(args.source))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2) + "\n")
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
