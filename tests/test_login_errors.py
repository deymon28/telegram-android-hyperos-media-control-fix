"""Execute the real auth-code error branch with lightweight host JVM adapters."""

import os
import shutil
import pathlib
import re
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE_ROOT = pathlib.Path(os.environ.get('TG_SOURCE_DIR', str(ROOT / 'Telegram')))
JAVA = pathlib.Path(shutil.which('java') or pathlib.Path(os.environ['JAVA_HOME']) / 'bin/java')


class LoginErrorTest(unittest.TestCase):
    def test_send_code_errors_are_visible(self):
        source = (SOURCE_ROOT / 'TMessagesProj/src/main/java/org/telegram/ui/Components/AlertsCreator.java').read_text(encoding='utf-8')
        branch = re.search(r'} else if \(([^\n]*request instanceof TLRPC.TL_auth_resendCode[^\n]*)\) \{(.*?)\n        } else if', source, re.S)
        self.assertIsNotNone(branch)
        program = '''
class LoginErrorProbe {
    static class TLRPC { static class TL_auth_sendCode {} static class TL_auth_resendCode {} }
    static class Error { String text; int code; Error(String t, int c) { text=t; code=c; } }
    static class R { static class string {
        static String InvalidPhoneNumber="invalid", InvalidCode="code", CodeExpired="expired", FloodWait="wait", ErrorOccurred="error";
    } }
    static class LocaleController { static String getString(String value) { return value; } }
    static String showSimpleAlert(Object fragment, String text) { return text; }
    static String process(Object request, Error error) {
        Object fragment = new Object();
        if (CONDITION) { BODY }
        return null;
    }
    public static void main(String[] args) {
        for (Object request : new Object[]{new TLRPC.TL_auth_sendCode(), new TLRPC.TL_auth_resendCode()}) {
            for (String message : new String[]{"API_ID_PUBLISHED_FLOOD", "API_ID_INVALID", "UNEXPECTED_AUTH_ERROR"}) {
                String shown = process(request, new Error(message, 400));
                if (shown == null || !shown.contains(message)) throw new AssertionError("Hidden auth error: " + message);
            }
            if (process(request, new Error("CANCELLED", -1000)) != null) throw new AssertionError("Cancellation must stay silent");
            if (!"invalid".equals(process(request, new Error("PHONE_NUMBER_INVALID", 400)))) throw new AssertionError("Known error mapping changed");
        }
        System.out.println("PASS: 10 auth-code error scenarios");
    }
}
'''.replace('CONDITION', branch.group(1)).replace('BODY', branch.group(2))
        with tempfile.TemporaryDirectory(prefix='tg-login-errors-') as folder:
            probe = pathlib.Path(folder) / 'LoginErrorProbe.java'
            probe.write_text(program, encoding='utf-8')
            result = subprocess.run([str(JAVA), str(probe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())


if __name__ == '__main__':
    unittest.main()
