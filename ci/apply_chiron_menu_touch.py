#!/usr/bin/env python3
"""Opt-in, pinned UniXNU menu touch overlay; leaves the XNU kernel unchanged."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINNED = {
    "Menu.c": "00c0af0543c666a692f37f8718b30e4dfe14d264",
    "UniXNULib.inf": "bfdb3b3dc0fb5b84ec1d98998364f1f512679663",
}


def apply(source):
    library = source / "package/UniXNUPkg/Library/UniXNULib"
    contents = {}
    for name, expected in PINNED.items():
        data = (library / name).read_bytes()
        blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if blob != expected:
            raise ValueError(f"pinned UniXNU source differs: {name}")
        contents[name] = data
    copies = {
        name: ROOT / "ci/uefi" / name
        for name in ("Menu.c", "ChironMenuTouch.c", "ChironMenuTouch.h")
    }
    copies.update({
        name: ROOT / "drivers/chiron-touch" / name
        for name in ("chiron_dsx.c", "chiron_dsx.h", "chiron_f12.c", "chiron_f12.h")
    })
    for name in copies:
        if name != "Menu.c" and (library / name).exists():
            raise ValueError(f"overlay destination already exists: {name}")
    inf = contents["UniXNULib.inf"].decode()
    if inf.count("  Menu.c\n") != 1:
        raise ValueError("UniXNULib source list changed")
    inf = inf.replace("  Menu.c\n",
        "  Menu.c\n  ChironMenuTouch.c\n  chiron_dsx.c\n  chiron_f12.c\n", 1)
    result = {}
    for name, path in copies.items():
        data = path.read_bytes()
        (library / name).write_bytes(data)
        result[name] = hashlib.sha256(data).hexdigest()
    (library / "UniXNULib.inf").write_text(inf)
    result["UniXNULib.inf"] = hashlib.sha256(inf.encode()).hexdigest()
    return {
        "schema": "chiron-unixnu-menu-touch-v1",
        "upstream_commit": "7b52e7968c76453cdc18b5aa174fc368d5293b91",
        "result": result,
        "i2c_firmware_sha256": "1b1df5b767b488d8049455c70c3b3d92e45aca8a2d33a5c075c6201a4e836ff6",
        "i2c_instance": 4,
        "i2c_config_bytes": 12,
        "i2c_timeout_ms": 100,
        "kernel_rebuilt": False,
        "power_writes": False,
        "menu_owned_synchronous_io": True,
        "hardware_observed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    proof = apply(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, indent=2) + "\n")


if __name__ == "__main__":
    main()
