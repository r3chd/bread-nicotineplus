# Nicotine+ Voice Control (v1) — Design

## Goal

A voice-controlled interface for Nicotine+ (the Soulseek client): "search for Blue Monday by New Order," then "download the second one." Scoped to Nicotine+ only — no wake word, no OS control, no vision fallback.

## Environment (verified 2026-08-11)

- Nicotine+ **3.3.10**, installed as a macOS app bundle at `/Applications/Nicotine+.app`.
- The installed app ships only compiled `.pyc` files under `Contents/Resources/lib/pynicotine` — no `.py` source. The plugin API below was verified against the actual `nicotine-plus/nicotine-plus` GitHub source at tag `3.3.10` (matching the installed version exactly), not against training-data assumptions.
- No existing user plugins: `~/.config/nicotine/plugins` does not exist yet. Nothing to overwrite.
- User's Nicotine+ data dir: `~/.config/nicotine/`.

## Verified plugin API (source-grounded)

- `pynicotine.pluginsystem.BasePlugin` — `__init__()`, `init()` (settings loaded), `commands` dict, `self.core` (reference to `pynicotine.core.core`), `self.log(msg)`, `self.output(text)`. Matches the shape assumed in the original prompt.
- **Triggering a search**: `core.search.do_search(query, "global")` — fire-and-forget; results arrive asynchronously over the network, not as a return value.
- **Enqueuing a download**: `core.downloads.enqueue_download(username, virtual_path, folder_path=None, size=0, file_attributes=None, bypass_filter=False)`.
- **Per-file result shape** (from `slskmessages.FileListMessage._parse_result_list`): `(code, name, size, ext, attrs)` where `attrs` is a dict keyed by `FileAttribute` (`BITRATE`, `DURATION`, `VBR`, `SAMPLE_RATE`, `BIT_DEPTH`). Per-response fields (from `FileSearchResponse`): `search_username`, `token`, `list` (the file entries), `freeulslots`, `ulspeed`, `inqueue`.

### Deviation from the assumed API — flagged per instructions

`BasePlugin.search_request_notification` / `distrib_search_notification` exist, but they fire when **other users'** searches hit your client (for auto-responder-style plugins) — not when results come back for **your own** search. There is no official `BasePlugin` hook for "a result arrived for my query."

The only way to capture results for a search you triggered is to hook the lower-level internal event bus directly:

```python
from pynicotine.events import events
events.connect("file-search-response", callback)
```

This is the same mechanism the GTK UI itself uses internally to populate its search results view. It is not part of the documented `BasePlugin` notification surface, so it is more likely to shift across Nicotine+ releases than the official API. **Decision: use it anyway** — it's the only viable path to real-time results for a self-initiated search, and it's already how the reference GTK client does it. (Confirmed with the user.)

## Architecture

Two long-lived local processes, connected over a Unix domain socket:

```
Nicotine+ (GTK app)                          Voice Controller (separate process)
┌──────────────────────────┐                 ┌─────────────────────────────────┐
│ voice_control plugin      │                 │ Whisper (STT) -> Claude (tools)  │
│ - hooks core.search       │   Unix socket   │  -> socket client                │
│ - hooks events bus for    │◄───────────────►│ Push-to-talk (Enter key)         │
│   "file-search-response"  │  JSON lines     │ Session state: last results      │
│ - calls core.downloads    │                 │                                  │
│ - control.sock server     │                 │                                  │
└──────────────────────────┘                 └─────────────────────────────────┘
```

The plugin runs **inside** Nicotine+'s process and is the only thing that talks to `core.search` / `core.downloads`. The controller is a fully separate process with no Nicotine+ imports — it only ever speaks JSON over the socket. A crash or bug in the voice/LLM stack cannot touch the running Soulseek client.

**Transport**: Unix domain socket at `~/.config/nicotine/plugins/voice_control/control.sock`. Chosen over TCP-on-localhost: no port to collide with, filesystem permissions restrict access to the local user, simple to clean up on plugin unload.

## Components

### 1. Plugin

Path: `~/.config/nicotine/plugins/voice_control/__init__.py` + `PLUGININFO` (standard Nicotine+ user-plugin layout).

A `BasePlugin` subclass. On `init()`:
- Starts a background thread running a Unix domain socket server at `control.sock`, accepting line-delimited JSON.
- Hooks `events.connect("file-search-response", ...)` to collect incoming results per search token into an in-memory buffer, keyed by token, for a fixed collection window (default 5s, configurable via plugin settings).
- Calls `core.downloads.enqueue_download(...)` directly to start downloads.

**Threading constraint**: all socket I/O runs on a background thread; all `core.*` calls and `events` interactions are marshaled back onto Nicotine+'s GLib main loop (e.g. via `GLib.idle_add`) — GTK/pynicotine internals are not safe to call directly from a non-main thread.

**Socket protocol** — one JSON object per line, in and out:

