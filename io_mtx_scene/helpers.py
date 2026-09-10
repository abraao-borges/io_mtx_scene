import bpy
import struct
import mathutils
import math
import logging
from . constants import *

__reload_order_index__ = -42

# PROPERTIES
#############################################
global_export_scale = 1
LOG = logging.getLogger(ADDON_NAME)


# METHODS
#############################################
#----------------------------------------------------------------------------------
#- Returns a numbered, unique version of the desired name
#----------------------------------------------------------------------------------
def get_unique_name(name):
    ob_name = name
    name_idx = 0
    # Create new rail path
    while ob_name in bpy.data.objects:
        name_idx += 1
        ob_name = name + '_' + "{:02d}".format(name_idx)
    return ob_name
    
#----------------------------------------------------------------------------------
#- Returns TRUE if the given string is a hex-formatted int32 (name checksum)
#----------------------------------------------------------------------------------
def is_hex_string(name):
    if name.startswith("0x") and (len(name) == 9 or len(name) == 10):
        #print("{} IS a hex string!".format(name))
        return True
    #print("{} is NOT a hex string!".format(name))
    return False
    
#----------------------------------------------------------------------------------
#- Auto-creates (if needed) and assigns the given object to a group
#----------------------------------------------------------------------------------
def to_group(blender_object, group_name):
    collection = bpy.data.collections.get(group_name)
    if collection is None:
        collection = bpy.data.collections.new(group_name)
    if blender_object.name not in collection.objects:
        collection.objects.link(blender_object)


def link_object(obj, collection=None):
    target_collection = collection or getattr(bpy.context, "collection", None) or bpy.context.scene.collection
    if obj.name not in target_collection.objects:
        target_collection.objects.link(obj)
    return target_collection


def unlink_object(obj, collection=None):
    target_collection = collection or getattr(bpy.context, "collection", None) or bpy.context.scene.collection
    if obj.name in target_collection.objects:
        target_collection.objects.unlink(obj)
    return target_collection
        
#----------------------------------------------------------------------------------
#- Auto-creates (if needed) and returns a TriggerScript with the given name
#----------------------------------------------------------------------------------
def get_triggerscript(script_name):
    script_text = bpy.data.texts.get("script_" + script_name, None)
    if not script_text:
        print("TriggerScript {} does not exist, creating it!".format(script_name))
        script_text = bpy.data.texts.new(name="script_" + script_name)
    return script_text
    
#----------------------------------------------------------------------------------
#- Returns a cleaned version of a script name for exporting
#----------------------------------------------------------------------------------
def format_triggerscript_name(script_name):
    if script_name.startswith('script_') or script_name.startswith('Script_'):
        return script_name[7:]
        
    return script_name
    
#----------------------------------------------------------------------------------
#- Scales a 2D vector v by scale s, with a pivot point
#----------------------------------------------------------------------------------
def scale_2d( v, s, p ):
    return ( p[0] + s[0]*(v[0] - p[0]), p[1] + s[1]*(v[1] - p[1]) )
    
def scale_uvs( uv_map, scale, pivot=mathutils.Vector((0.5, 0.5)) ):
    #print("Scaling UVs by factor: {}".format(scale))
    for i in range( len(uv_map.data) ):
        uv_map.data[i].uv = scale_2d( uv_map.data[i].uv, scale, pivot )
        
#----------------------------------------------------------------------------------
#- Returns an array of vertices for the given object
#----------------------------------------------------------------------------------
def get_vertices_thug(obj):
    verts = []
    for v in obj.data.vertices:
        verts.append(to_thug_coords(v.co))
    return verts
    
def get_uv_index(obj, uv_name):
    uv_index = -1
    if not hasattr(obj.data, "uv_layers"):
        return -1
    for uv_layer in obj.data.uv_layers:
        uv_index += 1
        if uv_layer.name == str(uv_name):
            return uv_index
    return -1
        
#----------------------------------------------------------------------------------
#- Returns an existing material given a name, or creates one with that name
#----------------------------------------------------------------------------------
def get_material(name):
    blender_mat = bpy.data.materials.get(str(name))
    if blender_mat is None:
        blender_mat = bpy.data.materials.new(str(name))
        blender_mat.use_nodes = True
        if blender_mat.node_tree:
            nodes = blender_mat.node_tree.nodes
            for node in list(nodes):
                if node.type == "OUTPUT_MATERIAL":
                    continue
                nodes.remove(node)
            principled = nodes.new(type="ShaderNodeBsdfPrincipled")
            principled.location = (0, 0)
            principled.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
            principled.inputs["Alpha"].default_value = 1.0
            output = nodes.new(type="ShaderNodeOutputMaterial")
            output.location = (220, 0)
            blender_mat.node_tree.links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    return blender_mat
    
