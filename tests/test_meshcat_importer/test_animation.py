# SPDX-License-Identifier: MIT
"""Tests for animation module."""


class TestAnimationData:
    """Tests for animation data structures."""

    def test_animation_track_creation(self):
        """Test creating an AnimationTrack."""
        from meshcat_html_importer.animation.animation_data import (
            AnimationTrack,
            TrackType,
        )

        track = AnimationTrack(
            name="object.position",
            track_type=TrackType.POSITION,
            times=[0.0, 1.0, 2.0],
            values=[0, 0, 0, 1, 0, 0, 2, 0, 0],
        )

        assert len(track) == 3
        assert track.get_value_at(0) == (0, 0, 0)
        assert track.get_value_at(1) == (1, 0, 0)
        assert track.get_value_at(2) == (2, 0, 0)

    def test_animation_track_quaternion(self):
        """Test quaternion track values."""
        from meshcat_html_importer.animation.animation_data import (
            AnimationTrack,
            TrackType,
        )

        track = AnimationTrack(
            name="object.quaternion",
            track_type=TrackType.QUATERNION,
            times=[0.0],
            values=[0, 0, 0, 1],  # Identity quaternion (x, y, z, w)
        )

        assert track.get_value_at(0) == (0, 0, 0, 1)

    def test_animation_clip_duration(self):
        """Test animation clip duration calculation."""
        from meshcat_html_importer.animation.animation_data import (
            AnimationClip,
            AnimationTrack,
            TrackType,
        )

        clip = AnimationClip(name="test", fps=30.0)
        clip.add_track(
            AnimationTrack(
                name="object.position",
                track_type=TrackType.POSITION,
                times=[0.0, 1.0, 2.5],
                values=[0, 0, 0] * 3,
            )
        )

        assert clip.duration == 2.5
        assert clip.frame_count == 76  # 2.5 * 30 + 1


class TestKeyframeConverter:
    """Tests for keyframe conversion."""

    def test_time_to_frame(self):
        """Test time to frame conversion."""
        from meshcat_html_importer.animation.keyframe_converter import time_to_frame

        # time_to_frame(time_value, recording_fps, target_fps)
        # time_value is frame number at recording_fps
        assert time_to_frame(0, 64.0, 30.0) == 0
        assert (
            time_to_frame(64, 64.0, 30.0) == 30
        )  # 1 second at 64fps -> frame 30 at 30fps
        assert (
            time_to_frame(32, 64.0, 30.0) == 15
        )  # 0.5 second at 64fps -> frame 15 at 30fps
        assert time_to_frame(30, 30.0, 30.0) == 30  # same fps, no conversion

    def test_time_to_frame_with_offset(self):
        """Test time to frame with start offset."""
        from meshcat_html_importer.animation.keyframe_converter import time_to_frame

        assert time_to_frame(0, 64.0, 30.0, start_frame=100) == 100
        assert (
            time_to_frame(64, 64.0, 30.0, start_frame=100) == 130
        )  # 1 second -> +30 frames

    def test_convert_quaternion_to_blender(self):
        """Test quaternion format conversion."""
        from meshcat_html_importer.animation.keyframe_converter import (
            convert_quaternion_to_blender,
        )

        # Three.js format: (x, y, z, w)
        # Blender format: (w, x, y, z)
        threejs_quat = (0.1, 0.2, 0.3, 0.9)
        blender_quat = convert_quaternion_to_blender(threejs_quat)

        assert blender_quat == (0.9, 0.1, 0.2, 0.3)
