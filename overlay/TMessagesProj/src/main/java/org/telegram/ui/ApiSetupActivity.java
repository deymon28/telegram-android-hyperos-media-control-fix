package org.telegram.ui;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.view.View;
import android.view.WindowManager;
import android.view.autofill.AutofillManager;
import android.view.inputmethod.EditorInfo;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.telegram.messenger.ApiCredentialFormat;
import org.telegram.messenger.ApiCredentials;
import org.telegram.messenger.ApplicationLoader;
import org.telegram.messenger.BuildVars;

/** Configures public builds before any Telegram account or connection is initialized. */
public final class ApiSetupActivity extends Activity {
    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        if (ApiCredentials.isConfigured(this)) {
            openClient();
            return;
        }
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        if (Build.VERSION.SDK_INT >= 26) {
            getWindow().getDecorView().setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS);
        }
        getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(dp(24), dp(48), dp(24), dp(24));
        content.setBackgroundColor(Color.rgb(245, 245, 250));
        if (Build.VERSION.SDK_INT >= 26) content.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS);
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.addView(content);
        setContentView(scroll);

        TextView title = new TextView(this);
        title.setText("Set up TG Media");
        title.setTextColor(Color.rgb(35, 28, 55));
        title.setTextSize(28);
        content.addView(title);
        TextView description = new TextView(this);
        description.setText("This unofficial Telegram client requires your own API ID and API hash from my.telegram.org/apps.\n\nThese are application credentials, not your phone number, login code, or two-step verification password. They are stored only in this app's private storage.\n\nAfter setup, sign in to your Telegram account normally.");
        description.setTextColor(Color.rgb(55, 50, 68));
        description.setTextSize(16);
        description.setPadding(0, dp(18), 0, dp(18));
        content.addView(description);

        EditText id = new EditText(this);
        id.setHint("API ID");
        id.setContentDescription("API ID");
        id.setInputType(InputType.TYPE_CLASS_NUMBER);
        id.setSingleLine(true);
        id.setSaveEnabled(false);
        id.setImeOptions(EditorInfo.IME_FLAG_NO_PERSONALIZED_LEARNING);
        if (Build.VERSION.SDK_INT >= 26) id.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO);
        content.addView(id);
        EditText hash = new EditText(this);
        hash.setHint("API hash");
        hash.setContentDescription("API hash");
        hash.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        hash.setSingleLine(true);
        hash.setSaveEnabled(false);
        hash.setImeOptions(EditorInfo.IME_FLAG_NO_PERSONALIZED_LEARNING);
        if (Build.VERSION.SDK_INT >= 26) hash.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO);
        content.addView(hash);

        Button website = new Button(this);
        website.setText("Get API credentials");
        website.setOnClickListener(view -> startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse("https://my.telegram.org/apps"))));
        content.addView(website);
        Button save = new Button(this);
        save.setText("Save and continue");
        save.setOnClickListener(view -> {
            if (ApiCredentialFormat.parseId(id.getText().toString()) == 0) {
                id.setError("Enter your own positive API ID. The public sample ID is not supported.");
                return;
            }
            if (ApiCredentialFormat.normalizeHash(hash.getText().toString()).isEmpty()) {
                hash.setError("Enter the 32-character hexadecimal API hash.");
                return;
            }
            if (!ApiCredentials.save(this, id.getText().toString(), hash.getText().toString())) {
                hash.setError("Could not save API configuration. Please try again.");
                return;
            }
            hash.setText("");
            openClient();
        });
        content.addView(save);
    }

    private void openClient() {
        if (Build.VERSION.SDK_INT >= 26) {
            AutofillManager autofill = getSystemService(AutofillManager.class);
            if (autofill != null) autofill.cancel();
        }
        BuildVars.APP_ID = ApiCredentials.getApiId(this);
        BuildVars.APP_HASH = ApiCredentials.getApiHash(this);
        ((ApplicationLoader) getApplication()).initializeTelegram();
        startActivity(ApiSetupRedirect.resume(this));
        finish();
    }
}
