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


_DEFAULT_SETTINGS: dict = {
    "warn_on_duplicate": True,
    "default_type": "QR",
    "qr_error_correction": "M",
    "pdf_cols": 3,
}


def load_settings(path: Path) -> dict:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            stored = json.load(f)
        return {**_DEFAULT_SETTINGS, **stored}
    return dict(_DEFAULT_SETTINGS)


def save_settings(settings: dict, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def _type_label(r: dict) -> str:
    """レコードの種別ラベルを返す。QR は誤り訂正レベルを付加する（例: QR:H）。"""
    if r["type"] == "QR" and r.get("error_correction"):
        return f"QR:{r['error_correction']}"
    return r["type"]


def has_duplicate(
    text: str,
    code_type: str,
    records: list[dict],
    error_correction: str | None = None,
) -> bool:
    """完全一致チェック。QR の場合は error_correction も一致するときのみ True。

    レコードに error_correction フィールドがない旧データは保守的に重複と判定する。
    """
    for r in records:
        if r["text"] != text or r["type"] != code_type:
            continue
        if code_type == "QR" and error_correction is not None and "error_correction" in r:
            if r["error_correction"] != error_correction:
                continue
        return True
    return False


def list_labels(records: list[dict]) -> list[str]:
    return [f"[{_type_label(r)}]  {r['text']}" for r in records]


def list_labels_with_status(records: list[dict]) -> list[str]:
    """ファイルが欠損しているレコードには先頭に ⚠ を付ける。"""
    labels = []
    for r in records:
        prefix = "" if Path(r["path"]).exists() else "⚠"
        desc = r.get("description", "")
        default = r["text"].split("\n")[0]
        desc_suffix = f"  ({desc})" if desc and desc != default else ""
        labels.append(f"{prefix}[{_type_label(r)}]  {r['text']}{desc_suffix}")
    return labels


def find_index(label: str, records: list[dict]) -> int:
    for i, r in enumerate(records):
        if f"[{_type_label(r)}]  {r['text']}" == label:
            return i
    return -1


def calc_preview_size(win_w: int, win_h: int, left_panel_w: int) -> tuple[int, int]:
    """ウィンドウサイズからプレビュー領域のピクセルサイズを計算する。

    返す幅は常に左パネルと余白の合計を超えない (ウィンドウが増大しない) ことを保証する。
    """
    pw = max(200, win_w - left_panel_w - 20)
    ph = max(200, win_h - 80)
    return pw, ph
