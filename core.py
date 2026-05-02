import io
import json
import sys
from pathlib import Path

import barcode
import qrcode
from PIL import Image, ImageDraw, ImageFont
from barcode.writer import ImageWriter

_FONT_CANDIDATES: list[str] = []
if sys.platform == "win32":
    _FONT_CANDIDATES = ["C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/calibri.ttf"]
elif sys.platform == "darwin":
    _FONT_CANDIDATES = ["/System/Library/Fonts/Helvetica.ttc"]
else:
    _FONT_CANDIDATES = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


def _load_font(size: int = 14) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def load_metadata(path: Path) -> list[dict]:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_metadata(records: list[dict], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def generate_qr(text: str, filepath: Path) -> None:
    qr = qrcode.make(text).convert("RGB")

    font = _load_font(14)
    text_h = 24
    canvas = Image.new("RGB", (qr.width, qr.height + text_h), (255, 255, 255))
    canvas.paste(qr, (0, 0))

    draw = ImageDraw.Draw(canvas)
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (canvas.width - (bbox[2] - bbox[0])) // 2
    y = qr.height + (text_h - (bbox[3] - bbox[1])) // 2
    draw.text((x, y), text, fill=(0, 0, 0), font=font)

    canvas.save(str(filepath))


def generate_barcode_file(text: str, base_path: Path) -> Path:
    code = barcode.get("code128", text, writer=ImageWriter())
    saved = code.save(str(base_path))
    return Path(saved)


def img_to_bytes(img: Image.Image) -> bytes:
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    return bio.getvalue()


def blank_image(w: int, h: int) -> bytes:
    return img_to_bytes(Image.new("RGB", (w, h), color=(230, 230, 230)))


def fit_image(path: str, w: int, h: int) -> bytes:
    """画像を (w, h) の白背景に中央配置してリサイズする"""
    img = Image.open(path).convert("RGB")
    img.thumbnail((w, h), Image.LANCZOS)
    bg = Image.new("RGB", (w, h), (255, 255, 255))
    bg.paste(img, ((w - img.width) // 2, (h - img.height) // 2))
    return img_to_bytes(bg)


def list_labels(records: list[dict]) -> list[str]:
    return [f"[{r['type']}]  {r['text']}" for r in records]


def find_index(label: str, records: list[dict]) -> int:
    for i, r in enumerate(records):
        if f"[{r['type']}]  {r['text']}" == label:
            return i
    return -1


def calc_preview_size(win_w: int, win_h: int, left_panel_w: int) -> tuple[int, int]:
    """ウィンドウサイズからプレビュー領域のピクセルサイズを計算する。

    返す幅は常に左パネルと余白の合計を超えない (ウィンドウが増大しない) ことを保証する。
    """
    pw = max(200, win_w - left_panel_w - 20)
    ph = max(200, win_h - 80)
    return pw, ph
