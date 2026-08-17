# Nicotine+ Speech-to-Text (Push-to-Talk) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `listen` subcommand to `controller/controller.py` that loops: press Enter to start recording, press Enter again to stop, transcribe the clip with `faster-whisper`, and send the transcript through the *existing* socket path as a `search` request — proving push-to-talk audio capture and local speech-to-text work end-to-end, feeding into the same plugin round trip step 3 already validated. No LLM/tool-calling yet (that's step 5).

**Architecture:** Three new pure/mockable functions (`record_audio`, `transcribe_audio`, `load_whisper_model`) built on top of `sounddevice` (mic capture into an in-memory numpy array) and `faster-whisper` (CPU transcription), plus a `run_listen_loop` orchestrator that wires them to the existing `send_request()`/`ConnectionError` machinery from step 3. Everything stays in the single `controller/controller.py` file, matching the file's existing shape. The three existing one-shot subcommands (`search`, `download`, `list_results`) are untouched.

**Tech Stack:** `faster-whisper` (CPU, `base` model by default), `sounddevice` + `numpy` for mic capture. These are the controller's first non-stdlib dependencies — anticipated by the `requirements.txt` comment left in step 3 ("faster-whisper / anthropic will be added here in step 4/5").

**Spec:** `docs/superpowers/specs/2026-08-11-nicotine-voice-control-design.md` — this plan implements the spec's "2. Controller" component's "Push-to-talk (step 4)" and "Speech-to-text" bullets, using the "same path as step 3" (the `search` action). Linear issue: D3V-35.

**Prior work:** Step 3 (`docs/superpowers/plans/2026-08-16-nicotine-controller-skeleton.md`, merged to `main` at `fb2cf01`) delivered `controller/controller.py` with `build_request()`, `send_request()`, `build_parser()`, and `main()` supporting `search`/`download`/`list_results`, plus `DEFAULT_SOCKET_PATH` and `DEFAULT_TIMEOUT_SECONDS` constants. This plan only adds to that file — it does not modify `nicotine_plugin/`.

## Global Constraints

