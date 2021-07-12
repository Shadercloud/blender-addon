# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

bl_info = {
    "name": "Shader Cloud",
    "author": "Shader Cloud",
    "version": (0, 1, 2),
    "blender": (2, 80, 0),
    "location": "Shader Editor > Sidebar > Shader Cloud",
    "description": "Allows you to export materials to Shader Cloud",
    "doc_url": "https://github.com/Shadercloud/blender-addon",
    "category": "Object",
    "api_url": "https://shader.cloud/", # With trailing slash /
}

import bpy
import requests
import io
import os
import textwrap
import base64
import zlib
import struct
import tempfile
import json

from rna_xml import rna2xml
from contextlib import redirect_stdout

def img_to_png(blender_image):
    width = blender_image.size[0]
    height = blender_image.size[1]
    buf = bytearray([int(p * 255) for p in blender_image.pixels])

    # reverse the vertical line order and add null bytes at the start
    width_byte_4 = width * 4
    raw_data = b''.join(b'\x00' + buf[span:span + width_byte_4]
                        for span in range((height - 1) * width_byte_4, -1, - width_byte_4))

    def png_pack(png_tag, data):
        chunk_head = png_tag + data
        return (struct.pack("!I", len(data)) +
                chunk_head +
                struct.pack("!I", 0xFFFFFFFF & zlib.crc32(chunk_head)))

    png_bytes = b''.join([
        b'\x89PNG\r\n\x1a\n',
        png_pack(b'IHDR', struct.pack("!2I5B", width, height, 8, 6, 0, 0, 0)),
        png_pack(b'IDAT', zlib.compress(raw_data, 9)),
        png_pack(b'IEND', b'')])

    return 'data:image/png;base64,' + base64.b64encode(png_bytes).decode()

def ParseShaderCloudCategories():
    try:
        cat_string = bpy.context.preferences.addons['shadercloud'].preferences.shader_cloud_categories
    except Exception:
        cat_string = DownloadShaderCloudCategories()
        try:
            bpy.context.preferences.addons['shadercloud'].preferences.shader_cloud_categories = cat_string
        except Exception:
            pass

    json_value = json.loads(cat_string)
    if(json_value['success']):
        cats = [("0", "Select a Category", '', '', 0)]

        n = 1
        for x in json_value['categories']:
            cats.append((str(x['id']), x['name'], '', '', n))
            n+=1
        return cats

    raise Exception("Could not parse categories")

def DownloadShaderCloudCategories():
    print('Downloading Shadercloud Categories')
    try:
        url = bl_info['api_url']+'api/categories'
        req = requests.get(url)
        if req.status_code != 200:
            self.message("ERROR", "API Category Request Failed")
            return {"CANCELLED"}
    except requests.exceptions.RequestException as e:
        self.message("ERROR", "API Category Request Failed")
        return {"CANCELLED"}

    return req.text

class ShaderCloudPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    api_key: bpy.props.StringProperty(default="", name="API Key")
    use_arranger: bpy.props.BoolProperty(default=1, name="Use Arranger Node Addon")
    shader_cloud_categories: bpy.props.StringProperty(default=DownloadShaderCloudCategories(), name="shader_cloud_categories")

    def draw(self, context):
        layout = self.layout
        layout.label(text='Shader Cloud API Key:')
        row = layout.row()
        row.prop(self, 'api_key', expand=True)
        row = layout.row()
        row.prop(self, 'use_arranger', expand=True)
        row = layout.row()
        row.operator('object.shader_cloud_save')
        row = layout.row()
        row.prop(self, 'shader_cloud_categories', expand=False)

class ShaderCloudPanel:
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Shader Cloud"
    bl_options = {"HEADER_LAYOUT_EXPAND"}

class MaterialProps(bpy.types.PropertyGroup):

    material_name: bpy.props.StringProperty(name="Material Name",default="")
    api_loading_export: bpy.props.BoolProperty(name="API Loading",default=False)
    message_text: bpy.props.StringProperty(name="Message Text",default="")
    message_type: bpy.props.StringProperty(name="Message Type",default="")
    material_description: bpy.props.StringProperty(name="Material Description",default="")

    material_category: bpy.props.EnumProperty(
        items=ParseShaderCloudCategories(),
        name="Material Category",
        default="0",
    )

class ImportProps(bpy.types.PropertyGroup):

    material_id: bpy.props.IntProperty(name="Material ID",default=0)
    api_loading_import: bpy.props.BoolProperty(name="API Loading",default=False)
    message_text: bpy.props.StringProperty(name="Message Text",default="")
    message_type: bpy.props.StringProperty(name="Message Type",default="")

def ClearMessages():
    bpy.context.scene.material_props.message_text = ''
    bpy.context.scene.import_props.message_text = ''

