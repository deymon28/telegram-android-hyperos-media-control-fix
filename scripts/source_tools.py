"""Resolve official Telegram source and safely prepare a patched, pinned tree."""
from __future__ import annotations

import configparser
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = "DrKLO/Telegram"
SHA = re.compile(r"[0-9a-f]{40}")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class SourceError(RuntimeError):
    """An actionable preparation failure without response bodies or credentials."""


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def request(url: str):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or parsed.hostname not in {"api.github.com", "codeload.github.com", "chromium.googlesource.com", "downloads.gradle.org"}):
        raise SourceError("Unsupported source URL. Only approved HTTPS source hosts are allowed.")
    for attempt in range(3):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers={
                "User-Agent": "TG-Media-autobuilder", "Accept": "application/vnd.github+json",
            }), timeout=120)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise SourceError(f"Source request failed (HTTP {exc.code}); retry later or use --locked.") from None
        except (OSError, urllib.error.URLError):
            if attempt == 2:
                raise SourceError("Source download failed after three attempts. Check network access.") from None
        print(f"Source request retry {attempt + 1}/2", flush=True)
        time.sleep(2 ** attempt)
    raise SourceError("Source request failed.")


def api(repository: str, endpoint: str):
    if not REPO.fullmatch(repository):
        raise SourceError("Invalid source repository name.")
    with request(f"https://api.github.com/repos/{repository}/{endpoint}") as response:
        return json.load(response)


def resolve_ref(ref: str) -> str:
    if SHA.fullmatch(ref):
        return ref
    if not re.fullmatch(r"[A-Za-z0-9_./-]{1,160}", ref) or ".." in ref:
        raise SourceError("Invalid upstream ref; use latest, a tag, a branch, or a full commit SHA.")
    data = api(UPSTREAM, "commits/" + urllib.parse.quote("master" if ref == "latest" else ref, safe=""))
    revision = data.get("sha", "")
    if not SHA.fullmatch(revision):
        raise SourceError("GitHub did not return a full upstream commit SHA.")
    return revision


def safe_relative(name: str) -> PurePosixPath:
    if re.search(r'[\\:*?<>|"\x00-\x1f]', name):
        raise SourceError("Unsafe source path.")
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise SourceError("Source path escapes its destination.")
    for part in relative.parts:
        if part.endswith((".", " ")) or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part):
            raise SourceError("Source path is not portable to Windows.")
    return relative


def output_path(destination: Path, relative: PurePosixPath) -> Path:
    result = destination.joinpath(*relative.parts)
    if not result.resolve().is_relative_to(destination.resolve()):
        raise SourceError("Source extraction would escape its destination.")
    return result


def validate_members(members) -> None:
    paths = {}
    for relative, is_dir in members:
        for index in range(1, len(relative.parts) + 1):
            part = PurePosixPath(*relative.parts[:index]).as_posix()
            key = part.casefold()
            directory = index < len(relative.parts) or is_dir
            if key in paths and paths[key] != (part, directory):
                raise SourceError("Case collision or file/directory conflict in source archive.")
            paths[key] = (part, directory)


