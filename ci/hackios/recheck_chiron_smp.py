#!/usr/bin/env python3
"""Replay the delivered SMP kernel/root on a real two-cluster QEMU guest."""

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

import apply_chiron
import cloud
from driver_phone import admit_qualification, content_digest, payload_entries, verify_source


MPIDRS = [0, 1, 2, 3, 256, 257, 258, 259]
MACHINE = "virt,gic-version=3"
QEMU_CPUS = 20


def loader_topology(text):
    text = apply_chiron.replace_once(
        text, "\t\t\t\tuint64_t mpidr = (uint64_t)core << plat->core_shift;",
        "\t\t\t\tuint64_t mpidr = (uint64_t)core << plat->core_shift;\n"
        "\t\t\t\tif (plat == &platform_qemu_virt)\n"
        "\t\t\t\t\tmpidr = ((uint64_t)(core / 4) << 8) | (core % 4);",
    )
    text = apply_chiron.replace_once(
        text, "\tif (more_cores) {\n\t\tfor (int distance",
        "\tif (more_cores) {\n"
        "\t\tif (plat == &platform_qemu_virt) {\n"
        "\t\t\tfor (int cpu = 4; cpu < 16; cpu++) {\n"
        "\t\t\t\tif (psci_call(PSCI_AFFINITY_INFO, cpu, 0, 0) != 1) {\n"
        '\t\t\t\t\tprint("qemu smp: unused affinity not OFF; refusing boot\\n");\n'
        "\t\t\t\t\treturn 0;\n\t\t\t\t}\n\t\t\t}\n"
        '\t\t\tprint("qemu smp: 12 unused affinities confirmed OFF\\n");\n'
        "\t\t}\n\t\tfor (int distance",
    )
    return apply_chiron.replace_once(
        text,
        "plat->gic_redist - plat->io_base, 0x100000 };",
        "plat->gic_redist - plat->io_base, (plat == &platform_qemu_virt ? 0x280000 : 0x100000) };",
    )


