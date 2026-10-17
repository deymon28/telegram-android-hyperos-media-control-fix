# TG Media for HyperOS

An unofficial Telegram Android client with media-control fixes for headphones and
watches on HyperOS, plus a current-upstream APK autobuilder. The reviewed baseline
is Telegram 13.0.2 at commit
[`f32ffeec763f0bdbf4bec25b52d654f77bff733d`](https://github.com/DrKLO/Telegram/tree/f32ffeec763f0bdbf4bec25b52d654f77bff733d).

TG Media uses the separate package `org.telegram.tgmedia.web` and can be installed
alongside official Telegram. It has its own account sessions and local data.

## Install

1. Download the ARM64 APK and checksums from
   [Releases](https://github.com/deymon28/telegram-android-hyperos-media-control-fix/releases).
2. Install and open **TG Media**.
3. Obtain your own API ID and API hash at [my.telegram.org/apps](https://my.telegram.org/apps),
   following [Telegram's instructions](https://core.telegram.org/api/obtaining_api_id).
4. Enter these values on the setup screen, then sign in normally with your phone
   number. Keep **Test Backend** disabled.

The public APK contains no maintainer API credentials. API credentials are
separate from your phone number, login code, and two-step verification password.
Never post any of them in an issue. ARM64 Android 6.0 or newer is required.

Later releases install over an existing TG Media installation and keep its login,
because every release uses the same package and signing certificate.

## Build the latest official source

```powershell
python scripts/autobuild.py --keystore ../private-signing/tg-media.jks
```

Configure the JDK, Android SDK, and signing password first as described in
[BUILDING.md](BUILDING.md). The script downloads current official source and exact
submodules, applies the patches, runs checks, and produces a signed APK with a
matching source archive. Conflicts and failed privacy checks stop the build.
Use `--locked` for the reviewed source version. Publication remains a manual step.

## Changes and limitations

- Use a modern audio media session on Xiaomi devices without competing legacy
  registrations, and keep paused playback available to external controls.
- Keep headset play working after a pause when another app, such as official
  Telegram after an in-chat sound, holds an idle sound player. Android routes media
  buttons to any app with an active player, so a paused TG Media track keeps a
  silent marker until another app starts media playback.
- Release in-chat sound players after five idle seconds, so TG Media itself does
  not take headset buttons from other media apps.
- Give eligible full-screen videos a media session and preserve ownership during
  picture-in-picture transitions.
- Show initial login API errors instead of silently ignoring them.
- Require API setup before supported external activities, widgets, and playback
  entry points initialize the client; preserve the original request after setup.
- Store API configuration in private app preferences with backups disabled.
- Package English UI resources. Official passkeys, billing, automatic updates,
  Firebase initialization, Google authentication, and the sample map key are
  disabled or omitted. Push and map behavior can differ from official Telegram.

Android chooses the active media session system-wide; an app that starts
playback takes accessory controls, as intended. See [VALIDATION.md](VALIDATION.md)
for the exact evidence and remaining device checks, [PRIVACY.md](PRIVACY.md) for
configuration handling, and [CHANGELOG.md](CHANGELOG.md) for changes.

## Source and license

The repository keeps only the inputs and checks needed to use, build, and verify
TG Media:

| Files | Purpose |
| --- | --- |
| `patches/`, `overlay/` | Changes applied to official Telegram source. |
| `scripts/` | Source preparation, APK building, and release privacy checks. |
| `tests/` | Regression and security checks run by the autobuilder. |
| `sources.lock.json` | Exact upstream revisions and download hashes. |
| `SIGNING-CERTIFICATE.txt` | Public fingerprint for verifying release identity. |
| Build, privacy, validation, and changelog documents | Usage instructions, limitations, and verification results. |
| `LICENSE`, `.gitignore`, `.gitattributes` | Licensing and repository hygiene. |

This repository contains the patches, additional source files, build scripts, and
source hashes. Every released APK must be accompanied by its complete
corresponding-source archive. Telegram-derived code is GPL version 2 or later;
see [LICENSE](LICENSE). Third-party components retain their licenses and notices.
TG Media is not affiliated with or endorsed by Telegram or Xiaomi.
