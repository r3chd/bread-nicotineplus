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
