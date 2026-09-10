#############################################
# THUG1/2 MATERIAL IMPORT/CONFIGURE/EXPORT
#############################################
import bpy
import struct
import mathutils
import math
import os, sys
from bpy.props import *
from . constants import *
from . helpers import *
from . tex import *

# METHODS
#############################################
def _ensure_default_material_exists():
    if "_THUG_DEFAULT_MATERIAL_" in bpy.data.materials:
        return

    default_mat = bpy.data.materials.new(name="_THUG_DEFAULT_MATERIAL_")
    default_mat.use_nodes = True
    if hasattr(default_mat, "diffuse_color"):
        default_mat.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    nodes = default_mat.node_tree.nodes
    if nodes:
        for node in list(nodes):
            if node.type == "OUTPUT_MATERIAL":
                continue
            nodes.remove(node)
        principled = nodes.new(type="ShaderNodeBsdfPrincipled")
        principled.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
        principled.inputs["Alpha"].default_value = 1.0
        principled.inputs["Roughness"].default_value = 0.8
        output = nodes.new(type="ShaderNodeOutputMaterial")
        default_mat.node_tree.links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    default_mat.node_tree.nodes.update()

def _thug_material_pass_props_color_updated(self, context):
    from bl_ui.properties_material import active_node_mat
    if not context or not context.object:
        return
    mat = context.object.active_material
    if not mat:
        return
    idblock = active_node_mat(mat)
    r, g, b = self.color
    idblock.active_texture.factor_red = r * 2
    idblock.active_texture.factor_green = g * 2
    idblock.active_texture.factor_blue = b * 2

def rename_imported_materials():
    for mat in bpy.data.materials:
        if "thug_mat_name_checksum" in mat and mat["thug_mat_name_checksum"] != "":
            mat.name = mat["thug_mat_name_checksum"]


def _sanitize_base_color(default_color):
    if default_color is None:
        return (1.0, 1.0, 1.0, 1.0)
    try:
        r, g, b, a = default_color[:4]
    except Exception:
        try:
            r, g, b = default_color[:3]
            a = 1.0
        except Exception:
            return (1.0, 1.0, 1.0, 1.0)
    if (r + g + b) <= 0.0:
        return (1.0, 1.0, 1.0, 1.0)
    return (float(r), float(g), float(b), float(a))


