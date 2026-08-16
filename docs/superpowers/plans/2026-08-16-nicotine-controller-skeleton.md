# Nicotine+ Controller Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone `controller.py` process that sends one `search` / `download` / `list_results` action to the `voice_control` plugin's Unix socket via a CLI, prints the JSON response, and reports a clear error if the socket isn't there — proving the design's socket protocol works end-to-end from outside Nicotine+, with no LLM or audio involved yet.

**Architecture:** A single-file CLI script that argparses one of three subcommands (`search QUERY`, `download --index N|--match TEXT`, `list-results`), builds the matching request dict from the spec's protocol table, connects to the Unix socket, writes one JSON line, reads one JSON line back, and prints it. All socket I/O is wrapped so that a missing/refused socket produces one clear stderr message and exit code 1, never a raw traceback. No session-state mirror, no interactive loop, no LLM/tool-calling — those are steps 4-6 per the design spec's build order and explicitly out of scope here.

**Tech Stack:** Python stdlib only (`argparse`, `socket`, `json`, `sys`, `os`) — matching Global Constraint "Python for both plugin and controller" and the spec's "own venv/requirements.txt, no Nicotine+ imports" for the controller component. `requirements.txt` is created but stays empty (stdlib-only) for this step; step 5 (LLM tool-calling) is what will first add entries to it.

**Spec:** `docs/superpowers/specs/2026-08-11-nicotine-voice-control-design.md` — this plan implements the spec's "2. Controller" component's "Text-mode (step 3)" bullet and the "Socket unavailable" line of its Error handling section. Linear issue: D3V-34.

**Prior work:** Step 2 (`docs/superpowers/plans/2026-08-13-nicotine-socket-layer.md`, merged to `main` at `d67ca22`) delivered the plugin-side socket server at `nicotine_plugin/voice_control/control_socket.py` + `__init__.py`, bound to `~/.local/share/nicotine/plugins/voice_control/control.sock`, already serving `search`/`download`/`list_results` exactly per the protocol table below. This plan only adds a client for that existing server — it does not modify any file under `nicotine_plugin/`.

## Global Constraints

- Controller source lives in this repo at `controller/` (new top-level directory, sibling to `nicotine_plugin/`) — a separate Python process, own `venv`/`requirements.txt`, **no Nicotine+ imports** (spec, "2. Controller").
- **Transport (from spec, unchanged from step 2):** Unix domain socket at `~/.local/share/nicotine/plugins/voice_control/control.sock`, one JSON object per line, in and out.
- **Socket protocol (from spec, verbatim):**

  | Request | Response |
  | --- | --- |
  | `{"action": "search", "query": "<text>"}` | `{"results": [{"index": 1, "filename": ..., "user": ..., "size": ..., "format": ..., "bitrate": ..., "speed": ...}, ...]}` |
  | `{"action": "download", "index": <n>}` | `{"status": "queued", "filename": ...}` or `{"error": ...}` |
  | `{"action": "download", "match": "<text>"}` | same as above |
  | `{"action": "list_results"}` | same shape as `search` response, replayed from last search |

