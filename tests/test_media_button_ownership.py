"""Compile the media-button ownership marker against minimal Android stubs and drive it.

The stubs model only the public framework calls the class makes. They do not emulate
Android media routing; device validation remains separate.
"""
import os
import pathlib
import re
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE_ROOT = pathlib.Path(os.environ.get('TG_SOURCE_DIR', str(ROOT / 'Telegram')))
JAVA = pathlib.Path(os.environ['JAVA_HOME']) / 'bin'
OWNERSHIP = ROOT / 'overlay/TMessagesProj/src/main/java/org/telegram/messenger/MediaButtonOwnership.java'
NOTIFICATIONS = SOURCE_ROOT / 'TMessagesProj/src/main/java/org/telegram/messenger/NotificationsController.java'
SERVICE = SOURCE_ROOT / 'TMessagesProj/src/main/java/org/telegram/messenger/MusicPlayerService.java'

STUBS = {
    'android/content/Context.java': '''package android.content;
public class Context {
    public static final String AUDIO_SERVICE = "audio";
    public Object audio;
    public Object getSystemService(String name) { return audio; }
}''',
    'android/os/Build.java': '''package android.os;
public class Build {
    public static class VERSION { public static int SDK_INT = 36; }
    public static class VERSION_CODES { public static final int O = 26; }
}''',
    'android/os/Looper.java': '''package android.os;
public class Looper { public static Looper getMainLooper() { return new Looper(); } }''',
    'android/os/Handler.java': '''package android.os;
public class Handler {
    public static java.util.List<Runnable> pending = new java.util.ArrayList<>();
    public Handler(Looper looper) {}
    public boolean postDelayed(Runnable r, long delay) { pending.add(r); return true; }
    public void removeCallbacks(Runnable r) { pending.remove(r); }
    public static void runPending() { java.util.List<Runnable> copy = new java.util.ArrayList<>(pending); pending.clear(); for (Runnable r : copy) r.run(); }
}''',
    'android/media/AudioAttributes.java': '''package android.media;
public class AudioAttributes {
    public static final int USAGE_UNKNOWN = 0, USAGE_MEDIA = 1, USAGE_GAME = 14,
        USAGE_ASSISTANCE_SONIFICATION = 13, USAGE_VOICE_COMMUNICATION = 2, CONTENT_TYPE_SONIFICATION = 4;
    final int usage;
    public AudioAttributes(int usage) { this.usage = usage; }
    public int getUsage() { return usage; }
    public static class Builder {
        int usage;
        public Builder setUsage(int value) { usage = value; return this; }
        public Builder setContentType(int value) { return this; }
        public AudioAttributes build() { return new AudioAttributes(usage); }
    }
}''',
    'android/media/AudioPlaybackConfiguration.java': '''package android.media;
public class AudioPlaybackConfiguration {
    final AudioAttributes attributes;
    public AudioPlaybackConfiguration(int usage) { attributes = new AudioAttributes(usage); }
    public AudioAttributes getAudioAttributes() { return attributes; }
}''',
    'android/media/SoundPool.java': '''package android.media;
public class SoundPool {
    public static int created, started, released;
    public static int lastSound = -1; public static float lastVolume = -1;
    public boolean isReleased;
    public int play(int sound, float left, float right, int priority, int loop, float rate) {
        started++; lastSound = sound; lastVolume = Math.max(left, right); return 0;
    }
    public void release() { if (!isReleased) { isReleased = true; released++; } }
    public static class Builder {
        AudioAttributes attributes;
        public Builder setMaxStreams(int value) { return this; }
        public Builder setAudioAttributes(AudioAttributes value) { attributes = value; return this; }
        public SoundPool build() {
            if (attributes.getUsage() != AudioAttributes.USAGE_ASSISTANCE_SONIFICATION) throw new AssertionError("marker must not be media");
            created++; return new SoundPool();
        }
    }
}''',
    'android/media/AudioManager.java': '''package android.media;
public class AudioManager {
    public abstract static class AudioPlaybackCallback {
        public void onPlaybackConfigChanged(java.util.List<AudioPlaybackConfiguration> configs) {}
    }
    public AudioPlaybackCallback callback;
    public int registrations;
    public java.util.List<AudioPlaybackConfiguration> active = new java.util.ArrayList<>();
    public void registerAudioPlaybackCallback(AudioPlaybackCallback cb, android.os.Handler handler) { callback = cb; registrations++; }
    public void unregisterAudioPlaybackCallback(AudioPlaybackCallback cb) { if (callback == cb) callback = null; }
    public java.util.List<AudioPlaybackConfiguration> getActivePlaybackConfigurations() { return active; }
}''',
    'org/telegram/messenger/FileLog.java': '''package org.telegram.messenger;
public class FileLog { public static void e(Throwable t) { throw new AssertionError(t); } }''',
    'org/telegram/messenger/OwnershipProbe.java': '''package org.telegram.messenger;
import android.content.Context;
import android.media.*;
import android.os.*;
import java.util.*;
public class OwnershipProbe {
    static void check(boolean condition, String message) { if (!condition) throw new AssertionError(message); }
    static List<AudioPlaybackConfiguration> configs(int... usages) {
        List<AudioPlaybackConfiguration> list = new ArrayList<>();
        for (int usage : usages) list.add(new AudioPlaybackConfiguration(usage));
        return list;
    }
    static int live() { return SoundPool.created - SoundPool.released; }
    public static void main(String[] args) {
        AudioManager audio = new AudioManager();
        Context context = new Context(); context.audio = audio;
        MediaButtonOwnership ownership = new MediaButtonOwnership(context);

        ownership.onPlaybackStateChanged(false);
        check(SoundPool.created == 0, "a paused service without playback must not claim buttons");

        ownership.onPlaybackStateChanged(true);
        check(live() == 1 && SoundPool.started == 1, "playback must hold one marker");
        check(SoundPool.lastSound == 0 && SoundPool.lastVolume == 0f, "the marker must stay silent");
        ownership.onPlaybackStateChanged(true);
        check(SoundPool.created == 1, "repeated state updates must not stack markers");

        ownership.onPlaybackStateChanged(false);
        audio.callback.onPlaybackConfigChanged(configs(AudioAttributes.USAGE_ASSISTANCE_SONIFICATION));
        Handler.runPending();
        check(live() == 1, "another app's interface sounds must not release ownership");

        audio.active = configs(AudioAttributes.USAGE_MEDIA);
        audio.callback.onPlaybackConfigChanged(audio.active);
        audio.active = configs();
        Handler.runPending();
        check(live() == 1, "our own track still finishing its pause must not release ownership");

        audio.active = configs(AudioAttributes.USAGE_MEDIA);
        audio.callback.onPlaybackConfigChanged(audio.active);
        Handler.runPending();
        check(live() == 0, "another media player must take button priority");

        ownership.onPlaybackStateChanged(true);
        check(live() == 1, "resuming playback must reclaim ownership");
        audio.callback.onPlaybackConfigChanged(configs(AudioAttributes.USAGE_MEDIA));
        Handler.runPending();
        check(live() == 1, "our own playback must not release ownership");

        ownership.onPlaybackStateChanged(false);
        audio.active = configs(AudioAttributes.USAGE_GAME);
        audio.callback.onPlaybackConfigChanged(audio.active);
        Handler.runPending();
        check(live() == 0, "games also take button priority");

        ownership.onPlaybackStateChanged(true);
        ownership.release();
        check(live() == 0 && audio.callback == null, "dismissing playback must release everything");
        check(audio.registrations == 1, "the playback callback is registered once");

        Build.VERSION.SDK_INT = 25;
        MediaButtonOwnership legacy = new MediaButtonOwnership(context);
        int before = SoundPool.created;
        legacy.onPlaybackStateChanged(true);
        check(SoundPool.created == before, "pre-Android 8 devices cannot observe other players and keep upstream behavior");
        System.out.println("PASS: 11 media-button ownership transitions");
    }
}''',
}


