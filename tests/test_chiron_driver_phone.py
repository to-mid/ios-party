"""Reject stale kernels and inherited desktop qualification in SMP phone images."""

import copy
import importlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ci/hackios"))
phone = importlib.import_module("driver_phone")


def pack(entries):
    body = bytearray()
    for name, value in entries:
        body.extend(struct.pack("<16sII", name.encode(), len(value), 0))
        body.extend(value)
        body.extend(bytes(-len(value) % 16))
    body.extend(bytes(24))
    return b"XNUP" + struct.pack("<I", len(body)) + body


def fixture(count=2, command=None):
    kernel = struct.pack("<8I", 0xFEEDFACF, 0x100000C, 0, 2, 0, 0, 0, 0) + phone.ABI
    root = b"small root fixture"
    command = command or f"cpus={count} epoch=1792760000"
    payload = pack([("kernel", kernel), ("ramdisk", root),
                    ("cmdline", command.encode() + b"\0"), ("usb", b"1\0")])
    digest = phone.content_digest(kernel)
    source = {
        "schema": "chiron-driver-build-v1", "variant": f"smp{count}", "cpu_count": count,
        "source_build_run": "123", "project_sha": "a" * 40,
        "kernel_reused": False, "runtime_qualified": False,
        "hardware_observed": False, "phone_boot_ready": False,
        "artifacts": {"kernel.development.hackios": digest},
    }
    proof = {
        "schema": phone.SCHEMA, "variant": f"smp{count}", "cpu_count": count,
        "source_build": source, "kernel": digest, "command_line": command,
        "loader_overlay": {"cpu_limit": count},
        "runtime_qualified": False, "hardware_observed": False, "phone_boot_ready": False,
    }
    inputs = {"profile": {"cpu_count": count}, "payload": phone.content_digest(payload),
              "small_root": phone.content_digest(root), "driver_experiment": proof}
    return proof, inputs, payload


def touch_fixture(command="cpus=1 epoch=1792760000 -v chiron_touch_probe=1"):
    proof, inputs, payload = fixture()
    entries = phone.payload_entries(payload)
    entries["kernel"] += b"chiron touch: board probe"
    entries["cmdline"] = command.encode() + b"\0"
    payload = pack(list(entries.items()))
    kernel = phone.content_digest(entries["kernel"])
    source = proof["source_build"]
    source.update(variant="touch", cpu_count=1, touch=True,
                  overlays={"kernel": {"changes": {"touch": {"board_probe_implemented": True}}}})
    source["artifacts"]["kernel.development.hackios"] = kernel
    proof.update(variant="touch", cpu_count=1, probe_only=True, kernel=kernel, command_line=command)
    proof["loader_overlay"]["cpu_limit"] = 1
    inputs["profile"]["cpu_count"] = 1
    inputs["payload"] = phone.content_digest(payload)
    return proof, inputs, payload


def clock_fixture():
    command = "cpus=1 epoch=1792760000 -v chiron_touch_clock_probe=1"
    proof, inputs, payload = touch_fixture(command)
    entries = phone.payload_entries(payload)
    entries["kernel"] += b"CHIRON TOUCH CLOCK PROBE"
    payload = pack(list(entries.items()))
    kernel = phone.content_digest(entries["kernel"])
    proof["kernel"] = kernel
    proof["source_build"]["artifacts"]["kernel.development.hackios"] = kernel
    proof["source_build"]["overlays"]["kernel"]["changes"]["touch"]["clock_prepare_implemented"] = True
    proof.update(clock_prepare=True, clock_writes=True, read_only=False)
    inputs["payload"] = phone.content_digest(payload)
    return proof, inputs, payload


