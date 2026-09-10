# io_mtx_scene
**io_mtx_scene** is a modern Blender addon for importing and working with
scene and model assets from **MTX Mototrax** and related Neversoft game
formats.
It is a continuation and modernization of the original
[`io_thps_scene`](https://github.com/denetii/io_thps_scene) Blender addon by
**denetii**, adapted for modern versions of Blender.
The project is currently focused on bringing the functionality of the
original `io_thps_scene` workflow to **Blender 5.x**, with particular
attention to the formats and assets used by MTX Mototrax.
### Blender compatibility
The primary target is Blender 5.x. The original `io_thps_scene` project was developed for older Blender
versions, including Blender 2.79 and the 2.8x generation. This project
updates the addon architecture and Blender API usage for modern Blender
versions.

### Installation
1. Download or clone this repository.
2. Install the addon through Blender's addon/extension installation
   interface.
3. Enable **io_mtx_scene**.
4. Use the import tools provided by the addon.

### Materials and textures
Not working properly. Still under development.

### Project status
The importer is already capable of loading:
- meshes
- vertex groups
- weights
- UV data
- textures
- material assignments (presents visual glitches)

### License
See the repository license file for the licensing terms of this project
and the applicable terms for the original code on which it is based.
Because io_mtx_scene contains derivative work from another project, the
license and attribution requirements of the original project should also
be respected.
