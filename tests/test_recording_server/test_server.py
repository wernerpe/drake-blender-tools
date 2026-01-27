# SPDX-License-Identifier: MIT
"""Tests for drake_recording_server.server module."""

import dataclasses as dc
import sys
from unittest.mock import MagicMock

import pytest


# Mock bpy before importing the server module
@pytest.fixture(autouse=True)
def mock_bpy():
    """Mock bpy module for tests."""
    mock = MagicMock()
    sys.modules["bpy"] = mock
    yield mock
    # Clean up to avoid polluting other tests
    if "drake_recording_server.server" in sys.modules:
        del sys.modules["drake_recording_server.server"]
    if "drake_recording_server" in sys.modules:
        del sys.modules["drake_recording_server"]
    del sys.modules["bpy"]


class TestRenderParams:
    """Tests for RenderParams dataclass."""

    def test_render_params_fields(self, mock_bpy):
        """Test that RenderParams has expected fields."""
        from drake_recording_server.server import RenderParams

        fields = {f.name for f in dc.fields(RenderParams)}
        expected = {
            "scene",
            "scene_sha256",
            "image_type",
            "width",
            "height",
            "near",
            "far",
            "focal_x",
            "focal_y",
            "fov_x",
            "fov_y",
            "center_x",
            "center_y",
            "min_depth",
            "max_depth",
        }
        assert fields == expected

    def test_render_params_optional_fields(self, mock_bpy):
        """Test that min_depth and max_depth are optional."""
        from drake_recording_server.server import RenderParams

        optional_fields = []
        for f in dc.fields(RenderParams):
            if f.default is not dc.MISSING or f.default_factory is not dc.MISSING:
                optional_fields.append(f.name)

        assert "min_depth" in optional_fields
        assert "max_depth" in optional_fields


class TestBlender:
    """Tests for Blender class."""

    def test_blender_init(self, mock_bpy, tmp_path):
        """Test Blender initialization."""
        from drake_recording_server.server import Blender

        keyframe_path = tmp_path / "keyframes.pkl"

        blender = Blender(
            blend_file=None,
            bpy_settings_file=None,
            export_path=tmp_path / "scene.blend",
            keyframe_dump_path=keyframe_path,
        )

        assert blender._keyframes == []

    def test_blender_dump_keyframes(self, mock_bpy, tmp_path):
        """Test dumping keyframes to disk."""
        import pickle

        from drake_recording_server.server import Blender

        keyframe_path = tmp_path / "keyframes.pkl"

        blender = Blender(
            keyframe_dump_path=keyframe_path,
        )

        # Add some test keyframes
        blender._keyframes = [
            [
                {
                    "name": "obj1",
                    "location": [0, 0, 0],
                    "rotation_quaternion": [0, 0, 0, 1],
                }
            ]
        ]

        blender.dump_keyframes_to_disk()

        # Verify file was written
        assert keyframe_path.exists()

        with open(keyframe_path, "rb") as f:
            loaded = pickle.load(f)

        assert len(loaded) == 1
        assert loaded[0][0]["name"] == "obj1"


class TestServerApp:
    """Tests for ServerApp Flask application."""

    def test_server_app_creation(self, mock_bpy, tmp_path):
        """Test ServerApp initialization."""
        from drake_recording_server.server import ServerApp

        app = ServerApp(
            temp_dir=str(tmp_path),
            keyframe_dump_path=tmp_path / "keyframes.pkl",
        )

        assert app is not None
        assert app.name == "drake_blender_recording_server"

    def test_root_endpoint(self, mock_bpy, tmp_path):
        """Test the root endpoint returns HTML."""
        from drake_recording_server.server import ServerApp

        app = ServerApp(
            temp_dir=str(tmp_path),
            keyframe_dump_path=tmp_path / "keyframes.pkl",
        )

        with app.test_client() as client:
            response = client.get("/")
            assert response.status_code == 200
            assert b"Drake Blender Recording Server" in response.data
