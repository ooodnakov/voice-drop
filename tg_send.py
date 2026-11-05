#!/usr/bin/env python3
"""Telegram sender/login backend for VoiceDrop.

Commands:
    --list-targets               recent chats of the current account
    --whoami                     account info for a session
    --login-send-code <phone>    request a login code
    --login-code <code> [--login-send-code <phone>] [--phone-hash <hash>]
    --login-password <password>  complete 2FA
    <paths...>                   send files (needs --chat)

Always prints one JSON object on stdout.
"""

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# The app pipes stdout with no console attached, so Python defaults to the
# ANSI code page. Chat titles are Unicode; force UTF-8 to keep JSON intact.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

SESSION_LOCK_PATH = Path(tempfile.gettempdir()) / "VoiceDrop" / "telegram-session.lock"


class SessionLock:
    """Serialize access to a Telethon SQLite session across processes.

    The same session file can be opened by the MCP server and by several app
    invocations; SQLite's default 5 s busy timeout is not enough when uploads
    overlap.
    """

    def __init__(self, timeout: float = 90.0):
        self.timeout = timeout
        self._handle = None

    def __enter__(self):
        SESSION_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        handle = open(SESSION_LOCK_PATH, "a+")
        handle.seek(0)
        handle.write("0")
        handle.flush()
        handle.seek(0)
        deadline = time.time() + self.timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._handle = handle
                return handle
            except OSError:
                if time.time() > deadline:
                    handle.close()
                    raise
                time.sleep(0.2)

    def __exit__(self, *_exc):
        handle, self._handle = self._handle, None
        if handle is None:
            return False
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
        return False


async def connect_with_retry(client, attempts: int = 20, delay: float = 0.5):
    for attempt in range(attempts):
        try:
            await client.connect()
            return
        except Exception as error:  # noqa: BLE001 - only "database is locked" retries
            if "locked" not in str(error).lower() or attempt == attempts - 1:
                raise
            await asyncio.sleep(delay)


AUDIO_EXTENSIONS = {
    ".mp3",
    ".m4a",
    ".m4b",
    ".wav",
    ".flac",
    ".aac",
    ".ogg",
    ".oga",
    ".opus",
    ".wma",
    ".aiff",
    ".aif",
    ".amr",
    ".caf",
}
OPUS_EXTENSIONS = {".ogg", ".oga", ".opus"}
VIDEO_EXTENSIONS = {
    ".mp4",
    ".m4v",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
    ".flv",
    ".wmv",
    ".3gp",
    ".mpeg",
    ".mpg",
    ".ts",
}
VIDEO_NOTE_SECONDS = 59
VIDEO_NOTE_EDGE = 480


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def transcode_to_opus(source: Path, ffmpeg_path: str = "") -> Path:
    """Convert any audio ffmpeg understands into a 48 kHz mono Opus voice file."""
    ffmpeg = ffmpeg_path or shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found on PATH; cannot build a voice note")
    target = Path(tempfile.gettempdir()) / f"voicedrop_{source.stem}_{source.stat().st_mtime_ns}.ogg"
    process = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-c:a",
            "libopus",
            "-b:a",
            "48k",
            "-ar",
            "48000",
            "-ac",
            "1",
            "-application",
            "voip",
            str(target),
        ],
        capture_output=True,
    )
    if process.returncode != 0 or not target.exists():
        detail = (process.stderr or b"").decode("utf-8", "replace").strip()[-400:]
        raise RuntimeError(f"ffmpeg failed for {source.name}: {detail or 'unknown error'}")
    return target


