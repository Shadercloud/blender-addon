# SPDX-License-Identifier: GPL-2.0-or-later

# Legacy add-on metadata; Blender 4.2+ reads blender_manifest.toml instead.
bl_info = {
    "name": "Shader Cloud",
    "author": "Shader Cloud",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "Shader Editor > Sidebar > Shader Cloud",
    "description": "Import and export materials to Shader Cloud",
    "doc_url": "https://github.com/Shadercloud/blender-addon",
    "category": "Material",
}

API_URL = "https://shader.cloud/"  # With trailing slash

import base64
import io
import json
import struct
import textwrap
import urllib.error
import urllib.parse
import urllib.request
import zlib

import bpy

try:
    import numpy as np  # bundled with official Blender builds; distro builds may lack it
except ImportError:
    np = None

from .rna_xml import rna2xml


class ApiError(Exception):
    pass


def addon_prefs(context=None):
    return (context or bpy.context).preferences.addons[__package__].preferences


def api_request(path, data=None, api_key=None, timeout=120):
    """POST (when data is given) or GET a Shader Cloud API endpoint and return the decoded JSON."""
    if not getattr(bpy.app, "online_access", True):
        raise ApiError("Online access is disabled (Preferences > System > Network > Allow Online Access)")

    headers = {
        "Accept": "application/json",
        "User-Agent": "ShaderCloud-Blender/%s (Blender %s)" % (".".join(map(str, bl_info["version"])), bpy.app.version_string),
    }
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    request = urllib.request.Request(API_URL + path, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as e:
        try:
            message = json.loads(e.read()).get("message")
        except Exception:
            message = None
        if e.code == 401:
            message = "invalid API key (set it in the add-on preferences)"
        raise ApiError("API request failed (%d): %s" % (e.code, message or e.reason))
    except urllib.error.URLError as e:
        raise ApiError("Could not reach Shader Cloud: %s" % e.reason)
    except TimeoutError:
        raise ApiError("Shader Cloud did not respond in time")

    try:
        return json.loads(payload)
    except ValueError:
        raise ApiError("Unexpected response from Shader Cloud")


# --- Categories --------------------------------------------------------------------------------
# Enum items returned from a callback must stay referenced from Python, so they live here.
_category_items = []


def _set_categories(json_text):
    global _category_items
    data = json.loads(json_text)
    if not data.get("success"):
        raise ApiError("Could not load categories")
    items = [("0", "Select a Category", "")]
    items += [(str(c["id"]), c["name"], "") for c in data["categories"]]
    _category_items = items


def category_items(self, context):
    if not _category_items:
        try:
            cached = addon_prefs(context).categories_cache
            if cached:
                _set_categories(cached)
        except Exception:
            pass
    return _category_items or [("0", "Click refresh to load categories", "")]


# --- Helpers -------------------------------------------------------------------------------------
def active_material(context):
    space = context.space_data
    if space and getattr(space, "id", None) and isinstance(space.id, bpy.types.Material):
        return space.id
    obj = context.object
    return obj.active_material if obj else None


def img_to_png(image):
    """Encode a Blender image as a PNG data URI (RGBA, 8 bit)."""
    width, height = image.size
    stride = width * 4
    if np is not None:
        pixels = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        rows = (np.clip(pixels, 0.0, 1.0) * 255.0).astype(np.uint8).reshape(height, stride)[::-1]
        raw_data = b"".join(b"\x00" + row.tobytes() for row in rows)
    else:
        buf = bytes(int(min(max(p, 0.0), 1.0) * 255.0) for p in image.pixels[:])
        raw_data = b"".join(b"\x00" + buf[span:span + stride] for span in range((height - 1) * stride, -1, -stride))

    def png_pack(png_tag, data):
        chunk_head = png_tag + data
        return struct.pack("!I", len(data)) + chunk_head + struct.pack("!I", 0xFFFFFFFF & zlib.crc32(chunk_head))

    png_bytes = b"".join([
        b"\x89PNG\r\n\x1a\n",
        png_pack(b"IHDR", struct.pack("!2I5B", width, height, 8, 6, 0, 0, 0)),
        png_pack(b"IDAT", zlib.compress(raw_data, 9)),
        png_pack(b"IEND", b""),
    ])
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode()


def material_xml(material):
    """The material's node tree (and nested groups) in the XML format the Shader Cloud API reads."""
    out = io.StringIO()

    def write_groups(tree):
        for node in tree.nodes:
            if node.type == "GROUP" and node.node_tree:
                out.write('<group name="%s">\n' % node.node_tree.name.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;"))
                rna2xml(fw=out.write, root_node="groupdata", root_rna=node.node_tree)
                out.write("</group>\n")
                write_groups(node.node_tree)

    out.write("<xml>")
    rna2xml(fw=out.write, root_node="Material", root_rna=material.node_tree)
    out.write("<groups>\n")
    write_groups(material.node_tree)
    out.write("</groups>\n</xml>")
    return out.getvalue()


def arrange_tree(tree, spacing_x=80, spacing_y=40):
    """Lay nodes out in columns by their distance from the output (imported nodes have no positions)."""
    nodes = [n for n in tree.nodes if n.type not in {"FRAME", "REROUTE"}]
    downstream = {n: [] for n in nodes}
    for link in tree.links:
        if link.from_node in downstream and link.to_node in downstream:
            downstream[link.from_node].append(link.to_node)

    depth = {}

    def node_depth(node, visiting):
        if node in depth:
            return depth[node]
        if node in visiting:  # cycles are invalid, but don't recurse forever
            return 0
        visiting.add(node)
        depth[node] = 1 + max((node_depth(m, visiting) for m in downstream[node]), default=-1)
        return depth[node]

    for node in nodes:
        node_depth(node, set())

    def height(node):
        if node.dimensions.y > 0:
            return node.dimensions.y / max(bpy.context.preferences.system.ui_scale, 0.1)
        sockets = sum(1 for s in node.outputs if s.enabled) + sum(1 for s in node.inputs if s.enabled)
        extra = 140 if node.bl_idname == "ShaderNodeValToRGB" else (60 if node.bl_idname == "ShaderNodeTexImage" else 0)
        return 60 + 22 * sockets + extra

    columns = {}
    for node in nodes:
        columns.setdefault(depth[node], []).append(node)

    x = 0
    for col in sorted(columns):
        def target_y(node):
            ys = [m.location.y - height(m) / 2 for m in downstream[node]]
            return -sum(ys) / len(ys) if ys else 0
        col_nodes = sorted(columns[col], key=target_y)
        total = sum(height(n) for n in col_nodes) + spacing_y * (len(col_nodes) - 1)
        width = max(n.width for n in col_nodes)
        y = total / 2
        for node in col_nodes:
            node.location = (x - node.width, y)
            y -= height(node) + spacing_y
        x -= width + spacing_x


# --- Properties ----------------------------------------------------------------------------------
class ShaderCloudPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    api_key: bpy.props.StringProperty(name="API Key", default="", subtype="PASSWORD")
    auto_arrange: bpy.props.BoolProperty(name="Arrange imported nodes", default=True)
    categories_cache: bpy.props.StringProperty(default="", options={"HIDDEN"})

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "api_key")
        row = layout.row()
        row.operator("wm.url_open", text="Get an API key", icon="URL").url = API_URL + "account/api"
        layout.prop(self, "auto_arrange")


class ShaderCloudProps(bpy.types.PropertyGroup):
    material_name: bpy.props.StringProperty(name="Material Name", default="")
    material_description: bpy.props.StringProperty(name="Material Description", default="")
    material_category: bpy.props.EnumProperty(name="Material Category", items=category_items)
    material_id: bpy.props.IntProperty(name="Material ID", default=0, min=0)
    export_message: bpy.props.StringProperty(default="")
    import_message: bpy.props.StringProperty(default="")


def draw_message(col, text):
    for line in textwrap.wrap(text, width=30):
        col.label(text=line)


# --- Operators -----------------------------------------------------------------------------------
class SHADERCLOUD_OT_refresh_categories(bpy.types.Operator):
    bl_idname = "shadercloud.refresh_categories"
    bl_label = "Refresh Categories"
    bl_description = "Download the list of material categories from Shader Cloud"

    def execute(self, context):
        try:
            data = api_request("api/categories")
            text = json.dumps(data)
            _set_categories(text)
            addon_prefs(context).categories_cache = text
        except (ApiError, KeyError, ValueError) as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        return {"FINISHED"}


class SHADERCLOUD_OT_export(bpy.types.Operator):
    bl_idname = "shadercloud.export"
    bl_label = "Export Material"
    bl_description = "Export the active material's nodes to Shader Cloud"

    @classmethod
    def poll(cls, context):
        material = active_material(context)
        return material is not None and material.node_tree is not None

    def execute(self, context):
        props = context.window_manager.shadercloud
        props.export_message = ""
        material = active_material(context)

        def fail(message):
            props.export_message = message
            self.report({"ERROR"}, message)
            return {"CANCELLED"}

        if not props.material_name.strip():
            return fail("You must give your material a name")
        try:
            category = int(props.material_category)
        except (TypeError, ValueError):
            category = 0
        if category <= 0:
            return fail("Please select a category")
        api_key = addon_prefs(context).api_key
        if not api_key:
            return fail("Set your API key in the add-on preferences")

        data = {
            "xml": material_xml(material),
            "material_name": props.material_name,
            "material_description": props.material_description,
            "material_category": category,
        }
        if material.get("shadercloud_id"):
            data["material_id"] = material["shadercloud_id"]

        for node in material.node_tree.nodes:
            image = getattr(node, "image", None)
            if image is not None and image.size[0] > 0 and image.size[1] > 0:
                data["images[%s][image_data]" % node.name] = img_to_png(image)
                data["images[%s][color_space]" % node.name] = image.colorspace_settings.name

        try:
            result = api_request("api/import", data, api_key)
        except ApiError as e:
            return fail(str(e))
        if not result.get("success"):
            return fail("Shader Cloud error: %s" % result.get("error", "unknown error"))

        material["shadercloud_id"] = result["material_id"]
        props.export_message = "Material was successfully added to Shader Cloud"
        self.report({"INFO"}, props.export_message)
        return {"FINISHED"}


class SHADERCLOUD_OT_import(bpy.types.Operator):
    bl_idname = "shadercloud.import_material"
    bl_label = "Import Material"
    bl_description = "Import a Shader Cloud material onto the active object"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.window_manager.shadercloud
        props.import_message = ""

        def fail(message):
            props.import_message = message
            self.report({"ERROR"}, message)
            return {"CANCELLED"}

        if props.material_id <= 0:
            return fail("You must enter a material ID")
        if context.object is None:
            return fail("Select an object to apply the material to")
        api_key = addon_prefs(context).api_key
        if not api_key:
            return fail("Set your API key in the add-on preferences")

        try:
            result = api_request("api/download", {"material_id": props.material_id}, api_key)
        except ApiError as e:
            return fail(str(e))
        if not result.get("success"):
            return fail("Shader Cloud error: %s" % result.get("error", "unknown error"))

        groups_before = set(bpy.data.node_groups)
        materials_before = set(bpy.data.materials)
        try:
            # The site generates a script that builds the material (and its node groups)
            exec(compile(result["code"], "shadercloud_material_%d" % props.material_id, "exec"), {"__name__": "shadercloud_material"})
        except Exception as e:
            return fail("Could not build the material: %s" % e)

        if addon_prefs(context).auto_arrange:
            for material in set(bpy.data.materials) - materials_before:
                if material.node_tree:
                    arrange_tree(material.node_tree)
            for group in set(bpy.data.node_groups) - groups_before:
                arrange_tree(group)

        props.import_message = "Material was successfully imported"
        self.report({"INFO"}, props.import_message)
        return {"FINISHED"}


class SHADERCLOUD_OT_reset(bpy.types.Operator):
    bl_idname = "shadercloud.reset"
    bl_label = "Export as New Material"
    bl_description = "Forget this material's Shader Cloud ID so the next export creates a new material"

    @classmethod
    def poll(cls, context):
        material = active_material(context)
        return material is not None and "shadercloud_id" in material

    def execute(self, context):
        del active_material(context)["shadercloud_id"]
        return {"FINISHED"}


# --- Panels --------------------------------------------------------------------------------------
class ShaderCloudPanel:
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Shader Cloud"

    @classmethod
    def poll(cls, context):
        return getattr(context.space_data, "tree_type", None) == "ShaderNodeTree"


class SHADERCLOUD_PT_export(ShaderCloudPanel, bpy.types.Panel):
    bl_idname = "SHADERCLOUD_PT_export"
    bl_label = "Shader Cloud Export"

    def draw(self, context):
        props = context.window_manager.shadercloud
        col = self.layout.column(align=True)
        material = active_material(context)
        if material is None:
            col.label(text="No active material")
            return

        col.label(text="Material Name")
        col.prop(props, "material_name", text="")
        col.label(text="Material Category")
        row = col.row(align=True)
        row.prop(props, "material_category", text="")
        row.operator("shadercloud.refresh_categories", text="", icon="FILE_REFRESH")
        col.label(text="Material Description")
        col.prop(props, "material_description", text="")
        if props.export_message:
            draw_message(col, props.export_message)
        col.separator()
        col.operator("shadercloud.export")

        if material.get("shadercloud_id"):
            col.separator()
            col.label(text="Shader Cloud ID: %s" % material["shadercloud_id"])
            col.operator("wm.url_open", text="Visit Material Page", icon="URL").url = API_URL + "material/%s" % material["shadercloud_id"]
            col.operator("shadercloud.reset")


class SHADERCLOUD_PT_import(ShaderCloudPanel, bpy.types.Panel):
    bl_idname = "SHADERCLOUD_PT_import"
    bl_label = "Shader Cloud Import"

    def draw(self, context):
        props = context.window_manager.shadercloud
        col = self.layout.column(align=True)
        col.label(text="Material ID")
        col.prop(props, "material_id", text="")
        if props.import_message:
            draw_message(col, props.import_message)
        col.separator()
        col.operator("shadercloud.import_material")


classes = (
    ShaderCloudPreferences,
    ShaderCloudProps,
    SHADERCLOUD_OT_refresh_categories,
    SHADERCLOUD_OT_export,
    SHADERCLOUD_OT_import,
    SHADERCLOUD_OT_reset,
    SHADERCLOUD_PT_export,
    SHADERCLOUD_PT_import,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.shadercloud = bpy.props.PointerProperty(type=ShaderCloudProps)


def unregister():
    del bpy.types.WindowManager.shadercloud
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
