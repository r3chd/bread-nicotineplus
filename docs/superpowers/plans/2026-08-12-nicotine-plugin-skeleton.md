# Nicotine+ Plugin Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `voice_control` Nicotine+ plugin skeleton that can trigger a Soulseek search via `core.search.do_search()` and log collected results, verified inside the user's running Nicotine+ 3.3.10 install — no socket server yet (that's step 2 of the design's build order, out of scope here).

**Architecture:** A `BasePlugin` subclass living in this repo at `nicotine_plugin/voice_control/`, symlinked into Nicotine+'s user plugin directory. It registers a `/vcsearch <query>` chat command that triggers a search, hooks the low-level `pynicotine.events` bus for `"file-search-response"` to collect results for a fixed window, then logs a summary.

**Tech Stack:** Python (matching Nicotine+'s own runtime — no extra dependencies for this step). Nicotine+ 3.3.10 plugin API: `pynicotine.pluginsystem.BasePlugin`, `pynicotine.events.events`, `pynicotine.slskmessages.FileAttribute`.

## Global Constraints

- Plugin source lives in this repo at `nicotine_plugin/voice_control/`; the live Nicotine+ plugin directory (`~/.local/share/nicotine/plugins/voice_control`) is a **symlink** to it — always edit the repo copy, never edit through the symlink path.
- Must not touch any other file under `~/.local/share/nicotine/` or `~/.config/nicotine/` (existing user data/config) — confirm with the user before creating the plugins directory itself, since it's outside the repo and outside anything git tracks.
- No socket server, no controller process, no Whisper/Claude integration in this plan — those are design-spec steps 2–6, explicitly out of scope.
- Follow the verified API exactly as documented in `docs/superpowers/specs/2026-08-11-nicotine-voice-control-design.md`: `core.search.do_search(query, "global")`, `events.connect("file-search-response", callback)`, `events.schedule(delay, callback)`. `core.downloads.enqueue_download(...)` is not used in this step (that's step 2+).

---

## File Structure

- Create: `nicotine_plugin/voice_control/PLUGININFO` — plugin metadata (`Name`, `Description`, `Version`, `Authors`), format verified against two bundled Nicotine+ plugins in the 3.3.10 source.
- Create: `nicotine_plugin/voice_control/__init__.py` — the `Plugin(BasePlugin)` class: chat command registration, search trigger, event-bus hook, result collection, logging.
- Create (symlink, **not** tracked by git): `~/.local/share/nicotine/plugins/voice_control` → `<repo>/nicotine_plugin/voice_control`.

## Task 1: Confirm plugin location and scaffold the repo directory + PLUGININFO

**Files:**
- Create: `nicotine_plugin/voice_control/PLUGININFO`

**Interfaces:**
- Produces: the `PLUGININFO` file Nicotine+'s `PluginHandler.get_plugin_info()` reads to populate `human_name` ("Voice Control"), used throughout the plugin (e.g. in every `self.log(...)` line's prefix in the Nicotine+ UI).

- [ ] **Step 1: Confirm with the user before creating anything under `~/.local/share/nicotine/`.**

This path is outside the project repo, inside Nicotine+'s live data folder. State exactly what will be created — the `plugins/` directory (doesn't exist yet) plus a symlink named `voice_control` inside it, pointing back into this repo — and wait for explicit go-ahead before running any command in Task 3 that touches that path. (This step only needs to happen once; Task 3 references it rather than asking again.)

- [ ] **Step 2: Create the repo directory for the plugin source.**

Run: `mkdir -p nicotine_plugin/voice_control`

- [ ] **Step 3: Write `nicotine_plugin/voice_control/PLUGININFO`** with this exact content (format verified against `pynicotine/plugins/plugin_debugger/PLUGININFO` and `pynicotine/plugins/now_playing_search/PLUGININFO` in the Nicotine+ 3.3.10 source — each line is a Python-literal-eval-able assignment; `Authors` is a list):

```
Version = "2026-08-12r00"
Authors = ["r3chd"]
Name = "Voice Control"
Description = "Backend for voice-controlled Soulseek search and download. Step 1: triggers a search via /vcsearch <query> and logs collected results."
```