class MediaButtonOwnershipTest(unittest.TestCase):
    def test_marker_lifecycle_with_android_stubs(self):
        with tempfile.TemporaryDirectory(prefix='button-ownership-') as folder:
            base = pathlib.Path(folder)
            sources = []
            for name, text in STUBS.items():
                path = base / 'src' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding='utf-8')
                sources.append(str(path))
            sources.append(str(OWNERSHIP))
            exe = '.exe' if os.name == 'nt' else ''
            compiled = subprocess.run([str(JAVA / ('javac' + exe)), '-d', str(base / 'out'), *sources],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(JAVA / ('java' + exe)), '-cp', str(base / 'out'),
                                     'org.telegram.messenger.OwnershipProbe'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())

    def test_player_service_drives_and_releases_the_marker(self):
        source = SERVICE.read_text(encoding='utf-8')
        self.assertRegex(source, r'mediaSession\.setPlaybackState\(playbackState\.build\(\)\);\s*'
                                 r'if \(mediaButtonOwnership != null\) \{\s*'
                                 r'mediaButtonOwnership\.onPlaybackStateChanged\(!MediaController\.getInstance\(\)\.isMessagePaused\(\)\);')
        destroy = re.search(r'public void onDestroy\(\) \{(.*?)\n    \}', source, re.S).group(1)
        self.assertIn('mediaButtonOwnership.release();', destroy)

    def test_in_chat_sounds_release_their_pool_when_idle(self):
        source = NOTIFICATIONS.read_text(encoding='utf-8')
        plays = re.findall(r'soundPool\.play\(sound(?:In|Out), [^\n]+\n(?:[^\n]*\n){0,5}?\s*scheduleSoundPoolRelease\(\);', source)
        self.assertEqual(len(plays), 2, 'Both in-chat sounds must schedule pool release.')
        release = re.search(r'private final Runnable releaseSoundPool = \(\) -> \{(.*?)\n    \};', source, re.S)
        self.assertIsNotNone(release)
        for statement in ('soundPool.release();', 'soundPool = null;', 'soundInLoaded = false;', 'soundOutLoaded = false;'):
            self.assertIn(statement, release.group(1))
        self.assertRegex(source, r'cancelRunnable\(releaseSoundPool\);\s*notificationsQueue\.postRunnable\(releaseSoundPool, \d+\);')


if __name__ == '__main__':
    unittest.main()
