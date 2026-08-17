# voice_control Controller

Standalone CLI that sends commands to the running `voice_control` Nicotine+
plugin over its Unix socket and prints the JSON response, supporting both
typed and push-to-talk voice input. No Nicotine+ imports — this can run from
any Python 3 interpreter as long as Nicotine+ is running with the plugin
enabled.

## Setup

Create and activate this controller's own virtualenv, then install its
requirements (includes `faster-whisper`, `sounddevice`, and `numpy` for the
`listen` subcommand):

    python3 -m venv controller/.venv && source controller/.venv/bin/activate && pip install -r controller/requirements.txt

The venv must be active to run any subcommand below, including the text-mode
`search`/`download`/`list_results` commands.

## Usage

    python3 controller/controller.py search "Blue Monday New Order"
    python3 controller/controller.py list_results
    python3 controller/controller.py download --index 1
    python3 controller/controller.py download --match "blue monday"

Push-to-talk voice input (press Enter to start recording, Enter again to
stop; sends the transcript as a search; Ctrl+C to exit):

    python3 controller/controller.py listen
    python3 controller/controller.py listen --whisper-model small

The first `listen` run downloads the Whisper model (~140MB for `base`) over
the network, which can take tens of seconds with no progress indicator
before the "Voice control listening..." banner appears — this is expected,
not a hang. On macOS you'll also be prompted for microphone permission the
first time; denying it will cause the run to fail.

Custom socket path (default is
`~/.local/share/nicotine/plugins/voice_control/control.sock`):

    python3 controller/controller.py --socket-path /path/to/control.sock list_results

## Tests

With the venv active (see Setup):

    cd controller && python3 -m unittest tests.test_controller -v
