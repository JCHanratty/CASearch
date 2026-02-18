# CASearch Logo & Icon Design

## Summary

Create an application icon for CASearch (Contract Dashboard) that serves as the .exe icon, pywebview window icon, and web favicon.

## Visual Design

- **Motif**: Magnifying glass overlapping a document page
- **Colors**: Copper/amber (#d99a3a) magnifying glass on dark surface (#292524) background, document in mid-tone (#44403c) with text-line details (#78716c)
- **Style**: Flat/material design with bold strokes, readable from 16x16 to 256x256
- **Shape**: Rounded rectangle background containing the document + magnifying glass composition

## Icon Specifications

| File | Format | Sizes | Purpose |
|------|--------|-------|---------|
| static/icon.svg | SVG | Vector | Source artwork |
| static/icon.ico | ICO | 16, 32, 48, 64, 128, 256 | Windows .exe icon |
| static/icon.png | PNG | 256x256 | pywebview window icon |
| static/favicon.ico | ICO | 16, 32, 48 | Browser favicon |

## Integration Points

1. **casearch.spec** - Add `icon='static/icon.ico'` to `EXE()` call
2. **run.py** - Add icon path to `webview.create_window()`, handling frozen vs dev mode
3. **templates/layout.html** - Add `<link rel="icon" href="/static/favicon.ico">` in `<head>`

## Generation Method

- Create SVG programmatically
- Use Python (Pillow + cairosvg) to render SVG to PNG at multiple resolutions
- Package PNGs into .ico files
