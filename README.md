This addon allows you to import and export materials directly from Shader Cloud (https://shader.cloud)

Requires Blender 4.2 or newer (tested with Blender 5.0).

# Installation

1. Download the add-on ZIP from https://shader.cloud/download/blender-addon (or from the [latest release](https://github.com/Shadercloud/blender-addon/releases/latest)).
2. In Blender go to Edit -> Preferences -> Get Extensions, open the menu in the top right and choose **Install from Disk...**, then select the ZIP.
3. Enable **Allow Online Access** (Preferences -> System -> Network) so the add-on can reach shader.cloud.
4. Expand "Shader Cloud" in the Add-ons list and paste your API key from https://shader.cloud/account/api

The panels are in the Shader Editor sidebar (N) under the "Shader Cloud" tab.

# Building the ZIP

The `shadercloud` folder is the extension. Zip its contents (with `blender_manifest.toml` at the root of the ZIP), or run:

    blender --command extension build --source-dir shadercloud

## Changelog

### 1.0.0
- Blender 4.2+ extension format; works with Blender 5
- Bundles `rna_xml` (removed from Blender 5)
- Imported nodes are laid out automatically (no longer needs the Node Arrange add-on)
- Categories load on demand (refresh button) instead of when Blender starts
- No third-party Python modules required
- Clearer errors (invalid API key, online access disabled, connection problems)

### 0.1.2
- Added category selection
- Added material description field
- Added button to view material on shader.cloud
