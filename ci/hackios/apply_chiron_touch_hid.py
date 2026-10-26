#!/usr/bin/env python3
"""Explicit single-swipe diagnostic using the pinned upstream rwtouch producer."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
UPSTREAM_RWT = "013133a862b304dac018b5f86df4c88e630f381359bf82ea5d7fc38d7115fccc"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def replay_source(original):
    """Keep upstream gesture timing; release a posted finger on ordinary error exit."""
    if digest(original) != UPSTREAM_RWT:
        raise ValueError("pinned rwtouch source differs")
    text = original.decode()
    text = text.replace(
        "static int device;\n",
        """static int device;
static int chiron_replay_down;
static uint32_t chiron_replay_x, chiron_replay_y;

static void
chiron_replay_release(void)
{
	struct redwood_touch t = { .type = REDWOOD_TOUCH_UP, .finger = 0,
	    .x = chiron_replay_x, .y = chiron_replay_y };

	if (chiron_replay_down) {
		chiron_replay_down = 0;
		if (write(device, &t, sizeof(t)) != (ssize_t)sizeof(t))
			perror("CHIRON-HID-SYNTHETIC: cleanup UP failed");
	}
}
""", 1)
    text = text.replace(
        '\t\tperror("rwtouch: write");',
        """		/* A short write might have published the record before failing. */
		chiron_replay_x = t.x;
		chiron_replay_y = t.y;
		if (type == REDWOOD_TOUCH_DOWN)
			chiron_replay_down = 1;
		perror("rwtouch: write");""", 1)
    text = text.replace(
        "\n}\n\nstatic void\nbutton(",
        """
	chiron_replay_x = t.x;
	chiron_replay_y = t.y;
	chiron_replay_down = type != REDWOOD_TOUCH_UP;
}

