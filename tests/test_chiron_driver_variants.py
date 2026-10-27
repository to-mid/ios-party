"""Check that independent driver builds cannot inherit baseline kernel evidence."""

import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ci/hackios"))
variants = importlib.import_module("driver_variants")


class VariantTests(unittest.TestCase):
    def test_baseline_does_not_import_or_apply_an_experimental_driver(self):
        with patch.object(variants.importlib, "import_module", side_effect=AssertionError):
            report = variants.overlay("kernel", Path("/unused"), "baseline")
        self.assertEqual(report["cpu_count"], 1)
        self.assertEqual(report["changes"], {})

    def test_invalid_selection_and_reused_kernels_are_rejected_before_editing(self):
        for name in ("", "smp3", "../touch", "combined"):
            with self.assertRaises(ValueError):
                variants.selection(name)
        for variable in ("HACKIOS_REUSE_KERNEL_DIR", "HACKIOS_CHIRON_KERNEL_DIR"):
            with patch.dict(os.environ, {variable: "/old-kernel"}):
                for variant in ("touch", "touch-smp2", "touch-smp8", "smp2", "smp4", "smp8"):
                    with self.subTest(variable=variable, variant=variant):
                        with self.assertRaisesRegex(ValueError, "fresh source kernel"):
                            variants.overlay("kernel", Path("/unused"), variant)

    def test_smp_and_touch_call_only_their_own_source_overlay(self):
        source = Path("/source")
        called = []
        smp = types.SimpleNamespace(
            loader_overlay=lambda path, count: called.append(("smp", path, count)) or {"cores": count},
            kernel_overlay=lambda path: called.append(("smp-kernel", path)) or {"changed": True},
        )
        touch = types.SimpleNamespace(
            kernel_overlay=lambda path: called.append(("touch", path)) or {"transport_ready": False},
        )
        with patch.dict(sys.modules, {"apply_chiron_smp": smp, "apply_chiron_touch": touch}):
            smp_report = variants.overlay("loader", source, "smp8")
            touch_report = variants.overlay("kernel", source, "touch")
        self.assertEqual(called, [("smp", source, 8), ("touch", source)])
        self.assertEqual(smp_report["cpu_count"], 8)
        self.assertEqual(touch_report["cpu_count"], 1)
        self.assertFalse(touch_report["phone_boot_ready"])
        self.assertFalse(smp_report["runtime_qualified"])

    def test_single_core_runtime_qualification_rejects_each_experiment(self):
        variants.require_baseline_runtime("baseline")
        for variant in ("touch", "touch-smp2", "touch-smp8", "smp2", "smp4", "smp8"):
            with self.assertRaisesRegex(ValueError, "single-core"):
                variants.require_baseline_runtime(variant)

    def test_combined_touch_smp_keeps_both_overlays_and_strict_firmware_return(self):
        source = Path("/source")
        called = []
        smp = types.SimpleNamespace(
            loader_overlay=lambda path, count: called.append(("smp-loader", count)) or {},
            kernel_overlay=lambda path, strict_firmware_return=False:
                called.append(("smp-kernel", strict_firmware_return)) or {},
        )
        touch = types.SimpleNamespace(
            loader_overlay=lambda path: called.append(("touch-loader",)) or {},
            kernel_overlay=lambda path: called.append(("touch-kernel",)) or {},
        )
        for count in (2, 8):
            called.clear()
            with patch.dict(sys.modules, {"apply_chiron_smp": smp, "apply_chiron_touch": touch}):
                report = variants.overlay("kernel", source, f"touch-smp{count}")
                loader = variants.overlay("loader", source, f"touch-smp{count}")
            self.assertEqual(called, [("smp-kernel", True), ("touch-kernel",),
                                      ("smp-loader", count), ("touch-loader",)])
            self.assertEqual(report["cpu_count"], count)
            self.assertEqual(set(report["changes"]), {"smp", "touch"})
            self.assertEqual(set(loader["changes"]), {"smp", "touch"})
            self.assertFalse(report["runtime_qualified"])

    def test_legacy_pipeline_refuses_experiments_before_images_or_guest_execution(self):
        pipeline = importlib.import_module("chiron")
        with patch.dict(os.environ, {"HACKIOS_CHIRON_VARIANT": "touch"}), \
                patch.object(pipeline.cloud, "images") as images, \
                patch.object(pipeline.cloud, "qemu") as qemu, \
                patch.object(pipeline.cloud, "summarize") as summarize:
            for function in (pipeline.images, pipeline.qemu, pipeline.summarize):
                with self.assertRaisesRegex(ValueError, "single-core"):
                    function(Path("/unused"), Path("/unused"))
            images.assert_not_called()
            qemu.assert_not_called()
            summarize.assert_not_called()

    def test_build_manifest_binds_variant_stages_and_actual_compiler_outputs(self):
        with tempfile.TemporaryDirectory(prefix="chiron-variant-") as temporary:
            root = Path(temporary)
            for name in ("kernel.development.hackios", "kernel.development.hackios.unstripped", "loader.efi"):
                (root / name).write_bytes(name.encode())
            (root / "kernel-undefined.txt").write_text("")
            for stage in ("loader", "kernel"):
                (root / f"driver-{stage}-overlay.json").write_text(json.dumps(
                    {"variant": "smp2", "stage": stage, "changes": {}}
                ))
            report = variants.record_build(root, "smp2")
            self.assertEqual(report["artifacts"]["loader.efi"], variants.digest(root / "loader.efi"))
            self.assertFalse(report["kernel_reused"])
            self.assertFalse(report["runtime_qualified"])
            self.assertFalse(report["phone_boot_ready"])
            with self.assertRaisesRegex(ValueError, "provenance"):
                variants.record_build(root, "smp8")
            (root / "kernel-undefined.txt").write_text("_missing_driver_symbol\n")
            with self.assertRaisesRegex(ValueError, "undefined-symbol"):
                variants.record_build(root, "smp2")


if __name__ == "__main__":
    unittest.main()