#----------------------------------------------------------------------------------
def get_index(seq, value, key=lambda x: x, default=-1):
    i = 0
    for item in seq:
        if key(item) == value:
            return i
        i += 1
    return default

def swizzle_axis(val, mask):
    bit = 1
    result = 0
    while bit <= mask:
        test = mask & bit
        if test != 0: result |= val & bit
        else: val <<= 1
        bit <<= 1
    return result
#----------------------------------------------------------------------------------
def swizzle(data, width, height, bitCount, depth, unswizzle):
    def aux(x, y, z, masks):
        return (swizzle_axis(x, masks.x) |
                swizzle_axis(y, masks.y) |
                (0 if z == -1 else swizzle_axis(z, masks.z)))

    bitCount //= 8
    a = 0
    b = 0
    outdata = [0] * len(data)
    offset = 0
    masks = MaskSet(width, height, depth)
    for y in range(height):
        for x in range(width):
            a = (y * width + x) * bitCount
            b = aux(x, y, -1, masks) * bitCount
            if not unswizzle:
                a, b = b, a
            for i in range(offset, bitCount + offset):
                outdata[a + i] = data[b + i]
    return outdata


def _need_to_flip_normals(ob):
    negatives = 0
    while ob:
        for val in ob.scale:
            if val < 0:
                negatives += 1
        ob = ob.parent
    return negatives % 2 == 1

def _flip_normals(ob):
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.flip_normals()
    bpy.ops.object.mode_set(mode="OBJECT")

def _make_temp_obj(data):
    ob = bpy.data.objects.get("~THUG TEMPORARY OBJECT~")
    if ob:
        ob.data = data
        return ob
    else:
        return bpy.data.objects.new("~THUG TEMPORARY OBJECT~", data)


#----------------------------------------------------------------------------------
def reset_custom_normals(mesh):
    """Reset/clear custom split normals on a Mesh to ensure smooth shading.
    Uses `clear_custom_split_normals()` when available, otherwise falls back
    to recalculating normals and disabling auto-smooth.
    """
    try:
        if mesh is None:
            return
        # Preferrable API when present
        if hasattr(mesh, 'clear_custom_split_normals'):
            try:
                mesh.clear_custom_split_normals()
                try:
                    mesh.use_auto_smooth = False
                except Exception:
                    pass
                return
            except Exception:
                pass

        # Fallback: recalc normals and disable auto-smooth
        try:
            if hasattr(mesh, 'calc_normals'):
                mesh.calc_normals()
        except Exception:
            pass

        try:
            if hasattr(mesh, 'use_auto_smooth'):
                mesh.use_auto_smooth = False
        except Exception:
            pass
    except Exception as e:
        print(f"[THUG] reset_custom_normals failed: {e}")

#----------------------------------------------------------------------------------
def get_sphere_from_bbox(bbox):
    bbox_min, bbox_max = bbox

    sphere_x = (bbox_min[0] + bbox_max[0]) / 2.0
    sphere_y = (bbox_min[1] + bbox_max[1]) / 2.0
    sphere_z = (bbox_min[2] + bbox_max[2]) / 2.0
    sphere_radius = (
        (sphere_x - bbox_min[0]) ** 2 +
        (sphere_y - bbox_min[1]) ** 2 +
        (sphere_z - bbox_min[2]) ** 2) ** 0.5

    return (sphere_x, sphere_y, sphere_z, sphere_radius)


