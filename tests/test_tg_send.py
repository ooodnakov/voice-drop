"""Unit tests for the pure helpers in tg_send.py (no network, no ffmpeg)."""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tg_send  # noqa: E402 - needs the path fix above


class ChatIsNumericTest(unittest.TestCase):
    def test_numeric_ids(self):
        for value in ("123", " 123 ", "-1001234567890", "0"):
            self.assertTrue(tg_send.chat_is_numeric(value), value)

    def test_non_numeric_values(self):
        for value in ("@durov", "durov", "", "   ", "-", "12 34", "1.5", "12a", None):
            self.assertFalse(tg_send.chat_is_numeric(value), value)


class VoicePlanTest(unittest.TestCase):
    def test_opus_in_ogg_container_is_sent_as_is(self):
        for extension in (".ogg", ".oga", ".opus", ".OGG"):
            self.assertEqual(tg_send.voice_plan(extension, "opus", False), "send", extension)
            self.assertEqual(tg_send.voice_plan(extension, "OPUS", True), "send", extension)

    def test_vorbis_ogg_is_transcoded(self):
        self.assertEqual(tg_send.voice_plan(".ogg", "vorbis", True), "transcode")

    def test_missing_probe_is_transcoded(self):
        self.assertEqual(tg_send.voice_plan(".ogg", None, True), "transcode")

    def test_other_audio_is_transcoded(self):
        for extension in (".mp3", ".wav", ".m4a", ".flac"):
            self.assertEqual(tg_send.voice_plan(extension, None, True), "transcode", extension)

    def test_without_ffmpeg_it_fails(self):
        self.assertEqual(tg_send.voice_plan(".ogg", "vorbis", False), "fail")
        self.assertEqual(tg_send.voice_plan(".ogg", None, False), "fail")
        self.assertEqual(tg_send.voice_plan(".mp3", None, False), "fail")


class FileEventTest(unittest.TestCase):
    def test_success_event_shape(self):
        path = ROOT / "tg_send.py"
        event = tg_send.file_event(path, 3, message_id=77, voice=True, circle=False)
        self.assertEqual(event["event"], "file")
        self.assertEqual(event["path"], str(path.resolve()))
        self.assertTrue(Path(event["path"]).is_absolute())
        self.assertEqual(event["file"], "tg_send.py")
        self.assertEqual(event["index"], 3)
        self.assertEqual(event["id"], 77)
        self.assertIs(event["voice"], True)
        self.assertIs(event["circle"], False)
        self.assertNotIn("error", event)
        self.assertEqual(
            set(event), {"event", "path", "file", "index", "id", "voice", "circle"}
        )

    def test_failure_event_has_error_instead_of_id(self):
        path = ROOT / "tg_send.py"
        event = tg_send.file_event(path, 0, voice=False, circle=True, error="boom")
        self.assertEqual(event["error"], "boom")
        self.assertNotIn("id", event)
        self.assertIs(event["circle"], True)
        self.assertEqual(
            set(event), {"event", "path", "file", "index", "error", "voice", "circle"}
        )


class TempNameTest(unittest.TestCase):
    def test_converted_names_are_uniquified(self):
        with tempfile.TemporaryDirectory() as raw:
            workdir = Path(raw)
            first = tg_send.converted_target(workdir, "voice", 0)
            self.assertEqual(first.name, "voice-0.ogg")
            self.assertEqual(first.parent, workdir)
            first.write_bytes(b"x")

            second = tg_send.converted_target(workdir, "voice", 0)
            self.assertEqual(second.name, "voice-0-2.ogg")
            second.write_bytes(b"x")

            third = tg_send.converted_target(workdir, "voice", 0)
            self.assertEqual(third.name, "voice-0-3.ogg")

            circle = tg_send.converted_target(workdir, "circle", 4)
            self.assertEqual(circle.name, "circle-4.mp4")


