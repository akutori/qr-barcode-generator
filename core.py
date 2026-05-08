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
    "sort_order": "date_new",
}

SORT_OPTION_LABELS: dict[str, str] = {
    "date_new":  "追加日 新しい順",
    "date_old":  "追加日 古い順",
    "label_az":  "表示名 A→Z",
    "label_za":  "表示名 Z→A",
    "text_az":   "テキスト A→Z",
    "text_za":   "テキスト Z→A",
    "desc_az":   "説明 A→Z",
    "desc_za":   "説明 Z→A",
    "type_qr":   "種別 QR先",
    "type_bc":   "種別 Barcode先",
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


def _display_text(r: dict) -> str:
    """一覧に表示するテキストを返す。説明があれば説明、なければ先頭行。"""
    return r.get("description") or r["text"].split("\n")[0]


def _item_label(r: dict) -> str:
    """レコード 1 件分の一覧ラベルを生成する（ファイル有無チェックなし）。
    説明が設定されている場合は ✎ プレフィックスを付ける。
    """
    prefix = "✎" if r.get("description") else ""
    return f"{prefix}[{_type_label(r)}]  {_display_text(r)}"


def list_labels(records: list[dict]) -> list[str]:
    return [_item_label(r) for r in records]


def list_labels_with_status(records: list[dict]) -> list[str]:
    """ファイルが欠損しているレコードには先頭に ⚠ を付ける。"""
    labels = []
    for r in records:
        file_prefix = "" if Path(r["path"]).exists() else "⚠"
        labels.append(f"{file_prefix}{_item_label(r)}")
    return labels


def find_index(label: str, records: list[dict]) -> int:
    for i, r in enumerate(records):
        if _item_label(r) == label or f"⚠{_item_label(r)}" == label:
            return i
    return -1


def calc_preview_size(win_w: int, win_h: int, left_panel_w: int) -> tuple[int, int]:
    """ウィンドウサイズからプレビュー領域のピクセルサイズを計算する。

    返す幅は常に左パネルと余白の合計を超えない (ウィンドウが増大しない) ことを保証する。
    """
    pw = max(200, win_w - left_panel_w - 20)
    ph = max(200, win_h - 80)
    return pw, ph


def sort_records(records: list[dict], indices: list[int], sort_key: str) -> list[int]:
    """indices を sort_key（SORT_OPTION_LABELS のキー）に従って並べ替えて返す。
    不明な sort_key は "date_new" と同じ扱い。
    """
    if not indices:
        return []

    if sort_key == "date_old":
        return list(indices)

    if sort_key == "label_az":
        return sorted(indices, key=lambda i: _item_label(records[i]).lower())

    if sort_key == "label_za":
        return sorted(indices, key=lambda i: _item_label(records[i]).lower(), reverse=True)

    if sort_key == "text_az":
        return sorted(indices, key=lambda i: records[i]["text"].lower())

    if sort_key == "text_za":
        return sorted(indices, key=lambda i: records[i]["text"].lower(), reverse=True)

    if sort_key == "desc_az":
        nonempty = sorted(
            [i for i in indices if records[i].get("description")],
            key=lambda i: records[i].get("description", "").lower(),
        )
        empty = [i for i in indices if not records[i].get("description")]
        return nonempty + empty

    if sort_key == "desc_za":
        empty = [i for i in indices if not records[i].get("description")]
        nonempty = sorted(
            [i for i in indices if records[i].get("description")],
            key=lambda i: records[i].get("description", "").lower(),
            reverse=True,
        )
        return empty + nonempty

    if sort_key == "type_qr":
        return sorted(indices, key=lambda i: records[i]["type"], reverse=True)

    if sort_key == "type_bc":
        return sorted(indices, key=lambda i: records[i]["type"])

    # デフォルト: "date_new"
    return list(reversed(indices))