def ensure_principled_material(mat, default_color=(1.0, 1.0, 1.0, 1.0)):
    if mat is None:
        return None
    mat.use_nodes = True
    if hasattr(mat, "use_backface_culling"):
        mat.use_backface_culling = False
    if hasattr(mat, "blend_method"):
        try:
            mat.blend_method = "OPAQUE"
        except Exception:
            pass
    if hasattr(mat, "diffuse_color"):
        mat.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    if not hasattr(mat, "node_tree") or mat.node_tree is None:
        return None

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    # Preserve existing texture/image nodes. Older versions of this helper
    # rebuilt the node tree from scratch, which silently deleted the texture
    # nodes created by the THPS material importer.
    # Remove only non-shader helper nodes that this function may have created
    # previously; never remove TEX_IMAGE nodes.
    for node in list(nodes):
        if node.type in {"TEX_IMAGE", "UV_MAP", "TEX_COORD", "MIX", "MIX_RGB", "VERTEX_COLOR", "ATTRIBUTE", "VALTORGB", "MATH"}:
            continue
        if node.type not in {"BSDF_PRINCIPLED", "OUTPUT_MATERIAL"}:
            nodes.remove(node)

    bsdf = next((node for node in nodes if node.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")
        bsdf.name = "Principled BSDF"
    base_color = _sanitize_base_color(default_color)
    bsdf.inputs["Base Color"].default_value = base_color
    if "Alpha" in bsdf.inputs:
        bsdf.inputs["Alpha"].default_value = 1.0
    if "Roughness" in bsdf.inputs:
        bsdf.inputs["Roughness"].default_value = 0.8
    if bsdf.inputs.get("Specular IOR Level") is not None:
        bsdf.inputs["Specular IOR Level"].default_value = 0.0
    bsdf.location = (200, 0)

    output = next((node for node in nodes if node.type == "OUTPUT_MATERIAL"), None)
    if output is None:
        output = nodes.new(type="ShaderNodeOutputMaterial")
        output.name = "Material Output"
    output.location = (500, 0)
    output.is_active_output = True
    if bsdf.outputs.get("BSDF") and output.inputs.get("Surface"):
        for link in list(bsdf.outputs["BSDF"].links):
            if link.to_node == output:
                continue
            links.remove(link)
        if not output.inputs["Surface"].is_linked:
            links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    nodes.update()
    return bsdf


def _get_checksum_image_names(tex_checksum):
    values = []
    for value in (
        hex(tex_checksum),
        str(tex_checksum),
        f"{tex_checksum:x}",
        f"{tex_checksum:X}",
        f"0x{tex_checksum:08x}",
        f"0X{tex_checksum:08X}",
    ):
        if value not in values:
            values.append(value)
    return values


def _resolve_texture_image_from_checksum(tex_checksum, directory=None):
    """Resolve a THPS image by checksum, including images whose Blender name
    acquired a .001-style suffix because the name already existed."""
    if tex_checksum is None:
        return None

    checksum = int(tex_checksum) & 0xFFFFFFFF

    # Prefer an explicit checksum property written by tex.py.
    for image in bpy.data.images:
        try:
            if int(str(image.get("thug_texture_checksum", "-1")), 0) == checksum:
                return image
        except Exception:
            pass

    for image_name in _get_checksum_image_names(checksum):
        image = bpy.data.images.get(image_name)
        if image is not None:
            return image

    if not directory:
        return None

    dirs_to_check = []
    for candidate in (directory, os.path.join(directory, "tex"), os.path.join(directory, "textures")):
        if candidate not in dirs_to_check:
            dirs_to_check.append(candidate)

    for search_dir in dirs_to_check:
        if not os.path.isdir(search_dir):
            continue

        for filename in (
            f"{tex_checksum:08x}.tga",
            f"{tex_checksum:08x}.png",
            f"{tex_checksum:08X}.tga",
            f"{tex_checksum:08X}.png",
            f"{tex_checksum:x}.tga",
            f"{tex_checksum:x}.png",
            f"{hex(tex_checksum)}.tga",
            f"{hex(tex_checksum)}.png",
            str(tex_checksum),
        ):
            for path in (
                os.path.join(search_dir, filename),
                os.path.join(search_dir, "tex", filename),
                os.path.join(search_dir, "textures", filename),
            ):
                if os.path.isfile(path):
                    try:
                        return bpy.data.images.load(path)
                    except Exception:
                        return None

    return None


def bind_image_texture_to_principled(mat, image, bsdf=None, image_name="THUG_Tex", index=0):
    if mat is None:
        return None
    if not hasattr(mat, "node_tree") or mat.node_tree is None:
        return None
    if image is None:
        return None

    if bsdf is None:
        bsdf = next((node for node in mat.node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = ensure_principled_material(mat)

    output = next((node for node in mat.node_tree.nodes if node.type == "OUTPUT_MATERIAL"), None)
    if output is None:
        output = mat.node_tree.nodes.new(type="ShaderNodeOutputMaterial")

    tex_node = next((node for node in mat.node_tree.nodes if node.type == "TEX_IMAGE" and getattr(node, "image", None) == image), None)
    if tex_node is None:
        tex_node = next((node for node in mat.node_tree.nodes if node.type == "TEX_IMAGE"), None)
    if tex_node is None:
        tex_node = mat.node_tree.nodes.new(type="ShaderNodeTexImage")

    tex_node.name = f"{image_name}_{index}"
    tex_node.label = f"Pass {index}"
    tex_node.location = (-300, -index * 180)
    tex_node.image = image

    # The SCN material records pass N -> UV set N. The mesh may not exist yet,
    # so create the node now; rebuild_thps_uv_nodes_for_object() will rebind it
    # to the actual layer after the mesh is constructed.
    mesh_uv_name = str(index)
    uv_node = next((n for n in mat.node_tree.nodes
                    if n.type == "UVMAP" and n.name == f"THUG UV Pass {index}"), None)
    if uv_node is None:
        uv_node = mat.node_tree.nodes.new(type="ShaderNodeUVMap")
        uv_node.name = f"THUG UV Pass {index}"
        uv_node.label = f"UV Set {index}"
        uv_node.location = (-550, -index * 180)
    uv_node.uv_map = mesh_uv_name

    vector = tex_node.inputs.get("Vector")
    uv_output = uv_node.outputs.get("UV")
    if vector is not None and uv_output is not None:
        for link in list(vector.links):
            mat.node_tree.links.remove(link)
        mat.node_tree.links.new(uv_output, vector)

    if hasattr(tex_node, "extension"):
        tex_node.extension = "REPEAT"

    base_color_socket = bsdf.inputs.get("Base Color")
    if base_color_socket is not None and tex_node.outputs.get("Color"):
        if not base_color_socket.is_linked:
            if not any(link.from_node == tex_node and link.from_socket.name == "Color" and link.to_socket == base_color_socket for link in mat.node_tree.links):
                mat.node_tree.links.new(tex_node.outputs["Color"], base_color_socket)

    if bsdf.outputs.get("BSDF") and output.inputs.get("Surface"):
        if not output.inputs["Surface"].is_linked:
            mat.node_tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    return tex_node


def get_mix_input(node, *possible_names, fallback_index=0):
    if node is None or not hasattr(node, "inputs"):
        return None
    for name in possible_names:
        if name in node.inputs:
            return node.inputs[name]
    if len(node.inputs) > fallback_index:
        return node.inputs[fallback_index]
    return None


def _set_white_socket_default(socket):
    if socket is not None and not socket.is_linked:
        socket.default_value = (1.0, 1.0, 1.0, 1.0)


def ensure_vertex_color_material_input(mat, attribute_name="THUG_COLOR", bsdf=None):
    if mat is None or mat.node_tree is None:
        return None
    if not mat.get("thug_has_vertex_colors", False):
        return None
    if bsdf is None:
        bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return None

    if bsdf.inputs.get("Base Color") is not None and bsdf.inputs["Base Color"].is_linked:
        return None

    color_attr_node = None
    if hasattr(bpy.types, "ShaderNodeColorAttribute"):
        color_attr_node = mat.node_tree.nodes.new(type="ShaderNodeColorAttribute")
        color_attr_node.name = "THUG Vertex Color"
        color_attr_node.location = (-500, 200)
        for candidate_name in (attribute_name, "Col", "Color", "color"):
            try:
                color_attr_node.layer_name = candidate_name
                break
            except Exception:
                pass
    elif hasattr(bpy.types, "ShaderNodeAttribute"):
        color_attr_node = mat.node_tree.nodes.new(type="ShaderNodeAttribute")
        color_attr_node.name = "THUG Vertex Color"
        color_attr_node.location = (-500, 200)
        for candidate_name in (attribute_name, "Col", "Color", "color"):
            try:
                color_attr_node.attribute_name = candidate_name
                break
            except Exception:
                pass

    if color_attr_node is None:
        return None

    mix_node = mat.node_tree.nodes.new(type="ShaderNodeMix")
    mix_node.name = "THUG Color Mix"
    mix_node.location = (-200, 200)
    if hasattr(mix_node, "data_type"):
        mix_node.data_type = "RGBA"
    if hasattr(mix_node, "blend_type"):
        mix_node.blend_type = "MULTIPLY"

    factor_input = get_mix_input(mix_node, "Factor", "Fac", fallback_index=0)
    if factor_input is not None:
        factor_input.default_value = 1.0

    a_input = get_mix_input(mix_node, "A", "Color1", fallback_index=1)
    b_input = get_mix_input(mix_node, "B", "Color2", fallback_index=2)
    base_color_socket = bsdf.inputs.get("Base Color")

    if a_input is not None:
        if color_attr_node.outputs.get("Color") and not a_input.is_linked:
            mat.node_tree.links.new(color_attr_node.outputs["Color"], a_input)
        else:
            _set_white_socket_default(a_input)
    if b_input is not None:
        if not b_input.is_linked:
            b_input.default_value = (1.0, 1.0, 1.0, 1.0)
    if base_color_socket is not None and mix_node.outputs.get("Result"):
        if not base_color_socket.is_linked:
            mat.node_tree.links.new(mix_node.outputs["Result"], base_color_socket)
    return color_attr_node


def _find_loaded_texture_image(tex_checksum):
    """Find a .tex image by its unsigned 32-bit THPS checksum."""
    checksum = int(tex_checksum) & 0xFFFFFFFF

    # Preferred: explicit checksum metadata. Store as a string because Blender
    # custom integer properties are signed 32-bit, while THPS checksums are
    # unsigned 32-bit values.
    for image in bpy.data.images:
        try:
            raw = image.get("thug_texture_checksum")
            if raw is not None and int(str(raw), 0) == checksum:
                return image
        except (TypeError, ValueError, OverflowError):
            pass

    # Fallback: the original addon names imported images with the checksum.
    for name in _get_checksum_image_names(checksum):
        image = bpy.data.images.get(name)
        if image is not None:
            return image

    # Blender can append .001/.002 when a datablock name collides.
    prefix = f"0x{checksum:08x}"
    for image in bpy.data.images:
        if image.name.lower().startswith(prefix):
            return image

    return None


def _translate_thps_blend_mode(blend_mode):
    """Map common THPS blend mode names to Blender MixRGB modes."""
    name = str(blend_mode or "").upper()
    if "SUBTRACT" in name:
        return "SUBTRACT"
    if "MODULATE" in name or "MULTIPLY" in name:
        return "MULTIPLY"
    if "ADD" in name or "BRIGHTEN" in name:
        return "ADD"
    if "SCREEN" in name:
        return "SCREEN"
    return "MIX"



def rebuild_thps_uv_nodes_for_object(obj):
    """Bind THPS Image Texture nodes to the actual UV layers on a mesh object.

    Material datablocks are parsed before SCN meshes are constructed, so UV Map
    nodes created during material parsing may exist before the target mesh has
    its final UV layers. Rebind them after the mesh is built using the actual
    layer names and pass index.
    """
    if obj is None or getattr(obj, "type", None) != "MESH":
        return
    mesh = getattr(obj, "data", None)
    if mesh is None or not hasattr(mesh, "uv_layers"):
        return

    uv_layers = list(mesh.uv_layers)
    print("[THUG] Final UV binding for {}: {}".format(
        obj.name, [uv.name for uv in uv_layers]))

    for slot_index, slot in enumerate(obj.material_slots):
        mat = getattr(slot, "material", None)
        if mat is None or mat.node_tree is None:
            continue

        nodes = mat.node_tree.nodes
        links = mat.node_tree.links

        for tex_node in [n for n in nodes if n.type == "TEX_IMAGE" and
                         str(getattr(n, "label", "")).startswith("Pass ")]:
            try:
                pass_index = int(str(tex_node.label).split()[-1])
            except Exception:
                continue

            if not uv_layers:
                continue

            # THPS export convention is UV pass N -> UV set N. If a file has
            # fewer UV sets than passes, safely fall back to UV set 0.
            uv_name = uv_layers[pass_index].name if pass_index < len(uv_layers) else uv_layers[0].name

            uv_node = next((n for n in nodes
                            if n.type == "UVMAP" and n.name == f"THUG UV Pass {pass_index}"), None)
            if uv_node is None:
                uv_node = nodes.new(type="ShaderNodeUVMap")
                uv_node.name = f"THUG UV Pass {pass_index}"
                uv_node.label = f"UV Set {pass_index}"
                uv_node.location = (-550, -pass_index * 180)

            uv_node.uv_map = uv_name

            vector = tex_node.inputs.get("Vector")
            uv_output = uv_node.outputs.get("UV")
            if vector is not None and uv_output is not None:
                for link in list(vector.links):
                    links.remove(link)
                links.new(uv_output, vector)

            print("[THUG]   {} -> {} (pass {})".format(
                mat.name, uv_name, pass_index))


def _rebuild_thps_diffuse_surface(mat, pass_nodes, pass_props):
    """Build a Blender-5.x approximation of the original THPS pass stack.

    The legacy addon did not simply connect pass 0 to Principled. It created
    one UV/image node per pass, multiplied each image by the pass color, and
    composited later passes according to their THPS blend mode.
    """
    if mat is None or mat.node_tree is None or not pass_nodes:
        return

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = ensure_principled_material(mat)
    if bsdf is None:
        return

    base = bsdf.inputs.get("Base Color")
    if base is None:
        return

    # Remove only previous links feeding Base Color.
    for link in list(base.links):
        links.remove(link)

    colors = []
    for i, (tex_node, props) in enumerate(zip(pass_nodes, pass_props)):
        if tex_node is None:
            continue
        color = getattr(props, "color", (0.5, 0.5, 0.5))
        try:
            # The original shader uses pass color * 2.0.
            cr, cg, cb = float(color[0]) * 2.0, float(color[1]) * 2.0, float(color[2]) * 2.0
        except Exception:
            cr = cg = cb = 1.0
        cr, cg, cb = max(0.0, min(1.0, cr)), max(0.0, min(1.0, cg)), max(0.0, min(1.0, cb))

        color_node = nodes.new(type="ShaderNodeMixRGB")
        color_node.name = f"THUG Pass Color {i}"
        color_node.label = f"Pass Color {i}"
        color_node.blend_type = "MULTIPLY"
        color_node.inputs[0].default_value = 1.0
        color_node.inputs[2].default_value = (cr, cg, cb, 1.0)
        color_node.location = (-50, -i * 180)
        links.new(tex_node.outputs["Color"], color_node.inputs[1])
        colors.append(color_node)

    if not colors:
        return
    current = colors[0].outputs[0]

    for i in range(1, len(colors)):
        mix = nodes.new(type="ShaderNodeMixRGB")
        mix.name = f"THUG Pass Mix {i}"
        mix.label = f"Pass Mix {i}"
        props = pass_props[i]
        mix.blend_type = _translate_thps_blend_mode(getattr(props, "blend_mode", ""))
        mix.inputs[0].default_value = 1.0
        mix.location = (180, -i * 180)
        links.new(current, mix.inputs[1])
        links.new(colors[i].outputs[0], mix.inputs[2])
        current = mix.outputs[0]

    links.new(current, base)



def read_materials(reader, printer, num_materials, directory, operator, output_file=None, texture_map=None, texture_prefix=None):
    import os
    r = reader
    p = printer

    for i in range(num_materials):
        p("material {}", i)
        mat_checksum = p("  material checksum: {}", hex(r.u32()))
        mat_name_checksum = p("  material name checksum: {}", hex(r.u32()))
        # Match the original addon: name checksum is the stable material
        # identity, so repeated material records reuse one datablock.
        blender_mat = bpy.data.materials.get(str(mat_name_checksum))
        if blender_mat is None:
            blender_mat = bpy.data.materials.new(str(mat_checksum))
        ps = blender_mat.thug_material_props
        blender_mat["thug_mat_name_checksum"] = mat_name_checksum

        num_passes = p("  material passes: {}", r.u32())
        ps.alpha_cutoff = p("  alpha cutoff: {}", r.u32() % 256)
        ps.sorted = p("  sorted: {}", r.bool())
        ps.draw_order = p("  draw order: {}", r.f32())
        ps.single_sided = p("  single_sided: {}", r.bool())
        ps.no_backface_culling = p("  no backface culling: {}", r.bool())
        ps.z_bias = p("  z bias: {}", r.i32())

        ps.grassify = p("  grassify: {}", r.bool())
        if ps.grassify:
            ps.grass_height = p("  grass height: {}", r.f32())
            ps.grass_layers = p("  grass layers: {}", r.i32())

        ps.specular_power = p("  specular power: {}", r.f32())
        if ps.specular_power > 0.0:
            ps.specular_color = p("  specular color: {}", r.read("3f"))

        bsdf = ensure_principled_material(blender_mat, default_color=(1.0, 1.0, 1.0, 1.0))
        blender_mat["thug_has_vertex_colors"] = False
        imported_pass_nodes = []
        imported_pass_props = []

        for j in range(num_passes):
            blender_tex = bpy.data.textures.new("{}/{}".format(mat_name_checksum, j), "IMAGE")
            pps = blender_tex.thug_material_pass_props
            p("  pass #{}", j)
            tex_checksum = p("    pass texture checksum: {}", r.u32())
            actual_tex_checksum = hex(tex_checksum)
            image_name = str(actual_tex_checksum)

            # .tex import names images with the checksum.  Also accept an
            # explicit checksum property because Blender may append .001,
            # .002, etc. when a datablock already exists.
            image = _find_loaded_texture_image(tex_checksum)
            if image is None:
                image = _resolve_texture_image_from_checksum(tex_checksum, directory)

            if image is None:
                image = bpy.data.images.new(
                    name=image_name, width=1, height=1,
                    alpha=True, float_buffer=False,
                )
                image.pixels = [1.0, 1.0, 1.0, 1.0]
                image["thug_missing_texture"] = True
                image["thug_texture_checksum"] = f"0x{(int(tex_checksum) & 0xFFFFFFFF):08X}"
                print("    WARNING: texture image not found for checksum {}".format(image_name))
            else:
                try:
                    image["thug_texture_checksum"] = f"0x{(int(tex_checksum) & 0xFFFFFFFF):08X}"
                except Exception:
                    pass

            try:
                blender_tex["thug_texture_checksum"] = f"0x{(int(tex_checksum) & 0xFFFFFFFF):08X}"
            except Exception:
                pass

            blender_tex.image = image
            try:
                print("    resolved image: {} ({}x{})".format(image.name, image.size[0], image.size[1]))
            except Exception:
                print("    resolved image: <invalid image>")

            if blender_mat.node_tree is not None and bsdf is not None:
                image_node = bind_image_texture_to_principled(blender_mat, image, bsdf=bsdf, image_name="THUG_Tex", index=j)
                if image_node is not None:
                    imported_pass_nodes.append(image_node)
                if image_node is not None and j == 0:
                    base_color_socket = bsdf.inputs.get("Base Color")
                    if base_color_socket and image_node.outputs.get("Color") and not base_color_socket.is_linked:
                        blender_mat.node_tree.links.new(image_node.outputs["Color"], base_color_socket)

                    alpha_socket = bsdf.inputs.get("Alpha")
                    if alpha_socket and image_node.outputs.get("Alpha") and not alpha_socket.is_linked:
                        blender_mat.node_tree.links.new(image_node.outputs["Alpha"], alpha_socket)

            if False and output_file and (True or texture_map) and j == 0:
                """
                output_file.write("map_Kd {}.tex_{}.tga\n".format(
                    texture_prefix,
                    texture_map.get(tex_checksum, 0)))
                """
                output_file.write("map_Kd {}.tga\n".format(tex_checksum))
            pass_flags = p("    pass material flags: {}", r.u32())
            p("    pass has color: {}", r.bool())
            pps.color = p("    pass color: {}", r.read("3f"))
            pps.blend_mode = BLEND_MODES[r.u32()]
            pps.blend_fixed_alpha = r.u32() & 0xFF

            imported_pass_props.append(pps)
            # The corresponding Image Texture node was created above; record it
            # after parsing the pass properties so the final stack can be built.

            if hasattr(blender_mat, "blend_method"):
                try:
                    if pass_flags & MATFLAG_TRANSPARENT:
                        blender_mat.blend_method = "BLEND"
                    else:
                        blender_mat.blend_method = "OPAQUE"
                except Exception:
                    pass

            if hasattr(blender_mat, "surface_render_method"):
                try:
                    blender_mat.surface_render_method = "BLEND" if (pass_flags & MATFLAG_TRANSPARENT) else "OPAQUE"
                except Exception:
                    pass

            pps.u_addressing = "Repeat" if p("    pass u addressing: {}", r.u32()) == 0 else "Clamp"
            pps.v_addressing = "Repeat" if p("    pass v addressing: {}", r.u32()) == 0 else "Clamp"

            if blender_mat.node_tree is not None:
                for node in blender_mat.node_tree.nodes:
                    if node.type == "TEX_IMAGE" and getattr(node, "image", None) == image:
                        if hasattr(node, "extension"):
                            node.extension = "EXTEND" if pps.u_addressing == "Clamp" else "REPEAT"
                        break

            pps.envmap_multiples = p("    pass envmap uv tiling multiples: {}", r.read("2f"))
            pps.filtering_mode = r.u32()
            p("    pass filtering mode: {}", pps.filtering_mode)
            pps.test_passes = pass_flags

            if pass_flags & MATFLAG_TEXTURED:
                pps.pf_textured = True
            if pass_flags & MATFLAG_TRANSPARENT:
                pps.pf_transparent = True
            else:
                pps.pf_transparent = False
            if pass_flags & MATFLAG_ENVIRONMENT:
                pps.pf_environment = True
            if pass_flags & MATFLAG_DECAL:
                pps.pf_decal = True
            if pass_flags & MATFLAG_SMOOTH:
                pps.pf_smooth = True

            if pass_flags & MATFLAG_PASS_IGNORE_VERTEX_ALPHA:
                pps.ignore_vertex_alpha = True

            if pass_flags & MATFLAG_UV_WIBBLE:
                p("    pass has uv wibble!", None)
                pps.has_uv_wibbles = True
                uvs = pps.uv_wibbles
                uvs.uv_velocity = r.read("2f")
                uvs.uv_frequency = r.read("2f")
                uvs.uv_amplitude = r.read("2f")
                uvs.uv_phase = r.read("2f")

            if j == 0 and pass_flags & MATFLAG_VC_WIBBLE:
                p("    pass has vc wibble!", None)
                for k in range(r.u32()):
                    num_keys = r.u32()
                    r.i32()
                    r.read(str(num_keys * 2) + "i")

            if pass_flags & MATFLAG_PASS_TEXTURE_ANIMATES:
                p("    pass texture animates!", None)
                pps.has_animated_texture = True
                at = pps.animated_texture
                num_keyframes = r.i32()
                at.period = r.i32()
                at.iterations = r.i32()
                at.phase = r.i32()

                for k in range(num_keyframes):
                    atkf = at.keyframes.add()
                    atkf.time = r.u32()
                    atkf.image = hex(r.u32())

            if tex_checksum:
                p("    mmag: {}", r.u32())
                p("    mmin: {}", r.u32())
                p("    k: {}", r.f32())
                p("    l: {}", r.f32())
            else:
                r.read("4I")

        if imported_pass_nodes and imported_pass_props:
            _rebuild_thps_diffuse_surface(blender_mat, imported_pass_nodes, imported_pass_props[:len(imported_pass_nodes)])


def export_ugplus_material(m, output_file, target_game, operator=None):
    def w(fmt, *args):
        output_file.write(struct.pack(fmt, *args))
    
    mprops = m.thug_material_props
    
    shader_id = -5.40 # PBR
    if mprops.ugplus_shader == 'PBR_Lightmapped':
        shader_id = -8.0
    elif mprops.ugplus_shader == 'Water':
        shader_id = -1.08
    elif mprops.ugplus_shader == 'Water_Custom':
        shader_id = -3.16
    elif mprops.ugplus_shader == 'Water_Displacement':
        shader_id = -23.42
    elif mprops.ugplus_shader == 'Skybox':
        shader_id = -8.15
    elif mprops.ugplus_shader == 'Cloud':
        shader_id = -16.0

    export_textures = []
    # Now we export the textures in a specific order, depending on the shader
    if mprops.ugplus_shader == 'PBR':
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_diffuse, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_normal, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_reflection, 'flags': MATFLAG_BUMP_LOAD_MATRIX })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_detail, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_lightmap, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_weathermask, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_snow, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_specular, 'flags': 0 })
    elif mprops.ugplus_shader == 'PBR_Lightmapped':
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_diffuse, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_normal, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_reflection, 'flags': MATFLAG_BUMP_LOAD_MATRIX })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_detail, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_lightmap, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_lightmap2, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_lightmap3, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_lightmap4, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_weathermask, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_snow, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_specular, 'flags': 0 })
        
    elif mprops.ugplus_shader == 'Skybox':
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_diffuse, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_diffuse_evening, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_diffuse_night, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_diffuse_morning, 'flags': 0 })
        
    elif mprops.ugplus_shader == 'Cloud':
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_cloud, 'flags': 0 })
        
    elif mprops.ugplus_shader == 'Water':
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_reflection, 'flags': MATFLAG_BUMP_LOAD_MATRIX })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_lightmap, 'flags': 0 }) 
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_lightmap2, 'flags': 0 }) 
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_lightmap3, 'flags': 0 }) 
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_lightmap4, 'flags': 0 }) 
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_fallback, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_detail, 'flags': 0 }) 
        
    elif mprops.ugplus_shader == 'Water_Custom':
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_normal, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_normal2, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_fallback, 'flags': 0 })
        #export_textures.append({ 'mat_node': mprops.ugplus_matslot_reflection, 'flags': MATFLAG_BUMP_LOAD_MATRIX })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_detail, 'flags': 0 }) 
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_lightmap, 'flags': 0 }) 
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_lightmap2, 'flags': 0 }) 
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_lightmap3, 'flags': 0 }) 
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_lightmap4, 'flags': 0 }) 
        
    elif mprops.ugplus_shader == 'Water_Displacement':
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_normal, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_normal2, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_displacement, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_displacement2, 'flags': 0 })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_lightmap, 'flags': 0 }) 
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_lightmap2, 'flags': 0 }) 
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_lightmap3, 'flags': 0 }) 
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_lightmap4, 'flags': 0 }) 
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_fallback, 'flags': 0 })
        #export_textures.append({ 'mat_node': mprops.ugplus_matslot_reflection, 'flags': MATFLAG_BUMP_LOAD_MATRIX })
        export_textures.append({ 'mat_node': mprops.ugplus_matslot_detail, 'flags': 0 }) 
        
    num_passes = 4 if len(export_textures) > 4 else len(export_textures)
    print("Material {} has {} passes".format(m.name, num_passes))
    
    if is_hex_string(m.name):
        checksum = int(m.name, 0)
    else:
        checksum = crc_from_string(bytes(m.name, 'ascii'))
    
    w("I", checksum)  # material checksum
    w("I", checksum)  # material name checksum
    w("I", num_passes)  # material passes
    w("I", mprops.alpha_cutoff)  # alpha cutoff (actually an unsigned byte)
    w("?", mprops.sorted)  # sorted?
    w("f", mprops.draw_order)  # draw order
    w("?", mprops.single_sided)  # single sided
    w("?", mprops.no_backface_culling)  # no backface culling
    w("i", mprops.z_bias)  # z-bias

    #grassify = False
    w("?", mprops.grassify)  # grassify
    if mprops.grassify:  # if grassify
        print("EXPORTING GRASS MATERIAL!")
        w("f", mprops.grass_height)  # grass height
        w("i", mprops.grass_layers)  # grass layers
        
    w("f", shader_id)  # specular power (used to mark the shader ID for the new shaders)

    # Export all the textures we need for the shader, as gathered above
    tex_count = -1
    for node in export_textures:
        tex_count += 1
        tex = node['mat_node']
        tex_flags = node['flags']
        
        # Use the name of the texture image, or generate the color name (will be generated during .tex export)
        if tex.tex_image == None or tex.tex_image.name == '':
            colortex_name = 'io_thps_scene_Color_' + ''.join('{:02X}'.format(int(255*a)) for a in tex.tex_color)
            tex_checksum = crc_from_string(bytes(colortex_name, 'ascii'))
            
        elif is_hex_string(tex.tex_image.name):
            tex_checksum = int(tex.tex_image.name, 0)
        else:
            tex_checksum = crc_from_string(bytes(tex.tex_image.name, 'ascii'))
        
        # If the texture count is beyond the 4 texture limit, we are simply exporting frames for an animated texture
        # which is created at the bottom of this loop
        if tex_count > 3:
            w("I", (tex_count - 3))
            w("I", tex_checksum)  # texture checksum
            continue
            
        w("I", tex_checksum)  # texture checksum
        pass_flags = MATFLAG_SMOOTH | MATFLAG_TEXTURED
        pass_flags |= tex_flags
        if tex_count == 3 and len(export_textures) > 4:
            print("ANIMATED TEXTURE")
            pass_flags |= MATFLAG_PASS_TEXTURE_ANIMATES
        if tex_count <= 3 and tex.has_uv_wibbles:
            print("UV WIBBLES")
            pass_flags |= MATFLAG_UV_WIBBLE
        if tex_count == 0 and mprops.ugplus_trans:
            pass_flags |= MATFLAG_TRANSPARENT
        
        w("I", pass_flags)  # flags # 4132
        w("?", True)  # has color flag; seems to be ignored
        w("3f",  *(m.diffuse_color / 2.0))  # color
        
        #w("I", globals()[pprops.blend_mode] if pprops else vBLEND_MODE_DIFFUSE)
        
        if tex_count == 0 and mprops.ugplus_trans:
            w("I", vBLEND_MODE_BLEND)
        else:
            w("I", vBLEND_MODE_DIFFUSE)
            
        #w("I", pprops.blend_fixed_alpha if pprops else 0)
        w("I", 0)

        w("I", 0)  # u adressing (wrap, clamp, etc)
        w("I", 0)  # v adressing
        w("2f", *((3.0, 3.0)))  # envmap multiples
        w("I", 65540)  # filtering mode
        
        # Export UV wibbles on the first 4 passes
        if pass_flags & MATFLAG_UV_WIBBLE:
            w("2f", *tex.uv_wibbles.uv_velocity)
            w("2f", *tex.uv_wibbles.uv_frequency)
            w("2f", *tex.uv_wibbles.uv_amplitude)
            w("2f", *tex.uv_wibbles.uv_phase)
            
        # If we're going beyond pass 4, place additional textures in the animated texture slot for pass 4
        if tex_count == 3 and len(export_textures) > 4:
            w("i", len(export_textures) - 3)
            w("i", 108) # period
            w("i", 0) # iterations
            w("i", 0) # phase
            #for keyframe in at.keyframes:
            # Export the first animated frame here, since it will be the 4th texture slot we want anyway
            w("I", tex_count - 3)
            w("I", tex_checksum)  # texture checksum
            
            continue

        w("I", 1)  # MMAG
        w("I", 4)  # MMIN
        w("f", -8.0)  # K
        w("f", -8.0)  # L
    
    if tex_count > 3:
        # Need to write these after for the final texture pass if the material uses more than 4 passes
        w("I", 1)  # MMAG
        w("I", 4)  # MMIN
        w("f", -8.0)  # K
        w("f", -8.0)  # L
        
        
