from pynicotine.events import events
from pynicotine.pluginsystem import BasePlugin
from pynicotine.slskmessages import FileAttribute

SEARCH_COLLECTION_SECONDS = 5
MAX_LOGGED_RESULTS = 10


def _sanitize_for_log(value):
    return value.encode("unicode_escape").decode("ascii")


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

    def init(self):
        events.connect("file-search-response", self._file_search_response)

    def disable(self):
        events.disconnect("file-search-response", self._file_search_response)

    def vcsearch_command(self, args, **_unused):
        query = args.strip()

        if not query:
            self.output("Usage: /vcsearch <query>")
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
                "user": _sanitize_for_log(msg.username),
                "filename": _sanitize_for_log(filename),
                "size": size,
                "bitrate": attrs.get(FileAttribute.BITRATE) if attrs else None,
                "speed": msg.ulspeed,
            })

    def _log_collected_results(self):
        try:
            count = len(self._collected_results)
            self.log(f"vcsearch: token {self._active_token} collected {count} result(s)")

            for result in self._collected_results[:MAX_LOGGED_RESULTS]:
                self.log(
                    "vcsearch:   {user} - {filename} "
                    "({size} bytes, bitrate={bitrate}, speed={speed})".format(**result)
                )
        except Exception as error:
            self.log(f"vcsearch: failed to log results: {error!r}")
        finally:
            self._active_token = None
            self._collected_results = []
