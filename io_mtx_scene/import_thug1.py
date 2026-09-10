#############################################
# THUG1 SCENE (.scn/.mdl/skin) IMPORT
#############################################
import bpy
import bmesh
import struct
import mathutils
import math
from bpy.props import *
from bpy_extras.io_utils import ImportHelper
from . helpers import *
from . material import *

# METHODS
#############################################
def import_scn_ug1(filename, directory, context, operator):
    p = Printer()
    p.on = True

    filename = str(filename) if filename is not None else ""
    directory = str(directory) if directory is not None else ""
    input_file = filename if os.path.isabs(filename) else os.path.join(directory, filename)

    if not input_file or not os.path.exists(input_file):
        if operator is not None and hasattr(operator, "report"):
            operator.report({'ERROR'}, f"File not found: {input_file}")
        return {'CANCELLED'}

    with open(input_file, "rb") as inp:
        r = Reader(inp.read())

    _mat_version = r.u32()
    _mesh_version = r.u32()
    _vert_version = r.u32()

    num_materials = p("num materials: {}", r.u32())
    read_materials(r, p, num_materials, directory, operator)
    num_sectors = p("num sectors: {}", r.i32())
    read_sectors_ug1(r, p, num_sectors, context, operator)
    rename_imported_materials()
    
#----------------------------------------------------------------------------------
def read_sectors_ug1(reader, printer, num_sectors, context, operator=None, output_file=None):
    r = reader
    p = printer
    outf = output_file

    vert_position_index_offset = 1
    vert_texcoord_index_offset = 1

    for i in range(num_sectors):
        write_sector_to_obj = False # True

        bm = bmesh.new()

        p("sector {}", i)
        sec_checksum = p("  sector checksum: {}", hex(r.u32()))

        blender_mesh = bpy.data.meshes.new("scn_mesh_" + str(sec_checksum))
        blender_object = bpy.data.objects.new("scn_" + str(sec_checksum), blender_mesh)
        blender_object.thug_export_collision = False
        to_group(blender_object, "SceneMesh")
        context.scene.collection.objects.link(blender_object)
        # context.view_layer.objects.active = blender_object

        bone_index = p("  bone index: {}", r.i32())
        sec_flags = p("  sector flags: {}", r.u32())
        num_meshes = p("  number of meshes: {}", r.u32())
        if num_meshes > 100000:
            raise Exception("Invalid data: more than 100k meshes.")
        p("  bbox: {}", (r.read("3f"), r.read("3f")))
        p("  bounding sphere: {}", r.read("4f"))

        if ((bone_index != -1) or
                (sec_flags & SECFLAGS_HAS_VERTEX_WEIGHTS) or
                not (sec_flags & SECFLAGS_HAS_TEXCOORDS) or
                (sec_flags & SECFLAGS_BILLBOARD_PRESENT)):
            write_sector_to_obj = False

        if sec_flags & SECFLAGS_BILLBOARD_PRESENT:
            p("  billboard type: {}", r.u32())
            p("  billboard origin: {}", r.read("3f"))
            p("  billboard pivot pos: {}", r.read("3f"))
            p("  billboard pivot axis: {}", r.read("3f"))

        if sec_flags & SECFLAGS_HAS_VERTEX_NORMALS:
            p("  sector has vertex normals!", None)

        if sec_flags & SECFLAGS_HAS_VERTEX_WEIGHTS:
            p("  sector has vertex weights!", None)

        if sec_flags & SECFLAGS_HAS_TEXCOORDS:
            p("  sector has tc sets!", None)

        if sec_flags & SECFLAGS_HAS_VERTEX_COLORS:
            p("  sector has vertex colors!", None)
            color_layer = bm.loops.layers.color.new("color")
            alpha_layer = bm.loops.layers.color.new("alpha")

        if sec_flags & SECFLAGS_HAS_VERTEX_COLOR_WIBBLES:
            p("  sector has vertex color wibbles!", None)

        vertex_normals = {}
        vertex_weights = {}
        vertex_weight0 = {}
        vertex_bones = {}
        
        mesh_vert_positions = []
        mesh_vert_normals = []
        mesh_vert_colors = []
        mesh_vert_texcoords = []
        
        this_mesh_verts = []
        per_vert_data = {}
        
        amount_of_verts = p("  vertices: {}", r.u32())
        p("  vertex data stride: {}", r.u32())

        # Read vertex data
        for l in range(amount_of_verts):
            #this_stride = 0

            vert_pos = r.read("3f")
            vert_pos = from_thug_coords(vert_pos)
            #this_stride += 12

            new_vert = bm.verts.new(vert_pos)
            per_vert_data[new_vert] = {}
            this_mesh_verts.append(new_vert)
            
        if sec_flags & SECFLAGS_HAS_VERTEX_NORMALS:
            if sec_flags & SECFLAGS_HAS_VERTEX_WEIGHTS:
                # Apparently, normals are supposed to be packed in weighted mesh
                # but for some reason, this never seems to be the case!
                for l in range(amount_of_verts):
                    new_vert = this_mesh_verts[l]
                    vertex_normal = r.read("3f")
                    if operator.import_custom_normals:
                        vertex_normals[new_vert] = from_thug_coords(vertex_normal)
            else:
                for l in range(amount_of_verts):
                    new_vert = this_mesh_verts[l]
                    vertex_normal = r.read("3f")
                    if operator.import_custom_normals:
                        vertex_normals[new_vert] = from_thug_coords(vertex_normal)
                
        
        if sec_flags & SECFLAGS_HAS_VERTEX_WEIGHTS:
            for l in range(amount_of_verts):
                new_vert = this_mesh_verts[l]
                packed_weights = r.u32()
                vert_weights = (
                    (packed_weights & 0x7FF) / 1023.0,
                    ((packed_weights >> 11) & 0x7FF) / 1023.0,
                    ((packed_weights >> 22) & 0x3FF) / 511.0,
                    0.0
                )
                vertex_weight0[new_vert] = vert_weights

            for l in range(amount_of_verts):
                new_vert = this_mesh_verts[l]
                bone_indices = r.read("4H")
                vertex_bones[new_vert] = bone_indices
                
            for l in range(amount_of_verts):
                new_vert = this_mesh_verts[l]
                vertex_weights[new_vert] = (vertex_weight0[new_vert], vertex_bones[new_vert])
                
        
        if sec_flags & SECFLAGS_HAS_TEXCOORDS:
            texcoords_to_read = r.u32()
            for l in range(amount_of_verts):
                new_vert = this_mesh_verts[l]
                for m in range(texcoords_to_read):
                    texcoords = r.read("2f")
                    per_vert_data[new_vert].setdefault("uvs", []).append(texcoords)
                    if write_sector_to_obj and m == 0:
                        outf.write("vt {:f} {:f}\n".format(
                            texcoords[0],
                            texcoords[1]))
                    
        
        if sec_flags & SECFLAGS_HAS_VERTEX_COLORS:
            for l in range(amount_of_verts):
                new_vert = this_mesh_verts[l]
                per_vert_data[new_vert]["color"] = r.read("4B")
                #this_stride += 4
                
                
        if sec_flags & SECFLAGS_HAS_VERTEX_COLOR_WIBBLES:
            for l in range(amount_of_verts):
                new_vert = this_mesh_verts[l]
                r.u8() # We can't import this data currently, so skip past 
                #this_stride += 4

        
                
        for j in range(num_meshes):
            p("  mesh #{}", j)
            p("    center: {}", r.vec3f())
            p("    radius: {}", r.f32())
            p("    bbox: {}", (r.vec3f(), r.vec3f()))

            mesh_flags = r.u32()
            p("    flags: {} ({})".format(mesh_flags, bin(mesh_flags)), None)

            mat_checksum = p("    material checksum: {}", hex(r.u32()))
            mat_name = str(mat_checksum)

            # 1) Try direct name lookup
            mat = bpy.data.materials.get(mat_name)

            # 2) Fallback: find material that recorded the same checksum property
            if mat is None:
                for m in bpy.data.materials:
                    try:
                        if str(m.get("thug_mat_name_checksum", "")) == mat_name:
                            mat = m
                            break
                    except Exception:
                        pass

            # 3) If still not found, create it and ensure node setup (preserve TEX_IMAGE nodes)
            if mat is None:
                mat = bpy.data.materials.new(name=mat_name)
                try:
                    ensure_principled_material(mat)
                except Exception:
                    try:
                        mat.use_nodes = True
                    except Exception:
                        pass

            # 4) Guarantee object has a material slot referencing this material
            mat_index = None
            for existing_mat_index, mat_slot in enumerate(blender_object.material_slots):
                if mat_slot.material == mat:
                    mat_index = existing_mat_index
                    break
            if mat_index is None:
                blender_object.data.materials.append(mat)
                mat_index = len(blender_object.material_slots) - 1

            num_lod_levels = p("    number of lod index levels: {}", r.u32())
            if num_lod_levels > 16:
                raise Exception("Bad number of lod levels!")
            # num_tc_sets = p("    number of texcoord sets: {}", num_passes) # MATERIAL_PASSES[mat_checksum])

            for k in range(num_lod_levels):
                num_indices_for_this_lod_level = r.u32()
                p("    {}", "num indices for lod level #{}: {}".format(k, num_indices_for_this_lod_level))
                vert_indices = r.read(str(num_indices_for_this_lod_level) + "H")
                #num_indices_for_this_lod_level2 = r.u16()
                #p("    {}", "num indices for lod level #{} (2nd round?): {}".format(k, num_indices_for_this_lod_level2))
                #the_indices_for_this_lod_level2 = r.read(str(num_indices_for_this_lod_level2) + "H")
                #min_index = p("    min index: {}", min(*the_indices_for_this_lod_level2))
                #max_index = p("    max index: {}", max(*the_indices_for_this_lod_level2))

                #r.read("14x") # padding?

    
            # bm.verts.ensure_lookup_table()
            inds = vert_indices
            for l in range(2, len(inds)):
                try:
                    if l % 2 == 0:
                        verts = (this_mesh_verts[inds[l - 2]],
                                 this_mesh_verts[inds[l - 1]],
                                 this_mesh_verts[inds[l]])
                        if len(set(verts)) != 3:
                            continue # degenerate triangle
                        bmface = bm.faces.new(verts)
                    else:
                        verts = (this_mesh_verts[inds[l - 2]],
                                 this_mesh_verts[inds[l]],
                                 this_mesh_verts[inds[l - 1]],)
                        if len(set(verts)) != 3:
                            continue # degenerate triangle
                        bmface = bm.faces.new(verts)
                    if mat_index is not None:
                        bmface.material_index = mat_index
                except IndexError as err:
                    print(err)
                except ValueError as err:
                    print(err)

            if sec_flags & SECFLAGS_HAS_TEXCOORDS:
                # Collect per-vertex UV sets aligned with `this_mesh_verts` order
                # for assignment after the BMesh is converted to a Mesh.
                vert_uvs = [per_vert_data.get(bmv, {}).get("uvs", []) for bmv in this_mesh_verts]
                uv_sets = max((len(uv) for uv in vert_uvs), default=0)

                # Preserve vertex color assignment (BMesh color layers) if present.
                if sec_flags & SECFLAGS_HAS_VERTEX_COLORS:
                    for face in bm.faces:
                        for loop in face.loops:
                            pvd = per_vert_data.get(loop.vert)
                            if not pvd: continue
                            cb, cg, cr, ca = pvd["color"]
                            alpha_val = (ca / 128.0) if 'ca' in locals() else 1.0
                            loop[color_layer] = (cr / 128.0, cg / 128.0, cb / 128.0, alpha_val)
                            loop[alpha_layer] = (ca / 128.0, ca / 128.0, ca / 128.0, 1.0)
            else:
                vert_uvs = []
                uv_sets = 0
        bm.verts.index_update()
        bm.to_mesh(blender_mesh)
        # Clear any custom split normals so meshes import with smooth shading
        try:
            from .helpers import reset_custom_normals
            reset_custom_normals(blender_mesh)
        except Exception:
            pass
        
        if vertex_weights:
            vgs = blender_object.vertex_groups
            for vert, (weights, bone_indices) in vertex_weights.items():
                for weight, bone_index in zip(weights, bone_indices):
                    # Blender 5.x requires keyword arguments for vertex group creation.
                    # Static meshes / unskinned vertices may carry a negative or invalid bone index.
                    # Skip those entries instead of crashing the import process.
                    if bone_index is None or not isinstance(bone_index, int) or bone_index < 0:
                        continue
                    group_name = str(bone_index)
                    vert_group = vgs.get(group_name) or vgs.new(name=group_name)
                    print("{:2s} {:3f}".format(vert_group.name, weight), end='; ')
                    vert_group.add([vert.index], weight, "ADD")
                print()

        # Ensure smooth shading and regenerate/clear custom split normals so
        # meshes import with smooth shading by default. If `vertex_normals`
        # were provided and the operator explicitly requested importing
        # custom normals (`operator.import_custom_normals`), preserve them.
        if blender_mesh.polygons:
            blender_mesh.polygons.foreach_set("use_smooth", [True] * len(blender_mesh.polygons))

        blender_mesh.validate()
        blender_mesh.update()

        try:
            bm_normals = bmesh.new()
            bm_normals.from_mesh(blender_mesh)
            bmesh.ops.recalc_face_normals(bm_normals, faces=bm_normals.faces)
            bm_normals.to_mesh(blender_mesh)
            bm_normals.free()
        except Exception:
            pass

        # Recalculate vertex normals from geometry.
        try:
            if hasattr(blender_mesh, "calc_normals"):
                blender_mesh.calc_normals()
        except Exception:
            pass

        # If the importer provided explicit per-vertex normals and the user
        # requested to keep them, apply those as split custom normals. Otherwise
        # reset custom normals to the averaged vertex normals (equivalent to
        # Blender's "Reset Custom Split Normals") so shading appears smooth.
        try:
            if vertex_normals and operator is not None and getattr(operator, 'import_custom_normals', False):
                # Apply provided custom normals (per-loop array built earlier)
                vertex_normals = { vert.index: normal for vert, normal in vertex_normals.items() }
                new_normals = [ vertex_normals.get(l.vertex_index, (0.0, 0.0, 1.0)) for l in blender_mesh.loops ]
                if len(new_normals) == len(blender_mesh.loops) and hasattr(blender_mesh, "normals_split_custom_set"):
                    blender_mesh.normals_split_custom_set(new_normals)
                    blender_mesh.use_auto_smooth = True
            else:
                # Reset custom normals: compute smooth per-vertex normals and
                # assign them to loops so split normals are cleared.
                vert_normals = [v.normal for v in blender_mesh.vertices]
                new_normals = [ vert_normals[l.vertex_index] for l in blender_mesh.loops ]
                if len(new_normals) == len(blender_mesh.loops) and hasattr(blender_mesh, "normals_split_custom_set"):
                    blender_mesh.normals_split_custom_set(new_normals)
                    # Disable auto-smooth so Blender uses standard vertex normals.
                    try:
                        blender_mesh.use_auto_smooth = False
                    except Exception:
                        pass
        except Exception as norm_err:
            print(f"[THUG] Normal handling failed: {norm_err}")

        blender_mesh.update()

        # Assign UVs using Mesh.uv_layers (Blender 2.8+/5.2 API). We map per-vertex
        # UVs to per-loop UV slots so the UVs follow the polygon loop ordering.
        try:
            if uv_sets:
                for set_index in range(uv_sets):
                    layer_name = f"UVMap_{set_index}" if set_index != 0 else "UVMap"
                    uv_layer = blender_mesh.uv_layers.get(layer_name) or blender_mesh.uv_layers.new(name=layer_name)
                    blender_mesh.uv_layers.active = uv_layer

                    for poly in blender_mesh.polygons:
                        for loop_index in range(poly.loop_start, poly.loop_start + poly.loop_total):
                            vert_index = blender_mesh.loops[loop_index].vertex_index
                            if vert_index < len(vert_uvs):
                                uvs = vert_uvs[vert_index]
                                if set_index < len(uvs):
                                    u, v = uvs[set_index]
                                    # If textures appear flipped vertically, flip v here:
                                    # v = 1.0 - v
                                    uv_layer.data[loop_index].uv = (u, v)
        except Exception as uv_err:
            print(f"[THUG] UV assignment failed: {uv_err}")

        if sec_flags & SECFLAGS_HAS_VERTEX_COLORS:
            color_attr_name = "THUG_COLOR"
            if color_attr_name in blender_mesh.attributes:
                blender_mesh.attributes.remove(blender_mesh.attributes[color_attr_name])
            color_attr = blender_mesh.attributes.new(name=color_attr_name, type="FLOAT_COLOR", domain="CORNER")
            bm_vert_colors = {vert.index: per_vert_data.get(vert, {}).get("color") for vert in this_mesh_verts if "color" in per_vert_data.get(vert, {})}
            for poly in blender_mesh.polygons:
                for loop_index in poly.loop_indices:
                    loop = blender_mesh.loops[loop_index]
                    color = bm_vert_colors.get(loop.vertex_index)
                    if color is not None:
                        cr, cg, cb, ca = color
                        color_attr.data[loop_index].color = (cr / 255.0, cg / 255.0, cb / 255.0, ca / 255.0)
            for mat_slot in blender_object.material_slots:
                mat = mat_slot.material
                if mat is not None:
                    ensure_principled_material(mat)
                    ensure_vertex_color_material_input(mat, attribute_name=color_attr_name)


        # Material datablocks are parsed before this mesh exists. Rebind the
        # THUG Image Texture nodes now that Blender has the final UV layers.
        try:
            rebuild_thps_uv_nodes_for_object(blender_object)
        except Exception as uv_err:
            print("[THUG] UV node rebuild failed for {}: {}".format(blender_object.name, uv_err))

    #p("number of hierarchy objects: {}", r.i32())
    print("COMPLETE!")


                