def export_materials(output_file, target_game, operator=None, is_model=False):
    def w(fmt, *args):
        output_file.write(struct.pack(fmt, *args))

    # out_objects = [o for o in bpy.data.objects if o.type == "MESH"]
    # out_materials = {o.active_material for o in out_objects if o.active_material}

    _ensure_default_material_exists()

    out_materials = bpy.data.materials[:]

    num_materials = len(out_materials)
    w("I", num_materials)
    for m in out_materials:
        LOG.debug("writing material: {}".format(m.name))
        mprops = m.thug_material_props
        
        # Export shader 
        if mprops.use_new_mats and mprops.ugplus_shader != 'None':
            LOG.debug("exporting new material system properties...")
            export_ugplus_material(m, output_file, target_game, operator)
            continue 
            
        #denetii - only include texture slots that affect the diffuse color in the Blender material
        passes = [tex_slot.texture for tex_slot in m.texture_slots if tex_slot and tex_slot.use and tex_slot.use_map_color_diffuse]
        if len(passes) > 4:
            if operator:
                operator.report(
                    {"WARNING"},
                    "Material {} has more than 4 passes (enabled texture slots). Using only the first 4.".format(m.name))
            passes = passes[:4]
        if not passes and m.name != "_THUG_DEFAULT_MATERIAL_":
            if operator:
                if not m.name.startswith('io_thps_scene_'):
                    operator.report({"WARNING"}, "Material {} has no passes (enabled texture slots). Using it's diffuse color.".format(m.name))
                passes = [None]

        if is_hex_string(m.name):
            checksum = int(m.name, 0)
        else:
            checksum = crc_from_string(bytes(m.name, 'ascii'))
        
        w("I", checksum)  # material checksum
        w("I", checksum)  # material name checksum
        w("I", len(passes) or 1)  # material passes
        w("I", mprops.alpha_cutoff)  # alpha cutoff (actually an unsigned byte)
        w("?", mprops.sorted)  # sorted?
        w("f", mprops.draw_order)  # draw order
        w("?", mprops.single_sided)  # single sided
        w("?", mprops.no_backface_culling)  # no backface culling
        w("i", mprops.z_bias)  # z-bias

        #grassify = False
        w("?", mprops.grassify)  # grassify
        if mprops.grassify:  # if grassify
            print("EXPORTING GRASS MATERIAL!")
            w("f", mprops.grass_height)  # grass height
            w("i", mprops.grass_layers)  # grass layers

        w("f", mprops.specular_power)  # specular power
        if mprops.specular_power > 0.0:
            w("3f", *mprops.specular_color)  # specular color

        # using_default_texture = not passes
        
        for texture in passes:
            pprops = texture and texture.thug_material_pass_props
            tex_checksum = 0
            if texture and hasattr(texture, 'image') and texture.image:
                if is_hex_string(texture.image.name):
                    tex_checksum = int(texture.image.name, 0)
                else:
                    tex_checksum = crc_from_string(bytes(texture.image.name, 'ascii'))

            w("I", tex_checksum)  # texture checksum
            pass_flags = 0 # MATFLAG_SMOOTH
            if tex_checksum and pprops.pf_textured:
                pass_flags |= MATFLAG_TEXTURED
            if pprops and pprops.has_uv_wibbles:
                pass_flags |= MATFLAG_UV_WIBBLE
            if (pprops and
                pprops.has_animated_texture and
                len(pprops.animated_texture.keyframes)):
                pass_flags |= MATFLAG_PASS_TEXTURE_ANIMATES
            if pprops and pprops.pf_transparent:
                pass_flags |= MATFLAG_TRANSPARENT
            if pprops and pprops.ignore_vertex_alpha:
                pass_flags |= MATFLAG_PASS_IGNORE_VERTEX_ALPHA
            if pprops and pprops.pf_decal:
                pass_flags |= MATFLAG_DECAL
            if pprops and pprops.pf_smooth:
                pass_flags |= MATFLAG_SMOOTH
            if pprops and pprops.pf_environment:
                pass_flags |= MATFLAG_ENVIRONMENT
            if pprops and pprops.pf_bump:
                print("EXPORTING BUMP MAP TEXTURE!")
                #pass_flags |= MATFLAG_BUMP_SIGNED_TEXTURE
                pass_flags |= MATFLAG_NORMAL_TEST
                #pass_flags |= MATFLAG_BUMP_LOAD_MATRIX
            if pprops and pprops.pf_water:
                print("EXPORTING WATER TEXTURE!")
                pass_flags |= MATFLAG_WATER_EFFECT
                
            w("I", pass_flags)  # flags # 4132
            w("?", True)  # has color flag; seems to be ignored
            w("3f",  *(pprops.color if pprops else m.diffuse_color / 2.0))  # color

            # alpha register values, first u32 - a BLEND_MODE, second u32 - fixed alpha (clipped to u8)
            # w("Q", 5)
            w("I", globals()[pprops.blend_mode] if pprops else vBLEND_MODE_DIFFUSE)
            w("I", pprops.blend_fixed_alpha if pprops else 0)

            w("I", 0 if (not pprops) or pprops.u_addressing == "Repeat" else 1)  # u adressing (wrap, clamp, etc)
            w("I", 0 if (not pprops) or pprops.v_addressing == "Repeat" else 1)  # v adressing
            w("2f", *(pprops.envmap_multiples if pprops else (3.0, 3.0)))  # envmap multiples
            w("I", 65540)  # filtering mode

            # uv wibbles
            if pprops and pass_flags & MATFLAG_UV_WIBBLE:
                w("2f", *pprops.uv_wibbles.uv_velocity)
                w("2f", *pprops.uv_wibbles.uv_frequency)
                w("2f", *pprops.uv_wibbles.uv_amplitude)
                w("2f", *pprops.uv_wibbles.uv_phase)

            # vertex color wibbles

            # anims
            if pass_flags & MATFLAG_PASS_TEXTURE_ANIMATES:
                at = pprops.animated_texture
                w("i", len(at.keyframes))
                w("i", at.period)
                w("i", at.iterations)
                w("i", at.phase)
                for keyframe in at.keyframes:
                    w("I", keyframe.time)
                    w("I", crc_from_string(bytes(keyframe.image, 'ascii')))

            w("I", 1)  # MMAG
            w("I", 4)  # MMIN
            w("f", -8.0)  # K
            w("f", -8.0)  # L