def to_video_note(source: Path, ffmpeg_path: str = "") -> Path:
    """Square-crop a video into an MP4 Telegram accepts as a round video note."""
    ffmpeg = ffmpeg_path or shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found on PATH; cannot build a video note")
    target = Path(tempfile.gettempdir()) / f"voicedrop_circle_{source.stem}_{source.stat().st_mtime_ns}.mp4"
    edge = VIDEO_NOTE_EDGE
    process = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-t",
            str(VIDEO_NOTE_SECONDS),
            "-vf",
            f"crop='min(iw,ih)':'min(iw,ih)',scale={edge}:{edge}:flags=lanczos",
            "-c:v",
            "libx264",
            "-profile:v",
            "baseline",
            "-level",
            "3.1",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
            "-crf",
            "26",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-ac",
            "1",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            str(target),
        ],
        capture_output=True,
    )
    if process.returncode != 0 or not target.exists():
        detail = (process.stderr or b"").decode("utf-8", "replace").strip()[-400:]
        raise RuntimeError(f"ffmpeg failed to build a video note from {source.name}: {detail or 'unknown error'}")
    return target


def target_value(entity, dialog_id: int) -> str:
    username = getattr(entity, "username", None)
    return f"@{username}" if username else str(dialog_id)


def target_kind(entity) -> str:
    from telethon.tl.types import Chat, Channel, User

    if getattr(entity, "bot", False):
        return "bot"
    if isinstance(entity, User):
        return "user"
    if isinstance(entity, Chat):
        return "group"
    if isinstance(entity, Channel):
        return "channel" if getattr(entity, "broadcast", False) else "group"
    return "unknown"


def user_payload(me) -> dict:
    name = " ".join(part for part in (me.first_name, me.last_name) if part) or (
        me.username or str(me.id)
    )
    return {"id": me.id, "name": name, "username": me.username or ""}


def make_client(args):
    from telethon import TelegramClient

    return TelegramClient(args.session, args.api_id, args.api_hash)


async def whoami(args) -> int:
    with SessionLock():
        client = make_client(args)
        await connect_with_retry(client)
        try:
            if not await client.is_user_authorized():
                emit({"ok": False, "error": "not authorized"})
                return 3
            me = await client.get_me()
            emit({"ok": True, "user": user_payload(me), "session": args.session})
        finally:
            await client.disconnect()
    return 0


async def login_send_code(args) -> int:
    with SessionLock():
        client = make_client(args)
        await connect_with_retry(client)
        try:
            sent = await client.send_code_request(args.login_send_code)
            emit(
                {
                    "ok": True,
                    "phone_hash": sent.phone_code_hash,
                    "code_type": type(sent.type).__name__,
                }
            )
        except Exception as error:  # noqa: BLE001 - surfaced to the UI
            emit({"ok": False, "error": str(error)})
            return 1
        finally:
            await client.disconnect()
    return 0


async def login_code(args) -> int:
    from telethon.errors import SessionPasswordNeededError

    with SessionLock():
        client = make_client(args)
        await connect_with_retry(client)
        try:
            await client.sign_in(
                phone=args.login_send_code,
                code=args.login_code,
                phone_code_hash=args.phone_hash,
            )
            me = await client.get_me()
            emit({"ok": True, "user": user_payload(me)})
            return 0
        except SessionPasswordNeededError:
            emit({"ok": False, "needs_password": True})
            return 0
        except Exception as error:  # noqa: BLE001 - surfaced to the UI
            emit({"ok": False, "error": str(error)})
            return 1
        finally:
            await client.disconnect()


async def login_password(args) -> int:
    with SessionLock():
        client = make_client(args)
        await connect_with_retry(client)
        try:
            await client.sign_in(password=args.login_password)
            me = await client.get_me()
            emit({"ok": True, "user": user_payload(me)})
            return 0
        except Exception as error:  # noqa: BLE001 - surfaced to the UI
            emit({"ok": False, "error": str(error)})
            return 1
        finally:
            await client.disconnect()


