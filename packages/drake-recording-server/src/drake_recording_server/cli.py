# SPDX-License-Identifier: MIT
"""Command-line interface for the Drake Recording Server."""

from __future__ import annotations

import argparse
from pathlib import Path

from drake_recording_server.server import run_server


def main() -> None:
    """Run the Drake Recording Server from the command line."""
    parser = argparse.ArgumentParser(
        description="Record Drake simulation states as Blender keyframes"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="URL to host on (default: %(default)s)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to host on (default: %(default)s)",
    )
    parser.add_argument(
        "--blend_file",
        type=Path,
        metavar="FILE",
        help="Path to a *.blend file to use as base scene",
    )
    parser.add_argument(
        "--export_path",
        required=True,
        type=Path,
        help="Path to export the Blender scene (.blend)",
    )
    parser.add_argument(
        "--keyframe_dump_path",
        required=True,
        type=Path,
        help="Path to dump keyframes to disk (.pkl)",
    )
    parser.add_argument(
        "--bpy_settings_file",
        type=Path,
        metavar="FILE",
        help="Path to a *.py file to configure Blender settings",
    )
    args = parser.parse_args()

    if args.export_path.suffix != ".blend":
        raise ValueError(
            f"Expected export_path to have '.blend' suffix, "
            f"got '{args.export_path.suffix}'"
        )
    if args.keyframe_dump_path.suffix != ".pkl":
        raise ValueError(
            f"Expected keyframe_dump_path to have '.pkl' suffix, "
            f"got '{args.keyframe_dump_path.suffix}'"
        )

    run_server(
        host=args.host,
        port=args.port,
        blend_file=args.blend_file,
        bpy_settings_file=args.bpy_settings_file,
        export_path=args.export_path,
        keyframe_dump_path=args.keyframe_dump_path,
    )


if __name__ == "__main__":
    main()
