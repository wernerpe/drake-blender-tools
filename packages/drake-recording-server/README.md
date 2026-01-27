# Drake Recording Server

A Flask server that records Drake simulation states as Blender keyframes.

## Overview

This package implements Drake's glTF Render Client-Server API but instead of rendering images, it saves object poses as keyframes that can be imported into Blender.

## Installation

From the monorepo root:

```bash
uv sync
uv pip install -e packages/drake-recording-server
```

## Usage

### Starting the Server

```bash
drake-recording-server \
    --export_path output/scene.blend \
    --keyframe_dump_path output/keyframes.pkl
```

### Options

- `--host`: URL to host on (default: 127.0.0.1)
- `--port`: Port to host on (default: 8000)
- `--blend_file`: Path to a base `.blend` file to use as template
- `--export_path`: Path to export the Blender scene (required)
- `--keyframe_dump_path`: Path to dump keyframes as pickle file (required)
- `--bpy_settings_file`: Path to a Python file for custom Blender settings

### Integration with Drake

Configure your Drake simulation to use the glTF render client pointing to this server:

```python
from pydrake.geometry import DrakeVisualizerParams

params = DrakeVisualizerParams()
params.publish_period = 1.0 / 30.0  # 30 fps
# Configure to connect to the recording server
```

## Output Format

The server generates two outputs:

1. **`.blend` file**: The Blender scene with all objects from the first frame
2. **`.pkl` file**: Pickled keyframe data containing object poses for each frame

### Keyframe Data Format

```python
[
    [  # Frame 0
        {
            "name": "object_name",
            "location": [x, y, z],
            "rotation_quaternion": [x, y, z, w],
        },
        # ... more objects
    ],
    # ... more frames
]
```

## Importing into Blender

Use the `keyframe_importer.py` addon (in `blender_addons/`) to import the keyframe data:

1. Open the exported `.blend` file in Blender
2. Install the `keyframe_importer.py` addon
3. Use View3D > UI > Keyframe Importer > Import Keyframes
4. Select the `.pkl` file
