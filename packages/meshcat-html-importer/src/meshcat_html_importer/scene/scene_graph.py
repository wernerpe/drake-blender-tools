# SPDX-License-Identifier: MIT
"""Scene graph representation for meshcat data."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from meshcat_html_importer.parser.command_types import Command, CommandType
from meshcat_html_importer.scene.geometry import (
    MeshFileGeometry,
    MeshGeometry,
    PrimitiveGeometry,
    parse_geometry,
)
from meshcat_html_importer.scene.materials import ParsedMaterial, parse_material
from meshcat_html_importer.scene.transforms import (
    Transform,
    combine_transforms,
    matrix_to_trs,
    parse_transform_matrix,
)


@dataclass
class AnimationKeyframe:
    """A single keyframe for animation."""

    time: float  # Time in seconds
    position: tuple[float, float, float] | None = None
    rotation: tuple[float, float, float, float] | None = None  # Quaternion (x,y,z,w)
    scale: tuple[float, float, float] | None = None


@dataclass
class SceneNode:
    """A node in the scene graph."""

    path: str
    name: str
    transform: Transform = field(default_factory=Transform.identity)
    geometry: MeshGeometry | PrimitiveGeometry | MeshFileGeometry | None = None
    material: ParsedMaterial | None = None
    visible: bool = True
    children: dict[str, SceneNode] = field(default_factory=dict)
    parent: SceneNode | None = None
    keyframes: list[AnimationKeyframe] = field(default_factory=list)

    # Object type hints
    object_type: str = "Object3D"  # Mesh, Line, Points, etc.

    def get_world_transform(self) -> Transform:
        """Get the world transform by combining all parent transforms.

        Returns:
            Transform in world space
        """
        # Collect transforms from root to this node
        transforms = []
        node = self
        while node is not None:
            transforms.append(node.transform)
            node = node.parent

        # Combine from root (last) to leaf (first)
        transforms.reverse()

        if not transforms:
            return Transform.identity()

        world_transform = transforms[0]
        for t in transforms[1:]:
            world_transform = combine_transforms(world_transform, t)

        return world_transform


class SceneGraph:
    """Complete scene graph built from meshcat commands."""

    def __init__(self, assets: dict[str, str] | None = None):
        """Initialize scene graph.

        Args:
            assets: Dictionary of CAS assets (hash -> data URI)
        """
        self.root = SceneNode(path="/", name="root")
        self._nodes: dict[str, SceneNode] = {"/": self.root}
        self._textures: dict[str, Any] = {}
        self._animation_fps: float = 30.0
        self._assets: dict[str, str] = assets or {}

    def process_commands(self, commands: list[Command]) -> None:
        """Process a list of commands to build the scene graph.

        Args:
            commands: List of parsed Command objects
        """
        for cmd in commands:
            try:
                self._process_command(cmd)
            except Exception as e:
                print(f"Warning: Failed to process command {cmd.type}: {e}")

    def _process_command(self, cmd: Command) -> None:
        """Process a single command."""
        if cmd.type == CommandType.SET_OBJECT:
            self._handle_set_object(cmd)
        elif cmd.type == CommandType.SET_TRANSFORM:
            self._handle_set_transform(cmd)
        elif cmd.type == CommandType.DELETE:
            self._handle_delete(cmd)
        elif cmd.type == CommandType.SET_PROPERTY:
            self._handle_set_property(cmd)
        elif cmd.type == CommandType.SET_ANIMATION:
            self._handle_set_animation(cmd)

    def _get_or_create_node(self, path: str) -> SceneNode:
        """Get existing node or create node hierarchy for path."""
        if path in self._nodes:
            return self._nodes[path]

        # Create parent nodes as needed
        parts = path.strip("/").split("/")
        current_path = ""
        parent = self.root

        for part in parts:
            current_path = f"{current_path}/{part}"
            if current_path not in self._nodes:
                node = SceneNode(
                    path=current_path,
                    name=part,
                    parent=parent,
                )
                parent.children[part] = node
                self._nodes[current_path] = node
            parent = self._nodes[current_path]

        return parent

    def _handle_set_object(self, cmd: Command) -> None:
        """Handle set_object command."""
        path = cmd.path
        data = cmd.data

        # The Three.js JSON structure can have:
        # 1. geometries[] array + materials[] array + object with UUID refs
        # 2. _meshfile_object with data directly in object.object
        # cmd.data has keys: type, path, object
        # cmd.data["object"] has the actual Three.js scene data
        top_level = data.get("object", {})
        inner_obj = top_level.get("object", {})

        node = self._get_or_create_node(path)
        node.object_type = inner_obj.get("type", "Object3D")

        # Extract object-level matrix if present (contains local scale/transform)
        obj_matrix = inner_obj.get("matrix")
        if obj_matrix:
            # Parse the 4x4 matrix and apply to node transform
            obj_transform = matrix_to_trs(np.array(obj_matrix).reshape(4, 4))
            # Combine with existing transform (object matrix is the inner transform)
            node.transform = combine_transforms(node.transform, obj_transform)

        # Handle _meshfile_object type (custom meshcat format)
        if inner_obj.get("type") == "_meshfile_object":
            # Parse directly as meshfile geometry, passing CAS assets for resolution
            node.geometry = parse_geometry(
                {
                    "type": "_meshfile_geometry",
                    "format": inner_obj.get("format"),
                    "data": inner_obj.get("data"),
                    "resources": inner_obj.get("resources", {}),
                },
                cas_assets=self._assets,
            )
            # _meshfile_object typically doesn't have separate material
            return

        # Handle standard Three.js format with geometries/materials arrays
        geometries = top_level.get("geometries", [])
        materials = top_level.get("materials", [])

        # Build lookup dicts by UUID
        geom_by_uuid = {g.get("uuid"): g for g in geometries}
        mat_by_uuid = {m.get("uuid"): m for m in materials}

        # Get geometry by UUID reference
        geom_uuid = inner_obj.get("geometry")
        if geom_uuid and geom_uuid in geom_by_uuid:
            node.geometry = parse_geometry(geom_by_uuid[geom_uuid])

        # Get material by UUID reference
        mat_uuid = inner_obj.get("material")
        if mat_uuid and mat_uuid in mat_by_uuid:
            node.material = parse_material(mat_by_uuid[mat_uuid])

        # Store any textures referenced
        self._extract_textures(top_level)

    def _handle_set_transform(self, cmd: Command) -> None:
        """Handle set_transform command.

        Note: set_transform only updates pose (translation/rotation), not scale.
        The scale from set_object's object matrix is preserved. This is because
        meshcat sends identity transforms for visual nodes after set_object,
        and the actual geometry scale is in the object matrix, not the transform.
        """
        path = cmd.path
        data = cmd.data
        matrix_data = data.get("matrix")

        if matrix_data is None:
            return

        node = self._get_or_create_node(path)

        try:
            matrix = parse_transform_matrix(matrix_data)
            new_transform = matrix_to_trs(matrix)

            # Preserve existing scale from object matrix if new transform has
            # identity scale. The object matrix contains the geometry scale.
            existing_scale = node.transform.scale
            if existing_scale != (1.0, 1.0, 1.0) and new_transform.scale == (
                1.0,
                1.0,
                1.0,
            ):
                node.transform = Transform(
                    translation=new_transform.translation,
                    rotation=new_transform.rotation,
                    scale=existing_scale,
                )
            else:
                node.transform = new_transform
        except Exception as e:
            print(f"Warning: Failed to parse transform for {path}: {e}")

    def _handle_delete(self, cmd: Command) -> None:
        """Handle delete command."""
        path = cmd.path

        if path in self._nodes:
            node = self._nodes[path]
            if node.parent and node.name in node.parent.children:
                del node.parent.children[node.name]
            del self._nodes[path]

            # Also delete all children
            prefix = path + "/"
            to_delete = [p for p in self._nodes if p.startswith(prefix)]
            for p in to_delete:
                del self._nodes[p]

    def _handle_set_property(self, cmd: Command) -> None:
        """Handle set_property command."""
        path = cmd.path
        data = cmd.data
        prop = data.get("property", "")
        value = data.get("value")

        node = self._get_or_create_node(path)

        if prop == "visible":
            node.visible = bool(value)
        # Add more property handlers as needed

    def _handle_set_animation(self, cmd: Command) -> None:
        """Handle set_animation command."""
        data = cmd.data
        animations = data.get("animations", [])
        options = data.get("options", {})

        # Store FPS - Drake uses "fps" key, meshcat.js uses "play_fps"
        fps_value = options.get("fps") or options.get("play_fps") or 64.0
        self._animation_fps = float(fps_value)

        for anim in animations:
            path = anim.get("path", "")
            clip = anim.get("clip", {})
            tracks = clip.get("tracks", [])

            node = self._get_or_create_node(path)
            self._parse_animation_tracks(node, tracks)

    def _parse_animation_tracks(self, node: SceneNode, tracks: list[dict]) -> None:
        """Parse animation tracks into keyframes.

        Meshcat animation tracks use a 'keys' array with {time, value} objects,
        where time is the frame number and value is the property value.
        """
        # Collect keyframe times and values per track
        all_times: set[float] = set()
        track_data: dict[str, dict[float, Any]] = {}

        for track in tracks:
            track_name = track.get("name", "")
            keys = track.get("keys", [])

            # Build time -> value mapping for this track
            time_to_value = {}
            for key in keys:
                t = key.get("time", 0)
                value = key.get("value")
                time_to_value[t] = value
                all_times.add(t)

            track_data[track_name] = time_to_value

        # Build keyframes for each time
        sorted_times = sorted(all_times)

        for t in sorted_times:
            kf = AnimationKeyframe(time=t)

            for track_name, time_to_value in track_data.items():
                if t not in time_to_value:
                    continue

                value = time_to_value[t]
                if value is None:
                    continue

                # Convert numpy arrays to lists/tuples
                if isinstance(value, np.ndarray):
                    value = value.tolist()

                if ".position" in track_name or track_name == ".position":
                    kf.position = tuple(value)
                elif ".quaternion" in track_name or track_name == ".quaternion":
                    kf.rotation = tuple(value)
                elif ".scale" in track_name or track_name == ".scale":
                    kf.scale = tuple(value)

            node.keyframes.append(kf)

    def _extract_textures(self, obj_data: dict) -> None:
        """Extract texture data from object definition."""
        textures = obj_data.get("textures", [])
        for tex in textures:
            uuid = tex.get("uuid")
            if uuid:
                self._textures[uuid] = tex

        images = obj_data.get("images", [])
        for img in images:
            uuid = img.get("uuid")
            if uuid:
                self._textures[f"image_{uuid}"] = img

    def get_all_nodes(self) -> list[SceneNode]:
        """Get all nodes in the scene graph."""
        return list(self._nodes.values())

    def get_mesh_nodes(self) -> list[SceneNode]:
        """Get all nodes that have geometry."""
        return [n for n in self._nodes.values() if n.geometry is not None]

    def get_animated_nodes(self) -> list[SceneNode]:
        """Get all nodes that have animation keyframes."""
        return [n for n in self._nodes.values() if n.keyframes]

    def get_texture(self, uuid: str) -> dict | None:
        """Get texture data by UUID."""
        return self._textures.get(uuid)

    @property
    def animation_fps(self) -> float:
        """Get the animation FPS."""
        return self._animation_fps