def extract(archive: Path, destination: Path, archive_format: str) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if archive_format == "zip":
        with zipfile.ZipFile(archive) as source:
            members, roots, seen = [], set(), set()
            for item in source.infolist():
                path = safe_relative(item.filename)
                if not path.parts:
                    continue
                roots.add(path.parts[0])
                if len(path.parts) == 1:
                    if not item.is_dir():
                        raise SourceError("Expected a directory at the root of a GitHub archive.")
                    continue
                relative = PurePosixPath(*path.parts[1:])
                normalized = str(relative).casefold()
                if normalized in seen:
                    raise SourceError("Duplicate or case-colliding source archive member.")
                seen.add(normalized)
                members.append((item, relative))
            if len(roots) != 1:
                raise SourceError("Expected one root directory in the source ZIP archive.")
            validate_members((relative, item.is_dir()) for item, relative in members)
            member_map = {relative: item for item, relative in members}
            source_links = {}

            def resolve_link(relative, visited=None):
                visited = set() if visited is None else visited
                if relative in visited or relative not in member_map:
                    raise SourceError("Cyclic or missing source symlink target.")
                visited.add(relative)
                item = member_map[relative]
                if item.is_dir():
                    raise SourceError("Source directory symlinks are unsupported.")
                if not stat.S_ISLNK(item.external_attr >> 16):
                    return item
                link = source.read(item).decode("utf-8")
                source_links[relative.as_posix()] = link
                if re.search(r'[\\:*?<>|"\x00-\x1f]', link) or link.startswith("/"):
                    raise SourceError("Unsafe source symlink target.")
                normalized = safe_relative(posixpath.normpath((relative.parent / link).as_posix()))
                if any(name != normalized and name.is_relative_to(normalized) for name in member_map):
                    raise SourceError("Source directory symlinks are unsupported.")
                if normalized not in member_map:
                    # Git for Windows also checks out dangling links as their text.
                    # Keep metadata so corresponding-source ZIPs restore the link.
                    return item
                return resolve_link(normalized, visited)

            # Validate every link before writing any file. Materialize from archive
            # members so Windows needs no developer mode or symlink privilege.
            link_targets = {relative: resolve_link(relative) for item, relative in members
                            if stat.S_ISLNK(item.external_attr >> 16)}
            for item, relative in members:
                target = output_path(destination, relative)
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source.open(link_targets.get(relative, item)) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
            if source_links:
                metadata = destination / ".tg-source-links.json"
                if metadata.exists():
                    raise SourceError("Source archive collides with generated symlink metadata.")
                metadata.write_text(json.dumps(source_links, indent=2) + "\n", encoding="utf-8")
    elif archive_format == "tar.gz":
        with tarfile.open(archive, "r:gz") as source:
            members, seen = source.getmembers(), set()
            for item in members:
                relative = safe_relative(item.name)
                if not item.isfile() and not item.isdir():
                    raise SourceError("Links and special files are not permitted in source TAR archives.")
                normalized = str(relative).casefold()
                if normalized in seen:
                    raise SourceError("Duplicate source TAR member.")
                seen.add(normalized)
                output_path(destination, relative)
            validate_members((safe_relative(item.name), item.isdir()) for item in members)
            for item in members:
                target = output_path(destination, safe_relative(item.name))
                if item.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source.extractfile(item) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
    else:
        raise SourceError("Unsupported source archive format.")


def validate_entry(entry: dict) -> None:
    if not SHA.fullmatch(entry.get("revision", "")):
        raise SourceError("A source lock entry has no full commit SHA.")
    safe_relative(entry["path"])
    if len(safe_relative(entry["filename"]).parts) != 1:
        raise SourceError("Archive cache names must be simple filenames.")
    if entry["format"] not in {"zip", "tar.gz"}:
        raise SourceError("Unsupported locked source format.")
    expected = entry.get("sha256")
    if expected is not None and not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise SourceError("Invalid locked SHA-256 digest.")
    if entry["repository"] == "libyuv":
        url = f"https://chromium.googlesource.com/libyuv/libyuv/+archive/{entry['revision']}.tar.gz"
    elif REPO.fullmatch(entry["repository"]):
        url = f"https://codeload.github.com/{entry['repository']}/zip/{entry['revision']}"
    else:
        raise SourceError("Unsupported source repository in lock file.")
    if entry["url"] != url:
        raise SourceError("Source URL does not match its pinned repository and commit.")


def download(entry: dict, cache: Path) -> Path:
    validate_entry(entry)
    cache.mkdir(parents=True, exist_ok=True)
    archive, expected = cache / entry["filename"], entry.get("sha256")
    checksum_file = archive.with_suffix(archive.suffix + ".sha256")
    if archive.exists():
        if not expected and checksum_file.is_file():
            expected = checksum_file.read_text(encoding="ascii").strip()
            if not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise SourceError("Invalid cached source checksum sidecar.")
        if not expected:
            raise SourceError("Cached source has no expected digest; use a generated lock file or a fresh cache.")
        if digest(archive) != expected:
            raise SourceError("Cached source checksum mismatch; remove the affected archive and retry.")
        entry["sha256"] = expected
        return archive
    partial = archive.with_suffix(archive.suffix + ".part")
    print(f"Downloading {entry['repository']} at {entry['revision']}", flush=True)
    try:
        with request(entry["url"]) as response, partial.open("wb") as dst:
            shutil.copyfileobj(response, dst, length=1024 * 1024)
        actual = digest(partial)
        if expected and actual != expected:
            raise SourceError("Downloaded source checksum mismatch.")
        entry["sha256"] = actual
        os.replace(partial, archive)
        checksum_file.write_text(actual + "\n", encoding="ascii")
    finally:
        partial.unlink(missing_ok=True)
    return archive


