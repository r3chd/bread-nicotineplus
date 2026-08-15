# Nicotine+ Socket Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Unix domain socket server to the existing `voice_control` Nicotine+ plugin so an external process can trigger a search, list the last results, and queue a download by sending line-delimited JSON — verified with a raw Python socket script against the user's running Nicotine+ 3.3.10 install, with no controller process yet (that's step 3 of the design's build order, out of scope here).

**Architecture:** A new `ControlSocketServer` (in its own module, `control_socket.py`) runs a non-threading `socketserver.UnixStreamServer` on a background daemon thread, bound to `control.sock` inside the plugin's own folder. It knows nothing about Soulseek — it just accepts a connection, reads line-delimited JSON, and hands each parsed request to an injected callback, writing back whatever JSON that callback returns. The `Plugin` class (in `__init__.py`) owns that callback: it dispatches on `request["action"]`, marshals every `core.*` / `events` call onto Nicotine+'s GLib main thread via `events.invoke_main_thread(...)` (the same primitive `events.schedule(...)` uses internally — verified by disassembling `pynicotine/events.pyc` at tag 3.3.10), and keeps "last search results" as session state so `download` can resolve a 1-based `index` or a substring `match` against them.

**Tech Stack:** Python stdlib only — `socketserver`, `socket`, `threading`, `json`, `time`, `os` — matching Global Constraint "Python for both plugin and controller" and "no extra dependencies for this step" carried over from the step-1 plan. No third-party packages.

**Spec:** `docs/superpowers/specs/2026-08-11-nicotine-voice-control-design.md` — this plan implements the "Socket layer" (Build order, item 2) and, since the socket protocol table/ranking/error-handling rules live under the spec's "1. Plugin" component (not under "2. Controller"), this plan implements that entire component's socket-facing surface: the `search`, `download`, and `list_results` actions.

**Prior work:** Step 1 (`docs/superpowers/plans/2026-08-12-nicotine-plugin-skeleton.md`) delivered `nicotine_plugin/voice_control/PLUGININFO` and `nicotine_plugin/voice_control/__init__.py` (currently 80 lines — read in full before starting; every task below modifies it). The live plugin directory `~/.local/share/nicotine/plugins/voice_control` is already a symlink to this repo's `nicotine_plugin/voice_control/` (created in step 1, confirmed with the user then — no new confirmation needed for files placed inside that existing directory).

## Global Constraints

