"""Execute the real setup redirect and extracted Android entry guards on a JVM.

The adapters model Intent payloads and lifecycle calls, not Android task or URI
permission management. Device validation is still required for those contracts.
"""

import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = pathlib.Path(os.environ.get("TG_SOURCE_DIR", str(ROOT / "Telegram"))) / "TMessagesProj/src/main/java/org/telegram"
OVERLAY = ROOT / "overlay/TMessagesProj/src/main/java/org/telegram/ui"


def before(name, signature, boundary):
    source = (SOURCE / name).read_text(encoding="utf-8")
    start = source.index("{", source.index(signature)) + 1
    return source[start:source.index(boundary, start)]


def java_tool(name):
    filename = name + (".exe" if os.name == "nt" else "")
    candidate = pathlib.Path(os.environ.get("JAVA_HOME", "")) / "bin" / filename
    return str(candidate) if candidate.is_file() else shutil.which(name)


class StartupGatesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.java = java_tool("java")
        javac = java_tool("javac")
        if not cls.java or not javac:
            raise RuntimeError("A JDK is required for the startup gate checks.")
        cls.folder = tempfile.TemporaryDirectory(prefix="tg-startup-gates-")
        cls.addClassCleanup(cls.folder.cleanup)
        folder = pathlib.Path(cls.folder.name)
        sources = {
            "android/os/Bundle.java": "package android.os; public class Bundle {}",
            "android/content/ComponentName.java": """
package android.content;
public class ComponentName {
    private final String pkg, name;
    public ComponentName(android.app.Activity activity, Class<?> type) { this(activity.getPackageName(), type.getName()); }
    public ComponentName(String pkg, String name) { this.pkg = pkg; this.name = name; }
    public String getPackageName() { return pkg; }
    public String getClassName() { return name; }
}
""",
            "android/content/Intent.java": """
package android.content;
import java.util.HashMap;
public class Intent {
    public static final int FLAG_GRANT_READ_URI_PERMISSION = 1, FLAG_GRANT_WRITE_URI_PERMISSION = 2;
    public static final int FLAG_GRANT_PERSISTABLE_URI_PERMISSION = 64, FLAG_GRANT_PREFIX_URI_PERMISSION = 128;
    public static final int FLAG_ACTIVITY_FORWARD_RESULT = 0x02000000, FLAG_ACTIVITY_NEW_TASK = 0x10000000;
    private ComponentName component;
    private int flags;
    private String action, data, type, pkg;
    private Object clipData;
    private Intent selector;
    private HashMap<String, Object> extras = new HashMap<>();
    public Intent() {}
    public Intent(android.app.Activity activity, Class<?> target) { component = new ComponentName(activity, target); }
    public Intent(Intent original) {
        component = original.component; flags = original.flags; action = original.action;
        data = original.data; type = original.type; pkg = original.pkg;
        clipData = original.clipData; selector = original.selector; extras.putAll(original.extras);
    }
    public ComponentName getComponent() { return component; }
    public Intent setComponent(ComponentName c) { component = c; return this; }
    public int getFlags() { return flags; }
    public Intent setFlags(int f) { flags = f; return this; }
    public Intent addFlags(int f) { flags |= f; return this; }
    public Intent setAction(String a) { action = a; return this; }
    public String getAction() { return action; }
    public Intent setDataAndType(String d, String t) { data = d; type = t; return this; }
    public String getData() { return data; }
    public String getType() { return type; }
    public Intent setPackage(String p) { pkg = p; return this; }
    public String getPackage() { return pkg; }
    public void setSelector(Intent s) { selector = s; }
    public Intent getSelector() { return selector; }
    public void setClipData(Object c) { clipData = c; }
    public Object getClipData() { return clipData; }
    public Intent putExtra(String key, Object value) { extras.put(key, value); return this; }
    public Object getExtra(String key) { return extras.get(key); }
    @SuppressWarnings("unchecked") public <T> T getParcelableExtra(String key) { return (T) extras.get(key); }
    public void removeExtra(String key) { extras.remove(key); }
}
""",
            "android/app/Activity.java": """
package android.app;
import android.content.Intent;
import android.os.Bundle;
public class Activity {
    public Intent intent, started;
    public boolean finished;
    public int superDestroyed;
    public String getPackageName() { return "org.telegram.tgmedia.web"; }
    public Intent getIntent() { return intent; }
    public void startActivity(Intent next) { started = next; }
    public void finish() { finished = true; }
    protected void onCreate(Bundle state) {}
    protected void onPause() {}
    protected void onResume() {}
    protected void onDestroy() { superDestroyed++; }
}
""",
            "org/telegram/messenger/ApiCredentials.java": """
package org.telegram.messenger;
public class ApiCredentials {
    public static boolean configured;
    public static boolean isConfigured(Object context) { return configured; }
}
""",
            "org/telegram/ui/ApiSetupActivity.java": "package org.telegram.ui; public class ApiSetupActivity extends android.app.Activity {}",
            "org/telegram/ui/ApiSetupRedirect.java": (OVERLAY / "ApiSetupRedirect.java").read_text(encoding="utf-8"),
        }
        for name, boundary in (
            ("LaunchActivity", "        isActive = true;"),
            ("ExternalActionActivity", "        ApplicationLoader.postInitApplication();"),
            ("ShareActivity", "        ApplicationLoader.postInitApplication();"),
        ):
            prefix = before(f"ui/{name}.java", "protected void onCreate(Bundle savedInstanceState)", boundary)
            body = f"""package org.telegram.ui;
import android.os.Bundle;
import android.content.Intent;
public class {name} extends android.app.Activity {{
    private boolean awaitingApiSetup;
    public int initialized, paused, resumed, destroyed;
    public void create() {{ onCreate(null); }}
    protected void onCreate(Bundle savedInstanceState) {{ {prefix} initialized++; }}
"""
            if name == "ExternalActionActivity":
                for method, boundary, counter in (
                    ("onPause", "        actionBarLayout.onPause();", "paused"),
                    ("onResume", "        actionBarLayout.onResume();", "resumed"),
                    ("onDestroy", "        onFinish();", "destroyed"),
                ):
                    prefix = before(f"ui/{name}.java", f"protected void {method}()", boundary)
                    body += f"public void {method}() {{ {prefix} {counter}++; }}\n"
            sources[f"org/telegram/ui/{name}.java"] = body + "}\n"
        for name in ("ChatsWidgetConfigActivity", "ContactsWidgetConfigActivity"):
            sources[f"org/telegram/ui/{name}.java"] = f"package org.telegram.ui; public class {name} extends ExternalActionActivity {{}}"

        player_create = before("messenger/MusicPlayerService.java", "public void onCreate()", "        audioManager")
        player_start = before("messenger/MusicPlayerService.java", "public int onStartCommand(", "        try {")
        player_destroy = before("messenger/MusicPlayerService.java", "public void onDestroy()", "        unregisterReceiver")
        browser_create = before("messenger/MusicBrowserService.java", "public void onCreate()", "        ApplicationLoader.postInitApplication();")
        browser_root = before("messenger/MusicBrowserService.java", "public BrowserRoot onGetRoot(", "        if (clientPackageName == null)")
        browser_children = before("messenger/MusicBrowserService.java", "public void onLoadChildren(", "        TelegramMediaSession holder")
        receiver = before("messenger/MusicPlayerReceiver.java", "public void onReceive(", "        if (intent.getAction().equals")
        sources["org/telegram/messenger/ServiceGatesProbe.java"] = f"""
package org.telegram.messenger;
import android.content.Intent;
import java.util.List;
public class ServiceGatesProbe {{
    static class Base {{ void onCreate() {{}} void onDestroy() {{}} }}
    static class Player extends Base {{
        boolean awaitingApiSetup, stopped; int initialized, started, destroyed;
        static final int START_NOT_STICKY = 2;
        void stopSelf() {{ stopped = true; }}
        public void onCreate() {{ {player_create} initialized++; }}
        public int onStartCommand(Intent intent, int flags, int startId) {{ {player_start} started++; return 1; }}
        public void onDestroy() {{ {player_destroy} destroyed++; }}
    }}
    static class Result {{ List<?> value; void sendResult(List<?> list) {{ value = list; }} }}
    static class Browser extends Base {{
        int initialized, roots, children;
        public void onCreate() {{ {browser_create} initialized++; }}
        public Object onGetRoot() {{ {browser_root} roots++; return new Object(); }}
        public void onLoadChildren(Result result) {{ {browser_children} children++; }}
    }}
    static class Receiver {{
        int deliveries;
        void onReceive(Object context, Intent intent) {{ {receiver} deliveries++; }}
    }}
    public static void check() {{
        ApiCredentials.configured = false;
        Player player = new Player(); player.onCreate();
        if (player.onStartCommand(null, 0, 0) != Player.START_NOT_STICKY || !player.stopped) throw new AssertionError("Unconfigured service must stop without restart");
        player.onDestroy();
        if (player.initialized + player.started + player.destroyed != 0) throw new AssertionError("Unconfigured player touched client state");
        Browser browser = new Browser(); browser.onCreate(); Result result = new Result();
        if (browser.onGetRoot() != null) throw new AssertionError("Unconfigured browser exposed a session");
        browser.onLoadChildren(result);
        if (result.value == null || !result.value.isEmpty() || browser.initialized + browser.roots + browser.children != 0) throw new AssertionError("Unconfigured browse request did not complete safely");
        Receiver receiver = new Receiver(); receiver.onReceive(null, new Intent().setAction("play"));
        if (receiver.deliveries != 0) throw new AssertionError("Unconfigured receiver touched media state");
        ApiCredentials.configured = true;
        receiver.onReceive(null, null); receiver.onReceive(null, new Intent());
        if (receiver.deliveries != 0) throw new AssertionError("Malformed receiver intent reached media state");
        receiver.onReceive(null, new Intent().setAction("play"));
        player = new Player(); player.onCreate(); player.onStartCommand(null, 0, 0); player.onDestroy();
        browser = new Browser(); browser.onCreate(); browser.onGetRoot(); browser.onLoadChildren(new Result());
        if (receiver.deliveries != 1 || player.initialized != 1 || player.started != 1 || player.destroyed != 1 || browser.initialized != 1 || browser.roots != 1 || browser.children != 1) throw new AssertionError("Configured entry behavior changed");
    }}
}}
"""
        sources["org/telegram/ui/StartupGateProbe.java"] = r"""
package org.telegram.ui;
import android.app.Activity;
import android.content.ComponentName;
import android.content.Intent;
import org.telegram.messenger.ApiCredentials;
import org.telegram.messenger.ServiceGatesProbe;
public class StartupGateProbe {
    static final String NEXT = "org.telegram.tgmedia.API_SETUP_NEXT";
    static void check(boolean ok, String reason) { if (!ok) throw new AssertionError(reason); }
    static void redirects() {
        Activity[] entries = {new LaunchActivity(), new ExternalActionActivity(), new ShareActivity(), new ChatsWidgetConfigActivity(), new ContactsWidgetConfigActivity()};
        for (Activity entry : entries) {
            Object clips = new Object();
            entry.intent = new Intent().setAction("fixture.ACTION").setDataAndType("content://fixture/media/7", "video/mp4").putExtra("fixture", 7);
            entry.intent.setClipData(clips);
            entry.intent.setSelector(new Intent().setComponent(new ComponentName("outside", "Untrusted")));
            entry.intent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_GRANT_READ_URI_PERMISSION);
            ApiSetupRedirect.open(entry);
            check(entry.finished, "Original activity did not finish");
            check(entry.started.getComponent().getClassName().equals(ApiSetupActivity.class.getName()), "Missing setup redirect");
            check(entry.started.getClipData() == clips && (entry.started.getFlags() & Intent.FLAG_GRANT_READ_URI_PERMISSION) != 0, "Setup lost share grant payload");
            check((entry.started.getFlags() & Intent.FLAG_ACTIVITY_FORWARD_RESULT) != 0, "Setup lost caller result chain");
            ApiSetupActivity setup = new ApiSetupActivity(); setup.intent = entry.started;
            Intent next = ApiSetupRedirect.resume(setup);
            check(next.getComponent().getClassName().equals(entry.getClass().getName()), "Wrong resumed activity");
            check("fixture.ACTION".equals(next.getAction()) && "content://fixture/media/7".equals(next.getData()) && "video/mp4".equals(next.getType()), "Intent action/data/type lost");
            check(Integer.valueOf(7).equals(next.getExtra("fixture")) && next.getClipData() == clips, "Extras/ClipData lost");
            check(next.getSelector() == null && next.getExtra(NEXT) == null && (next.getFlags() & Intent.FLAG_ACTIVITY_NEW_TASK) == 0, "Unsafe navigation flags or selector survived");
            check((next.getFlags() & (Intent.FLAG_ACTIVITY_FORWARD_RESULT | Intent.FLAG_GRANT_READ_URI_PERMISSION)) == (Intent.FLAG_ACTIVITY_FORWARD_RESULT | Intent.FLAG_GRANT_READ_URI_PERMISSION), "Resumed request lost grant/result flags");
        }
        ApiSetupActivity setup = new ApiSetupActivity();
        check(ApiSetupRedirect.resume(setup).getComponent().getClassName().equals(LaunchActivity.class.getName()), "Default launch failed");
        for (ComponentName component : new ComponentName[]{new ComponentName("outside", LaunchActivity.class.getName()), new ComponentName(setup.getPackageName(), "org.telegram.ui.PrivateActivity"), new ComponentName(setup.getPackageName(), ApiSetupActivity.class.getName())}) {
            setup.intent = new Intent().putExtra(NEXT, new Intent().setComponent(component));
            check(ApiSetupRedirect.resume(setup).getComponent().getClassName().equals(LaunchActivity.class.getName()), "Untrusted redirect target accepted");
        }
        setup.intent = new Intent().putExtra(NEXT, "malformed");
        check(ApiSetupRedirect.resume(setup).getComponent().getClassName().equals(LaunchActivity.class.getName()), "Malformed redirect did not fall back");
    }
    static void gates() {
        ApiCredentials.configured = false;
        LaunchActivity launch = new LaunchActivity(); launch.create();
        check(launch.initialized == 0 && launch.started != null && launch.finished, "Launch gate bypassed");
        ShareActivity share = new ShareActivity(); share.create();
        check(share.initialized == 0 && share.started != null && share.finished, "Share gate bypassed");
        for (ExternalActionActivity activity : new ExternalActionActivity[]{new ExternalActionActivity(), new ChatsWidgetConfigActivity(), new ContactsWidgetConfigActivity()}) {
            activity.create(); activity.onResume(); activity.onPause(); activity.onDestroy();
            check(activity.initialized + activity.paused + activity.resumed + activity.destroyed == 0 && activity.superDestroyed == 1, "Unconfigured exported activity entered client lifecycle");
            check(activity.started != null && activity.finished, "Exported activity did not reach setup");
        }
        ApiCredentials.configured = true;
        launch = new LaunchActivity(); launch.create(); share = new ShareActivity(); share.create();
        check(launch.initialized == 1 && launch.started == null && share.initialized == 1 && share.started == null, "Configured Launch/Share behavior changed");
        ExternalActionActivity external = new ExternalActionActivity(); external.create(); external.onResume(); external.onPause(); external.onDestroy();
        check(external.initialized == 1 && external.paused == 1 && external.resumed == 1 && external.destroyed == 1 && external.started == null, "Configured external lifecycle changed");
        ServiceGatesProbe.check();
    }
    public static void main(String[] args) {
        if ("redirects".equals(args[0])) redirects(); else gates();
        System.out.println("PASS: startup " + args[0]);
    }
}
"""
        files = []
        for name, text in sources.items():
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            files.append(str(path))
        result = subprocess.run([javac, "-d", str(folder), *files], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        cls.classpath = str(folder)

    def run_probe(self, mode):
        result = subprocess.run([self.java, "-cp", self.classpath, "org.telegram.ui.StartupGateProbe", mode], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_first_launch_preserves_requests_and_rejects_untrusted_targets(self):
        self.run_probe("redirects")

    def test_unconfigured_entry_points_stop_before_client_initialization(self):
        self.run_probe("gates")


if __name__ == "__main__":
    unittest.main()