- [ ] **Step 4: Verify the file was written correctly.**

Run: `cat nicotine_plugin/voice_control/PLUGININFO`
Expected: the exact four lines above, byte-for-byte.

- [ ] **Step 5: Commit.**

```bash
git add nicotine_plugin/voice_control/PLUGININFO
git commit -m "Add PLUGININFO for voice_control Nicotine+ plugin"
```

## Task 2: Implement the plugin skeleton (search trigger + result collection + logging)

**Files:**
- Create: `nicotine_plugin/voice_control/__init__.py`

**Interfaces:**
- Consumes: `pynicotine.pluginsystem.BasePlugin` (`self.core`, `self.log(msg)`, `self.output(text)`, `self.commands`), `pynicotine.events.events` (`connect`, `disconnect`, `schedule`), `pynicotine.slskmessages.FileAttribute` (`BITRATE = 0`).
- Produces: `Plugin` class — the required top-level name Nicotine+'s plugin loader imports (`instance = plugin.Plugin()` in `pluginsystem.py`) — registering the `/vcsearch` command under `self.commands["vcsearch"]`.

- [ ] **Step 1: Write `nicotine_plugin/voice_control/__init__.py`** with this exact content:

```python
from pynicotine.events import events
from pynicotine.pluginsystem import BasePlugin
from pynicotine.slskmessages import FileAttribute

SEARCH_COLLECTION_SECONDS = 5
MAX_LOGGED_RESULTS = 10


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
                "user": msg.search_username,
                "filename": filename,
                "size": size,
                "bitrate": attrs.get(FileAttribute.BITRATE) if attrs else None,
                "speed": msg.ulspeed,
            })

    def _log_collected_results(self):
        count = len(self._collected_results)
        self.log(f"vcsearch: token {self._active_token} collected {count} result(s)")

        for result in self._collected_results[:MAX_LOGGED_RESULTS]:
            self.log(
                "vcsearch:   {user} - {filename} "
                "({size} bytes, bitrate={bitrate}, speed={speed})".format(**result)
            )
```