- Plugin source lives in this repo at `nicotine_plugin/voice_control/`; the live Nicotine+ plugin directory is a symlink to it — always edit the repo copy, never edit through the symlink path.
- **Threading constraint (from spec):** all socket I/O runs on a background thread; all `core.*` calls and `events` interactions are marshaled back onto Nicotine+'s GLib main loop — GTK/pynicotine internals are not safe to call directly from a non-main thread.
- **Transport (from spec):** Unix domain socket at `~/.local/share/nicotine/plugins/voice_control/control.sock` (in code: `os.path.join(self.path, "control.sock")` — see Task 1's implementer note for why `self.path` resolves to exactly this path).
- **Socket protocol (from spec, verbatim):**

  | Request | Response |
  | --- | --- |
  | `{"action": "search", "query": "<text>"}` | `{"results": [{"index": 1, "filename": ..., "user": ..., "size": ..., "format": ..., "bitrate": ..., "speed": ...}, ...]}` |
  | `{"action": "download", "index": <n>}` | `{"status": "queued", "filename": ...}` or `{"error": ...}` |
  | `{"action": "download", "match": "<text>"}` | same as above |
  | `{"action": "list_results"}` | same shape as `search` response, replayed from last search |

- **Result ranking (from spec, verbatim):** composite score, highest first: (1) has a free upload slot, (2) higher bitrate / lossless format, (3) higher user upload speed, (4) shorter queue length. Top 10 returned by default.
- **Download resolution (from spec, verbatim):** `index` is 1-based into the last returned result list. `match` is a case-insensitive substring match against filenames in the last result list; zero or multiple matches returns `{"error": "..."}` rather than guessing.
- **Error handling (from spec):** malformed JSON on the socket → plugin responds `{"error": "invalid_json"}` per line rather than dropping the connection. Nicotine+-side exceptions (e.g. `enqueue_download` raising) are caught in the plugin, logged via `self.log(...)`, returned as `{"error": ...}` — never allowed to propagate into Nicotine+'s own event loop.
- No controller process, no Whisper/Claude integration in this plan — those are design-spec steps 3–6, explicitly out of scope.
- `core.downloads.enqueue_download(username, virtual_path, folder_path=None, size=0, file_attributes=None, bypass_filter=False)` — verified via `pynicotine/downloads.pyc` at tag 3.3.10: always returns `None` (no success/failure signal in the return value), so a `{"status": "queued", ...}` response only means "no exception was raised while enqueuing," not "the download will succeed."

---

## File Structure

- Create: `nicotine_plugin/voice_control/control_socket.py` — `ControlSocketServer`, a small Soulseek-agnostic wrapper around `socketserver.UnixStreamServer`: accepts connections, reads line-delimited JSON, calls an injected `on_request(dict) -> dict` callback, writes the JSON response back. Has no knowledge of `core`, `events`, or search/download semantics — those live in `__init__.py`.
- Modify: `nicotine_plugin/voice_control/__init__.py` — add socket server lifecycle (`init()`/`disable()`), the `_run_on_main_thread` marshaling helper, and the `search`/`download`/`list_results` request handlers.
- Modify: `.gitignore` — the socket file is created at runtime inside `nicotine_plugin/voice_control/` (because the live plugin directory is a symlink *to* that repo folder, not a copy of it), so it must never be committed.

## Task 1: Socket transport module + lifecycle wiring (echo stub)

**Files:**
- Create: `nicotine_plugin/voice_control/control_socket.py`
- Modify: `nicotine_plugin/voice_control/__init__.py` (add imports, `self._control_socket` state, start it in `init()`, stop it in `disable()`, temporary stub handler)
- Modify: `.gitignore`

**Interfaces:**
- Produces: `ControlSocketServer(socket_path: str, on_request: Callable[[dict], dict])` with `.start()` and `.stop()` methods — Task 2 and Task 3 do not touch this class, only the `on_request` callback passed to it.
- Consumes (from Task 1's own stub, replaced in Task 2): nothing yet — the stub ignores its input.

This task isolates the tricky part (thread lifecycle, clean socket-file creation/teardown, line-delimited JSON framing) from the tricky-in-a-different-way part (main-thread marshaling, ranking, download resolution), so each can be verified independently, matching the spec's own note that this step is testable with "a raw Python script sending JSON lines" before any real command dispatch exists.

- [ ] **Step 1: Write `nicotine_plugin/voice_control/control_socket.py`** with this exact content:

```python
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
```

Notes for the implementer (why this shape, carry these into code review):

- `socketserver.UnixStreamServer` (non-threading) is used deliberately instead of `socketserver.ThreadingMixIn` — only one connection is ever handled at a time, which means the request-handling code in `__init__.py` (Task 2/3) never has to worry about two socket requests mutating `self._last_results` or `self._active_token` concurrently. Only one local controller process is expected to talk to this socket (per spec's architecture diagram); if that changes later, switching to a threading server is a one-line change isolated to this file.
- `_Server.shutdown()` is documented by the stdlib to be safe (and required) to call from a *different* thread than the one running `serve_forever()` — `Plugin.disable()` runs on Nicotine+'s main thread, `serve_forever()` runs on `VoiceControlSocketServer`, so this is exactly the supported cross-thread shutdown pattern, not a hazard.
- `for line in self.rfile` keeps one TCP-like connection open across multiple request/response round trips (matching how `nc -U` or a persistent socket client is normally used) rather than closing after one line — the spec doesn't mandate one-shot connections, and this is friendlier to manual testing.
- `os.path.exists(...)` / `os.unlink(...)` around bind and around stop handle a stale socket file left behind by a crashed previous run (`bind()` raises `OSError: [Errno 48] Address already in use` on a leftover socket file otherwise) and leave no stray file behind on clean shutdown.

- [ ] **Step 2: Add the socket-file `.gitignore` entry.** Read the current `.gitignore` first (it's 2 lines: `__pycache__/` and `*.pyc`), then add a third line:

```
nicotine_plugin/voice_control/control.sock
```

This is necessary because `~/.local/share/nicotine/plugins/voice_control` is a symlink *to* `nicotine_plugin/voice_control/` in this repo (created in step 1) — the socket file Nicotine+ creates at runtime physically lands inside the tracked repo directory, not in some separate untracked location.

- [ ] **Step 3: Wire the server into the plugin lifecycle.** In `nicotine_plugin/voice_control/__init__.py`, add the import and update `__init__`, `init`, and `disable`:

```python
import os

from control_socket import ControlSocketServer
from pynicotine.events import events
from pynicotine.pluginsystem import BasePlugin
from pynicotine.slskmessages import FileAttribute
```

(The `from control_socket import ControlSocketServer` line is a plain top-level import, not `from .control_socket import ...` — verified via disassembling `PluginHandler._import_plugin_instance` in `pynicotine/pluginsystem.pyc`: for user plugins loaded via `importlib.util.spec_from_file_location`, Nicotine+ does `sys.path.append(plugin_path)` *before* executing `__init__.py`, so `control_socket.py` sitting next to `__init__.py` in that same directory is importable as a plain top-level module. Relative imports would additionally depend on whether `spec_from_file_location` inferred package semantics from the `__init__.py` filename, which is not something this codebase's own loader relies on anywhere — plain absolute imports are the verified, guaranteed-to-work mechanism.)

In `Plugin.__init__`, after the existing `self._active_token = None` / `self._collected_results = []` lines, add:

```python
        self._control_socket = None
```

Replace the existing `init` and `disable` methods with:

```python
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
```

Add a temporary stub handler (Task 2 replaces its body — do not leave both versions in the file):

```python
    def _handle_request(self, request):
        return {"echo": request}
```

Implementer note: `self.path` is set by `PluginHandler._import_plugin_instance` (verified via `pynicotine/pluginsystem.pyc`) to the joined `(plugin_folder, plugin_name)` path *before* `init()` is called, and — unlike `_import_plugin_instance`'s internal `plugin_path` variable used for `importlib` loading — this join is never passed through `os.path.realpath`. Since the live plugin folder is `~/.local/share/nicotine/plugins` (a real directory) and `voice_control` inside it is a symlink, `self.path` is `~/.local/share/nicotine/plugins/voice_control` — the symlink path itself, matching the spec's stated socket location exactly, not the repo path the symlink resolves to.

- [ ] **Step 4: Verify Python syntax is valid for both files.**

Run: `python3 -c "import ast; ast.parse(open('nicotine_plugin/voice_control/control_socket.py').read()); ast.parse(open('nicotine_plugin/voice_control/__init__.py').read())"`
Expected: no output, exit code 0.

- [ ] **Step 5: Commit.**

```bash
git add nicotine_plugin/voice_control/control_socket.py nicotine_plugin/voice_control/__init__.py .gitignore
git commit -m "Add Unix socket transport for voice_control plugin (echo stub)"
```

- [ ] **Step 6: Quit Nicotine+ if running, then relaunch it from a terminal** so plugin load errors are visible:

```bash
/Applications/Nicotine+.app/Contents/MacOS/Nicotine+ &
```

Expected: the app window opens normally, no traceback mentioning `voice_control` or `control_socket` in the terminal. If the plugin was already enabled from step 1, it reloads automatically on launch; if not, enable it via Preferences → Plugins → "Voice Control".

- [ ] **Step 7: Verify the socket accepts a connection and the echo stub responds.** Write this script to `/private/tmp/claude-501/-Users-r3chd-Documents-GitHub-bread-nicotineplus/ffe76fe1-73ee-4102-869d-10d347a74c2d/scratchpad/verify_socket_echo.py`:

```python
import json
import os
import socket

socket_path = os.path.expanduser(
    "~/.local/share/nicotine/plugins/voice_control/control.sock"
)

with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
    sock.connect(socket_path)
    sock.sendall(json.dumps({"action": "ping"}).encode("utf-8") + b"\n")
    response_line = sock.makefile("r").readline()
    print(response_line.strip())
```

Run: `python3 /private/tmp/claude-501/-Users-r3chd-Documents-GitHub-bread-nicotineplus/ffe76fe1-73ee-4102-869d-10d347a74c2d/scratchpad/verify_socket_echo.py`
Expected output (exact): `{"echo": {"action": "ping"}}`

- [ ] **Step 8: Verify clean shutdown removes the socket file.** Disable the plugin via Preferences → Plugins (uncheck "Voice Control"), then run:

```bash
ls -la ~/.local/share/nicotine/plugins/voice_control/control.sock
```

Expected: `ls: .../control.sock: No such file or directory` (the file is gone — `ControlSocketServer.stop()` unlinked it). Re-enable the plugin afterward so Task 2's live verification has it running again.

- [ ] **Step 9: Nothing further to commit in this task** (verification script lives in the scratchpad directory, not the repo). Record in the final report that the socket transport round-trips a request and cleans up its socket file on disable.

## Task 2: Main-thread marshaling + `search` action

**Files:**
- Modify: `nicotine_plugin/voice_control/__init__.py`

**Interfaces:**
- Consumes: `ControlSocketServer` from Task 1 (unchanged); `self.core.search.do_search(query, "global")` / `self.core.search.token` (verified live in step 1); `events.invoke_main_thread(callback, *args, **kwargs)` — verified via `pynicotine/events.pyc`: queues `callback` onto an internal deque drained by the GLib main loop, the same mechanism `events.schedule(...)` uses internally, so it is guaranteed to run on the main thread.
- Produces: `self._run_on_main_thread(func, *args, **kwargs) -> Any` — Task 3's `download` handler reuses this exact helper to call `core.downloads.enqueue_download(...)` safely from the socket thread. `self._last_results: list[dict]` — session state with keys `index`, `filename`, `user`, `size`, `format`, `bitrate`, `speed`; Task 3's `download`/`list_results` handlers read this list.

- [ ] **Step 1: Add the main-thread marshaling helper.** In `nicotine_plugin/voice_control/__init__.py`, add `import threading` and `import time` to the top imports (alongside the existing `import os`), then add this method to `Plugin` (near `init`/`disable`):

```python
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
```

Implementer note — **never call this from the main thread itself.** `events.invoke_main_thread` only queues `_call`; it runs the next time the GLib main loop drains that queue. If `_run_on_main_thread` were called from the main thread (e.g. from inside `vcsearch_command`, which is already invoked on the main thread by the chat-command dispatcher), `done.wait()` would block the very thread that's supposed to later drain the queue and set `done` — a permanent deadlock. This is exactly why `vcsearch_command` (Task 2 of the step-1 plan) keeps calling `self.core.search.do_search(...)` directly rather than through this helper: it's already on the main thread. Only code running on `ControlSocketServer`'s background thread — everything added in this task and Task 3 — may call `_run_on_main_thread`.

- [ ] **Step 2: Add ranking constants and helpers.** Near the existing `SEARCH_COLLECTION_SECONDS` / `MAX_LOGGED_RESULTS` constants, add:

```python
MAX_RETURNED_RESULTS = 10
LOSSLESS_FORMATS = frozenset({"flac", "wav", "ape", "wv"})


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
```

Implementer note — **why `_file_format` parses the filename instead of using the `ext` field already destructured in `_file_search_response`'s `for _code, filename, size, _ext, attrs in msg.list` loop:** disassembling `FileSearchResponse._parse_result_list` in `pynicotine/slskmessages.pyc` at tag 3.3.10 shows `ext` is initialized to `None` before the per-file loop and is *never reassigned inside it* — the wire format's extension-length field (`ext_len`) is read and used only to skip past that many bytes when locating the attrs block; the extension string itself is never unpacked. Every result's `ext` is `None` in this Nicotine+ version. This mirrors the `msg.search_username` deviation already documented in the spec and step-1 plan: verify low-level wire-format assumptions against the actual bytecode rather than the field's apparent purpose. `_rank_key` sorts ascending, so lower sort-tuples must mean "better": `0` (has free slot) sorts before `1`; `-_quality_score` puts higher quality (including `float("inf")` for lossless) first; `-speed` puts higher speed first; plain `queue_length` puts shorter queues first — this is the exact "(1) free slot, (2) bitrate/lossless, (3) speed, (4) queue length" order from the spec's Result ranking section.

- [ ] **Step 3: Extend `_file_search_response` to capture ranking fields, and stop sanitizing at collection time.** Replace the existing method body:

```python
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
```

And update `_log_collected_results` to sanitize at the point of logging instead (it's the only remaining place raw peer-controlled strings reach `self.log(...)`):

```python
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
```

Implementer note — **why sanitization moves instead of staying at collection time:** the final-review fix in step 1 added `_sanitize_for_log` (`value.encode("unicode_escape").decode("ascii")`) to stop a malicious peer's username/filename from injecting fake newline-delimited log lines into Nicotine+'s plain-text log via `self.log(f"...")`. That risk is specific to the *plain-text log sink*. `self._collected_results` now also feeds the socket `search` response (this task) and `download`'s `enqueue_download(username, virtual_path, ...)` call (Task 3) — both need the real username and real virtual path, and sanitizing them would either send a mangled filename to the controller or make `enqueue_download` try to download a file path that doesn't exist on the peer. `json.dumps` already escapes control characters (including embedded `\n`) inside JSON string values, so passing raw strings through the socket response doesn't reopen the log-injection hole — only `self.log(...)` call sites need `_sanitize_for_log`, so it now runs at each of those call sites instead of once at collection.

- [ ] **Step 4: Add the socket-triggered search flow.** Add these three methods to `Plugin`:

```python
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
```

Implementer note — **why `time.sleep(SEARCH_COLLECTION_SECONDS)` here but `events.schedule(...)` in `vcsearch_command`:** `_handle_search` runs on `ControlSocketServer`'s dedicated background thread (it's called, eventually, from `_handle_request`, which `_RequestHandler.handle()` invokes on that thread). Blocking that thread with a plain `time.sleep` for the collection window is harmless — it only delays the response to *this* socket connection, and `socketserver.UnixStreamServer` (non-threading, per Task 1) already only serves one connection at a time. `vcsearch_command`, by contrast, runs on the main GTK thread (chat commands are dispatched there); blocking the main thread with `time.sleep` would freeze the whole UI for 5 seconds, which is why it uses `events.schedule(...)` to come back later without blocking anything.

Add `self._last_results = []` to `Plugin.__init__`, alongside `self._active_token = None` / `self._collected_results = []`.

- [ ] **Step 5: Replace the Task 1 echo stub with real dispatch.** Replace:

```python
    def _handle_request(self, request):
        return {"echo": request}
```

with:

```python
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
```

(Task 3 adds the `list_results` and `download` branches to `_dispatch_request` — this task only wires `search`, matching the spec's per-error-case handling of never letting an exception from `core.*` calls escape onto the socket connection or the plugin handler thread.)

- [ ] **Step 6: Verify Python syntax is valid.**

Run: `python3 -c "import ast; ast.parse(open('nicotine_plugin/voice_control/__init__.py').read())"`
Expected: no output, exit code 0.

- [ ] **Step 7: Commit.**

```bash
git add nicotine_plugin/voice_control/__init__.py
git commit -m "Wire voice_control socket search action through GLib main-thread marshaling"
```

- [ ] **Step 8: Quit and relaunch Nicotine+ from a terminal**, same as Task 1 Step 6, and confirm the plugin (re)loads with no traceback. If it was left disabled at the end of Task 1, re-enable it via Preferences → Plugins.

- [ ] **Step 9: Verify a real search round-trips through the socket.** Write this script to `/private/tmp/claude-501/-Users-r3chd-Documents-GitHub-bread-nicotineplus/ffe76fe1-73ee-4102-869d-10d347a74c2d/scratchpad/verify_socket_search.py`:

```python
import json
import os
import socket

socket_path = os.path.expanduser(
    "~/.local/share/nicotine/plugins/voice_control/control.sock"
)

with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
    sock.connect(socket_path)
    sock.sendall(
        json.dumps({"action": "search", "query": "New Order Blue Monday"}).encode("utf-8")
        + b"\n"
    )
    response_line = sock.makefile("r").readline()
    response = json.loads(response_line)
    print(f"got {len(response['results'])} result(s)")
    for result in response["results"][:3]:
        print(result)
```

Run: `python3 /private/tmp/claude-501/-Users-r3chd-Documents-GitHub-bread-nicotineplus/ffe76fe1-73ee-4102-869d-10d347a74c2d/scratchpad/verify_socket_search.py`
Expected: within ~5 seconds (the collection window), prints `got <N> result(s)` where `N` is between 0 and 10, followed by up to 3 result dicts each shaped `{'index': ..., 'filename': ..., 'user': ..., 'size': ..., 'format': ..., 'bitrate': ..., 'speed': ...}` — zero results is a valid outcome if the network returns nothing in time, but the process must not hang or raise. Cross-check the Nicotine+ terminal/Log pane shows the two `vcsearch (socket): ...` lines from `_handle_search`.

- [ ] **Step 10: Nothing further to commit** (verification script lives in the scratchpad directory). Record in the final report the result count observed and that no traceback appeared in the Nicotine+ terminal.

## Task 3: `download` and `list_results` actions

**Files:**
- Modify: `nicotine_plugin/voice_control/__init__.py`

**Interfaces:**
- Consumes: `self._run_on_main_thread` (Task 2); `self._last_results` (Task 2); `core.downloads.enqueue_download(username, virtual_path, folder_path=None, size=0, file_attributes=None, bypass_filter=False)` (verified signature, see Global Constraints).
- Produces: nothing further consumed by later tasks — this is the last task in this plan.

- [ ] **Step 1: Add download-target resolution.** Add this method to `Plugin`:

```python
    def _resolve_download_target(self, request):
        if "index" in request:
            index = request["index"]

            for result in self._last_results:
                if result["index"] == index:
                    return result

            return None

        if "match" in request:
            needle = request["match"].strip().lower()
            matches = [
                result for result in self._last_results
                if needle in result["filename"].lower()
            ]

            if len(matches) != 1:
                return None

            return matches[0]

        return None

    def _download_error_reason(self, request):
        if not self._last_results:
            return "no_active_results"

        if "index" in request:
            return "index_out_of_range"

        if "match" in request:
            return "ambiguous_match"

        return "missing_index_or_match"
```

Implementer note: the spec treats "zero or multiple matches" for `match` as one case ("returns `{"error": "..."}` rather than guessing") without asking for a finer-grained distinction, so `"ambiguous_match"` covers both zero-match and multiple-match outcomes for `match` — this plan doesn't invent a separate `"no_match"` reason the spec doesn't ask for.

- [ ] **Step 2: Add the download handler.** Add this method to `Plugin`:

```python
    def _handle_download(self, request):
        target = self._resolve_download_target(request)

        if target is None:
            return {"error": self._download_error_reason(request)}

        try:
            self._run_on_main_thread(
                self.core.downloads.enqueue_download,
                target["user"],
                target["filename"],
            )
        except Exception as error:
            self.log(f"vcsearch (socket): download failed: {error!r}")
            return {"error": "download_failed"}

        self.log(
            "vcsearch (socket): queued download from {user}: {filename}".format(
                user=_sanitize_for_log(target["user"]),
                filename=_sanitize_for_log(target["filename"]),
            )
        )

        return {"status": "queued", "filename": target["filename"]}
```

Implementer note: `target["filename"]` here is exactly the `filename` field from `msg.list` (a full virtual path like `user\Music\Album\Track.flac`, forward slashes swapped to backslashes by `FileListMessage._parse_result_list` itself before the plugin ever sees it) — this is what `enqueue_download`'s `virtual_path` parameter expects, verified against its call sites' naming in `pynicotine/downloads.pyc`. `folder_path`, `size`, and `file_attributes` are left at their defaults (`None`, `0`, `None`): the spec's protocol table doesn't ask this step to expose a download-destination override, and `enqueue_download` already falls back to `self.get_default_download_folder(username)` when `folder_path` is falsy (verified in the same disassembly) — passing `0`/`None` here doesn't skip any validation, it's simply the documented "use the default" path through that function.

- [ ] **Step 3: Wire `download` and `list_results` into dispatch.** Replace `_dispatch_request`:

```python
    def _dispatch_request(self, request):
        action = request.get("action")

        if action == "search":
            return self._handle_search(request.get("query", ""))

        if action == "list_results":
            return {"results": self._last_results}

        if action == "download":
            return self._handle_download(request)

        return {"error": "unknown_action"}
```

- [ ] **Step 4: Verify Python syntax is valid.**

Run: `python3 -c "import ast; ast.parse(open('nicotine_plugin/voice_control/__init__.py').read())"`
Expected: no output, exit code 0.

- [ ] **Step 5: Commit.**

```bash
git add nicotine_plugin/voice_control/__init__.py
git commit -m "Add download and list_results actions to voice_control control socket"
```

- [ ] **Step 6: Quit and relaunch Nicotine+ from a terminal**, confirm no traceback, plugin enabled (same as prior tasks).

- [ ] **Step 7: Verify `list_results` replays the last search without triggering a new one.** Reuse the same connection pattern as Task 2's script — write `/private/tmp/claude-501/-Users-r3chd-Documents-GitHub-bread-nicotineplus/ffe76fe1-73ee-4102-869d-10d347a74c2d/scratchpad/verify_socket_download.py`:

```python
import json
import os
import socket
import time


def send(sock, request):
    sock.sendall(json.dumps(request).encode("utf-8") + b"\n")
    return json.loads(sock.makefile("r").readline())


socket_path = os.path.expanduser(
    "~/.local/share/nicotine/plugins/voice_control/control.sock"
)

with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
    sock.connect(socket_path)

    search_response = send(sock, {"action": "search", "query": "New Order Blue Monday"})
    print(f"search: {len(search_response['results'])} result(s)")

    list_response = send(sock, {"action": "list_results"})
    assert list_response == search_response, "list_results must replay the same results"
    print("list_results matches search response: OK")

    bad_response = send(sock, {"action": "download", "index": 999999})
    print(f"out-of-range download: {bad_response}")

    if search_response["results"]:
        download_response = send(sock, {"action": "download", "index": 1})
        print(f"download index 1: {download_response}")
```

Run: `python3 /private/tmp/claude-501/-Users-r3chd-Documents-GitHub-bread-nicotineplus/ffe76fe1-73ee-4102-869d-10d347a74c2d/scratchpad/verify_socket_download.py`
Expected: `search: <N> result(s)`, then `list_results matches search response: OK`, then `out-of-range download: {'error': 'index_out_of_range'}`, and — only if `N > 0` — a final line `download index 1: {'status': 'queued', 'filename': '...'}`. Cross-check the Nicotine+ terminal/Log pane shows a `vcsearch (socket): queued download from ...` line, and (if you're logged in with a real download folder configured) that the file appears in Nicotine+'s Downloads tab shortly after.

- [ ] **Step 8: Nothing further to commit** (verification script lives in the scratchpad directory). Record in the final report the exact output observed, especially whether a real download was queued, and confirm this satisfies the design spec's build-order step 2 success criteria ("add the Unix socket server; test with `nc -U` or a raw Python script sending JSON lines").

---

## Self-Review Notes

- **Spec coverage:** build-order step 2 ("Socket layer — add the Unix socket server; test with `nc -U` or a raw Python script sending JSON lines") is fully covered by Tasks 1–3. The spec's full "1. Plugin" component — transport, threading constraint, all three protocol actions, ranking, download resolution, and the `invalid_json` / exception-catching error-handling rules — is covered; only the "2. Controller" component (steps 3–6) is out of scope, per Global Constraints.
- **Placeholder scan:** no TBD/TODO; every code block is complete and runnable as written; every manual verification step states the exact expected output shape (including the "zero results is valid" edge case, which is a real possible outcome of a live network call, not an unhandled gap).
- **Type/signature consistency:** `ControlSocketServer(socket_path, on_request)` from Task 1 is called identically in Task 1 Step 3 (`ControlSocketServer(socket_path, self._handle_request)`) and never re-signatured later. `_run_on_main_thread(func, *args, **kwargs)` from Task 2 Step 1 is called with a bound method + positional args in Task 3 Step 2 (`self._run_on_main_thread(self.core.downloads.enqueue_download, target["user"], target["filename"])`), matching its `*args` forwarding. `self._last_results` entries are built with keys `index, filename, user, size, format, bitrate, speed` in Task 2 Step 4 and read via `result["index"]` / `result["filename"]` in Task 3 Steps 1–2 — consistent throughout. `_dispatch_request` is fully replaced (not appended to) in Task 3 Step 3, carrying forward the exact `search` branch from Task 2 Step 5 unchanged.
- **Sanitization boundary:** confirmed as a deliberate, documented change from step 1's collection-time sanitization to log-time-only sanitization (Task 2 Step 3's implementer note) — this is a correction of scope, not a silent reintroduction of the log-injection risk the step-1 final review fixed, since `self.log(...)` remains the only sink that receives unsanitized-by-default strings, and every remaining `self.log(...)` call site in this plan (`_log_collected_results`, `_handle_download`) explicitly wraps its peer-controlled values in `_sanitize_for_log(...)`.
