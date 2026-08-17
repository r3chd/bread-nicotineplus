# Nicotine+ LLM Tool-Calling Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `controller/tool_calling.py`, a standalone module that resolves one freeform command transcript (plus the last search results as context) into exactly one of the three plugin actions (`search`, `download`, `list_results`) by calling Claude Haiku 4.5 with tool definitions — tested in isolation on sample transcripts, with no dependency on step 4's audio code and no wiring into the socket yet.

**Architecture:** Three Anthropic-API tool schemas mirroring the plugin's existing socket protocol exactly, a pure `translate_tool_call()` function that maps a Claude `tool_use` block into the socket's request-dict shape (no I/O, fully unit-testable), and a `resolve_transcript()` function that builds the system/user prompt, calls `client.messages.create()` with `tool_choice` forced to `"any"` (Claude must always emit a tool call, never plain text), and returns the translated result. `client` is injectable so tests never make a real network call.

**Tech Stack:** `anthropic` (the official Python SDK) — this controller's first LLM dependency, anticipated by the `requirements.txt` comment left in step 3. Model: `claude-haiku-4-5` (verified current model ID).

**Spec:** `docs/superpowers/specs/2026-08-11-nicotine-voice-control-design.md` — this plan implements the spec's "2. Controller" component's "Tool-calling (step 5)" bullet: "transcript + last-results-as-context sent to Claude Haiku 4.5 (`claude-haiku-4-5`) with three tool definitions — `search(query)`, `download(selector)`, `list_results()`", and the build order's step 5: "tested on sample transcripts (including ambiguous ones) in isolation before wiring to the socket." Linear issue: D3V-36.

**Prior work:** Step 3 (`docs/superpowers/plans/2026-08-16-nicotine-controller-skeleton.md`, merged to `main` at `fb2cf01`) delivered `controller/controller.py`'s socket protocol client and the plugin-side protocol itself (`nicotine_plugin/voice_control/__init__.py`, from step 2), which this plan's tool schemas and `translate_tool_call()` must match exactly. This plan is independent of step 4 (speech-to-text) — it consumes a plain transcript string, not audio, and does not modify `controller.py`. Wiring `tool_calling.py` into the actual push-to-talk loop is step 6's job ("wire it end to end"), explicitly out of scope here.

## Global Constraints

