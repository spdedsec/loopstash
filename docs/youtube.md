# YouTube Publishing Engine

**LOOPSTASH** includes a direct YouTube integration for authorized publishing and scheduling of archived media.

---

## 🔑 Authentication & OAuth 2.0

### Step 1: Set Up Google Cloud OAuth 2.0 Client

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a Project and enable the **YouTube Data API v3**.
3. Configure the OAuth Consent Screen (add `youtube.upload` and `youtube.readonly` scopes).
4. Create Credentials of type **OAuth client ID** -> **Desktop app**.
5. Download the JSON credentials file (e.g. `client_secret.json`).

### Step 2: Connect Your Channel

```bash
loopstash publish auth youtube --client-secrets /path/to/client_secret.json
```

A local browser window will open to authenticate your Google Account. Once confirmed, LOOPSTASH fetches the YouTube Channel ID and stores tokens securely.

### Credential Storage Security
- **System Keyring**: Default storage utilizes the OS secure keyring (`secretstorage` on Linux, Keychain on macOS, Windows Credential Manager).
- **Secure File Fallback**: If no keyring daemon is present, tokens are stored in `~/.config/loopstash/credentials/<channel_id>.json` with strict POSIX permissions `0o600` (readable only by the current user).

### Listing and Disconnecting Accounts
```bash
# View connected channels
loopstash publish accounts

# Disconnect a channel
loopstash publish disconnect UC_SAMPLE_CHANNEL_ID
```

---

## 🚀 Publishing Videos

### Immediate Public Release
```bash
loopstash publish youtube <MEDIA_ID> --channel <CHANNEL_ID> --confirm-rights
```

### Previewing Before Upload
```bash
loopstash publish preview <MEDIA_ID>
```
Displays title, description, tags, privacy status, and checks daily quota.

---

## 🛡️ Section 13 Safeguards (Copyright & Rights Protection)

1. **Explicit Rights Confirmation**:
   LOOPSTASH will refuse to upload any video unless the `--confirm-rights` flag is passed or `rights_confirmed` is explicitly set via `loopstash library edit <ID> --confirm-rights`.
2. **Pre-flight Integrity Verification**:
   Before initiating an upload, LOOPSTASH re-computes the file's SHA-256 digest and compares it against the cataloged database hash to ensure the media has not been altered or tampered with on disk.

---

## 📊 Resumable Chunked Uploading & Quota Tracking

- **Chunked Resumption**: Videos are uploaded in 4 MB chunks using Google's resumable protocol. If the connection drops at 85%, the session URI is reloaded from SQLite and uploads continue from the 85% mark without starting over.
- **Quota Tracking**: Daily upload units are tracked automatically in SQLite. View current capacity with:
  ```bash
  loopstash publish status
  ```
