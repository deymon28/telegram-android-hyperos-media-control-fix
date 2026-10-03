"""Test autobuild release gates without network, signing, or an Android build."""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[1]
with mock.patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]):
    import source_tools

    SPEC = importlib.util.spec_from_file_location("autobuild_under_test", ROOT / "scripts/autobuild.py")
    autobuild = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(autobuild)


class ProcessRunnerTest(unittest.TestCase):
    def test_child_output_redacts_the_signing_password(self):
        secret = "synthetic-runtime-signing-password"
        output, log = io.StringIO(), io.StringIO()
        with redirect_stdout(output):
            autobuild.run([sys.executable, "-c", "import os; print(os.environ['TG_SIGNING_PASSWORD'])"],
                          env=dict(os.environ, TG_SIGNING_PASSWORD=secret), log=log, timeout=10)
        self.assertNotIn(secret, output.getvalue() + log.getvalue())
        self.assertIn("[REDACTED]", log.getvalue())

    def test_stalled_child_is_stopped_at_the_deadline(self):
        started = time.monotonic()
        with redirect_stdout(io.StringIO()), self.assertRaises(autobuild.BuildError):
            autobuild.run([sys.executable, "-c", "import time; time.sleep(60)"], timeout=0.2)
        self.assertLess(time.monotonic() - started, 15)


class BuildFixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="tg-autobuild-tests-")
        self.addCleanup(directory.cleanup)
        self.work = Path(directory.name)
        self.project = self.work / "project"
        self.source = self.work / "source"
        self.source.mkdir()
        (self.project / "patches").mkdir(parents=True)
        (self.project / "overlay").mkdir()
        self.write(self.project / "patches/tg-media.patch", "synthetic patch\n")
        self.write(self.project / "overlay/Feature.java", "class Feature {}\n")
        self.write(self.project / "README.md", "Synthetic project\n")
        self.write(self.source / "gradle.properties", "APP_VERSION_NAME=1.2.3\nAPP_VERSION_CODE=123\n")
        self.write(self.source / "gradle/wrapper/gradle-wrapper.properties", "distributionUrl=https\\://services.gradle.org/distributions/gradle-8.11.1-bin.zip\n")
        for module in (self.source, self.source / "TMessagesProj", self.source / "TMessagesProj_AppStandalone"):
            self.write(module / "build.gradle", "// Synthetic build script.\n")
        self.write(self.source / "TMessagesProj/src/main/java/Existing.java", "class Existing {}\n")
        self.write(self.source / "sources.lock.json", json.dumps([
            {"repository": "DrKLO/Telegram", "revision": "1" * 40, "path": "."},
        ]))
        root_patch = mock.patch.object(autobuild, "ROOT", self.project)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.output = io.StringIO()
        redirect = redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    @staticmethod
    def write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")

    def snapshot(self):
        source_tools.snapshot(self.source, self.project)
        return source_tools.verify_prepared(self.source, self.project)


class VersionTest(BuildFixture):
    def test_default_version_reserves_two_digits_for_patch_revision(self):
        self.assertEqual(autobuild.version(self.source, 2), ("1.2.3-hyperos.2", 12302))
        self.assertEqual(autobuild.version(self.source, 99), ("1.2.3-hyperos.99", 12399))

    def test_explicit_name_and_code_override_upstream_values(self):
        self.assertEqual(autobuild.version(self.source, 2, "custom.4", 2100000000), ("custom.4", 2100000000))
        self.assertEqual(autobuild.version(self.source, 2, code=1), ("1.2.3-hyperos.2", 1))

    def test_unsafe_names_and_out_of_range_android_codes_fail(self):
        for name in ("../output", "release/name", "release name", "-release", "a" * 81):
            with self.subTest(name=name):
                with self.assertRaises(autobuild.BuildError):
                    autobuild.version(self.source, 2, name=name)
        for code in (-1, 0, 2100000001):
            with self.subTest(code=code):
                with self.assertRaises(autobuild.BuildError):
                    autobuild.version(self.source, 2, code=code)

    def test_default_code_cannot_overflow_android_limit(self):
        self.write(self.source / "gradle.properties", "APP_VERSION_NAME=1.2.3\nAPP_VERSION_CODE=21000000\n")
        with self.assertRaises(autobuild.BuildError):
            autobuild.version(self.source, 1)

    def test_cli_rejects_invalid_revision_and_worker_bounds(self):
        for option, value in (("--revision", "0"), ("--revision", "100"), ("--workers", "0"), ("--workers", "17")):
            with self.subTest(option=option, value=value), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as failure:
                    autobuild.parser().parse_args([option, value])
                self.assertEqual(failure.exception.code, 2)


