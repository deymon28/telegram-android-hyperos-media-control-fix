# Privacy and credentials

TG Media does not bundle the maintainer's Telegram API ID or API hash. Each installation asks for the user's own application credentials before initializing the Telegram client. Credentials are format-validated locally; Telegram validates them during connection and login.

The API ID and API hash are stored in Android private SharedPreferences under `private_api_config`. They are not encrypted separately from Android's app-data protection. A rooted or otherwise compromised device can expose application data. App backups are disabled, and the credential preferences are explicitly excluded from cloud backup and device-transfer rules. The setup screen disables screenshots, autofill, and saved text state.

The API credentials are passed to Telegram's normal client connection and authentication code. This patch adds no analytics or separate collection endpoint. The upstream client still communicates with Telegram and services used by its features. This project has not performed a complete privacy audit of upstream Telegram or all of its dependencies.

The public APK omits the sample Firebase provider/configuration, Google authentication client ID, and Google Maps API key. The source retains upstream license notices and public upstream development samples; these are not maintainer credentials.

To remove saved configuration, clear TG Media's app data or uninstall it. This also removes that installation's local account sessions and local data. It does not clear the official Telegram application's data.

When reporting a problem, omit phone numbers, account names, API hashes, login codes, passwords, chat contents, device identifiers, and raw diagnostic logs. Reproduce with non-sensitive media where possible.
