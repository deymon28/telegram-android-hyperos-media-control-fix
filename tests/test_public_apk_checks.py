"""Public artifact checks use synthetic credentials and need no Android SDK."""
import importlib.util
import json
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET
import zipfile


SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import privacy_checks

SPEC = importlib.util.spec_from_file_location("public_apk_checker", SCRIPTS / "check-public-apk.py")
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)

MANIFEST = '''N: android=http://schemas.android.com/apk/res/android (line=2)
  E: manifest (line=2)
    A: package="org.telegram.tgmedia.web" (Raw: "org.telegram.tgmedia.web")
    A: android:versionCode(0x0101021b)=70395
    A: android:versionName(0x0101021c)="12.10.1-test.2"
    E: application (line=4)
      A: android:allowBackup(0x01010280)=false
      A: android:fullBackupContent(0x010104eb)=@0x7f12000a
      A: android:dataExtractionRules(0x0101063e)=@0x7f12000b
      E: activity (line=6)
        A: android:name(0x01010003)="org.telegram.ui.ApiSetupActivity"
        A: android:exported(0x01010010)=false
'''
DEX_XML = '''<api>
<package name="org.telegram.ui">
  <class name="ApiSetupActivity" abstract="false" visibility="public">
    <constructor name="ApiSetupActivity" visibility="public" />
  </class>
</package>
<package name="org.telegram.messenger">
  <class name="BuildConfig">
    <field name="TG_API_ID" type="int" static="true" final="true" value="0" />
    <field name="TG_API_HASH" type="java.lang.String" static="true" final="true" value="" />
    <field name="TG_RUNTIME_API_CONFIG" type="boolean" static="true" final="true" value="true" />
  </class>
</package>
</api>'''


def arm64_elf():
    data = bytearray(64)
    data[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", data, 18, 183)
    return bytes(data)


def fake_apk(path, extras=()):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"synthetic manifest")
        archive.writestr("classes.dex", b"Lorg/telegram/ui/ApiSetupActivity; Lorg/telegram/messenger/BuildConfig;")
        archive.writestr("lib/arm64-v8a/libtmessages.49.so", arm64_elf())
        for name, data in extras:
            archive.writestr(name, data)


class PrivacyScanTests(unittest.TestCase):
    def test_exact_private_values_in_utf8_and_both_utf16_orders(self):
        private = "synthetic-private-value-4862"
        for codec in ("utf-8", "utf-16le", "utf-16be"):
            with self.subTest(codec=codec):
                findings = privacy_checks.scan_bytes(private.encode(codec), "assets/file.bin", [private])
                self.assertEqual(findings, [{"category": "private-value", "label": "assets/file.bin"}])
                self.assertNotIn(private, json.dumps(findings))

    def test_local_paths_and_utf16_are_rejected_without_exposing_identity(self):
        windows = "C:" + chr(92) + "Users" + chr(92) + "private-synthetic-owner" + chr(92) + "project"
        unix = "/" + "home/" + "private-synthetic-owner/project"
        for path in (windows, unix):
            for codec in ("utf-8", "utf-16le", "utf-16be"):
                findings = privacy_checks.scan_bytes(path.encode(codec), "library.so")
                self.assertTrue(findings)
                self.assertNotIn("private-synthetic-owner", json.dumps(findings))

    def test_example_paths_do_not_override_exact_private_value_denials(self):
        path = "/" + "home/runner/project"
        self.assertEqual(privacy_checks.scan_bytes(path.encode(), "sample.txt"), [])
        self.assertEqual(privacy_checks.scan_bytes(path.encode(), "sample.txt", ["runner"])[0]["category"], "private-value")

    def test_private_key_and_tokens_are_detected(self):
        payloads = [
            ("-----BEGIN " + "PRIVATE KEY-----").encode(),
            ("ghp_" + "A" * 36).encode(),
            ("12345678" + ":" + "A" * 35).encode(),
        ]
        for data in payloads:
            findings = privacy_checks.scan_bytes(data, "embedded.bin")
            self.assertTrue(findings)
            self.assertNotIn(data.decode(), json.dumps(findings))

    def test_labels_and_file_failures_do_not_disclose_private_directory(self):
        label = "C:" + "/" + "Users/private-synthetic-owner/private-value.bin"
        findings = privacy_checks.scan_bytes(b"private-value", label, ["private-value"])
        self.assertEqual(findings[0]["label"], "<redacted>.bin")
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "input.bin"
            path.write_bytes(b"synthetic-private")
            self.assertEqual(privacy_checks.scan_file(path, deny_values=["synthetic-private"])[0]["label"], "input.bin")
            with self.assertRaises(ValueError) as error:
                privacy_checks.scan_file(path.with_name("missing.bin"))
            self.assertNotIn(folder, str(error.exception))
        token = "ghp_" + "A" * 36
        findings = privacy_checks.scan_bytes(token.encode(), "assets/" + token)
        self.assertEqual(findings[0]["label"], "assets/<redacted>")


