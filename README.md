# VoiceDrop

Portable Tauri tray-less window: drop a file on it and it lands in Telegram.
Audio becomes a voice note, anything else a document. Log in as any Telegram
account from inside the app.

## Requirements

- `ffmpeg` — voice notes and round videos. The Linux `.deb` pulls it in; otherwise
  install it or drop the binary into `bin/`.
- Python 3.10+ **or** `uv` — the first launch provisions `runtime/python`
  (`uv venv` + `uv pip install telethon`, or `python -m venv` + pip). The Windows
  zip and the Linux tarball ship `bin/uv`.

## Build

```
npm install
npm run tauri build      # installers for the current OS
cargo build --release    # plain binary in target/release
```

Release targets: Windows `msi` + `nsis`, macOS `dmg` (universal), Linux `deb` +
`AppImage`, plus a plain binary archive per OS. `.github/workflows/release.yml`
builds them on a `v*` tag; `ci.yml` builds every push.

## Layout (portable)

```
VoiceDrop\
  voice-drop.exe
  bin\uv.exe            (ships with the folder; used to build the runtime)
  bin\ffmpeg.exe        (optional: ffmpeg here instead of PATH)
  data\config.json      (created on first run)
  data\accounts\*.session
  data\instance.lock
  runtime\python\       (auto-created venv with telethon)
```

Everything the app needs lives next to the exe, so the whole folder can be
copied to another machine unchanged (Telethon session files are portable). When
the folder is not writable — macOS `.app` in `/Applications`, Linux AppImage —
`data/` and `runtime/` fall back to the per-user data directory
(`%APPDATA%\VoiceDrop`, `~/Library/Application Support/VoiceDrop`,
`$XDG_DATA_HOME/voicedrop`).

## First run

1. `prepare` builds the runtime with **uv** (`bin\uv.exe`, else `uv` on PATH):
   `uv venv --python 3.12 runtime\python` + `uv pip install telethon`. If uv is
   absent it falls back to `python -m venv` + pip. The environment lives inside
   the portable folder, so the whole directory stays self-contained.
2. `ffmpeg` is required: either on `PATH` or as `bin\ffmpeg.exe`. Without it
   sending is refused with an explicit error.
3. `⇢ +` logs in: phone → code → 2FA password if the account has one. The
   session is written to `data\accounts\<digits>.session`.
4. `⚙` first asks for your own `api_id` / `api_hash` — create them once at
   my.telegram.org → API development tools (no credentials ship with the app),
   and shows the resolved python/ffmpeg paths plus a run-check for
   python/telegram/ffmpeg.

Nothing is imported from the system: the app reads only its own `data/config.json`
next to the executable, so a copied folder starts empty and a fresh download never
inherits another install's account or credentials.

## Using it

- Drop a file anywhere on the window or press **or choose a file…**
- Video files always go as Telegram round video notes: ffmpeg square-crops to
  480x480, H.264 baseline + AAC, trims to 59 s.
- The window is frameless: the printed title strip is the drag handle and the
  drawn `–` / `×` controls minimise and close it. Layout adapts from 340×260 to
  any larger size (drop field and tracking list share the height; type and
  paddings scale once above 700 px).
- Choose the account (`account` row) and the recipient (`to` row); both are
  remembered. The recipient is a formatted listbox (name, username/chat id and
  kind), keyboard driven, and it flips above the button in short windows. `↻`
  reloads recent chats, `−` removes the active account's session.
- `⚙ → Theme`: `gruvbox-dark` (default), `gruvbox-light`, `paper`, `slate`, and
  `system`, which follows the OS light/dark preference.
- Rows in the log show `voice · #id` / `document · #id` or the failure reason.

## How it works

```
voice-drop.exe ──▶ %TEMP%\VoiceDrop\tg_send.py   (embedded copy of tg_send.py)
                └─▶ runtime\python\Scripts\python.exe tg_send.py --session … --chat …
```

`tg_send.py` uses Telethon: audio is transcoded through ffmpeg to 48 kHz mono
Opus (`-application voip`) and sent with `voice_note=True`; videos become square
MP4s sent with `video_note=True`; other files go as documents. Concurrent processes are serialized through
`%TEMP%\VoiceDrop\telegram-session.lock` and retried on `database is locked`.
A second app instance exits immediately.

## CLI modes

```
voice-drop.exe --oneshot <chat> <result.json> <file> [file...]

python tg_send.py --session <path> --api-id N --api-hash H --list-targets
python tg_send.py --session <path> --api-id N --api-hash H --whoami
python tg_send.py --session <path> --api-id N --api-hash H --login-send-code +380...
```
