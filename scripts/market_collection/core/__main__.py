"""Manual entry point for the fixed public source registry."""

import argparse
import csv
import json
import os
from pathlib import Path

from . import CollectionError, history, live, replay

# GitHub shows at most 10 error annotations per step; past that the step summary's "Failed source keys" line has them all.
ANNOTATION_LIMIT = 10


def _escape(text: str, *, prop: bool = False) -> str:
    text = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return text.replace(":", "%3A").replace(",", "%2C") if prop else text


def failure_annotations(run_dir: Path) -> list[str]:
    """One GitHub Actions error line per failed source naming its model, route and collector (parser), so the run page
    says which supplier path broke and why instead of only "exit code 1" (2026-10-28, 박건)."""
    parsers = {item["spec"]["source_key"]: item["spec"]["parser"]
               for item in json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))["sources"]}
    with (run_dir / "sources.csv").open(newline="", encoding="utf-8") as stream:
        failed = [row for row in csv.DictReader(stream) if row["fetch_status"] != "ok" or row["parse_status"] != "ok"]
    lines = []
    for row in failed[:ANNOTATION_LIMIT - 1] if len(failed) > ANNOTATION_LIMIT else failed:
        http = f" (HTTP {row['http_status']})" if row["http_status"] not in ("", "200") else ""
        reason = f"가져오기 {row['fetch_status']}{http} · 해석 {row['parse_status']}" + (f" · {row['error'][:300]}" if row["error"] else "")
        title = f"수집 실패 · {row['model_id']} · {row['route']}"
        lines.append(f"::error title={_escape(title, prop=True)}::{_escape(row['source_key'] + ' · 수집기 ' + parsers.get(row['source_key'], '?') + ' · ' + reason)}")
    if len(failed) > ANNOTATION_LIMIT:
        lines.append(f"::error title={_escape(f'수집 실패 외 {len(failed) - len(lines)}개', prop=True)}::"
                     + _escape("나머지 출처는 Summarize collection 요약의 Failed source keys에 있습니다."))
    return lines


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
    actions = os.environ.get("GITHUB_ACTIONS") == "true"
    try:
        if args.command == "live":
            result = live(args.output_root, tuple(args.disable_source))
            if actions:
                try:
                    lines = failure_annotations(result)
                except Exception as exc:  # display only: it must never change this step's result
                    lines = [f"::warning::failure annotations skipped: {_escape(f'{type(exc).__name__}: {exc}')}"]
                if lines:
                    print("\n".join(lines))
        elif args.command == "replay":
            result = replay(args.run, args.output_root)
        else:
            result = history(args.output_root, args.output)
    except (CollectionError, OSError, ValueError) as exc:
        if actions:
            print(f"::error title={_escape('수집 실행 실패', prop=True)}::{_escape(f'{type(exc).__name__}: {exc}')}")
        parser.exit(1, f"error: {exc}\n")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
