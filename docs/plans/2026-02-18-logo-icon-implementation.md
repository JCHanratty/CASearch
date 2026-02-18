# CASearch Logo & Icon Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create a copper-on-dark magnifying-glass-over-document icon for CASearch and wire it into the .exe, pywebview window, and browser favicon.

**Architecture:** Generate the icon as an SVG file, then use a Python script with Pillow and cairosvg to render it to PNG at multiple resolutions and package into .ico files. Modify three existing files to reference the new icon.

**Tech Stack:** SVG, Python (Pillow, cairosvg), PyInstaller spec, pywebview, HTML

---

### Task 1: Install icon generation dependencies

**Files:** None (pip install only)

**Step 1: Install Pillow and cairosvg**

Run:
```bash
cd "C:/Users/jorda/Desktop/AI Collective Group" && .venv/Scripts/pip.exe install Pillow cairosvg
```

Expected: Successfully installed packages.

---

### Task 2: Create the SVG icon source file

**Files:**
- Create: `static/icon.svg`

**Step 1: Create `static/icon.svg`**

Write this file with a 256x256 viewBox. The design:
- Rounded rectangle background (#292524)
- Document page shape (#44403c) with 3 text lines (#78716c)
- Copper (#d99a3a) magnifying glass overlapping bottom-right of document
- Magnifying glass has a circular lens with a thick handle angled at ~45 degrees

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">
  <!-- Background -->
  <rect width="256" height="256" rx="40" ry="40" fill="#292524"/>

  <!-- Document page -->
  <rect x="48" y="36" width="120" height="156" rx="8" ry="8" fill="#44403c"/>
  <!-- Dog-ear fold -->
  <path d="M138 36 L168 66 L138 66 Z" fill="#57534e"/>
  <!-- Text lines on document -->
  <rect x="64" y="82" width="88" height="6" rx="3" fill="#78716c"/>
  <rect x="64" y="100" width="72" height="6" rx="3" fill="#78716c"/>
  <rect x="64" y="118" width="80" height="6" rx="3" fill="#78716c"/>
  <rect x="64" y="136" width="56" height="6" rx="3" fill="#78716c"/>
  <rect x="64" y="154" width="68" height="6" rx="3" fill="#78716c"/>

  <!-- Magnifying glass -->
  <!-- Glass circle (outer ring) -->
  <circle cx="158" cy="152" r="44" fill="none" stroke="#d99a3a" stroke-width="10"/>
  <!-- Glass fill (dark with slight transparency to suggest lens) -->
  <circle cx="158" cy="152" r="38" fill="#292524" opacity="0.7"/>
  <!-- Lens highlight -->
  <circle cx="148" cy="142" r="12" fill="#44403c" opacity="0.5"/>
  <!-- Handle -->
  <line x1="189" y1="183" x2="220" y2="214" stroke="#d99a3a" stroke-width="14" stroke-linecap="round"/>
</svg>
```

**Step 2: Verify the SVG renders**

Open `static/icon.svg` in a browser to visually confirm it looks correct. The icon should show a dark document with text lines and a copper magnifying glass overlapping the bottom-right corner.

**Step 3: Commit**

```bash
git add static/icon.svg
git commit -m "feat: add CASearch logo SVG source (magnifying glass + document)"
```

---

### Task 3: Create the icon generation script and generate .ico/.png files

**Files:**
- Create: `scripts/generate_icon.py`
- Output: `static/icon.ico`, `static/icon.png`, `static/favicon.ico`

**Step 1: Create `scripts/generate_icon.py`**

```python
"""Generate .ico and .png icon files from the SVG source."""

import os
from pathlib import Path
from io import BytesIO

import cairosvg
from PIL import Image


def main():
    base = Path(__file__).resolve().parent.parent
    svg_path = base / "static" / "icon.svg"
    svg_data = svg_path.read_bytes()

    # Render PNG at multiple sizes
    sizes = [16, 32, 48, 64, 128, 256]
    images = {}
    for size in sizes:
        png_data = cairosvg.svg2png(bytestring=svg_data, output_width=size, output_height=size)
        img = Image.open(BytesIO(png_data))
        images[size] = img

    # Save 256x256 PNG for pywebview
    png_out = base / "static" / "icon.png"
    images[256].save(str(png_out), format="PNG")
    print(f"Saved {png_out}")

    # Save full .ico (all sizes) for the .exe
    ico_out = base / "static" / "icon.ico"
    ico_images = [images[s] for s in sizes]
    ico_images[0].save(
        str(ico_out),
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=ico_images[1:],
    )
    print(f"Saved {ico_out}")

    # Save favicon.ico (smaller sizes only)
    fav_sizes = [16, 32, 48]
    fav_images = [images[s] for s in fav_sizes]
    fav_out = base / "static" / "favicon.ico"
    fav_images[0].save(
        str(fav_out),
        format="ICO",
        sizes=[(s, s) for s in fav_sizes],
        append_images=fav_images[1:],
    )
    print(f"Saved {fav_out}")


if __name__ == "__main__":
    main()
```

**Step 2: Run the generation script**

Run:
```bash
cd "C:/Users/jorda/Desktop/AI Collective Group" && .venv/Scripts/python.exe scripts/generate_icon.py
```

Expected output:
```
Saved .../static/icon.png
Saved .../static/icon.ico
Saved .../static/favicon.ico
```

**Step 3: Verify the generated files exist and have reasonable sizes**

Run:
```bash
ls -la "C:/Users/jorda/Desktop/AI Collective Group/static/icon."* "C:/Users/jorda/Desktop/AI Collective Group/static/favicon.ico"
```

Expected: `icon.ico` should be ~70-150KB (multi-res), `icon.png` ~5-30KB, `favicon.ico` ~5-15KB.

**Step 4: Commit**

```bash
git add scripts/generate_icon.py static/icon.ico static/icon.png static/favicon.ico
git commit -m "feat: generate icon.ico, icon.png, favicon.ico from SVG source"
```

---

### Task 4: Wire icon into casearch.spec (PyInstaller .exe icon)

**Files:**
- Modify: `casearch.spec:50-64` (the `EXE()` call)

**Step 1: Add `icon=` parameter to EXE()**

In `casearch.spec`, change the `EXE()` call to add `icon='static/icon.ico'` after `console=False`:

```python
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='CASearch',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=False,
    icon='static/icon.ico',
)
```

**Step 2: Commit**

```bash
git add casearch.spec
git commit -m "feat: set .exe icon in PyInstaller spec"
```

---

### Task 5: Wire icon into pywebview window (run.py)

**Files:**
- Modify: `run.py:46-53` (the `webview.create_window()` call)

**Step 1: Add icon path resolution and pass to create_window**

The icon path must resolve correctly in both frozen (PyInstaller) and dev mode. `base_path` is already set correctly on line 11-15. Add the icon path after `base_path` is resolved, and pass it to `create_window()`.

After line 15 (the `else` block for `base_path`), the icon path is:

```python
    icon_path = os.path.join(base_path, "static", "icon.png")
```

Add this line after line 19 (`sys.path.insert(0, base_path)`), then modify `create_window()`:

```python
    # Open native desktop window
    icon_path = os.path.join(base_path, "static", "icon.png")
    window = webview.create_window(
        "Contract Dashboard",
        url,
        width=1280,
        height=860,
        min_size=(900, 600),
    )
    webview.start()
```

Note: pywebview on Windows does NOT support the `icon` parameter in `create_window()` — it uses the .exe icon from PyInstaller instead. So for the desktop window, the .exe icon from Task 4 handles it. We still keep the `icon.png` available for any future cross-platform use, but no change to `create_window()` is needed.

Actually, check the pywebview docs: the Windows EdgeChromium backend does NOT support custom window icons via the API. The window icon comes from the .exe itself. So **no change to run.py is needed** for the window icon — Task 4 handles it.

**Step 2: Commit (skip if no changes)**

No changes needed to run.py for window icon.

---

### Task 6: Wire favicon into layout.html

**Files:**
- Modify: `templates/layout.html:6-7` (add favicon link after `<title>`)

**Step 1: Add favicon link tag**

After line 6 (`<title>{{ page_title }} — Contract Dashboard</title>`), add:

```html
    <link rel="icon" type="image/x-icon" href="/static/favicon.ico">
```

**Step 2: Commit**

```bash
git add templates/layout.html
git commit -m "feat: add favicon to browser tab"
```

---

### Task 7: Final commit and push

**Step 1: Verify all changes are committed**

Run:
```bash
cd "C:/Users/jorda/Desktop/AI Collective Group" && git status
```

Expected: clean working tree.

**Step 2: Push to git**

Run:
```bash
git push origin main
```

Expected: successful push to origin/main.
