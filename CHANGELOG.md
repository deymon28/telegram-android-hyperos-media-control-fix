# Changelog

## 13.0.2-hyperos.1

- Rebase onto official Telegram 13.0.2. The minimum Android version follows upstream to 6.0.
- Keep headset play working after a pause while another app holds an idle sound player, and
  yield accessory controls as soon as another app starts media playback.
- Release idle in-chat sound players so TG Media does not take other apps' headset buttons.
- Pin regenerated googlesource archives by content; byte-level pins failed on every fresh download.
- Add `--skip-built` for unattended builds and choose the next free release revision by default.

## 12.10.6-hyperos.2

- Rebase the media fix onto official Telegram 12.10.6 and its Media3 player/session API.
- Preserve video session ownership and pause/resume controls without exposing secret, preview, or local-editor media.
- Guard external activities, widgets, and playback entry points before API setup; resume the original request afterward.
- Add a current-source autobuilder with exact submodule resolution, verified Gradle bootstrap, reusable source locks, bounded builds, and complete corresponding-source output.
- Reject changed or unexpected build inputs, unsafe archive paths, embedded API credentials, private publication files, and personal signing subjects.
- Keep public APK checks active under optimized Python and verify compiled configuration, ABI, manifest, signature, and alignment.
- Preserve English-only packaged localization with upstream's new resource generator.