def active_fixture():
    proof, inputs, payload = clock_fixture()
    entries = phone.payload_entries(payload)
    entries["kernel"] += b"CHIRON TOUCH PINMUX PROBE chiron touch: pinmux prepare result="
    entries["kernel"] += b"chiron touch: reset initialization verified;"
    entries["kernel"] += b"F01.Data0.status_after_configure"
    entries["kernel"] += b"chiron touch: transport input_fifo="
    proof["command_line"] = ("cpus=1 epoch=1792760000 chiron_touch=1 chiron_touch_reset=1"
                             " loader_return_after=60 touch_lat=1")
    entries["cmdline"] = proof["command_line"].encode() + b"\0"
    payload = pack(list(entries.items()))
    kernel = phone.content_digest(entries["kernel"])
    proof["kernel"] = kernel
    proof["source_build"]["artifacts"]["kernel.development.hackios"] = kernel
    proof["source_build"]["overlays"]["kernel"]["changes"]["touch"].update(
        pinmux_prepare_implemented=True, active_pinmux_prepare_implemented=True,
        reset_sensor_initialization_implemented=True, f01_configured_read_zero_implemented=True,
        active_object_prefix_read_implemented=True, transport_result_diagnostics_implemented=True)
    proof["loader_overlay"]["touch"] = {
        "reset_prepare_implemented": True, "reset_retained_android_profile": True,
        "reset_power_probe_after_usb_start": True,
    }
    proof.update(probe_only=False, touch_active=True, qup_writes=True,
                 sensor_configuration_writes=True,
                 pinmux_prepare=True, pinmux_writes=True, reset_prepare=True, reset_writes=True)
    inputs["payload"] = phone.content_digest(payload)
    return proof, inputs, payload