class EnvironmentTest(unittest.TestCase):
    def test_required_toolchain_variables_are_checked(self):
        for values in ({}, {"JAVA_HOME": "fixture-jdk"}, {"ANDROID_HOME": "fixture-sdk"}):
            with self.subTest(names=tuple(values)), mock.patch.dict(os.environ, values, clear=True):
                with self.assertRaises(autobuild.BuildError):
                    autobuild.environment()

    def test_embedded_api_credentials_are_rejected_without_printing_values(self):
        for key in ("TG_API_ID", "TG_API_HASH", "ORG_GRADLE_PROJECT_TG_API_ID", "ORG_GRADLE_PROJECT_TG_API_HASH"):
            values = {"JAVA_HOME": "fixture-jdk", "ANDROID_HOME": "fixture-sdk", key: "synthetic-sensitive-value"}
            with self.subTest(key=key), mock.patch.dict(os.environ, values, clear=True):
                with self.assertRaises(autobuild.BuildError) as failure:
                    autobuild.environment()
                self.assertNotIn("synthetic-sensitive-value", str(failure.exception))

    def test_environment_adds_jdk_and_encoding_without_mutating_caller(self):
        values = {"JAVA_HOME": "fixture-jdk", "ANDROID_HOME": "fixture-sdk", "PATH": "fixture-tools"}
        with mock.patch.dict(os.environ, values, clear=True):
            result = autobuild.environment()
            self.assertEqual(dict(os.environ), values)
        self.assertEqual(result["PATH"], str(Path("fixture-jdk") / "bin") + os.pathsep + "fixture-tools")
        self.assertEqual(result["PYTHONIOENCODING"], "utf-8")


class GeneratedConfigTest(BuildFixture):
    def config(self, content):
        self.write(self.source / "TMessagesProj/build/generated/source/buildConfig/afat/org/telegram/BuildConfig.java", content)

    def test_runtime_configuration_has_empty_compile_time_api_values(self):
        self.config('public static final boolean TG_RUNTIME_API_CONFIG = true;\npublic static final int TG_API_ID = 0;\npublic static final String TG_API_HASH = "";\n')
        autobuild.generated_api_check(self.source)

    def test_missing_runtime_configuration_or_embedded_values_fail(self):
        invalid = (
            'public static final boolean TG_RUNTIME_API_CONFIG = false;\npublic static final int TG_API_ID = 0;\npublic static final String TG_API_HASH = "";\n',
            'public static final boolean TG_RUNTIME_API_CONFIG = true;\npublic static final int TG_API_ID = 12;\npublic static final String TG_API_HASH = "";\n',
            'public static final boolean TG_RUNTIME_API_CONFIG = true;\npublic static final int TG_API_ID = 0;\npublic static final String TG_API_HASH = "fixture";\n',
        )
        with self.assertRaises(autobuild.BuildError):
            autobuild.generated_api_check(self.source)
        for content in invalid:
            with self.subTest(content=content):
                self.config(content)
                with self.assertRaises(autobuild.BuildError):
                    autobuild.generated_api_check(self.source)

    def test_comments_do_not_hide_nonempty_compiled_values(self):
        self.config('public static final boolean TG_RUNTIME_API_CONFIG = true;\npublic static final int TG_API_ID = 12;\npublic static final String TG_API_HASH = "fixture";\n// TG_API_ID = 0; TG_API_HASH = "";\n')
        with self.assertRaises(autobuild.BuildError):
            autobuild.generated_api_check(self.source)


