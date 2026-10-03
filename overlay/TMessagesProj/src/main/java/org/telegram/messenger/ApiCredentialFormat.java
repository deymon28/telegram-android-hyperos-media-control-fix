package org.telegram.messenger;

import java.util.Locale;

public final class ApiCredentialFormat {
    private ApiCredentialFormat() {}

    public static int parseId(String value) {
        if (value == null || !value.trim().matches("[1-9][0-9]{0,9}")) return 0;
        try {
            int id = Integer.parseInt(value.trim());
            return id == 4 ? 0 : id;
        } catch (NumberFormatException ignored) {
            return 0;
        }
    }

    public static String normalizeHash(String value) {
        if (value == null || !value.trim().matches("[a-fA-F0-9]{32}")) return "";
        return value.trim().toLowerCase(Locale.ROOT);
    }
}
