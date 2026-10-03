package org.telegram.ui;

import android.app.Activity;
import android.content.ComponentName;
import android.content.Intent;

/** Keeps a first-launch request inside the existing exported activity boundary. */
public final class ApiSetupRedirect {
    private static final String NEXT_INTENT = "org.telegram.tgmedia.API_SETUP_NEXT";
    private static final int URI_GRANTS = Intent.FLAG_GRANT_READ_URI_PERMISSION
            | Intent.FLAG_GRANT_WRITE_URI_PERMISSION
            | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION
            | Intent.FLAG_GRANT_PREFIX_URI_PERMISSION;

    private ApiSetupRedirect() {}

    private static boolean isAllowedTarget(String name) {
        return LaunchActivity.class.getName().equals(name)
                || ExternalActionActivity.class.getName().equals(name)
                || ShareActivity.class.getName().equals(name)
                || ChatsWidgetConfigActivity.class.getName().equals(name)
                || ContactsWidgetConfigActivity.class.getName().equals(name);
    }

    private static Intent sanitize(Activity activity, Intent requested) {
        ComponentName component = requested == null ? null : requested.getComponent();
        if (component == null || !activity.getPackageName().equals(component.getPackageName())
                || !isAllowedTarget(component.getClassName())) {
            return new Intent(activity, LaunchActivity.class);
        }
        Intent next = new Intent(requested);
        next.setSelector(null);
        next.setPackage(activity.getPackageName());
        next.setFlags(next.getFlags() & URI_GRANTS);
        next.removeExtra(NEXT_INTENT);
        return next;
    }

    public static void open(Activity activity) {
        Intent requested = activity.getIntent() == null ? new Intent() : new Intent(activity.getIntent());
        requested.setComponent(new ComponentName(activity, activity.getClass()));
        Intent next = sanitize(activity, requested);
        Intent setup = new Intent(activity, ApiSetupActivity.class);
        setup.putExtra(NEXT_INTENT, next);
        setup.setClipData(next.getClipData());
        setup.setDataAndType(next.getData(), next.getType());
        setup.addFlags(next.getFlags() | Intent.FLAG_ACTIVITY_FORWARD_RESULT);
        activity.startActivity(setup);
        activity.finish();
    }

    public static Intent resume(Activity activity) {
        try {
            Intent requested = activity.getIntent() == null ? null : activity.getIntent().getParcelableExtra(NEXT_INTENT);
            return sanitize(activity, requested).addFlags(Intent.FLAG_ACTIVITY_FORWARD_RESULT);
        } catch (RuntimeException invalidRequest) {
            // Malformed external extras must not interrupt credential setup or get logged.
            return new Intent(activity, LaunchActivity.class).addFlags(Intent.FLAG_ACTIVITY_FORWARD_RESULT);
        }
    }
}