class GradleToolchainTest(BuildFixture):
    def test_explicit_gradle_must_match_the_upstream_wrapper_version(self):
        executable = self.work / "tools/gradle"
        self.write(executable, "synthetic tool\n")
        env = {"PATH": ""}
        with mock.patch.object(autobuild.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="Gradle 8.11.1\n")):
            self.assertEqual(autobuild.locate_gradle(str(executable), env, self.source, self.work / "cache"), str(executable.resolve()))
        with mock.patch.object(autobuild.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="Gradle 9.0.0\n")):
            with self.assertRaises(autobuild.BuildError):
                autobuild.locate_gradle(str(executable), env, self.source, self.work / "cache")

    def test_automatic_gradle_download_verifies_official_checksum(self):
        archive_data = io.BytesIO()
        executable = "gradle.bat" if os.name == "nt" else "gradle"
        with zipfile.ZipFile(archive_data, "w") as archive:
            archive.writestr("gradle-8.11.1/bin/" + executable, b"synthetic tool\n")
        content = archive_data.getvalue()
        expected = hashlib.sha256(content).hexdigest()
        url = "https://downloads.gradle.org/distributions/gradle-8.11.1-bin.zip"
        responses = {url + ".sha256": expected.encode("ascii"), url: content}
        with mock.patch.object(autobuild.shutil, "which", return_value=None), mock.patch.object(
            autobuild, "request", side_effect=lambda requested: io.BytesIO(responses[requested])
        ) as request:
            result = Path(autobuild.locate_gradle(None, {"PATH": ""}, self.source, self.work / "cache"))
        self.assertEqual(result.read_bytes(), b"synthetic tool\n")
        self.assertEqual(request.call_count, 2)
        self.assertFalse(list((self.work / "cache").rglob("*.part")))

    def test_gradle_checksum_mismatch_cannot_install_or_preserve_partial_download(self):
        url = "https://downloads.gradle.org/distributions/gradle-8.11.1-bin.zip"
        responses = {url + ".sha256": b"0" * 64, url: b"incorrect synthetic archive"}
        with mock.patch.object(autobuild.shutil, "which", return_value=None), mock.patch.object(
            autobuild, "request", side_effect=lambda requested: io.BytesIO(responses[requested])
        ):
            with self.assertRaises(autobuild.BuildError):
                autobuild.locate_gradle(None, {"PATH": ""}, self.source, self.work / "cache")
        self.assertFalse((self.work / "cache/toolchains/gradle-8.11.1").exists())
        self.assertFalse(list((self.work / "cache").rglob("*.part")))
        self.assertFalse(list((self.work / "cache").rglob("*.zip")))


