"""
Process the Fellow owl logo image into assets/logo.png, assets/icon.png, and assets/icon.ico.
"""
import os
from PIL import Image, ImageDraw


def create_fellow_icons():
    os.makedirs("assets", exist_ok=True)
    source_jpg = os.path.join("assets", "logo_source.jpg")

    if not os.path.exists(source_jpg):
        raise FileNotFoundError(f"Logo source image not found at {source_jpg}")

    img = Image.open(source_jpg).convert("RGBA")

    # Center-crop to square if not already square
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    square = img.crop((left, top, left + side, top + side))

    # 1. Save full-bleed high-res logo.png (512x512) for UI badges & avatars
    logo_512 = square.resize((512, 512), Image.Resampling.LANCZOS)
    logo_path = os.path.join("assets", "logo.png")
    logo_512.save(logo_path, format="PNG")

    # 2. Save rounded-corner icon.png (256x256) and multi-size Windows icon.ico
    icon_size = 256
    icon_img = square.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (icon_size, icon_size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, icon_size, icon_size], radius=48, fill=255)

    rounded_icon = Image.new("RGBA", (icon_size, icon_size), (0, 0, 0, 0))
    rounded_icon.paste(icon_img, (0, 0), mask=mask)

    png_path = os.path.join("assets", "icon.png")
    ico_path = os.path.join("assets", "icon.ico")

    rounded_icon.save(png_path, format="PNG")
    rounded_icon.save(
        ico_path,
        format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
    print(f"Generated {logo_path}, {png_path}, and {ico_path} from uploaded Fellow owl logo.")


if __name__ == "__main__":
    create_fellow_icons()