Notes for the implementer (why this shape, not something else — carry these into code review, don't silently "simplify" them away):

- `core.search.do_search(query, "global")` is fire-and-forget — it does not return the token. Reading `self.core.search.token` immediately afterward works because `do_search` synchronously increments and assigns that token before returning, and this callback runs on Nicotine+'s single GTK main thread, so there's no race with another search being triggered in between.
- The `events.connect("file-search-response", ...)` hook is the only way to observe results for a self-initiated search. `BasePlugin.search_request_notification` fires for *other users'* searches hitting this client, not for results returned to a search *we* started — see the design spec's "Deviation from the assumed API" section for why this lower-level hook is necessary.
- Both `events.connect(...)` callbacks and `events.schedule(...)` callbacks are guaranteed to run on Nicotine+'s main thread: network message events are queued via `events.emit_main_thread()` and drained on the main loop, and scheduled callbacks go through the same `invoke_main_thread` path (verified against `pynicotine/events.py` and `pynicotine/slskproto.py` at tag 3.3.10). No `GLib.idle_add` or other thread-marshaling is needed for this step — that becomes relevant again in step 2, where the plugin's own socket-server thread (which pynicotine does *not* create or marshal for you) will need it.
- `msg.list` entries are `(code, filename, size, ext, attrs)` 5-tuples; `attrs` is a dict keyed by the integer constants on `FileAttribute` (`BITRATE = 0`, etc.), or `None`/empty when the response carries no attributes — verified against `slskmessages.FileListMessage._parse_result_list` and `unpack_file_attributes`.
- `disable()` unregisters the event hook so re-enabling the plugin later (via Preferences → Plugins) doesn't register a second, duplicate callback.

- [ ] **Step 2: Verify Python syntax is valid before touching the live Nicotine+ install.**

Run: `python3 -c "import ast; ast.parse(open('nicotine_plugin/voice_control/__init__.py').read())"`
Expected: no output, exit code 0.

- [ ] **Step 3: Commit.**

```bash
git add nicotine_plugin/voice_control/__init__.py
git commit -m "Implement voice_control plugin skeleton: search trigger + result logging"
```

## Task 3: Symlink into Nicotine+'s plugin directory and verify it loads

**Files:**
- Create (outside repo, not tracked by git): `~/.local/share/nicotine/plugins/voice_control` (symlink)

**Interfaces:** none — this task wires the already-implemented plugin into the live app and verifies it end to end.

- [ ] **Step 1: Create the Nicotine+ user plugins directory and the symlink.** This is the action confirmed with the user in Task 1, Step 1 — do not run this before that confirmation happened.

```bash
mkdir -p ~/.local/share/nicotine/plugins
ln -s "$(pwd)/nicotine_plugin/voice_control" ~/.local/share/nicotine/plugins/voice_control
```

- [ ] **Step 2: Verify the symlink resolves to the repo files.**

Run: `ls -la ~/.local/share/nicotine/plugins/voice_control/`
Expected: `PLUGININFO` and `__init__.py` listed (via the symlink).

- [ ] **Step 3: Quit Nicotine+ if it's currently running** (Nicotine+ menu → Quit, or Cmd+Q), then relaunch it **from a terminal** so plugin load errors and `self.log()` output are visible directly, rather than only in the app's internal Log pane:

```bash
/Applications/Nicotine+.app/Contents/MacOS/Nicotine+ &
```

Expected: the app window opens normally, and the terminal shows no traceback mentioning `voice_control`. (`PluginHandler.enable_plugin` catches import/init exceptions and logs `"Unable to load plugin %(module)s\n%(exc_trace)s"` rather than crashing the app — if the plugin fails to load, this is where it will show up.)

- [ ] **Step 4: Enable the plugin in the UI.** Nicotine+ menu → Preferences → Plugins → find "Voice Control" in the list → check the enable box. Confirm no error appears in the terminal or in Preferences.

- [ ] **Step 5: Trigger the search command.** Open any chat surface that accepts `/commands` — a chat room (Rooms tab, join or create any room) or a private chat window — and type:

```
/vcsearch New Order Blue Monday
```

(Requires being logged into the Soulseek server — Nicotine+ connects automatically on launch if credentials are already configured; if not connected, `do_search` still runs without error but no results will arrive.)

- [ ] **Step 6: Verify the result.** Within a few seconds of typing the command, the terminal running Nicotine+ should print two kinds of lines (exact counts vary with live network results, but the shape is fixed):

```
vcsearch: searching for 'New Order Blue Monday' (token <some integer>)
vcsearch: token <same integer> collected <N> result(s)
vcsearch:   <username> - <filename> (<size> bytes, bitrate=<int or None>, speed=<int>)
```

— zero or more of the last line, up to 10, depending on what the Soulseek network actually returns for that query at test time. Confirm in the Nicotine+ Log pane (usually visible at the bottom of the main window; toggle via the View menu if hidden) that the same lines appear there too, prefixed with the plugin's human name ("Voice Control").

- [ ] **Step 7: Nothing to commit in this task** (the symlink is outside git, and no repo files changed here). Record in the final report to the user that the plugin loads and the search-trigger-plus-logging round trip works end to end — this satisfies the design spec's step 1 success criteria.

---

## Self-Review Notes

- **Spec coverage**: build-order step 1 ("load in Nicotine+, trigger a search, log results — no socket yet") is fully covered by Tasks 1–3. Steps 2–6 of the spec's build order are explicitly excluded via Global Constraints and untouched by any task here.
- **Placeholder scan**: no TBD/TODO; every code block is complete and runnable as written; every manual verification step states the exact expected output shape rather than "check that it works."
- **Type/signature consistency**: `vcsearch_command(self, args, **_unused)` matches the calling convention documented in `pynicotine/pluginsystem.py` `PluginHandler._trigger_command` (`callback(args)` / `callback(args, user=user)` / `callback(args, room=room)`); `_file_search_response(self, msg)` matches the single-positional-arg convention used by every other `events.connect("file-search-response", ...)` registration found in the Nicotine+ 3.3.10 source (`pynicotine/search.py`, `pynicotine/gtkgui/search.py`).