class PreparedInventoryTest(BuildFixture):
    def test_original_tree_is_accepted_but_changed_source_is_rejected(self):
        self.snapshot()
        self.write(self.source / "TMessagesProj/src/main/java/Existing.java", "class Changed {}\n")
        with self.assertRaises(source_tools.SourceError):
            source_tools.verify_prepared(self.source, self.project)

    def test_deleted_source_and_changed_overlay_are_rejected(self):
        self.snapshot()
        (self.source / "TMessagesProj/src/main/java/Existing.java").unlink()
        with self.assertRaises(source_tools.SourceError):
            source_tools.verify_prepared(self.source, self.project)
        self.write(self.source / "TMessagesProj/src/main/java/Existing.java", "class Existing {}\n")
        self.write(self.project / "overlay/Feature.java", "class ChangedFeature {}\n")
        with self.assertRaises(source_tools.SourceError):
            source_tools.verify_prepared(self.source, self.project)

    def test_added_java_source_cannot_enter_a_build_without_corresponding_source(self):
        self.snapshot()
        self.write(self.source / "TMessagesProj/src/main/java/Injected.java", "class Injected {}\n")
        with self.assertRaises(source_tools.SourceError):
            source_tools.verify_prepared(self.source, self.project)

    def test_a_source_directory_named_build_is_not_treated_as_generated(self):
        self.snapshot()
        self.write(self.source / "TMessagesProj/src/main/java/build/Injected.java", "class Injected {}\n")
        with self.assertRaises(source_tools.SourceError):
            source_tools.verify_prepared(self.source, self.project)

    def test_normal_generated_build_outputs_do_not_invalidate_preparation(self):
        manifest = self.snapshot()
        self.write(self.source / "TMessagesProj/build/generated/source/buildConfig/BuildConfig.java", "synthetic generated source\n")
        self.write(self.source / "TMessagesProj_AppStandalone/build/outputs/build.log", "synthetic build output\n")
        self.write(self.source / ".gradle/cache.bin", "synthetic cache\n")
        self.assertEqual(source_tools.verify_prepared(self.source, self.project), manifest)

    def test_inventory_paths_cannot_escape_the_source_tree(self):
        manifest = self.snapshot()
        outside = self.work / "outside.txt"
        self.write(outside, "synthetic outside data\n")
        manifest["files"]["../outside.txt"] = source_tools.digest(outside)
        self.write(self.source / "prepared-manifest.json", json.dumps(manifest))
        with self.assertRaises(source_tools.SourceError):
            source_tools.verify_prepared(self.source, self.project)


class SourcePackageTest(BuildFixture):
    def test_packaging_uses_prepared_inventory_and_omits_later_local_files(self):
        manifest = self.snapshot()
        for name in ("local.properties", "account-data.json", ".env", "device.log", "TMessagesProj/build/generated/secret.txt"):
            self.write(self.source / name, "synthetic local marker\n")
        self.write(self.project / ".env", "synthetic private project setting\n")
        self.write(self.project / "tests/__pycache__/test_fixture.pyc", "synthetic cache\n")
        destination = self.work / "source.zip"
        autobuild.package_source(self.source, destination, manifest)
        with zipfile.ZipFile(destination) as archive:
            self.assertIsNone(archive.testzip())
            names = set(archive.namelist())
            expected = {"TG-Media-source/Telegram/" + name for name in manifest["files"]}
            self.assertTrue(expected.issubset(names))
            self.assertIn("TG-Media-source/README.md", names)
            self.assertIn("TG-Media-source/sources.lock.json", names)
            self.assertIn("TG-Media-source/Telegram/prepared-manifest.json", names)
            self.assertFalse(any("local.properties" in name or "account-data" in name or "device.log" in name or "__pycache__" in name or name.endswith("/.env") for name in names))
            for name in names:
                self.assertNotIn(b"synthetic local marker", archive.read(name))

    def test_source_zip_restores_a_dangling_link_in_a_nested_module(self):
        module = self.source / "native/module"
        self.write(module / "tests/settings.txt", "../missing/settings.txt")
        self.write(module / ".tg-source-links.json", json.dumps({"tests/settings.txt": "../missing/settings.txt"}))
        manifest = self.snapshot()
        destination = self.work / "source.zip"
        autobuild.package_source(self.source, destination, manifest)
        with zipfile.ZipFile(destination) as archive:
            item = archive.getinfo("TG-Media-source/Telegram/native/module/tests/settings.txt")
            self.assertTrue(stat.S_ISLNK(item.external_attr >> 16))
            self.assertEqual(archive.read(item), b"../missing/settings.txt")

    def test_existing_source_archive_is_not_overwritten(self):
        destination = self.work / "source.zip"
        destination.write_bytes(b"existing release archive")
        with self.assertRaises(FileExistsError):
            autobuild.package_source(self.source, destination, self.snapshot())
        self.assertEqual(destination.read_bytes(), b"existing release archive")

    def test_private_file_types_in_publication_folders_are_rejected(self):
        self.write(self.project / "scripts/private.jks", "synthetic private file\n")
        with self.assertRaises(autobuild.BuildError):
            list(autobuild.project_files())

    def test_local_notes_are_not_included_in_corresponding_source(self):
        self.write(self.project / "LOCAL_NOTES.md", "synthetic local notes\n")
        self.write(self.project / "docs/work-notes.md", "synthetic internal notes\n")
        destination = self.work / "source.zip"
        autobuild.package_source(self.source, destination, self.snapshot())
        with zipfile.ZipFile(destination) as archive:
            self.assertNotIn("TG-Media-source/LOCAL_NOTES.md", archive.namelist())
            self.assertNotIn("TG-Media-source/docs/work-notes.md", archive.namelist())


