"""Validate a public APK without printing embedded or local private values."""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
import zlib

from privacy_checks import scan_bytes


PACKAGE = "org.telegram.tgmedia.web"
SETUP_ACTIVITY = "org.telegram.ui.ApiSetupActivity"
_PRIVATE_SUFFIXES = (".jks", ".keystore", ".pem", ".p12", ".pfx", ".key", ".session", ".log")
_PRIVATE_DIRECTORIES = {".git", ".tools", ".gradle", ".cxx", "__pycache__"}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _tool_output(build_tools: pathlib.Path, name: str, arguments: list[str]) -> str:
    executable = build_tools / (name + (".exe" if os.name == "nt" else ""))
    if not executable.is_file():
        raise RuntimeError(f"Required Android build tool is missing: {name}.")
    try:
        result = subprocess.run(
            [str(executable), *arguments], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError(f"Android build tool could not complete: {name}.") from None
    if result.returncode:
        # stderr can contain local paths or embedded artifact data.
        raise RuntimeError(f"Android build tool rejected the artifact: {name}.")
    return result.stdout


def _parse_xmltree(text: str) -> ET.Element:
    """Read aapt2's hierarchy, binding attributes to their actual element."""
    stack = []
    root = None
    for line in text.splitlines():
        indent = len(line) - len(line.lstrip())
        element = re.match(r"\s*E: ([^\s(]+)", line)
        if element:
            while stack and stack[-1][0] >= indent:
                stack.pop()
            node = ET.Element(element.group(1))
            if stack:
                stack[-1][1].append(node)
            else:
                _require(root is None, "Unexpected multiple binary XML roots.")
                root = node
            stack.append((indent, node))
            continue
        attribute = re.match(r"\s*A: ([^\s=(]+)(?:\([^)]*\))?=(.*)", line)
        if attribute:
            _require(bool(stack), "Binary XML attribute has no element.")
            name, value = attribute.groups()
            if name.startswith("http://schemas.android.com/apk/res/android:"):
                name = "android:" + name.rsplit(":", 1)[1]
            if value.startswith('"'):
                quoted = re.match(r'"((?:\\.|[^"\\])*)"', value)
                _require(quoted is not None, "Invalid binary XML string attribute.")
                value = quoted.group(1)
            else:
                value = value.split(" (Raw:", 1)[0].strip()
            _require(name not in stack[-1][1].attrib, "Duplicate binary XML attribute.")
            stack[-1][1].set(name, value)
    _require(root is not None, "Android tool returned no binary XML.")
    return root


def _manifest_checks(manifest: ET.Element, version_name=None, version_code=None):
    _require(manifest.tag == "manifest", "APK has no manifest root.")
    _require(manifest.get("package") == PACKAGE, "APK has the wrong application ID.")
    if version_name is not None:
        _require(manifest.get("android:versionName") == str(version_name), "APK version name differs from the requested release.")
    if version_code is not None:
        raw = manifest.get("android:versionCode", "")
        try:
            actual_code = int(raw, 16 if raw.startswith("0x") else 10)
        except ValueError:
            raise ValueError("APK has an invalid version code.") from None
        try:
            expected_code = int(version_code)
        except (ValueError, TypeError):
            raise ValueError("Requested version code is invalid.") from None
        _require(actual_code == expected_code, "APK version code differs from the requested release.")
    applications = manifest.findall("application")
    _require(len(applications) == 1, "APK must declare one application.")
    application = applications[0]
    _require(application.get("android:allowBackup") == "false", "App backups are not disabled.")
    for name in ("debuggable", "testOnly"):
        _require(application.get("android:" + name, "false") == "false", "APK is debuggable or test-only.")
    for name in ("fullBackupContent", "dataExtractionRules"):
        _require(application.get("android:" + name, "").startswith("@"), "APK is missing credential backup rules.")
    activities = [node for node in application.findall("activity") if node.get("android:name") == SETUP_ACTIVITY]
    _require(len(activities) == 1, "APK is missing the declared API setup activity.")
    _require(activities[0].get("android:exported") == "false", "API setup activity must not be directly exported.")
    for node in application:
        name = node.get("android:name", "")
        _require(name != "com.google.firebase.provider.FirebaseInitProvider", "APK includes Firebase initialization.")
        _require(name != "com.google.android.maps.v2.API_KEY", "APK includes embedded map configuration.")


def _dex_checks(xml: str) -> tuple[bool, bool]:
    try:
        tree = ET.fromstring(xml)
    except ET.ParseError:
        raise ValueError("Android tool returned invalid DEX metadata.") from None
    setup_found = False
    configuration_found = False
    for package in tree.findall("package"):
        for cls in package.findall("class"):
            name = package.get("name", "") + "." + cls.get("name", "")
            if name == SETUP_ACTIVITY:
                _require(not setup_found, "Duplicate API setup DEX class.")
                _require(cls.get("abstract") == "false", "Optimizer made API setup abstract.")
                _require(cls.get("visibility") == "public", "API setup DEX class is inaccessible.")
                _require(any(item.get("visibility") == "public" for item in cls.findall("constructor")), "Optimizer removed the public API setup constructor.")
                setup_found = True
            elif name == "org.telegram.messenger.BuildConfig":
                _require(not configuration_found, "Duplicate Telegram build configuration.")
                expected = {"TG_API_ID": ("int", "0"), "TG_API_HASH": ("java.lang.String", ""), "TG_RUNTIME_API_CONFIG": ("boolean", "true")}
                for field_name, (field_type, field_value) in expected.items():
                    fields = [field for field in cls.findall("field") if field.get("name") == field_name]
                    _require(len(fields) == 1, "Public API build configuration is missing from DEX.")
                    field = fields[0]
                    _require(field.get("type") == field_type and field.get("value") == field_value and field.get("static") == "true" and field.get("final") == "true", "APK has embedded API credentials or runtime setup is disabled.")
                configuration_found = True
    return setup_found, configuration_found


def _entry_checks(name: str, data: bytes, deny_values):
    path = pathlib.PurePosixPath(name)
    _require(not path.is_absolute() and ".." not in path.parts and "\\" not in name and not re.match(r"^[a-zA-Z]:", name), "APK has an unsafe entry path.")
    lower = name.lower()
    _require(not _PRIVATE_DIRECTORIES.intersection(part.lower() for part in path.parts), "APK includes a private build directory.")
    _require(not lower.endswith(_PRIVATE_SUFFIXES), "APK includes a private key, session, or diagnostic artifact.")
    _require(path.name.lower() not in {"local.properties", "gradle.properties"} and not path.name.lower().startswith(".env") and not re.search(r"(?:telegram[-_]?api|signing|credentials|secrets).*\.json$", path.name, re.I), "APK includes private configuration.")
    findings = scan_bytes(data, name, deny_values)
    if findings:
        # An archive filename can itself contain private data. Report categories.
        categories = ", ".join(sorted({item["category"] for item in findings}))
        raise ValueError("APK privacy check failed: " + categories + ".")
    if path.parts and path.parts[0] == "lib" and lower.endswith(".so"):
        _require(len(path.parts) == 3 and path.parts[1] == "arm64-v8a", "APK contains an unexpected native ABI.")
        _require(len(data) >= 20 and data[:6] == b"\x7fELF\x02\x01" and struct.unpack_from("<H", data, 18)[0] == 183, "Native library is not an ARM64 ELF binary.")


def check_apk(apk: pathlib.Path, build_tools: pathlib.Path, *, version_name=None, version_code=None, deny_values=()):
    """Raise ValueError/RuntimeError unless this is a public ARM64 TG Media APK."""
    apk = pathlib.Path(apk).resolve()
    build_tools = pathlib.Path(build_tools).resolve()
    deny_values = tuple(deny_values)
    if not apk.is_file():
        raise ValueError("APK file does not exist.")
    manifest = _parse_xmltree(_tool_output(build_tools, "aapt2", ["dump", "xmltree", str(apk), "--file", "AndroidManifest.xml"]))
    _manifest_checks(manifest, version_name, version_code)
    setup_found = False
    configuration_found = False
    native_found = False
    try:
        with zipfile.ZipFile(apk) as archive:
            names = archive.namelist()
            _require(len(names) == len(set(names)), "APK contains duplicate ZIP entries.")
            for entry in archive.infolist():
                try:
                    data = archive.read(entry)
                except (RuntimeError, EOFError, NotImplementedError, zlib.error):
                    raise ValueError("APK contains an unreadable archive entry.") from None
                _entry_checks(entry.filename, data, deny_values)
                if re.fullmatch(r"lib/arm64-v8a/libtmessages\.[0-9]+\.so", entry.filename):
                    native_found = True
                if not re.fullmatch(r"classes(?:[0-9]+)?\.dex", entry.filename):
                    continue
                if not any(descriptor in data for descriptor in (b"Lorg/telegram/ui/ApiSetupActivity;", b"Lorg/telegram/messenger/BuildConfig;")):
                    continue
                with tempfile.TemporaryDirectory(prefix="tg-public-dex-") as folder:
                    dex = pathlib.Path(folder) / "classes.dex"
                    dex.write_bytes(data)
                    xml = _tool_output(build_tools, "dexdump", ["-e", "-l", "xml", str(dex)])
                setup, config = _dex_checks(xml)
                _require(not (setup and setup_found), "Duplicate API setup DEX class.")
                _require(not (config and configuration_found), "Duplicate Telegram build configuration.")
                setup_found |= setup
                configuration_found |= config
    except (zipfile.BadZipFile, OSError):
        raise ValueError("APK archive is unreadable or corrupt.") from None
    _require(native_found, "APK is missing its ARM64 Telegram native library.")
    _require(setup_found, "API setup is missing from compiled DEX.")
    _require(configuration_found, "Public API build configuration could not be verified in DEX.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=pathlib.Path)
    parser.add_argument("--build-tools", type=pathlib.Path)
    parser.add_argument("--version-name")
    parser.add_argument("--version-code", type=int)
    args = parser.parse_args(argv)
    build_tools = args.build_tools
    if build_tools is None:
        sdk = os.environ.get("ANDROID_HOME")
        if not sdk:
            parser.error("Set ANDROID_HOME or pass --build-tools.")
        build_tools = pathlib.Path(sdk) / "build-tools/36.0.0"
    try:
        check_apk(args.apk, build_tools, version_name=args.version_name, version_code=args.version_code)
    except (ValueError, RuntimeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: public APK manifest, ABI, runtime API configuration, and privacy checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