#----------------------------------------------------------------------------------
def _material_pass_settings_draw(self, context):
    if not context.object:
        return
    ob = context.object
    if not ob.active_material or not ob.active_material.active_texture:
        return
    attrs = [
        "color",
        "blend_mode",
        "blend_fixed_alpha",
        "u_addressing",
        "v_addressing"]
        #"filtering_mode",
        #"test_passes"]
    pass_props = ob.active_material.active_texture.thug_material_pass_props
    for attr in attrs:
        self.layout.prop(
            pass_props,
            attr)
            
    box = self.layout.box().column(True)
    box.row().prop(pass_props, "pf_textured")
    img = getattr(ob.active_material.active_texture, 'image', None)
    if img and pass_props.pf_textured:
        box.row().prop(img.thug_image_props, 'compression_type')
    box.row().prop(pass_props, "pf_bump")
    box.row().prop(pass_props, "pf_water")
    box.row().prop(pass_props, "pf_environment")
    if pass_props.pf_environment:
        box.row().prop(pass_props, "envmap_multiples")
    box.row().prop(pass_props, "pf_decal")
    box.row().prop(pass_props, "pf_smooth")
    box.row().prop(pass_props, "pf_transparent")
    box.row().prop(pass_props, "ignore_vertex_alpha")
    box.row().prop(pass_props, "has_uv_wibbles")
    box.row().prop(pass_props, 'has_animated_texture')
    
    if pass_props.has_uv_wibbles:
        box = self.layout.box().column(True)
        box.row().prop(pass_props.uv_wibbles, "uv_velocity")
        box.row().prop(pass_props.uv_wibbles, "uv_frequency")
        box.row().prop(pass_props.uv_wibbles, "uv_amplitude")
        box.row().prop(pass_props.uv_wibbles, "uv_phase")
    if pass_props.has_animated_texture:
        at = pass_props.animated_texture

        box = self.layout.box()
        col = box.column(True)
        col.prop(at, "period")
        col.prop(at, "iterations")
        col.prop(at, "phase")
        row = box.row(True)
        row.operator("object.thug_add_texture_keyframe", text="Add")
        row.operator("object.thug_remove_texture_keyframe", text="Remove")
        box.row().template_list("THUGAnimatedTextureKeyframesUIList", "", at, "keyframes", at, "keyframes_index", rows=1)
        # box.row().operator(at, "keyframes")