- Controller stays a separate process, own `venv`/`requirements.txt`, **no Nicotine+ imports** (spec, "2. Controller") — unchanged from step 3.
- **Push-to-talk (spec, step 4):** "press Enter to start recording, Enter again to stop." No true held-hotkey, no OS Accessibility permission requirement.
- **Speech-to-text (spec):** `faster-whisper`, small/base model, CPU. This plan defaults to `base` (fastest on CPU; overridable via `--whisper-model`).
- **"feeding into the same path as step 3" (spec, step 4):** the transcript is sent as a `search` request through the existing `send_request()` — no new socket-protocol handling, no natural-language command parsing (that is step 5's job).
- **Verified library APIs (checked against current docs, not memory):**
  - `faster_whisper.WhisperModel(model_size, device="cpu", compute_type="int8")` — constructor. `model.transcribe(audio_ndarray)` returns `(segments, info)`; `segments` is a generator of objects with a `.text` attribute; the model accepts a numpy array directly, no temp file needed.
  - `sounddevice.InputStream(samplerate=..., channels=..., dtype=..., callback=callback)` used as a context manager; the callback signature is `callback(indata, frames, time, status)`, called on a separate audio thread once per audio block.
- No wiring to an LLM, no natural-language tool selection — step 5, explicitly out of scope here.

---

## File Structure

- Modify: `controller/controller.py` — add `SAMPLE_RATE`, `DEFAULT_WHISPER_MODEL` constants; add `record_audio()`, `transcribe_audio()`, `load_whisper_model()`, `run_listen_loop()`; add `listen` subcommand to `build_parser()`; extend `main()` to dispatch `listen` before `build_request()`.
- Modify: `controller/tests/test_controller.py` — add test classes for the four new functions, mocking `sounddevice`/`faster_whisper` so no real microphone or model download is needed to run the suite.
- Modify: `controller/requirements.txt` — add `faster-whisper`, `sounddevice`, `numpy`.
- Modify: `controller/README.md` — document `listen` usage.

## Task 1: Recording + transcription primitives, with unit tests

**Files:**
- Modify: `controller/controller.py`
- Modify: `controller/tests/test_controller.py`
- Modify: `controller/requirements.txt`

**Interfaces:**
- Consumes: nothing from other tasks (first task).
- Produces:
  - `SAMPLE_RATE: int = 16000` — module constant, Whisper's native sample rate.
  - `DEFAULT_WHISPER_MODEL: str = "base"` — module constant.
  - `record_audio(sample_rate: int = SAMPLE_RATE) -> numpy.ndarray` — blocks on `sd.InputStream` + a second `input()` press to stop; returns a 1-D `float32` array of concatenated samples (empty array, shape `(0,)`, if nothing was captured).
  - `transcribe_audio(audio: numpy.ndarray, model) -> str` — returns `""` immediately for empty audio (no model call); otherwise joins `segment.text.strip()` for every segment, stripped.
  - `load_whisper_model(model_size: str) -> faster_whisper.WhisperModel` — `WhisperModel(model_size, device="cpu", compute_type="int8")`.

- [ ] **Step 1: Add the new dependencies to `requirements.txt`.**

Replace the contents of `controller/requirements.txt`:

```
faster-whisper
sounddevice
numpy
```

- [ ] **Step 2: Install the dependencies into the controller's venv.**

Run:
```bash
cd controller && python3 -m venv .venv 2>/dev/null; source .venv/bin/activate && pip install -r requirements.txt
```
Expected: `faster-whisper`, `sounddevice`, and `numpy` (and their transitive deps, e.g. `ctranslate2`, `huggingface-hub`) install without error. This does **not** download any Whisper model weights yet — model download only happens the first time `WhisperModel(...)` is actually constructed, which the unit tests below never do (they mock it).

- [ ] **Step 3: Write the failing tests.** Append to `controller/tests/test_controller.py` — first update the import line, then add the new test classes anywhere after the existing ones:

Change line 15 from:
```python
from controller import build_parser, build_request, main, send_request
```
to:
```python
import numpy as np
from unittest.mock import MagicMock, patch

from controller import (
    DEFAULT_WHISPER_MODEL,
    build_parser,
    build_request,
    main,
    record_audio,
    send_request,
    transcribe_audio,
)
```

Append this to the end of the file:

```python
class TestRecordAudio(unittest.TestCase):

    def test_concatenates_callback_chunks_in_order(self):
        captured = {}

        class FakeInputStream:
            def __init__(self, samplerate, channels, dtype, callback):
                captured["callback"] = callback

            def __enter__(self):
                captured["callback"](
                    np.array([[0.1], [0.2]], dtype="float32"), 2, None, None
                )
                captured["callback"](np.array([[0.3]], dtype="float32"), 1, None, None)
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                return False

        with patch("controller.sd.InputStream", side_effect=FakeInputStream), patch(
            "builtins.input", return_value=""
        ):
            audio = record_audio(sample_rate=16000)

        np.testing.assert_allclose(audio, np.array([0.1, 0.2, 0.3], dtype="float32"))

    def test_no_audio_captured_returns_empty_array(self):
        class EmptyInputStream:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                return False

        with patch("controller.sd.InputStream", side_effect=EmptyInputStream), patch(
            "builtins.input", return_value=""
        ):
            audio = record_audio(sample_rate=16000)

        self.assertEqual(audio.shape, (0,))


class TestTranscribeAudio(unittest.TestCase):

    def test_empty_audio_returns_empty_string_without_calling_model(self):
        model = MagicMock()

        result = transcribe_audio(np.zeros(0, dtype="float32"), model)

        self.assertEqual(result, "")
        model.transcribe.assert_not_called()

    def test_joins_and_strips_segment_texts(self):
        model = MagicMock()
        segment_one = MagicMock(text=" search blue monday ")
        segment_two = MagicMock(text="new order ")
        model.transcribe.return_value = ([segment_one, segment_two], MagicMock())

        result = transcribe_audio(np.ones(10, dtype="float32"), model)

        self.assertEqual(result, "search blue monday new order")


class TestLoadWhisperModel(unittest.TestCase):

    def test_constructs_whisper_model_for_cpu(self):
        with patch("controller.WhisperModel") as mock_whisper_model:
            from controller import load_whisper_model

            load_whisper_model(DEFAULT_WHISPER_MODEL)

        mock_whisper_model.assert_called_once_with(
            DEFAULT_WHISPER_MODEL, device="cpu", compute_type="int8"
        )
```

- [ ] **Step 4: Run tests to verify they fail.**

Run: `cd controller && python3 -m unittest tests.test_controller -v`
Expected: `ImportError: cannot import name 'record_audio' from 'controller'` (or similar for `transcribe_audio`/`DEFAULT_WHISPER_MODEL`) — none of these exist in `controller.py` yet.

- [ ] **Step 5: Implement the three functions in `controller/controller.py`.**

Add these imports after the existing `import sys` line (keep the existing five stdlib imports as-is, add a blank line, then the new third-party imports):

```python
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
```

Add these constants after `DEFAULT_TIMEOUT_SECONDS`:

```python
SAMPLE_RATE = 16000  # Whisper's native sample rate; sounddevice records at this rate directly
DEFAULT_WHISPER_MODEL = "base"
```

Add these three functions after `build_request()` and before `send_request()`:

```python
def record_audio(sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    frames = []

    def callback(indata, frames_count, time, status):
        frames.append(indata.copy())

    print("Recording... press Enter to stop.")
    with sd.InputStream(
        samplerate=sample_rate, channels=1, dtype="float32", callback=callback
    ):
        input()

    if not frames:
        return np.zeros(0, dtype="float32")

    return np.concatenate(frames)[:, 0]


def transcribe_audio(audio: np.ndarray, model: WhisperModel) -> str:
    if audio.size == 0:
        return ""

    segments, _ = model.transcribe(audio)
    return " ".join(segment.text.strip() for segment in segments).strip()


def load_whisper_model(model_size: str) -> WhisperModel:
    return WhisperModel(model_size, device="cpu", compute_type="int8")
```

- [ ] **Step 6: Run tests to verify they pass.**

Run: `cd controller && python3 -m unittest tests.test_controller -v`
Expected: 23 tests (18 existing + 5 new), all `ok`.

- [ ] **Step 7: Commit.**

```bash
git add controller/controller.py controller/tests/test_controller.py controller/requirements.txt
git commit -m "Add audio recording and Whisper transcription primitives"
```

## Task 2: `listen` subcommand and push-to-talk loop, with unit tests

**Files:**
- Modify: `controller/controller.py`
- Modify: `controller/tests/test_controller.py`
- Modify: `controller/README.md`

**Interfaces:**
- Consumes (from Task 1): `record_audio(sample_rate=SAMPLE_RATE) -> np.ndarray`, `transcribe_audio(audio, model) -> str`, `load_whisper_model(model_size) -> WhisperModel`, `DEFAULT_WHISPER_MODEL`. Consumes (from step 3): `send_request(request, socket_path, timeout=...) -> dict`, `DEFAULT_SOCKET_PATH`, `DEFAULT_TIMEOUT_SECONDS`, `build_parser()`, `ConnectionError` usage pattern.
- Produces: `run_listen_loop(socket_path: str, timeout: float, model: WhisperModel) -> int` — runs until `KeyboardInterrupt`, returns `0`. `listen` subcommand on `build_parser()` with a `--whisper-model` flag (default `DEFAULT_WHISPER_MODEL`). `main()` dispatches `args.action == "listen"` before calling `build_request()`.

- [ ] **Step 1: Write the failing tests.** Append to `controller/tests/test_controller.py`. First add `run_listen_loop` and `DEFAULT_SOCKET_PATH`/`DEFAULT_TIMEOUT_SECONDS` to the import block from Task 1 (they're all in one `from controller import (...)` block — add `run_listen_loop` alphabetically):

```python
from controller import (
    DEFAULT_SOCKET_PATH,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_WHISPER_MODEL,
    build_parser,
    build_request,
    main,
    record_audio,
    run_listen_loop,
    send_request,
    transcribe_audio,
)
```

Append this to the end of the file:

```python
class TestBuildParserListen(unittest.TestCase):

    def test_listen_action_default_model(self):
        parser = build_parser()
        args = parser.parse_args(["listen"])
        self.assertEqual(args.action, "listen")
        self.assertEqual(args.whisper_model, DEFAULT_WHISPER_MODEL)

    def test_listen_action_custom_model(self):
        parser = build_parser()
        args = parser.parse_args(["listen", "--whisper-model", "small"])
        self.assertEqual(args.whisper_model, "small")


class TestRunListenLoop(unittest.TestCase):

    @patch("controller.send_request")
    @patch("controller.transcribe_audio")
    @patch("controller.record_audio")
    @patch("builtins.input")
    def test_sends_transcript_as_search_and_prints_response(
        self, mock_input, mock_record, mock_transcribe, mock_send
    ):
        mock_input.side_effect = [None, KeyboardInterrupt]
        mock_record.return_value = np.ones(10, dtype="float32")
        mock_transcribe.return_value = "blue monday"
        mock_send.return_value = {"results": []}

        exit_code = run_listen_loop("/tmp/x.sock", 45, MagicMock())

        self.assertEqual(exit_code, 0)
        mock_send.assert_called_once_with(
            {"action": "search", "query": "blue monday"}, "/tmp/x.sock", timeout=45
        )

    @patch("controller.send_request")
    @patch("controller.transcribe_audio")
    @patch("controller.record_audio")
    @patch("builtins.input")
    def test_empty_transcript_skips_socket_call(
        self, mock_input, mock_record, mock_transcribe, mock_send
    ):
        mock_input.side_effect = [None, KeyboardInterrupt]
        mock_record.return_value = np.zeros(0, dtype="float32")
        mock_transcribe.return_value = ""

        run_listen_loop("/tmp/x.sock", 45, MagicMock())

        mock_send.assert_not_called()

    @patch("controller.send_request")
    @patch("controller.transcribe_audio")
    @patch("controller.record_audio")
    @patch("builtins.input")
    def test_socket_error_does_not_end_the_loop(
        self, mock_input, mock_record, mock_transcribe, mock_send
    ):
        mock_input.side_effect = [None, None, KeyboardInterrupt]
        mock_record.return_value = np.ones(10, dtype="float32")
        mock_transcribe.return_value = "blue monday"
        mock_send.side_effect = [ConnectionError("socket gone"), {"results": []}]

        exit_code = run_listen_loop("/tmp/x.sock", 45, MagicMock())

        self.assertEqual(exit_code, 0)
        self.assertEqual(mock_send.call_count, 2)

    @patch("builtins.input", side_effect=KeyboardInterrupt)
    def test_ctrl_c_at_prompt_exits_cleanly(self, mock_input):
        exit_code = run_listen_loop("/tmp/x.sock", 45, MagicMock())
        self.assertEqual(exit_code, 0)


class TestMainListen(unittest.TestCase):

    @patch("controller.run_listen_loop", return_value=0)
    @patch("controller.load_whisper_model")
    def test_main_listen_loads_model_and_runs_loop(self, mock_load, mock_loop):
        model = MagicMock()
        mock_load.return_value = model

        exit_code = main(["listen"])

        mock_load.assert_called_once_with(DEFAULT_WHISPER_MODEL)
        mock_loop.assert_called_once_with(
            DEFAULT_SOCKET_PATH, DEFAULT_TIMEOUT_SECONDS, model
        )
        self.assertEqual(exit_code, 0)

    @patch(
        "controller.load_whisper_model",
        side_effect=RuntimeError("model download failed"),
    )
    def test_main_listen_model_load_failure_returns_1_not_a_traceback(self, mock_load):
        exit_code = main(["listen"])
        self.assertEqual(exit_code, 1)
```

- [ ] **Step 2: Run tests to verify they fail.**

Run: `cd controller && python3 -m unittest tests.test_controller -v`
Expected: `ImportError: cannot import name 'run_listen_loop' from 'controller'` — not defined yet.

- [ ] **Step 3: Implement `run_listen_loop()` in `controller/controller.py`.**

Add after `send_request()` and before `build_parser()`:

```python
def run_listen_loop(socket_path: str, timeout: float, model: WhisperModel) -> int:
    print("Voice control listening. Press Enter to start recording, Ctrl+C to exit.")
    while True:
        try:
            input()
            audio = record_audio()
            transcript = transcribe_audio(audio, model)
        except KeyboardInterrupt:
            print("\nExiting listen mode.")
            return 0

        if not transcript:
            print("no speech detected, try again")
            continue

        print(f"heard: {transcript}")
        request = {"action": "search", "query": transcript}

        try:
            response = send_request(request, socket_path, timeout=timeout)
        except ConnectionError as error:
            print(f"error: {error}", file=sys.stderr)
            continue

        print(json.dumps(response, indent=2))
```

- [ ] **Step 4: Add the `listen` subcommand to `build_parser()`.**

In `controller/controller.py`, inside `build_parser()`, after the `subparsers.add_parser("list_results", ...)` line and before `return parser`, add:

```python
    listen_parser = subparsers.add_parser(
        "listen", help="push-to-talk voice input (Enter to start/stop recording)"
    )
    listen_parser.add_argument(
        "--whisper-model",
        default=DEFAULT_WHISPER_MODEL,
        help=f"faster-whisper model size (default: {DEFAULT_WHISPER_MODEL})",
    )
```

- [ ] **Step 5: Dispatch `listen` in `main()` before `build_request()`.**

Replace the body of `main()`:

```python
def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.action == "listen":
        try:
            model = load_whisper_model(args.whisper_model)
            return run_listen_loop(args.socket_path, args.timeout, model)
        except Exception as error:
            print(f"error: {error}", file=sys.stderr)
            return 1

    request = build_request(args)

    try:
        response = send_request(request, args.socket_path, timeout=args.timeout)
    except ConnectionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(json.dumps(response, indent=2))
    return 0
```

- [ ] **Step 6: Run tests to verify they pass.**

Run: `cd controller && python3 -m unittest tests.test_controller -v`
Expected: 30 tests (23 from Task 1 + 7 new), all `ok`.

- [ ] **Step 7: Update `controller/README.md`.**

Replace the `## Usage` section with:

```markdown
## Usage

    python3 controller/controller.py search "Blue Monday New Order"
    python3 controller/controller.py list_results
    python3 controller/controller.py download --index 1
    python3 controller/controller.py download --match "blue monday"

Push-to-talk voice input (press Enter to start recording, Enter again to
stop; sends the transcript as a search; Ctrl+C to exit):

    python3 controller/controller.py listen
    python3 controller/controller.py listen --whisper-model small

Custom socket path (default is
`~/.local/share/nicotine/plugins/voice_control/control.sock`):

    python3 controller/controller.py --socket-path /path/to/control.sock list_results
```

- [ ] **Step 8: Verify Python syntax is valid.**

Run: `python3 -c "import ast; ast.parse(open('controller/controller.py').read())"`
Expected: no output, exit code 0.

- [ ] **Step 9: Commit.**

```bash
git add controller/controller.py controller/tests/test_controller.py controller/README.md
git commit -m "Add push-to-talk listen subcommand with Whisper transcription"
```

- [ ] **Step 10: Flag manual verification for the human.** This step needs a real microphone and a running Nicotine+ instance with the Voice Control plugin enabled — it cannot be driven by an agent. In the final report, tell the user to run:

```bash
cd controller && source .venv/bin/activate && python3 controller.py listen
```

and confirm: (1) the first run downloads the `base` Whisper model (one-time, needs network), (2) pressing Enter starts recording ("Recording... press Enter to stop." appears), (3) speaking a query like "Blue Monday New Order" and pressing Enter again prints a transcript close to what was said, (4) a JSON response with a `results` list is printed, matching step 3's live-verified socket round trip, (5) Ctrl+C exits cleanly with "Exiting listen mode." and no traceback.

---

## Self-Review Notes

- **Spec coverage:** the spec's "Push-to-talk (step 4)" bullet ("press Enter to start recording, Enter again to stop") is implemented by `run_listen_loop`'s two `input()` calls (one at the top of the loop, one inside `record_audio`). The "Speech-to-text" bullet ("`faster-whisper`, small/base model, CPU") is implemented by `load_whisper_model`/`transcribe_audio` defaulting to `base` on `device="cpu"`, with `--whisper-model` letting a user switch to `small`. "Feeding into the same path as step 3" is implemented by building `{"action": "search", "query": transcript}` and calling the exact same `send_request()` used by the `search` subcommand. Step 5 (LLM tool-calling) and step 6 (full end-to-end wiring) are explicitly out of scope, matching the spec's own build-order sequencing.
- **Placeholder scan:** no TBD/TODO; every code block is complete and runnable as written; both the empty-transcript and socket-error paths have concrete expected behavior and tests.
- **Type/signature consistency:** `record_audio(sample_rate=SAMPLE_RATE) -> np.ndarray` is defined once in Task 1 Step 5 and called with no arguments (using the default) from `run_listen_loop` in Task 2 Step 3 — matches. `transcribe_audio(audio, model) -> str` is defined once in Task 1 and called as `transcribe_audio(audio, model)` in Task 2 — matches. `run_listen_loop(socket_path, timeout, model) -> int` is defined once in Task 2 Step 3 and called identically from `main()` in Task 2 Step 5 and from the `TestMainListen` test — matches. `load_whisper_model(model_size) -> WhisperModel` is defined in Task 1 and called as `load_whisper_model(args.whisper_model)` in Task 2 — matches.
- **No Nicotine+ imports:** confirmed — the only new imports are `numpy`, `sounddevice`, and `faster_whisper`. Nothing in this plan touches `nicotine_plugin/`.
- **Library APIs verified against current docs (not assumed from training data):** `faster_whisper.WhisperModel` constructor signature and `model.transcribe(ndarray)` returning `(segments, info)` confirmed via Context7 (`/systran/faster-whisper`). `sounddevice.InputStream(samplerate=, channels=, dtype=, callback=)` as a context manager, and the `callback(indata, frames, time, status)` signature, confirmed via Context7 (`/spatialaudio/python-sounddevice`).
