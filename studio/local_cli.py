"""Command-line discovery and validation for local PeakCut video inputs."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Iterable


SUPPORTED_EXTENSIONS = frozenset({".mp4", ".mkv", ".mov", ".webm"})


class LocalInputError(ValueError):
    """A clear error for a bad CLI input or option."""


def discover_local_videos(input_path: str | Path) -> list[Path]:
    """Return validated mp4/mkv/mov/webm files from a file or folder.

    Folder inputs are searched recursively. Paths are resolved and sorted, and
    output is deduplicated when the same file is reached more than once.
    """
    raw = str(input_path).strip()
    if not raw:
        raise LocalInputError("Input is required.")

    path = Path(raw).expanduser()
    if not path.exists():
        raise LocalInputError(f'Input does not exist: "{path}"')

    if path.is_file():
        found = [path]
    elif path.is_dir():
        found = list(path.rglob("*"))
    else:
        raise LocalInputError(f'Input is neither a file nor a folder: "{path}"')

    files: dict[Path, None] = {}
    for candidate in found:
        if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_EXTENSIONS:
            resolved = candidate.resolve()
            files.setdefault(resolved, None)

    if not files:
        wanted = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise LocalInputError(
            f'No supported video files found in "{path}". Use {wanted} files.'
        )
    return sorted(files, key=lambda item: str(item).lower())


def build_parser() -> argparse.ArgumentParser:
    """Build the local CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m studio.local_cli",
        description="Discover local video files for PeakCut clip generation.",
    )
    parser.add_argument(
        "--input",
        required=True,
        metavar="FILE_OR_FOLDER",
        help="A local mp4, mkv, mov or webm file, or a folder containing one.",
    )
    parser.add_argument("--count", type=int, default=12, help="Number of clips to generate (default: 12).")
    parser.add_argument("--min", dest="min_length", type=int, default=20, help="Minimum clip length in seconds (default: 20).")
    parser.add_argument("--max", dest="max_length", type=int, default=55, help="Maximum clip length in seconds (default: 55).")
    parser.add_argument(
        "--log-file",
        default="peakcut-local.log",
        help="Log file path (default: peakcut-local.log).",
    )
    return parser


def validate_options(count: int, min_length: int, max_length: int) -> None:
    """Validate clip count and duration options before processing."""
    if count < 1:
        raise LocalInputError("--count must be at least 1.")
    if min_length < 1:
        raise LocalInputError("--min must be at least 1 second.")
    if max_length < 1:
        raise LocalInputError("--max must be at least 1 second.")
    if min_length > max_length:
        raise LocalInputError("--min cannot be greater than --max.")


def configure_logging(log_file: str | Path) -> logging.Logger:
    """Configure plain console and file logging for the local CLI."""
    logger = logging.getLogger("peakcut.local")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        logger.addHandler(console)

        path = Path(log_file).expanduser()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(path, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(file_handler)
        except OSError as exc:
            raise LocalInputError(f'Could not open log file "{path}": {exc}') from exc
    return logger


def print_discovery(logger: logging.Logger, files: Iterable[Path], count: int, min_length: int, max_length: int) -> None:
    """Print a concise discovery summary to the console."""
    found = list(files)
    logger.info("Found %d local video%s.", len(found), "" if len(found) == 1 else "s")
    for index, path in enumerate(found, 1):
        logger.info("%02d %s", index, path)
    logger.info("Clip request: count=%d min=%ds max=%ds", count, min_length, max_length)
    logger.info("Discovery only; clip processing will be added in the next step.")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        validate_options(args.count, args.min_length, args.max_length)
        files = discover_local_videos(args.input)
        logger = configure_logging(args.log_file)
        print_discovery(logger, files, args.count, args.min_length, args.max_length)
        return 0
    except LocalInputError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"Error: could not inspect the input: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
