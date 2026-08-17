import argparse
import json
import os
import socket
import sys

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel


DEFAULT_SOCKET_PATH = os.path.expanduser(
    "~/.local/share/nicotine/plugins/voice_control/control.sock"
)

# The plugin server does a single-threaded accept loop with a
# SEARCH_COLLECTION_SECONDS (~5s) sleep inside its search handler, plus up to
# two ~10s main-thread-call waits (~30s worst case). 45s gives comfortable
# headroom above that real ceiling.
DEFAULT_TIMEOUT_SECONDS = 45

SAMPLE_RATE = 16000  # Whisper's native sample rate; sounddevice records at this rate directly
DEFAULT_WHISPER_MODEL = "base"


def build_request(args: argparse.Namespace) -> dict:
    if args.action == "search":
        return {"action": "search", "query": args.query}

    if args.action == "download":
        if args.index is not None:
            return {"action": "download", "index": args.index}

        return {"action": "download", "match": args.match}

    if args.action == "list_results":
        return {"action": "list_results"}

    raise ValueError(f"unknown action: {args.action!r}")


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


def send_request(
    request: dict, socket_path: str, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> dict:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(socket_path)
            sock.sendall(json.dumps(request).encode("utf-8") + b"\n")
            with sock.makefile("r", encoding="utf-8") as stream:
                response_line = stream.readline()
    except TimeoutError as error:
        raise ConnectionError(
            f"no response from voice_control socket at {socket_path!r} "
            f"within {timeout}s"
        ) from error
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

    try:
        return json.loads(response_line)
    except json.JSONDecodeError as error:
        raise ConnectionError(
            f"voice_control socket at {socket_path!r} sent a malformed "
            f"response line: {response_line!r}"
        ) from error


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
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=(
            "seconds to wait for a response before giving up "
            f"(default: {DEFAULT_TIMEOUT_SECONDS})"
        ),
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
        response = send_request(request, args.socket_path, timeout=args.timeout)
    except ConnectionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(json.dumps(response, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
