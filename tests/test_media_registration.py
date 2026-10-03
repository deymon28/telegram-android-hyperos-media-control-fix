"""Execute verbatim Java registration predicates on a host JVM with controlled inputs.

These policy checks cover overlapping playback backends, video/PiP permissions,
and PiP ownership transitions. They do not emulate Android media routing,
Bluetooth, playback callbacks, or prove HyperOS device behavior.
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
SERVICE = pathlib.Path(os.environ.get('MUSIC_PLAYER_SERVICE_SOURCE', str(SOURCE_ROOT / 'TMessagesProj/src/main/java/org/telegram/messenger/MusicPlayerService.java')))
VIEWER = pathlib.Path(os.environ.get('PHOTO_VIEWER_SOURCE', str(SOURCE_ROOT / 'TMessagesProj/src/main/java/org/telegram/ui/PhotoViewer.java')))


class MediaRegistrationTest(unittest.TestCase):
    def test_pip_does_not_create_a_competing_session_for_a_source_that_owns_its_session(self):
        controller = SOURCE_ROOT / 'TMessagesProj/src/main/java/org/telegram/messenger/pip/PipActivityController.java'
        source = controller.read_text(encoding='utf-8')
        predicate = re.search(r'if \(([^\n]+)\) \{\s*mediaSession = new MediaSession(?:Compat|\.Builder)', source)
        self.assertIsNotNone(predicate, 'PiP media-session creation predicate was not found.')
        program = '''
class PipRegistrationProbe {
    static class Source { boolean needMediaSession; Source(boolean needs) { needMediaSession = needs; } }
    public static void main(String[] args) {
        int[][] cases = {{1, 1, 0, 0}, {0, 1, 1, 1}, {1, 0, 0, 0}, {1, 1, 1, 0}, {0, 1, 0, 0}};
        for (int[] item : cases) {
            boolean oldMediaSession = item[0] == 1;
            Source newSource = item[1] == 1 ? new Source(item[2] == 1) : null;
            boolean newMediaSession = newSource != null && newSource.needMediaSession;
            int created = 0;
            if (oldMediaSession != newMediaSession) {
                if (EXPRESSION) { created++; }
            }
            if (created != item[3]) {
                throw new AssertionError("PiP created an unwanted competing media session");
            }
        }
        System.out.println("PASS: 5 PiP session ownership transitions");
    }
}
'''.replace('EXPRESSION', predicate.group(1))
        with tempfile.TemporaryDirectory(prefix='pip-registration-') as folder:
            probe = pathlib.Path(folder) / 'PipRegistrationProbe.java'
            probe.write_text(program, encoding='utf-8')
            result = subprocess.run([str(JAVA / ('java.exe' if os.name == 'nt' else 'java')), str(probe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())

    def test_regular_video_controls_do_not_require_pip_permission(self):
        source = VIEWER.read_text(encoding='utf-8')
        predicate = re.search(r'final boolean useVideoMediaSession\s*=\s*(.*?);', source, re.S)
        if predicate:
            expression = predicate.group(1)
        else:
            # Upstream creates its video session only through this PiP branch.
            predicate = re.search(r'if \((PipUtils\.checkPermissions\(parentActivity\) == PipPermissions\.PIP_GRANTED_PIP)\)', source)
            self.assertIsNotNone(predicate, 'Video session registration policy was not found.')
            expression = predicate.group(1)
        program = '''
class VideoRegistrationProbe {
    static class Message {
        boolean video = true, secret;
        boolean isVideo() { return video; }
        boolean isSecretMedia() { return secret; }
    }
    static class PipPermissions { static final int PIP_GRANTED_PIP = 2; }
    static class PipUtils {
        static int permission;
        static int checkPermissions(Object context) { return permission; }
    }
    public static void main(String[] args) {
        boolean preview = false, livePhoto = false;
        java.util.List<Object> imagesArrLocals = new java.util.ArrayList<>();
        Message currentMessageObject = new Message();
        Object parentActivity = new Object();
        for (int permission : new int[] {1, -1, 2}) {
            PipUtils.permission = permission;
            boolean exposesSession = EXPRESSION;
            if (!exposesSession) {
                throw new AssertionError("Regular video has no media session with PiP permission " + permission);
            }
        }
        for (int exclusion = 0; exclusion < 6; exclusion++) {
            preview = exclusion == 0;
            livePhoto = exclusion == 1;
            imagesArrLocals.clear();
            if (exclusion == 2) imagesArrLocals.add(new Object());
            currentMessageObject = exclusion == 3 ? null : new Message();
            if (currentMessageObject != null) {
                currentMessageObject.video = exclusion != 4;
                currentMessageObject.secret = exclusion == 5;
            }
            boolean exposesSession = EXPRESSION;
            if (exposesSession) throw new AssertionError("Ineligible media exposed a session: " + exclusion);
        }
        System.out.println("PASS: normal video works with all PiP permissions; six private/non-video exclusions hold");
    }
}
'''.replace('EXPRESSION', expression)
        with tempfile.TemporaryDirectory(prefix='video-registration-') as folder:
            probe = pathlib.Path(folder) / 'VideoRegistrationProbe.java'
            probe.write_text(program, encoding='utf-8')
            result = subprocess.run([str(JAVA / ('java.exe' if os.name == 'nt' else 'java')), str(probe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())

    def test_only_one_playback_backend_across_android_and_xiaomi_versions(self):
        source = SERVICE.read_text(encoding='utf-8')
        expression = re.search(r'boolean supportLockScreenControls\s*=\s*(.*?);', source).group(1)
        program = '''
class RegistrationProbe {
    static class Build {
        static class VERSION { static int SDK_INT; }
        static class VERSION_CODES { static final int LOLLIPOP = 21; }
    }
    static class TextUtils {
        static boolean isEmpty(String value) { return value == null || value.isEmpty(); }
    }
    static class AndroidUtilities {
        static String property;
        static String getSystemProperty(String name) { return property; }
    }
    public static void main(String[] args) {
        int[][] cases = {{19, 0}, {19, 1}, {21, 0}, {21, 1}, {33, 1}, {34, 1}, {35, 1}, {36, 1}, {36, 0}};
        for (int[] item : cases) {
            Build.VERSION.SDK_INT = item[0];
            AndroidUtilities.property = item[1] == 0 ? "" : "14";
            boolean legacy = EXPRESSION;
            int backends = (legacy ? 1 : 0) + (item[0] >= 21 ? 1 : 0);
            if (backends != 1) {
                throw new AssertionError("SDK " + item[0] + ", Xiaomi=" + item[1]
                    + ": expected one playback backend, found " + backends);
            }
        }
        System.out.println("PASS: 9 Android/Xiaomi backend registration scenarios");
    }
}
'''.replace('EXPRESSION', expression)
        with tempfile.TemporaryDirectory(prefix='media-registration-') as folder:
            probe = pathlib.Path(folder) / 'RegistrationProbe.java'
            probe.write_text(program, encoding='utf-8')
            result = subprocess.run([str(JAVA / ('java.exe' if os.name == 'nt' else 'java')), str(probe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())


if __name__ == '__main__':
    unittest.main()