class OBJECT_OT_shader_cloud_export(bpy.types.Operator):
    bl_idname = "object.shader_cloud_export"
    bl_label = "Export Material"
    bl_description = "Export your material nodes to shader cloud"

    def setLoading(self,val):
        bpy.context.scene.material_props.api_loading_export = val

    def message(self, type, message):
        bpy.context.scene.material_props.message_text = message
        bpy.context.scene.material_props.message_type = type
        print(type)
        if type == 'INFO' or type == 'ERROR':
            self.report({type}, message)


    def nestedGroups(self, tree):
        if hasattr(tree, 'nodes') == False:
            return False
        for x in tree.nodes:
            if x.type == 'GROUP':
                print('<group name="'+x.node_tree.name+'">')
                rna2xml(root_node="groupdata", root_rna=x.node_tree)
                print('</group>')
                self.nestedGroups(x.node_tree)

    @classmethod
    def poll(self, context):
        if bpy.context.scene.material_props.api_loading_export:
            return False
        return True

    def execute(self, context):

        name = bpy.context.scene.material_props.material_name
        description = bpy.context.scene.material_props.material_description
        category = int(bpy.context.scene.material_props.material_category)

        if name == '':
            self.message("ERROR", "You must give your material a name")
            return {"CANCELLED"}

        if category <= 0:
            self.message("ERROR", "Please select a category")
            return {"CANCELLED"}

        material = bpy.context.active_object.active_material

        f = io.StringIO()
        with redirect_stdout(f):
            rna2xml(root_node="Material", root_rna=material.node_tree)

            print('<groups>')
            self.nestedGroups(material.node_tree)
            print('</groups>')

        url = bl_info['api_url']+'api/import'

        myobj = {'xml': '<xml>'+f.getvalue()+'</xml>', 'material_name': name, 'material_description': description, 'material_category': category}

        if material.get('shadercloud_id'):
            myobj['material_id'] = material.get('shadercloud_id')

        api_key = context.preferences.addons['shadercloud'].preferences.api_key

        # Check if there are any images that need uploading
        images = []
        for node in material.node_tree.nodes:
            if hasattr(node, "image"):
                myobj['images['+node.name+'][image_data]'] = img_to_png(node.image)
                myobj['images['+node.name+'][color_space]'] = node.image.colorspace_settings.name

        headers = {"Authorization": "Bearer "+api_key, "Accept": "application/json"}

        try:
            req = requests.post(url, data = myobj, headers = headers)
            x = req.json()
            if req.status_code != 200:
                self.message("ERROR", "API Request Failed: "+x.get('message'))
                return {"CANCELLED"}
        except requests.exceptions.RequestException as e:
            self.message("ERROR", "API Request Failed")
            return {"CANCELLED"}

        if(x.get('success') == False):
            self.message("ERROR", 'Shader Cloud Error: ' +x.get('error'))
            return {"CANCELLED"}

        material['shadercloud_id'] = x.get('material_id')

        self.message("INFO", "Material was succesfully added to Shader Cloud")

        return {"FINISHED"}

    def invoke(self, context, event):

        ClearMessages()

        self.setLoading(True)

        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

        result = self.execute(context)

        self.setLoading(False)

        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

        return result


class SHADER_CLOUD_PT_1(ShaderCloudPanel, bpy.types.Panel):
    bl_idname = "SHADER_CLOUD_PT_1"
    bl_label = "Shader Cloud Export"

    def draw(self, context):
        props = context.scene.material_props

        layout = self.layout

        col = layout.column(align=True)

        row = col.row(align=True)
        row.label(text="Material Name")

        row = col.row(align=True)
        row.prop(props,"material_name",text="")

        row = col.row(align=True)
        row.label(text="Material Category")

        row = col.row(align=True)
        row.prop(props,"material_category",text="")

        row = col.row(align=True)
        row.label(text="Material Description")

        row = col.row(align=True)
        row.prop(props,"material_description",text="")

        if bpy.context.scene.material_props.api_loading_export:
            row = col.row(align=True)
            row.label(text="Connecting to API...")


        if bpy.context.scene.material_props.message_text != "":
            wrapp = textwrap.TextWrapper(width=30) #50 = maximum length
            wList = wrapp.wrap(text=bpy.context.scene.material_props.message_text)

            for text in wList:
                row = col.row(align=True)
                row.label(text=text)

        row = col.row(align=True)
        row.operator('object.shader_cloud_export')

        material = bpy.context.active_object.active_material

        if material.get('shadercloud_id'):
            row = col.row(align=True)
            row.label(text="Shader Cloud ID: "+str(material.get('shadercloud_id')))
            row = col.row(align=True)
            row.operator("wm.url_open", text="Visit Material Page").url = bl_info['api_url']+"material/"+str(material.get('shadercloud_id'))
            row = col.row(align=True)
            row.operator('object.shader_cloud_reset')



