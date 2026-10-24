#!/usr/bin/env python3
"""Build explicitly unqualified phone inputs from a verified driver source build."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import sys
import time

from driver_variants import digest, selection


HERE = Path(__file__).resolve().parent
ABI = struct.pack("<QQ", 0x31504D534E524843, 0xCEAFB2ACB1ADB7BC)
SCHEMA = "chiron-driver-phone-experiment-v1"


def content_digest(data):
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def payload_entries(data):
    if len(data) < 32 or data[:4] != b"XNUP" or struct.unpack_from("<I", data, 4)[0] != len(data) - 8:
        raise ValueError("invalid experimental XNUP header")
    result, offset = {}, 8
    while offset + 24 <= len(data):
        name, size, reserved = struct.unpack_from("<16sII", data, offset)
        offset += 24
        if name == bytes(16):
            if size or reserved or offset != len(data):
                raise ValueError("invalid XNUP terminator")
            if set(result) != {"kernel", "ramdisk", "cmdline", "usb"}:
                raise ValueError("unexpected experimental XNUP entries")
            return result
        name, separator, padding = name.partition(b"\0")
        if not separator or padding.strip(b"\0") or reserved:
            raise ValueError("invalid XNUP entry name or flags")
        name = name.decode("ascii")
        padded = (size + 15) & ~15
        if name in result or size > 24 << 20 or offset + padded > len(data):
            raise ValueError("duplicate or out-of-range XNUP entry")
        result[name] = data[offset:offset + size]
        if any(data[offset + size:offset + padded]):
            raise ValueError("nonzero XNUP padding")
        offset += padded
    raise ValueError("missing XNUP terminator")


def check_kernel(data, variant="smp2"):
    if len(data) < 32 or struct.unpack_from("<III", data) != (0xFEEDFACF, 0x100000C, 0):
        raise ValueError("experimental kernel is not plain ARM64 Mach-O")
    if variant == "touch":
        if b"chiron touch:" not in data:
            raise ValueError("experimental kernel lacks the touch implementation")
    elif ABI not in data:
        raise ValueError("experimental kernel lacks the SMP ABI")


def admit_qualification(proof, inputs, payload, source_experiment=False):
    if not source_experiment:
        if inputs.get("driver_experiment"):
            raise ValueError("driver inputs require explicit source-experiment packaging")
        if not proof.get("cortex_a53_no_pan_lse_desktop"):
            raise ValueError("the real constrained A53 desktop has not passed")
        return None
    if proof.get("schema") != SCHEMA or inputs.get("driver_experiment") != proof:
        raise ValueError("experimental proof differs from phone inputs")
    chosen = selection(proof.get("variant", ""))
    if chosen["variant"] not in ("smp2", "smp8", "touch") or proof.get("cpu_count") != chosen["cpu_count"]:
        raise ValueError("unsupported phone experiment")
    source = proof["source_build"]
    if (source.get("schema") != "chiron-driver-build-v1" or source.get("variant") != chosen["variant"]
            or source.get("cpu_count") != chosen["cpu_count"]):
        raise ValueError("source build variant differs")
    for record in (source, proof):
        for key in ("runtime_qualified", "hardware_observed", "phone_boot_ready"):
            if record.get(key) is not False:
                raise ValueError("source experiment must retain unqualified status")
    if source.get("kernel_reused") is not False:
        raise ValueError("the experimental source kernel was reused")
    if chosen["touch"]:
        touch = source.get("overlays", {}).get("kernel", {}).get("changes", {}).get("touch", {})
        if (source.get("touch") is not True or touch.get("board_probe_implemented") is not True
                or proof.get("probe_only") is not True):
            raise ValueError("touch image requires the board probe implementation")
        if type(proof.get("clock_prepare", False)) is not bool:
            raise ValueError("clock preparation mode must be an explicit boolean")
        if proof.get("clock_prepare", False):
            if (touch.get("clock_prepare_implemented") is not True
                    or proof.get("read_only") is not False
                    or proof.get("clock_writes") is not True):
                raise ValueError("clock preparation requires explicit write-mode proof")
        elif proof.get("read_only", True) is not True or proof.get("clock_writes", False):
            raise ValueError("read-only probe cannot request clock writes")
        if type(proof.get("pinmux_prepare", False)) is not bool:
            raise ValueError("pinmux preparation mode must be an explicit boolean")
        if proof.get("pinmux_prepare", False):
            if (touch.get("pinmux_prepare_implemented") is not True
                    or proof.get("clock_prepare") is not True
                    or proof.get("pinmux_writes") is not True):
                raise ValueError("pinmux preparation requires explicit write-mode proof")
        elif proof.get("pinmux_writes", False):
            raise ValueError("probe cannot request undeclared pinmux writes")
    if inputs["profile"]["cpu_count"] != chosen["cpu_count"]:
        raise ValueError("phone CPU profile differs")
    if proof["loader_overlay"]["cpu_limit"] != chosen["cpu_count"]:
        raise ValueError("phone loader CPU selection differs")
    entries = payload_entries(payload)
    kernel = content_digest(entries["kernel"])
    if kernel != source["artifacts"]["kernel.development.hackios"] or kernel != proof["kernel"]:
        raise ValueError("payload kernel differs from the fresh source build")
    check_kernel(entries["kernel"], chosen["variant"])
    if chosen["touch"] and proof.get("clock_prepare", False):
        if b"CHIRON TOUCH CLOCK PROBE" not in entries["kernel"]:
            raise ValueError("payload kernel lacks the clock probe")
    if chosen["touch"] and proof.get("pinmux_prepare", False):
        if b"CHIRON TOUCH PINMUX PROBE" not in entries["kernel"]:
            raise ValueError("payload kernel lacks the pinmux probe")
    root = content_digest(entries["ramdisk"])
    if any(root[key] != inputs["small_root"][key] for key in root):
        raise ValueError("payload small root differs")
    packed = content_digest(payload)
    if any(packed[key] != inputs["payload"][key] for key in packed):
        raise ValueError("experimental payload digest differs")
    command = entries["cmdline"]
    pattern = rb"cpus=" + str(chosen["cpu_count"]).encode() + rb" epoch=[0-9]+"
    if chosen["touch"]:
        pattern += (rb" -v chiron_touch_pinmux_probe=1" if proof.get("pinmux_prepare", False)
                    else rb" -v chiron_touch_clock_probe=1" if proof.get("clock_prepare", False)
                    else rb" -v chiron_touch_probe=1")
    if (not command.endswith(b"\0") or b"\0" in command[:-1]
            or not re.fullmatch(pattern + rb"\0", command)
            or command[:-1].decode("ascii") != proof["command_line"]
            or entries["usb"] != b"1\0"):
        raise ValueError("experimental CPU arguments or USB mode differ")
    return {"variant": chosen["variant"], "cpu_count": chosen["cpu_count"],
            "kernel": kernel, "source_build_run": source["source_build_run"],
            "source_project_sha": source["project_sha"], "runtime_qualified": False,
            "hardware_observed": False}


def verify_source(folder, variant, source_run, clock_prepare=False, pinmux_prepare=False):
    proof = json.loads((folder / "driver-build.json").read_text())
    chosen = selection(variant)
    if (proof.get("schema") != "chiron-driver-build-v1"
            or variant not in ("smp2", "smp8", "touch") or proof.get("variant") != variant):
        raise ValueError("wrong source artifact variant")
    if proof.get("source_build_run") != source_run or proof.get("cpu_count") != chosen["cpu_count"]:
        raise ValueError("wrong source build run or CPU count")
    if not re.fullmatch(r"[0-9a-f]{40}", proof.get("project_sha", "")):
        raise ValueError("missing source commit")
    if proof.get("kernel_reused") is not False:
        raise ValueError("source artifact reused an old kernel")
    if any(proof.get(key) is not False for key in
           ("runtime_qualified", "hardware_observed", "phone_boot_ready")):
        raise ValueError("source build has an unexpected qualification state")
    if set(proof["artifacts"]) != {
        "kernel.development.hackios", "kernel.development.hackios.unstripped", "loader.efi"
    }:
        raise ValueError("incomplete source artifact manifest")
    for name, expected in proof["artifacts"].items():
        if name not in ("kernel.development.hackios", "kernel.development.hackios.unstripped", "loader.efi"):
            raise ValueError("unexpected source artifact path")
        if digest(folder / name) != expected:
            raise ValueError("source artifact digest differs: " + name)
    if (folder / "kernel-undefined.txt").read_text().strip():
        raise ValueError("source kernel has unresolved symbols")
    check_kernel((folder / "kernel.development.hackios").read_bytes(), variant)
    if chosen["touch"]:
        report = proof.get("overlays", {}).get("kernel", {}).get("changes", {}).get("touch", {})
        if report.get("board_probe_implemented") is not True:
            raise ValueError("touch source build has no board probe")
        if clock_prepare and report.get("clock_prepare_implemented") is not True:
            raise ValueError("touch source build has no clock preparation")
        if pinmux_prepare and report.get("pinmux_prepare_implemented") is not True:
            raise ValueError("touch source build has no pinmux preparation")
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-build", type=Path, required=True)
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--variant", choices=("smp2", "smp8", "touch"), required=True)
    parser.add_argument("--clock-prepare", action="store_true")
    parser.add_argument("--pinmux-prepare", action="store_true")
    args = parser.parse_args()
    import apply_chiron_smp
    import cloud

    cloud.require_runner()
    if args.pinmux_prepare:
        args.clock_prepare = True
    if args.clock_prepare and args.variant != "touch":
        parser.error("clock/pinmux preparation requires the touch variant")
    source = verify_source(args.source_build, args.variant, args.source_run,
                           args.clock_prepare, args.pinmux_prepare)
    chosen = selection(args.variant)
    base = args.runtime / "phone"
    inputs = json.loads((base / "inputs.json").read_text())
    root = cloud.digest(base / "root.apfs")
    if root != inputs["small_root"]:
        raise ValueError("the qualified userland small root differs")
    shutil.copytree(base, args.output)
    cloud.command([sys.executable, HERE / "phone_loader.py", "--work", args.work,
                   "--phone", args.output])
    if chosen["touch"]:
        import apply_chiron_touch
        overlay = {"cpu_limit": 1, "touch": apply_chiron_touch.loader_overlay(args.work)}
    else:
        overlay = apply_chiron_smp.loader_overlay(args.work, chosen["cpu_count"])
    cloud.command(["bash", "xnuloader/build.sh"], cwd=args.work)
    shutil.copy2(args.work / "xnuloader/loader.efi", args.output / "loader.efi")
    command_line = f"{apply_chiron_smp.command_line(chosen['cpu_count'])} epoch={int(time.time())}"
    if chosen["touch"]:
        command_line += (" -v chiron_touch_pinmux_probe=1" if args.pinmux_prepare
                         else " -v chiron_touch_clock_probe=1" if args.clock_prepare
                         else " -v chiron_touch_probe=1")
    kernel = args.source_build / "kernel.development.hackios"
    cloud.command([sys.executable, args.work / "xnuloader/mkpayload.py", "--usb",
                   "-o", args.output / "payload.bin", f"kernel={kernel}",
                   f"ramdisk={args.output / 'root.apfs'}", "--cmdline", command_line])
    inputs = json.loads((args.output / "inputs.json").read_text())
    inputs["loader"] = cloud.digest(args.output / "loader.efi")
    inputs["payload"] = cloud.digest(args.output / "payload.bin")
    inputs["profile"]["cpu_count"] = chosen["cpu_count"]
    inputs["hardware_boot_qualified"] = False
    inputs["phone_loader_source_sha256"] = digest(args.work / "xnuloader/loader.c")["sha256"]
    experiment = {
        "schema": SCHEMA, "variant": args.variant, "cpu_count": chosen["cpu_count"],
        "source_build": source, "kernel": digest(kernel), "loader_overlay": overlay,
        "command_line": command_line, "packaging_project_sha": os.environ["GITHUB_SHA"],
        "probe_only": chosen["touch"],
        "clock_prepare": args.clock_prepare,
        "clock_writes": args.clock_prepare,
        "pinmux_prepare": args.pinmux_prepare,
        "pinmux_writes": args.pinmux_prepare,
        "read_only": chosen["touch"] and not args.clock_prepare,
        "runtime_qualified": False, "hardware_observed": False, "phone_boot_ready": False,
    }
    inputs["driver_experiment"] = experiment
    admit_qualification(experiment, inputs, (args.output / "payload.bin").read_bytes(), True)
    cloud.save(args.output / "inputs.json", inputs)
    cloud.save(args.output / "driver-experiment.json", experiment)


if __name__ == "__main__":
    main()
