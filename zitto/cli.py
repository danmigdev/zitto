"""Command line interface."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__, plugins
from .settings import HOME
from .targets import UnknownTarget, available, resolve

DEFAULT_OUT = Path(os.environ.get("ZITTO_OUTPUT") or HOME / "output")


def build_parser() -> argparse.ArgumentParser:
    links = plugins.links()
    listing = "\n".join(f"  {t.key:<11} {t.label}" for t in available())
    parser = argparse.ArgumentParser(
        prog="zitto",
        description="Remove a voice or an instrument from an audio or video file.",
        epilog=f"what can be removed:\n{listing}\n\nWith no arguments the window opens.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("source", help="audio or video file" + "".join(f", or {s.label}" for s in links))
    parser.set_defaults(playlist=False)
    if links:
        parser.add_argument("--playlist", action="store_true",
                            help="when the link is a song inside a list, take the whole list")
    parser.add_argument("--player", action="store_true",
                        help="open the window with this song, instead of removing something")
    parser.add_argument("-r", "--remove", nargs="+", default=["voice"], metavar="WHAT",
                        help="one or more of the names below (default: voice)")
    parser.add_argument("-o", "--output", type=Path, help="output file (.mp3, .flac or .wav)")
    parser.add_argument("-f", "--format", choices=["mp3", "flac", "wav"], default="mp3",
                        help="output format when -o is not given (default: mp3 320k)")
    parser.add_argument("-d", "--out-dir", type=Path, default=DEFAULT_OUT,
                        help=f"output folder when -o is not given (default: {DEFAULT_OUT})")
    parser.add_argument("--save-removed", action="store_true", help="also save the removed part")
    parser.add_argument("-v", "--verbose", action="store_true", help="show the engine log")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")  # titles may not fit the console code page
    args = parser.parse_args(argv)
    if args.player:
        from .gui import main as window
        return window(args.source)
    try:
        targets = resolve(args.remove)
    except UnknownTarget as e:
        parser.error(str(e))
    if args.output and args.output.suffix.lower() not in (".mp3", ".flac", ".wav"):
        parser.error("the output file must end in .mp3, .flac or .wav")

    from .audio import AudioError
    from .engine import Engine, EngineError
    from .remover import remove, remove_playlist
    from .source import LIST, SONG_IN_LIST, SourceError, kind

    found = kind(args.source)
    in_playlist = found == SONG_IN_LIST
    whole = found == LIST or (args.playlist and in_playlist)
    if args.playlist and not whole:
        parser.error("--playlist: there is no playlist in that link")
    if whole and args.output:
        parser.error("a playlist produces several files: use -d FOLDER instead of -o")
    if in_playlist and not whole:
        print("This song is inside a list: taking only the song. Add --playlist for all of it.")

    engine = Engine(verbose=args.verbose)
    try:
        if whole:
            summary = remove_playlist(args.source, targets, args.out_dir, args.format,
                                      args.save_removed, engine)
            print(f"{summary}\nFolder: {summary.folder}")
            for line in summary.problems():
                print(f"  {line}", file=sys.stderr)
            return 0 if summary.complete else 1
        result = remove(args.source, targets, args.out_dir, args.format, args.output,
                        args.save_removed, engine)
    except (AudioError, EngineError, SourceError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled." + (" Run the same command again to continue." if whole else ""), file=sys.stderr)
        return 130
    print(f"Done: {result.output}")
    if result.removed:
        print(f"Removed part: {result.removed}")
    return 0
