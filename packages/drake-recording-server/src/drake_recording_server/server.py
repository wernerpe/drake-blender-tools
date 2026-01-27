# SPDX-License-Identifier: MIT
# Modified from https://github.com/RobotLocomotion/drake-blender/blob/main/server.py
"""Flask server for recording Drake simulation states as Blender keyframes."""

from __future__ import annotations

import dataclasses as dc
import datetime
import io
import math
import pickle
import tempfile
import typing
from pathlib import Path
from types import NoneType

import bpy
import flask
from PIL import Image


@dc.dataclass
class RenderParams:
    """Encapsulates all parameters to render a color, depth, or label image.

    https://drake.mit.edu/doxygen_cxx/group__render__engine__gltf__client__server__api.html#render-endpoint-form-data
    """

    scene: Path
    """The glTF input file."""

    scene_sha256: str
    """The checksum of `scene`."""

    image_type: typing.Literal["color", "depth", "label"]
    """The type of image being rendered."""

    width: int
    """Width of the desired rendered image in pixels."""

    height: int
    """Height of the desired rendered image in pixels."""

    near: float
    """The near clipping plane of the camera."""

    far: float
    """The far clipping plane of the camera."""

    focal_x: float
    """The focal length x, in pixels."""

    focal_y: float
    """The focal length y, in pixels."""

    fov_x: float
    """The field of view in the x-direction (in radians)."""

    fov_y: float
    """The field of view in the y-direction (in radians)."""

    center_x: float
    """The principal point's x coordinate in pixels."""

    center_y: float
    """The principal point's y coordinate in pixels."""

    min_depth: typing.Optional[float] = None
    """The minimum depth range. Only provided when image_type='depth'."""

    max_depth: typing.Optional[float] = None
    """The maximum depth range. Only provided when image_type='depth'."""


class Blender:
    """Encapsulates access to Blender.

    Note that even though this is a class, bpy is a singleton so likewise you
    should only ever create one instance of this class.
    """

    def __init__(
        self,
        *,
        blend_file: Path | None = None,
        bpy_settings_file: Path | None = None,
        export_path: Path | None = None,
        keyframe_dump_path: Path | None = None,
    ):
        self._blend_file = blend_file
        self._bpy_settings_file = bpy_settings_file
        self._export_path = export_path
        self._keyframe_dump_path = keyframe_dump_path

        self._keyframes: list[list[dict]] = []

        if self._keyframe_dump_path and self._keyframe_dump_path.exists():
            response = input(
                f"Keyframe dump path {self._keyframe_dump_path} already exists. Do "
                "you want to delete it? [y/N]: "
            )
            if response.lower() == "y":
                self._keyframe_dump_path.unlink()
            else:
                raise ValueError(
                    f"Keyframe dump path {self._keyframe_dump_path} already exists "
                    "and user chose not to delete it."
                )

    def reset_scene(self) -> None:
        """Reset the scene by loading factory settings and removing default objects."""
        bpy.ops.wm.read_factory_settings()
        for item in bpy.data.objects:
            item.select_set(True)
        bpy.ops.object.delete()

    def save_keyframe(self, *, params: RenderParams) -> None:
        """Save the current object poses as a keyframe."""
        # Load the blend file to set up the basic scene if provided.
        if self._blend_file is not None:
            bpy.ops.wm.open_mainfile(filepath=str(self._blend_file))
        else:
            self.reset_scene()

        # Apply the user's custom settings.
        if self._bpy_settings_file:
            with open(self._bpy_settings_file) as f:
                code = compile(f.read(), self._bpy_settings_file, "exec")
                exec(code, {"bpy": bpy}, dict())

        self._client_objects = bpy.data.collections.new("ClientObjects")
        old_count = len(bpy.data.objects)

        # Import a glTF file. Note that the Blender glTF importer imposes a
        # +90 degree rotation around the X-axis when loading meshes. Thus, we
        # counterbalance the rotation right after the glTF-loading.
        bpy.ops.import_scene.gltf(filepath=str(params.scene))
        new_count = len(bpy.data.objects)

        # Reality check that all of the imported objects are selected by default.
        assert new_count - old_count == len(bpy.context.selected_objects)

        # Rotate to compensate for glTF coordinate system difference.
        bpy.ops.transform.rotate(
            value=math.pi / 2,
            orient_axis="X",
            orient_type="GLOBAL",
            center_override=(0, 0, 0),
        )

        # Store the poses of the newly imported objects.
        imported_objects = bpy.context.selected_objects
        frame_data = []
        for obj in imported_objects:
            pose_data = {
                "name": obj.name,
                "location": list(obj.location),
                "rotation_quaternion": list(obj.rotation_quaternion),
            }
            frame_data.append(pose_data)
        self._keyframes.append(frame_data)

        # Create a new collection for imported objects and move them there.
        drake_objects = bpy.data.collections.new("DrakeObjects")
        bpy.context.scene.collection.children.link(drake_objects)
        for obj in bpy.context.selected_objects:
            # Unlink from current collections.
            for coll in obj.users_collection:
                coll.objects.unlink(obj)
            # Link to our collection.
            drake_objects.objects.link(obj)

        # Export the first scene.
        if self._export_path is not None and len(self._keyframes) == 1:
            self._export_path.parent.mkdir(parents=True, exist_ok=True)
            bpy.ops.wm.save_as_mainfile(filepath=str(self._export_path))

    def dump_keyframes_to_disk(self) -> None:
        """Write accumulated keyframes to disk as a pickle file."""
        if self._keyframe_dump_path:
            with open(self._keyframe_dump_path, "wb") as f:
                pickle.dump(self._keyframes, f)


