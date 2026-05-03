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


_MAX_LABEL_LEN = 25


def _truncate_label(text: str) -> str:
    return text[:_MAX_LABEL_LEN] + "…" if len(text) > _MAX_LABEL_LEN else text


def generate_pdf_grid(records: list[dict], output_path: Path, cols: int = 3) -> None:
    PAGE_W, PAGE_H = 1240, 1754  # A4 @ 150 DPI
    MARGIN = 60
    GAP = 30
    CELL_IMG_H = 280
    LABEL_H = 32
    CELL_H = CELL_IMG_H + 8 + LABEL_H

    cell_w = (PAGE_W - 2 * MARGIN - (cols - 1) * GAP) // cols
    rows_per_page = (PAGE_H - 2 * MARGIN + GAP) // (CELL_H + GAP)
    per_page = cols * rows_per_page

    font = _load_font(24)
    pages: list[Image.Image] = []

    for page_start in range(0, len(records), per_page):
        page_recs = records[page_start: page_start + per_page]
        canvas = Image.new("RGB", (PAGE_W, PAGE_H), (255, 255, 255))
        draw = ImageDraw.Draw(canvas)

        for i, rec in enumerate(page_recs):
            col = i % cols
            row = i // cols
            x = MARGIN + col * (cell_w + GAP)
            y = MARGIN + row * (CELL_H + GAP)

            try:
                img = Image.open(rec["path"]).convert("RGB")
                img.thumbnail((cell_w, CELL_IMG_H), Image.LANCZOS)
                ix = x + (cell_w - img.width) // 2
                iy = y + (CELL_IMG_H - img.height) // 2
                canvas.paste(img, (ix, iy))
            except Exception:
                pass

            label = _truncate_label(rec["text"])
            bbox = draw.textbbox((0, 0), label, font=font)
            lw = bbox[2] - bbox[0]
            lx = x + (cell_w - min(lw, cell_w)) // 2
            ly = y + CELL_IMG_H + 8
            draw.text((lx, ly), label, fill=(0, 0, 0), font=font)

        pages.append(canvas)

    if pages:
        pages[0].save(
            str(output_path),
            save_all=True,
            append_images=pages[1:],
            format="PDF",
            resolution=150,
        )


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
