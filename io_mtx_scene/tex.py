#############################################
# THPS TEX (.tex) IMPORT/EXPORT
#############################################
import bpy
import os
import struct
from bpy.props import *

from . constants import *
from . helpers import *

# METHODS
#############################################
def _write_dds_file(temp_path, width, height, payload, compression="DXT1"):
    """Write a fully compliant standard DDS file for Blender's native image loader."""
    if not temp_path:
        return None

    try:
        compression = str(compression).upper()
        block_size = 8 if compression == "DXT1" else 16
        if compression in {"DXT1", "DXT5"}:
            pitch_or_linear_size = max(1, ((width + 3) // 4) * ((height + 3) // 4) * block_size)
        else:
            # For uncompressed RGBA the DDS header field is row pitch,
            # not total image byte count.
            pitch_or_linear_size = width * 4

        if payload is None:
            payload = b"\x00" * pitch_or_linear_size

        dds_header = bytearray(128)
        dds_header[0:4] = b"DDS "
        struct.pack_into("<I", dds_header, 4, 124)

        # DDS_HEADER.dwFlags:
        # CAPS | HEIGHT | WIDTH | PIXELFORMAT
        # plus LINEARSIZE for block-compressed textures.
        header_flags = 0x00001007
        if compression in {"DXT1", "DXT5"}:
            header_flags |= 0x00080000  # DDSD_LINEARSIZE
        else:
            header_flags |= 0x00000008  # DDSD_PITCH
        struct.pack_into("<I", dds_header, 8, header_flags)
        struct.pack_into("<I", dds_header, 12, int(height))
        struct.pack_into("<I", dds_header, 16, int(width))
        struct.pack_into("<I", dds_header, 20, pitch_or_linear_size)
        struct.pack_into("<I", dds_header, 28, 1)
        struct.pack_into("<I", dds_header, 76, 32)
        
        if compression in {"DXT1", "DXT5"}:
            struct.pack_into("<I", dds_header, 80, 0x00000004)
            dds_header[84:88] = b"DXT1" if compression == "DXT1" else b"DXT5"
        else:
            struct.pack_into("<I", dds_header, 80, 0x00000041)
            struct.pack_into("<I", dds_header, 88, 32)
            struct.pack_into("<I", dds_header, 92, 0x00FF0000)
            struct.pack_into("<I", dds_header, 96, 0x0000FF00)
            struct.pack_into("<I", dds_header, 100, 0x000000FF)
            struct.pack_into("<I", dds_header, 104, 0xFF000000)

        struct.pack_into("<I", dds_header, 108, 0x00001000)

        os.makedirs(os.path.dirname(temp_path), exist_ok=True)
        with open(temp_path, "wb") as out:
            out.write(dds_header)
            out.write(payload if isinstance(payload, (bytes, bytearray)) else bytes(payload))
        return temp_path
    except Exception:
        import traceback
        traceback.print_exc()
        return None


def _decode_565(value):
    r = ((value >> 11) & 0x1F) * 255 / 31.0
    g = ((value >> 5) & 0x3F) * 255 / 63.0
    b = (value & 0x1F) * 255 / 31.0
    return r, g, b


def _decode_dxt1(width, height, payload):
    """Decode a standard DXT1 block stream into bottom-left-origin RGBA floats.

    THPS uses GL DXT1/5 compressed textures. The old addon uploaded these exact
    blocks with glCompressedTexImage2D(), then read them back with glGetTexImage().
    This CPU decoder reproduces that operation without Blender's removed bgl API.
    """
    import struct
    import numpy as np

    width = int(width)
    height = int(height)
    out = np.zeros((height, width, 4), dtype=np.float32)
    blocks_w = (width + 3) // 4
    blocks_h = (height + 3) // 4
    expected = blocks_w * blocks_h * 8
    if len(payload) < expected:
        raise ValueError(f"DXT1 payload too small: got {len(payload)}, expected at least {expected}")

    pos = 0
    for by in range(blocks_h):
        for bx in range(blocks_w):
            c0, c1, bits = struct.unpack_from('<HHI', payload, pos)
            pos += 8
            p0 = _decode_565(c0)
            p1 = _decode_565(c1)
            if c0 > c1:
                colors = (
                    (*p0, 1.0),
                    (*p1, 1.0),
                    tuple((2.0 * p0[i] + p1[i]) / 3.0 for i in range(3)) + (1.0,),
                    tuple((p0[i] + 2.0 * p1[i]) / 3.0 for i in range(3)) + (1.0,),
                )
            else:
                colors = (
                    (*p0, 1.0),
                    (*p1, 1.0),
                    tuple((p0[i] + p1[i]) / 2.0 for i in range(3)) + (1.0,),
                    (0.0, 0.0, 0.0, 0.0),
                )
            for py in range(4):
                y = by * 4 + py
                if y >= height:
                    continue
                row = (bits >> (py * 8)) & 0xFF
                for px in range(4):
                    x = bx * 4 + px
                    if x >= width:
                        continue
                    idx = (row >> (px * 2)) & 0x3
                    r, g, b, a = colors[idx]
                    out[y, x] = (r / 255.0, g / 255.0, b / 255.0, a)

    # Match the original addon: glGetTexImage() output was assigned directly
    # to Blender Image.pixels, with no additional vertical flip.
    return out.reshape(-1, 4)


def _decode_dxt5(width, height, payload):
    """Decode a standard DXT5 block stream into bottom-left-origin RGBA floats."""
    import struct
    import numpy as np

    width = int(width)
    height = int(height)
    out = np.zeros((height, width, 4), dtype=np.float32)
    blocks_w = (width + 3) // 4
    blocks_h = (height + 3) // 4
    expected = blocks_w * blocks_h * 16
    if len(payload) < expected:
        raise ValueError(f"DXT5 payload too small: got {len(payload)}, expected at least {expected}")

    pos = 0
    for by in range(blocks_h):
        for bx in range(blocks_w):
            a0 = payload[pos]
            a1 = payload[pos + 1]
            alpha_bits = int.from_bytes(payload[pos + 2:pos + 8], 'little')
            c0, c1, color_bits = struct.unpack_from('<HHI', payload, pos + 8)
            pos += 16

            if a0 > a1:
                alphas = [
                    a0, a1,
                    (6 * a0 + 1 * a1) / 7.0,
                    (5 * a0 + 2 * a1) / 7.0,
                    (4 * a0 + 3 * a1) / 7.0,
                    (3 * a0 + 4 * a1) / 7.0,
                    (2 * a0 + 5 * a1) / 7.0,
                    (1 * a0 + 6 * a1) / 7.0,
                ]
            else:
                alphas = [
                    a0, a1,
                    (4 * a0 + 1 * a1) / 5.0,
                    (3 * a0 + 2 * a1) / 5.0,
                    (2 * a0 + 3 * a1) / 5.0,
                    (1 * a0 + 4 * a1) / 5.0,
                    0.0,
                    255.0,
                ]

            p0 = _decode_565(c0)
            p1 = _decode_565(c1)
            colors = (
                (*p0, 1.0),
                (*p1, 1.0),
                tuple((2.0 * p0[i] + p1[i]) / 3.0 for i in range(3)) + (1.0,),
                tuple((p0[i] + 2.0 * p1[i]) / 3.0 for i in range(3)) + (1.0,),
            )

            for py in range(4):
                y = by * 4 + py
                if y >= height:
                    continue
                arow = (alpha_bits >> (py * 12)) & 0xFFF
                crow = (color_bits >> (py * 8)) & 0xFF
                for px in range(4):
                    x = bx * 4 + px
                    if x >= width:
                        continue
                    aidx = (arow >> (px * 3)) & 0x7
                    cidx = (crow >> (px * 2)) & 0x3
                    r, g, b, _ = colors[cidx]
                    out[y, x] = (r / 255.0, g / 255.0, b / 255.0, alphas[aidx] / 255.0)

    # Match the original addon: glGetTexImage() output was assigned directly
    # to Blender Image.pixels, with no additional vertical flip.
    return out.reshape(-1, 4)


def _make_decoded_image(tex_name, width, height, payload, compression):
    """Create a Blender image from the raw THPS DXT payload."""
    import numpy as np

    compression = str(compression).upper()
    if compression == "DXT1":
        pixels = _decode_dxt1(width, height, payload)
    elif compression == "DXT5":
        pixels = _decode_dxt5(width, height, payload)
    elif compression == "RGBA8":
        # Uncompressed 8-bit RGBA payload. Handle possible per-row stride/padding.
        w = int(width)
        h = int(height)
        arr = np.frombuffer(payload, dtype=np.uint8)
        expected = w * h * 4
        if h == 0 or w == 0:
            raise ValueError("Invalid image dimensions")

        if arr.size == expected:
            img_arr = arr.reshape((h, w, 4))
        else:
            # Payload may include row stride/padding. Compute integer row stride.
            row_stride = arr.size // h
            if row_stride < w * 4:
                raise ValueError(f"RGBA8 payload too small: got {arr.size}, expected >= {expected}")
            rows = []
            for r in range(h):
                start = r * row_stride
                row_bytes = arr[start:start + (w * 4)]
                if row_bytes.size < w * 4:
                    # pad row if necessary
                    row_bytes = np.pad(row_bytes, (0, w * 4 - row_bytes.size), constant_values=0)
                rows.append(row_bytes.reshape((w, 4)))
            img_arr = np.stack(rows, axis=0)

        # Maintain bottom-left origin (match DXT decoders). If textures look vertically
        # flipped, change to img_arr = img_arr[::-1, :, :]
        pixels = (img_arr.astype(np.float32) / 255.0).reshape(-1, 4)
    else:
        raise ValueError(f"Unsupported compressed texture format: {compression}")

    img = bpy.data.images.new(str(tex_name), int(width), int(height), alpha=True, float_buffer=False)
    # foreach_set expects a flat sequence
    img.pixels.foreach_set(pixels.reshape(-1))
    img.update()
    img["thug_texture_checksum"] = f"0x{(int(str(tex_name), 0) & 0xFFFFFFFF):08X}"
    try:
        img.thug_image_props.compression_type = compression
    except Exception:
        pass
    try:
        img.pack()
    except Exception:
        pass
    return img


def _load_dds_image_from_temp(tex_name, width, height, payload, compression="DXT1"):
    """Compatibility wrapper retained for callers; decode DXT directly on CPU."""
    return _make_decoded_image(tex_name, width, height, payload, compression)

def import_tex(filename, directory, operator=None):
    filepath = os.path.join(directory, filename) if directory else filename
    if not os.path.exists(filepath):
        if operator is not None and hasattr(operator, "report"):
            operator.report({'WARNING'}, f"Texture file not found: {filepath}")
        return None

    print(f"Reading .TEX file: {filepath}")
    try:
        with open(filepath, "rb") as inp:
            r = Reader(inp.read())
            return read_tex(r, Printer())
    except Exception as exc:
        import traceback
        traceback.print_exc()
        if operator is not None and hasattr(operator, "report"):
            operator.report({'ERROR'}, f"Failed to import texture {filename}: {exc}")
        return None


def get_all_compressed_mipmaps(image, compression_type, mm_offset):
    return []


def get_all_mipmaps(image, mm_offset = 0):
    pixels = list(image.pixels)
    width, height = image.size
    if not pixels:
        return []
    rgba = []
    for i in range(0, len(pixels), 4):
        rgba.append((width, height, bytes((int(pixels[i + 0] * 255.0), int(pixels[i + 1] * 255.0), int(pixels[i + 2] * 255.0), int(pixels[i + 3] * 255.0)))))
    return [(width, height, bytes([v for rgba_tuple in rgba for v in rgba_tuple[2]]))]


def read_tex(reader, printer):
    global name_format
    r = reader
    p = printer

    p("tex file version: {}", r.i32())
    num_textures = p("num textures: {}", r.i32())

    already_seen = set()
    loaded_images = {}

    for i in range(num_textures):
        p("texture #{}", i)
        checksum = p("  checksum: {}", hex(r.u32()))

        if checksum in already_seen:
            p("Duplicate checksum!", None)
        else:
            already_seen.add(checksum)

        img_width = p("  width: {}", r.u32())
        img_height = p("  height: {}", r.u32())
        levels = p("  levels: {}", r.u32())
        texel_depth = p("  texel depth: {}", r.u32())
        pal_depth = p("  palette depth: {}", r.u32())
        dxt_version = p("  dxt version: {}", r.u32())
        pal_size = p("  palette depth: {}", r.u32())

        if dxt_version == 2:
            dxt_version = 1

        if pal_size > 0:
            if pal_depth == 32:
                pal_colors = []
                for j in range(pal_size//4):
                    cb, cg, cr, ca = r.read("4B")
                    # TEX palettes are stored BGRA; Blender expects RGBA.
                    pal_colors.append((cr/255.0, cg/255.0, cb/255.0, ca/255.0))
            else:
                r.read(str(pal_size) + "B")

        for j in range(levels):
            data_size = r.u32()
            payload_bytes = r.buf[r.offset:r.offset+data_size]
            
            if j == 0:
                img_name_str = str(checksum)
                if dxt_version in (1, 5):
                    comp_str = "DXT5" if dxt_version == 5 else "DXT1"
                    blend_img = _load_dds_image_from_temp(img_name_str, img_width, img_height, payload_bytes, compression=comp_str)
                    if blend_img:
                        try:
                            blend_img.thug_image_props.compression_type = comp_str
                        except Exception:
                            pass
                        loaded_images[checksum] = blend_img
                elif dxt_version == 0:
                    if pal_size > 0 and pal_depth == 32 and texel_depth == 8:
                        swizzled_data = swizzle(payload_bytes, img_width, img_height, 8, 0, True)
                        blend_img = bpy.data.images.new(img_name_str, img_width, img_height, alpha=True)
                        try:
                            blend_img["thug_texture_checksum"] = f"0x{(int(checksum) & 0xFFFFFFFF):08X}"
                        except Exception:
                            pass
                        pixels = []
                        for pal_idx in swizzled_data:
                            if 0 <= pal_idx < len(pal_colors):
                                pixels.extend(pal_colors[pal_idx])
                        blend_img.pixels = pixels
                        loaded_images[checksum] = blend_img
                    else:
                        blend_img = _load_dds_image_from_temp(
                            img_name_str, img_width, img_height, payload_bytes,
                            compression="RGBA8",
                        )
                        if blend_img:
                            loaded_images[checksum] = blend_img
    
            r.offset += data_size

    return loaded_images


class THUG2TexToImages(bpy.types.Operator):
    bl_idname = "io.thug2_tex"
    bl_label = "THPS Xbox/PC .tex"

    filename = StringProperty(name="File Name")
    directory = StringProperty(name="Directory")

    def execute(self, context):
        filename = self.filename
        directory = self.directory
        filepath = str(getattr(self, "filepath", ""))
        filename_str = str(filename) if filename is not None else ""
        directory_str = str(directory) if directory is not None else ""
        if type(filename).__name__ == "_PropertyDeferred":
            filename_str = os.path.basename(filepath)
        if type(directory).__name__ == "_PropertyDeferred":
            directory_str = os.path.dirname(filepath)

        p = Printer()
        p("Reading .TEX file: {}", os.path.join(directory_str, filename_str))
        with open(os.path.join(directory_str, filename_str), "rb") as inp:
            r = Reader(inp.read())
            read_tex(r, p)

        return {'FINISHED'}

    def invoke(self, context, event):
        wm = bpy.context.window_manager
        wm.fileselect_add(self)
        return {'RUNNING_MODAL'}