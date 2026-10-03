"""Exercise source preparation with synthetic archives and real local Git patches."""

from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("source_tools", ROOT / "scripts/source_tools.py")
source_tools = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source_tools)
ROOT_REVISION = "1" * 40
MODULE_REVISION = "2" * 40


def zip_data(members):
    result = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(result, "w") as archive:
            for name, content in members:
                if isinstance(name, str):
                    raw_name = name
                    name = zipfile.ZipInfo(raw_name)
                    # Keep intentionally unsafe names unchanged in ZIP bytes.
                    name.filename = raw_name
                archive.writestr(name, content)
    return result.getvalue()


def tar_data(members):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w:gz") as archive:
        for name, content, kind in members:
            item = tarfile.TarInfo(name)
            item.type = kind
            if kind == tarfile.REGTYPE:
                item.size = len(content)
                archive.addfile(item, io.BytesIO(content))
            else:
                item.linkname = "../outside" if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ""
                archive.addfile(item)
    return result.getvalue()


def entry(repository="DrKLO/Telegram", revision=ROOT_REVISION, path=".", content=None):
    result = {
        "repository": repository,
        "revision": revision,
        "path": path,
        "filename": repository.replace("/", "-") + "-" + revision + ".zip",
        "format": "zip",
        "url": f"https://codeload.github.com/{repository}/zip/{revision}",
    }
    if content is not None:
        result["sha256"] = hashlib.sha256(content).hexdigest()
    return result


def patch_text(before="original", after="patched"):
    return (
        "diff --git a/message.txt b/message.txt\n"
        "--- a/message.txt\n"
        "+++ b/message.txt\n"
        "@@ -1 +1 @@\n"
        f"-{before}\n"
        f"+{after}\n"
    )


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="tg-source-tests-")
        self.addCleanup(folder.cleanup)
        self.work = Path(folder.name)
        self.addCleanup(mock.patch.stopall)
        self.output = io.StringIO()
        redirect = redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def project(self):
        project = self.work / "project"
        (project / "patches").mkdir(parents=True)
        (project / "overlay/nested").mkdir(parents=True)
        (project / "patches/tg-media.patch").write_text(patch_text(), encoding="utf-8", newline="\n")
        (project / "overlay/nested/overlay.txt").write_text("overlay\n", encoding="utf-8")
        (project / "sources.lock.json").write_text("[]\n", encoding="utf-8")
        return project


class SafePathTest(unittest.TestCase):
    def test_valid_source_paths_include_root_and_unicode(self):
        for value in (".", "src/main/File.java", "resources/Caf\u00e9.txt", "src/.gitignore"):
            with self.subTest(path=value):
                self.assertEqual(source_tools.safe_relative(value), PurePosixPath(value))

    def test_escaping_and_windows_unsafe_paths_are_rejected(self):
        values = (
            "../escape", "folder/../../escape", "/absolute", "//server/share",
            "C:/absolute", "C:relative", "folder\\escape", "file:stream",
            "nul", "src/CON.txt", "aux", "LPT9.log", "folder./file", "folder /file",
            "file\0tail", 'bad"name', "bad*name", "bad?name", "bad<name",
            "bad>name", "bad|name", "bad\nname", "bad\tname",
        )
        for value in values:
            with self.subTest(path=repr(value)):
                with self.assertRaises(source_tools.SourceError):
                    source_tools.safe_relative(value)