| Request | Response |
|---|---|
| `{"action": "search", "query": "<text>"}` | `{"results": [{"index": 1, "filename": ..., "user": ..., "size": ..., "format": ..., "bitrate": ..., "speed": ...}, ...]}` |
| `{"action": "download", "index": <n>}` | `{"status": "queued", "filename": ...}` or `{"error": ...}` |
| `{"action": "download", "match": "<text>"}` | same as above |
| `{"action": "list_results"}` | same shape as `search` response, replayed from last search |

**Result ranking** (composite score, highest first): (1) has a free upload slot, (2) higher bitrate / lossless format, (3) higher user upload speed, (4) shorter queue length. Top 10 returned by default.

**Download resolution**: `index` is 1-based into the last returned result list. `match` is a case-insensitive substring match against filenames in the last result list; zero or multiple matches returns `{"error": "..."}` rather than guessing.

### 2. Controller

Separate Python process, own `venv` / `requirements.txt`, no Nicotine+ imports.

- **Text-mode (step 3)**: `python controller.py "search Blue Monday New Order"` — sends the command straight to the plugin socket, prints results. No LLM, no audio.
- **Push-to-talk (step 4)**: press Enter to start recording, Enter again to stop. Chosen over a true held-hotkey (e.g. via `pynput`) because it needs no macOS Accessibility/Input Monitoring permission grant and works in any terminal — simpler to build and debug, and "hold vs. press-again" is a UX detail that can change later without touching the rest of the pipeline.
- **Speech-to-text**: `faster-whisper`, small/base model, CPU.
- **Tool-calling (step 5)**: transcript + last-results-as-context sent to **Claude Haiku 4.5** (`claude-haiku-4-5`) with three tool definitions — `search(query)`, `download(selector)`, `list_results()`. Haiku 4.5 chosen over Sonnet 5 for latency and cost: this is a simple 3-tool selection task over a short context (a handful of recent results), well within Haiku's capability, and every millisecond of round-trip latency is felt in a voice UX. Fall back to Sonnet 5 only if testing shows Haiku's resolution of ambiguous phrasing ("the one that sounds like...") isn't good enough.
- **Session state**: the controller keeps its own copy of "last results" (mirroring the plugin's) so it can describe options back to Claude/the user without an extra round trip; the plugin remains the source of truth for resolving `download` calls.

## Data flow

1. User speaks (or types) a command.
2. Controller sends transcript + tool defs + last-results-as-context to Claude → Claude emits one tool call.
3. Controller sends the corresponding JSON command over the Unix socket to the plugin (one line), reads one line back.
4. `search`: plugin calls `core.search.do_search(query, "global")`, buffers `file-search-response` events for the collection window, ranks and returns the top N, stores the list as "last results".
5. `download`: plugin resolves `index` or `match` against "last results", calls `core.downloads.enqueue_download(...)`.
6. Controller reports the result back (printed for v1; text-to-speech is out of scope) and updates its own session-state mirror.

## Error handling

- **Socket unavailable** (plugin not loaded / Nicotine+ not running): controller reports a clear connection error, no silent retry.
- **Search timeout**: zero results within the collection window returns an empty list, not a hang.
- **Ambiguous `match`**: zero or multiple matches returns `{"error": "..."}`; controller surfaces this to Claude so it can ask a follow-up rather than guessing.
- **Malformed JSON on the socket**: plugin responds `{"error": "invalid_json"}` per line rather than dropping the connection.
- **Nicotine+-side exceptions** (e.g. `enqueue_download` raising): caught in the plugin, logged via `self.log(...)`, returned to the controller as `{"error": ...}` — never allowed to propagate into Nicotine+'s own event loop, where it could affect other plugins.

## Build order (each step independently testable before the next)

1. **Plugin skeleton** — load in Nicotine+, trigger a search, log results to the Nicotine+ log window. No socket yet.
2. **Socket layer** — add the Unix socket server; test with `nc -U` or a raw Python script sending JSON lines.
3. **Controller skeleton (text input)** — `python controller.py "search ..."` round-trips through the socket.
4. **Speech-to-text** — `faster-whisper` on a recorded clip, push-to-talk via Enter key, feeding into the same path as step 3.
5. **LLM tool-calling layer** — Claude Haiku 4.5 with the three tool definitions, tested on sample transcripts (including ambiguous ones) in isolation before wiring to the socket.
6. **Wire it end to end** — push-to-talk → Whisper → Claude tool call → socket → Nicotine+ plugin executes → result printed back.

Out of scope for v1: wake word detection, full OS control, vision-based fallback, text-to-speech output.

## Constraints (unchanged from the original brief)

- Python for both plugin and controller.
- Use Nicotine+'s own internal functions/events, not UI scraping or click simulation.
- Ask before modifying the user's existing Nicotine+ config/plugins directory (moot for now — it doesn't exist yet, so creating `voice_control/` under it is additive, not destructive; still confirm before any future change that touches existing user data).
