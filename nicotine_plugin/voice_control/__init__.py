import os
import threading
import time

from control_socket import ControlSocketServer
from pynicotine.events import events
from pynicotine.pluginsystem import BasePlugin
from pynicotine.slskmessages import FileAttribute

SEARCH_COLLECTION_SECONDS = 5
MAX_LOGGED_RESULTS = 10
MAX_RETURNED_RESULTS = 10
LOSSLESS_FORMATS = frozenset({"flac", "wav", "ape", "wv"})


def _sanitize_for_log(value):
    return value.encode("unicode_escape").decode("ascii")


def _file_format(filename):
    return os.path.splitext(filename)[1].lstrip(".").lower()


def _quality_score(result):
    file_format = _file_format(result["filename"])

    if file_format in LOSSLESS_FORMATS:
        return float("inf")

    return result["bitrate"] or 0


def _rank_key(result):
    return (
        0 if result["free_slot"] else 1,
        -_quality_score(result),
        -(result["speed"] or 0),
        result["queue_length"] or 0,
    )


class Plugin(BasePlugin):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.commands = {
            "vcsearch": {
                "callback": self.vcsearch_command,
                "description": "voice_control: trigger a Soulseek search and log results",
                "parameters": ["<query>"],
            }
        }

        self._active_token = None
        self._collected_results = []
        self._last_results = []
        self._control_socket = None
        self._search_lock = threading.Lock()

    def init(self):
        events.connect("file-search-response", self._file_search_response)

        socket_path = os.path.join(self.path, "control.sock")
        self._control_socket = ControlSocketServer(socket_path, self._handle_request)
        self._control_socket.start()

    def disable(self):
        events.disconnect("file-search-response", self._file_search_response)

        if self._control_socket is not None:
            self._control_socket.stop()
            self._control_socket = None

    def _run_on_main_thread(self, func, *args, **kwargs):
        done = threading.Event()
        result_box = {}

        def _call():
            try:
                result_box["value"] = func(*args, **kwargs)
            except Exception as error:
                result_box["error"] = error
            finally:
                done.set()

        events.invoke_main_thread(_call)
        done.wait()

        if "error" in result_box:
            raise result_box["error"]

        return result_box.get("value")

    def vcsearch_command(self, args, **_unused):
        query = args.strip()

        if not query:
            self.output("Usage: /vcsearch <query>")
            return False

        if not self._search_lock.acquire(blocking=False):
            self.output("vcsearch: a search is already in progress, try again shortly")
            return False

        self._collected_results = []
        self.core.search.do_search(query, "global")
        self._active_token = self.core.search.token

        self.log(f"vcsearch: searching for '{query}' (token {self._active_token})")

        events.schedule(delay=SEARCH_COLLECTION_SECONDS, callback=self._log_collected_results)

        return True

    def _file_search_response(self, msg):
        if msg.token != self._active_token:
            return

        for _code, filename, size, _ext, attrs in msg.list:
            self._collected_results.append({
                "user": msg.username,
                "filename": filename,
                "size": size,
                "bitrate": attrs.get(FileAttribute.BITRATE) if attrs else None,
                "speed": msg.ulspeed,
                "free_slot": msg.freeulslots,
                "queue_length": msg.inqueue,
            })

    def _log_collected_results(self):
        try:
            count = len(self._collected_results)
            self.log(f"vcsearch: token {self._active_token} collected {count} result(s)")

            for result in self._collected_results[:MAX_LOGGED_RESULTS]:
                self.log(
                    "vcsearch:   {user} - {filename} "
                    "({size} bytes, bitrate={bitrate}, speed={speed})".format(
                        user=_sanitize_for_log(result["user"]),
                        filename=_sanitize_for_log(result["filename"]),
                        size=result["size"],
                        bitrate=result["bitrate"],
                        speed=result["speed"],
                    )
                )
        except Exception as error:
            self.log(f"vcsearch: failed to log results: {error!r}")
        finally:
            self._active_token = None
            self._collected_results = []
            self._search_lock.release()

    def _start_search(self, query):
        self._collected_results = []
        self.core.search.do_search(query, "global")
        self._active_token = self.core.search.token
        return self._active_token

    def _finish_search(self):
        results = list(self._collected_results)
        self._active_token = None
        self._collected_results = []
        return results

    def _handle_search(self, query):
        if not query:
            return {"error": "empty_query"}

        if not self._search_lock.acquire(blocking=False):
            return {"error": "search_in_progress"}

        try:
            token = self._run_on_main_thread(self._start_search, query)
            self.log(f"vcsearch (socket): searching for '{_sanitize_for_log(query)}' (token {token})")

            time.sleep(SEARCH_COLLECTION_SECONDS)

            results = self._run_on_main_thread(self._finish_search)
            ranked = sorted(results, key=_rank_key)[:MAX_RETURNED_RESULTS]

            self._last_results = [
                {
                    "index": position,
                    "filename": result["filename"],
                    "user": result["user"],
                    "size": result["size"],
                    "format": _file_format(result["filename"]),
                    "bitrate": result["bitrate"],
                    "speed": result["speed"],
                }
                for position, result in enumerate(ranked, start=1)
            ]

            self.log(f"vcsearch (socket): token {token} returning {len(self._last_results)} result(s)")

            return {"results": self._last_results}
        finally:
            self._search_lock.release()

    def _handle_request(self, request):
        try:
            return self._dispatch_request(request)
        except Exception as error:
            self.log(f"vcsearch (socket): request failed: {error!r}")
            return {"error": "internal_error"}

    def _dispatch_request(self, request):
        action = request.get("action")

        if action == "search":
            return self._handle_search(request.get("query", ""))

        return {"error": "unknown_action"}
