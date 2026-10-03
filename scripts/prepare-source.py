"""Prepare reviewed source by default, or explicitly resolve current upstream."""
import argparse
from pathlib import Path
import sys
from source_tools import ROOT, SourceError, prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache")
    parser.add_argument("--target", type=Path, default=ROOT / "Telegram")
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--ref", help="latest, an upstream tag/branch, or a full commit SHA")
    choice.add_argument("--lock", type=Path, help="An explicit source lock manifest")
    args = parser.parse_args()
    lock = args.lock or (None if args.ref else ROOT / "sources.lock.json")
    try:
        prepare(args.target.resolve(), args.cache.resolve(), ref=args.ref or "latest", lock=lock)
    except (SourceError, OSError, ValueError) as exc:
        print(f"ERROR: {exc if isinstance(exc, SourceError) else type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