def source_entry(repository: str, revision: str, path: str, known: list[dict]) -> dict:
    for existing in known:
        if existing["repository"] == repository and existing["revision"] == revision:
            return dict(existing, path=path)
    if repository == "libyuv":
        return {"repository": repository, "revision": revision, "path": path,
                "filename": f"libyuv-{revision}.tar.gz", "format": "tar.gz",
                "url": f"https://chromium.googlesource.com/libyuv/libyuv/+archive/{revision}.tar.gz"}
    return {"repository": repository, "revision": revision, "path": path,
            "filename": repository.replace("/", "-") + f"-{revision}.zip", "format": "zip",
            "url": f"https://codeload.github.com/{repository}/zip/{revision}"}


def module_entries(repository: str, revision: str, location: Path, prefix: str, known: list[dict]) -> list[dict]:
    modules_file = location / ".gitmodules"
    if not modules_file.exists():
        return []
    if repository == "libyuv":
        raise SourceError("New nested googlesource submodules require a reviewed lock update.")
    config = configparser.ConfigParser(interpolation=None)
    config.read(modules_file, encoding="utf-8")
    tree = api(repository, f"git/trees/{revision}?recursive=1")
    if tree.get("truncated"):
        raise SourceError("GitHub returned a truncated source tree; cannot pin every submodule.")
    commits = {item["path"]: item["sha"] for item in tree["tree"] if item["type"] == "commit"}
    entries = []
    for section in config.sections():
        path, url = config[section]["path"], config[section]["url"]
        relative = safe_relative(path)
        if str(relative) == "." or path not in commits:
            raise SourceError("Cannot match an upstream submodule to its pinned Git commit.")
        match = re.fullmatch(r"https://github.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?", url)
        if match:
            module_repo = match.group(1)
        elif url.rstrip("/") == "https://chromium.googlesource.com/libyuv/libyuv":
            module_repo = "libyuv"
        else:
            raise SourceError("New submodule host requires review before building.")
        entries.append(source_entry(module_repo, commits[path], (PurePosixPath(prefix) / relative).as_posix(), known))
    if len(entries) != len(commits):
        raise SourceError("Not every upstream Git submodule is represented in .gitmodules.")
    return entries


def apply_patches(target: Path, project: Path = ROOT) -> None:
    # An ignored generated tree may be inside the patch repository. Prevent Git
    # from discovering that enclosing repository and silently skipping hunks.
    git_env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(target.resolve().parent))
    for variable in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        git_env.pop(variable, None)
    patches = [project / "patches/tg-media.patch"]
    patches += sorted(p for p in (project / "patches").glob("*.patch") if p not in patches)
    for patch in patches:
        print(f"Applying {patch.name}", flush=True)
        for args in (["--check"], []):
            result = subprocess.run(["git", "apply", *args, str(patch.resolve())], cwd=target, env=git_env,
                                    capture_output=True, text=True, encoding="utf-8", errors="replace")
            if result.returncode:
                detail = "\n".join(line for line in result.stderr.splitlines()
                                   if line.startswith(("error: patch failed:", "error: corrupt patch at line")))
                raise SourceError(f"{patch.name} is incompatible with this upstream source.\n{detail}\n"
                                  "Review and refresh the patch; no APK was built.")
    shutil.copytree(project / "overlay", target, dirs_exist_ok=True)


def project_inputs(project: Path = ROOT) -> dict:
    return {path.relative_to(project).as_posix(): digest(path)
            for folder in ("patches", "overlay") for path in sorted((project / folder).rglob("*"))
            if path.is_file()}


