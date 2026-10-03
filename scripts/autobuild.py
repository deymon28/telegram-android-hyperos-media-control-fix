"""Download current official Telegram, apply TG Media, test, and build a public APK."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import zipfile

from source_tools import ROOT, SourceError, digest, extract, prepare, request, resolve_ref, verify_prepared


class BuildError(RuntimeError):
    pass


def stop_process_tree(child):
    if child.poll() is not None:
        return True
    tree_stopped = True
    if os.name == "nt":
        result = subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        tree_stopped = result.returncode == 0
        if not tree_stopped:
            child.kill()
    else:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            return True
    try:
        child.wait(timeout=15)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            os.killpg(child.pid, signal.SIGKILL)
        else:
            child.kill()
        child.wait(timeout=15)
    return tree_stopped


def run(command, *, cwd=ROOT, env=None, log=None, timeout=7200):
    settings = env or os.environ
    secret = settings.get("TG_SIGNING_PASSWORD", "")
    with subprocess.Popen([str(arg) for arg in command], cwd=cwd, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace", start_new_session=os.name != "nt") as child:
        expired = threading.Event()
        tree_stopped = threading.Event()

        def deadline():
            expired.set()
            if stop_process_tree(child):
                tree_stopped.set()

        timer = threading.Timer(timeout, deadline)
        timer.daemon = True
        timer.start()
        try:
            for line in child.stdout:
                if secret:
                    line = line.replace(secret, "[REDACTED]")
                # Build logs remain local, but also scrub common host identity prefixes.
                for prefix in (str(Path.home()), str(ROOT.parent)):
                    line = line.replace(prefix, "<local>").replace(prefix.replace("\\", "/"), "<local>")
                print(line, end="", flush=True)
                if log:
                    log.write(line)
                    log.flush()
            code = child.wait()
        except KeyboardInterrupt:
            stop_process_tree(child)
            raise
        finally:
            timer.cancel()
            if expired.is_set():
                timer.join(timeout=45)
    if expired.is_set():
        detail = "Its process tree was stopped." if tree_stopped.is_set() else "The build process stopped; child-process termination could not be confirmed."
        raise BuildError("Command exceeded its timeout. " + detail + " No release was published.")
    if code:
        raise BuildError("Command failed; no release artifacts were published by this run.")


def properties(path: Path) -> dict:
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def version(source: Path, revision: int, name=None, code=None):
    upstream = properties(source / "gradle.properties")
    name = name or f"{upstream['APP_VERSION_NAME']}-hyperos.{revision}"
    code = code if code is not None else int(upstream["APP_VERSION_CODE"]) * 100 + revision
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,79}", name) or not 0 < code <= 2100000000:
        raise BuildError("Invalid release version name or Android version code.")
    return name, code


def environment() -> dict:
    env = dict(os.environ)
    if not env.get("JAVA_HOME") or not env.get("ANDROID_HOME"):
        raise BuildError("Set JAVA_HOME to JDK 21 and ANDROID_HOME to the Android SDK.")
    # Reject instead of silently hiding accidental credentials in the caller environment.
    if any(env.get(key) for key in ("TG_API_ID", "TG_API_HASH", "ORG_GRADLE_PROJECT_TG_API_ID", "ORG_GRADLE_PROJECT_TG_API_HASH")):
        raise BuildError("Remove embedded API credential environment variables before a public build.")
    env["PATH"] = str(Path(env["JAVA_HOME"]) / "bin") + os.pathsep + env.get("PATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def locate_gradle(value: str | None, env: dict, source=None, cache=None) -> str:
    required = None
    if source:
        wrapper = properties(source / "gradle/wrapper/gradle-wrapper.properties")
        match = re.fullmatch(r"https\\?://services.gradle.org/distributions/gradle-([0-9]+(?:\.[0-9]+)+)-bin.zip", wrapper.get("distributionUrl", ""))
        if not match:
            raise BuildError("Upstream Gradle distribution changed; review its toolchain URL.")
        required = match.group(1)
    candidate = value or shutil.which("gradle", path=env["PATH"])
    if not candidate and env.get("GRADLE_HOME"):
        candidate = str(Path(env["GRADLE_HOME"]) / "bin" / ("gradle.bat" if os.name == "nt" else "gradle"))
    if candidate and Path(candidate).is_file() and required:
        result = subprocess.run([str(candidate), "--version"], env=env, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=90)
        if result.returncode or not re.search(rf"(?m)^Gradle {re.escape(required)}\s*$", result.stdout):
            if value:
                raise BuildError("The supplied Gradle version does not match upstream gradle-wrapper.properties.")
            candidate = None
    if (not candidate or not Path(candidate).is_file()) and required:
        cache = (cache or ROOT / ".cache") / "toolchains"
        cache.mkdir(parents=True, exist_ok=True)
        url = f"https://downloads.gradle.org/distributions/gradle-{required}-bin.zip"
        with request(url + ".sha256") as response:
            expected = response.read().decode("ascii").strip()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise BuildError("Gradle distribution checksum is invalid.")
        archive = cache / f"gradle-{required}-bin.zip"
        if not archive.exists():
            print(f"Downloading Gradle {required} with its official SHA-256 checksum", flush=True)
            partial = archive.with_suffix(".zip.part")
            try:
                with request(url) as response, partial.open("wb") as out:
                    shutil.copyfileobj(response, out, length=1024 * 1024)
                if digest(partial) != expected:
                    raise BuildError("Gradle distribution checksum mismatch.")
                partial.rename(archive)
            finally:
                partial.unlink(missing_ok=True)
        if digest(archive) != expected:
            raise BuildError("Cached Gradle distribution checksum mismatch.")
        install = cache / f"gradle-{required}"
        if not install.exists():
            with tempfile.TemporaryDirectory(prefix=".gradle-", dir=cache) as folder:
                unpacked = Path(folder) / "distribution"
                extract(archive, unpacked, "zip")
                (unpacked / ".archive-sha256").write_text(expected, encoding="ascii")
                unpacked.rename(install)
        if not (install / ".archive-sha256").is_file() or (install / ".archive-sha256").read_text() != expected:
            raise BuildError("Gradle cache has no matching verified distribution marker.")
        candidate = str(install / "bin" / ("gradle.bat" if os.name == "nt" else "gradle"))
        if os.name != "nt":
            Path(candidate).chmod(0o755)
    if not candidate or not Path(candidate).is_file():
        raise BuildError("Gradle is unavailable; set GRADLE_HOME or pass --gradle.")
    return str(Path(candidate).resolve())


def generated_api_check(source: Path) -> None:
    configs = list((source / "TMessagesProj/build/generated/source/buildConfig").rglob("BuildConfig.java"))
    public_configs = [path for path in configs if re.search(r"(?m)^\s*public static final boolean TG_RUNTIME_API_CONFIG = true;", path.read_text(encoding="utf-8"))]
    if not public_configs:
        raise BuildError("Missing runtime-configured generated BuildConfig.")
    for path in public_configs:
        text = path.read_text(encoding="utf-8")
        if (not re.search(r"(?m)^\s*public static final int TG_API_ID = 0;\s*$", text)
                or not re.search(r'(?m)^\s*public static final String TG_API_HASH = "";\s*$', text)):
            raise BuildError("Generated build configuration contains embedded API credentials.")


def apk_checker():
    spec = importlib.util.spec_from_file_location("public_apk_check", ROOT / "scripts/check-public-apk.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check_apk


def project_files():
    top = (".gitattributes", ".gitignore", "README.md", "BUILDING.md",
           "PRIVACY.md", "CHANGELOG.md", "VALIDATION.md", "LICENSE", "SIGNING-CERTIFICATE.txt")
    for name in top:
        path = ROOT / name
        if path.is_file():
            if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):
                raise BuildError("Unexpected symlink in project publication inputs.")
            yield path
    types = {"scripts": {".py"}, "tests": {".py"}, "patches": {".patch"},
             "overlay": {".java", ".xml"}}
    for folder, suffixes in types.items():
        for path in sorted((ROOT / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()) or path.suffix not in suffixes:
                    raise BuildError("Unexpected file type or symlink in project publication inputs.")
                yield path


def package_source(source: Path, destination: Path, manifest: dict) -> None:
    # The preparation inventory excludes every later build output and arbitrary local file.
    links = {}
    for name in manifest["files"]:
        if name.endswith(".tg-source-links.json"):
            metadata = source / name
            for relative, target in json.loads(metadata.read_text(encoding="utf-8")).items():
                links[(metadata.parent / relative).relative_to(source).as_posix()] = target
    with zipfile.ZipFile(destination, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in project_files():
            archive.write(path, "TG-Media-source/" + path.relative_to(ROOT).as_posix())
        archive.write(source / "sources.lock.json", "TG-Media-source/sources.lock.json")
        for name in manifest["files"]:
            if name in links:
                entry = zipfile.ZipInfo("TG-Media-source/Telegram/" + name)
                entry.create_system = 3
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(entry, links[name].encode("utf-8"))
            else:
                archive.write(source / name, "TG-Media-source/Telegram/" + name)
        archive.write(source / "prepared-manifest.json", "TG-Media-source/Telegram/prepared-manifest.json")
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise BuildError("Corresponding-source archive integrity check failed.")


def check_project_privacy() -> None:
    from privacy_checks import scan_bytes
    for path in project_files():
        findings = scan_bytes(path.read_bytes(), path.relative_to(ROOT).as_posix())
        if findings:
            raise BuildError("Publication privacy scan failed for project-owned files.")


def build(source: Path, args, env: dict) -> Path:
    manifest = verify_prepared(source)
    gradle = locate_gradle(args.gradle, env, source, args.cache.resolve())
    if not args.keystore or not args.keystore.is_file() or not env.get("TG_SIGNING_PASSWORD"):
        raise BuildError("Supply --keystore and TG_SIGNING_PASSWORD securely; a private signing key is required.")
    if args.keystore.resolve().is_relative_to(ROOT.resolve()) or args.keystore.resolve().is_relative_to(source.resolve()):
        raise BuildError("Keep the signing key outside this repository and all downloaded source trees.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.key_alias):
        raise BuildError("Invalid signing key alias.")
    name, code = version(source, args.revision, args.version_name, args.version_code)
    sdk = Path(env["ANDROID_HOME"])
    build_tools = sdk / "build-tools" / args.build_tools
    exe = ".bat" if os.name == "nt" else ""
    signer = build_tools / ("apksigner" + exe)
    aligner = build_tools / ("zipalign.exe" if os.name == "nt" else "zipalign")
    if not signer.is_file() or not aligner.is_file():
        raise BuildError("Required Android Build Tools are missing; install the documented SDK packages.")
    env = dict(env, TG_SOURCE_DIR=str(source))
    print("Running host regression and publication checks", flush=True)
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], env=env)
    check_project_privacy()
    args.output.mkdir(parents=True, exist_ok=True)
    output = args.output / f"TG-Media-{name}"
    if output.exists():
        raise BuildError("Release output already exists; use a new revision or a different --output directory.")
    print(f"Building TG Media {name}, Android version code {code}, ARM64", flush=True)
    with (source.parent / "build.log").open("w", encoding="utf-8") as log:
        run([gradle, ":TMessagesProj_AppStandalone:assembleAfatStandalone", "--console=plain", "--no-daemon",
             f"--max-workers={args.workers}", "-PTG_RUNTIME_API_CONFIG=true", "-PAPP_PACKAGE=org.telegram.tgmedia",
             f"-PAPP_VERSION_NAME={name}", f"-PPERSONAL_VERSION_CODE={code}",
             f"-PTG_SIGNING_KEYSTORE={args.keystore.resolve()}", f"-PTG_SIGNING_ALIAS={args.key_alias}",
             "-Pandroid.injected.build.abi=arm64-v8a", "-Pandroid.injected.testOnly=false"],
            cwd=source, env=env, log=log, timeout=getattr(args, "timeout_minutes", 120) * 60)
    generated_api_check(source)
    metadata_files = list((source / "TMessagesProj_AppStandalone/build").glob("intermediates/apk/afat/standalone/output-metadata.json"))
    if len(metadata_files) != 1:
        raise BuildError("Expected one release APK output metadata file.")
    metadata_file = metadata_files[0]
    metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    if metadata.get("applicationId") != "org.telegram.tgmedia.web" or len(metadata.get("elements", [])) != 1:
        raise BuildError("Unexpected APK package or output count.")
    item = metadata["elements"][0]
    apk = (metadata_file.parent / item["outputFile"]).resolve()
    if not apk.is_relative_to(metadata_file.parent.resolve()) or item.get("versionCode") != code or item.get("versionName") != name:
        raise BuildError("Unexpected APK output path or version metadata.")
    run([signer, "verify", "--verbose", apk], env=env)
    run([aligner, "-c", "-P", "16", "4", apk], env=env)
    apk_checker()(apk, build_tools, version_name=name, version_code=code,
                  deny_values=(env["TG_SIGNING_PASSWORD"],))
    manifest = verify_prepared(source)
    check_project_privacy()
    with tempfile.TemporaryDirectory(prefix=".release-", dir=args.output) as folder:
        staging = Path(folder) / output.name
        staging.mkdir()
        apk_name = f"TG-Media-{name}-arm64-v8a.apk"
        shutil.copyfile(apk, staging / apk_name)
        package_source(source, staging / f"TG-Media-{name}-source.zip", manifest)
        shutil.copyfile(source / "sources.lock.json", staging / "sources.lock.json")
        certificate_result = subprocess.run([str(signer), "verify", "--print-certs", str(apk)], env=env,
                                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if certificate_result.returncode:
            raise BuildError("Could not inspect the APK signing certificate.")
        certificate = certificate_result.stdout
        subjects = re.findall(r"(?m)^Signer #[0-9]+ certificate DN: (.+)$", certificate)
        if subjects != ["CN=TG Media Release"]:
            raise BuildError("Use one signing certificate with the generic subject CN=TG Media Release.")
        # Save the public certificate digest only, never a potentially personal subject.
        fingerprint = re.search(r"certificate SHA-256 digest: ([0-9a-fA-F]{64})", certificate)
        if not fingerprint:
            raise BuildError("Could not verify the public signing certificate fingerprint.")
        (staging / "SIGNING-CERTIFICATE.txt").write_text("SHA-256: " + fingerprint.group(1).lower() + "\n", encoding="utf-8")
        provenance = {"upstream_commit": json.loads((source / "sources.lock.json").read_text())[0]["revision"],
                      "version_name": name, "version_code": code, "abi": "arm64-v8a",
                      "patch_inputs": manifest["patch_inputs"], "device_tested": False,
                      "signing_certificate_sha256": fingerprint.group(1).lower()}
        (staging / "build-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        checksums = "".join(f"{digest(path)}  {path.name}\n" for path in sorted(staging.iterdir()))
        (staging / "SHA256SUMS.txt").write_text(checksums, encoding="utf-8")
        staging.rename(output)
    print(f"SUCCESS: checked APK and corresponding source are in dist/{output.name}", flush=True)
    return output


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    mode = result.add_mutually_exclusive_group()
    mode.add_argument("--ref", default="latest", help="Official source ref (default: current master)")
    mode.add_argument("--locked", nargs="?", type=Path, const=ROOT / "sources.lock.json", help="Use reviewed or supplied source lock")
    mode.add_argument("--prepared-source", type=Path, help="Reuse a previously verified preparation inventory")
    result.add_argument("--prepare-only", action="store_true", help="Download and patch without building")
    result.add_argument("--cache", type=Path, default=ROOT / ".cache")
    result.add_argument("--work-dir", type=Path, default=ROOT / ".work")
    result.add_argument("--output", type=Path, default=ROOT / "dist")
    result.add_argument("--keystore", type=Path)
    result.add_argument("--key-alias", default="tgmedia")
    result.add_argument("--gradle", help="Path to installed Gradle executable")
    result.add_argument("--build-tools", default="36.0.0")
    result.add_argument("--revision", type=int, choices=range(1, 100), default=2, metavar="1..99")
    result.add_argument("--version-name")
    result.add_argument("--version-code", type=int)
    result.add_argument("--workers", type=int, choices=range(1, 17), default=4, metavar="1..16")
    result.add_argument("--timeout-minutes", type=int, choices=range(1, 1441), default=120, metavar="1..1440")
    return result


def main():
    args = parser().parse_args()
    try:
        env = None if args.prepare_only else environment()
        if not args.prepare_only:
            if not args.keystore or not args.keystore.is_file() or not env.get("TG_SIGNING_PASSWORD"):
                raise BuildError("Supply --keystore and TG_SIGNING_PASSWORD before downloading a build.")
        if args.prepared_source:
            source = args.prepared_source.resolve()
            if args.prepare_only:
                verify_prepared(source)
        else:
            ref = resolve_ref(args.ref) if not args.locked else "locked"
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            source = args.work_dir.resolve() / f"{ref[:12]}-{stamp}" / "Telegram"
            prepare(source, args.cache.resolve(), ref=ref, lock=args.locked)
        if args.prepare_only:
            print("Prepared source: " + str(source), flush=True)
        else:
            build(source, args, env)
    except (SourceError, BuildError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        message = str(exc) if isinstance(exc, (SourceError, BuildError)) else type(exc).__name__
        print("ERROR: " + message, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("CANCELLED: no new release was published.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