class BuildPublicationTest(BuildFixture):
    def build_setup(self):
        self.snapshot()
        tools = self.work / "sdk/build-tools/36.0.0"
        tools.mkdir(parents=True)
        self.write(tools / ("apksigner.bat" if os.name == "nt" else "apksigner"), "synthetic tool\n")
        self.write(tools / ("zipalign.exe" if os.name == "nt" else "zipalign"), "synthetic tool\n")
        gradle = self.work / "tools/gradle"
        keystore = self.work / "private/release.jks"
        self.write(gradle, "synthetic tool\n")
        self.write(keystore, "synthetic signing fixture\n")
        self.args = SimpleNamespace(
            gradle=str(gradle), keystore=keystore, key_alias="fixture", revision=2,
            version_name=None, version_code=None, build_tools="36.0.0",
            output=self.work / "dist", cache=self.work / "cache", workers=1,
        )
        self.env = {
            "ANDROID_HOME": str(self.work / "sdk"), "JAVA_HOME": str(self.work / "jdk"),
            "PATH": "", "TG_SIGNING_PASSWORD": "synthetic-password-fixture",
        }
        self.metadata_overrides = {}
        self.element_overrides = {}
        self.after_compile = None
        self.checker = mock.Mock()
        self.certificate = "Signer #1 certificate DN: CN=TG Media Release\nSigner #1 certificate SHA-256 digest: " + "a" * 64 + "\n"
        patches = (
            mock.patch.object(autobuild, "verify_prepared", side_effect=lambda source: source_tools.verify_prepared(source, self.project)),
            mock.patch.object(autobuild, "run", side_effect=self.fake_run),
            mock.patch.object(autobuild, "check_project_privacy"),
            mock.patch.object(autobuild, "apk_checker", return_value=self.checker),
            mock.patch.object(autobuild.subprocess, "run", side_effect=self.fake_subprocess),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def fake_subprocess(self, command, **kwargs):
        if "--version" in command:
            return SimpleNamespace(returncode=0, stdout="Gradle 8.11.1\n", stderr="")
        if "--print-certs" in command:
            return SimpleNamespace(returncode=0, stdout=self.certificate, stderr="")
        self.fail("Unexpected external command in simulated build")

    def fake_run(self, command, **kwargs):
        if ":TMessagesProj_AppStandalone:assembleAfatStandalone" not in command:
            return
        generated = self.source / "TMessagesProj/build/generated/source/buildConfig/afat/org/telegram/BuildConfig.java"
        self.write(generated, 'public static final boolean TG_RUNTIME_API_CONFIG = true;\npublic static final int TG_API_ID = 0;\npublic static final String TG_API_HASH = "";\n')
        output = self.source / "TMessagesProj_AppStandalone/build/intermediates/apk/afat/standalone"
        self.write(output / "app.apk", "synthetic APK fixture\n")
        element = {"outputFile": "app.apk", "versionCode": 12302, "versionName": "1.2.3-hyperos.2"}
        element.update(self.element_overrides)
        metadata = {"applicationId": "org.telegram.tgmedia.web", "elements": [element]}
        metadata.update(self.metadata_overrides)
        self.write(output / "output-metadata.json", json.dumps(metadata))
        if self.after_compile:
            self.after_compile()

    def assert_no_release(self):
        self.assertFalse((self.args.output / "TG-Media-1.2.3-hyperos.2").exists())
        self.assertFalse(list(self.args.output.glob(".release-*")))

    def test_simulated_success_publishes_checksums_source_and_generic_certificate(self):
        self.build_setup()
        result = autobuild.build(self.source, self.args, self.env)
        self.assertEqual(result, self.args.output / "TG-Media-1.2.3-hyperos.2")
        self.assertTrue((result / "TG-Media-1.2.3-hyperos.2-arm64-v8a.apk").is_file())
        self.assertTrue((result / "TG-Media-1.2.3-hyperos.2-source.zip").is_file())
        self.assertEqual((result / "SIGNING-CERTIFICATE.txt").read_text(encoding="utf-8"), "SHA-256: " + "a" * 64 + "\n")
        provenance = json.loads((result / "build-provenance.json").read_text(encoding="utf-8"))
        self.assertFalse(provenance["device_tested"])
        self.assertEqual(provenance["version_code"], 12302)
        checksums = (result / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(checksums), len(list(result.iterdir())) - 1)
        for line in checksums:
            expected, name = line.split("  ", 1)
            self.assertEqual(source_tools.digest(result / name), expected)
        self.checker.assert_called_once()
        self.assertFalse(list(self.args.output.glob(".release-*")))

    def test_packaging_failure_does_not_leave_partial_release_assets(self):
        self.build_setup()

        def failed_package(source, destination, manifest):
            destination.write_bytes(b"partial corresponding source")
            raise autobuild.BuildError("Synthetic packaging failure")

        with mock.patch.object(autobuild, "package_source", side_effect=failed_package):
            with self.assertRaises(autobuild.BuildError):
                autobuild.build(self.source, self.args, self.env)
        self.assert_no_release()

    def test_apk_privacy_check_failure_prevents_publication(self):
        self.build_setup()
        self.checker.side_effect = autobuild.BuildError("Synthetic APK privacy failure")
        with self.assertRaises(autobuild.BuildError):
            autobuild.build(self.source, self.args, self.env)
        self.assert_no_release()

    def test_personal_signing_subject_prevents_publication(self):
        self.build_setup()
        self.certificate = self.certificate.replace("CN=TG Media Release", "CN=Synthetic Personal Identity")
        with self.assertRaises(autobuild.BuildError):
            autobuild.build(self.source, self.args, self.env)
        self.assert_no_release()

    def test_changed_source_during_compilation_prevents_publication(self):
        self.build_setup()
        self.after_compile = lambda: self.write(self.source / "TMessagesProj/src/main/java/Existing.java", "class Modified {}\n")
        with self.assertRaises(source_tools.SourceError):
            autobuild.build(self.source, self.args, self.env)
        self.assert_no_release()

    def test_metadata_with_an_escaping_apk_path_is_rejected(self):
        self.build_setup()
        self.element_overrides["outputFile"] = "../outside.apk"
        with self.assertRaises(autobuild.BuildError):
            autobuild.build(self.source, self.args, self.env)
        self.checker.assert_not_called()
        self.assert_no_release()

    def test_metadata_version_must_match_requested_release(self):
        self.build_setup()
        self.element_overrides["versionCode"] = 1
        with self.assertRaises(autobuild.BuildError):
            autobuild.build(self.source, self.args, self.env)
        self.checker.assert_not_called()
        self.assert_no_release()

    def test_existing_release_is_preserved(self):
        self.build_setup()
        existing = self.args.output / "TG-Media-1.2.3-hyperos.2/keep.txt"
        self.write(existing, "existing release\n")
        with self.assertRaises(autobuild.BuildError):
            autobuild.build(self.source, self.args, self.env)
        self.assertEqual(existing.read_text(encoding="utf-8"), "existing release\n")
        self.assertFalse((self.source / "TMessagesProj/build").exists())


if __name__ == "__main__":
    unittest.main()