def observations(text):
    online = sorted({int(cpu) for cpu in re.findall(r"arm_cpu_init\(\): cpu ([1-7]) online", text)})
    clusters = sorted({int(c) for c in re.findall(r"cluster_id ([01]) cpu_number", text)})
    psets = {(int(cluster), int(pset)) for cluster, pset in re.findall(
        r"pset_find\(cluster_id=(\d+)\) returned pset (\d+)", text)}
    return {
        "secondary_cpus_online": online,
        "clusters_registered": clusters,
        "all_secondary_cpus_online": online == list(range(1, 8)),
        "both_clusters_registered": clusters == [0, 1],
        "shared_scheduler_pset": psets == {(0, 0), (1, 0)},
        "unused_affinities_off": "qemu smp: 12 unused affinities confirmed OFF" in text,
        "eight_cpu_cache_init": "mcache: 8 CPU(s)" in text,
        "framebuffer_swaps": bool(re.search(r"MiniIOMobileFramebuffer: swap \d+", text)),
        "springboard_process": bool(re.search(
            r"(?m)^\s*\d+\s+.*SpringBoard\.app/|top:.*SpringBoard\[\d+\]", text)),
        "backboardd_process": bool(re.search(
            r"(?m)^\s*\d+\s+.*(?:/usr/libexec/backboardd|backboardd\s*$)", text)),
        "fatal": bool(re.search(r"panic\(cpu|Undefined kernel instruction|initproc failed", text)),
        "timer_return": "loader_return_after expired" in text,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "phone", "system", "work", "evidence"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--seconds", type=int, default=540)
    args = parser.parse_args()
    cloud.require_runner()
    args.work, args.evidence = args.work.resolve(), args.evidence.resolve()
    args.evidence.mkdir(parents=True, exist_ok=True)
    source = verify_source(args.source, "touch-smp8", args.source_run)
    sys.path.insert(0, str(cloud.ROOT / "tools"))
    from chiron_ram_fat import RamFat32
    from inspect_chiron_boot import inspect
    manifest = json.loads((args.phone / "manifest.json").read_text())
    image = args.phone / manifest["image"]
    if cloud.digest(image)["sha256"] != manifest["sha256"]:
        raise ValueError("delivered phone image hash differs")
    with contextlib.redirect_stdout(io.StringIO()):
        inspect(image, args.evidence / "container")
    boot = json.loads((args.evidence / "container/inspection.json").read_text())
    if not boot["legacy_sha1_matches"] or boot["header"]["ramdisk_size"] != 24 << 20:
        raise ValueError("invalid phone transport container")
    data = image.read_bytes()
    offset = boot["payload_offsets"]["ramdisk"]
    fat = RamFat32(data[offset:offset + (24 << 20)] + bytes(24 << 20))
    payload = fat.read("EFI/HackiOS/payload.bin")
    qualification = admit_qualification(
        manifest["qualification"], manifest["phone_inputs"], payload, True)
    entries = payload_entries(payload)
    system_digest = cloud.digest(args.system)
    if (qualification["source_build_run"] != args.source_run
            or content_digest(entries["kernel"]) != source["artifacts"]["kernel.development.hackios"]
            or any(content_digest(entries["ramdisk"])[key] != manifest["phone_inputs"]["small_root"][key]
                   for key in ("bytes", "sha256"))
            or any(system_digest[key] != manifest["phone_inputs"]["system_root"][key]
                   for key in ("bytes", "sha256"))):
        raise ValueError("QEMU inputs differ from the delivered kernel/root")
    kernel = args.work / "kernel"
    pin = json.loads(cloud.SOURCES.read_text())["kernel"]
    cloud.fetch_git(kernel, pin["url"], pin["commit"])
    patch = cloud.ROOT / "ci/hackios/patches/0001-wait-for-ramfb-dma.patch"
    cloud.command(["git", "-C", kernel, "apply", patch])
    profile = json.loads((Path(__file__).with_name("chiron-profile.json")).read_text())
    apply_chiron.loader_overlay(kernel, profile)
    loader = kernel / "xnuloader/loader.c"
    loader.write_text(loader_topology(loader.read_text()))
    cloud.command(["bash", "xnuloader/build.sh"], cwd=kernel,
                  log=args.evidence / "qemu-loader-build.log")
    target = kernel / "xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837"
    target.parent.mkdir(parents=True)
    shutil.copy2(args.source / "kernel.development.hackios", target)
    (kernel / "userland").mkdir()
    (kernel / "userland/root.apfs").write_bytes(entries["ramdisk"])
    os.link(args.system, kernel / "userland/system.apfs")
    run = kernel / "qemu/run.sh"
    run.write_text(apply_chiron.replace_once(run.read_text(), "-cpu cortex-a76", "-cpu cortex-a53"))
    ios = kernel / "qemu/run-ios.sh"
    text = apply_chiron.replace_once(ios.read_text(), "RD_ADDR=0xFC000000", "RD_ADDR=0x108000000")
    ios.write_text(apply_chiron.replace_once(text, '--rdres "$(printf', '--physbase 100000000 --rdres "$(printf'))
    dtb = args.evidence / "qemu-topology.dtb"
    cloud.command(["qemu-system-aarch64", "-M", MACHINE + ",dumpdtb=" + str(dtb),
                   "-cpu", "cortex-a53", "-smp", str(QEMU_CPUS), "-m", "7G", "-display", "none"],
                  log=args.evidence / "qemu-topology.log")
    nodes = cloud.output(["fdtget", "-l", dtb, "/cpus"]).splitlines()
    affinities = sorted(int(cloud.output(["fdtget", "-t", "x", dtb, "/cpus/" + node, "reg"]), 16)
                        for node in nodes if node.startswith("cpu@"))
    if affinities != list(range(16)) + list(range(256, 260)):
        raise ValueError(f"QEMU did not instantiate the two-cluster affinities: {affinities}")
    cloud.save(args.evidence / "inputs.json", {
        "source_build_run": args.source_run, "source_commit": source["project_sha"],
        "image_sha256": manifest["sha256"], "kernel": content_digest(entries["kernel"]),
        "small_root": content_digest(entries["ramdisk"]), "system": system_digest,
        "qemu_loader": cloud.digest(kernel / "xnuloader/loader.efi"),
        "qemu_loader_source": cloud.digest(loader), "machine": MACHINE,
        "cpu_model": "cortex-a53", "instantiated_vcpus": QEMU_CPUS,
        "available_mpidrs": affinities, "selected_mpidrs": MPIDRS,
        "guest_seconds": args.seconds,
        "physical_touch_enabled": False, "hardware_observed": False,
        "limits": "QEMU PSCI/GIC/firmware; not Kryo heterogeneous cores, QUP, or phone watchdog",
    })
    screens = args.evidence / "screens"
    screens.mkdir()
    env = dict(os.environ, QEMU_EFI="/usr/share/AAVMF/AAVMF_CODE.fd",
               QEMU_VARS="/usr/share/AAVMF/AAVMF_VARS.fd", QEMU_RAM_GB="7",
               QEMU_SMP=str(QEMU_CPUS), QEMU_INSTANCE="0", AUDIO="none",
               CMDLINE="cpus=8 chiron_touch=0")
    started = time.monotonic()
    actions = []
    with (args.evidence / "qemu-serial.log").open("wb") as serial:
        process = subprocess.Popen(["bash", "qemu/run-ios.sh", str(args.seconds)],
                                   cwd=kernel, env=env, stdout=serial,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            for delay in (30, 90, 180, 300, 420, 500):
                if delay >= args.seconds:
                    break
                while process.poll() is None and time.monotonic() - started < delay:
                    time.sleep(1)
                if process.poll() is not None:
                    break
                rc = cloud.command(["bash", "qemu/screendump.sh", screens / f"t{delay:04d}.png"],
                                   cwd=kernel, env=env, allow_failure=True,
                                   log=args.evidence / f"screen-{delay}.log")
                actions.append({"host_seconds": delay, "screenshot_returncode": rc})
            process.wait(timeout=max(1, args.seconds + 65 - (time.monotonic() - started)))
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
            text = (args.evidence / "qemu-serial.log").read_text(errors="replace")
            seen = observations(text)
            result = {
                **seen, "run_id": os.environ["GITHUB_RUN_ID"], "actions": actions,
                "returncode": process.returncode, "host_seconds": time.monotonic() - started,
                "hardware_observed": False, "physical_touch_qualified": False,
            }
            result["passed"] = all(seen[key] for key in (
                "all_secondary_cpus_online", "both_clusters_registered", "shared_scheduler_pset",
                "unused_affinities_off",
                "eight_cpu_cache_init",
                "framebuffer_swaps", "springboard_process", "backboardd_process",
            )) and not seen["fatal"] and result["host_seconds"] >= args.seconds
            cloud.save(args.evidence / "qemu-result.json", result)
    if not result["passed"]:
        raise RuntimeError("SMP QEMU validation failed; inspect the serial log and qemu-result.json")


if __name__ == "__main__":
    main()