class SessionLockPathTest(unittest.TestCase):
    def test_lock_sits_next_to_the_session(self):
        with tempfile.TemporaryDirectory() as raw:
            session = Path(raw) / "acct.session"
            self.assertEqual(
                tg_send.session_lock_path(str(session)), session.with_name("acct.session.lock")
            )

    def test_timeout_from_environment(self):
        self.assertEqual(tg_send.lock_timeout({}), tg_send.DEFAULT_LOCK_TIMEOUT)
        self.assertEqual(tg_send.lock_timeout({"VOICEDROP_LOCK_TIMEOUT": "12"}), 12.0)
        self.assertEqual(
            tg_send.lock_timeout({"VOICEDROP_LOCK_TIMEOUT": "junk"}),
            tg_send.DEFAULT_LOCK_TIMEOUT,
        )


class ParseArgsTest(unittest.TestCase):
    def test_api_credentials_fall_back_to_env(self):
        args = tg_send.parse_args(
            ["--session", "acct.session"],
            environ={"VOICEDROP_API_ID": "12345", "VOICEDROP_API_HASH": "abc123"},
        )
        self.assertEqual(args.api_id, 12345)
        self.assertEqual(args.api_hash, "abc123")

    def test_cli_flags_win_over_env(self):
        args = tg_send.parse_args(
            ["--session", "acct.session", "--api-id", "7", "--api-hash", "cli"],
            environ={"VOICEDROP_API_ID": "12345", "VOICEDROP_API_HASH": "env"},
        )
        self.assertEqual(args.api_id, 7)
        self.assertEqual(args.api_hash, "cli")

    def test_credentials_missing_without_env(self):
        args = tg_send.parse_args(["--session", "acct.session"], environ={})
        self.assertIsNone(args.api_id)
        self.assertIsNone(args.api_hash)

    def test_upload_timeout_defaults_and_overrides(self):
        self.assertEqual(
            tg_send.parse_args(["--session", "s"], environ={}).timeout,
            tg_send.DEFAULT_UPLOAD_TIMEOUT,
        )
        self.assertEqual(
            tg_send.parse_args(
                ["--session", "s"], environ={"VOICEDROP_UPLOAD_TIMEOUT": "120"}
            ).timeout,
            120.0,
        )
        self.assertEqual(
            tg_send.parse_args(["--session", "s", "--timeout", "0"], environ={}).timeout, 0.0
        )
        self.assertEqual(
            tg_send.parse_args(
                ["--session", "s", "--timeout", "5"], environ={"VOICEDROP_UPLOAD_TIMEOUT": "120"}
            ).timeout,
            5.0,
        )

    def test_login_2fa_reads_password_from_env(self):
        args = tg_send.parse_args(
            ["--session", "s", "--login-2fa"], environ={"VOICEDROP_2FA": "secret"}
        )
        self.assertTrue(args.login_2fa)
        self.assertEqual(args.login_password, "secret")


class FakeClient:
    def __init__(self, known=None, dialogs=()):
        self.known = dict(known or {})
        self.dialogs = list(dialogs)
        self.requested = []

    async def get_entity(self, value):
        self.requested.append(value)
        if value in self.known:
            return self.known[value]
        raise ValueError(f"no entity for {value!r}")

    async def iter_dialogs(self):
        for dialog in self.dialogs:
            yield dialog


class ResolveTargetTest(unittest.TestCase):
    def test_numeric_id_is_passed_as_int(self):
        client = FakeClient(known={42: "user-42"})
        entity = asyncio.run(tg_send.resolve_target(client, " 42 "))
        self.assertEqual(entity, "user-42")
        self.assertEqual(client.requested, [42])

    def test_username_is_passed_through(self):
        client = FakeClient(known={"@durov": "durov"})
        self.assertEqual(asyncio.run(tg_send.resolve_target(client, "@durov")), "durov")
        self.assertEqual(client.requested, ["@durov"])

    def test_unknown_numeric_id_scans_dialogs(self):
        entity = SimpleNamespace(id=99)
        client = FakeClient(
            dialogs=[SimpleNamespace(id=99, entity=entity)],
        )
        self.assertIs(asyncio.run(tg_send.resolve_target(client, "99")), entity)

    def test_unknown_numeric_id_reports_clear_error(self):
        client = FakeClient(dialogs=[SimpleNamespace(id=1, entity=SimpleNamespace(id=1))])
        with self.assertRaises(ValueError) as raised:
            asyncio.run(tg_send.resolve_target(client, "-100123"))
        self.assertIn("-100123 not found in this account's chats", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
