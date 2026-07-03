import json
import re
from pathlib import Path


def load_metadata(path: Path) -> list[dict]:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_metadata(records: list[dict], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


_DEFAULT_SETTINGS: dict = {
    "warn_on_duplicate": True,
    "default_type": "Q",
    "qr_error_correction": "M",
    "qr_encoding": "UTF-8",
    "pdf_cols": 3,
    "sort_order": "date_new",
    "left_panel_w": 310,
}


def clamp_panel_width(w: int, min_w: int = 220, max_w: int = 600) -> int:
    """左パネル幅を妥当な範囲にクランプする（settings.json の手動編集・破損への防御）。"""
    return max(min_w, min(w, max_w))

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
    "custom":    "カスタム順",
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


_TYPE_DISPLAY = {"Q": "QR", "B": "Barcode"}


def type_label(r: dict) -> str:
    """レコードの種別ラベルを返す。Q は誤り訂正レベルとエンコードを付加する（例: QR:H:SJIS）。"""
    disp = _TYPE_DISPLAY.get(r["type"], r["type"])
    if r["type"] == "Q":
        ec = r.get("error_correction", "")
        suffix = f":{ec}" if ec else ""
        if r.get("encoding") == "SJIS":
            suffix += ":SJIS"
        return f"{disp}{suffix}"
    return disp


def has_duplicate(
    text: str,
    code_type: str,
    records: list[dict],
    error_correction: str | None = None,
    encoding: str | None = None,
) -> bool:
    """完全一致チェック。QR の場合は error_correction と encoding も一致するときのみ True。

    - error_correction フィールドのない旧レコードは保守的に重複と判定する。
    - encoding フィールドのない旧レコードは UTF-8 として扱う。
    """
    for r in records:
        if r["text"] != text or r["type"] != code_type:
            continue
        if code_type == "Q":
            if error_correction is not None and "error_correction" in r:
                if r["error_correction"] != error_correction:
                    continue
            if encoding is not None:
                if r.get("encoding", "UTF-8") != encoding:
                    continue
        return True
    return False


def _display_text(r: dict) -> str:
    """一覧に表示するテキストを返す。説明があれば説明、なければ先頭行。"""
    return r.get("description") or r["text"].split("\n")[0]


_FILENAME_INVALID_CHARS = re.compile(r'[\\/:*?"<>|]')
_MAX_FILENAME_LEN = 50


def suggested_filename(r: dict) -> str:
    """「名前を付けて保存」ダイアログのデフォルトファイル名を返す。

    _display_text と同じ内容（説明があれば説明、なければテキスト先頭行）を使い、
    Windows のファイル名に使えない文字は _ に置換する。拡張子は r["path"] のものを維持する。
    """
    base = _FILENAME_INVALID_CHARS.sub("_", _display_text(r)).strip()
    base = base[:_MAX_FILENAME_LEN].strip()
    if not base:
        base = "image"
    ext = Path(r["path"]).suffix or ".png"
    return base + ext


def _item_label(r: dict) -> str:
    """レコード 1 件分の一覧ラベルを生成する（ファイル有無チェックなし）。
    説明が設定されている場合は ✎ プレフィックスを付ける。
    """
    prefix = "✎" if r.get("description") else ""
    return f"{prefix}[{type_label(r)}]  {_display_text(r)}"


def list_labels(records: list[dict]) -> list[str]:
    return [_item_label(r) for r in records]


def list_labels_with_status(records: list[dict], save_dir: Path) -> list[str]:
    """ファイルが欠損しているレコードには先頭に ⚠ を付ける。

    r["path"] はファイル名のみを保持するため、save_dir と結合して存在確認する。
    """
    labels = []
    for r in records:
        file_prefix = "" if (Path(save_dir) / r["path"]).exists() else "⚠"
        labels.append(f"{file_prefix}{_item_label(r)}")
    return labels


def find_index(label: str, records: list[dict]) -> int:
    for i, r in enumerate(records):
        base = _item_label(r)
        if base == label or f"⚠{base}" == label:
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

    if sort_key == "custom":
        return sorted(indices, key=lambda i: records[i].get("order", i))

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


def move_index(indices: list[int], from_pos: int, to_pos: int) -> list[int]:
    """indices 内の要素を from_pos から to_pos の位置へ移動した新しいリストを返す。

    元の indices は変更しない。from_pos が範囲外なら現状維持で返す。
    to_pos は 0..len(indices)-1 にクランプする。
    """
    if from_pos == to_pos:
        return list(indices)
    if not (0 <= from_pos < len(indices)):
        return list(indices)

    result = list(indices)
    item = result.pop(from_pos)
    to_pos = max(0, min(to_pos, len(result)))
    result.insert(to_pos, item)
    return result


def apply_custom_order(records: list[dict], ordered_indices: list[int]) -> None:
    """ordered_indices の並び順通りに records[i]["order"] を 0 から振り直す（破壊的更新）。

    ordered_indices に含まれない records の order は変更しない。
    """
    for pos, idx in enumerate(ordered_indices):
        records[idx]["order"] = pos
