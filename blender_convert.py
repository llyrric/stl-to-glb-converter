"""
Headless Blender conversion script: STL -> indexed, non-decimated GLB.

Run via:
    blender -b -P blender_convert.py -- <input_stl_path> <output_glb_path>

Compatible with Blender 3.x and 4.x.
"""

import sys
import os

import bpy


def log(msg):
    print(f"[blender_convert] {msg}", flush=True)


def get_args():
    argv = sys.argv
    if "--" not in argv:
        raise SystemExit("Expected arguments after '--': <input_stl> <output_glb>")
    args = argv[argv.index("--") + 1:]
    if len(args) < 2:
        raise SystemExit("Usage: blender -b -P blender_convert.py -- <input_stl> <output_glb>")
    return args[0], args[1]


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)

    # Remove orphan data left over from the default scene (cube/camera/light meshes, etc.)
    for block_collection in (bpy.data.meshes, bpy.data.cameras, bpy.data.lights):
        for block in list(block_collection):
            if block.users == 0:
                block_collection.remove(block)


def import_stl(input_path):
    before = set(bpy.data.objects)
    try:
        # Blender 4.x+ native C++ importer
        bpy.ops.wm.stl_import(filepath=input_path)
    except AttributeError:
        # Older Blender fallback
        bpy.ops.import_mesh.stl(filepath=input_path)

    after = set(bpy.data.objects)
    new_objects = list(after - before)
    if not new_objects:
        raise RuntimeError(f"STL import produced no objects: {input_path}")
    return new_objects


def process_mesh(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")

    # Merge duplicate verts into indexed geometry WITHOUT moving any surfaces
    # or reducing triangle density.
    bpy.ops.mesh.remove_doubles(threshold=0.00005)
    bpy.ops.mesh.normals_make_consistent(inside=False)

    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.shade_smooth()


def export_glb(output_path):
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    kwargs = {
        "filepath": output_path,
        "export_format": "GLB",
        "use_selection": True,
        "export_apply": False,
        "export_normals": True,
        "export_draco_mesh_compression_enable": False,
    }

    # The vertex-color export option was renamed (bool -> enum) across Blender
    # versions; detect which one this build supports so the script works on
    # any Blender 3.x/4.x/5.x install.
    valid_props = {p.identifier for p in bpy.ops.export_scene.gltf.get_rna_type().properties}
    if "export_colors" in valid_props:
        kwargs["export_colors"] = True
    elif "export_vertex_color" in valid_props:
        kwargs["export_vertex_color"] = "ACTIVE"

    bpy.ops.export_scene.gltf(**kwargs)


def main():
    input_path, output_path = get_args()
    log(f"Input:  {input_path}")
    log(f"Output: {output_path}")

    if not os.path.isfile(input_path):
        raise SystemExit(f"Input STL not found: {input_path}")

    clear_scene()

    log("Importing STL...")
    new_objects = import_stl(input_path)
    mesh_objects = [o for o in new_objects if o.type == "MESH"]
    if not mesh_objects:
        raise RuntimeError("No mesh object found after STL import")

    bpy.ops.object.select_all(action="DESELECT")
    for obj in mesh_objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = mesh_objects[0]

    # Join multi-object STLs into a single mesh so export/edit-mode ops are simple.
    if len(mesh_objects) > 1:
        bpy.ops.object.join()

    target = bpy.context.view_layer.objects.active
    log(f"Processing mesh '{target.name}' "
        f"({len(target.data.vertices)} verts, {len(target.data.polygons)} faces)...")

    process_mesh(target)

    log(f"Post-merge: {len(target.data.vertices)} verts, {len(target.data.polygons)} faces")

    bpy.ops.object.select_all(action="DESELECT")
    target.select_set(True)
    bpy.context.view_layer.objects.active = target

    log("Exporting GLB...")
    export_glb(output_path)
    log("Done.")


if __name__ == "__main__":
    main()
