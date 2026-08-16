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
