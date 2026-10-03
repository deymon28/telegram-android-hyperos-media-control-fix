package org.telegram.messenger;

import android.content.Context;
import android.content.SharedPreferences;

/** Runtime API configuration for public APKs; never contains build-owner credentials. */
public final class ApiCredentials {
    private static final String STORE = "private_api_config";

    private ApiCredentials() {}

    public static int getApiId(Context context) {
        if (!BuildConfig.TG_RUNTIME_API_CONFIG) return BuildConfig.TG_API_ID;
        if (context == null) return 0;
        return ApiCredentialFormat.parseId(context.getSharedPreferences(STORE, Context.MODE_PRIVATE).getString("api_id", ""));
    }

    public static String getApiHash(Context context) {
        if (!BuildConfig.TG_RUNTIME_API_CONFIG) return BuildConfig.TG_API_HASH;
        if (context == null) return "";
        return ApiCredentialFormat.normalizeHash(context.getSharedPreferences(STORE, Context.MODE_PRIVATE).getString("api_hash", ""));
    }

    public static boolean isConfigured(Context context) {
        return !BuildConfig.TG_RUNTIME_API_CONFIG || (getApiId(context) != 0 && !getApiHash(context).isEmpty());
    }

    public static boolean save(Context context, String id, String hash) {
        int parsedId = ApiCredentialFormat.parseId(id);
        String normalizedHash = ApiCredentialFormat.normalizeHash(hash);
        if (parsedId == 0 || normalizedHash.isEmpty()) return false;
        SharedPreferences preferences = context.getSharedPreferences(STORE, Context.MODE_PRIVATE);
        return preferences.edit().putString("api_id", Integer.toString(parsedId)).putString("api_hash", normalizedHash).commit();
    }
}
