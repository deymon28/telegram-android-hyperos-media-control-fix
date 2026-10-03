"""Value-redacting checks for project-owned publication inputs.

Upstream source fixtures can deliberately contain private keys and example paths.
Verify their provenance before deciding which findings are expected. Also check
the exact private values locally before every release.
"""
from __future__ import annotations

import hashlib
import pathlib
import re


_WINDOWS_PATH = re.compile(
    rb"(?i)[a-z]:[\\/]+(?:Users|Documents and Settings|PROJECTS)[\\/]+[^\x00\s\"'<>;]*"
)
_UNIX_HOME = re.compile(rb"/(?:home|Users)/([^/\x00\s\"'<>;]+)/")
_EXAMPLE_ACCOUNTS = {
    b"user", b"username", b"yourname", b"your-user", b"your_user", b"example",
    b"runner", b"builder", b"build", b"buildbot", b"jenkins", b"ci", b"test",
}
# Public metadata verified in the pinned upstream BoringSSL/tde2e and FFmpeg
# static libraries and the isoparser 1.0.6 dependency. Hash only the home prefix.
# Exact caller-supplied private values are checked before these exemptions.
_UPSTREAM_HOME_PREFIXES = {
    "3ad999ae7117832fbc00d3524e7e62c87c130a8fc09f640b36f094798d8eed94",
    "17ece5cbd625ed4a3248e25c9e8d15c566fd9a0994f53eb36ab113524e9522bf",
    "c1f3b7218fec822dd0af27e7ff160be5cfc61c9b36ef933acfe474f8eb87fa86",
}
_PRIVATE_KEY = re.compile(
    rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
)
_TOKENS = re.compile(
    rb"(?:\bgh[pousr]_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{30,}\b|"
    rb"\b[0-9]{7,12}:[A-Za-z0-9_-]{30,50}\b)"
)


def _private_encodings(deny_values):
    result = []
    for value in deny_values:
        if isinstance(value, bytes):
            if value:
                result.append(value)
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                continue
        else:
            value = str(value)
        if value:
            result.extend(value.encode(codec) for codec in ("utf-8", "utf-16le", "utf-16be"))
    return result


def _safe_label(label, deny_values):
    value = str(label).replace("\\", "/")
    if pathlib.PurePosixPath(value).is_absolute() or re.match(r"^[a-zA-Z]:/", value):
        value = pathlib.PurePosixPath(value).name
    for private in deny_values:
        if isinstance(private, bytes):
            private = private.decode("utf-8", errors="ignore")
        private = str(private)
        if private:
            value = re.sub(re.escape(private), "<redacted>", value, flags=re.I)
    value = _TOKENS.sub(b"<redacted>", value.encode("utf-8", errors="replace")).decode("utf-8")
    return value


def scan_bytes(data: bytes, label: str, deny_values=()) -> list[dict[str, str]]:
    """Return category/label findings, never matched bytes or secret values."""
    deny_values = tuple(deny_values)
    safe_label = _safe_label(label, deny_values)
    categories = set()
    lowered = data.lower()
    if any(value.lower() in lowered for value in _private_encodings(deny_values)):
        categories.add("private-value")
    searchable = data.replace(b"\x00", b"")  # Include UTF-16 paths and key headers.
    for match in _WINDOWS_PATH.finditer(searchable):
        parts = re.split(rb"[\\/]+", match.group(0))
        if len(parts) > 2 and parts[1].lower() != b"projects" and parts[2].lower() in _EXAMPLE_ACCOUNTS:
            continue
        categories.add("local-windows-path")
    for match in _UNIX_HOME.finditer(searchable):
        if match.group(1).lower() in _EXAMPLE_ACCOUNTS:
            continue
        if hashlib.sha256(match.group(0)).hexdigest() in _UPSTREAM_HOME_PREFIXES:
            continue
        categories.add("local-unix-home-path")
    if _PRIVATE_KEY.search(searchable):
        categories.add("private-key-material")
    if _TOKENS.search(searchable):
        categories.add("credential-token")
    return [{"category": category, "label": safe_label} for category in sorted(categories)]


def scan_file(path: pathlib.Path, label: str | None = None, deny_values=()) -> list[dict[str, str]]:
    """Scan one file, using only its basename when no relative label is given."""
    path = pathlib.Path(path)
    try:
        data = path.read_bytes()
    except OSError:
        raise ValueError("Could not read the publication input.") from None
    return scan_bytes(data, label or path.name, deny_values)