#----------------------------------------------------------------------------------
def get_bbox2(vertices, matrix=mathutils.Matrix.Identity(4), is_park_editor=False):
    min_x = float("inf")
    min_y = float("inf")
    min_z = float("inf")
    max_x = -float("inf")
    max_y = -float("inf")
    max_z = -float("inf")
    for v in vertices:
        v = to_thug_coords(matrix * v.co)
        # v = v.co
        min_x = min(v[0], min_x)
        min_y = min(v[1], min_y)
        min_z = min(v[2], min_z)
        max_x = max(v[0], max_x)
        max_y = max(v[1], max_y)
        max_z = max(v[2], max_z)
        
    # bounding box is calculated differently for park dictionaries!
    if is_park_editor and len(vertices): 
        #print("bounding box was: " + str(min_x) + "x" + str(min_z) + ", " + str(max_x) + "x" + str(max_z))
        new_min_x = (min_x / 60.0)
        new_min_z = (min_z / 60.0)
        new_max_x = (max_x / 60.0)
        new_max_z = (max_z / 60.0)
        
        if new_min_x < 0: min_x = float(round(new_min_x) * 60);
        else: min_x = float(round(new_min_x) * 60);
        if new_min_z < 0: min_z = float(round(new_min_z) * 60);
        else: min_z = float(round(new_min_z) * 60);
        
        if new_max_x < 0: max_x = float(round(new_max_x) * 60);
        else: max_x = float(round(new_max_x) * 60);
        if new_max_z < 0: max_z = float(round(new_max_z) * 60);
        else: max_z = float(round(new_max_z) * 60);
        
        # Fix bounding boxes that aren't aligned at center
        if (max_x + min_x) > 0: min_x = (max_x * -1.0)
        elif (max_x + min_x) < 0: max_x = (min_x * -1.0)
        if (max_z + min_z) > 0: min_z = (max_z * -1.0)
        elif (max_z + min_z) < 0: max_z = (min_z * -1.0)
            
        min_y = 0.0
        #print("NEW bounding box is: " + str(min_x) + "x" + str(min_z) + ", " + str(max_x) + "x" + str(max_z))
    return ((min_x, min_y, min_z, 1.0), (max_x, max_y, max_z, 1.0))

def to_thug_coords(v):
    return (v[0] * global_export_scale, v[2] * global_export_scale, -v[1] * global_export_scale)
#----------------------------------------------------------------------------------
def to_thug_coords_ns(v):
    return (v[0], v[2], -v[1])
#----------------------------------------------------------------------------------
def from_thug_coords(v):
    return (v[0], -v[2], v[1])
#----------------------------------------------------------------------------------
def from_thps_coords(v):
    return (v[0]/2.25, v[2]/2.25, -v[1]/2.25)
#----------------------------------------------------------------------------------
def to_thug_coords_rot(v):
    return (v[0], v[2] + math.radians(180), -v[1])
#----------------------------------------------------------------------------------
def crc_from_string(string):
    string = string.lower()
    string = string.replace(b"/", b"\\")
    rc = 0xffffffff

    for ch in string:
        rc = CRCTable[(rc ^ ch) & 0xff] ^ ((rc >> 8) & 0x00ffffff)

    return rc
#----------------------------------------------------------------------------------
def safe_mode_set(mode):
    if bpy.context.mode != mode:
        bpy.ops.object.mode_set(mode=mode)
#----------------------------------------------------------------------------------
def is_string_clean(string):
    import re
    return re.search(r"[^A-Za-z0-9_]", string) is None
#----------------------------------------------------------------------------------
def get_clean_string(string):
    import re
    return re.sub(r"[^A-Za-z0-9_]", "_", string)
#----------------------------------------------------------------------------------
def get_clean_name(ob):
    clean_name = get_clean_string(ob.name)
    
    # This is from an imported level, so drop the _COL/_SCN part
    if clean_name.endswith("_COL") or clean_name.endswith("_SCN"): 
        return clean_name[:-4]
    # This is from an imported level, so drop the scn_ part
    elif clean_name.startswith("scn_") or clean_name.startswith("col_"):
        return clean_name[4:] 
    else:
        return clean_name
                        
#----------------------------------------------------------------------------------
def get_scale_matrix(ob):
    matrix = mathutils.Matrix.Identity(4)
    matrix[0][0] = ob.scale[0]
    matrix[1][1] = ob.scale[1]
    matrix[2][2] = ob.scale[2]
    return matrix
    
