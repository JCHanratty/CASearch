"""Generate .ico and .png icon files by drawing the icon with Pillow."""

import math
from pathlib import Path

from PIL import Image, ImageDraw


# Colors from the app's theme
BG_COLOR = (41, 37, 36)        # #292524 - surface-900
DOC_COLOR = (68, 64, 60)       # #44403c - surface-700
FOLD_COLOR = (87, 83, 78)      # #57534e - surface-600
LINE_COLOR = (120, 113, 108)   # #78716c - surface-500
COPPER = (217, 154, 58)        # #d99a3a - copper-400
LENS_BG = (41, 37, 36, 178)   # semi-transparent dark


def draw_icon(size: int) -> Image.Image:
    """Draw the CASearch icon at the given size."""
    # Work at 4x resolution for antialiasing, then downscale
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Scale helper
    def sc(v):
        return round(v * s / 256)

    # Background rounded rectangle
    draw.rounded_rectangle(
        [0, 0, s - 1, s - 1],
        radius=sc(40),
        fill=BG_COLOR,
    )

    # Document page
    doc_left, doc_top = sc(48), sc(36)
    doc_right, doc_bottom = sc(168), sc(192)
    fold_size = sc(30)

    # Document body (with folded corner cut)
    doc_points = [
        (doc_left, doc_top + sc(8)),  # top-left (rounded)
        (doc_left + sc(8), doc_top),  # top-left curve
        (doc_right - fold_size, doc_top),  # top before fold
        (doc_right, doc_top + fold_size),  # fold corner
        (doc_right, doc_bottom - sc(8)),  # bottom-right
        (doc_right - sc(8), doc_bottom),  # bottom-right curve
        (doc_left + sc(8), doc_bottom),  # bottom-left curve
        (doc_left, doc_bottom - sc(8)),  # bottom-left
    ]
    draw.polygon(doc_points, fill=DOC_COLOR)

    # Dog-ear fold triangle
    fold_points = [
        (doc_right - fold_size, doc_top),
        (doc_right, doc_top + fold_size),
        (doc_right - fold_size, doc_top + fold_size),
    ]
    draw.polygon(fold_points, fill=FOLD_COLOR)

    # Text lines on document
    line_y_positions = [82, 100, 118, 136, 154]
    line_widths = [88, 72, 80, 56, 68]
    for y_pos, width in zip(line_y_positions, line_widths):
        ly = sc(y_pos)
        lh = sc(6)
        lx = sc(64)
        lw = sc(width)
        draw.rounded_rectangle(
            [lx, ly, lx + lw, ly + lh],
            radius=sc(3),
            fill=LINE_COLOR,
        )

    # Magnifying glass
    cx, cy = sc(158), sc(152)
    outer_r = sc(44)
    inner_r = sc(38)
    stroke_w = sc(10)

    # Glass outer ring (copper)
    for offset in range(-stroke_w // 2, stroke_w // 2 + 1):
        draw.ellipse(
            [cx - outer_r + offset, cy - outer_r + offset,
             cx + outer_r - offset, cy + outer_r - offset],
            outline=COPPER,
            width=2,
        )

    # Fill inside the ring with dark background
    draw.ellipse(
        [cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r],
        fill=(41, 37, 36, 200),
    )

    # Lens highlight (subtle)
    hx, hy, hr = sc(148), sc(142), sc(12)
    draw.ellipse(
        [hx - hr, hy - hr, hx + hr, hy + hr],
        fill=(68, 64, 60, 100),
    )

    # Handle
    handle_width = sc(14)
    # Draw thick handle as a polygon (angled line)
    x1, y1 = sc(189), sc(183)
    x2, y2 = sc(220), sc(214)
    angle = math.atan2(y2 - y1, x2 - x1)
    dx = handle_width / 2 * math.sin(angle)
    dy = handle_width / 2 * math.cos(angle)
    handle_points = [
        (x1 - dx, y1 + dy),
        (x1 + dx, y1 - dy),
        (x2 + dx, y2 - dy),
        (x2 - dx, y2 + dy),
    ]
    draw.polygon(handle_points, fill=COPPER)
    # Rounded caps
    cap_r = handle_width // 2
    draw.ellipse([x1 - cap_r, y1 - cap_r, x1 + cap_r, y1 + cap_r], fill=COPPER)
    draw.ellipse([x2 - cap_r, y2 - cap_r, x2 + cap_r, y2 + cap_r], fill=COPPER)

    # Downscale with high-quality resampling for antialiasing
    img = img.resize((size, size), Image.Resampling.LANCZOS)
    return img


def main():
    base = Path(__file__).resolve().parent.parent
    sizes = [16, 32, 48, 64, 128, 256]
    images = {size: draw_icon(size) for size in sizes}

    # Save 256x256 PNG
    png_out = base / "static" / "icon.png"
    images[256].save(str(png_out), format="PNG")
    print(f"Saved {png_out}")

    # Save full .ico (all sizes) for the .exe
    # Pillow ICO: save the largest image, list all desired sizes
    ico_out = base / "static" / "icon.ico"
    images[256].convert("RGBA").save(
        str(ico_out),
        format="ICO",
        sizes=[(s, s) for s in sizes],
    )
    print(f"Saved {ico_out}")

    # Save favicon.ico (smaller sizes only)
    fav_sizes = [16, 32, 48]
    fav_out = base / "static" / "favicon.ico"
    images[48].convert("RGBA").save(
        str(fav_out),
        format="ICO",
        sizes=[(s, s) for s in fav_sizes],
    )
    print(f"Saved {fav_out}")


if __name__ == "__main__":
    main()