- **Verified plugin protocol (from `nicotine_plugin/voice_control/__init__.py`'s `_dispatch_request`), verbatim:**
  - `search`: `{"action": "search", "query": "<text>"}`
  - `download` by position: `{"action": "download", "index": <n>}` (1-based)
  - `download` by substring: `{"action": "download", "match": "<text>"}` (case-insensitive substring against filenames; if both given, `index` takes precedence per `_resolve_download_target`)
  - `list_results`: `{"action": "list_results"}`
- **Verified Anthropic API shapes (checked against current docs via Context7, not memory):**
  - Tool definition: `{"name": ..., "description": ..., "input_schema": {"type": "object", "properties": {...}, "required": [...]}}`.
  - `client.messages.create(model=..., max_tokens=..., system=..., tools=[...], tool_choice={"type": "any"}, messages=[...])` — `tool_choice: {"type": "any"}` forces Claude to call some tool rather than reply with plain text.
  - Response `content` is a list of content blocks; a tool-call block has `.type == "tool_use"`, `.name` (the tool name), and `.input` (a parsed dict matching `input_schema`).
- **Model:** `claude-haiku-4-5` — the exact current model ID (do not append a date suffix).
- **`tool_choice` is forced to `"any"`, not `"auto"`:** the spec's "ask a follow-up rather than guess" behavior for ambiguous matches is a multi-turn flow (feeding the plugin's `ambiguous_match` error back to Claude) — that belongs to step 6 ("wire it end to end"), not this isolated, single-shot step. Forcing a tool call here means Claude always resolves to a concrete action; the plugin's own `ambiguous_match`/`index_out_of_range` rejection remains the safety net until step 6 adds a retry loop.
- No socket I/O, no audio, no argparse/CLI wiring, no `controller.py` changes — those are out of scope for this plan (step 6's job, or already done in steps 3/4).

---

## File Structure

- Create: `controller/tool_calling.py` — tool schemas, `translate_tool_call()`, `resolve_transcript()`.
- Create: `controller/tests/test_tool_calling.py` — unit tests for both functions, using a mock Anthropic client (no real API calls).
- Modify: `controller/requirements.txt` — add `anthropic`.
- Modify: `controller/README.md` — document the new module and the manual sample-transcript verification step.

## Task 1: Tool-calling layer (schemas, translation, resolution), with unit tests

**Files:**
- Create: `controller/tool_calling.py`
- Create: `controller/tests/test_tool_calling.py`
- Modify: `controller/requirements.txt`

**Interfaces:**
- Consumes: nothing from other tasks (first and only task in this plan).
- Produces:
  - `DEFAULT_MODEL: str = "claude-haiku-4-5"` — module constant.
  - `TOOLS: list[dict]` — module constant, the three tool definitions (`search`, `download`, `list_results`), in that order.
  - `translate_tool_call(name: str, tool_input: dict) -> dict` — pure function, no I/O. Raises `ValueError` for `name == "download"` with neither `"index"` nor `"match"` in `tool_input`, and for any unrecognized `name`.
  - `resolve_transcript(transcript: str, last_results: list, client=None) -> dict` — if `client` is `None`, constructs `anthropic.Anthropic()`; calls `client.messages.create(...)` once with `tool_choice={"type": "any"}`; returns `translate_tool_call(...)` applied to the first `tool_use` block in the response; raises `RuntimeError` if no `tool_use` block is present in the response.

- [ ] **Step 1: Add `anthropic` to `requirements.txt`.**

Replace the contents of `controller/requirements.txt`:

```
anthropic
```

- [ ] **Step 2: Install the dependency into the controller's venv.**

Run:
```bash
cd controller && python3 -m venv .venv 2>/dev/null; source .venv/bin/activate && pip install -r requirements.txt
```
Expected: `anthropic` (and its transitive deps, e.g. `httpx`, `pydantic`) install without error. No network call to the Anthropic API happens here or anywhere in the unit tests below — only package installation.

- [ ] **Step 3: Write the failing tests.** Create `controller/tests/test_tool_calling.py`:

```python
import json
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tool_calling import DEFAULT_MODEL, resolve_transcript, translate_tool_call


class TestTranslateToolCall(unittest.TestCase):

    def test_search(self):
        result = translate_tool_call("search", {"query": "Blue Monday New Order"})
        self.assertEqual(result, {"action": "search", "query": "Blue Monday New Order"})

    def test_download_by_index(self):
        result = translate_tool_call("download", {"index": 2})
        self.assertEqual(result, {"action": "download", "index": 2})

    def test_download_by_match(self):
        result = translate_tool_call("download", {"match": "blue monday"})
        self.assertEqual(result, {"action": "download", "match": "blue monday"})

    def test_download_index_takes_precedence_over_match(self):
        result = translate_tool_call("download", {"index": 3, "match": "blue monday"})
        self.assertEqual(result, {"action": "download", "index": 3})

    def test_download_missing_both_raises(self):
        with self.assertRaises(ValueError):
            translate_tool_call("download", {})

    def test_list_results(self):
        result = translate_tool_call("list_results", {})
        self.assertEqual(result, {"action": "list_results"})

    def test_unknown_tool_raises(self):
        with self.assertRaises(ValueError):
            translate_tool_call("delete_everything", {})


def _make_tool_use_block(name, tool_input):
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.input = tool_input
    return block


def _make_text_block(text):
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


class TestResolveTranscript(unittest.TestCase):

    def _mock_client(self, response_content):
        client = MagicMock()
        response = MagicMock()
        response.content = response_content
        client.messages.create.return_value = response
        return client

    def test_builds_request_with_model_tools_and_forced_tool_choice(self):
        client = self._mock_client(
            [_make_tool_use_block("list_results", {})]
        )

        resolve_transcript("what were the results", [], client=client)

        _, kwargs = client.messages.create.call_args
        self.assertEqual(kwargs["model"], DEFAULT_MODEL)
        self.assertEqual(kwargs["tool_choice"], {"type": "any"})
        tool_names = {tool["name"] for tool in kwargs["tools"]}
        self.assertEqual(tool_names, {"search", "download", "list_results"})

    def test_transcript_and_last_results_appear_in_message_content(self):
        client = self._mock_client(
            [_make_tool_use_block("search", {"query": "Blue Monday"})]
        )
        last_results = [
            {"index": 1, "filename": "New Order - Blue Monday.flac", "user": "someuser"}
        ]

        resolve_transcript("search for blue monday", last_results, client=client)

        _, kwargs = client.messages.create.call_args
        message_content = kwargs["messages"][0]["content"]
        self.assertIn("search for blue monday", message_content)
        self.assertIn("Blue Monday.flac", message_content)

    def test_empty_last_results_shown_as_no_results(self):
        client = self._mock_client(
            [_make_tool_use_block("search", {"query": "test"})]
        )

        resolve_transcript("search for test", [], client=client)

        _, kwargs = client.messages.create.call_args
        message_content = kwargs["messages"][0]["content"]
        self.assertIn("No search results", message_content)

    def test_returns_translated_search_call(self):
        client = self._mock_client(
            [_make_tool_use_block("search", {"query": "Blue Monday New Order"})]
        )

        result = resolve_transcript("search for blue monday by new order", [], client=client)

        self.assertEqual(
            result, {"action": "search", "query": "Blue Monday New Order"}
        )

    def test_returns_translated_download_by_match(self):
        client = self._mock_client(
            [_make_tool_use_block("download", {"match": "blue monday"})]
        )

        result = resolve_transcript(
            "download the one that sounds like blue monday",
            [{"index": 1, "filename": "New Order - Blue Monday.flac"}],
            client=client,
        )

        self.assertEqual(result, {"action": "download", "match": "blue monday"})

    def test_no_tool_use_block_raises(self):
        client = self._mock_client([_make_text_block("I'm not sure what you mean.")])

        with self.assertRaises(RuntimeError):
            resolve_transcript("uh what", [], client=client)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 4: Run tests to verify they fail.**

Run: `cd controller && python3 -m unittest tests.test_tool_calling -v`
Expected: `ModuleNotFoundError: No module named 'tool_calling'` — `controller/tool_calling.py` doesn't exist yet.

- [ ] **Step 5: Write `controller/tool_calling.py`.**

```python
import json

import anthropic

DEFAULT_MODEL = "claude-haiku-4-5"

SEARCH_TOOL = {
    "name": "search",
    "description": (
        "Trigger a new Soulseek search for the given query. Use this when "
        "the user wants to search for a new song, artist, or album - not "
        "when they are referring to existing results."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query text, e.g. artist and track name.",
            }
        },
        "required": ["query"],
    },
}

