"""Game Commentator - entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from commentator import __version__
from commentator.logs import DevLogger, bus, dev_mode_from_env, dev_session


def build_parser() -> argparse.ArgumentParser:
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
        help="where dev-mode session folders go (default: <repo>/logs, "
        "or %%LOCALAPPDATA%%\\GameCommentator\\logs for the .exe)",
    )
    parser.add_argument("--game", help="force a game config by id, e.g. pico_park")
    parser.add_argument("--window", help="force a window by (part of) its title")
    parser.add_argument("--persona", help="persona config id (not used yet)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def run(args: argparse.Namespace, dev_logger: DevLogger | None) -> int:
    # Until the engine exists (task B5.1): startup, detection, overlay and capture.
    from commentator.app import run as run_app

    return run_app(args, dev_logger)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not (args.dev or dev_mode_from_env()):
        return run(args, None)

    session_info = {"version": __version__, "argv": sys.argv[1:] if argv is None else argv}
    with dev_session(bus, root=args.log_dir, session_info=session_info) as logger:
        print(f"Dev mode: logging to {logger.session_dir}")
        return run(args, logger)


if __name__ == "__main__":
    sys.exit(main())
