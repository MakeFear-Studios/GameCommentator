"""Game Commentator - entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from commentator import __version__
from commentator.logs import bus, dev_mode_from_env, dev_session


def build_parser() -> argparse.ArgumentParser:
    # Task 0.5 adds --game, --persona, etc. here.
    parser = argparse.ArgumentParser(prog="GameCommentator", description="Real-time AI commentary for your gameplay.")
    parser.add_argument(
        "--dev",
        action="store_true",
        help="dev mode: record everything to a session log (also enabled by GAME_COMMENTATOR_DEV=1)",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help="where dev-mode session folders go (default: <repo>/logs, or %%LOCALAPPDATA%%\\GameCommentator\\logs for the .exe)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def run() -> None:
    # Placeholder until the engine exists (task B5.1).
    print("Game Commentator: ready.")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not (args.dev or dev_mode_from_env()):
        run()
        return 0

    session_info = {"version": __version__, "argv": sys.argv[1:] if argv is None else argv}
    with dev_session(bus, root=args.log_dir, session_info=session_info) as logger:
        print(f"Dev mode: logging to {logger.session_dir}")
        run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