class ExperimentAdmissionTests(unittest.TestCase):
    def test_home_wake_requires_real_capture_and_no_synthetic_gesture(self):
        proof, inputs, payload = active_fixture()
        entries = phone.payload_entries(payload)
        entries["kernel"] += b"chiron touch: capture layout"
        proof["kernel"] = phone.content_digest(entries["kernel"])
        proof["source_build"]["artifacts"]["kernel.development.hackios"] = proof["kernel"]
        proof["source_build"]["overlays"]["kernel"]["changes"]["touch"][
            "poll_frame_capture_implemented"] = True
        proof["touch_capture"] = True
        proof["touch_home_wake"] = True
        proof["command_line"] = proof["command_line"].replace(
            "loader_return_after=60", "chiron_touch_capture=1 loader_return_after=300")
        entries["cmdline"] = proof["command_line"].encode() + b"\0"
        payload = pack(list(entries.items()))
        inputs["payload"] = phone.content_digest(payload)
        proof["home_wake"] = {
            "schema": "chiron-touch-home-wake-v1", "synthetic": False,
            "gesture_count": 0, "home_button_count": 1, "producer_rebuilt": False,
            "physical_touch_qualified": False, "logical_readback_changed": ["/etc/rc.jobs"],
            "root": inputs["small_root"],
        }
        phone.admit_qualification(proof, inputs, payload, True)
        for key, value in (("touch_capture", False), ("touch_active", False),
                           ("touch_hid_replay", True), ("touch_home_wake", "true")):
            bad = copy.deepcopy(proof)
            bad[key] = value
            with self.assertRaises(ValueError, msg=key):
                phone.admit_qualification(bad, {**inputs, "driver_experiment": bad}, payload, True)
        for key, value in (("synthetic", True), ("gesture_count", 1),
                           ("producer_rebuilt", True), ("home_button_count", 2),
                           ("logical_readback_changed", ["/bin/rwtouch", "/etc/rc.jobs"]),
                           ("root", {"bytes": 1, "sha256": "0" * 64})):
            bad = copy.deepcopy(proof)
            bad["home_wake"][key] = value
            with self.assertRaises(ValueError, msg=key):
                phone.admit_qualification(bad, {**inputs, "driver_experiment": bad}, payload, True)

    def test_hid_replay_is_explicit_synthetic_and_excludes_the_physical_producer(self):
        proof, inputs, payload = touch_fixture(
            "cpus=1 epoch=1792760000 chiron_touch=0 loader_return_after=180 touch_lat=1")
        proof.update(probe_only=False, touch_hid_replay=True)
        proof["hid_replay"] = {
            "schema": "chiron-touch-hid-replay-v1",
            "synthetic": True, "consumer_ready_assumed": False, "gesture_count": 1,
            "physical_touch_qualified": False,
            "logical_readback_changed": ["/bin/rwtouch", "/etc/rc.jobs"],
            "root": inputs["small_root"],
        }
        phone.admit_qualification(proof, inputs, payload, True)
        for key, value in (("touch_active", True), ("reset_prepare", True),
                           ("qup_writes", True), ("touch_capture", True),
                           ("touch_hid_replay", False)):
            bad = copy.deepcopy(proof)
            bad[key] = value
            with self.assertRaises(ValueError, msg=key):
                phone.admit_qualification(bad, {**inputs, "driver_experiment": bad}, payload, True)
        for key, value in (("consumer_ready_assumed", True), ("gesture_count", 2),
                           ("physical_touch_qualified", True),
                           ("logical_readback_changed", ["/etc/rc.jobs"])):
            bad = copy.deepcopy(proof)
            bad["hid_replay"][key] = value
            with self.assertRaises(ValueError, msg=key):
                phone.admit_qualification(bad, {**inputs, "driver_experiment": bad}, payload, True)

    def test_capture_requires_new_observer_and_explicit_five_minute_graphics_mode(self):
        proof, inputs, payload = active_fixture()
        entries = phone.payload_entries(payload)
        entries["kernel"] += b"chiron touch: capture layout"
        proof["kernel"] = phone.content_digest(entries["kernel"])
        proof["source_build"]["artifacts"]["kernel.development.hackios"] = proof["kernel"]
        proof["source_build"]["overlays"]["kernel"]["changes"]["touch"][
            "poll_frame_capture_implemented"] = True
        proof["touch_capture"] = True
        proof["command_line"] = proof["command_line"].replace(
            "loader_return_after=60", "chiron_touch_capture=1 loader_return_after=300")
        entries["cmdline"] = proof["command_line"].encode() + b"\0"
        payload = pack(list(entries.items()))
        inputs["payload"] = phone.content_digest(payload)
        phone.admit_qualification(proof, inputs, payload, True)
        for key, value in (("touch_capture", False), ("touch_capture", "true"),
                           ("touch_desktop", True), ("touch_active", False)):
            altered = copy.deepcopy(proof)
            altered[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                phone.admit_qualification(
                    altered, dict(inputs, driver_experiment=altered), payload, True)
        altered = copy.deepcopy(proof)
        altered["source_build"]["overlays"]["kernel"]["changes"]["touch"][
            "poll_frame_capture_implemented"] = False
        with self.assertRaises(ValueError):
            phone.admit_qualification(altered, dict(inputs, driver_experiment=altered), payload, True)
        altered = copy.deepcopy(proof)
        altered["command_line"] = altered["command_line"].replace("=300", "=60")
        changed = pack(list(dict(entries, cmdline=altered["command_line"].encode() + b"\0").items()))
        with self.assertRaises(ValueError):
            phone.admit_qualification(
                altered, dict(inputs, driver_experiment=altered,
                              payload=phone.content_digest(changed)), changed, True)

    def test_desktop_touch_requires_explicit_persistent_mode_and_unchanged_kernel(self):
        proof, inputs, payload = active_fixture()
        entries = phone.payload_entries(payload)
        kernel = entries["kernel"]
        proof["touch_desktop"] = True
        proof["command_line"] = proof["command_line"].replace(" loader_return_after=60", "")
        entries["cmdline"] = proof["command_line"].encode() + b"\0"
        payload = pack(list(entries.items()))
        inputs["payload"] = phone.content_digest(payload)
        phone.admit_qualification(proof, inputs, payload, True)
        self.assertEqual(phone.payload_entries(payload)["kernel"], kernel)
        for value in (False, "true", 1):
            altered = copy.deepcopy(proof)
            altered["touch_desktop"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                phone.admit_qualification(
                    altered, dict(inputs, driver_experiment=altered), payload, True)
        for suffix in (" loader_return_after=60", " loader_return_after=180", " -v"):
            altered = copy.deepcopy(proof)
            altered["command_line"] += suffix
            values = dict(entries, cmdline=altered["command_line"].encode() + b"\0")
            changed = pack(list(values.items()))
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                phone.admit_qualification(
                    altered, dict(inputs, payload=phone.content_digest(changed),
                                  driver_experiment=altered), changed, True)
        altered = copy.deepcopy(proof)
        altered["touch_active"] = False
        with self.assertRaises(ValueError):
            phone.admit_qualification(
                altered, dict(inputs, driver_experiment=altered), payload, True)

    def test_active_touch_requires_new_kernel_retained_power_reset_and_explicit_writes(self):
        proof, inputs, payload = active_fixture()
        phone.admit_qualification(proof, inputs, payload, True)
        for key, value in (("touch_active", False), ("touch_active", "true"),
                           ("probe_only", True), ("qup_writes", False),
                           ("sensor_configuration_writes", False),
                           ("pinmux_writes", False), ("reset_prepare", False),
                           ("read_only", True), ("power_writes", True), ("l6_abi", True)):
            altered = copy.deepcopy(proof)
            altered[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                phone.admit_qualification(
                    altered, dict(inputs, driver_experiment=altered), payload, True)
        for key in ("reset_retained_android_profile", "reset_power_probe_after_usb_start"):
            altered = copy.deepcopy(proof)
            altered["loader_overlay"]["touch"][key] = False
            with self.subTest(key=key), self.assertRaises(ValueError):
                phone.admit_qualification(
                    altered, dict(inputs, driver_experiment=altered), payload, True)
        altered = copy.deepcopy(proof)
        altered["source_build"]["overlays"]["kernel"]["changes"]["touch"][
            "reset_sensor_initialization_implemented"] = False
        with self.assertRaises(ValueError):
            phone.admit_qualification(altered, dict(inputs, driver_experiment=altered), payload, True)
        altered = copy.deepcopy(proof)
        altered["source_build"]["overlays"]["kernel"]["changes"]["touch"][
            "f01_configured_read_zero_implemented"] = False
        with self.assertRaises(ValueError):
            phone.admit_qualification(altered, dict(inputs, driver_experiment=altered), payload, True)
        for key in ("active_object_prefix_read_implemented", "transport_result_diagnostics_implemented"):
            altered = copy.deepcopy(proof)
            altered["source_build"]["overlays"]["kernel"]["changes"]["touch"][key] = False
            with self.subTest(key=key), self.assertRaises(ValueError):
                phone.admit_qualification(altered, dict(inputs, driver_experiment=altered), payload, True)
        proof["source_build"]["overlays"]["kernel"]["changes"]["touch"][
            "active_pinmux_prepare_implemented"] = False
        with self.assertRaisesRegex(ValueError, "active touch"):
            phone.admit_qualification(proof, inputs, payload, True)

    def test_active_touch_rejects_old_kernel_and_diagnostic_arguments(self):
        proof, inputs, payload = active_fixture()
        entries = phone.payload_entries(payload)
        entries["kernel"] = entries["kernel"].replace(b"chiron touch: pinmux prepare result=", b"")
        altered = pack(list(entries.items()))
        proof["kernel"] = phone.content_digest(entries["kernel"])
        proof["source_build"]["artifacts"]["kernel.development.hackios"] = proof["kernel"]
        inputs["payload"] = phone.content_digest(altered)
        with self.assertRaisesRegex(ValueError, "active pinmux"):
            phone.admit_qualification(proof, inputs, altered, True)
        proof, inputs, payload = active_fixture()
        entries = phone.payload_entries(payload)
        proof["command_line"] = proof["command_line"].replace(
            "chiron_touch=1", "chiron_touch_pinmux_probe=1")
        entries["cmdline"] = proof["command_line"].encode() + b"\0"
        altered = pack(list(entries.items()))
        inputs["payload"] = phone.content_digest(altered)
        with self.assertRaisesRegex(ValueError, "arguments"):
            phone.admit_qualification(proof, inputs, altered, True)
        for argument in (" loader_return_after=60", " touch_lat=1"):
            proof, inputs, payload = active_fixture()
            entries = phone.payload_entries(payload)
            proof["command_line"] = proof["command_line"].replace(argument, "")
            entries["cmdline"] = proof["command_line"].encode() + b"\0"
            altered = pack(list(entries.items()))
            inputs["payload"] = phone.content_digest(altered)
            with self.subTest(argument=argument), self.assertRaisesRegex(ValueError, "arguments"):
                phone.admit_qualification(proof, inputs, altered, True)
        proof, inputs, payload = active_fixture()
        entries = phone.payload_entries(payload)
        proof["command_line"] = proof["command_line"].replace(" chiron_touch=1", " -v chiron_touch=1")
        entries["cmdline"] = proof["command_line"].encode() + b"\0"
        altered = pack(list(entries.items()))
        inputs["payload"] = phone.content_digest(altered)
        with self.assertRaisesRegex(ValueError, "arguments"):
            phone.admit_qualification(proof, inputs, altered, True)

    def test_l6_capture_requires_shadow_dispatch_and_kernel_disabled(self):
        proof, inputs, payload = touch_fixture()
        proof.update(l6_abi=True, l6_capture=True, read_only=True)
        proof["loader_overlay"]["touch"] = {
            "l6_abi_implemented": True, "kernel_start_disabled": True,
            "l6_capture_implemented": True, "calculator_spmi_hardware_calls": False,
            "firmware_dispatch_redirected": True,
        }
        phone.admit_qualification(proof, inputs, payload, True)
        for key, value in (("l6_capture_implemented", False),
                           ("calculator_spmi_hardware_calls", True),
                           ("firmware_dispatch_redirected", False),
                           ("kernel_start_disabled", False)):
            altered = copy.deepcopy(proof)
            altered["loader_overlay"]["touch"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                phone.admit_qualification(
                    altered, dict(inputs, driver_experiment=altered), payload, True)

    def test_l6_abi_snapshot_requires_read_only_loader_and_disabled_kernel(self):
        proof, inputs, payload = touch_fixture()
        proof.update(l6_abi=True, read_only=True)
        proof["loader_overlay"]["touch"] = {
            "l6_abi_implemented": True, "kernel_start_disabled": True,
        }
        phone.admit_qualification(proof, inputs, payload, True)
        for key, value in (("l6_abi", "true"), ("read_only", False), ("power_writes", True),
                           ("reset_writes", True)):
            altered = copy.deepcopy(proof)
            altered[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                phone.admit_qualification(altered, dict(inputs, driver_experiment=altered), payload, True)
        proof["loader_overlay"]["touch"]["kernel_start_disabled"] = False
        with self.assertRaisesRegex(ValueError, "ABI"):
            phone.admit_qualification(proof, inputs, payload, True)

    def test_reset_probe_requires_loader_support_and_an_explicit_command(self):
        proof, inputs, payload = clock_fixture()
        entries = phone.payload_entries(payload)
        entries["kernel"] += b"CHIRON TOUCH PINMUX PROBE"
        proof["command_line"] = ("cpus=1 epoch=1792760000 -v chiron_touch_pinmux_probe=1"
                                 " chiron_touch_reset=1")
        entries["cmdline"] = proof["command_line"].encode() + b"\0"
        payload = pack(list(entries.items()))
        kernel = phone.content_digest(entries["kernel"])
        proof["kernel"] = kernel
        proof["source_build"]["artifacts"]["kernel.development.hackios"] = kernel
        proof["source_build"]["overlays"]["kernel"]["changes"]["touch"]["pinmux_prepare_implemented"] = True
        proof["loader_overlay"]["touch"] = {"reset_prepare_implemented": True}
        proof.update(pinmux_prepare=True, pinmux_writes=True, reset_prepare=True, reset_writes=True)
        inputs["payload"] = phone.content_digest(payload)
        phone.admit_qualification(proof, inputs, payload, True)
        for key, value in (("reset_prepare", False), ("reset_prepare", "true"), ("reset_writes", False)):
            altered = copy.deepcopy(proof)
            altered[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                phone.admit_qualification(altered, dict(inputs, driver_experiment=altered), payload, True)
        proof["loader_overlay"]["touch"]["reset_prepare_implemented"] = False
        with self.assertRaisesRegex(ValueError, "reset"):
            phone.admit_qualification(proof, inputs, payload, True)

    def test_l6_probe_requires_explicit_power_write_proof(self):
        proof, inputs, payload = clock_fixture()
        entries = phone.payload_entries(payload)
        entries["kernel"] += b"CHIRON TOUCH PINMUX PROBE"
        proof["command_line"] = ("cpus=1 epoch=1792760000 -v chiron_touch_pinmux_probe=1"
                                 " chiron_touch_reset=1 chiron_touch_l6=1")
        entries["cmdline"] = proof["command_line"].encode() + b"\0"
        payload = pack(list(entries.items()))
        kernel = phone.content_digest(entries["kernel"])
        proof["kernel"] = kernel
        proof["source_build"]["artifacts"]["kernel.development.hackios"] = kernel
        proof["source_build"]["overlays"]["kernel"]["changes"]["touch"]["pinmux_prepare_implemented"] = True
        proof["loader_overlay"]["touch"] = {
            "reset_prepare_implemented": True, "l6_prepare_implemented": True,
            "power_write_policy": "LDO6-only-1848000-to-1800000-uV-with-reset-low",
        }
        proof.update(pinmux_prepare=True, pinmux_writes=True, reset_prepare=True, reset_writes=True,
                     l6_prepare=True, power_writes=True)
        inputs["payload"] = phone.content_digest(payload)
        phone.admit_qualification(proof, inputs, payload, True)
        for key, value in (("l6_prepare", False), ("l6_prepare", "true"), ("power_writes", False)):
            altered = copy.deepcopy(proof)
            altered[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                phone.admit_qualification(altered, dict(inputs, driver_experiment=altered), payload, True)
        proof["loader_overlay"]["touch"]["l6_prepare_implemented"] = False
        with self.assertRaisesRegex(ValueError, "L6"):
            phone.admit_qualification(proof, inputs, payload, True)

    def test_pinmux_probe_requires_its_own_write_proof_and_command(self):
        proof, inputs, payload = clock_fixture()
        entries = phone.payload_entries(payload)
        entries["kernel"] += b"CHIRON TOUCH PINMUX PROBE"
        proof["command_line"] = "cpus=1 epoch=1792760000 -v chiron_touch_pinmux_probe=1"
        entries["cmdline"] = proof["command_line"].encode() + b"\0"
        payload = pack(list(entries.items()))
        kernel = phone.content_digest(entries["kernel"])
        proof["kernel"] = kernel
        proof["source_build"]["artifacts"]["kernel.development.hackios"] = kernel
        proof["source_build"]["overlays"]["kernel"]["changes"]["touch"]["pinmux_prepare_implemented"] = True
        proof.update(pinmux_prepare=True, pinmux_writes=True)
        inputs["payload"] = phone.content_digest(payload)
        phone.admit_qualification(proof, inputs, payload, True)
        for key, value in (("pinmux_prepare", False), ("pinmux_writes", False),
                           ("clock_prepare", False), ("pinmux_prepare", "true")):
            altered = copy.deepcopy(proof)
            altered[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                phone.admit_qualification(altered, dict(inputs, driver_experiment=altered), payload, True)
        proof["source_build"]["overlays"]["kernel"]["changes"]["touch"]["pinmux_prepare_implemented"] = False
        with self.assertRaisesRegex(ValueError, "pinmux"):
            phone.admit_qualification(proof, inputs, payload, True)

    def test_both_source_experiments_bind_actual_kernel_root_and_cpu_arguments(self):
        for count in (2, 8):
            proof, inputs, payload = fixture(count)
            result = phone.admit_qualification(proof, inputs, payload, True)
            self.assertEqual(result["cpu_count"], count)
            self.assertFalse(result["runtime_qualified"])

    def test_baseline_gate_is_preserved_and_cannot_qualify_driver_inputs(self):
        self.assertIsNone(phone.admit_qualification({"cortex_a53_no_pan_lse_desktop": True}, {}, None))
        with self.assertRaisesRegex(ValueError, "desktop"):
            phone.admit_qualification({}, {}, None)
        _, inputs, payload = fixture()
        with self.assertRaisesRegex(ValueError, "explicit"):
            phone.admit_qualification({"cortex_a53_no_pan_lse_desktop": True}, inputs, payload)

    def test_source_proof_cannot_inherit_runtime_success_or_reuse_kernel(self):
        for key in ("runtime_qualified", "hardware_observed", "phone_boot_ready", "kernel_reused"):
            proof, inputs, payload = fixture()
            proof["source_build"][key] = True
            with self.subTest(key=key), self.assertRaises(ValueError):
                phone.admit_qualification(proof, inputs, payload, True)

    def test_changed_kernel_is_rejected_even_with_updated_whole_payload_digest(self):
        proof, inputs, payload = fixture()
        entries = phone.payload_entries(payload)
        entries["kernel"] += b"modified kernel"
        changed = pack(list(entries.items()))
        inputs["payload"] = phone.content_digest(changed)
        with self.assertRaisesRegex(ValueError, "kernel differs"):
            phone.admit_qualification(proof, inputs, changed, True)

    def test_conflicting_cpu_arguments_or_profile_are_rejected(self):
        for command in ("cpus=2 cpus=1 epoch=1", "cpus=8 epoch=1", "cpus=2\t epoch=1"):
            proof, inputs, payload = fixture(command=command)
            with self.subTest(command=command), self.assertRaises(ValueError):
                phone.admit_qualification(proof, inputs, payload, True)
        proof, inputs, payload = fixture()
        inputs["profile"]["cpu_count"] = 8
        with self.assertRaisesRegex(ValueError, "profile"):
            phone.admit_qualification(proof, inputs, payload, True)

    def test_payload_boundaries_duplicates_and_hidden_modes_are_rejected(self):
        _, _, payload = fixture()
        entries = list(phone.payload_entries(payload).items())
        cases = [
            payload[:-1],
            pack(entries + [entries[0]]),
            pack(entries + [("checkpoint", b"999\0")]),
            payload + bytes(16),
        ]
        for data in cases:
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                phone.payload_entries(data)
        _, _, valid = fixture()
        altered = bytearray(valid)
        # The root fixture is 18 bytes; locate its first padding byte.
        offset = 8 + 24 + len(entries[0][1]) + 24 + len(entries[1][1])
        altered[offset] = 1
        with self.assertRaisesRegex(ValueError, "padding"):
            phone.payload_entries(altered)

    def test_source_folder_checks_actual_hashes_run_and_smp_abi(self):
        proof, _, payload = fixture()
        kernel = phone.payload_entries(payload)["kernel"]
        with tempfile.TemporaryDirectory(prefix="chiron-driver-proof-") as temporary:
            folder = Path(temporary)
            (folder / "kernel.development.hackios").write_bytes(kernel)
            for name, data in (("kernel.development.hackios.unstripped", kernel), ("loader.efi", b"MZfixture")):
                (folder / name).write_bytes(data)
                proof["source_build"]["artifacts"][name] = phone.content_digest(data)
            (folder / "kernel-undefined.txt").write_text("")
            (folder / "driver-build.json").write_text(json.dumps(proof["source_build"]))
            phone.verify_source(folder, "smp2", "123")
            with self.assertRaisesRegex(ValueError, "run"):
                phone.verify_source(folder, "smp2", "124")
            (folder / "kernel.development.hackios").write_bytes(kernel + b"x")
            with self.assertRaisesRegex(ValueError, "digest"):
                phone.verify_source(folder, "smp2", "123")
        with self.assertRaisesRegex(ValueError, "ABI"):
            phone.check_kernel(kernel[:-16])

    def test_mismatched_proof_is_rejected_before_packaging(self):
        proof, inputs, payload = fixture()
        changed = copy.deepcopy(proof)
        changed["command_line"] += " debug=1"
        with self.assertRaisesRegex(ValueError, "proof differs"):
            phone.admit_qualification(changed, inputs, payload, True)

    def test_touch_probe_requires_explicit_board_support_and_read_only_mode(self):
        proof, inputs, payload = touch_fixture()
        result = phone.admit_qualification(proof, inputs, payload, True)
        self.assertEqual(result["variant"], "touch")
        self.assertEqual(result["cpu_count"], 1)
        altered = copy.deepcopy(proof)
        altered["source_build"]["overlays"]["kernel"]["changes"]["touch"]["board_probe_implemented"] = False
        changed_inputs = dict(inputs, driver_experiment=altered)
        with self.assertRaisesRegex(ValueError, "board probe"):
            phone.admit_qualification(altered, changed_inputs, payload, True)
        proof["probe_only"] = False
        with self.assertRaisesRegex(ValueError, "board probe"):
            phone.admit_qualification(proof, inputs, payload, True)

    def test_touch_probe_cannot_silently_start_active_transport(self):
        for command in ("cpus=1 epoch=1 chiron_touch=1",
                        "cpus=1 epoch=1 chiron_touch_probe=1 chiron_touch=1"):
            proof, inputs, payload = touch_fixture(command)
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, "arguments"):
                phone.admit_qualification(proof, inputs, payload, True)

    def test_clock_probe_has_a_distinct_explicit_write_mode(self):
        proof, inputs, payload = clock_fixture()
        self.assertEqual(phone.admit_qualification(proof, inputs, payload, True)["variant"], "touch")
        for key, value in (("read_only", True), ("clock_writes", False), ("clock_prepare", "true")):
            altered = copy.deepcopy(proof)
            altered[key] = value
            changed_inputs = dict(inputs, driver_experiment=altered)
            with self.subTest(key=key), self.assertRaises(ValueError):
                phone.admit_qualification(altered, changed_inputs, payload, True)
        proof["source_build"]["overlays"]["kernel"]["changes"]["touch"]["clock_prepare_implemented"] = False
        with self.assertRaisesRegex(ValueError, "write-mode"):
            phone.admit_qualification(proof, inputs, payload, True)

    def test_read_only_proof_cannot_smuggle_the_clock_probe_argument(self):
        proof, inputs, payload = touch_fixture(
            "cpus=1 epoch=1792760000 -v chiron_touch_clock_probe=1")
        with self.assertRaisesRegex(ValueError, "arguments"):
            phone.admit_qualification(proof, inputs, payload, True)


if __name__ == "__main__":
    unittest.main()
