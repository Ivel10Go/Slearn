"""Erzeugt das App-Symbol: ein schwarzes, leicht gerundetes Quadrat mit "L".

    python scripts/make-icons.py

Ergebnis: assets/icon.png (1024 px), assets/icon.ico (Windows) und
assets/icon.icns (macOS). Die beiden letzten erzeugen wir selbst: das
Icon-Werkzeug von electron-builder bricht unter Windows mit
"WebAssembly.Memory(): could not allocate memory" ab. Die Form entspricht
`BrandMark` in frontend/components/app-shell.tsx.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 1024
ROOT = Path(__file__).resolve().parents[1]


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        ROOT.parent / "frontend/node_modules/geist/dist/fonts/geist-sans/Geist-SemiBold.ttf",
        Path("C:/Windows/Fonts/segoeuisb.ttf"),
        Path("/System/Library/Fonts/SFNS.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default(size)


def main() -> None:
    # macOS-Symbole haben einen Rand von etwa 10 % - ohne ihn wirkt das Symbol
    # im Dock groesser als alle anderen.
    margin = 100
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((margin, margin, SIZE - margin, SIZE - margin), radius=190, fill=(10, 10, 10, 255))
    letter = font(560)
    box = draw.textbbox((0, 0), "L", font=letter)
    width, height = box[2] - box[0], box[3] - box[1]
    draw.text(((SIZE - width) / 2 - box[0], (SIZE - height) / 2 - box[1]), "L", font=letter, fill=(250, 250, 250, 255))
    assets = ROOT / "assets"
    image.save(assets / "icon.png")
    image.save(assets / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    image.save(assets / "icon.icns")
    print(f"Symbole geschrieben: {assets}")


if __name__ == "__main__":
    main()