def snapshot(target: Path, project: Path = ROOT) -> None:
    print("Recording the complete prepared-source inventory", flush=True)
    paths = [path for path in sorted(target.rglob("*")) if path.is_file()]
    files = {}
    for index, path in enumerate(paths, 1):
        files[path.relative_to(target).as_posix()] = digest(path)
        if index % max(len(paths) // 10, 1) == 0 or index == len(paths):
            print(f"Recorded source inputs: {index}/{len(paths)}", flush=True)
    (target / "prepared-manifest.json").write_text(json.dumps({
        "patch_inputs": project_inputs(project), "files": files,
    }, indent=2) + "\n", encoding="utf-8")


def verify_prepared(target: Path, project: Path = ROOT) -> dict:
    print("Verifying the complete prepared-source inventory", flush=True)
    manifest = json.loads((target / "prepared-manifest.json").read_text(encoding="utf-8"))
    if manifest["patch_inputs"] != project_inputs(project):
        raise SourceError("Patch or overlay changed after source preparation; prepare a fresh tree.")
    total = len(manifest["files"])
    for index, (name, expected) in enumerate(manifest["files"].items(), 1):
        path = output_path(target, safe_relative(name))
        if not path.is_file() or digest(path) != expected:
            raise SourceError("Prepared source changed; prepare a fresh tree before building or packaging.")
        if index % max(total // 10, 1) == 0 or index == total:
            print(f"Verified source inputs: {index}/{total}", flush=True)
    modules = {PurePosixPath(name).parent for name in manifest["files"]
               if PurePosixPath(name).name in {"build.gradle", "build.gradle.kts"}}
    allowed_generated = {(module / folder).as_posix() for module in modules
                         for folder in ("build", ".gradle", ".cxx", ".kotlin")}
    for directory, folders, filenames in os.walk(target):
        folders[:] = [name for name in folders
                      if (Path(directory) / name).relative_to(target).as_posix() not in allowed_generated]
        for name in filenames:
            relative = (Path(directory) / name).relative_to(target).as_posix()
            if relative != "prepared-manifest.json" and relative not in manifest["files"]:
                raise SourceError("Unexpected file in prepared source; rebuild from a clean preparation.")
    return manifest


def prepare(target: Path, cache: Path, *, ref: str = "latest", lock: Path | None = None,
            project: Path = ROOT) -> list[dict]:
    if target.exists():
        raise SourceError("Source destination already exists; use a fresh destination to avoid mixed revisions.")
    known = json.loads((project / "sources.lock.json").read_text(encoding="utf-8"))
    for path in sorted(cache.glob("resolved-*.json")) if cache.exists() else []:
        known += json.loads(path.read_text(encoding="utf-8"))
    entries = json.loads(lock.read_text(encoding="utf-8")) if lock else [source_entry(UPSTREAM, resolve_ref(ref), ".", known)]
    if not entries or entries[0]["repository"] != UPSTREAM or entries[0]["path"] != ".":
        raise SourceError("The first lock entry must be the official Telegram root.")
    for item in entries:
        validate_entry(item)
        if lock and not item.get("sha256"):
            raise SourceError("Locked source must include a checksum for every archive.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".preparing-", dir=target.parent) as folder:
        staging = Path(folder) / "Telegram"
        staging.mkdir()
        used, index = set(), 0
        while index < len(entries):
            entry = entries[index]
            key = entry["path"].casefold()
            if key in used or len(entries) > 100:
                raise SourceError("Duplicate or excessive source submodules.")
            used.add(key)
            archive = download(entry, cache)
            destination = output_path(staging, safe_relative(entry["path"]))
            print(f"Extracting {entry['repository']}", flush=True)
            extract(archive, destination, entry["format"])
            if not lock:
                entries += module_entries(entry["repository"], entry["revision"], destination, entry["path"], known)
            index += 1
        (cache / f"resolved-{entries[0]['revision']}.json").write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
        apply_patches(staging, project)
        (staging / "sources.lock.json").write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
        snapshot(staging, project)
        staging.rename(target)
    print(f"Prepared official source {entries[0]['revision']} with {len(entries) - 1} pinned submodules.", flush=True)
    return entries