- **Error handling (from spec, this step's scope):** "Socket unavailable (plugin not loaded / Nicotine+ not running): controller reports a clear connection error, no silent retry." No other error case (malformed JSON, ambiguous match, etc.) is the controller's job to invent — those come back as `{"error": "..."}` JSON from the plugin, which the controller just prints as-is.
- No LLM, no audio, no push-to-talk, no session-state mirror — those are design-spec steps 4-6, explicitly out of scope for this plan.
- `python controller.py "search Blue Monday New Order"`-shaped invocation is the spec's literal example; this plan implements it as `python controller.py search "Blue Monday New Order"` (subcommand + argument) rather than parsing a single free-text string, since the plugin already expects one action with structured fields (`query`, or `index`/`match`) and inventing a natural-language parser here would prematurely duplicate step 5's job.

---

## File Structure

- Create: `controller/controller.py` — CLI entry point: argument parsing, socket connect/send/receive, error reporting, `main()`.
- Create: `controller/requirements.txt` — empty (stdlib-only for this step; a comment line documents why).
- Create: `controller/README.md` — one-paragraph usage note (`python controller/controller.py search "..."`, etc.) so the CLI shape doesn't have to be reverse-engineered from `argparse` output later.
- Create: `controller/tests/test_controller.py` — unit tests for request-building and error formatting, using a fake/mock socket so tests don't require a running Nicotine+.

## Task 1: Request builders + socket transport, with unit tests

**Files:**
- Create: `controller/controller.py`
- Create: `controller/tests/test_controller.py`
- Create: `controller/requirements.txt`

**Interfaces:**
- Produces: `build_request(args: argparse.Namespace) -> dict` — pure function, no I/O, used directly by tests. `send_request(request: dict, socket_path: str) -> dict` — opens a `socket.AF_UNIX` connection, writes `json.dumps(request) + "\n"`, reads one line, returns the parsed dict; raises `ConnectionError` (stdlib) if the socket file doesn't exist or the connection is refused. `DEFAULT_SOCKET_PATH: str` — module-level constant, `~/.local/share/nicotine/plugins/voice_control/control.sock`, expanded via `os.path.expanduser`.
- Consumes: nothing from other tasks (this is the only task).

- [ ] **Step 1: Write the failing tests.** Create `controller/tests/test_controller.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail.**

Run: `cd controller && python3 -m unittest tests.test_controller -v`
Expected: `ModuleNotFoundError: No module named 'controller'` (or `ImportError: cannot import name 'build_request'`) — `controller.py` doesn't exist yet.

- [ ] **Step 3: Write `controller/controller.py`.**

```python
import argparse
import json
import os
import socket
import sys


DEFAULT_SOCKET_PATH = os.path.expanduser(
    "~/.local/share/nicotine/plugins/voice_control/control.sock"
)


def build_request(args):
    if args.action == "search":
        return {"action": "search", "query": args.query}

    if args.action == "download":
        if args.index is not None:
            return {"action": "download", "index": args.index}

        return {"action": "download", "match": args.match}

    if args.action == "list_results":
        return {"action": "list_results"}

    raise ValueError(f"unknown action: {args.action!r}")


def send_request(request, socket_path):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(socket_path)
            sock.sendall(json.dumps(request).encode("utf-8") + b"\n")
            response_line = sock.makefile("r").readline()
    except OSError as error:
        raise ConnectionError(
            f"could not reach voice_control socket at {socket_path!r} "
            f"(is Nicotine+ running with the plugin enabled?): {error}"
        ) from error

    if not response_line:
        raise ConnectionError(
            f"voice_control socket at {socket_path!r} closed the connection "
            "without sending a response"
        )

    return json.loads(response_line)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="controller.py",
        description="Text-mode client for the Nicotine+ voice_control plugin socket.",
    )
    parser.add_argument(
        "--socket-path",
        default=DEFAULT_SOCKET_PATH,
        help=f"path to control.sock (default: {DEFAULT_SOCKET_PATH})",
    )

    subparsers = parser.add_subparsers(dest="action", required=True)

    search_parser = subparsers.add_parser("search", help="trigger a search")
    search_parser.add_argument("query", help="search query text")

    download_parser = subparsers.add_parser(
        "download", help="queue a download from the last search results"
    )
    target_group = download_parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument(
        "--index", type=int, default=None, help="1-based index into last results"
    )
    target_group.add_argument(
        "--match", default=None, help="substring match against last results' filenames"
    )

    subparsers.add_parser("list_results", help="replay the last search results")

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    request = build_request(args)

    try:
        response = send_request(request, args.socket_path)
    except ConnectionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(json.dumps(response, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass.**

Run: `cd controller && python3 -m unittest tests.test_controller -v`
Expected: 5 tests, all `ok`.

- [ ] **Step 5: Create `controller/requirements.txt`.**

```
# Stdlib-only for the text-mode controller skeleton (build-order step 3).
# faster-whisper / anthropic will be added here in step 4/5.
```

- [ ] **Step 6: Create `controller/README.md`.**

```markdown
# voice_control Controller (text-mode skeleton)

Standalone CLI that sends one command to the running `voice_control` Nicotine+
plugin over its Unix socket and prints the JSON response. No Nicotine+
imports — this can run from any Python 3 interpreter as long as Nicotine+ is
running with the plugin enabled.

## Usage

    python3 controller/controller.py search "Blue Monday New Order"
    python3 controller/controller.py list_results
    python3 controller/controller.py download --index 1
    python3 controller/controller.py download --match "blue monday"

Custom socket path (default is
`~/.local/share/nicotine/plugins/voice_control/control.sock`):

    python3 controller/controller.py --socket-path /path/to/control.sock list_results

## Tests

    cd controller && python3 -m unittest tests.test_controller -v
```

- [ ] **Step 7: Verify Python syntax is valid for the new module.**

Run: `python3 -c "import ast; ast.parse(open('controller/controller.py').read())"`
Expected: no output, exit code 0.

- [ ] **Step 8: Commit.**

```bash
git add controller/controller.py controller/tests/test_controller.py controller/requirements.txt controller/README.md
git commit -m "Add text-mode controller skeleton for voice_control socket"
```

- [ ] **Step 9: Verify against the live plugin.** Confirm Nicotine+ is running with the Voice Control plugin enabled (it should already be, from step 2's verification), then run:

```bash
python3 controller/controller.py list_results
```

Expected: prints JSON (likely `{"results": []}` if no search has run yet this session — that's a valid response, not an error). Then run:

```bash
python3 controller/controller.py search "Blue Monday New Order"
```

Expected: within ~5 seconds, prints a JSON object with a `results` list (0-10 entries, each shaped `{"index": ..., "filename": ..., "user": ..., "size": ..., "format": ..., "bitrate": ..., "speed": ...}`) — matches the raw-socket verification already done in step 2, now going through the CLI instead of a scratch script.

- [ ] **Step 10: Verify the "socket unavailable" error path.** Temporarily disable the Voice Control plugin (Preferences → Plugins → uncheck "Voice Control") or quit Nicotine+, then run:

```bash
python3 controller/controller.py list_results
```

Expected: stderr line starting `error: could not reach voice_control socket at ...`, exit code 1 (check with `echo $?`), no Python traceback. Re-enable the plugin / relaunch Nicotine+ afterward so the repo is left in the same working state it started in.

- [ ] **Step 11: Nothing further to commit** (verification in Steps 9-10 only exercises the CLI already committed in Step 8). Record in the final report the exact output observed for the search round-trip and the disconnected-socket error message.

---

## Self-Review Notes

- **Spec coverage:** the spec's "2. Controller" bullet "Text-mode (step 3): `python controller.py "search ..."` — sends the command straight to the plugin socket, prints results. No LLM, no audio." is fully covered — `search`, `download` (both `index` and `match` forms), and `list_results` are all reachable from the CLI, matching every row of the socket protocol table. The Error handling section's "Socket unavailable... controller reports a clear connection error, no silent retry" is covered by `send_request`'s `except OSError` → `ConnectionError` translation (no retry loop anywhere) and verified live in Step 10. Steps 4-6 (STT, LLM tool-calling, end-to-end wiring) are explicitly out of scope, matching the spec's own build-order sequencing.
- **Placeholder scan:** no TBD/TODO; every code block is complete and runnable as written; both success and failure paths have concrete expected output in the verification steps.
- **Type/signature consistency:** `build_request(args)` is defined once in Task 1 Step 3 and exercised by the four `TestBuildRequest` cases in Step 1 with matching `Namespace` shapes (`action`, plus `query` or `index`/`match`). `send_request(request, socket_path)` is defined once and called both by `main()` (Step 3) and directly by `TestSendRequest` (Step 1) with the same two-positional-argument shape. `DEFAULT_SOCKET_PATH` is a module-level constant used as the `argparse` default and nowhere else redefined.
- **No Nicotine+ imports:** confirmed — `controller/controller.py`'s imports are `argparse, json, os, socket, sys` only, matching Global Constraint and the spec's "no Nicotine+ imports" requirement for the controller component. Nothing in this plan touches `nicotine_plugin/`.