class OBJECT_OT_shader_cloud_import(bpy.types.Operator):
    bl_idname = "object.shader_cloud_import"
    bl_label = "Import Material"
    bl_description = "Import your material nodes to shader cloud"

    def setLoading(self,val):
        bpy.context.scene.import_props.api_loading_import = val

    def message(self, type, message):
        bpy.context.scene.import_props.message_text = message
        bpy.context.scene.import_props.message_type = type
        print(type)
        if type == 'INFO' or type == 'ERROR':
            self.report({type}, message)

    @classmethod
    def poll(self, context):
        if bpy.context.scene.import_props.api_loading_import:
            return False
        return True

    def execute(self, context):

        material_id = bpy.context.scene.import_props.material_id

        if material_id <= 0:
            self.message("ERROR", "You must enter a material ID")
            return {"CANCELLED"}

        url = bl_info['api_url']+'api/download'
        myobj = {'material_id': material_id}
        api_key = context.preferences.addons['shadercloud'].preferences.api_key

        headers = {"Authorization": "Bearer "+api_key, "Accept": "application/json"}

        try:
            req = requests.post(url, data = myobj, headers = headers)
            x = req.json()
            if req.status_code != 200:
                self.message("ERROR", "API Request Failed: "+x.get('message'))
                return {"CANCELLED"}
        except requests.exceptions.RequestException as e:
            self.message("ERROR", "API Request Failed")
            return {"CANCELLED"}

        if(x.get('success') == False):
            self.message("ERROR", 'Shader Cloud Error: ' +x.get('error'))
            return {"CANCELLED"}

        exec(x.get('code'))

        self.message("INFO", "Material was succesfully imported to your blender object")


        return {"FINISHED"}

    def invoke(self, context, event):
        ClearMessages()

        self.setLoading(True)

        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

        result = self.execute(context)

        self.setLoading(False)

        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

        # Enable node arranger
        if(context.preferences.addons['shadercloud'].preferences.use_arranger):
            bpy.ops.preferences.addon_enable(module = "node_arrange")

            for area in bpy.context.screen.areas:
                if area.type == 'NODE_EDITOR':
                    for region in area.regions:
                        if region.type == 'WINDOW':
                            ctx = bpy.context.copy()
                            ctx['area'] = area
                            ctx['region'] = region
                            bpy.ops.node.button(ctx, "INVOKE_DEFAULT")

        return result



class SHADER_CLOUD_PT_2(ShaderCloudPanel, bpy.types.Panel):
    bl_idname = "SHADER_CLOUD_PT_2"
    bl_label = "Shader Cloud Import"

    def draw(self, context):
        props = context.scene.import_props

        layout = self.layout

        col = layout.column(align=True)

        row = col.row(align=True)

        row.label(text="Material ID")

        row = col.row(align=True)

        row.prop(props,"material_id",text="")

        if bpy.context.scene.import_props.api_loading_import:
            row = col.row(align=True)
            row.label(text="Connecting to API...")


        if bpy.context.scene.import_props.message_text != "":
            wrapp = textwrap.TextWrapper(width=30) #50 = maximum length
            wList = wrapp.wrap(text=bpy.context.scene.import_props.message_text)

            for text in wList:
                row = col.row(align=True)
                row.label(text=text)

        row = col.row(align=True)
        row.operator('object.shader_cloud_import')

class OBJECT_OT_shader_cloud_save(bpy.types.Operator):
    bl_idname = "object.shader_cloud_save"
    bl_label = "Save Settings"

    def invoke(self, context, event):
        return {"FINISHED"}


class OBJECT_OT_shader_cloud_reset(bpy.types.Operator):
    bl_idname = "object.shader_cloud_reset"
    bl_label = "Reset New Material"

    def invoke(self, context, event):

        material = bpy.context.active_object.active_material
        if material.get('shadercloud_id'):
            del material['shadercloud_id']

        return {"FINISHED"}


classes = (
    ShaderCloudPreferences,
    MaterialProps,
    ImportProps,
    SHADER_CLOUD_PT_1,
    SHADER_CLOUD_PT_2,
    OBJECT_OT_shader_cloud_export,
    OBJECT_OT_shader_cloud_import,
    OBJECT_OT_shader_cloud_save,
    OBJECT_OT_shader_cloud_reset,

)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.material_props = bpy.props.PointerProperty(type=MaterialProps)
    bpy.types.Scene.import_props = bpy.props.PointerProperty(type=ImportProps)

def unregister():
    for cls in classes:
        bpy.utils.unregister_class(cls)
    del(bpy.types.Scene.material_props)
    del(bpy.types.Scene.import_props)


if __name__ == "__main__":
    register()
