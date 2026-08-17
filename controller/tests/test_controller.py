import contextlib
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest
from argparse import Namespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from unittest.mock import MagicMock, patch

from controller import (
    DEFAULT_WHISPER_MODEL,
    build_parser,
    build_request,
    main,
    record_audio,
    send_request,
    transcribe_audio,
)


class TestBuildRequest(unittest.TestCase):

    def test_search(self):
        args = Namespace(action="search", query="Blue Monday New Order")
        self.assertEqual(
            build_request(args),
            {"action": "search", "query": "Blue Monday New Order"},
        )

    def test_download_by_index(self):
        args = Namespace(action="download", index=2, match=None)
        self.assertEqual(build_request(args), {"action": "download", "index": 2})

    def test_download_by_match(self):
        args = Namespace(action="download", index=None, match="blue monday")
        self.assertEqual(
            build_request(args), {"action": "download", "match": "blue monday"}
        )

    def test_list_results(self):
        args = Namespace(action="list_results")
        self.assertEqual(build_request(args), {"action": "list_results"})


class TestBuildParser(unittest.TestCase):
    """Exercise the actual argparse wiring, not hand-built Namespaces."""

    def test_search_action(self):
        args = build_parser().parse_args(["search", "Blue Monday New Order"])
        self.assertEqual(args.action, "search")
        self.assertEqual(
            build_request(args),
            {"action": "search", "query": "Blue Monday New Order"},
        )

    def test_download_by_index_action(self):
        args = build_parser().parse_args(["download", "--index", "2"])
        self.assertEqual(args.action, "download")
        self.assertEqual(build_request(args), {"action": "download", "index": 2})

    def test_download_by_match_action(self):
        args = build_parser().parse_args(["download", "--match", "blue monday"])
        self.assertEqual(args.action, "download")
        self.assertEqual(
            build_request(args), {"action": "download", "match": "blue monday"}
        )

    def test_list_results_action(self):
        args = build_parser().parse_args(["list_results"])
        self.assertEqual(args.action, "list_results")
        self.assertEqual(build_request(args), {"action": "list_results"})

    def test_action_is_required(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args([])

    def test_download_index_and_match_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(
                ["download", "--index", "1", "--match", "blue monday"]
            )

    def test_socket_path_flag(self):
        args = build_parser().parse_args(
            ["--socket-path", "/tmp/custom.sock", "list_results"]
        )
        self.assertEqual(args.socket_path, "/tmp/custom.sock")

    def test_timeout_default(self):
        args = build_parser().parse_args(["list_results"])
        self.assertEqual(args.timeout, 45)

    def test_timeout_flag(self):
        args = build_parser().parse_args(["--timeout", "5", "list_results"])
        self.assertEqual(args.timeout, 5.0)


class TestSendRequest(unittest.TestCase):

    def setUp(self):
        tmpdir = tempfile.mkdtemp(prefix="vc_")
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        self.socket_path = os.path.join(tmpdir, "c.sock")

    def test_round_trip(self):
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(server.close)
        server.bind(self.socket_path)
        server.listen(1)

        def serve_one():
            conn, _ = server.accept()
            with conn:
                line = conn.makefile("r", encoding="utf-8").readline()
                request = json.loads(line)
                response = {"echo": request}
                conn.sendall(json.dumps(response).encode("utf-8") + b"\n")

        thread = threading.Thread(target=serve_one, daemon=True)
        thread.start()

        response = send_request({"action": "list_results"}, self.socket_path)

        thread.join(timeout=2)

        self.assertEqual(response, {"echo": {"action": "list_results"}})

    def test_missing_socket_raises_connection_error(self):
        with self.assertRaises(ConnectionError):
            send_request({"action": "list_results"}, self.socket_path)

    def test_timeout_raises_connection_error(self):
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(server.close)
        server.bind(self.socket_path)
        server.listen(1)

        def accept_and_stall():
            conn, _ = server.accept()
            self.addCleanup(conn.close)

        thread = threading.Thread(target=accept_and_stall, daemon=True)
        thread.start()

        with self.assertRaises(ConnectionError) as ctx:
            send_request({"action": "list_results"}, self.socket_path, timeout=0.2)

        thread.join(timeout=2)
        self.assertIn("no response", str(ctx.exception))

    def test_malformed_response_raises_connection_error(self):
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(server.close)
        server.bind(self.socket_path)
        server.listen(1)

        def serve_garbage():
            conn, _ = server.accept()
            with conn:
                conn.makefile("r", encoding="utf-8").readline()
                conn.sendall(b"not json\n")

        thread = threading.Thread(target=serve_garbage, daemon=True)
        thread.start()

        with self.assertRaises(ConnectionError) as ctx:
            send_request({"action": "list_results"}, self.socket_path)

        thread.join(timeout=2)
        self.assertIn("malformed", str(ctx.exception))


class TestMain(unittest.TestCase):

    def test_missing_socket_returns_1_with_clean_error(self):
        tmpdir = tempfile.mkdtemp(prefix="vc_")
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        socket_path = os.path.join(tmpdir, "does-not-exist.sock")

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = main(["--socket-path", socket_path, "list_results"])

        self.assertEqual(exit_code, 1)
        output = stderr.getvalue()
        self.assertIn("error:", output)
        self.assertNotIn("Traceback", output)


class TestRecordAudio(unittest.TestCase):

    def test_concatenates_callback_chunks_in_order(self):
        captured = {}

        class FakeInputStream:
            def __init__(self, samplerate, channels, dtype, callback):
                captured["callback"] = callback

            def __enter__(self):
                captured["callback"](
                    np.array([[0.1], [0.2]], dtype="float32"), 2, None, None
                )
                captured["callback"](np.array([[0.3]], dtype="float32"), 1, None, None)
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                return False

        with patch("controller.sd.InputStream", side_effect=FakeInputStream), patch(
            "builtins.input", return_value=""
        ):
            audio = record_audio(sample_rate=16000)

        np.testing.assert_allclose(audio, np.array([0.1, 0.2, 0.3], dtype="float32"))

    def test_no_audio_captured_returns_empty_array(self):
        class EmptyInputStream:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                return False

        with patch("controller.sd.InputStream", side_effect=EmptyInputStream), patch(
            "builtins.input", return_value=""
        ):
            audio = record_audio(sample_rate=16000)

        self.assertEqual(audio.shape, (0,))


class TestTranscribeAudio(unittest.TestCase):

    def test_empty_audio_returns_empty_string_without_calling_model(self):
        model = MagicMock()

        result = transcribe_audio(np.zeros(0, dtype="float32"), model)

        self.assertEqual(result, "")
        model.transcribe.assert_not_called()

    def test_joins_and_strips_segment_texts(self):
        model = MagicMock()
        segment_one = MagicMock(text=" search blue monday ")
        segment_two = MagicMock(text="new order ")
        model.transcribe.return_value = ([segment_one, segment_two], MagicMock())

        result = transcribe_audio(np.ones(10, dtype="float32"), model)

        self.assertEqual(result, "search blue monday new order")


class TestLoadWhisperModel(unittest.TestCase):

    def test_constructs_whisper_model_for_cpu(self):
        with patch("controller.WhisperModel") as mock_whisper_model:
            from controller import load_whisper_model

            load_whisper_model(DEFAULT_WHISPER_MODEL)

        mock_whisper_model.assert_called_once_with(
            DEFAULT_WHISPER_MODEL, device="cpu", compute_type="int8"
        )


if __name__ == "__main__":
    unittest.main()
