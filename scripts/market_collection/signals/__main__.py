"""CLI: python3 -B -m scripts.market_collection.signals {live,replay,history,summary}.

live exits 0 when every source was fetched and parsed, 1 when some source failed
(the run is still written), and 2 when the run was refused.
"""

import argparse
import sys
from pathlib import Path

from scripts.market_collection.core import CollectionError

from . import DEFAULT_LOOKBACK_DAYS, history, live, replay, summary, validated_run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m scripts.market_collection.signals")
    commands = parser.add_subparsers(dest="command", required=True)
    live_parser = commands.add_parser("live")
    live_parser.add_argument("--root", type=Path, default=Path("data/market_collection"))
    live_parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    live_parser.add_argument("--disable-source", action="append", default=[])
    replay_parser = commands.add_parser("replay")
    replay_parser.add_argument("--run", type=Path, required=True)
    replay_parser.add_argument("--root", type=Path, default=Path("data/market_collection"))
    history_parser = commands.add_parser("history")
    history_parser.add_argument("--root", type=Path, default=Path("data/market_collection"))
    history_parser.add_argument("--output", type=Path, required=True)
    summary_parser = commands.add_parser("summary")
    summary_parser.add_argument("--root", type=Path, default=Path("data/market_collection"))
    args = parser.parse_args(argv)
    try:
        if args.command == "summary":
            sys.stdout.write(summary(args.root))
            return 0
        if args.command == "live":
            result = live(args.root, args.lookback_days, tuple(args.disable_source))
        elif args.command == "replay":
            result = replay(args.run, args.root)
        else:
            print(history(args.root, args.output))
            return 0
    except (CollectionError, OSError, ValueError, KeyError) as exc:
        print(f"signals refused: {exc}", file=sys.stderr)
        return 2
    print(result)
    _, sources, signals = validated_run(result)
    failed = [source["source_key"] for source in sources if source["fetch_status"] != "ok" or source["parse_status"] != "ok"]
    print(f"sources {len(sources)} · signals {len(signals)} · failed {len(failed)}" + (": " + ", ".join(failed) if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
