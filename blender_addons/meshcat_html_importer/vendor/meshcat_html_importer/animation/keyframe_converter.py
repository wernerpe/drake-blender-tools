# SPDX-License-Identifier: MIT
"""Convert animation keyframes between formats."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from meshcat_html_importer.scene.scene_graph import AnimationKeyframe, SceneNode


@dataclass
class BlenderKeyframe:
    """A keyframe in Blender format."""

    frame: int
    location: tuple[float, float, float] | None = None
    rotation_quaternion: tuple[float, float, float, float] | None = None  # (w,x,y,z)
    scale: tuple[float, float, float] | None = None


def convert_quaternion_to_blender(
    quat: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Convert quaternion from Three.js (x,y,z,w) to Blender (w,x,y,z).

    Args:
        quat: Quaternion in Three.js format (x, y, z, w)

    Returns:
        Quaternion in Blender format (w, x, y, z)
    """
    x, y, z, w = quat
    return (w, x, y, z)


def time_to_frame(
    time_value: float,
    recording_fps: float,
    target_fps: float,
    start_frame: int = 0,
) -> int:
    """Convert recording time to target frame number.

    Args:
        time_value: Time value from recording (frame number at recording_fps)
        recording_fps: FPS of the original recording
        target_fps: Target FPS for Blender
        start_frame: Starting frame offset

    Returns:
        Frame number at target FPS
    """
    # Convert recording frame to seconds, then to target frame
    time_seconds = time_value / recording_fps
    return start_frame + int(round(time_seconds * target_fps))


def downsample_keyframes(
    keyframes: list[AnimationKeyframe],
    recording_fps: float,
    target_fps: float,
) -> list[AnimationKeyframe]:
    """Downsample keyframes from recording FPS to target FPS.

    Selects keyframes at regular intervals to match target FPS,
    using nearest-neighbor sampling.

    Args:
        keyframes: Original keyframes at recording_fps
        recording_fps: FPS of the original recording
        target_fps: Target FPS for output

    Returns:
        Downsampled list of keyframes
    """
    if not keyframes or target_fps >= recording_fps:
        return keyframes

    # Sort by time
    sorted_kfs = sorted(keyframes, key=lambda kf: kf.time)

    if len(sorted_kfs) < 2:
        return sorted_kfs

    # Get time range
    min_time = sorted_kfs[0].time
    max_time = sorted_kfs[-1].time
    duration_seconds = (max_time - min_time) / recording_fps

    # Calculate target frame count
    target_frame_count = int(duration_seconds * target_fps) + 1

    # Sample at regular intervals
    result = []
    time_by_kf = {kf.time: kf for kf in sorted_kfs}
    all_times = sorted(time_by_kf.keys())

    for target_frame in range(target_frame_count):
        target_time_seconds = target_frame / target_fps
        target_recording_time = min_time + target_time_seconds * recording_fps

        # Find nearest keyframe
        nearest_time = min(all_times, key=lambda t: abs(t - target_recording_time))
        nearest_kf = time_by_kf[nearest_time]

        # Create new keyframe with adjusted time (as target frame number)
        from meshcat_html_importer.scene.scene_graph import AnimationKeyframe

        new_kf = AnimationKeyframe(
            time=float(target_frame),  # Now in target frames
            position=nearest_kf.position,
            rotation=nearest_kf.rotation,
            scale=nearest_kf.scale,
        )
        result.append(new_kf)

    return result


def convert_keyframes_to_blender(
    keyframes: list[AnimationKeyframe],
    recording_fps: float = 1000.0,
    target_fps: float = 30.0,
    start_frame: int = 0,
    downsample: bool = True,
) -> list[BlenderKeyframe]:
    """Convert meshcat keyframes to Blender format.

    Args:
        keyframes: List of AnimationKeyframe from scene node
        recording_fps: FPS of the original recording (default 1000 for Drake simulations)
        target_fps: Target FPS for Blender animation
        start_frame: Starting frame number
        downsample: Whether to downsample to target FPS

    Returns:
        List of BlenderKeyframe objects
    """
    if not keyframes:
        return []

    # Downsample if requested
    if downsample:
        processed_kfs = downsample_keyframes(keyframes, recording_fps, target_fps)
    else:
        processed_kfs = keyframes

    blender_keyframes = []

    for kf in processed_kfs:
        # After downsampling, time is already in target frames
        if downsample:
            frame = start_frame + int(round(kf.time))
        else:
            frame = time_to_frame(kf.time, recording_fps, target_fps, start_frame)

        # Convert quaternion format
        rotation = None
        if kf.rotation is not None:
            rotation = convert_quaternion_to_blender(kf.rotation)

        blender_kf = BlenderKeyframe(
            frame=frame,
            location=kf.position,
            rotation_quaternion=rotation,
            scale=kf.scale,
        )
        blender_keyframes.append(blender_kf)

    return blender_keyframes


def get_animation_range(
    nodes: list[SceneNode],
    recording_fps: float = 1000.0,
    target_fps: float = 30.0,
    start_frame: int = 0,
) -> tuple[int, int]:
    """Get the frame range for all animations at target FPS.

    Args:
        nodes: List of scene nodes with keyframes
        recording_fps: FPS of the original recording
        target_fps: Target FPS for Blender
        start_frame: Starting frame number

    Returns:
        Tuple of (start_frame, end_frame)
    """
    min_frame = start_frame
    max_time = 0

    for node in nodes:
        for kf in node.keyframes:
            max_time = max(max_time, kf.time)

    # Convert max time to target frame
    duration_seconds = max_time / recording_fps
    max_frame = start_frame + int(round(duration_seconds * target_fps))

    return (min_frame, max_frame)