class PublicApkChecksTests(unittest.TestCase):
    def test_real_manifest_attribute_binding_and_requested_version(self):
        manifest = CHECKER._parse_xmltree(MANIFEST)
        CHECKER._manifest_checks(manifest, "12.10.1-test.2", 70395)
        for name, value in (("package", "other.application"), ("android:versionName", "old"), ("android:versionCode", "1")):
            changed = CHECKER._parse_xmltree(MANIFEST)
            changed.set(name, value)
            with self.subTest(attribute=name), self.assertRaises(ValueError):
                CHECKER._manifest_checks(changed, "12.10.1-test.2", 70395)

    def test_wrong_package_cannot_hide_behind_expected_text_elsewhere(self):
        manifest = CHECKER._parse_xmltree(MANIFEST)
        manifest.set("package", "other.application")
        manifest.find("application").set("android:label", CHECKER.PACKAGE)
        with self.assertRaises(ValueError):
            CHECKER._manifest_checks(manifest)

    def test_backup_debug_test_and_export_flags_fail_closed(self):
        for attribute in ("allowBackup", "debuggable", "testOnly"):
            manifest = CHECKER._parse_xmltree(MANIFEST)
            manifest.find("application").set("android:" + attribute, "true")
            with self.subTest(attribute=attribute), self.assertRaises(ValueError):
                CHECKER._manifest_checks(manifest)
        manifest = CHECKER._parse_xmltree(MANIFEST)
        manifest.find("application/activity").set("android:exported", "true")
        with self.assertRaises(ValueError):
            CHECKER._manifest_checks(manifest)

    def test_runtime_configuration_and_setup_are_checked_in_correct_package(self):
        self.assertEqual(CHECKER._dex_checks(DEX_XML), (True, True))
        replacements = [
            ('value="0"', 'value="12345678"'),
            ('value=""', 'value="synthetic-private-api-hash"'),
            ('value="true"', 'value="false"'),
            ('abstract="false"', 'abstract="true"'),
            ('<constructor name="ApiSetupActivity" visibility="public" />', ''),
        ]
        for old, new in replacements:
            with self.subTest(changed=old), self.assertRaises(ValueError):
                CHECKER._dex_checks(DEX_XML.replace(old, new))
        self.assertEqual(CHECKER._dex_checks(DEX_XML.replace('name="org.telegram.messenger"', 'name="unrelated.library"')), (True, False))

    def test_private_entries_paths_and_wrong_native_architectures_are_rejected(self):
        for name in ("assets/private.pfx", "assets/.env.local", "assets/telegram-api.json", "assets/release-signing.json", "../outside", ".git/config", "C:/outside"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                CHECKER._entry_checks(name, b"placeholder", ())
        for name, data in (("lib/x86_64/libtest.so", arm64_elf()), ("lib/arm64-v8a/libtest.so", b"not an ELF")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                CHECKER._entry_checks(name, data, ())
        CHECKER._entry_checks("META-INF/com/android/build/gradle/app-metadata.properties", b"safe", ())

    def test_full_check_accepts_synthetic_public_apk_and_rejects_secret_asset(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            apk = root / "public.apk"
            fake_apk(apk)
            with mock.patch.object(CHECKER, "_tool_output", side_effect=lambda _, tool, args: MANIFEST if tool == "aapt2" else DEX_XML):
                CHECKER.check_apk(apk, root, version_name="12.10.1-test.2", version_code=70395)
                fake_apk(apk, [("assets/content.bin", b"synthetic-secret-1479")])
                with self.assertRaisesRegex(ValueError, "private-value") as error:
                    CHECKER.check_apk(apk, root, deny_values=["synthetic-secret-1479"])
                self.assertNotIn("synthetic-secret-1479", str(error.exception))

    def test_missing_compiled_configuration_cannot_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            apk = root / "public.apk"
            fake_apk(apk)
            dex = DEX_XML.replace('name="org.telegram.messenger"', 'name="unrelated.library"')
            with mock.patch.object(CHECKER, "_tool_output", side_effect=lambda _, tool, args: MANIFEST if tool == "aapt2" else dex):
                with self.assertRaisesRegex(ValueError, "could not be verified"):
                    CHECKER.check_apk(apk, root)

    def test_security_gate_survives_optimized_python(self):
        program = '''
import importlib.util, pathlib, sys, xml.etree.ElementTree as ET
scripts = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(scripts))
spec = importlib.util.spec_from_file_location('checker', scripts / 'check-public-apk.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
try:
    checker._manifest_checks(ET.fromstring('<manifest package="wrong.package" />'))
except ValueError:
    print('PASS: optimized Python retained the security gate')
else:
    raise SystemExit('Security gate was skipped')
'''
        result = subprocess.run([sys.executable, "-O", "-c", program, str(SCRIPTS)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, "Optimized Python bypassed a security check.")
        self.assertIn("retained the security gate", result.stdout)


if __name__ == "__main__":
    unittest.main()
