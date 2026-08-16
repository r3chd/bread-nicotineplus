import json
import socket
import threading
import unittest
from argparse import Namespace

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from controller import build_request, send_request


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


class TestSendRequest(unittest.TestCase):

    def setUp(self):
        self.socket_dir = "/tmp/vc_controller_test"
        os.makedirs(self.socket_dir, exist_ok=True)
        self.socket_path = os.path.join(self.socket_dir, "control.sock")
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

    def tearDown(self):
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

    def test_round_trip(self):
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self.socket_path)
        server.listen(1)

        def serve_one():
            conn, _ = server.accept()
            with conn:
                line = conn.makefile("r").readline()
                request = json.loads(line)
                response = {"echo": request}
                conn.sendall(json.dumps(response).encode("utf-8") + b"\n")

        thread = threading.Thread(target=serve_one)
        thread.start()

        response = send_request({"action": "list_results"}, self.socket_path)

        thread.join(timeout=2)
        server.close()

        self.assertEqual(response, {"echo": {"action": "list_results"}})

    def test_missing_socket_raises_connection_error(self):
        with self.assertRaises(ConnectionError):
            send_request({"action": "list_results"}, self.socket_path)


if __name__ == "__main__":
    unittest.main()
