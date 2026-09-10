bl_info = {
    "name": "THPS Scene Tools",
    "author": "denetii",
    "version": (1, 1, 0),
    "blender": (4, 2, 0),  # Updates target Blender version from (2, 79, 0)
    "location": "View3D > Sidebar > THPS Tab",
    "description": "THPS/THUG scene import, export, and lightmapping tools.",
    "category": "Import-Export",
}

import bpy


# Load and reload submodules
##################################

import importlib
import sys
import traceback

from . import developer_utils
from . import scene_props
from . import ui_draw

importlib.reload(developer_utils)
modules = developer_utils.setup_addon_modules(__path__, __name__, "bpy" in locals())

# Register
##################################


def register():
    guarded_modules = [
        scene_props,
        ui_draw,
    ]

    for module_name in (
        "import_thug1",
        "import_thug2",
        "import_thps2",
        "import_thps4",
        "import_park",
        "import_nodes",
        "qb",
        "skeleton",
        "tex",
        "autorail",
        "collision",
        "material",
        "object",
        "presets",
        "utils",
    ):
        module = sys.modules.get(f"{__name__}.{module_name}")
        if module is not None and module not in guarded_modules:
            guarded_modules.append(module)

    for module in guarded_modules:
        try:
            register_func = getattr(module, "register", None)
            if callable(register_func):
                register_func()
        except Exception:
            traceback.print_exc()
    print("Registered {} with {} modules".format(bl_info["name"], len(modules)))


def unregister():
    try:
        ui_draw.unregister()
        scene_props.unregister()
    except Exception:
        traceback.print_exc()
    print("Unregistered {}".format(bl_info["name"]))
