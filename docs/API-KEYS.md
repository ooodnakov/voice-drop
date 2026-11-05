# Getting your `api_id` and `api_hash`

VoiceDrop talks to Telegram with **your own application credentials**. They are
free, take about a minute to create, and are not the same thing as your account
password.

> **Why does it ask for them?** Telegram requires every third-party client to be
> registered as an "app". `api_id` and `api_hash` identify that app. The app
> password you use in the Telegram client is separate and is only entered later,
> during login.

## Steps

1. Open <https://my.telegram.org> and log in with your phone number.
   Use the number you want to send from; the login code arrives in Telegram.
2. Go to **API development tools**.
3. If you have no app yet, fill the short form:
   - **App title** — anything, for example `VoiceDrop`
   - **Short name** — lowercase, for example `voicedrop`
   - **URL** — can stay empty
   - **Platform** — choose *Desktop*
   - **Description** — anything, for example `personal file sender`
4. Press **Create application** (or **Create app**).
5. The page now shows **App api_id** (a number) and **App api_hash** (32 hex
   characters). Copy both.

## Put them into the app

1. Launch VoiceDrop. On a fresh install the **API & runtime** panel opens by
   itself.
2. Paste `api_id` and `api_hash`, press **Save**.
3. The sign-in panel appears: enter your phone number, the code from Telegram,
   and your 2FA password if the account has one.

Everything is stored only on your machine, in `data/config.json` next to the
executable (or the per-user data folder when the install directory is read-only).

## If something goes wrong

| Message | Meaning |
|---|---|
| `API_ID_INVALID` | wrong `api_id`, or it was copied with spaces |
| `API_ID_PUBLISHED_FLOOD` | this pair was published somewhere public; create a fresh app |
| `PHONE_NUMBER_INVALID` | the number needs its country code, e.g. `+380…` |
| `PHONE_CODE_INVALID` / `PHONE_CODE_EXPIRED` | request a new code and enter it quickly |
| `SESSION_PASSWORD_NEEDED` | the account has 2FA — enter the password in the next step |

## Security

- Treat `api_hash` like a password. Do not commit it, do not paste it into
  public issues, do not share screenshots of the settings panel.
- One pair per person is enough; creating a second app later is also fine.
- To revoke: on my.telegram.org open **API development tools** → **Delete
  application**. Existing sessions keep working until you log them out from
  Telegram → Settings → Devices.
