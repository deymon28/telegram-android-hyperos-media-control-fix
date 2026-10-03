"""Check the real login request settings with the fork's BuildVars value.

This host JVM probe verifies the request capability contract. It does not send
login requests or establish that a user's account can authenticate.
"""
import os
import shutil
import pathlib
import re
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE_ROOT = pathlib.Path(os.environ.get('TG_SOURCE_DIR', str(ROOT / 'Telegram')))
SOURCE = SOURCE_ROOT / 'TMessagesProj/src/main/java/org/telegram'
JAVA = pathlib.Path(shutil.which('java') or pathlib.Path(os.environ['JAVA_HOME']) / 'bin/java')


class LoginConfigurationTest(unittest.TestCase):
    def test_fork_never_advertises_official_firebase_sms_authentication(self):
        build_vars = (SOURCE / 'messenger/BuildVars.java').read_text(encoding='utf-8')
        login = (SOURCE / 'ui/LoginActivity.java').read_text(encoding='utf-8')
        key = re.search(r'public static String SAFETYNET_KEY = ("[^"]*");', build_vars)
        self.assertIsNotNone(key)
        settings = re.search(
            r'settings.allow_app_hash = settings.allow_firebase = .*?\n\s*if .*?\{\s*settings.allow_firebase = false;\s*}',
            login, re.S)
        self.assertIsNotNone(settings)
        program = '''
class LoginConfigurationProbe {
    static class BuildVars { static String SAFETYNET_KEY = KEY_EXPRESSION; }
    static class TextUtils { static boolean isEmpty(String s) { return s == null || s.isEmpty(); } }
    static class PushListenerController {
        static class GooglePushListenerServiceProvider {
            static final GooglePushListenerServiceProvider INSTANCE = new GooglePushListenerServiceProvider();
            boolean services;
            boolean hasServices() { return services; }
        }
    }
    static class Settings { boolean allow_app_hash, allow_firebase; }
    public static void main(String[] args) {
        for (boolean services : new boolean[]{false, true}) {
            for (boolean forceDisableSafetyNet : new boolean[]{false, true}) {
                PushListenerController.GooglePushListenerServiceProvider.INSTANCE.services = services;
                Settings settings = new Settings();
                SETTINGS
                if (settings.allow_firebase) {
                    throw new AssertionError("Unofficial client advertised Firebase SMS authentication");
                }
            }
        }
        System.out.println("PASS: 4 fork authentication capability scenarios");
    }
}
'''.replace('KEY_EXPRESSION', key.group(1)).replace('SETTINGS', settings.group(0))
        with tempfile.TemporaryDirectory(prefix='tg-login-config-') as folder:
            probe = pathlib.Path(folder) / 'LoginConfigurationProbe.java'
            probe.write_text(program, encoding='utf-8')
            result = subprocess.run([str(JAVA), str(probe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())


if __name__ == '__main__':
    unittest.main()
