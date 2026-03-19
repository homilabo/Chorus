"""Entry point for `python -m chorus` or `chorus` CLI."""

import argparse
import os

from chorus.repl import run


def main():
    parser = argparse.ArgumentParser(
        prog="chorus",
        description="Chorus — Multi-LLM deliberation tool. Have your AI models discuss, debate, and build on each other's ideas.",
    )
    parser.add_argument("--cwd", help="Working directory for CLI providers (default: current directory)", default=None)
    parser.add_argument("--manual", action="store_true", help="Manual command mode (/all, /debate, /cross)")
    parser.add_argument("--version", action="version", version="chorus 0.1.0")

    args = parser.parse_args()
    cwd = args.cwd or os.getcwd()
    run(cwd=cwd, manual=args.manual)


if __name__ == "__main__":
    main()