async def list_targets(args: argparse.Namespace) -> int:
    with SessionLock():
        client = make_client(args)
        await connect_with_retry(client)
        try:
            if not await client.is_user_authorized():
                emit({"ok": False, "error": "session is not authorized"})
                return 3
            targets = []
            seen = set()
            async for dialog in client.iter_dialogs(limit=args.limit):
                value = target_value(dialog.entity, dialog.id)
                if value in seen:
                    continue
                seen.add(value)
                targets.append(
                    {
                        "value": value,
                        "name": dialog.name or value,
                        "kind": target_kind(dialog.entity),
                    }
                )
        finally:
            await client.disconnect()

    emit({"ok": True, "targets": targets})
    return 0


async def run(args: argparse.Namespace) -> int:
    paths = [Path(raw).expanduser() for raw in args.paths]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        emit({"ok": False, "error": f"file not found: {', '.join(missing)}"})
        return 2

    results = []
    with SessionLock():
        client = make_client(args)
        await connect_with_retry(client)
        temp_files = []
        try:
            if not await client.is_user_authorized():
                emit({"ok": False, "error": "session is not authorized"})
                return 3
            entity = await client.get_entity(args.chat)

            for path in paths:
                suffix = path.suffix.lower()
                is_video = suffix in VIDEO_EXTENSIONS and not args.document
                is_circle = bool(args.circle) and is_video
                is_voice = not is_circle and (
                    args.voice or (suffix in AUDIO_EXTENSIONS and not args.document)
                )
                payload = path
                try:
                    if is_circle:
                        payload = to_video_note(path, args.ffmpeg or "")
                        temp_files.append(payload)
                    elif is_voice and suffix not in OPUS_EXTENSIONS:
                        payload = transcode_to_opus(path, args.ffmpeg or "")
                        temp_files.append(payload)
                    message = await client.send_file(
                        entity,
                        str(payload),
                        caption=args.caption or None,
                        voice_note=is_voice,
                        video_note=is_circle,
                    )
                    results.append(
                        {
                            "file": path.name,
                            "id": message.id,
                            "voice": is_voice,
                            "circle": is_circle,
                        }
                    )
                except Exception as error:  # noqa: BLE001 - report per file, keep going
                    results.append({"file": path.name, "error": str(error)})
        finally:
            await client.disconnect()
            for temp in temp_files:
                try:
                    temp.unlink()
                except OSError:
                    pass

    failed = [item for item in results if "error" in item]
    emit({"ok": not failed, "results": results})
    return 0 if not failed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="VoiceDrop Telegram backend")
    parser.add_argument("--session", required=True, help="Telethon session path")
    parser.add_argument("--api-id", required=True, type=int)
    parser.add_argument("--api-hash", required=True)
    parser.add_argument("--chat", help="username, @username, or numeric id")
    parser.add_argument("--caption", default="")
    parser.add_argument("--ffmpeg", default="", help="explicit ffmpeg path")
    parser.add_argument("--voice", action="store_true", help="force voice note for every file")
    parser.add_argument("--document", action="store_true", help="force document for every file")
    parser.add_argument("--circle", action="store_true", help="send videos as round video notes")
    parser.add_argument("--list-targets", action="store_true", help="print recent chats as JSON")
    parser.add_argument("--whoami", action="store_true")
    parser.add_argument("--login-send-code", metavar="PHONE")
    parser.add_argument("--login-code", metavar="CODE")
    parser.add_argument("--phone-hash", metavar="HASH")
    parser.add_argument("--login-password", metavar="PASSWORD")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("paths", nargs="*")

    args = parser.parse_args()
    if args.whoami:
        return asyncio.run(whoami(args))
    if args.login_send_code and not args.login_code:
        return asyncio.run(login_send_code(args))
    if args.login_code:
        if not args.login_send_code or not args.phone_hash:
            parser.error("--login-code needs --login-send-code and --phone-hash")
        return asyncio.run(login_code(args))
    if args.login_password:
        return asyncio.run(login_password(args))
    if args.list_targets:
        return asyncio.run(list_targets(args))
    if not args.chat or not args.paths:
        parser.error("--chat and at least one path are required unless a login/query mode is used")
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
