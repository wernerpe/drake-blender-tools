# SPDX-License-Identifier: MIT
"""Blender operators for meshcat HTML import.

This module provides two import paths:
1. When meshcat_html_importer package is installed, uses the full-featured package
2. Falls back to a self-contained implementation when package is unavailable

Both paths now have feature parity including:
- Animation inheritance from parent nodes
- Path exclusion filters (contact_forces, proximity, inertia)
- Better object naming
- glTF mesh joining
- Material preservation for meshfile imports
- World transform computation from node hierarchy
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper

# Try to import the full package first
_USE_PACKAGE = False
try:
    from meshcat_html_importer.blender.scene_builder import build_scene_from_file

    _USE_PACKAGE = True
except ImportError:
    pass


class IMPORT_OT_meshcat_html(Operator, ImportHelper):
    """Import a meshcat HTML recording."""

    bl_idname = "import_scene.meshcat_html"
    bl_label = "Import Meshcat HTML"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".html"
    filter_glob: StringProperty(default="*.html;*.htm", options={"HIDDEN"})

    recording_fps: FloatProperty(
        name="Recording FPS",
        description="FPS of the original recording (0 = auto-detect from file, Drake default: 64)",
        default=0.0,
        min=0.0,
        max=10000.0,
    )

    target_fps: FloatProperty(
        name="Target FPS",
        description="Target FPS for Blender animation",
        default=30.0,
        min=1.0,
        max=120.0,
    )

    start_frame: IntProperty(
        name="Start Frame",
        description="Starting frame number",
        default=0,
        min=0,
    )

    clear_scene: BoolProperty(
        name="Clear Scene",
        description="Remove existing objects before import",
        default=True,
    )

    def execute(self, context):
        if _USE_PACKAGE:
            return self._execute_with_package(context)
        else:
            return self._execute_fallback(context)

    def _execute_with_package(self, context):
        """Execute using the meshcat_html_importer package."""
        try:
            recording_fps = self.recording_fps if self.recording_fps > 0 else None

            created_objects = build_scene_from_file(
                self.filepath,
                recording_fps=recording_fps,
                target_fps=self.target_fps,
                start_frame=self.start_frame,
                clear_scene=self.clear_scene,
            )

            animation_count = sum(
                1
                for obj in created_objects.values()
                if obj.animation_data and obj.animation_data.action
            )

            from meshcat_html_importer.parser import parse_html_recording

            scene_data = parse_html_recording(self.filepath)
            actual_fps = recording_fps or scene_data.get("animation_fps", 64.0)

            self.report(
                {"INFO"},
                f"Imported {len(created_objects)} objects, "
                f"{animation_count} animations "
                f"(Recording: {actual_fps} FPS, Target: {self.target_fps} FPS) "
                f"[using package]",
            )
            return {"FINISHED"}
        except Exception as e:
            self.report({"ERROR"}, f"Import failed: {str(e)}")
            import traceback

            traceback.print_exc()
            return {"CANCELLED"}

    def _execute_fallback(self, context):
        """Execute using the self-contained fallback implementation."""
        try:
            from .vendor import msgpack
        except ImportError:
            try:
                import msgpack
            except ImportError:
                self.report(
                    {"ERROR"},
                    "msgpack library not found. Install meshcat_html_importer package "
                    "or ensure msgpack is available.",
                )
                return {"CANCELLED"}

        try:
            result = _import_meshcat_html_fallback(
                self.filepath,
                recording_fps=self.recording_fps if self.recording_fps > 0 else None,
                target_fps=self.target_fps,
                start_frame=self.start_frame,
                clear_scene=self.clear_scene,
            )
            self.report(
                {"INFO"},
                f"Imported {result['object_count']} objects, "
                f"{result['animation_count']} animations "
                f"(Recording: {result['recording_fps']} FPS, Target: {self.target_fps} FPS) "
                f"[using fallback]",
            )
            return {"FINISHED"}
        except Exception as e:
            self.report({"ERROR"}, f"Import failed: {str(e)}")
            import traceback

            traceback.print_exc()
            return {"CANCELLED"}

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "recording_fps")
        layout.prop(self, "target_fps")
        layout.prop(self, "start_frame")
        layout.prop(self, "clear_scene")

        box = layout.box()
        if _USE_PACKAGE:
            box.label(text="Using: meshcat_html_importer package", icon="CHECKMARK")
        else:
            box.label(text="Using: built-in fallback", icon="INFO")


def register():
    """Register operators."""
    bpy.utils.register_class(IMPORT_OT_meshcat_html)


def unregister():
    """Unregister operators."""
    bpy.utils.unregister_class(IMPORT_OT_meshcat_html)


# ============================================================================
# Fallback implementation with full feature parity
# ============================================================================

import base64
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from .vendor import msgpack
except ImportError:
    try:
        import msgpack
    except ImportError:
        msgpack = None

# Extension type codes for typed arrays
EXT_UINT8_ARRAY = 0x12
EXT_INT32_ARRAY = 0x15
EXT_UINT32_ARRAY = 0x16
EXT_FLOAT32_ARRAY = 0x17

FETCH_PATTERN = re.compile(
    r'fetch\s*\(\s*["\']data:application/octet-binary;base64,([A-Za-z0-9+/=]+)["\']\s*\)'
)

# Pattern to match casAssets assignments
CAS_ASSETS_ASSIGNMENT_PATTERN = re.compile(r'casAssets\["([^"]+)"\]\s*=\s*"([^"]*)"')

# Data URI pattern
DATA_URI_PATTERN = re.compile(r"data:([^;,]+)(?:;([^,]+))?,(.+)", re.DOTALL)

# Path prefixes to exclude
EXCLUDED_PATH_PREFIXES = (
    "/drake/contact_forces/",
    "/drake/proximity/",
    "/drake/inertia/",
)

ILLUSTRATION_PREFIX = "/drake/illustration/"


def decode_typed_array(code: int, data: bytes):
    """Decode msgpack extension to list."""
    import struct

    if code == EXT_UINT8_ARRAY:
        return list(data)
    elif code == EXT_INT32_ARRAY:
        count = len(data) // 4
        return list(struct.unpack(f"<{count}i", data))
    elif code == EXT_UINT32_ARRAY:
        count = len(data) // 4
        return list(struct.unpack(f"<{count}I", data))
    elif code == EXT_FLOAT32_ARRAY:
        count = len(data) // 4
        return list(struct.unpack(f"<{count}f", data))
    return data


def decode_msgpack_data(data: bytes) -> Any:
    """Decode msgpack with typed array support."""
    return msgpack.unpackb(
        data,
        ext_hook=decode_typed_array,
        raw=False,
        strict_map_key=False,
    )


@dataclass
class Transform:
    """Decomposed transform with translation, rotation (quaternion xyzw), and scale."""

    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)  # (x, y, z, w)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)

    @classmethod
    def identity(cls) -> "Transform":
        return cls()

    def to_blender_matrix(self):
        """Convert to Blender Matrix."""
        import mathutils

        # Blender quaternion is (w, x, y, z)
        x, y, z, w = self.rotation
        quat = mathutils.Quaternion((w, x, y, z))
        mat = mathutils.Matrix.LocRotScale(
            mathutils.Vector(self.translation),
            quat,
            mathutils.Vector(self.scale),
        )
        return mat


@dataclass
class SceneNode:
    """A node in the scene hierarchy with parent reference."""

    path: str
    name: str
    transform: Transform = field(default_factory=Transform.identity)
    geometry: dict | None = None
    material: dict | None = None
    visible: bool = True
    keyframes: list[dict] = field(default_factory=list)
    is_meshfile: bool = False
    parent: "SceneNode | None" = None
    children: dict[str, "SceneNode"] = field(default_factory=dict)

    def get_world_transform(self) -> Transform:
        """Get the world transform by combining all parent transforms."""
        transforms = []
        node = self
        while node is not None:
            transforms.append(node.transform)
            node = node.parent

        transforms.reverse()

        if not transforms:
            return Transform.identity()

        # Combine transforms using matrix multiplication
        import mathutils

        combined_mat = mathutils.Matrix.Identity(4)
        for t in transforms:
            combined_mat = combined_mat @ t.to_blender_matrix()

        return matrix_to_transform(combined_mat)


class SceneGraph:
    """Scene graph with full hierarchy support."""

    def __init__(self):
        self.root = SceneNode(path="/", name="root")
        self._nodes: dict[str, SceneNode] = {"/": self.root}
        self._animation_fps: float = 64.0

    def get_or_create_node(self, path: str) -> SceneNode:
        """Get existing node or create node hierarchy for path."""
        if path in self._nodes:
            return self._nodes[path]

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

    def get_node(self, path: str) -> SceneNode | None:
        return self._nodes.get(path)

    def get_all_nodes(self) -> list[SceneNode]:
        return list(self._nodes.values())

    def get_mesh_nodes(self) -> list[SceneNode]:
        return [n for n in self._nodes.values() if n.geometry is not None]

    def get_animated_nodes(self) -> list[SceneNode]:
        return [n for n in self._nodes.values() if n.keyframes]

    @property
    def animation_fps(self) -> float:
        return self._animation_fps

    @animation_fps.setter
    def animation_fps(self, value: float):
        self._animation_fps = value


def matrix_to_transform(mat) -> Transform:
    """Convert Blender matrix to Transform."""
    loc, rot, scale = mat.decompose()
    # rot is Blender Quaternion (w, x, y, z), convert to (x, y, z, w)
    return Transform(
        translation=(loc.x, loc.y, loc.z),
        rotation=(rot.x, rot.y, rot.z, rot.w),
        scale=(scale.x, scale.y, scale.z),
    )


def matrix_to_trs(matrix: list[float]):
    """Decompose column-major 4x4 matrix to (loc, rot, scale) Blender types."""
    import mathutils

    mat = mathutils.Matrix(
        [
            [matrix[0], matrix[4], matrix[8], matrix[12]],
            [matrix[1], matrix[5], matrix[9], matrix[13]],
            [matrix[2], matrix[6], matrix[10], matrix[14]],
            [matrix[3], matrix[7], matrix[11], matrix[15]],
        ]
    )

    loc, rot, scale = mat.decompose()
    return loc, rot, scale


def list_to_transform(matrix: list[float]) -> Transform:
    """Convert column-major matrix list to Transform."""
    loc, rot, scale = matrix_to_trs(matrix)
    return Transform(
        translation=(loc.x, loc.y, loc.z),
        rotation=(rot.x, rot.y, rot.z, rot.w),
        scale=(scale.x, scale.y, scale.z),
    )


def compose_matrix(loc, rot, scale) -> list[float]:
    """Compose translation, rotation, scale into column-major 4x4 matrix."""
    import mathutils

    mat = mathutils.Matrix.LocRotScale(loc, rot, scale)

    return [
        mat[0][0],
        mat[1][0],
        mat[2][0],
        mat[3][0],
        mat[0][1],
        mat[1][1],
        mat[2][1],
        mat[3][1],
        mat[0][2],
        mat[1][2],
        mat[2][2],
        mat[3][2],
        mat[0][3],
        mat[1][3],
        mat[2][3],
        mat[3][3],
    ]


def combine_transforms(parent: Transform, child: Transform) -> Transform:
    """Combine parent and child transforms."""
    combined_mat = parent.to_blender_matrix() @ child.to_blender_matrix()
    return matrix_to_transform(combined_mat)


def extract_commands(html_content: str) -> list[dict]:
    """Extract and decode msgpack commands from HTML."""
    matches = FETCH_PATTERN.findall(html_content)
    commands = []

    for b64_data in matches:
        try:
            raw = base64.b64decode(b64_data)
            decoded = decode_msgpack_data(raw)
            if isinstance(decoded, dict):
                commands.append(decoded)
        except Exception as e:
            print(f"Warning: Failed to decode command: {e}")

    return commands


def extract_cas_assets(html_content: str) -> dict[str, bytes]:
    """Extract CAS (Content-Addressable Storage) assets from HTML.

    These are embedded textures and mesh files keyed by hash.
    """
    assets = {}

    for match in CAS_ASSETS_ASSIGNMENT_PATTERN.finditer(html_content):
        key = match.group(1)
        data_uri = match.group(2)

        # Parse data URI
        uri_match = DATA_URI_PATTERN.match(data_uri)
        if uri_match:
            encoding = uri_match.group(2)
            data_str = uri_match.group(3)

            try:
                if encoding == "base64":
                    assets[key] = base64.b64decode(data_str)
                else:
                    assets[key] = data_str.encode("utf-8")
            except Exception:
                pass

    return assets


def _should_skip_path(path: str) -> bool:
    """Check if a path should be skipped during import."""
    for prefix in EXCLUDED_PATH_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


def _derive_object_name(path: str) -> str:
    """Derive a descriptive object name from a scene graph path."""
    parts = path.strip("/").split("/")

    if parts and parts[0] == "drake":
        parts = parts[1:]
    if parts and parts[0] == "illustration":
        parts = parts[1:]

    if not parts:
        return "Object"

    model_name = parts[0] if parts else "Object"

    if len(parts) > 1:
        last_part = parts[-1]
        if last_part not in ("visual", "collision", "base_link"):
            if "room_geometry" in model_name:
                return last_part
            if last_part != model_name:
                return f"{model_name}_{last_part}"

    return model_name


def build_scene_graph(commands: list[dict]) -> SceneGraph:
    """Build scene graph from commands with full hierarchy."""
    scene_graph = SceneGraph()

    for cmd in commands:
        cmd_type = cmd.get("type", "")
        path = cmd.get("path", "")

        if cmd_type == "set_animation":
            # Handle animation command (no path filtering)
            animations = cmd.get("animations", [])
            options = cmd.get("options", {})

            fps = options.get("fps") or options.get("play_fps")
            if fps:
                scene_graph.animation_fps = float(fps)

            for anim in animations:
                anim_path = anim.get("path", "")
                clip = anim.get("clip", {})
                if anim_path:
                    node = scene_graph.get_or_create_node(anim_path)
                    node.keyframes.append(clip)
            continue

        if not path:
            continue

        # Skip excluded paths for geometry
        if _should_skip_path(path):
            continue

        node = scene_graph.get_or_create_node(path)

        if cmd_type == "set_object":
            obj_data = cmd.get("object", {})
            inner_obj = obj_data.get("object", {})

            if inner_obj.get("type") == "_meshfile_object":
                node.geometry = {
                    "type": "_meshfile_geometry",
                    "format": inner_obj.get("format"),
                    "data": inner_obj.get("data"),
                    "resources": inner_obj.get("resources", {}),
                }
                node.is_meshfile = True
            else:
                geometries = obj_data.get("geometries", [])
                geom_uuid = inner_obj.get("geometry")
                if geometries and geom_uuid:
                    geom_by_uuid = {g.get("uuid"): g for g in geometries}
                    node.geometry = geom_by_uuid.get(geom_uuid)
                else:
                    node.geometry = obj_data.get("geometry")

                materials = obj_data.get("materials", [])
                mat_uuid = inner_obj.get("material")
                if materials and mat_uuid:
                    mat_by_uuid = {m.get("uuid"): m for m in materials}
                    node.material = mat_by_uuid.get(mat_uuid)
                else:
                    node.material = obj_data.get("material")

            # Extract object matrix (contains scale)
            obj_matrix = inner_obj.get("matrix")
            if obj_matrix:
                if hasattr(obj_matrix, "tolist"):
                    obj_matrix = obj_matrix.tolist()
                node.transform = list_to_transform(obj_matrix)

            # Update name
            node.name = _derive_object_name(path)

        elif cmd_type == "set_transform":
            matrix = cmd.get("matrix")
            if matrix:
                if hasattr(matrix, "tolist"):
                    matrix = matrix.tolist()

                new_transform = list_to_transform(matrix)
                existing_scale = node.transform.scale

                # Check if new scale is identity
                is_identity_scale = all(
                    abs(s - 1.0) < 1e-6 for s in new_transform.scale
                )
                has_existing_scale = any(abs(s - 1.0) > 1e-6 for s in existing_scale)

                if is_identity_scale and has_existing_scale:
                    # Preserve existing scale
                    node.transform = Transform(
                        translation=new_transform.translation,
                        rotation=new_transform.rotation,
                        scale=existing_scale,
                    )
                else:
                    node.transform = new_transform

        elif cmd_type == "set_property":
            prop = cmd.get("property", "")
            value = cmd.get("value")
            if prop == "visible":
                node.visible = bool(value)

    return scene_graph


def _find_animation_node(scene_graph: SceneGraph, path: str) -> SceneNode | None:
    """Find the animation node for a given path (searches ancestors)."""
    node = scene_graph.get_node(path)
    if node and node.keyframes:
        return node

    # Search ancestors
    parts = path.strip("/").split("/")
    for i in range(len(parts) - 1, 0, -1):
        ancestor_path = "/" + "/".join(parts[:i])
        ancestor = scene_graph.get_node(ancestor_path)
        if ancestor and ancestor.keyframes:
            return ancestor

    return None


def _get_local_offset_from_ancestor(
    obj_node: SceneNode | None,
    anim_node: SceneNode | None,
) -> Transform | None:
    """Get the local transform offset from animation node to object node."""
    if obj_node is None or anim_node is None:
        return None

    if obj_node.path == anim_node.path:
        return None

    # Collect transforms from anim_node to obj_node
    combined = Transform.identity()
    current = obj_node
    while current is not None and current.path != anim_node.path:
        combined = combine_transforms(current.transform, combined)
        current = current.parent

    if current is None:
        return None

    return combined


def create_mesh_from_geometry(
    name: str,
    geom: dict,
    cas_assets: dict[str, bytes] | None = None,
) -> bpy.types.Object | None:
    """Create Blender mesh from geometry data."""
    geom_type = geom.get("type", "")

    if geom_type == "BufferGeometry":
        return create_buffer_geometry_mesh(name, geom)
    elif geom_type in ("BoxGeometry", "BoxBufferGeometry"):
        return create_box_mesh(name, geom)
    elif geom_type in ("SphereGeometry", "SphereBufferGeometry"):
        return create_sphere_mesh(name, geom)
    elif geom_type in ("CylinderGeometry", "CylinderBufferGeometry"):
        return create_cylinder_mesh(name, geom)
    elif geom_type == "_meshfile_geometry":
        return create_meshfile_mesh(name, geom, cas_assets)

    return None


def create_buffer_geometry_mesh(name: str, geom: dict) -> bpy.types.Object | None:
    """Create mesh from BufferGeometry."""
    data = geom.get("data", {})
    attributes = data.get("attributes", {})

    position_attr = attributes.get("position", {})
    positions = position_attr.get("array", [])

    if not positions:
        return None

    if hasattr(positions, "tolist"):
        positions = positions.tolist()

    num_verts = len(positions) // 3
    vertices = [
        (positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2])
        for i in range(num_verts)
    ]

    index_data = data.get("index", {})
    indices = index_data.get("array", [])
    if hasattr(indices, "tolist"):
        indices = indices.tolist()

    if indices:
        num_tris = len(indices) // 3
        faces = [
            (indices[i * 3], indices[i * 3 + 1], indices[i * 3 + 2])
            for i in range(num_tris)
        ]
    else:
        num_tris = num_verts // 3
        faces = [(i * 3, i * 3 + 1, i * 3 + 2) for i in range(num_tris)]

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()

    return bpy.data.objects.new(name, mesh)


def create_box_mesh(name: str, geom: dict) -> bpy.types.Object:
    """Create box mesh."""
    w = geom.get("width", 1.0) / 2
    h = geom.get("height", 1.0) / 2
    d = geom.get("depth", 1.0) / 2

    vertices = [
        (-w, -h, -d),
        (w, -h, -d),
        (w, h, -d),
        (-w, h, -d),
        (-w, -h, d),
        (w, -h, d),
        (w, h, d),
        (-w, h, d),
    ]
    faces = [
        (0, 1, 2, 3),
        (4, 7, 6, 5),
        (0, 4, 5, 1),
        (2, 6, 7, 3),
        (0, 3, 7, 4),
        (1, 5, 6, 2),
    ]

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()

    return bpy.data.objects.new(name, mesh)


def create_sphere_mesh(name: str, geom: dict) -> bpy.types.Object:
    """Create sphere mesh."""
    import bmesh

    radius = geom.get("radius", 1.0)
    segments = geom.get("widthSegments", 32)
    rings = geom.get("heightSegments", 16)

    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=rings, radius=radius)
    bm.to_mesh(mesh)
    bm.free()

    return bpy.data.objects.new(name, mesh)


def create_cylinder_mesh(name: str, geom: dict) -> bpy.types.Object:
    """Create cylinder mesh."""
    import bmesh

    radius_top = geom.get("radiusTop", 1.0)
    radius_bottom = geom.get("radiusBottom", 1.0)
    height = geom.get("height", 1.0)
    segments = geom.get("radialSegments", 32)

    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=segments,
        radius1=radius_bottom,
        radius2=radius_top,
        depth=height,
    )
    bm.to_mesh(mesh)
    bm.free()

    return bpy.data.objects.new(name, mesh)


def create_meshfile_mesh(
    name: str,
    geom: dict,
    cas_assets: dict[str, bytes] | None = None,
) -> bpy.types.Object | None:
    """Import embedded mesh file with proper mesh joining."""
    import json

    fmt = geom.get("format", "").lower()
    data = geom.get("data")

    if not data:
        return None

    # For glTF format, we need to resolve CAS asset references in the JSON
    # before converting data to bytes
    cas_resources = {}

    if fmt == "gltf" and isinstance(data, str) and cas_assets:
        try:
            gltf = json.loads(data)

            # Resolve buffer URIs that reference CAS assets
            buffers = gltf.get("buffers") or []
            for buffer in buffers:
                uri = buffer.get("uri", "")
                if uri.startswith("cas-v1/"):
                    if uri in cas_assets:
                        cas_resources[uri] = cas_assets[uri]

            # Resolve image URIs that reference CAS assets
            images = gltf.get("images") or []
            for image in images:
                uri = image.get("uri", "")
                if uri.startswith("cas-v1/"):
                    if uri in cas_assets:
                        cas_resources[uri] = cas_assets[uri]

            # Keep data as string for now, will convert below
        except json.JSONDecodeError:
            pass

    # Convert data to bytes
    if isinstance(data, str):
        # For glTF format, check if data is JSON before trying base64 decode
        # (JSON strings might accidentally decode as valid base64)
        if fmt == "gltf" and data.strip().startswith("{"):
            data = data.encode("utf-8")
        else:
            try:
                data = base64.b64decode(data)
            except Exception:
                data = data.encode("utf-8")
    elif isinstance(data, list):
        data = bytes(data)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        if fmt in ("gltf", "glb"):
            ext = ".glb" if data[:4] == b"glTF" else ".gltf"
            mesh_file = temp_path / f"{name}{ext}"
            mesh_file.write_bytes(data)

            # Write CAS resources to temp directory
            for res_uri, res_data in cas_resources.items():
                res_path = temp_path / res_uri
                res_path.parent.mkdir(parents=True, exist_ok=True)
                res_path.write_bytes(res_data)

            # Also handle any explicit resources from geom
            resources = geom.get("resources", {})
            for res_name, res_data in resources.items():
                # Check if res_data is a CAS reference (string like "cas-v1/...")
                if isinstance(res_data, str) and cas_assets and res_data in cas_assets:
                    res_data = cas_assets[res_data]
                elif isinstance(res_data, str):
                    try:
                        res_data = base64.b64decode(res_data)
                    except Exception:
                        res_data = res_data.encode("utf-8")
                elif isinstance(res_data, list):
                    res_data = bytes(res_data)

                res_path = temp_path / res_name
                res_path.parent.mkdir(parents=True, exist_ok=True)
                res_path.write_bytes(res_data)

            old_objects = set(bpy.data.objects)
            bpy.ops.import_scene.gltf(filepath=str(mesh_file))
            new_objects = list(set(bpy.data.objects) - old_objects)

            if new_objects:
                return _select_main_object_and_cleanup(new_objects, name)

        elif fmt == "obj":
            mesh_file = temp_path / f"{name}.obj"
            mesh_file.write_bytes(data)

            old_objects = set(bpy.data.objects)
            bpy.ops.wm.obj_import(filepath=str(mesh_file))
            new_objects = list(set(bpy.data.objects) - old_objects)

            if new_objects:
                return _select_main_object_and_cleanup(new_objects, name)

    return None


def _select_main_object_and_cleanup(
    objects: list, name: str
) -> bpy.types.Object | None:
    """Select main mesh object, join if multiple, and cleanup empties."""
    mesh_objects = [obj for obj in objects if obj.type == "MESH" and obj.data]
    empty_objects = [obj for obj in objects if obj.type == "EMPTY"]

    if not mesh_objects:
        for obj in empty_objects:
            bpy.data.objects.remove(obj, do_unlink=True)
        return None

    for obj in mesh_objects:
        if obj.parent:
            world_matrix = obj.matrix_world.copy()
            obj.parent = None
            obj.matrix_world = world_matrix

    if len(mesh_objects) > 1:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in mesh_objects:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = mesh_objects[0]
        bpy.ops.object.join()
        main_obj = bpy.context.active_object
    else:
        main_obj = mesh_objects[0]

    main_obj.name = name
    if main_obj.data:
        main_obj.data.name = name

    for obj in empty_objects:
        if obj.name in bpy.data.objects:
            bpy.data.objects.remove(obj, do_unlink=True)

    return main_obj


def create_material(name: str, mat_data: dict) -> bpy.types.Material:
    """Create Blender material from Three.js material data."""
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes

    principled = nodes.get("Principled BSDF")
    if principled:
        color = mat_data.get("color", 0xFFFFFF)
        if isinstance(color, int):
            r = ((color >> 16) & 0xFF) / 255.0
            g = ((color >> 8) & 0xFF) / 255.0
            b = (color & 0xFF) / 255.0
            principled.inputs["Base Color"].default_value = (r, g, b, 1.0)

        if "metalness" in mat_data:
            principled.inputs["Metallic"].default_value = mat_data["metalness"]
        if "roughness" in mat_data:
            principled.inputs["Roughness"].default_value = mat_data["roughness"]

        opacity = mat_data.get("opacity", 1.0)
        if mat_data.get("transparent", False) and opacity < 1.0:
            principled.inputs["Alpha"].default_value = opacity
            mat.blend_method = "BLEND"

    return mat


def apply_animation_to_object(
    obj: bpy.types.Object,
    clips: list[dict],
    recording_fps: float,
    target_fps: float,
    start_frame: int,
    local_offset: Transform | None = None,
) -> int:
    """Apply animation clips to object with FPS conversion and optional local offset."""
    if not clips:
        return 0

    import mathutils

    keyframe_count = 0

    if obj.animation_data is None:
        obj.animation_data_create()

    action = bpy.data.actions.new(name=f"{obj.name}Action")
    obj.animation_data.action = action

    try:
        slot = action.slots.new(id_type="OBJECT", name=obj.name)
        obj.animation_data.action_slot = slot
    except AttributeError:
        pass

    obj.rotation_mode = "QUATERNION"

    # Precompute offset matrix if needed
    offset_matrix = None
    if local_offset is not None:
        offset_matrix = local_offset.to_blender_matrix()

    for clip in clips:
        tracks = clip.get("tracks", [])

        track_data = {}
        for track in tracks:
            track_name = track.get("name", "")

            # Meshcat animation tracks use 'keys' array with {time, value} objects
            keys = track.get("keys", [])

            if not keys:
                # Fallback to times/values format
                times = track.get("times", [])
                values = track.get("values", [])

                if hasattr(times, "tolist"):
                    times = times.tolist()
                if hasattr(values, "tolist"):
                    values = values.tolist()

                if times and values:
                    if ".position" in track_name or ".scale" in track_name:
                        value_size = 3
                    elif ".quaternion" in track_name:
                        value_size = 4
                    else:
                        continue

                    time_to_value = {}
                    for i, t in enumerate(times):
                        idx = i * value_size
                        if idx + value_size - 1 < len(values):
                            time_to_value[t] = values[idx : idx + value_size]
                    if time_to_value:
                        track_data[track_name] = time_to_value
                continue

            # Check track type
            if (
                ".position" not in track_name
                and ".quaternion" not in track_name
                and ".scale" not in track_name
            ):
                continue

            # Build time -> value mapping from keys array
            time_to_value = {}
            for key in keys:
                t = key.get("time", 0)
                value = key.get("value")
                if value is not None:
                    if hasattr(value, "tolist"):
                        value = value.tolist()
                    time_to_value[t] = value
            if time_to_value:
                track_data[track_name] = time_to_value

        if not track_data:
            continue

        all_times = set()
        for time_to_value in track_data.values():
            all_times.update(time_to_value.keys())

        if not all_times:
            continue

        min_time = min(all_times)
        max_time = max(all_times)
        duration_seconds = (max_time - min_time) / recording_fps
        target_frame_count = int(duration_seconds * target_fps) + 1

        for target_frame_idx in range(target_frame_count):
            target_time_seconds = target_frame_idx / target_fps
            target_recording_time = min_time + target_time_seconds * recording_fps
            frame = start_frame + target_frame_idx

            # Get values for this frame
            position = None
            rotation = None
            scale = None

            for track_name, time_to_value in track_data.items():
                sorted_times = sorted(time_to_value.keys())
                nearest_time = min(
                    sorted_times, key=lambda t: abs(t - target_recording_time)
                )
                value = time_to_value[nearest_time]

                if ".position" in track_name:
                    position = value
                elif ".quaternion" in track_name:
                    rotation = value
                elif ".scale" in track_name:
                    scale = value

            # Apply local offset if needed
            if offset_matrix is not None and (
                position is not None or rotation is not None
            ):
                # Build animation matrix
                anim_loc = (
                    mathutils.Vector(position)
                    if position
                    else mathutils.Vector((0, 0, 0))
                )
                if rotation:
                    x, y, z, w = rotation
                    anim_rot = mathutils.Quaternion((w, x, y, z))
                else:
                    anim_rot = mathutils.Quaternion()
                anim_scale = (
                    mathutils.Vector(scale) if scale else mathutils.Vector((1, 1, 1))
                )

                anim_matrix = mathutils.Matrix.LocRotScale(
                    anim_loc, anim_rot, anim_scale
                )
                combined = anim_matrix @ offset_matrix

                final_loc, final_rot, final_scale = combined.decompose()

                obj.location = final_loc
                obj.keyframe_insert(data_path="location", frame=frame)
                keyframe_count += 1

                obj.rotation_quaternion = final_rot
                obj.keyframe_insert(data_path="rotation_quaternion", frame=frame)
                keyframe_count += 1

                if scale:
                    obj.scale = final_scale
                    obj.keyframe_insert(data_path="scale", frame=frame)
                    keyframe_count += 1
            else:
                # No offset, apply directly
                if position:
                    obj.location = (position[0], position[1], position[2])
                    obj.keyframe_insert(data_path="location", frame=frame)
                    keyframe_count += 1

                if rotation:
                    x, y, z, w = rotation
                    obj.rotation_quaternion = (w, x, y, z)
                    obj.keyframe_insert(data_path="rotation_quaternion", frame=frame)
                    keyframe_count += 1

                if scale:
                    obj.scale = (scale[0], scale[1], scale[2])
                    obj.keyframe_insert(data_path="scale", frame=frame)
                    keyframe_count += 1

    return keyframe_count


def _import_meshcat_html_fallback(
    filepath: str,
    recording_fps: float | None = None,
    target_fps: float = 30.0,
    start_frame: int = 0,
    clear_scene: bool = True,
) -> dict:
    """Fallback import implementation with full feature parity."""
    html_content = Path(filepath).read_text(encoding="utf-8")
    commands = extract_commands(html_content)
    cas_assets = extract_cas_assets(html_content)

    scene_graph = build_scene_graph(commands)

    if recording_fps is None:
        recording_fps = scene_graph.animation_fps

    if clear_scene:
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete()

        for mesh in bpy.data.meshes:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        for mat in bpy.data.materials:
            if mat.users == 0:
                bpy.data.materials.remove(mat)
        for action in bpy.data.actions:
            if action.users == 0:
                bpy.data.actions.remove(action)

    collection_name = "MeshcatObjects"
    if collection_name not in bpy.data.collections:
        collection = bpy.data.collections.new(collection_name)
        bpy.context.scene.collection.children.link(collection)
    else:
        collection = bpy.data.collections[collection_name]

    object_count = 0
    animation_count = 0
    created_objects: dict[str, bpy.types.Object] = {}
    max_frame = start_frame

    # Create objects
    for node in scene_graph.get_mesh_nodes():
        if _should_skip_path(node.path):
            continue

        obj = create_mesh_from_geometry(node.name, node.geometry, cas_assets)
        if obj is None:
            continue

        # Apply world transform
        world_transform = node.get_world_transform()
        obj.location = world_transform.translation
        obj.rotation_mode = "QUATERNION"
        x, y, z, w = world_transform.rotation
        obj.rotation_quaternion = (w, x, y, z)
        obj.scale = world_transform.scale

        # Only apply material for non-meshfile geometry
        if node.material and not node.is_meshfile:
            mat = create_material(f"{node.name}_material", node.material)
            if obj.data:
                obj.data.materials.clear()
                obj.data.materials.append(mat)

        obj.hide_viewport = not node.visible
        obj.hide_render = not node.visible

        collection.objects.link(obj)
        created_objects[node.path] = obj
        object_count += 1

    # Apply animations with inheritance
    for path, obj in created_objects.items():
        anim_node = _find_animation_node(scene_graph, path)
        if anim_node is None:
            continue

        obj_node = scene_graph.get_node(path)
        local_offset = _get_local_offset_from_ancestor(obj_node, anim_node)

        kf_count = apply_animation_to_object(
            obj,
            anim_node.keyframes,
            recording_fps,
            target_fps,
            start_frame,
            local_offset,
        )
        if kf_count > 0:
            animation_count += 1

            for clip in anim_node.keyframes:
                for track in clip.get("tracks", []):
                    # Get all keyframe times (meshcat uses 'keys' array)
                    keys = track.get("keys", [])
                    if keys:
                        times = [key.get("time", 0) for key in keys]
                    else:
                        # Fallback to times array
                        times = track.get("times", [])
                        if hasattr(times, "tolist"):
                            times = times.tolist()

                    if times:
                        max_time = max(times)
                        time_seconds = max_time / recording_fps
                        frame = start_frame + int(round(time_seconds * target_fps))
                        max_frame = max(max_frame, frame)

    bpy.context.scene.frame_start = start_frame
    bpy.context.scene.frame_end = max_frame
    bpy.context.scene.render.fps = int(target_fps)

    return {
        "object_count": object_count,
        "animation_count": animation_count,
        "frame_range": (start_frame, max_frame),
        "recording_fps": recording_fps,
    }