static void
button(""", 1)
    text = text.replace(
        "\tfor (int i = 1; i < argc; i++) {",
        """	printf("CHIRON-HID-SYNTHETIC: explicit single-swipe diagnostic; physical driver disabled\\n");
	if (atexit(chiron_replay_release) != 0) {
		fprintf(stderr, "CHIRON-HID-SYNTHETIC: cannot install UP cleanup\\n");
		return 1;
	}
	for (int i = 1; i < argc; i++) {""", 1)
    # This context belongs to main, after all of the command-line steps.
    old = "\t\tusleep(50000);\n\t}\n\treturn 0;\n}"
    if text.count(old) != 1:
        raise ValueError("pinned replay main context differs")
    text = text.replace(old, '\t\tusleep(50000);\n\t}\n'
                        '\tprintf("CHIRON-HID-SYNTHETIC: sequence complete; '
                        'require tl: i and screen response, not only tl: p\\n");\n'
                        '\treturn 0;\n}', 1)
    return text.encode()


def install(root, binary):
    """Only an explicitly selected scratch root may acquire this diagnostic."""
    root, binary = Path(root), Path(binary)
    jobs, program = root / "etc/rc.jobs", root / "bin/rwtouch"
    for path in (root, jobs.parent, program.parent, jobs, program, binary):
        if path.is_symlink() or not path.exists():
            raise ValueError(f"missing regular diagnostic input: {path}")
    data = binary.read_bytes()
    if data[:12] != bytes.fromhex("cffaedfe0c00000100000000"):
        raise ValueError("replay producer must be an ARM64 Mach-O")
    before = {str(p.relative_to(root)): digest(p.read_bytes()) for p in (jobs, program)}
    jobs.write_bytes((HERE / "chiron_touch_hid.jobs").read_bytes())
    program.write_bytes(data)
    return {
        "schema": "chiron-touch-hid-replay-v1",
        "synthetic": True, "physical_touch_qualified": False,
        "consumer_ready_assumed": False, "gesture_count": 1,
        "required_boot_arguments": "chiron_touch=0 touch_lat=1 loader_return_after=180",
        "before": before,
        "after": {str(p.relative_to(root)): digest(p.read_bytes()) for p in (jobs, program)},
    }


def rebuild_root(baseline, output, source, compiler, sdk, linker):
    """Repack only the small root and compare every logical entry after readback."""
    import cloud

    cloud.require_runner()
    baseline, output, source = Path(baseline), Path(output), Path(source)
    output.mkdir(parents=True, exist_ok=False)
    tools = source / "tools/apfs"
    sys.path.insert(0, str(tools))
    apfs = cloud.load_module("chiron_hid_apfs", tools / "apfs.py")

    def snapshot(image):
        volume = apfs.pick_volume(apfs.Container(str(image)), None)
        records = {}
        for path, _, oid, _ in [("/", "", 2, 4), *apfs.walk(volume, 2)]:
            inode = volume.inode(oid)
            attrs = set(inode.xattrs)
            if attrs - {"com.apple.decmpfs", "com.apple.ResourceFork", "com.apple.fs.symlink"}:
                raise ValueError(f"unsupported small-root attributes: {path}")
            if not (inode.is_dir or inode.is_reg or inode.is_link):
                raise ValueError(f"unsupported small-root node: {path}")
            if any(p in (".", "..") for p in Path(path).parts[1:]):
                raise ValueError("invalid small-root path")
            records[path] = (inode.mode, inode.owner, inode.group,
                             b"" if inode.is_dir else inode.contents())
        return volume.volname, records

    name, original = snapshot(baseline)
    root = output / "root"
    root.mkdir()
    manifest = []
    for path, (mode, uid, gid, data) in original.items():
        target = root / path.lstrip("/")
        if mode & 0o170000 == 0o040000:
            target.mkdir(exist_ok=True)
        elif mode & 0o170000 == 0o120000:
            target.symlink_to(os.fsdecode(data))
        else:
            target.write_bytes(data)
        if not target.is_symlink():
            target.chmod(mode & 0o7777)
        if path != "/":
            manifest.append(f"{mode:o}\t{uid}\t{gid}\t{path.lstrip('/')}")
    metadata = output / "metadata.txt"
    metadata.write_text("\n".join(manifest) + "\n")
    patched = output / "rwtouch.c"
    patched.write_bytes(replay_source((source / "userland/src/rwtouch.c").read_bytes()))
    binary = output / "rwtouch"
    cloud.command([compiler, "-target", "arm64-apple-ios16.5", "--ld-path=" + str(linker), "-isysroot", sdk,
                   "-O1", "-I", source / "userland/src", patched, "-o", binary])
    proof = install(root, binary)
    image = output / "root.apfs"
    cloud.command([sys.executable, tools / "mkapfs.py", "-z", "--plain", "fw",
                   "-o", image, f"{name}={root},manifest={metadata}"])
    new_name, after = snapshot(image)
    if new_name != name or after.keys() != original.keys():
        raise ValueError("replay repack changed root directory membership")
    changed = {p for p in original if original[p] != after[p]}
    if changed != {"/etc/rc.jobs", "/bin/rwtouch"}:
        raise ValueError(f"replay repack changed unexpected entries: {sorted(changed)}")
    for path in changed:
        if original[path][:3] != after[path][:3]:
            raise ValueError(f"replay repack changed file ownership or mode: {path}")
    proof.update(baseline_root=cloud.digest(baseline), root=cloud.digest(image),
                 upstream_rwtouch_sha256=UPSTREAM_RWT,
                 replay_source_sha256=digest(patched.read_bytes()),
                 logical_readback_changed=sorted(changed),
                 rebuilt_program=cloud.digest(binary), hardware_observed=False,
                 runtime_qualified=False)
    cloud.save(output / "hid-replay.json", proof)
    return image, proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    source = sub.add_parser("source")
    source.add_argument("--upstream", type=Path, required=True)
    source.add_argument("--output", type=Path, required=True)
    root = sub.add_parser("install")
    root.add_argument("--root", type=Path, required=True)
    root.add_argument("--binary", type=Path, required=True)
    build = sub.add_parser("build")
    build.add_argument("--baseline", type=Path, required=True)
    build.add_argument("--work", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "source":
        args.output.write_bytes(replay_source(args.upstream.read_bytes()))
    elif args.mode == "install":
        print(json.dumps(install(args.root, args.binary), indent=2))
    else:
        import cloud
        cloud.require_runner()
        evidence = args.work / "evidence"
        evidence.mkdir(parents=True)
        cloud.prepare(args.work, evidence)
        rebuild_root(args.baseline, args.output, args.work / "kernel", "/usr/bin/clang",
                     args.work / "theos/sdks/iPhoneOS16.5.sdk",
                     args.work / "theos/toolchain/linux/iphone/bin/ld")


if __name__ == "__main__":
    main()
