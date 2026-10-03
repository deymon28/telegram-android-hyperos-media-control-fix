# Building TG Media

## One-command current-source build

The autobuilder resolves the current official Telegram `master` to a full commit,
fetches its exact submodules, applies the reviewed patches and overlay, runs host
regressions, and builds a signed ARM64 APK. It verifies the manifest, compiled API
configuration, native architecture, signature, alignment, privacy checks, and the
matching source archive before placing a completed release in `dist/`.

A newer Telegram can require a patch or toolchain update. A conflict or failed
check stops the run. The script never silently substitutes an older revision and
never uploads anything to GitHub by itself.

Requirements: Python 3.12 or newer, Git, JDK 21, an Android SDK with accepted
licenses, and enough free disk space for native compilation (allow at least 30 GB).
The checked baseline uses SDK 36, Build Tools 36.0.0 and 35.0.0, NDK 27.2.12479018, and CMake
3.22.1. Gradle is selected from the upstream wrapper version; if a matching local
installation is unavailable, its distribution is downloaded and checked against
the official Gradle SHA-256. The current baseline requires Gradle 8.13.

Set `JAVA_HOME` and `ANDROID_HOME`. Install SDK packages with your SDK manager:

```powershell
sdkmanager 'platforms;android-36' 'build-tools;36.0.0' 'build-tools;35.0.0' 'ndk;27.2.12479018' 'cmake;3.22.1'
```

Keep the signing key outside this repository and every generated source tree.
Use the exact generic certificate subject below so the APK does not disclose a
personal name, email, or address. Protect and back up the key and its password;
future updates must use the same key. Do not commit either value.

```powershell
keytool -genkeypair -keystore ../private-signing/tg-media.jks -alias tgmedia -keyalg RSA -keysize 3072 -validity 10000 -dname 'CN=TG Media Release'
$signingSecret = Read-Host 'Signing password' -AsSecureString
$env:TG_SIGNING_PASSWORD = [System.Net.NetworkCredential]::new('', $signingSecret).Password
try {
    python scripts/autobuild.py --keystore ../private-signing/tg-media.jks
} finally {
    Remove-Item Env:TG_SIGNING_PASSWORD -ErrorAction SilentlyContinue
}
```

Create the private signing directory before running `keytool`. Use equivalent environment variables on Linux. The current release is validated
on Windows; a complete Linux build has not been validated.

Do not set `TG_API_ID`, `TG_API_HASH`, or their `ORG_GRADLE_PROJECT_` equivalents.
Public builds reject embedded credentials and ask each user to configure their
own API access after installation. The password is passed to Gradle through the
environment, never as a command-line argument or an artifact.

## Reproducibility and updates

```powershell
# Rebuild the reviewed baseline and its hash-pinned submodules.
python scripts/autobuild.py --locked --keystore ../private-signing/tg-media.jks

# Resolve an explicit official ref, without building.
python scripts/autobuild.py --ref master --prepare-only

# Resume a prepared tree; its inventory and patch hashes must still match.
python scripts/autobuild.py --prepared-source .work/PREVIOUS-RUN/Telegram --keystore ../private-signing/tg-media.jks

# Read all options.
python scripts/autobuild.py --help
```

Each run uses a fresh generated directory under `.work/`. Downloads are cached in
`.cache/` and checked before reuse. `--cache`, `--work-dir`, `--output`, `--gradle`,
and `--workers` customize paths and resource use. Four Gradle workers are used by
default. Gradle has a 120-minute timeout, configurable with `--timeout-minutes`;
cancellation stops the process tree belonging to the invoked build. JDK, SDK,
and Gradle dependency resolution still require network access
unless the required packages and dependencies are cached.

`--revision` defaults to 2. The displayed name is the upstream version plus
`-hyperos.REVISION`; the Android version code is `upstreamCode * 100 + revision`.
For another release on the same upstream version, increment the revision. Explicit
`--version-name` and `--version-code` overrides are available. Existing release
output directories are never overwritten.

`--locked path/to/sources.lock.json` reproduces a prior resolution from its saved
manifest. Hashes for newly discovered commits are recorded from HTTPS downloads;
this records provenance and detects later corruption, not an independent signature
or an audit of all upstream changes. New or unsupported submodule hosts stop the
build for review. Source archive traversal, escaping links, and case/path conflicts
are rejected. Internal file links are materialized for Windows; original links,
including upstream dangling test-resource links, are restored in source ZIPs.

## Outputs and publication

A successful run creates one `dist/TG-Media-VERSION/` directory containing:

- The installable ARM64 APK.
- A complete corresponding-source ZIP, including pinned submodules, patches,
  overlay, tests, build scripts, and the resolved source lock.
- `sources.lock.json`, `build-provenance.json`, and `SIGNING-CERTIFICATE.txt`.
- `SHA256SUMS.txt` covering the completed release files.

The source inventory prevents local files and build outputs from being added to
the source archive. Project-owned inputs use an explicit file-type allowlist.
The official source retains its public example configuration, third-party license
notices, and cryptographic test fixtures; these are not maintainer credentials.

Review the exact commit and release files before manual publication. In addition
to generic checks, locally scan for the maintainer's exact private values without
printing them. The script never scans another app's account data or publishes
local build logs. Device validation is reported separately from compilation and
host tests; the build provenance starts with `device_tested: false`.

## Development checks

```powershell
python scripts/prepare-source.py
python -m unittest discover -s tests -v
```

`prepare-source.py` uses the checked `sources.lock.json` by default; `--ref latest`
opts into current source. It refuses an existing destination. Set `TG_SOURCE_DIR`
to test a different prepared Telegram tree. Use `autobuild.py --prepared-source`
to build an existing verified preparation.
