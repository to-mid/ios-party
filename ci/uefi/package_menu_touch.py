#!/usr/bin/env python3
"""Package a changed UniXNU firmware body with the exact previous XNU payload."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "ci/hackios"))
from chiron_ram_fat import RamFat32
from inspect_chiron_boot import inspect

spec = importlib.util.spec_from_file_location("chiron_package", ROOT / "ci/hackios/package-chiron.py")
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def package(prior, template, entry, firmware, proof, output):
    manifest = json.loads((prior / "manifest.json").read_text())
    image = prior / manifest["image"]
    if Path(manifest["image"]).name != manifest["image"]:
        raise ValueError("invalid previous image basename")
    data = image.read_bytes()
    if sha(data) != manifest["sha256"] or len(data) != manifest["bytes"]:
        raise ValueError("previous candidate digest differs")
    if (manifest["physical_transport_bytes"], manifest["logical_transport_bytes"]) != (24 << 20, 48 << 20):
        raise ValueError("previous transport geometry differs")
    if sha(entry.read_bytes()) != manifest["entry_sha256"]:
        raise ValueError("unchanged HackiOS entry digest differs")
    menu = json.loads(proof.read_text())
    if menu.get("schema") != "chiron-unixnu-menu-touch-v1":
        raise ValueError("missing compiled menu overlay proof")
    with tempfile.TemporaryDirectory(prefix="chiron-menu-package-") as temporary:
        work = Path(temporary)
        inspect(image, work / "inspection")
        boot = json.loads((work / "inspection/inspection.json").read_text())
        if not boot["legacy_sha1_matches"]:
            raise ValueError("previous boot checksum differs")
        offset = boot["payload_offsets"]["ramdisk"]
        fat = RamFat32(data[offset:offset + (24 << 20)] + bytes(24 << 20))
        phone = work / "phone"
        phone.mkdir()
        for name, expected in (("loader.efi", manifest["phone_inputs"]["loader"]),
                               ("payload.bin", manifest["phone_inputs"]["payload"])):
            content = fat.read("EFI/HackiOS/" + name)
            if len(content) != expected["bytes"] or sha(content) != expected["sha256"]:
                raise ValueError("previous FAT component differs: " + name)
            (phone / name).write_bytes(content)
        (phone / "inputs.json").write_text(json.dumps(manifest["phone_inputs"]))
        qualification = work / "qualification.json"
        qualification.write_text(json.dumps(manifest["qualification"]))
        result = packager.package(template, phone, entry, qualification, output,
            firmware / "chiron-unixnu.fd", firmware / "memory-policy.json",
            source_experiment=True, title="EXPERIMENTAL UNIXNU TOUCH",
            image_name="MIX2-EXPERIMENTAL-UNIXNU-TOUCH.img", menu_timeout=30)
    current = json.loads((output / "manifest.json").read_text())
    current["menu_touch"] = menu
    current["menu_touch"]["prior_candidate_sha256"] = manifest["sha256"]
    current["menu_touch"]["unchanged_payload_sha256"] = manifest["phone_inputs"]["payload"]["sha256"]
    current["menu_touch"]["kernel_rebuilt"] = False
    (output / "manifest.json").write_text(json.dumps(current, indent=2) + "\n")
    shutil.copyfile(ROOT / "tools/start_chiron_hackios.py", output / "start_chiron_hackios.py")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("prior", "template", "entry", "firmware", "proof", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(package(**vars(args)))


if __name__ == "__main__":
    main()