#----------------------------------------------------------------------------------
def _material_settings_draw(self, context):
    if not context.scene: return
    scn = context.scene
    if not context.object: return
    ob = context.object
    if not ob.active_material: return
    mps = ob.active_material.thug_material_props
    row = self.layout.row()
    row.prop(mps, "terrain_type")
    row = self.layout.row()
    row.prop(mps, "alpha_cutoff")
    row.prop(mps, "sorted")
    row = self.layout.row()
    row.prop(mps, "z_bias")
    row.prop(mps, "single_sided")
    row = self.layout.row()
    row.prop(mps, "draw_order")
    row.prop(mps, "no_backface_culling")
    row = self.layout.row()
    row.prop(mps, "specular_power")
    row.prop(mps, "no_skater_shadow")
    row = self.layout.row()
    row.prop(mps, "specular_color")
    self.layout.row().prop(mps, "grassify", toggle=True, icon="HAIR")
    if mps.grassify:
        row = self.layout.row()
        col = row.column(True)
        col.prop(mps, "grass_height")
        col.prop(mps, "grass_layers")

    
    if scn.thug_level_props.export_props.target_game != 'THUG1':
        return
        
    self.layout.row().prop(mps, "use_new_mats", toggle=True, icon="MATERIAL")
    if mps.use_new_mats:
        box = self.layout.box().column()
        row = box.row(True).column()
        row.prop(mps, "ugplus_shader")
        if mps.ugplus_shader != 'None':
            row.separator()
            row.prop(mps, "ugplus_trans")
            row.separator()
        
        if mps.ugplus_shader == 'PBR':
            ugplus_matslot_draw(mps.ugplus_matslot_diffuse, box, title='Diffuse')
            ugplus_matslot_draw(mps.ugplus_matslot_detail, box, title='Detail')
            ugplus_matslot_draw(mps.ugplus_matslot_normal, box, title='Normal', allow_uv_wibbles=False)
            ugplus_matslot_draw(mps.ugplus_matslot_specular, box, title='Specular', allow_uv_wibbles=False)
            ugplus_matslot_draw(mps.ugplus_matslot_reflection, box, title='Reflection', allow_uv_wibbles=False)
            ugplus_matslot_draw(mps.ugplus_matslot_lightmap, box, title='Lightmap', allow_uv_wibbles=False)
            #ugplus_matslot_draw(mps.ugplus_matslot_smoothness, box, title='Smoothness')
            ugplus_matslot_draw(mps.ugplus_matslot_weathermask, box, title='Rain/Snow Mask')
            ugplus_matslot_draw(mps.ugplus_matslot_snow, box, title='Snow', allow_uv_wibbles=False)
        if mps.ugplus_shader == 'PBR_Lightmapped':
            ugplus_matslot_draw(mps.ugplus_matslot_diffuse, box, title='Diffuse')
            ugplus_matslot_draw(mps.ugplus_matslot_detail, box, title='Detail')
            ugplus_matslot_draw(mps.ugplus_matslot_normal, box, title='Normal', allow_uv_wibbles=False)
            ugplus_matslot_draw(mps.ugplus_matslot_specular, box, title='Specular', allow_uv_wibbles=False)
            ugplus_matslot_draw(mps.ugplus_matslot_reflection, box, title='Reflection', allow_uv_wibbles=False)
            #ugplus_matslot_draw(mps.ugplus_matslot_smoothness, box, title='Smoothness')
            ugplus_matslot_draw(mps.ugplus_matslot_weathermask, box, title='Rain/Snow Mask')
            ugplus_matslot_draw(mps.ugplus_matslot_snow, box, title='Snow', allow_uv_wibbles=False)
            box.separator()
            box.label("Lightmaps")
            ugplus_matslot_draw(mps.ugplus_matslot_lightmap, box, title='Day')
            ugplus_matslot_draw(mps.ugplus_matslot_lightmap2, box, title='Evening')
            ugplus_matslot_draw(mps.ugplus_matslot_lightmap3, box, title='Night')
            ugplus_matslot_draw(mps.ugplus_matslot_lightmap4, box, title='Morning')
        elif mps.ugplus_shader == 'Water':
            ugplus_matslot_draw(mps.ugplus_matslot_fallback, box, title='Diffuse')
            ugplus_matslot_draw(mps.ugplus_matslot_reflection, box, title='Reflection')
            ugplus_matslot_draw(mps.ugplus_matslot_detail, box, title='Detail')
            box.separator()
            box.label("Lightmaps")
            ugplus_matslot_draw(mps.ugplus_matslot_lightmap, box, title='Day', allow_uv_wibbles=False)
            ugplus_matslot_draw(mps.ugplus_matslot_lightmap2, box, title='Evening', allow_uv_wibbles=False)
            ugplus_matslot_draw(mps.ugplus_matslot_lightmap3, box, title='Night', allow_uv_wibbles=False)
            ugplus_matslot_draw(mps.ugplus_matslot_lightmap4, box, title='Morning', allow_uv_wibbles=False)
        elif mps.ugplus_shader == 'Water_Custom':
            ugplus_matslot_draw(mps.ugplus_matslot_normal, box, title='Normal Map 1')
            ugplus_matslot_draw(mps.ugplus_matslot_normal2, box, title='Normal Map 2')
            ugplus_matslot_draw(mps.ugplus_matslot_fallback, box, title='Diffuse')
            ugplus_matslot_draw(mps.ugplus_matslot_reflection, box, title='Reflection')
            ugplus_matslot_draw(mps.ugplus_matslot_detail, box, title='Detail')
            box.separator()
            box.label("Lightmaps")
            ugplus_matslot_draw(mps.ugplus_matslot_lightmap, box, title='Day', allow_uv_wibbles=False)
            ugplus_matslot_draw(mps.ugplus_matslot_lightmap2, box, title='Evening', allow_uv_wibbles=False)
            ugplus_matslot_draw(mps.ugplus_matslot_lightmap3, box, title='Night', allow_uv_wibbles=False)
            ugplus_matslot_draw(mps.ugplus_matslot_lightmap4, box, title='Morning', allow_uv_wibbles=False)
        elif mps.ugplus_shader == 'Water_Displacement':
            ugplus_matslot_draw(mps.ugplus_matslot_normal, box, title='Normal Map 1')
            ugplus_matslot_draw(mps.ugplus_matslot_normal2, box, title='Normal Map 2')
            ugplus_matslot_draw(mps.ugplus_matslot_displacement, box, title='Displacement Map 1')
            ugplus_matslot_draw(mps.ugplus_matslot_displacement2, box, title='Displacement Map 2')
            ugplus_matslot_draw(mps.ugplus_matslot_fallback, box, title='Diffuse')
            ugplus_matslot_draw(mps.ugplus_matslot_reflection, box, title='Reflection')
            ugplus_matslot_draw(mps.ugplus_matslot_detail, box, title='Detail')
            box.separator()
            box.label("Lightmaps")
            ugplus_matslot_draw(mps.ugplus_matslot_lightmap, box, title='Day', allow_uv_wibbles=False)
            ugplus_matslot_draw(mps.ugplus_matslot_lightmap2, box, title='Evening', allow_uv_wibbles=False)
            ugplus_matslot_draw(mps.ugplus_matslot_lightmap3, box, title='Night', allow_uv_wibbles=False)
            ugplus_matslot_draw(mps.ugplus_matslot_lightmap4, box, title='Morning', allow_uv_wibbles=False)
        elif mps.ugplus_shader == 'Skybox':
            ugplus_matslot_draw(mps.ugplus_matslot_diffuse, box, title='Day')
            ugplus_matslot_draw(mps.ugplus_matslot_diffuse_evening, box, title='Evening')
            ugplus_matslot_draw(mps.ugplus_matslot_diffuse_night, box, title='Night')
            ugplus_matslot_draw(mps.ugplus_matslot_diffuse_morning, box, title='Morning')
        elif mps.ugplus_shader == 'Cloud':
            ugplus_matslot_draw(mps.ugplus_matslot_cloud, box, title='Cloud')
            

