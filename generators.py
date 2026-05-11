import sys
from pathlib import Path

from io import BytesIO

import barcode
import segno
from PIL import Image, ImageDraw, ImageFont
from barcode.writer import ImageWriter

_EC_MAP = {"L": "l", "M": "m", "Q": "q", "H": "h"}

# 誤り訂正レベルごとの最大容量（英数字 / バイナリ）
_EC_CAPACITY: dict[str, tuple[str, str]] = {
    "L": ("4,296文字", "2,953バイト"),
    "M": ("3,391文字", "2,331バイト"),
    "Q": ("2,420文字", "1,663バイト"),
    "H": ("1,852文字", "1,273バイト"),
}

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


def generate_qr(
    text: str,
    filepath: Path,
    error_correction: str = "M",
    encoding: str = "UTF-8",
) -> None:
    ec = _EC_MAP.get(error_correction, "m")
    codec = "cp932" if encoding == "SJIS" else "utf-8"
    try:
        data = text.encode(codec)
    except UnicodeEncodeError:
        raise ValueError(
            "テキストに Shift-JIS で表現できない文字が含まれています。"
            "（絵文字や一部の Unicode 文字は Shift-JIS では使用できません）"
        )
    try:
        # eci=True は Micro QR や ECI 非対応スキャナーで文字化けを引き起こすため不使用。
        # バイト列を make_qr に直接渡してフル QR バイトモードで生成する。
        qr = segno.make_qr(data, error=ec)
    except segno.encoder.DataOverflowError as e:
        alphanum, binary = _EC_CAPACITY.get(error_correction, ("4,296文字", "2,953バイト"))
        enc_label = "SJIS" if encoding == "SJIS" else "UTF-8"
        raise ValueError(
            f"テキストが長すぎてQRコードに収まりません（エンコード: {enc_label}）。\n"
            f"誤り訂正レベル {error_correction}: 英数字 {alphanum} / バイナリ {binary}\n"
            f"入力サイズ: {len(data)} バイト"
        ) from e
    buf = BytesIO()
    qr.save(buf, kind="png", scale=10, border=4)
    buf.seek(0)
    qr_img = Image.open(buf).convert("RGB")

    font = _load_font(14)
    text_h = 24
    canvas = Image.new("RGB", (qr_img.width, qr_img.height + text_h), (255, 255, 255))
    canvas.paste(qr_img, (0, 0))

    # 複数行テキストの場合はラベルを先頭行のみ表示（領域が固定 24px のため）
    label = _truncate_label(text.split("\n")[0])
    draw = ImageDraw.Draw(canvas)
    bbox = draw.textbbox((0, 0), label, font=font)
    x = (canvas.width - (bbox[2] - bbox[0])) // 2
    y = qr_img.height + (text_h - (bbox[3] - bbox[1])) // 2
    draw.text((x, y), label, fill=(0, 0, 0), font=font)

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

            label_src = rec.get("description") or rec["text"].split("\n")[0]
            label = _truncate_label(label_src)
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
