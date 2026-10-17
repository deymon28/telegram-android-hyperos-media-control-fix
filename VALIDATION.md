# Release validation

## 13.0.2-hyperos.1

Checked on 2026-10-17 against official Telegram commit
`f32ffeec763f0bdbf4bec25b52d654f77bff733d` (13.0.2, upstream code 7175).

APK SHA-256: `e5a98b0d1008c53491aee0278ed991877edb4135695cd0926e8d50dc485662c0`

### Build and artifact evidence

- The complete Windows release build succeeded: 334 Gradle tasks, 11 minutes 21 seconds,
  with the same toolchain as 12.10.6 (JDK 21, Gradle 8.13, SDK 36, Build Tools 36.0.0 and
  35.0.0, NDK 27.2.12479018, CMake 3.22.1).
- Package `org.telegram.tgmedia.web`, version code `717501`, ABI `arm64-v8a`, minimum
  Android 6.0. It is signed with the same `CN=TG Media Release` certificate as 12.10.6, so it
  installs over that release and keeps its login.
- Signature (v1 and v2), 16 KB ZIP alignment, manifest flags, compiled API setup, empty
  compiled API credentials, ARM64 ELF, local-path, and English-only localization checks
  passed. The APK contains 7,342 ZIP entries.

### Source and regression evidence

- 20 upstream archives (19 pinned submodules) were downloaded and verified; both patches
  applied. Preparation recorded 48,611 file hashes, checked before compiling and packaging.
- All 108 host tests passed on the prepared source. New tests compile the media-button
  marker against Android API stubs and drive 11 ownership transitions, check that the music
  service drives and releases it, check idle in-chat sound release, and cover content-pinned
  googlesource archives and unattended `--skip-built` runs.
- The Git inputs, APK, and 48,648-entry corresponding-source archive were checked locally
  against private credentials, signing secrets, identity markers, and device markers. No
  matches were found.

### Device evidence

Installed over 12.10.6-hyperos.2 on a Xiaomi 14T (HyperOS 3, Android 16) with Realme Buds
Air 8 Pro and Huawei Watch GT 6. The existing login and chats were kept.

- Official Telegram was running with an idle in-chat `SoundPool` reported as an active
  player, the condition that previously moved headset buttons to it after a pause.
  `dumpsys media_session` kept TG Media as the media button session through repeated
  pause and resume cycles; five consecutive headset play/pause commands reached TG Media.
- When YouTube Music started playback, the media button session moved to it and later
  headset commands reached YouTube Music, so TG Media yields accessory controls.
- The tester reported normal behavior. Full-screen video and picture-in-picture controls
  on 13.0.2 were not separately recorded.

If HyperOS "play audio from multiple apps" is enabled, apps are not asked to pause when
another app starts playback; this is a system setting, not TG Media behavior.

## 12.10.6-hyperos.2

Checked on 2026-10-03 against official Telegram commit
`f2908b14133bbffbf7ab04f641ecb5bfaf533242` (12.10.6, upstream code 7112).

APK SHA-256: `39fd7a0d6666abf30b139b9bd113a31a1d024cde5867d149eb7da6bc806bfb66`

### Build and artifact evidence

- The complete Windows release build succeeded: 334 Gradle tasks, 15 minutes 55 seconds.
- Toolchain: JDK 21, Gradle 8.13, Android SDK 36, Build Tools 36.0.0,
  NDK 27.2.12479018, and CMake 3.22.1. Media3 also uses Build Tools 35.0.0.
- Package: `org.telegram.tgmedia.web`; version code: `711202`; ABI: `arm64-v8a`.
- APK signatures verified with v1 and v2 signing; ZIP alignment passed with
  16 KB page alignment. Release signing uses the generic subject `CN=TG Media Release`.
- Packaged manifest checks cover release/debug flags, disabled backups, API setup,
  and removal of sample Firebase initialization and the sample map key.
- Compiled API setup retains a concrete activity and constructor after optimization.
  The compiled public configuration has API ID `0`, an empty API hash, and runtime
  configuration enabled. Each installation supplies its own credentials.
- Native ELF architecture and local-path checks passed. The only localization
  asset is `assets/localization_en.bin`.
- The APK contains 7,225 ZIP entries. Use the release's `SHA256SUMS.txt` to verify
  the downloaded APK and complete corresponding-source ZIP.

### Source and regression evidence

- All 16 upstream archives, including 15 pinned submodules, were downloaded and
  checked against `sources.lock.json`. Both patches applied to this exact baseline.
- Preparation recorded 47,186 file hashes. The builder verifies the inventory
  before compilation and again before packaging, and rejects unexpected inputs.
- All 95 host tests passed on the complete prepared source. They cover archive
  traversal and links, downloads and locks, real patch application, build failure
  gates, source packaging, credential redaction, process timeouts, compiled APK
  checks, and Java probes for setup, login errors, media registration, and PiP.
- APK rejection gates are also exercised with optimized Python (`python -O`).
- The reviewed working files, Git index, and reachable history had no matches
  for locally supplied private credentials, identity markers, or device markers.
  Commit identities use generic names and GitHub noreply addresses.

Before publication, a separate local gate compares every decompressed APK and
source-archive entry against the same exact private values, without printing them.
It also checks archive integrity and final Git inputs. Published release notes
record the final artifact audit result. Upstream public examples, license notices,
cryptographic test fixtures, and verified upstream compiler metadata are retained;
they are not release-maintainer credentials.

### Device coverage and limits

This Media3-based APK has not been installed or exercised on a physical HyperOS
device. It is distributed as a prerelease. Host tests and successful compilation
do not establish actual accessory behavior, login success, or background reliability.

Remaining device checks include real login with the tester's own API credentials,
audio/video play-pause while paused, full-screen/PiP transitions, screen locking,
background behavior, competing media apps, and more accessory models.