# OPERATORS
#############################################
class THUG1ScnToScene(bpy.types.Operator, ImportHelper):
    bl_idname = "io.thug1_xbx_scn_to_scene"
    bl_label = "THUG1 Scene (.scn/.skin/.mdl)"
    # bl_options = {'REGISTER', 'UNDO'}

    filepath = StringProperty(subtype='FILE_PATH')
    filter_glob = StringProperty(default="*.skin.xbx;*.scn.xbx;*.mdl.xbx;*.skin;*.scn;*.mdl", options={"HIDDEN"})
    filename = StringProperty(name="File Name")
    directory = StringProperty(name="Directory")
    load_tex = BoolProperty(name="Load the tex file", default=True)
    import_custom_normals = BoolProperty(name="Import custom normals", default=True)

    def execute(self, context):
        import os, re

        filepath = getattr(self, 'filepath', getattr(self, 'filename', getattr(self, 'directory', '')))
        filepath = str(filepath)
        directory = os.path.dirname(filepath)
        filename = os.path.basename(filepath)

        if self.load_tex:
            tex_filename = re.sub(r'\.(scn|skin|mdl)', '.tex', filename, flags=re.IGNORECASE)
            tex_path = os.path.join(directory, tex_filename)
            if tex_filename != filename and os.path.exists(tex_path):
                try:
                    from .tex import import_tex
                    import_tex(tex_filename, directory, self)
                except Exception as tex_err:
                    self.report({'WARNING'}, f"Failed to import texture {tex_filename}: {tex_err}")

        if not filepath or not os.path.exists(filepath):
            self.report({'ERROR'}, f"File not found: {filepath}")
            return {'CANCELLED'}

        result = import_scn_ug1(filepath, directory, context, self)
        return result if isinstance(result, dict) else {'FINISHED'}

    def invoke(self, context, event):
        wm = bpy.context.window_manager
        wm.fileselect_add(self)

        return {'RUNNING_MODAL'}
