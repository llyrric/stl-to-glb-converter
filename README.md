# STL to GLB Converter

A small desktop app that batch-converts STL files into indexed, non-decimated
binary `.glb` files using a headless Blender process. Built for miniature
painters who need 100% of the original vertex/triangle density preserved for
high-resolution vertex painting in game engines like Godot 4.

Raw STL files are "triangle soup" — every triangle has its own duplicate
vertices, with no sharing between adjacent faces. This tool imports the STL
into headless Blender, welds duplicate vertices at the same position into
proper indexed geometry (without moving a single surface), and exports a
clean binary GLB. No decimation or polygon reduction is ever applied.

## Requirements

- Python 3.9+
- [Blender](https://www.blender.org/download/) 3.x, 4.x, or 5.x installed somewhere on your system
- (Optional, for the nicer dark-themed UI) `customtkinter`

## Setup

```bash
pip install -r requirements.txt
```

If you skip this step, the app automatically falls back to plain `tkinter`
(included with Python), so it will still run.

## Run

**Windows:** double-click [`start.bat`](start.bat).

**Any platform:**

```bash
python gui_app.py
```

The app looks for Blender automatically (PATH, registry, Start Menu
shortcuts, common install folders, Spotlight on macOS, etc.) and remembers
whatever it finds — or whatever you manually browse to — in a small local
config file, so detection only has to succeed once per machine.

## Usage

1. **Blender Executable** — the app auto-detects common Blender install
   locations. If it's not found, click **Browse** and point it at your
   `blender.exe` (Windows), `Blender.app` binary (macOS), or `blender` binary
   (Linux).
2. **Select STL File(s)** — choose one or many `.stl` files to convert.
3. **Output Directory** — defaults to the folder of the first selected file;
   change it if you want converted `.glb` files elsewhere.
4. Optionally check **Overwrite existing files** if you want to re-convert
   files that already have a matching `.glb` in the output directory.
5. Click **Convert to GLB**. Progress and per-file Blender output are shown
   in the log window at the bottom.

## How it works

- `gui_app.py` is the desktop UI. For each selected STL, it launches
  `blender -b -P blender_convert.py -- <input.stl> <output.glb>` as a
  background subprocess so the UI stays responsive.
- `blender_convert.py` runs inside headless Blender: it imports the STL,
  merges duplicate vertices (`Merge by Distance`, threshold `0.00005`) to
  produce indexed geometry without moving any surfaces, fixes normals, and
  exports to GLB with Draco compression disabled so vertex positions stay
  exact. **No decimation is ever applied** — triangle count is preserved
  100%.

## Troubleshooting

- **"Blender Not Found"** — use the Browse button next to the Blender
  Executable field to manually locate your Blender install.
- **Conversion fails for a specific file** — check the log window; the
  underlying Blender error output is shown there.

## Project structure

- `gui_app.py` — the desktop UI (CustomTkinter, falls back to plain tkinter).
- `blender_convert.py` — the headless Blender script that does the actual
  import/weld/export work.
- `start.bat` — Windows double-click launcher.

## License

MIT