# PROPERTIES
#############################################
class THUGImageProps(bpy.types.PropertyGroup):
    compression_type = EnumProperty(items=(
        ("DXT1", "DXT1", "DXT1. 1-bit alpha. 1:8 compression for RGBA, 1:6 for RGB."),
        ("DXT5", "DXT5", "DXT5. Full alpha. 1:4 compression.")),
    name="Compression Type",
    default="DXT1")
#----------------------------------------------------------------------------------
class AddTextureKeyframe(bpy.types.Operator):
    bl_idname = "object.thug_add_texture_keyframe"
    bl_label = "Add THUG Texture Keyframe"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    @classmethod
    def poll(cls, context):
        if not context:
            return False
        ob = context.object
        if not ob:
            return False
        mat = ob.active_material
        if not mat:
            return False
        tex = mat.active_texture
        if not tex:
            return False
        mpp = tex.thug_material_pass_props
        if not mpp or not mpp.has_animated_texture:
            return False
        return True

    def execute(self, context):
        at = context.object.active_material.active_texture.thug_material_pass_props.animated_texture
        at.keyframes.add()
        at.keyframes_index = len(at.keyframes) - 1
        return {"FINISHED"}

#----------------------------------------------------------------------------------
class RemoveTextureKeyframe(bpy.types.Operator):
    bl_idname = "object.thug_remove_texture_keyframe"
    bl_label = "Remove THUG Texture Keyframe"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    @classmethod
    def poll(cls, context):
        if not context:
            return False
        ob = context.object
        if not ob:
            return False
        mat = ob.active_material
        if not mat:
            return False
        tex = mat.active_texture
        if not tex:
            return False
        mpp = tex.thug_material_pass_props
        if not mpp or not mpp.has_animated_texture:
            return False
        return True

    def execute(self, context):
        at = context.object.active_material.active_texture.thug_material_pass_props.animated_texture
        at.keyframes.remove(at.keyframes_index)
        at.keyframes_index = max(0, min(at.keyframes_index, len(at.keyframes) - 1))
        return {"FINISHED"}

