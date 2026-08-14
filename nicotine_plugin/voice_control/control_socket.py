import json
import os
import socketserver
import threading


class _RequestHandler(socketserver.StreamRequestHandler):

    def handle(self):
        for line in self.rfile:
            line = line.strip()

            if not line:
                continue

            try:
                request = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                response = {"error": "invalid_json"}
            else:
                response = self.server.on_request(request)

            self.wfile.write(json.dumps(response).encode("utf-8") + b"\n")


class _Server(socketserver.UnixStreamServer):

    def __init__(self, socket_path, on_request):
        self.on_request = on_request
        super().__init__(socket_path, _RequestHandler)


class ControlSocketServer:

    def __init__(self, socket_path, on_request):
        self.socket_path = socket_path
        self._on_request = on_request
        self._server = None
        self._thread = None

    def start(self):
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        self._server = _Server(self.socket_path, self._on_request)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="VoiceControlSocketServer",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        if self._server is None:
            return

        self._server.shutdown()
        self._server.server_close()

        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        self._server = None
        self._thread = None