#----------------------------------------------------------------------------------
def _generate_lambert_shading(ob):
    if not hasattr(ob.data, 'polygons') or len(ob.data.polygons) == 0:
        LOG.debug("_generate_lambert_shading got a bad object! {}".format(ob))
        return

    mesh = ob.data
    color_attr_name = "THUG_LAMBERT"
    if color_attr_name in mesh.color_attributes:
        mesh.color_attributes.remove(mesh.color_attributes[color_attr_name])
    color_attr = mesh.color_attributes.new(name=color_attr_name, type="FLOAT_COLOR", domain="CORNER")

    sun = bpy.data.objects.get("Sun")
    if sun and sun.type == "LIGHT":
        sun_dir = -(sun.matrix_world @ mathutils.Vector((0.0, 0.0, -1.0, 0.0))).to_3d()
        sun_color = sun.data.color * sun.data.energy
    else:
        sun_dir = mathutils.Vector((0.5, 0.5, 0.5)).normalized()
        sun_color = mathutils.Color((0.6, 0.6, 0.6))

    ob.data.calc_normals_split()
    mat = ob.matrix_world.copy()
    mat.invert()
    mat.transpose()

    ambient_color = mathutils.Color((1.0, 1.0, 1.0))
    world = bpy.context.scene.world
    if world and hasattr(world, "light_settings") and world.light_settings.use_environment_light:
        ambient_color *= world.light_settings.environment_energy
    else:
        ambient_color *= 0.25

    for i, loop in enumerate(ob.data.loops):
        normal = mat @ mathutils.Vector((loop.normal[0], loop.normal[1], loop.normal[2], 0.0))
        normal.normalize()
        sunlight_strength = sun_dir.dot(normal)
        if sunlight_strength < 0:
            sunlight_strength = 0.0
        c = ambient_color + (sun_color * sunlight_strength)
        color_attr.data[i].color = (c.r, c.g, c.b, 1.0)

    ob.data.update()
    return

        
def is_duplicate_mesh(ob, compare_ob):
    meshes = [ compare_ob ]
    is_dupe = False
    
    ref_vertices = []
    for f in ob.data.polygons:
        for idx in f.vertices:
            ref_vertices.append(ob.data.vertices[idx].co[0])
            ref_vertices.append(ob.data.vertices[idx].co[1])
            ref_vertices.append(ob.data.vertices[idx].co[2])
    ref_vertices = sorted(ref_vertices, key=float)
    
    for obj in meshes:
        if obj.matrix_world != ob.matrix_world:
            continue
        if not hasattr(obj.data, 'polygons'):
            continue
        
        vertices = []
        for f in obj.data.polygons:
            for idx in f.vertices:
                vertices.append(obj.data.vertices[idx].co[0])
                vertices.append(obj.data.vertices[idx].co[1])
                vertices.append(obj.data.vertices[idx].co[2])
        vertices = sorted(vertices, key=float)
        
        if len(vertices) != len(ref_vertices):
            continue
        
        vert_index = -1
        should_continue = False
        for vert in ref_vertices:
            vert_index += 1
            if vert != vertices[vert_index]:
                should_continue = True
                break
        if should_continue:
            continue
        is_dupe = True
        break
        
    if is_dupe:
        print("DUPLICATE OBJECT FOUND: {}".format(ob.name))
        return True
    return False
    
# CLASSES
#############################################
class Reader(object):
    def __init__(self, buf):
        self.offset = 0
        self.buf = buf

    def read(self, fmt):
        result = struct.unpack_from(fmt, self.buf, self.offset)
        self.offset += struct.calcsize(fmt)
        return result

    def u8(self):
        return self.read("B")[0]

    def u16(self):
        return self.read("H")[0]

    def u32(self):
        return self.read("I")[0]

    def u64(self):
        return self.read("Q")[0]

    def i32(self):
        return self.read("i")[0]

    def i64(self):
        return self.read("q")[0]

    def f32(self):
        return self.read("f")[0]

    def bool(self):
        return self.read("?")[0]

    def vec3f(self):
        return self.read("3f")
#----------------------------------------------------------------------------------
class Printer(object):
    def __init__(self):
        self.on = True

    def __call__(self, message, *stuff):
        if self.on:
            # LOG.debug(message, stuff)
            print(message.format(*stuff))
        return stuff[0]
#----------------------------------------------------------------------------------
class MaskSet:
    def __init__(self, width, height, depth):
        self.x = 0
        self.y = 0
        self.z = 0
        bit = 1
        index = 1
        while bit < width or bit < height or bit < depth:
            if bit < width:
                self.x |= index
                index <<= 1
            if bit < height:
                self.y |= index
                index <<= 1
            if bit < depth:
                self.z |= index
                index <<= 1
            bit <<= 1