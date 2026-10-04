"""Manual entry point for the fixed public source registry."""

import argparse
from pathlib import Path

from . import CollectionError, history, live, replay


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    live_parser = commands.add_parser("live")
    live_parser.add_argument("--output-root", type=Path, default=Path("data/market_collection"))
    live_parser.add_argument("--disable-source", action="append", default=[])
    replay_parser = commands.add_parser("replay")
    replay_parser.add_argument("--run", type=Path, required=True)
    replay_parser.add_argument("--output-root", type=Path, default=Path("data/market_collection"))
    history_parser = commands.add_parser("history")
    history_parser.add_argument("--output-root", type=Path, default=Path("data/market_collection"))
    history_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "live":
            result = live(args.output_root, tuple(args.disable_source))
        elif args.command == "replay":
            result = replay(args.run, args.output_root)
        else:
            result = history(args.output_root, args.output)
    except (CollectionError, OSError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
