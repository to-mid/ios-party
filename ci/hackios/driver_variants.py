#!/usr/bin/env python3
"""Build isolated chiron driver experiments without inheriting HK6c qualification."""

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path


VARIANTS = {
    "baseline": {"cpu_count": 1, "touch": False},
    "touch": {"cpu_count": 1, "touch": True},
    "touch-smp2": {"cpu_count": 2, "touch": True},
    "touch-smp8": {"cpu_count": 8, "touch": True},
    "smp2": {"cpu_count": 2, "touch": False},
    "smp4": {"cpu_count": 4, "touch": False},
    "smp8": {"cpu_count": 8, "touch": False},
}


def selection(variant=None):
    name = variant if variant is not None else os.environ.get("HACKIOS_CHIRON_VARIANT", "baseline")
    if name not in VARIANTS:
        raise ValueError(f"unknown chiron driver variant: {name!r}")
    return {"variant": name, **VARIANTS[name]}


def require_fresh_kernel(variant=None):
    chosen = selection(variant)
    if chosen["variant"] != "baseline" and (
        os.environ.get("HACKIOS_CHIRON_KERNEL_DIR") or os.environ.get("HACKIOS_REUSE_KERNEL_DIR")
    ):
        raise ValueError("driver experiments require a fresh source kernel; unset kernel reuse")
    return chosen


def require_baseline_runtime(variant=None):
    chosen = selection(variant)
    if chosen["variant"] != "baseline":
        raise ValueError("the existing single-core desktop qualification cannot qualify driver experiments")


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 << 20):
            sha.update(block)
    return {"bytes": path.stat().st_size, "sha256": sha.hexdigest()}


def overlay(kind, source, variant=None):
    chosen = require_fresh_kernel(variant)
    changes = {}
    if chosen["cpu_count"] > 1:
        module = importlib.import_module("apply_chiron_smp")
        if kind == "loader":
            changes["smp"] = module.loader_overlay(source, chosen["cpu_count"])
        else:
            changes["smp"] = (module.kernel_overlay(source, strict_firmware_return=True)
                              if chosen["touch"] else module.kernel_overlay(source))
    if chosen["touch"]:
        module = importlib.import_module("apply_chiron_touch")
        function = getattr(module, f"{kind}_overlay", None)
        if function is not None:
            changes["touch"] = function(source)
        elif kind == "kernel":
            raise ValueError("the touch experiment has no kernel overlay")
    return {
        "schema": "chiron-driver-source-experiment-v1",
        **chosen, "stage": kind, "changes": changes,
        "hardware_observed": False, "runtime_qualified": False,
        "phone_boot_ready": False,
    }


def record_build(evidence, variant=None):
    chosen = require_fresh_kernel(variant)
    overlays = {}
    for stage in ("loader", "kernel"):
        path = evidence / f"driver-{stage}-overlay.json"
        report = json.loads(path.read_text())
        if report.get("variant") != chosen["variant"] or report.get("stage") != stage:
            raise ValueError("driver overlay provenance differs from the selected build")
        overlays[stage] = report
    undefined = evidence / "kernel-undefined.txt"
    if not undefined.is_file() or undefined.read_text().strip():
        raise ValueError("source kernel has missing or nonempty undefined-symbol evidence")
    return {
        "schema": "chiron-driver-build-v1", **chosen,
        "artifacts": {
            name: digest(evidence / name) for name in (
                "kernel.development.hackios",
                "kernel.development.hackios.unstripped",
                "loader.efi",
            )
        },
        "overlays": overlays,
        "source_build_run": os.environ.get("GITHUB_RUN_ID"),
        "project_sha": os.environ.get("GITHUB_SHA"),
        "kernel_reused": False, "runtime_qualified": False,
        "hardware_observed": False, "phone_boot_ready": False,
        "scope": "Fresh compilation only; separate runtime and phone input qualification required.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("validate-build", "loader", "kernel", "record"))
    parser.add_argument("--variant", choices=tuple(VARIANTS))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    require_fresh_kernel(args.variant)
    if args.stage == "validate-build":
        return
    if args.evidence is None:
        parser.error("--evidence is required")
    args.evidence.mkdir(parents=True, exist_ok=True)
    if args.stage == "record":
        result = record_build(args.evidence, args.variant)
        filename = "driver-build.json"
    else:
        if args.source is None:
            parser.error("--source is required for overlays")
        result = overlay(args.stage, args.source, args.variant)
        filename = f"driver-{args.stage}-overlay.json"
    (args.evidence / filename).write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
