import json
import os
import socket
import socketserver
import threading


class _RequestHandler(socketserver.StreamRequestHandler):

    def setup(self):
        super().setup()
        self.server.register_connection(self.connection)

    def finish(self):
        self.server.unregister_connection(self.connection)
        super().finish()

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

            try:
                payload = json.dumps(response).encode("utf-8")
            except TypeError:
                payload = json.dumps({"error": "internal_error"}).encode("utf-8")

            self.wfile.write(payload + b"\n")


class _Server(socketserver.UnixStreamServer):

    # Single-connection, non-threading by design: Plugin._last_results is read and
    # rebound from socket-handler code with no lock of its own, which is only safe
    # because this server never runs two _RequestHandler.handle() calls concurrently.
    # Switching to a threading server would need a lock around _last_results too.

    def __init__(self, socket_path, on_request):
        self.on_request = on_request
        self._active_connection = None
        super().__init__(socket_path, _RequestHandler)

    def register_connection(self, connection):
        self._active_connection = connection

    def unregister_connection(self, connection):
        if self._active_connection is connection:
            self._active_connection = None

    def close_active_connection(self):
        connection = self._active_connection

        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


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
        os.chmod(self.socket_path, 0o600)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="VoiceControlSocketServer",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        if self._server is None:
            return

        self._server.close_active_connection()
        self._server.shutdown()
        self._server.server_close()

        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        self._server = None
        self._thread = None