class ServerApp(flask.Flask):
    """Flask server application for recording Drake simulation states.

    This server implements Drake's glTF Render Client-Server API but
    instead of rendering an image, it saves the object poses as keyframes.
    """

    def __init__(
        self,
        *,
        temp_dir: str,
        blend_file: Path | None = None,
        bpy_settings_file: Path | None = None,
        export_path: Path | None = None,
        keyframe_dump_path: Path | None = None,
    ):
        super().__init__("drake_blender_recording_server")

        self._temp_dir = temp_dir
        self._blender = Blender(
            blend_file=blend_file,
            bpy_settings_file=bpy_settings_file,
            export_path=export_path,
            keyframe_dump_path=keyframe_dump_path,
        )

        self.add_url_rule("/", view_func=self._root_endpoint)
        self.add_url_rule(
            rule="/render",
            endpoint="/render",
            methods=["POST"],
            view_func=self._render_endpoint,
        )

    def _root_endpoint(self) -> str:
        """Display a banner page at the server root."""
        return """\
        <!doctype html>
        <html><body><h1>Drake Blender Recording Server</h1></body></html>
        """

    def _render_endpoint(self):
        """Accept a request to render and return a generated image.

        NOTE: In practice this endpoint saves the object poses and returns a
        fake image to satisfy the caller.
        """
        try:
            params = self._parse_params(flask.request)
            self._save_keyframe(params)

            # Create the fake image.
            img = Image.new("RGB", (params.width, params.height), color="black")
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            buffer.seek(0)

            return flask.send_file(buffer, mimetype="image/png")
        except Exception as e:
            code = 500
            message = f"Internal server error: {repr(e)}"
            return (
                {
                    "error": True,
                    "message": message,
                    "code": code,
                },
                code,
            )

    def _parse_params(self, request: flask.Request) -> RenderParams:
        """Convert an HTTP request to a RenderParams."""
        result = dict()

        # Compute a lookup table for known form field names.
        param_fields = {x.name: x for x in dc.fields(RenderParams)}
        del param_fields["scene"]

        # Copy all of the form data into the result.
        for name, value in request.form.items():
            if name == "submit":
                # Ignore the HTML boilerplate.
                continue
            field = param_fields[name]
            type_origin = typing.get_origin(field.type)
            type_args = typing.get_args(field.type)
            if field.type in (int, float, str):
                result[name] = field.type(value)
            elif type_origin == typing.Literal:
                if value not in type_args:
                    raise ValueError(f"Invalid literal for {name}")
                result[name] = value
            elif type_origin == typing.Union:
                # Handle typing.Optional (Union[T, None]).
                assert len(type_args) == 2
                assert type_args[1] == NoneType
                result[name] = type_args[0](value)
            else:
                raise NotImplementedError(name)

        # Save the glTF scene data.
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
        scene = Path(f"{self._temp_dir}/{timestamp}.gltf")
        assert len(request.files) == 1
        request.files["scene"].save(scene)
        result["scene"] = scene

        return RenderParams(**result)

    def _save_keyframe(self, params: RenderParams) -> None:
        """Save the current object poses as a keyframe and dump to disk."""
        self._blender.save_keyframe(params=params)
        print(f"Saved keyframe {len(self._blender._keyframes)}")

        # Clean up the temporary glTF file.
        params.scene.unlink(missing_ok=True)

        self._blender.dump_keyframes_to_disk()


def run_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    blend_file: Path | None = None,
    bpy_settings_file: Path | None = None,
    export_path: Path,
    keyframe_dump_path: Path,
) -> None:
    """Run the recording server."""
    prefix = "drake_blender_recorder_"
    with tempfile.TemporaryDirectory(prefix=prefix) as temp_dir:
        app = ServerApp(
            temp_dir=temp_dir,
            blend_file=blend_file,
            bpy_settings_file=bpy_settings_file,
            export_path=export_path,
            keyframe_dump_path=keyframe_dump_path,
        )
        app.run(host=host, port=port, threaded=False)