class ExtractionTest(WorkspaceTest):
    def extract_members(self, names, archive_format, destination=None):
        archive = self.work / ("source.zip" if archive_format == "zip" else "source.tar.gz")
        if archive_format == "zip":
            archive.write_bytes(zip_data([(name, b"fixture\n") for name in names]))
        else:
            archive.write_bytes(tar_data([(name, b"fixture\n", tarfile.REGTYPE) for name in names]))
        destination = destination or self.work / "extracted"
        source_tools.extract(archive, destination, archive_format)
        return destination

    def test_zip_removes_exactly_one_root_and_preserves_contents(self):
        archive = self.work / "source.zip"
        archive.write_bytes(zip_data([
            ("source/", b""), ("source/src/", b""),
            ("source/src/message.txt", b"hello\n"), ("source/.gitmodules", b""),
        ]))
        destination = self.work / "extracted"
        source_tools.extract(archive, destination, "zip")
        self.assertEqual((destination / "src/message.txt").read_bytes(), b"hello\n")
        self.assertTrue((destination / ".gitmodules").is_file())
        self.assertFalse((destination / "source").exists())

    def test_tar_preserves_paths_including_a_single_top_level_directory(self):
        archive = self.work / "source.tar.gz"
        archive.write_bytes(tar_data([
            (".", b"", tarfile.DIRTYPE), ("src", b"", tarfile.DIRTYPE),
            ("src/message.txt", b"hello\n", tarfile.REGTYPE),
        ]))
        destination = self.work / "extracted"
        source_tools.extract(archive, destination, "tar.gz")
        self.assertEqual((destination / "src/message.txt").read_bytes(), b"hello\n")

    def test_zip_requires_one_directory_root(self):
        for names in ([], ["loose.txt"], ["one/a.txt", "two/b.txt"]):
            with self.subTest(names=names):
                with self.assertRaises(source_tools.SourceError):
                    self.extract_members(names, "zip")

    def test_archive_traversal_is_rejected_before_writing_members(self):
        for archive_format in ("zip", "tar.gz"):
            for bad in ("../escape", "/absolute", "folder/../../escape", "C:/escape", "folder\\..\\..\\escape"):
                with self.subTest(format=archive_format, name=bad):
                    names = ["root/safe.txt", "root/" + bad] if archive_format == "zip" and not bad.startswith("/") else ["safe.txt", bad]
                    if archive_format == "zip" and bad.startswith("/"):
                        names = ["root/safe.txt", bad]
                    destination = self.work / f"rejected-{len(list(self.work.iterdir()))}"
                    with self.assertRaises(source_tools.SourceError):
                        self.extract_members(names, archive_format, destination)
                    self.assertEqual(list(destination.rglob("*")), [])
        self.assertFalse((self.work / "escape").exists())

    def test_zip_escaping_symbolic_link_is_rejected(self):
        symlink = zipfile.ZipInfo("root/link")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive = self.work / "source.zip"
        archive.write_bytes(zip_data([("root/safe.txt", b"safe"), (symlink, b"../outside")]))
        destination = self.work / "extracted"
        with self.assertRaises(source_tools.SourceError):
            source_tools.extract(archive, destination, "zip")
        self.assertEqual(list(destination.iterdir()), [])

    def test_zip_internal_file_links_are_materialized_from_archive_members(self):
        def link(name):
            item = zipfile.ZipInfo(name)
            item.create_system = 3
            item.external_attr = (stat.S_IFLNK | 0o777) << 16
            return item

        archive = self.work / "source.zip"
        archive.write_bytes(zip_data([
            (link("root/lib/tests/settings.txt"), b"../../common/settings.txt"),
            (link("root/chained.txt"), b"lib/tests/settings.txt"),
            ("root/common/settings.txt", b"synthetic=configuration\n"),
        ]))
        destination = self.work / "extracted"
        source_tools.extract(archive, destination, "zip")
        for path in ("lib/tests/settings.txt", "chained.txt", "common/settings.txt"):
            with self.subTest(path=path):
                self.assertEqual((destination / path).read_bytes(), b"synthetic=configuration\n")
                self.assertFalse((destination / path).is_symlink())

    def test_zip_links_cannot_target_directories_or_cycles(self):
        for target in (b"/outside", b"../../outside", b"folder/", b"link", b"C:/outside", b"bad\\path"):
            with self.subTest(target=target):
                link = zipfile.ZipInfo("root/link")
                link.create_system = 3
                link.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive = self.work / "source.zip"
                archive.write_bytes(zip_data([
                    ("root/safe.txt", b"safe"), ("root/folder/", b""), (link, target),
                ]))
                destination = self.work / "extracted"
                with self.assertRaises(source_tools.SourceError):
                    source_tools.extract(archive, destination, "zip")
                self.assertEqual(list(destination.iterdir()), [])

    def test_zip_dangling_link_keeps_its_text_and_never_reads_disk_targets(self):
        link = zipfile.ZipInfo("root/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive = self.work / "source.zip"
        archive.write_bytes(zip_data([(link, b"existing.txt")]))
        destination = self.work / "extracted"
        destination.mkdir()
        existing = destination / "existing.txt"
        existing.write_bytes(b"synthetic existing data")
        source_tools.extract(archive, destination, "zip")
        self.assertEqual(existing.read_bytes(), b"synthetic existing data")
        self.assertEqual((destination / "link").read_bytes(), b"existing.txt")
        self.assertFalse((destination / "link").is_symlink())
        metadata = json.loads((destination / ".tg-source-links.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["link"], "existing.txt")

    def test_zip_dangling_link_chain_preserves_each_original_target(self):
        members = []
        for name, target in (("root/first", b"second"), ("root/second", b"missing")):
            link = zipfile.ZipInfo(name)
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            members.append((link, target))
        archive = self.work / "source.zip"
        archive.write_bytes(zip_data(members))
        destination = self.work / "extracted"
        source_tools.extract(archive, destination, "zip")
        metadata = json.loads((destination / ".tg-source-links.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata, {"first": "second", "second": "missing"})

    def test_zip_link_to_an_implicit_directory_is_rejected(self):
        link = zipfile.ZipInfo("root/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive = self.work / "source.zip"
        archive.write_bytes(zip_data([("root/folder/file.txt", b"fixture"), (link, b"folder")]))
        destination = self.work / "extracted"
        with self.assertRaises(source_tools.SourceError):
            source_tools.extract(archive, destination, "zip")
        self.assertEqual(list(destination.iterdir()), [])

    def test_tar_rejects_symbolic_links_hard_links_and_special_files(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE):
            with self.subTest(kind=kind):
                archive = self.work / "source.tar.gz"
                archive.write_bytes(tar_data([
                    ("safe.txt", b"safe", tarfile.REGTYPE), ("link", b"", kind),
                ]))
                destination = self.work / "extracted"
                with self.assertRaises(source_tools.SourceError):
                    source_tools.extract(archive, destination, "tar.gz")
                self.assertEqual(list(destination.iterdir()), [])

    def test_duplicate_and_case_colliding_members_are_rejected(self):
        for archive_format in ("zip", "tar.gz"):
            for names in (["same.txt", "same.txt"], ["same.txt", "SAME.txt"]):
                with self.subTest(format=archive_format, names=names):
                    if archive_format == "zip":
                        names = ["root/" + name for name in names]
                    destination = self.work / "extracted"
                    with self.assertRaises(source_tools.SourceError):
                        self.extract_members(names, archive_format, destination)
                    self.assertEqual(list(destination.iterdir()), [])

    def test_file_directory_collisions_are_rejected_before_extraction(self):
        for archive_format in ("zip", "tar.gz"):
            for names in (["node", "node/child.txt"], ["node/child.txt", "NODE"]):
                with self.subTest(format=archive_format, names=names):
                    if archive_format == "zip":
                        names = ["root/" + name for name in names]
                    destination = self.work / f"collision-{len(list(self.work.iterdir()))}"
                    with self.assertRaises(source_tools.SourceError):
                        self.extract_members(names, archive_format, destination)
                    self.assertEqual(list(destination.iterdir()), [])

    def test_directory_component_case_aliases_are_rejected(self):
        for archive_format in ("zip", "tar.gz"):
            with self.subTest(format=archive_format):
                names = ["src/one.txt", "SRC/two.txt"]
                if archive_format == "zip":
                    names = ["root/" + name for name in names]
                destination = self.work / ("alias-" + archive_format)
                with self.assertRaises(source_tools.SourceError):
                    self.extract_members(names, archive_format, destination)
                self.assertEqual(list(destination.iterdir()), [])


class LockEntryTest(unittest.TestCase):
    def test_valid_pinned_github_and_googlesource_entries(self):
        source_tools.validate_entry(entry(content=b"fixture"))
        source_tools.validate_entry({
            "repository": "libyuv", "revision": MODULE_REVISION,
            "path": "jni/libyuv", "filename": "libyuv.tar.gz", "format": "tar.gz",
            "url": f"https://chromium.googlesource.com/libyuv/libyuv/+archive/{MODULE_REVISION}.tar.gz",
        })

    def test_lock_rejects_unpinned_or_escaping_values_and_changed_urls(self):
        changes = (
            {"revision": "main"}, {"revision": "1" * 39},
            {"path": "../outside"}, {"filename": "nested/source.zip"},
            {"filename": "../source.zip"}, {"filename": "CON.zip"},
            {"format": "tar"}, {"sha256": "0" * 63}, {"sha256": "Z" * 64},
            {"url": "https://example.invalid/source.zip"},
            {"url": f"https://codeload.github.com/Other/Project/zip/{ROOT_REVISION}"},
            {"url": f"https://codeload.github.com/DrKLO/Telegram/zip/{MODULE_REVISION}"},
            {"url": f"http://codeload.github.com/DrKLO/Telegram/zip/{ROOT_REVISION}"},
        )
        for change in changes:
            with self.subTest(fields=tuple(change)):
                with self.assertRaises(source_tools.SourceError):
                    source_tools.validate_entry(dict(entry(), **change))


class DownloadTest(WorkspaceTest):
    def test_download_is_promoted_only_after_hashing(self):
        content = b"complete synthetic source"
        source = entry()
        cache = self.work / "cache"
        with mock.patch.object(source_tools, "request", return_value=io.BytesIO(content)) as request:
            archive = source_tools.download(source, cache)
        self.assertEqual(archive.read_bytes(), content)
        self.assertEqual(source["sha256"], hashlib.sha256(content).hexdigest())
        self.assertFalse(list(cache.glob("*.part")))
        request.assert_called_once_with(source["url"])

    def test_new_revision_can_resume_from_a_previous_verified_download(self):
        content = b"resumable synthetic source"
        cache = self.work / "cache"
        original = entry()
        with mock.patch.object(source_tools, "request", return_value=io.BytesIO(content)):
            archive = source_tools.download(original, cache)
        resumed = entry()
        with mock.patch.object(source_tools, "request") as request:
            self.assertEqual(source_tools.download(resumed, cache), archive)
        request.assert_not_called()
        self.assertEqual(resumed["sha256"], original["sha256"])

    def test_resumed_download_rechecks_cached_bytes(self):
        content = b"resumable synthetic source"
        cache = self.work / "cache"
        with mock.patch.object(source_tools, "request", return_value=io.BytesIO(content)):
            archive = source_tools.download(entry(), cache)
        archive.write_bytes(b"changed after download")
        with mock.patch.object(source_tools, "request") as request:
            with self.assertRaises(source_tools.SourceError):
                source_tools.download(entry(), cache)
        request.assert_not_called()

    def test_lock_checksum_takes_precedence_over_a_cached_download_hash(self):
        cache = self.work / "cache"
        with mock.patch.object(source_tools, "request", return_value=io.BytesIO(b"cached source")):
            source_tools.download(entry(), cache)
        with mock.patch.object(source_tools, "request") as request:
            with self.assertRaises(source_tools.SourceError):
                source_tools.download(entry(content=b"locked source"), cache)
        request.assert_not_called()

    def test_verified_cache_does_not_contact_the_network(self):
        content = b"verified cached source"
        source = entry(content=content)
        cache = self.work / "cache"
        cache.mkdir()
        archive = cache / source["filename"]
        archive.write_bytes(content)
        with mock.patch.object(source_tools, "request") as request:
            self.assertEqual(source_tools.download(source, cache), archive)
        request.assert_not_called()

    def test_unknown_or_corrupt_cached_file_is_not_trusted_or_overwritten(self):
        cache = self.work / "cache"
        cache.mkdir()
        archive = cache / entry()["filename"]
        archive.write_bytes(b"untrusted cache")
        for source in (entry(), entry(content=b"expected content")):
            with self.subTest(checksum_present="sha256" in source):
                with mock.patch.object(source_tools, "request") as request:
                    with self.assertRaises(source_tools.SourceError):
                        source_tools.download(source, cache)
                request.assert_not_called()
                self.assertEqual(archive.read_bytes(), b"untrusted cache")

    def test_checksum_failure_leaves_no_completed_or_partial_archive(self):
        source = entry(content=b"expected source")
        expected = source["sha256"]
        cache = self.work / "cache"
        with mock.patch.object(source_tools, "request", return_value=io.BytesIO(b"wrong source")):
            with self.assertRaises(source_tools.SourceError):
                source_tools.download(source, cache)
        self.assertEqual(list(cache.iterdir()), [])
        self.assertEqual(source["sha256"], expected)

    def test_interrupted_stream_cleans_up_partial_archive(self):
        class InterruptedStream(io.BytesIO):
            def read(self, size=-1):
                if self.tell():
                    raise OSError("Synthetic interrupted download")
                return super().read(size)

        source = entry()
        cache = self.work / "cache"
        with mock.patch.object(source_tools, "request", return_value=InterruptedStream(b"first chunk")):
            with self.assertRaises((OSError, source_tools.SourceError)):
                source_tools.download(source, cache)
        self.assertEqual(list(cache.iterdir()), [])
        self.assertNotIn("sha256", source)

    def test_stale_partial_file_is_replaced_by_a_complete_download(self):
        content = b"complete source"
        source = entry(content=content)
        cache = self.work / "cache"
        cache.mkdir()
        (cache / (source["filename"] + ".part")).write_bytes(b"stale partial")
        with mock.patch.object(source_tools, "request", return_value=io.BytesIO(content)):
            archive = source_tools.download(source, cache)
        self.assertEqual(archive.read_bytes(), content)
        self.assertFalse(list(cache.glob("*.part")))


class SubmoduleTest(WorkspaceTest):
    def write_modules(self, modules):
        text = "".join(
            f'[submodule "module-{index}"]\n\tpath = {path}\n\turl = {url}\n\tbranch = main\n'
            for index, (path, url) in enumerate(modules)
        )
        (self.work / ".gitmodules").write_text(text, encoding="utf-8")

    def test_modules_use_gitlinks_at_the_parent_commit_and_reuse_known_hashes(self):
        self.write_modules([
            ("jni/dependency", "https://github.com/Example/Dependency.git"),
            ("jni/libyuv", "https://chromium.googlesource.com/libyuv/libyuv"),
        ])
        known = [entry("Example/Dependency", MODULE_REVISION, "old/path", b"cached module")]
        tree = {"tree": [
            {"path": "jni/dependency", "type": "commit", "sha": MODULE_REVISION},
            {"path": "jni/libyuv", "type": "commit", "sha": "3" * 40},
            {"path": "ordinary.txt", "type": "blob", "sha": "4" * 40},
        ]}
        with mock.patch.object(source_tools, "api", return_value=tree) as api:
            sources = source_tools.module_entries("Example/Parent", ROOT_REVISION, self.work, "nested", known)
        api.assert_called_once_with("Example/Parent", f"git/trees/{ROOT_REVISION}?recursive=1")
        self.assertEqual([item["path"] for item in sources], ["nested/jni/dependency", "nested/jni/libyuv"])
        self.assertEqual(sources[0]["repository"], "Example/Dependency")
        self.assertEqual(sources[0]["revision"], MODULE_REVISION)
        self.assertEqual(sources[0]["sha256"], known[0]["sha256"])
        self.assertEqual(known[0]["path"], "old/path")
        self.assertEqual(sources[1]["repository"], "libyuv")
        self.assertTrue(sources[1]["url"].endswith("3" * 40 + ".tar.gz"))
        for source in sources:
            source_tools.validate_entry(source)

    def test_without_a_manifest_there_is_no_api_request(self):
        with mock.patch.object(source_tools, "api") as api:
            self.assertEqual(source_tools.module_entries("Example/Parent", ROOT_REVISION, self.work, ".", []), [])
        api.assert_not_called()

    def test_unknown_hosts_traversal_and_unmatched_gitlinks_fail(self):
        cases = (
            ([("dep", "https://example.invalid/dependency.git")], ["dep"]),
            ([("../outside", "https://github.com/Example/Dependency")], ["../outside"]),
            ([("dep", "https://github.com/Example/Dependency")], []),
            ([("dep", "https://github.com/Example/Dependency")], ["dep", "undeclared"]),
        )
        for modules, commits in cases:
            with self.subTest(modules=modules):
                self.write_modules(modules)
                tree = {"tree": [{"path": path, "type": "commit", "sha": MODULE_REVISION} for path in commits]}
                with mock.patch.object(source_tools, "api", return_value=tree):
                    with self.assertRaises(source_tools.SourceError):
                        source_tools.module_entries("Example/Parent", ROOT_REVISION, self.work, ".", [])

    def test_truncated_tree_cannot_silently_omit_modules(self):
        self.write_modules([("dep", "https://github.com/Example/Dependency")])
        with mock.patch.object(source_tools, "api", return_value={"tree": [], "truncated": True}):
            with self.assertRaises(source_tools.SourceError):
                source_tools.module_entries("Example/Parent", ROOT_REVISION, self.work, ".", [])


@unittest.skipUnless(shutil.which("git"), "Git is required for patch integration tests")
class PatchApplicationTest(WorkspaceTest):
    def nested_target(self):
        enclosing = self.work / "enclosing"
        enclosing.mkdir()
        result = subprocess.run(["git", "init", "--quiet", str(enclosing)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        (enclosing / ".gitignore").write_text("generated/\n", encoding="utf-8")
        target = enclosing / "generated/source"
        target.mkdir(parents=True)
        (target / "message.txt").write_text("original\n", encoding="utf-8", newline="\n")
        ignored = subprocess.run(["git", "check-ignore", "--quiet", "generated/source/message.txt"], cwd=enclosing)
        self.assertEqual(ignored.returncode, 0)
        return enclosing, target

    def test_real_patch_applies_inside_an_ignored_nested_directory(self):
        enclosing, target = self.nested_target()
        project = self.project()
        (project / "patches/zz-followup.patch").write_text(patch_text("patched", "final"), encoding="utf-8", newline="\n")
        initial_head = (enclosing / ".git/HEAD").read_bytes()
        source_tools.apply_patches(target, project)
        self.assertEqual((target / "message.txt").read_text(encoding="utf-8"), "final\n")
        self.assertEqual((target / "nested/overlay.txt").read_text(encoding="utf-8"), "overlay\n")
        self.assertEqual((enclosing / ".git/HEAD").read_bytes(), initial_head)
        self.assertFalse((enclosing / "message.txt").exists())
        self.assertFalse((target / ".git").exists())

    def test_inherited_git_repository_variables_do_not_redirect_application(self):
        enclosing, target = self.nested_target()
        project = self.project()
        with mock.patch.dict(os.environ, {
            "GIT_DIR": str(enclosing / ".git"), "GIT_WORK_TREE": str(enclosing),
            "GIT_INDEX_FILE": str(enclosing / ".git/synthetic-index"),
        }):
            source_tools.apply_patches(target, project)
        self.assertEqual((target / "message.txt").read_text(encoding="utf-8"), "patched\n")
        self.assertFalse((enclosing / ".git/synthetic-index").exists())

    def test_conflict_fails_without_changing_source_or_copying_overlay(self):
        _, target = self.nested_target()
        project = self.project()
        (target / "message.txt").write_text("upstream changed\n", encoding="utf-8")
        with self.assertRaises(source_tools.SourceError):
            source_tools.apply_patches(target, project)
        self.assertEqual((target / "message.txt").read_text(encoding="utf-8"), "upstream changed\n")
        self.assertFalse((target / "nested").exists())


@unittest.skipUnless(shutil.which("git"), "Git is required for source preparation integration tests")
class PreparationTest(WorkspaceTest):
    def test_latest_resolves_then_downloads_pinned_modules_and_publishes_a_complete_tree(self):
        project = self.project()
        root_data = zip_data([
            ("root/message.txt", b"original\n"),
            ("root/.gitmodules", b'[submodule "dep"]\npath = native/dep\nurl = https://github.com/Example/Dependency.git\nbranch = main\n'),
        ])
        module_data = zip_data([("module/dependency.txt", b"pinned dependency\n")])
        root_entry = entry(content=root_data)
        module_entry = entry("Example/Dependency", MODULE_REVISION, "native/dep", module_data)
        by_url = {root_entry["url"]: root_data, module_entry["url"]: module_data}

        def api(repository, endpoint):
            if repository == "DrKLO/Telegram" and endpoint == "commits/master":
                return {"sha": ROOT_REVISION}
            if repository == "DrKLO/Telegram" and endpoint == f"git/trees/{ROOT_REVISION}?recursive=1":
                return {"tree": [{"path": "native/dep", "type": "commit", "sha": MODULE_REVISION}]}
            self.fail("Unexpected source API request")

        target, cache = self.work / "prepared", self.work / "cache"
        with mock.patch.object(source_tools, "api", side_effect=api), mock.patch.object(
            source_tools, "request", side_effect=lambda url: io.BytesIO(by_url[url])
        ) as request:
            sources = source_tools.prepare(target, cache, project=project)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(sources, [root_entry, module_entry])
        self.assertEqual((target / "message.txt").read_text(encoding="utf-8"), "patched\n")
        self.assertEqual((target / "native/dep/dependency.txt").read_bytes(), b"pinned dependency\n")
        self.assertTrue((target / "nested/overlay.txt").is_file())
        self.assertEqual(json.loads((target / "sources.lock.json").read_text(encoding="utf-8")), sources)
        self.assertFalse(list(self.work.glob(".preparing-*")))

    def test_locked_preparation_uses_verified_cache_without_network(self):
        project = self.project()
        content = zip_data([("root/message.txt", b"original\n")])
        source = entry(content=content)
        lock = self.work / "locked.json"
        lock.write_text(json.dumps([source]), encoding="utf-8")
        cache = self.work / "cache"
        cache.mkdir()
        (cache / source["filename"]).write_bytes(content)
        target = self.work / "prepared"
        with mock.patch.object(source_tools, "api") as api, mock.patch.object(source_tools, "request") as request:
            sources = source_tools.prepare(target, cache, lock=lock, project=project)
        api.assert_not_called()
        request.assert_not_called()
        self.assertEqual(sources, [source])
        self.assertEqual((target / "message.txt").read_text(encoding="utf-8"), "patched\n")

    def test_failed_patch_never_publishes_a_partial_tree(self):
        project = self.project()
        content = zip_data([("root/message.txt", b"incompatible upstream\n")])
        source = entry(content=content)
        lock = self.work / "locked.json"
        lock.write_text(json.dumps([source]), encoding="utf-8")
        target, cache = self.work / "prepared", self.work / "cache"
        with mock.patch.object(source_tools, "request", return_value=io.BytesIO(content)):
            with self.assertRaises(source_tools.SourceError):
                source_tools.prepare(target, cache, lock=lock, project=project)
        self.assertFalse(target.exists())
        self.assertFalse(list(self.work.glob(".preparing-*")))
        self.assertFalse(list(cache.glob("*.part")))

    def test_existing_destination_is_preserved_without_downloads(self):
        target = self.work / "prepared"
        target.mkdir()
        marker = target / "keep.txt"
        marker.write_bytes(b"existing user work")
        with mock.patch.object(source_tools, "request") as request:
            with self.assertRaises(source_tools.SourceError):
                source_tools.prepare(target, self.work / "cache", project=self.project())
        request.assert_not_called()
        self.assertEqual(marker.read_bytes(), b"existing user work")

    def test_locked_archives_require_hashes_before_download(self):
        project = self.project()
        lock = self.work / "locked.json"
        lock.write_text(json.dumps([entry()]), encoding="utf-8")
        with mock.patch.object(source_tools, "request") as request:
            with self.assertRaises(source_tools.SourceError):
                source_tools.prepare(self.work / "prepared", self.work / "cache", lock=lock, project=project)
        request.assert_not_called()
        self.assertFalse((self.work / "prepared").exists())


if __name__ == "__main__":
    unittest.main()
