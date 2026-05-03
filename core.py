import io
import json
from pathlib import Path

from PIL import Image


def load_metadata(path: Path) -> list[dict]:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_metadata(records: list[dict], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


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