DOWNLOAD_TOOL = {
    "name": "download",
    "description": (
        "Queue a download from the most recent search results. Use 'index' "
        "when the user refers to a result by its position (e.g. 'the "
        "second one', 'number 3'). Use 'match' when the user refers to a "
        "result by a distinguishing word or phrase from its filename (e.g. "
        "'the one that sounds like Blue Monday', 'the flac version'). "
        "Provide exactly one of index or match, never both."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "index": {
                "type": "integer",
                "description": "1-based index into the most recent search results.",
            },
            "match": {
                "type": "string",
                "description": "A substring to match (case-insensitive) against result filenames.",
            },
        },
    },
}

LIST_RESULTS_TOOL = {
    "name": "list_results",
    "description": (
        "Replay the most recent search results without triggering a new "
        "search. Use this when the user asks to hear the results again or "
        "asks what the options were."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

TOOLS = [SEARCH_TOOL, DOWNLOAD_TOOL, LIST_RESULTS_TOOL]

SYSTEM_PROMPT = (
    "You are the command-resolution layer for a voice-controlled Soulseek "
    "(Nicotine+) client. The user just spoke or typed a command; your job "
    "is to resolve it to exactly one of the three provided tools. You will "
    "always be given the most recent search results (if any) as JSON "
    "context. Always call exactly one tool - never respond with plain text."
)


def translate_tool_call(name: str, tool_input: dict) -> dict:
    if name == "search":
        return {"action": "search", "query": tool_input["query"]}

    if name == "download":
        if "index" in tool_input:
            return {"action": "download", "index": tool_input["index"]}

        if "match" in tool_input:
            return {"action": "download", "match": tool_input["match"]}

        raise ValueError("download tool call missing both 'index' and 'match'")

    if name == "list_results":
        return {"action": "list_results"}

    raise ValueError(f"unknown tool name: {name!r}")


def _format_last_results(last_results: list) -> str:
    if not last_results:
        return "No search results yet."

    return json.dumps(last_results, indent=2)


def resolve_transcript(transcript: str, last_results: list, client=None) -> dict:
    if client is None:
        client = anthropic.Anthropic()

    message_content = (
        f"Most recent search results:\n{_format_last_results(last_results)}\n\n"
        f"User command: {transcript}"
    )

    response = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=TOOLS,
        tool_choice={"type": "any"},
        messages=[{"role": "user", "content": message_content}],
    )

    for block in response.content:
        if block.type == "tool_use":
            return translate_tool_call(block.name, block.input)

    raise RuntimeError("Claude did not return a tool call despite tool_choice='any'")
```

- [ ] **Step 6: Run tests to verify they pass.**

Run: `cd controller && python3 -m unittest tests.test_tool_calling -v`
Expected: 13 tests, all `ok`.

- [ ] **Step 7: Run the full controller test suite to confirm no regression.**

Run: `cd controller && python3 -m unittest discover tests -v`
Expected: all tests from `test_controller.py` (18) and `test_tool_calling.py` (13) pass — 31 total, all `ok`. `test_controller.py` must be completely unaffected since this plan never touches `controller.py`.

- [ ] **Step 8: Update `controller/README.md`.**

Replace the `## Setup` section with:

```markdown
## Setup

Create and activate this controller's own virtualenv, then install its
requirements (currently just the `anthropic` SDK, for the tool-calling
layer):

    python3 -m venv controller/.venv && source controller/.venv/bin/activate && pip install -r controller/requirements.txt

The tool-calling layer (`tool_calling.py`) needs an `ANTHROPIC_API_KEY`
environment variable (or another Anthropic SDK-supported credential source)
to make real API calls — the automated test suite mocks the Anthropic
client and needs no credentials.
```

Add a new section after `## Tests`:

```markdown
## Manual verification: tool-calling layer

`tool_calling.py`'s automated tests mock the Anthropic client, so they
verify the request/response plumbing but not whether Claude Haiku 4.5
actually resolves commands correctly - especially ambiguous ones. To check
that by hand (requires a working `ANTHROPIC_API_KEY`):

    cd controller && source .venv/bin/activate && python3 -c "
from tool_calling import resolve_transcript

last_results = [
    {'index': 1, 'filename': 'New Order - Blue Monday.flac', 'user': 'alice'},
    {'index': 2, 'filename': 'New Order - Blue Monday (12in Mix).mp3', 'user': 'bob'},
]

for transcript in [
    'search for blue monday by new order',
    'download the second one',
    'download the flac version',
    'what were the results again',
]:
    print(transcript, '->', resolve_transcript(transcript, last_results))
"

Expected: each line prints a plausible `{"action": ...}` dict matching the
spoken intent - e.g. the ambiguous "download the flac version" should
resolve to `{"action": "download", "match": "flac"}` or similar, not an
index. This module is not yet wired to the live socket (that's step 6) -
this only exercises the LLM resolution step in isolation.
```

- [ ] **Step 9: Verify Python syntax is valid.**

Run: `python3 -c "import ast; ast.parse(open('controller/tool_calling.py').read())"`
Expected: no output, exit code 0.

- [ ] **Step 10: Commit.**

```bash
git add controller/tool_calling.py controller/tests/test_tool_calling.py controller/requirements.txt controller/README.md
git commit -m "Add LLM tool-calling layer for voice_control commands"
```

- [ ] **Step 11: Flag manual verification for the human.** This step needs a working `ANTHROPIC_API_KEY` (or another Anthropic SDK credential source) — it cannot be run without real credentials, which this environment does not have. In the final report, tell the user to run the "Manual verification: tool-calling layer" script from the README (Step 8 above) and confirm: (1) `search for blue monday by new order` resolves to a `search` action with a sensible query, (2) `download the second one` resolves to `{"action": "download", "index": 2}`, (3) the ambiguous `download the flac version` resolves to a `download` action using `match` (not a guessed index) that would plausibly disambiguate the two similar filenames, and (4) `what were the results again` resolves to `{"action": "list_results"}`. If Haiku 4.5's resolution of the ambiguous case looks unreliable, the spec's documented fallback is Claude Sonnet 5 (pass `client.messages.create(model="claude-sonnet-5", ...)` or add a `--model` override) — note this in the report but do not implement a fallback mechanism in this plan; that's a follow-up decision for the human to make after seeing real behavior.

---

## Self-Review Notes

- **Spec coverage:** the spec's "Tool-calling (step 5)" bullet — "transcript + last-results-as-context sent to Claude Haiku 4.5 (`claude-haiku-4-5`) with three tool definitions — `search(query)`, `download(selector)`, `list_results()`" — is fully covered: `TOOLS` defines exactly these three tools (the spec's single `download(selector)` is implemented as `download(index?, match?)` to match the plugin's actual two-field protocol, exactly as step 3's controller CLI already does with `--index`/`--match`), and `resolve_transcript` sends the transcript plus JSON last-results context to `claude-haiku-4-5`. The build order's step 5 requirement — "tested on sample transcripts (including ambiguous ones) in isolation before wiring to the socket" — is covered by the mocked automated tests (isolation from the network) plus the flagged manual verification script using genuinely ambiguous sample transcripts (two similarly-named results). No socket wiring, no LLM calls in automated tests, no `controller.py` changes — matching the plan's explicit scope boundary.
- **Placeholder scan:** no TBD/TODO; every code block is complete and runnable as written; the download-missing-both and no-tool-use-block error paths both have concrete tests.
- **Type/signature consistency:** `translate_tool_call(name, tool_input)` is defined once in Step 5 and called identically by both `resolve_transcript` (Step 5) and every `TestTranslateToolCall` case (Step 3) with the same two-positional-argument shape. `resolve_transcript(transcript, last_results, client=None)` is defined once and called with matching argument order/names by every `TestResolveTranscript` case. `DEFAULT_MODEL` is a single module-level constant, referenced by `resolve_transcript` and asserted against in `test_builds_request_with_model_tools_and_forced_tool_choice` — never redefined.
- **No Nicotine+ imports, no `controller.py` changes:** confirmed — the only new import in `tool_calling.py` is `anthropic` (plus stdlib `json`). Nothing in this plan touches `nicotine_plugin/` or `controller/controller.py`. Step 7 explicitly re-runs `test_controller.py` to prove this.
- **Library API verified against current docs (not assumed from training data):** the tool-definition shape (`name`/`description`/`input_schema`), `tool_choice: {"type": "any"}` forcing a tool call, `client.messages.create(...)` signature, and the `tool_use` content-block shape (`.type`/`.name`/`.input`) were all confirmed via the Claude API reference material for the current SDK. The model ID `claude-haiku-4-5` was confirmed as the current, non-deprecated model string.