#----------------------------------------------------------------------------------
class THUGAnimatedTextureKeyframesUIList(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        split = layout.split(0.33)
        split.prop(item, "time")
        split.prop_search(item, "image", bpy.data, "images", text="")
#----------------------------------------------------------------------------------
class THUGAnimatedTextureKeyframe(bpy.types.PropertyGroup):
    time = IntProperty(name="Time", min=0)
    image = StringProperty(name="Image")
#----------------------------------------------------------------------------------
class THUGAnimatedTexture(bpy.types.PropertyGroup):
    period = IntProperty(name="Period")
    iterations = IntProperty(name="Iterations")
    phase = IntProperty(name="Phase")

    keyframes = CollectionProperty(type=THUGAnimatedTextureKeyframe)
    keyframes_index = IntProperty()
#----------------------------------------------------------------------------------
class THUGUVWibbles(bpy.types.PropertyGroup):
    uv_velocity = FloatVectorProperty(name="Velocity", size=2, default=(1.0, 1.0), soft_min=-100, soft_max=100)
    uv_frequency = FloatVectorProperty(name="Frequency", size=2, default=(0.0, 0.0), soft_min=-100, soft_max=100)
    uv_amplitude = FloatVectorProperty(name="Amplitude", size=2, default=(0.0, 0.0), soft_min=-100, soft_max=100)
    uv_phase = FloatVectorProperty(name="Phase", size=2, default=(0.0, 0.0), soft_min=-100, soft_max=100)
#----------------------------------------------------------------------------------
class THUGMaterialSettingsTools(bpy.types.Panel):
    bl_label = "TH Material Settings"
    bl_region_type = "TOOLS"
    bl_space_type = "VIEW_3D"
    bl_category = "THUG Tools"

    @classmethod
    def poll(cls, context):
        return context.object and context.preferences.addons[ADDON_NAME].preferences.material_settings_tools

    def draw(self, context):
        if not context.object: return
        ob = context.object

        rows = 1
        is_sortable = len(ob.material_slots) > 1
        if is_sortable:
            rows = 4

        row = self.layout.row()
        row.template_list("MATERIAL_UL_matslots", "", ob, "material_slots", ob, "active_material_index", rows=rows)
        col = row.column(align=True)
        col.operator("object.material_slot_add", icon='ZOOMIN', text="")
        col.operator("object.material_slot_remove", icon='ZOOMOUT', text="")
        col.menu("MATERIAL_MT_specials", icon='DOWNARROW_HLT', text="")
        if is_sortable:
            col.separator()
            col.operator("object.material_slot_move", icon='TRIA_UP', text="").direction = 'UP'
            col.operator("object.material_slot_move", icon='TRIA_DOWN', text="").direction = 'DOWN'

        self.layout.template_ID(ob, "active_material", new="material.new")

        if ob.mode == 'EDIT':
            row = self.layout.row(align=True)
            row.operator("object.material_slot_assign", text="Assign")
            row.operator("object.material_slot_select", text="Select")
            row.operator("object.material_slot_deselect", text="Deselect")

        # self.layout.template_preview(context.object.active_material)
        _material_settings_draw(self, context)

#----------------------------------------------------------------------------------
class THUGMaterialSettings(bpy.types.Panel):
    bl_label = "TH Material Settings"
    bl_region_type = "WINDOW"
    bl_space_type = "PROPERTIES"
    bl_context = "material"

    def draw(self, context):
        _material_settings_draw(self, context)
#----------------------------------------------------------------------------------
class THUGMaterialPassSettingsTools(bpy.types.Panel):
    bl_label = "TH Material Pass Tools"
    bl_region_type = "TOOLS"
    bl_space_type = "VIEW_3D"
    bl_category = "THUG Tools"

    @classmethod
    def poll(self, context):
        return context.object and context.preferences.addons[ADDON_NAME].preferences.material_pass_settings_tools

    def draw(self, context):
        from bl_ui.properties_material import active_node_mat
        mat = context.object.active_material
        if not mat:
            self.layout.label(text="You need a material to configure it's passes.")
            return
        idblock = active_node_mat(mat)
        self.layout.template_list("TEXTURE_UL_texslots", "", idblock, "texture_slots", idblock, "active_texture_index", rows=2)
        self.layout.template_ID(idblock, "active_texture", new="texture.new")
        _material_pass_settings_draw(self, context)

#----------------------------------------------------------------------------------
class THUGMaterialPassSettings(bpy.types.Panel):
    bl_label = "TH Material Pass Settings"
    bl_region_type = "WINDOW"
    bl_space_type = "PROPERTIES"
    bl_context = "texture"

    def draw(self, context):
        _material_pass_settings_draw(self, context)

def set_ugplus_materialslot(self, context):
    if self.tex_image:
        self.tex_image_name = self.tex_image.name
    else:
        self.tex_image_name = ''
#----------------------------------------------------------------------------------
class UGPlusMaterialSlotProps(bpy.types.PropertyGroup):
    #tex_image = StringProperty(name="Texture", description="Texture to be used.")
    tex_image = PointerProperty(name="Texture", type=bpy.types.Image, update=set_ugplus_materialslot)
    tex_image_name = StringProperty(name="ImageName")
    tex_color = FloatVectorProperty(name="Color",
                           subtype='COLOR',
                           default=(1.0, 1.0, 1.0, 1.0),
                           size=4,
                           min=0.0, max=1.0,
                           description="Color used if no texture provided.")
                           
    has_uv_wibbles = BoolProperty(name="Animate UVs", default=False, description='Animate UVs for this slot.')
    uv_wibbles = PointerProperty(type=THUGUVWibbles)
    
def ugplus_matslot_draw(self, layout, title, allow_uv_wibbles=True, mat_icon='FILE_IMAGE'):
    c = layout.column()
    row = c.row()
    split = row.split(percentage=0.3)
    c = split.column()
    c.label(title)
    split = split.split(percentage=0.7)
    c = split.column()
    c.scale_x = 0.8
    c.template_ID(self, "tex_image", open="image.open")
    c = split.column()
    c.scale_x = 0.1
    c.prop(self, 'tex_color', text='')
    if allow_uv_wibbles:
        col = split.column(align=True)
        col.scale_x = 0.1
        col.prop(self, 'has_uv_wibbles', toggle=True, icon='PLAY', text='')
        
        if (self.has_uv_wibbles):
            row = layout.row(True)
            col = row.column(align=True)
            col.scale_x = 0.5
            row = col.row()
            row.prop(self.uv_wibbles, "uv_velocity")
            row = col.row()
            row.prop(self.uv_wibbles, "uv_frequency")
            row = col.row()
            row.prop(self.uv_wibbles, "uv_amplitude")
            row = col.row()
            row.prop(self.uv_wibbles, "uv_phase")
    else:
        col = split.column(align=True)
        col.scale_x = 0.1
        col.label("")
        

#----------------------------------------------------------------------------------
class THUGMaterialProps(bpy.types.PropertyGroup):
    alpha_cutoff = IntProperty(
        name="Alpha Cutoff", min=0, max=255, default=1,
        description="The pixels will alpha lower than this will be discarded.")
    sorted = BoolProperty(name="Sorted", default=False)
    draw_order = FloatProperty(
        name="Draw Order",
        default=-1.0,
        description="The lesser the draw order the earlier the texture will be drawn. Used for sorting transparent textures.")
    single_sided = BoolProperty(name="Single Sided", default=False,
        description="If the material is not using the Diffuse blend mode this can be toggled to force it to be single sided.")
    no_backface_culling = BoolProperty(name="No Backface Culling", default=False,
        description="Makes material with Diffuse blend mode double sided")
    no_skater_shadow = BoolProperty(name="No Skater Shadow", default=False,
        description="Any mesh using this material will not render dynamic shadows.")
    z_bias = IntProperty(name="Z-Bias", default=0,
        description="Adjust this value to prevent Z-fighting on overlapping meshes.")
    specular_power = FloatProperty(name="Specular Power", default=0.0)
    specular_color = FloatVectorProperty(name="Specular Color", subtype="COLOR", min=0, max=1)
    grassify = BoolProperty(name="Grass Effect", description="Use generated grass particles on this material.")
    grass_height = FloatProperty(name="Grass Height", min=0.0, max=1000.0, description="Height of the grass particles.")
    grass_layers = IntProperty(name="Grass Layers", min=0, max=5, description="Number of layers.")
    terrain_type = EnumProperty(
        name="Terrain Type",
        description="The terrain type that will be used for faces using this material when their terrain type is set to \"Auto\".",
        items=[(tt, tt, tt) for tt in TERRAIN_TYPES])

    ###############################################################
    # NEW MATERIAL SYSTEM PROPERTIES
    ###############################################################
    use_new_mats = BoolProperty(name="Use New Material System", description="(Underground+ 1.5+ only) Use the new modern material/shader system.")
    ugplus_shader = EnumProperty(
        name="Shader",
        description="The shader to use for this material. Changes the available texture fields.",
        items=[
        ("None", "None", ""),
        ("PBR", "Fake PBR", "General material shader. Supports diffuse/point lights, normal mapping, specular highlights/reflections, and weather masks."),
        ("PBR_Lightmapped", "Lightmapped PBR", "Lightmapped material shader with up to 4 TOD-specific lightmaps. Primarily used for static scene mesh."),
        ("Skybox", "Skybox", "Material shader supporting 4 separate textures based on TOD."),
        ("Cloud", "Cloud", "Material with an appearance that fades/changes based on in-game weather settings (cloudiness)."),
        ("Water", "Water", "Built-in water effect, creates a water surface using an animated texture."),
        ("Water_Custom", "Water (Custom)", "Custom water effect using two normal maps and UV wibbles."),
        ("Water_Displacement", "Water (Displacement)", "More expensive custom water effect using two normal maps, two displacement maps, and UV wibbles."),
        ])
    ugplus_trans = BoolProperty(name="Transparency", description="Enable transparency on this material.")
    
    ugplus_matslot_diffuse = PointerProperty(type=UGPlusMaterialSlotProps, name="Diffuse", description="Base texture.")
    ugplus_matslot_detail = PointerProperty(type=UGPlusMaterialSlotProps, name="Detail", description="Detail texture which is multiplied onto the diffuse pass.")
    ugplus_matslot_normal = PointerProperty(type=UGPlusMaterialSlotProps, name="Normal", description="Normal map.")
    ugplus_matslot_normal2 = PointerProperty(type=UGPlusMaterialSlotProps, name="Normal #2", description="Normal map.")
    ugplus_matslot_displacement = PointerProperty(type=UGPlusMaterialSlotProps, name="Displacement", description="Displacement map.")
    ugplus_matslot_displacement2 = PointerProperty(type=UGPlusMaterialSlotProps, name="Displacement 2", description="Displacement map #2.")
    ugplus_matslot_specular = PointerProperty(type=UGPlusMaterialSlotProps, name="Specular", description="Intensity of specular reflections.")
    # Eventually, roughness will be a separate texture that is mixed into the alpha channel for the normal texture
    #ugplus_matslot_smoothness = PointerProperty(type=UGPlusMaterialSlotProps, name="Smoothness", description="Sharpness of specular reflections.")
    ugplus_matslot_reflection = PointerProperty(type=UGPlusMaterialSlotProps, name="Reflection", description="Texture used for specular reflections.")
    ugplus_matslot_lightmap = PointerProperty(type=UGPlusMaterialSlotProps, name="Lightmap", description="Lightmap texture.")
    ugplus_matslot_lightmap2 = PointerProperty(type=UGPlusMaterialSlotProps, name="Lightmap", description="Lightmap texture.")
    ugplus_matslot_lightmap3 = PointerProperty(type=UGPlusMaterialSlotProps, name="Lightmap", description="Lightmap texture.")
    ugplus_matslot_lightmap4 = PointerProperty(type=UGPlusMaterialSlotProps, name="Lightmap", description="Lightmap texture.")
    ugplus_matslot_weathermask = PointerProperty(type=UGPlusMaterialSlotProps, name="Rain Mask", description="Mask used for rain/snow effects.")
    ugplus_matslot_snow = PointerProperty(type=UGPlusMaterialSlotProps, name="Snow", description="Snow texture.")
        
    ugplus_matslot_fallback = PointerProperty(type=UGPlusMaterialSlotProps, name="Fallback", description="Texture used on lower graphics settings/lower shader detail settings.")
    ugplus_matslot_diffuse_night = PointerProperty(type=UGPlusMaterialSlotProps, name="Night", description="Texture used when the TOD is night.")
    ugplus_matslot_diffuse_evening = PointerProperty(type=UGPlusMaterialSlotProps, name="Evening", description="Texture used when the TOD is evening.")
    ugplus_matslot_diffuse_morning = PointerProperty(type=UGPlusMaterialSlotProps, name="Evening", description="Texture used when the TOD is morning.")
    ugplus_matslot_cloud = PointerProperty(type=UGPlusMaterialSlotProps, name="Cloud", description="Texture used when weather effects (rain, snow) are active.")
    ###############################################################
    
#----------------------------------------------------------------------------------
class THUGMaterialPassProps(bpy.types.PropertyGroup):
    color = FloatVectorProperty(
        name="Color", subtype="COLOR",
        default=(0.5, 0.5, 0.5),
        min=0.0, max=1.0,
        update=_thug_material_pass_props_color_updated)
    blend_mode = EnumProperty(items=(
     ("vBLEND_MODE_DIFFUSE", "DIFFUSE", "( 0 - 0 ) * 0 + Src"),
     # ( 0 - 0 ) * 0 + Src
     ("vBLEND_MODE_ADD", "ADD", "( Src - 0 ) * Src + Dst"),
     # ( Src - 0 ) * Src + Dst
     ("vBLEND_MODE_ADD_FIXED", "ADD_FIXED", "( Src - 0 ) * Fixed + Dst"),
     # ( Src - 0 ) * Fixed + Dst
     ("vBLEND_MODE_SUBTRACT", "SUBTRACT", "( 0 - Src ) * Src + Dst"),
     # ( 0 - Src ) * Src + Dst
     ("vBLEND_MODE_SUB_FIXED", "SUB_FIXED", "( 0 - Src ) * Fixed + Dst"),
     # ( 0 - Src ) * Fixed + Dst
     ("vBLEND_MODE_BLEND", "BLEND", "( Src * Dst ) * Src + Dst"),
     # ( Src * Dst ) * Src + Dst
     ("vBLEND_MODE_BLEND_FIXED", "BLEND_FIXED", "( Src * Dst ) * Fixed + Dst"),
     # ( Src * Dst ) * Fixed + Dst
     ("vBLEND_MODE_MODULATE", "MODULATE", "( Dst - 0 ) * Src + 0"),
     # ( Dst - 0 ) * Src + 0
     ("vBLEND_MODE_MODULATE_FIXED", "MODULATE_FIXED", "( Dst - 0 ) * Fixed + 0"),
     # ( Dst - 0 ) * Fixed + 0
     ("vBLEND_MODE_BRIGHTEN", "BRIGHTEN", "( Dst - 0 ) * Src + Dst"),
     # ( Dst - 0 ) * Src + Dst
     ("vBLEND_MODE_BRIGHTEN_FIXED", "BRIGHTEN_FIXED", "( Dst - 0 ) * Fixed + Dst"),
     # ( Dst - 0 ) * Fixed + Dst
     ("vBLEND_MODE_GLOSS_MAP", "GLOSS_MAP", ""),                             # Specular = Specular * Src    - special mode for gloss mapping
     ("vBLEND_MODE_BLEND_PREVIOUS_MASK", "BLEND_PREVIOUS_MASK", ""),                   # ( Src - Dst ) * Dst + Dst
     ("vBLEND_MODE_BLEND_INVERSE_PREVIOUS_MASK", "BLEND_INVERSE_PREVIOUS_MASK", ""),           # ( Dst - Src ) * Dst + Src
     ("vBLEND_MODE_MODULATE_COLOR", "MODULATE_COLOR", ""),
     ("vBLEND_MODE_ONE_INV_SRC_ALPHA", "ONE_INV_SRC_ALPHA", ""),
    ), name="Blend Mode", default="vBLEND_MODE_DIFFUSE")
    blend_fixed_alpha = IntProperty(name="Fixed Alpha", min=0, max=255)
    u_addressing = EnumProperty(items=(
        ("Repeat", "Repeat", ""),
        ("Clamp", "Clamp", ""),
    ), name="U Addressing", default="Repeat")
    v_addressing = EnumProperty(items=(
        ("Repeat", "Repeat", ""),
        ("Clamp", "Clamp", ""),
    ), name="V Addressing", default="Repeat")
    
    pf_textured = BoolProperty(name="Textured", default=True)
    pf_environment = BoolProperty(name="Environment texture", default=False) 
    pf_bump = BoolProperty(name="Bump texture", default=False) 
    pf_water = BoolProperty(name="Water texture", default=False) 
    pf_decal = BoolProperty(name="Decal", default=False) 
    pf_smooth = BoolProperty(name="Smooth", default=True) 
    pf_transparent = BoolProperty(name="Use Transparency", default=True)
    ignore_vertex_alpha = BoolProperty(name="Ignore Vertex Alpha", default=False)
    envmap_multiples = FloatVectorProperty(name="Envmap Multiples", size=2, default=(3.0, 3.0), min=0.1, max=10.0)
    
    filtering_mode = IntProperty(name="Filtering Mode", min=0, max=100000)
    test_passes = IntProperty(name="Material passes (test)", min=0, max=100000)
    # filtering mode?

    has_uv_wibbles = BoolProperty(name="Has UV Wibbles", default=False)
    uv_wibbles = PointerProperty(type=THUGUVWibbles)

    has_animated_texture = BoolProperty(name="Has Animated Texture", default=False)
    animated_texture = PointerProperty(type=THUGAnimatedTexture)
    
