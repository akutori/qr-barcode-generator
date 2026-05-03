import sys
from pathlib import Path

import barcode
import qrcode
from PIL import Image, ImageDraw, ImageFont
from barcode.writer import ImageWriter

_FONT_CANDIDATES: list[str] = []
if sys.platform == "win32":
    _FONT_CANDIDATES = [
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/msgothic.ttc",
        "C:/Windows/Fonts/YuGothM.ttc",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibri.ttf",
    ]
elif sys.platform == "darwin":
    _FONT_CANDIDATES = [
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
else:
    _FONT_CANDIDATES = [
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]


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


def generate_qr(text: str, filepath: Path) -> None:
    try:
        qr = qrcode.make(text).convert("RGB")
    except Exception as e:
        if "version" in str(e).lower():
            raise ValueError(
                f"テキストが長すぎてQRコードに収まりません。\n"
                f"上限の目安: 英数字 4,296文字 / バイナリ 2,953バイト\n"
                f"入力サイズ: {len(text.encode())} バイト"
            ) from e
        raise

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
    if not text.isascii():
        raise ValueError("バーコード (Code128) は ASCII 文字のみ対応しています。日本語・絵文字は使用できません。")
    code = barcode.get("code128", text, writer=ImageWriter())
    saved = code.save(str(base_path))
    return Path(saved)


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
