"""Validate the actual public-build API input parser on the host JVM."""
import os
import shutil
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE_ROOT = pathlib.Path(os.environ.get('TG_SOURCE_DIR', str(ROOT / 'Telegram')))
JAVA_HOME = pathlib.Path(os.environ['JAVA_HOME'])


class ApiCredentialFormatTest(unittest.TestCase):
    def test_public_setup_rejects_invalid_and_sample_credentials(self):
        source = (SOURCE_ROOT / 'TMessagesProj/src/main/java/org/telegram/messenger/ApiCredentialFormat.java').read_text(encoding='utf-8')
        probe = '''
import org.telegram.messenger.ApiCredentialFormat;
class ApiCredentialFormatProbe {
    public static void main(String[] args) {
        for (String id : new String[]{null, "", "0", "4", "-1", "01", "1e8", "2147483648", "123;drop"}) {
            if (ApiCredentialFormat.parseId(id) != 0) throw new AssertionError("Invalid ID accepted");
        }
        if (ApiCredentialFormat.parseId(" 12345678 ") != 12345678) throw new AssertionError("Valid ID rejected");
        if (ApiCredentialFormat.parseId("2147483647") != Integer.MAX_VALUE) throw new AssertionError("Valid maximum ID rejected");
        for (String hash : new String[]{null, "", "abc", "0123456789abcdef0123456789abcdeg", "0123456789abcdef0123456789abcdef00"}) {
            if (!ApiCredentialFormat.normalizeHash(hash).isEmpty()) throw new AssertionError("Invalid hash accepted");
        }
        if (!"0123456789abcdef0123456789abcdef".equals(ApiCredentialFormat.normalizeHash(" 0123456789ABCDEF0123456789ABCDEF "))) throw new AssertionError("Hash normalization failed");
        System.out.println("PASS: 17 public API input scenarios");
    }
}
'''
        with tempfile.TemporaryDirectory(prefix='tg-api-format-') as folder:
            root = pathlib.Path(folder)
            (root / 'ApiCredentialFormat.java').write_text(source, encoding='utf-8')
            (root / 'ApiCredentialFormatProbe.java').write_text(probe, encoding='utf-8')
            compile_result = subprocess.run([str(JAVA_HOME / ('bin/javac.exe' if os.name == 'nt' else 'bin/javac')), '-d', folder, str(root / 'ApiCredentialFormat.java'), str(root / 'ApiCredentialFormatProbe.java')], capture_output=True, text=True)
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            result = subprocess.run([str(JAVA_HOME / ('bin/java.exe' if os.name == 'nt' else 'bin/java')), '-cp', folder, 'ApiCredentialFormatProbe'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())


if __name__ == '__main__':
    unittest.main()
