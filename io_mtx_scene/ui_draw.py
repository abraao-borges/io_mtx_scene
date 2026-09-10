import bpy
from bpy.props import *

try:
    import gpu
    from gpu_extras.batch import batch_for_shader
except ImportError:  # pragma: no cover
    gpu = None
    batch_for_shader = None

from . import_thps4 import THPS4ScnToScene
from . import_thug1 import THUG1ScnToScene
from . import_thug2 import THUG2ScnToScene, THUG2ColToScene
from . import_park import ImportTHUGPrk
from . import_thps2 import THPS2PsxToScene
from . qb import THUGImportLevelQB
from . skeleton import THUGImportSkeleton
from . constants import *
from . material import *
from . collision import *
from . export_thug1 import *
from . export_thug2 import *
from . export_shared import *
from . presets import *
from . import script_template

IMPORT_OPERATOR_CLASSES = (
    THPS4ScnToScene,
    THUG1ScnToScene,
    THUG2ScnToScene,
    THUG2ColToScene,
    ImportTHUGPrk,
    THPS2PsxToScene,
    THUGImportLevelQB,
    THUGImportSkeleton,
)

# PROPERTIES
#############################################
draw_stuff_display_list_id = None
draw_stuff_dirty = True
draw_stuff_objects = set()
draw_handle = None

# METHODS
#############################################
@bpy.app.handlers.persistent
def draw_stuff_post_update(scene, depsgraph=None):
    global draw_stuff_dirty, draw_stuff_objects
    if draw_stuff_dirty:
        return
    if not draw_stuff_objects:
        draw_stuff_dirty = True
        return

    scn_objs = {ob.name: ob for ob in scene.objects}
    for ob_name in draw_stuff_objects:
        ob = scn_objs.get(ob_name)
        if not ob:
            draw_stuff_dirty = True
            return
        if depsgraph is not None and depsgraph.id_type_updated('OBJECT'):
            draw_stuff_dirty = True
            return

@bpy.app.handlers.persistent
def draw_stuff_pre_load_cleanup(*args):
    global draw_stuff_dirty, draw_stuff_objects
    draw_stuff_dirty = True
    draw_stuff_objects = set()

@bpy.app.handlers.persistent
def draw_stuff():
    global draw_stuff_dirty, draw_stuff_objects
    ctx = bpy.context
    if not len(ctx.selected_objects) and not ctx.object:
        return
    if not getattr(bpy.context.window_manager, "thug_show_face_collision_colors", False):
        return
    if gpu is None or batch_for_shader is None:
        return

    objects = set([ob.name for ob in ctx.selected_objects] if ctx.mode == "OBJECT" else [ctx.object.name])
    if draw_stuff_objects != objects:
        draw_stuff_dirty = True
    if draw_stuff_dirty:
        draw_stuff_objects = objects
        draw_stuff_dirty = False

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    verts = []
    for ob_name in draw_stuff_objects:
        ob = bpy.data.objects.get(ob_name)
        if ob is None or ob.type != "MESH":
            continue
        matrix = ob.matrix_world
        for v in ob.data.vertices[:8]:
            world_v = matrix @ v.co
            verts.extend([(world_v.x, world_v.y, world_v.z), (world_v.x, world_v.y, world_v.z)])
    if not verts:
        return

    batch = batch_for_shader(shader, 'LINES', {"pos": verts})
    shader.bind()
    shader.uniform_float("color", (1.0, 0.0, 0.0, 0.7))
    batch.draw(shader)

#----------------------------------------------------------------------------------
def import_menu_func(self, context):
    self.layout.operator(THUG2ColToScene.bl_idname, text=THUG2ColToScene.bl_label, icon='PLUGIN')
    self.layout.operator(THUG2ScnToScene.bl_idname, text=THUG2ScnToScene.bl_label, icon='PLUGIN')
    self.layout.operator(THUG1ScnToScene.bl_idname, text=THUG1ScnToScene.bl_label, icon='PLUGIN')
    self.layout.operator(THPS4ScnToScene.bl_idname, text=THPS4ScnToScene.bl_label, icon='PLUGIN')
    self.layout.operator(THPS2PsxToScene.bl_idname, text=THPS2PsxToScene.bl_label, icon='PLUGIN')
    self.layout.operator(THUGImportLevelQB.bl_idname, text=THUGImportLevelQB.bl_label, icon='PLUGIN')
    self.layout.operator(THUGImportSkeleton.bl_idname, text=THUGImportSkeleton.bl_label, icon='PLUGIN')
    self.layout.operator(ImportTHUGPrk.bl_idname, text=ImportTHUGPrk.bl_label, icon='PLUGIN')

#----------------------------------------------------------------------------------
def export_menu_func(self, context):
    if hasattr(bpy.ops.export, "scene_to_thug_xbx"):
        self.layout.operator("export.scene_to_thug_xbx", text="Scene to THUG1 level files", icon='PLUGIN')
    if hasattr(bpy.ops.export, "scene_to_thug_model"):
        self.layout.operator("export.scene_to_thug_model", text="Scene to THUG1 model", icon='PLUGIN')
    if hasattr(bpy.ops.export, "scene_to_thug2_xbx"):
        self.layout.operator("export.scene_to_thug2_xbx", text="Scene to THUG2 level files", icon='PLUGIN')
    if hasattr(bpy.ops.export, "scene_to_thug2_model"):
        self.layout.operator("export.scene_to_thug2_model", text="Scene to THUG2 model", icon='PLUGIN')

#----------------------------------------------------------------------------------
def add_menu_func(self, context):
    self.layout.menu(THUGPresetsMenu.bl_idname, text="THUG", icon='PLUGIN')

#----------------------------------------------------------------------------------
def register_menus():
    bpy.types.TOPBAR_MT_file_import.append(import_menu_func)
    bpy.types.TOPBAR_MT_file_export.append(export_menu_func)
    addPresetNodes()
    addPresetMesh()
    bpy.types.VIEW3D_MT_add.append(add_menu_func)
    script_template.init_templates()

#----------------------------------------------------------------------------------
def unregister_menus():
    bpy.types.TOPBAR_MT_file_import.remove(import_menu_func)
    bpy.types.TOPBAR_MT_file_export.remove(export_menu_func)
    bpy.types.VIEW3D_MT_add.remove(add_menu_func)
    clearPresetNodes()
    clearPresetMesh()

#----------------------------------------------------------------------------------
def register():
    for cls in IMPORT_OPERATOR_CLASSES:
        if hasattr(bpy.types, cls.__name__):
            continue
        bpy.utils.register_class(cls)
    register_menus()

#----------------------------------------------------------------------------------
def unregister():
    unregister_menus()
    for cls in reversed(IMPORT_OPERATOR_CLASSES):
        if hasattr(bpy.types, cls.__name__):
            bpy.utils.unregister_class(cls)


