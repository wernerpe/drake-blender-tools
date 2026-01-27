# Meshcat HTML Importer

Import meshcat HTML recordings into Blender.

## Overview

This package parses meshcat HTML recordings (saved from the meshcat web viewer) and converts them to Blender scenes with full animation support.

## Features

- Parse msgpack-encoded commands from HTML recordings
- Support for multiple geometry types:
  - `BufferGeometry` (custom meshes)
  - Primitives: `BoxGeometry`, `SphereGeometry`, `CylinderGeometry`
  - Mesh files: glTF, OBJ (embedded in `_meshfile_geometry`)
- Material conversion (Three.js → Blender Principled BSDF)
- Animation import with keyframe conversion
- Scene hierarchy preservation

## Installation

```bash
uv pip install meshcat-html-importer
```

Or from the monorepo root:

```bash
uv sync
```

## Usage

### Command Line

```bash
meshcat-html-import recording.html -o scene.blend
```

### Options

- `-o, --output`: Output Blender file path (default: output.blend)
- `--fps`: Frames per second for animation (default: 30)
- `--start-frame`: Starting frame number (default: 0)

### As a Blender Addon

Install the `meshcat_html_importer` extension from `blender_addons/`:

1. In Blender: Edit > Preferences > Get Extensions
2. Install from disk: select `blender_addons/meshcat_html_importer/`
3. Use: File > Import > Meshcat Recording (.html)

### Python API

```python
from meshcat_html_importer import parse_html_recording, build_blender_scene

# Parse the HTML file
scene_data = parse_html_recording("recording.html")

# Build in Blender (must run inside Blender)
build_blender_scene(scene_data)
```

## HTML Recording Format

Meshcat HTML recordings contain:

1. **Base64 msgpack commands** - Scene setup and animation data
   ```javascript
   fetch("data:application/octet-binary;base64,<DATA>")
   ```

2. **casAssets dictionary** - Textures and embedded files
   ```javascript
   var casAssets = {"sha256-hash": "data:...base64..."};
   ```

## Supported Three.js → Blender Mappings

### Materials
- `MeshStandardMaterial` → Principled BSDF (direct metalness/roughness)
- `MeshPhongMaterial` → Principled BSDF (shininess → roughness approximation)
- `MeshBasicMaterial` → Principled BSDF (emission-based for unlit look)

### Geometry
- Positions, normals, UVs, and indices from BufferGeometry
- Procedural primitives (box, sphere, cylinder)
- Embedded glTF/OBJ mesh files
