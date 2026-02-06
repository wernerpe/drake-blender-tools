# SPDX-License-Identifier: MIT
"""Extract msgpack commands and assets from meshcat HTML recordings."""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

from meshcat_html_importer.parser.command_types import Command
from meshcat_html_importer.parser.msgpack_decoder import decode_msgpack

# Pattern to match base64 msgpack data URIs
# fetch("data:application/octet-binary;base64,<DATA>")
FETCH_PATTERN = re.compile(
    r'fetch\s*\(\s*["\']data:application/octet-binary;base64,([A-Za-z0-9+/=]+)["\']\s*\)'
)

# Pattern to match casAssets dictionary (old format)
# var casAssets = {"sha256-hash": "data:..."};
CAS_ASSETS_DICT_PATTERN = re.compile(r"var\s+casAssets\s*=\s*(\{.*?\})\s*;", re.DOTALL)

# Pattern to extract individual asset entries from object literal
ASSET_ENTRY_PATTERN = re.compile(r'"([^"]+)"\s*:\s*"([^"]*)"')

# Pattern to match individual casAssets assignments (new format)
# casAssets["cas-v1/hash"] = "data:...";
CAS_ASSETS_ASSIGNMENT_PATTERN = re.compile(r'casAssets\["([^"]+)"\]\s*=\s*"([^"]*)"')


def extract_commands_from_html(html_content: str) -> list[bytes]:
    """Extract base64-encoded msgpack commands from HTML.

    Args:
        html_content: The HTML file content as a string

    Returns:
        List of decoded msgpack bytes
    """
    matches = FETCH_PATTERN.findall(html_content)
    commands = []

    for base64_data in matches:
        try:
            decoded = base64.b64decode(base64_data)
            commands.append(decoded)
        except Exception as e:
            print(f"Warning: Failed to decode base64 data: {e}")
            continue

    return commands


def extract_cas_assets(html_content: str) -> dict[str, str]:
    """Extract the casAssets dictionary from HTML.

    casAssets contains embedded textures and mesh files as data URIs,
    keyed by their SHA256 hash.

    Supports two formats:
    1. Object literal: var casAssets = {"hash": "data:...", ...};
    2. Individual assignments: casAssets["hash"] = "data:...";

    Args:
        html_content: The HTML file content as a string

    Returns:
        Dictionary mapping hash strings to data URIs
    """
    assets = {}

    # Try object literal format first
    match = CAS_ASSETS_DICT_PATTERN.search(html_content)
    if match:
        assets_str = match.group(1)
        for entry_match in ASSET_ENTRY_PATTERN.finditer(assets_str):
            key = entry_match.group(1)
            value = entry_match.group(2)
            assets[key] = value

    # Also try individual assignment format
    for entry_match in CAS_ASSETS_ASSIGNMENT_PATTERN.finditer(html_content):
        key = entry_match.group(1)
        value = entry_match.group(2)
        assets[key] = value

    return assets


def parse_commands(raw_commands: list[bytes]) -> list[Command]:
    """Parse raw msgpack bytes into Command objects.

    Args:
        raw_commands: List of msgpack-encoded command bytes

    Returns:
        List of parsed Command objects
    """
    commands = []

    for raw in raw_commands:
        try:
            decoded = decode_msgpack(raw)
            if isinstance(decoded, dict):
                cmd = Command.from_dict(decoded)
                commands.append(cmd)
        except Exception as e:
            print(f"Warning: Failed to parse command: {e}")
            continue

    return commands


def parse_html_recording(html_path: Path | str) -> dict[str, Any]:
    """Parse a complete meshcat HTML recording.

    Args:
        html_path: Path to the HTML file

    Returns:
        Dictionary containing:
        - commands: List of parsed Command objects
        - assets: Dictionary of casAssets (hash -> data URI)
        - raw_commands: List of raw decoded command dicts (for debugging)
    """
    html_path = Path(html_path)
    html_content = html_path.read_text(encoding="utf-8")

    # Extract raw command bytes
    raw_bytes = extract_commands_from_html(html_content)

    # Decode commands to dicts for inspection
    raw_commands = []
    for raw in raw_bytes:
        try:
            decoded = decode_msgpack(raw)
            raw_commands.append(decoded)
        except Exception:
            continue

    # Parse into Command objects
    commands = parse_commands(raw_bytes)

    # Extract assets
    assets = extract_cas_assets(html_content)

    # Extract animation FPS from set_animation commands
    animation_fps = 64.0  # Drake default
    for cmd in commands:
        if cmd.type.value == "set_animation":
            options = cmd.data.get("options", {})
            # Check options first: Drake uses "fps", meshcat.js uses "play_fps"
            fps = options.get("fps") or options.get("play_fps")
            # Fall back to the clip-level fps from the first animation
            if not fps:
                animations = cmd.data.get("animations", [])
                if animations:
                    fps = animations[0].get("clip", {}).get("fps")
            if fps:
                animation_fps = float(fps)
                break

    return {
        "commands": commands,
        "assets": assets,
        "raw_commands": raw_commands,
        "animation_fps": animation_fps,
    }
