"""Erzeugt das App-Symbol: ein schwarzes, leicht gerundetes Quadrat mit dem Gesicht
des Faultier-Maskottchens (Entwurf C2, siehe logo/).

    python scripts/make-icons.py

Ergebnis: assets/icon.png (1024 px), assets/icon.ico (Windows) und
assets/icon.icns (macOS). Die beiden letzten erzeugen wir selbst: das
Icon-Werkzeug von electron-builder bricht unter Windows mit
"WebAssembly.Memory(): could not allocate memory" ab. Die Form entspricht
`BrandMark` in frontend/components/app-shell.tsx und den SVGs in
frontend/public/icons/ (die PNG-Fassungen dort entstehen hier mit).
"""

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024
ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ICONS = ROOT.parent / "frontend/public/icons"
SUPER = 4  # Ueberabtastung gegen ausgefranste Kanten

# Gesichtsform in den Koordinaten von frontend/components/assistant/faultier.tsx.
FACE = [(60, 43), (76, 43, 85, 52, 85, 63), (85, 74, 74, 81, 60, 81), (46, 81, 35, 74, 35, 63), (35, 52, 44, 43, 60, 43)]
PATCH_L = [(56, 56.5), (52, 51.5, 42, 54.5, 37, 63), (35, 67.5, 39, 70, 43.5, 68), (50.5, 65, 58, 61.5, 56, 56.5)]
PATCH_R = [(64, 56.5), (68, 51.5, 78, 54.5, 83, 63), (85, 67.5, 81, 70, 76.5, 68), (69.5, 65, 62, 61.5, 64, 56.5)]
MOUTH = [(54.5, 70.5), (54.5, 70.5, 57.25, 73.5, 60, 70.5), (60, 70.5, 62.75, 73.5, 65.5, 70.5)]
INK, WHITE = (10, 10, 10, 255), (255, 255, 255, 255)


def bezier(path: list[tuple[float, ...]]) -> list[tuple[float, float]]:
    points = [path[0]]
    x0, y0 = path[0]
    for x1, y1, x2, y2, x3, y3 in path[1:]:
        for step in range(1, 25):
            t = step / 24
            u = 1 - t
            points.append((
                u**3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t**3 * x3,
                u**3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * y3,
            ))
        x0, y0 = x3, y3
    return points


def draw_face(draw: ImageDraw.ImageDraw, size: int, width: float) -> None:
    """Zeichnet das Gesicht mittig; `width` ist seine Breite in Pixeln."""
    scale = width / 50
    def at(x: float, y: float) -> tuple[float, float]:
        return (size / 2 + (x - 60) * scale, size / 2 + (y - 62) * scale)
    def shape(path: list[tuple[float, ...]], fill: tuple[int, ...]) -> None:
        draw.polygon([at(x, y) for x, y in bezier(path)], fill=fill)
    def dot(cx: float, cy: float, rx: float, ry: float, fill: tuple[int, ...]) -> None:
        (x0, y0), (x1, y1) = at(cx - rx, cy - ry), at(cx + rx, cy + ry)
        draw.ellipse((x0, y0, x1, y1), fill=fill)
    shape(FACE, WHITE)
    shape(PATCH_L, INK)
    shape(PATCH_R, INK)
    for cx in (48.5, 71.5):
        dot(cx, 60.5, 3.4, 3.4, WHITE)
        dot(cx + 0.4, 60.9, 2, 2, INK)
    dot(60, 66, 4.4, 2.9, INK)
    line = [at(x, y) for x, y in bezier(MOUTH)]
    draw.line(line, fill=INK, width=round(2 * scale), joint="curve")
    for x, y in (line[0], line[-1]):
        r = scale
        draw.ellipse((x - r, y - r, x + r, y + r), fill=INK)


def render(size: int, margin: float, radius: float, face_width: float) -> Image.Image:
    big = size * SUPER
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((margin * SUPER, margin * SUPER, big - margin * SUPER, big - margin * SUPER), radius=radius * SUPER, fill=INK)
    draw_face(draw, big, face_width * SUPER)
    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    # macOS-Symbole haben einen Rand von etwa 10 % - ohne ihn wirkt das Symbol
    # im Dock groesser als alle anderen.
    image = render(SIZE, margin=100, radius=190, face_width=540)
    assets = ROOT / "assets"
    image.save(assets / "icon.png")
    image.save(assets / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    image.save(assets / "icon.icns")
    # PWA-Symbole sind "maskable": das Betriebssystem schneidet selbst zu, also
    # randlos schwarz und das Gesicht in der inneren Sicherheitszone (~60 %).
    for px in (192, 512):
        render(px, margin=0, radius=0, face_width=px * 0.6).save(PUBLIC_ICONS / f"icon-{px}.png")
    print(f"Symbole geschrieben: {assets}, {PUBLIC_ICONS}")


if __name__ == "__main__":
    main()
