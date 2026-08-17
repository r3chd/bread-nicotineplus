# voice_control Controller (text-mode skeleton)

Standalone CLI that sends one command to the running `voice_control` Nicotine+
plugin over its Unix socket and prints the JSON response. No Nicotine+
imports — this can run from any Python 3 interpreter as long as Nicotine+ is
running with the plugin enabled.

## Setup

Create and activate this controller's own virtualenv, then install its
requirements (currently just the `anthropic` SDK, for the tool-calling
layer):

    python3 -m venv controller/.venv && source controller/.venv/bin/activate && pip install -r controller/requirements.txt

The tool-calling layer (`tool_calling.py`) needs an `ANTHROPIC_API_KEY`
environment variable (or another Anthropic SDK-supported credential source)
to make real API calls — the automated test suite mocks the Anthropic
client and needs no credentials.

## Usage

    python3 controller/controller.py search "Blue Monday New Order"
    python3 controller/controller.py list_results
    python3 controller/controller.py download --index 1
    python3 controller/controller.py download --match "blue monday"

Custom socket path (default is
`~/.local/share/nicotine/plugins/voice_control/control.sock`):

    python3 controller/controller.py --socket-path /path/to/control.sock list_results

## Tests

    cd controller && python3 -m unittest discover tests -v

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
