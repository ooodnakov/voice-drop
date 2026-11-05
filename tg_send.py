#!/usr/bin/env python3
"""Telegram sender/login backend for VoiceDrop.

Commands:
    --list-targets               recent chats of the current account
    --whoami                     account info for a session
    --login-send-code <phone>    request a login code
    --login-code <code> [--login-send-code <phone>] [--phone-hash <hash>]
    --login-2fa                  complete 2FA with the password from VOICEDROP_2FA
    --login-password <password>  complete 2FA (manual CLI use; password on argv)
    <paths...>                   send files (needs --chat)

Credentials and tunables come from the environment so they stay out of argv:
    VOICEDROP_API_ID, VOICEDROP_API_HASH, VOICEDROP_2FA,
    VOICEDROP_UPLOAD_TIMEOUT (seconds, default 900, 0 disables),
    VOICEDROP_LOCK_TIMEOUT (seconds, default 90).

Send mode prints one JSON progress object per line ({"event":"file",...}) and a
final {"ok":...,"results":[...]} summary. Every other mode prints exactly one
JSON object.
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

DEFAULT_LOCK_TIMEOUT = 90.0


def session_lock_path(session: str) -> Path:
    """Lock file that lives next to the session database it protects."""
    path = Path(session).expanduser()
    return path.with_name(path.name + ".lock")


def lock_timeout(environ=None) -> float:
    environment = os.environ if environ is None else environ
    raw = (environment.get("VOICEDROP_LOCK_TIMEOUT") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_LOCK_TIMEOUT
    return value if value > 0 else DEFAULT_LOCK_TIMEOUT


class SessionLock:
    """Serialize access to a Telethon SQLite session across processes.

    The same session file can be opened by the MCP server and by several app
    invocations; SQLite's default 5 s busy timeout is not enough when uploads
    overlap. Each session gets its own lock file so two accounts never block
    each other.
    """

    def __init__(self, session: str, timeout: float | None = None):
        self.path = session_lock_path(session)
        self.timeout = lock_timeout() if timeout is None else timeout
        self._handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+")
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


CONNECT_TIMEOUT = 30.0


def format_seconds(seconds: float) -> str:
    value = float(seconds)
    return str(int(value)) if value.is_integer() else f"{value:g}"


async def connect_with_retry(
    client, attempts: int = 20, delay: float = 0.5, timeout: float = CONNECT_TIMEOUT
):
    async def connect_once():
        for attempt in range(attempts):
            try:
                await client.connect()
                return
            except Exception as error:  # noqa: BLE001 - only "database is locked" retries
                if "locked" not in str(error).lower() or attempt == attempts - 1:
                    raise
                await asyncio.sleep(delay)

    try:
        await asyncio.wait_for(connect_once(), timeout=timeout)
    except asyncio.TimeoutError:
        raise RuntimeError(f"connection timed out after {format_seconds(timeout)}s") from None


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
DEFAULT_UPLOAD_TIMEOUT = 900.0
FFPROBE_TIMEOUT = 30.0
TEMP_PREFIX = "voicedrop-"


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def unique_path(directory: Path, name: str) -> Path:
    """Return directory/name, suffixing -2, -3, ... until the path is free."""
    candidate = Path(directory) / name
    stem, suffix = candidate.stem, candidate.suffix
    counter = 2
    while candidate.exists():
        candidate = Path(directory) / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def converted_target(workdir: Path, kind: str, index: int) -> Path:
    """Private output path for a converted file: voice-<n>.ogg / circle-<n>.mp4."""
    suffix = ".ogg" if kind == "voice" else ".mp4"
    return unique_path(Path(workdir), f"{kind}-{index}{suffix}")


def find_ffprobe(ffmpeg_path: str = "") -> str:
    """Locate ffprobe next to the given ffmpeg first, then on PATH."""
    names = ("ffprobe.exe", "ffprobe") if os.name == "nt" else ("ffprobe", "ffprobe.exe")
    if ffmpeg_path:
        directory = Path(ffmpeg_path).expanduser().parent
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return str(candidate)
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return ""


def probe_audio_codec(source: Path, ffprobe_path: str) -> str | None:
    """Return the first audio codec name, or None when it cannot be probed."""
    if not ffprobe_path:
        return None
    try:
        process = subprocess.run(
            [
                ffprobe_path,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "default=nw=1:nk=1",
                str(source),
            ],
            capture_output=True,
            timeout=FFPROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if process.returncode != 0:
        return None
    text = (process.stdout or b"").decode("utf-8", "replace").strip()
    return text.splitlines()[0].strip().lower() if text else None


def voice_plan(extension: str, codec: str | None, have_ffmpeg: bool) -> str:
    """Decide what a voice candidate needs: "send", "transcode", or "fail".

    Only a confirmed Opus stream inside an OGG container may go out as-is;
    everything else (Vorbis in .ogg, unprobeable files, other formats) is
    transcoded, and without ffmpeg there is no safe way to send it at all.
    """
    suffix = (extension or "").strip().lower()
    probed = (codec or "").strip().lower()
    if suffix in OPUS_EXTENSIONS and probed == "opus":
        return "send"
    return "transcode" if have_ffmpeg else "fail"


def prepare_voice_source(
    source: Path, ffmpeg_path: str = "", workdir: Path | None = None, index: int = 0
) -> Path:
    """Return a path safe to send with voice_note=True."""
    suffix = source.suffix.lower()
    ffmpeg = ffmpeg_path or shutil.which("ffmpeg")
    codec = None
    if suffix in OPUS_EXTENSIONS:
        codec = probe_audio_codec(source, find_ffprobe(ffmpeg_path))
    plan = voice_plan(suffix, codec, bool(ffmpeg))
    if plan == "send":
        return source
    if plan == "fail":
        if suffix in OPUS_EXTENSIONS:
            raise RuntimeError("ogg is not Opus and ffmpeg is missing")
        raise RuntimeError("ffmpeg not found on PATH; cannot build a voice note")
    return transcode_to_opus(source, ffmpeg_path, workdir, index)


def transcode_to_opus(
    source: Path, ffmpeg_path: str = "", workdir: Path | None = None, index: int = 0
) -> Path:
    """Convert any audio ffmpeg understands into a 48 kHz mono Opus voice file."""
    ffmpeg = ffmpeg_path or shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found on PATH; cannot build a voice note")
    target = converted_target(
        Path(workdir) if workdir is not None else Path(tempfile.gettempdir()), "voice", index
    )
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


def to_video_note(
    source: Path, ffmpeg_path: str = "", workdir: Path | None = None, index: int = 0
) -> Path:
    """Square-crop a video into an MP4 Telegram accepts as a round video note."""
    ffmpeg = ffmpeg_path or shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found on PATH; cannot build a video note")
    target = converted_target(
        Path(workdir) if workdir is not None else Path(tempfile.gettempdir()), "circle", index
    )
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


def chat_is_numeric(value: str | None) -> bool:
    """True for a (possibly negative) numeric chat id, e.g. "-1001234567890"."""
    text = (value or "").strip()
    return bool(text) and text.lstrip("-").isdigit()


async def resolve_target(client, chat: str):
    """Resolve a username/link or a numeric id into a Telethon entity.

    Numeric ids must reach Telethon as ints: passing the string makes it look
    up a username and fails. If Telethon's cache does not know the id yet, the
    dialog list is scanned, which matches what the UI shows under "chats".
    """
    value = (chat or "").strip()
    if not value:
        raise ValueError("no chat/recipient configured")
    if not chat_is_numeric(value):
        return await client.get_entity(value)

    numeric = int(value)
    try:
        return await client.get_entity(numeric)
    except (ValueError, TypeError):
        pass
    async for dialog in client.iter_dialogs():
        entity = getattr(dialog, "entity", None)
        if dialog.id == numeric or getattr(entity, "id", None) == numeric:
            return entity
    raise ValueError(
        f"recipient {numeric} not found in this account's chats; "
        "open the chat once in Telegram and retry"
    )


def restrict_session_permissions(session: str) -> None:
    """Make the session database (and its journal) private to the user."""
    if os.name == "nt":
        return
    session_path = Path(session).expanduser()
    for candidate in (session_path, Path(f"{session_path}-journal")):
        try:
            os.chmod(candidate, 0o600)
        except OSError:
            pass


def file_event(
    path: Path,
    index: int,
    *,
    message_id: int | None = None,
    voice: bool = False,
    circle: bool = False,
    error: str | None = None,
) -> dict:
    """Progress line for one file: sent on success, described on failure."""
    payload = {
        "event": "file",
        "path": str(path.expanduser().resolve()),
        "file": path.name,
        "index": index,
    }
    if error is None:
        payload["id"] = message_id
    else:
        payload["error"] = error
    payload["voice"] = bool(voice)
    payload["circle"] = bool(circle)
    return payload


def timed_out_text(timeout: float) -> str:
    return f"timed out after {format_seconds(timeout)}s"


async def send_file_with_timeout(
    client, entity, payload: Path, *, caption, voice_note: bool, video_note: bool, timeout
):
    send = client.send_file(
        entity,
        str(payload),
        caption=caption,
        voice_note=voice_note,
        video_note=video_note,
    )
    if timeout and timeout > 0:
        return await asyncio.wait_for(send, timeout=timeout)
    return await send


def user_payload(me) -> dict:
    name = " ".join(part for part in (me.first_name, me.last_name) if part) or (
        me.username or str(me.id)
    )
    return {"id": me.id, "name": name, "username": me.username or ""}


def make_client(args):
    from telethon import TelegramClient

    return TelegramClient(args.session, args.api_id, args.api_hash)


async def open_client(args):
    """Connect (bounded in time) and lock down the session file permissions."""
    client = make_client(args)
    await connect_with_retry(client)
    restrict_session_permissions(args.session)
    return client


async def whoami(args) -> int:
    with SessionLock(args.session):
        client = await open_client(args)
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
    with SessionLock(args.session):
        client = await open_client(args)
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

    with SessionLock(args.session):
        client = await open_client(args)
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
    with SessionLock(args.session):
        client = await open_client(args)
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
    with SessionLock(args.session):
        client = await open_client(args)
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
    workdir = None
    with SessionLock(args.session):
        client = await open_client(args)
        try:
            if not await client.is_user_authorized():
                emit({"ok": False, "error": "session is not authorized"})
                return 3
            try:
                entity = await resolve_target(client, args.chat)
            except Exception as error:  # noqa: BLE001 - surfaced to the UI
                emit({"ok": False, "error": str(error)})
                return 1

            workdir = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
            for index, path in enumerate(paths):
                suffix = path.suffix.lower()
                is_video = suffix in VIDEO_EXTENSIONS and not args.document
                is_circle = bool(args.circle) and is_video
                is_voice = not is_circle and (
                    args.voice or (suffix in AUDIO_EXTENSIONS and not args.document)
                )
                payload = path
                try:
                    if is_circle:
                        payload = to_video_note(path, args.ffmpeg or "", workdir, index)
                    elif is_voice:
                        payload = prepare_voice_source(path, args.ffmpeg or "", workdir, index)
                    message = await send_file_with_timeout(
                        client,
                        entity,
                        payload,
                        caption=args.caption or None,
                        voice_note=is_voice,
                        video_note=is_circle,
                        timeout=args.timeout,
                    )
                    results.append(
                        {
                            "file": path.name,
                            "id": message.id,
                            "voice": is_voice,
                            "circle": is_circle,
                        }
                    )
                    emit(
                        file_event(
                            path,
                            index,
                            message_id=message.id,
                            voice=is_voice,
                            circle=is_circle,
                        )
                    )
                except asyncio.TimeoutError:
                    detail = timed_out_text(args.timeout)
                    results.append({"file": path.name, "error": detail})
                    emit(
                        file_event(
                            path, index, voice=is_voice, circle=is_circle, error=detail
                        )
                    )
                except Exception as error:  # noqa: BLE001 - report per file, keep going
                    results.append({"file": path.name, "error": str(error)})
                    emit(
                        file_event(
                            path, index, voice=is_voice, circle=is_circle, error=str(error)
                        )
                    )
        finally:
            await client.disconnect()
            if workdir is not None:
                shutil.rmtree(workdir, ignore_errors=True)

    failed = [item for item in results if "error" in item]
    emit({"ok": not failed, "results": results})
    return 0 if not failed else 1


def _env_int(environ, name: str) -> int | None:
    raw = (environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _env_seconds(environ, name: str, default: float) -> float:
    raw = (environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VoiceDrop Telegram backend")
    parser.add_argument("--session", required=True, help="Telethon session path")
    parser.add_argument("--api-id", type=int, help="Telegram API id (or VOICEDROP_API_ID)")
    parser.add_argument("--api-hash", help="Telegram API hash (or VOICEDROP_API_HASH)")
    parser.add_argument("--chat", help="username, @username, phone invite link, or numeric id")
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
    parser.add_argument(
        "--login-2fa",
        action="store_true",
        help="complete 2FA using the password from VOICEDROP_2FA",
    )
    parser.add_argument("--login-password", metavar="PASSWORD", help="2FA password (manual use)")
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="per-file upload timeout; default from VOICEDROP_UPLOAD_TIMEOUT or 900, 0 disables",
    )
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("paths", nargs="*")
    return parser


def parse_args(argv=None, environ=None) -> argparse.Namespace:
    """Parse the CLI, filling credentials and timeouts from the environment."""
    environment = os.environ if environ is None else environ
    args = build_parser().parse_args(argv)
    if args.api_id is None:
        args.api_id = _env_int(environment, "VOICEDROP_API_ID")
    if not args.api_hash:
        args.api_hash = (environment.get("VOICEDROP_API_HASH") or "").strip() or None
    if args.timeout is None:
        args.timeout = _env_seconds(environment, "VOICEDROP_UPLOAD_TIMEOUT", DEFAULT_UPLOAD_TIMEOUT)
    if args.login_2fa and not args.login_password:
        args.login_password = (environment.get("VOICEDROP_2FA") or "").strip() or None
    return args


async def dispatch(args: argparse.Namespace) -> int:
    if args.whoami:
        return await whoami(args)
    if args.login_send_code and not args.login_code:
        return await login_send_code(args)
    if args.login_code:
        if not args.login_send_code or not args.phone_hash:
            emit({"ok": False, "error": "--login-code needs --login-send-code and --phone-hash"})
            return 2
        return await login_code(args)
    if args.login_2fa or args.login_password:
        if not args.login_password:
            emit({"ok": False, "error": "2FA password not provided; set VOICEDROP_2FA"})
            return 2
        return await login_password(args)
    if args.list_targets:
        return await list_targets(args)
    if not args.chat or not args.paths:
        emit(
            {
                "ok": False,
                "error": "--chat and at least one path are required unless a login/query mode is used",
            }
        )
        return 2
    return await run(args)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.api_id is None or not args.api_hash:
        emit(
            {
                "ok": False,
                "error": "missing Telegram API credentials: pass --api-id/--api-hash "
                "or set VOICEDROP_API_ID/VOICEDROP_API_HASH",
            }
        )
        return 2
    try:
        return asyncio.run(dispatch(args))
    except Exception as error:  # noqa: BLE001 - the UI always expects one JSON object
        emit({"ok": False, "error": str(error)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
