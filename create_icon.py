"""アプリアイコン生成スクリプト (Pillow のみ使用)"""
from pathlib import Path

from PIL import Image, ImageDraw

_DARK = (24, 24, 40)
_BG = (255, 255, 255)


def _finder(d: ImageDraw.ImageDraw, ox: int, oy: int, m: int) -> None:
    """7x7 QR finder pattern"""
    d.rectangle([ox,       oy,       ox + 7*m - 1, oy + 7*m - 1], fill=_DARK)
    d.rectangle([ox + m,   oy + m,   ox + 6*m - 1, oy + 6*m - 1], fill=_BG)
    d.rectangle([ox + 2*m, oy + 2*m, ox + 5*m - 1, oy + 5*m - 1], fill=_DARK)


def _bars(d: ImageDraw.ImageDraw, x0: int, y0: int, y1: int, available: int) -> None:
    """Code128 風の縦バー群"""
    widths = [3, 1, 2, 1, 1, 3, 1, 2, 2, 1, 3, 1, 1, 2]
    unit = max(1, available // sum(widths))
    x = x0
    for i, w in enumerate(widths):
        if i % 2 == 0:
            d.rectangle([x, y0, x + w * unit - 1, y1], fill=_DARK)
        x += w * unit


def make_frame(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = max(2, size // 18)
    mid = size // 2

    d.rounded_rectangle([0, 0, size - 1, size - 1],
                        radius=max(2, size // 10),
                        fill=(*_BG, 255))

    # QR 側（左）
    if size >= 32:
        qr_w = mid - pad
        module = max(1, qr_w // 9)
        _finder(d, pad, pad, module)

        if size >= 48:
            dot = max(1, module - 1)
            gap = max(1, module // 3)
            DATA = [
                [1, 0, 1, 1, 0, 1],
                [0, 1, 1, 0, 1, 0],
                [1, 1, 0, 1, 0, 1],
                [0, 0, 1, 0, 1, 1],
            ]
            dy0 = pad + 7 * module + gap * 2
            for r, row in enumerate(DATA):
                for c, val in enumerate(row):
                    if val:
                        x = pad + c * (dot + gap)
                        y = dy0 + r * (dot + gap)
                        if x + dot < mid - 2 and y + dot < size - pad:
                            d.rectangle([x, y, x + dot - 1, y + dot - 1], fill=_DARK)
    else:
        # 16x16: finder は入らないので外枠＋白内枠
        sq = mid - pad - 1
        d.rectangle([pad, pad, pad + sq - 1, pad + sq - 1], fill=_DARK)
        inner = max(1, sq // 3)
        d.rectangle([pad + inner, pad + inner,
                     pad + sq - inner - 1, pad + sq - inner - 1], fill=_BG)

    # 仕切り線
    d.line([(mid, pad), (mid, size - pad)], fill=(200, 200, 200, 255), width=1)

    # バーコード側（右）
    vpad = max(pad, size // 8)
    _bars(d, mid + pad, pad + vpad, size - pad - vpad, mid - pad * 2)

    return img


def main() -> None:
    out = Path("assets/icon.ico")
    out.parent.mkdir(exist_ok=True)

    # 256x256 を基底に Pillow がリサイズして各サイズを ICO に格納
    base = make_frame(256)
    base.save(str(out), format="ICO", sizes=[(256, 256), (48, 48), (32, 32), (16, 16)])
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
