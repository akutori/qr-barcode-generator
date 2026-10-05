import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PureWindowsPath


def load_metadata(path: Path) -> list[dict]:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _write_json_atomic(data, path: Path) -> None:
    """一時ファイルに書き切ってから置換する。書き込み途中の失敗で既存ファイルを壊さないため。"""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def save_metadata(records: list[dict], path: Path) -> None:
    _write_json_atomic(records, path)


_DEFAULT_SETTINGS: dict = {
    "warn_on_duplicate": True,
    "default_type": "Q",
    "qr_error_correction": "M",
    "qr_encoding": "UTF-8",
    "pdf_cols": 3,
    "sort_order": "date_new",
    "left_panel_w": 310,
    "open_folders": [],
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
    stored: dict = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            stored = json.load(f)
    settings = {**_DEFAULT_SETTINGS, **stored}
    # 手動編集された値がパスとして使われても generated 外へ出ないよう、読み込み時に検証する
    settings["open_folders"] = sanitize_open_folders(settings.get("open_folders"))
    return settings


def save_settings(settings: dict, path: Path) -> None:
    _write_json_atomic(settings, path)


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


def _is_same_code(
    r: dict,
    text: str,
    code_type: str,
    error_correction: str | None,
    encoding: str | None,
) -> bool:
    if r["text"] != text or r["type"] != code_type:
        return False
    if code_type == "Q":
        if error_correction is not None and "error_correction" in r:
            if r["error_correction"] != error_correction:
                return False
        if encoding is not None:
            if r.get("encoding", "UTF-8") != encoding:
                return False
    return True


def find_duplicate_indices(
    text: str,
    code_type: str,
    records: list[dict],
    error_correction: str | None = None,
    encoding: str | None = None,
) -> list[int]:
    """has_duplicate と同じ判定で、一致するレコードの位置をすべて返す。"""
    return [
        i for i, r in enumerate(records)
        if _is_same_code(r, text, code_type, error_correction, encoding)
    ]


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
    return any(
        _is_same_code(r, text, code_type, error_correction, encoding) for r in records
    )


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
    """ファイルが欠損している（または save_dir 外を指す不正な path の）レコードには先頭に ⚠ を付ける。

    r["path"] は save_dir からの相対パス（階層名/ファイル名 またはファイル名のみ）を保持する。
    """
    labels = []
    folder_ok: dict[str, bool] = {}
    for r in records:
        path = record_file_path(save_dir, r, folder_ok)
        file_prefix = "" if path is not None and path.exists() else "⚠"
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


# ---------------------------------------------------------------------------
# 階層（generated 直下 1 階層のフォルダ）
# ---------------------------------------------------------------------------

_MAX_FOLDER_NAME_LEN = _MAX_FILENAME_LEN
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_RESERVED_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"COM{i}" for i in [*range(1, 10), "¹", "²", "³"]}
    | {f"LPT{i}" for i in [*range(1, 10), "¹", "²", "³"]}
)
# generated 直下に置かれるアプリ管理ファイルと衝突する名前は階層名にできない
_RESERVED_FILE_NAMES = frozenset({
    "metadata.json", "metadata.json.bak", "metadata.json.tmp",
    "settings.json", "settings.json.tmp",
})


class FolderExistsError(Exception):
    """作成しようとした階層が（大文字小文字違いを含め）既に存在するときに送出する。"""


class MoveCollisionError(Exception):
    """移動先に同一コードがあるのに上書きが指定されていないときに送出する。"""

    def __init__(self, collisions: list[tuple[int, int]]) -> None:
        super().__init__("移動先に同一のコードが存在します。")
        self.collisions = collisions


@dataclass(frozen=True)
class MoveResult:
    moved: int
    overwritten: int


def _is_reserved_device_name(name: str) -> bool:
    # Windows は "CON.txt" のように拡張子付きでも予約デバイスとして扱う
    return name.split(".")[0].strip().upper() in _RESERVED_DEVICE_NAMES


def validate_folder_name(name: str) -> str:
    """階層名を検証し、前後の空白を除いた名前を返す。不正なら ValueError。"""
    if not isinstance(name, str):
        raise ValueError("階層名が不正です。")
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("階層名を入力してください。")
    if len(cleaned) > _MAX_FOLDER_NAME_LEN:
        raise ValueError(f"階層名は {_MAX_FOLDER_NAME_LEN} 文字以内で入力してください。")
    if _FILENAME_INVALID_CHARS.search(cleaned) or _CONTROL_CHARS.search(cleaned):
        raise ValueError('階層名に使用できない文字が含まれています。（\\ / : * ? " < > | と制御文字）')
    if cleaned.endswith("."):
        raise ValueError("階層名の末尾にドットは使用できません。")
    if _is_reserved_device_name(cleaned):
        raise ValueError("Windows の予約名は階層名に使用できません。")
    if cleaned.lower() in _RESERVED_FILE_NAMES:
        raise ValueError("アプリが使用するファイルと同じ名前は階層名に使用できません。")
    return cleaned


def same_folder_name(a: str, b: str) -> bool:
    """Windows のファイルシステムに合わせ、大文字小文字を区別せず比較する。"""
    return a.lower() == b.lower()


def build_record_path(folder: str, filename: str) -> str:
    """レコードの path（generated からの相対パス）を組み立てる。folder が空ならルート。"""
    return f"{folder}/{filename}" if folder else filename


def record_folder(rec: dict) -> str:
    """レコードが属する階層名を返す。ルート（または階層として解釈できないパス）は空文字。"""
    parts = str(rec.get("path", "")).replace("\\", "/").split("/")
    if len(parts) == 2 and _is_valid_folder_name(parts[0]):
        return parts[0]
    return ""


def safe_record_path(
    save_dir: Path, rel_path: str, _folder_ok: dict[str, bool] | None = None
) -> Path:
    """レコードの path を save_dir 配下の保存先に解決する。

    metadata.json は手動編集されうるため、読み書き・削除に使う前に必ずこの関数を通し、
    save_dir の外や 2 階層以上のパスは ValueError にする。

    シンボリックリンク・ジャンクションの階層で save_dir の外へ書き込み・削除されるのを防ぐため、
    階層ディレクトリの実体を確認する（ファイル自体がリンクでも削除・移動の対象はリンク自身なので確認しない）。
    一覧表示のように大量のレコードを続けて検査する呼び出し元は、_folder_ok に同じ dict を渡すと
    階層ごとの実体確認を 1 回にできる。
    """
    if not isinstance(rel_path, str) or not rel_path:
        raise ValueError("パスが空です。")
    if "\x00" in rel_path:
        raise ValueError("パスに不正な文字が含まれています。")
    normalized = rel_path.replace("\\", "/")
    windows = PureWindowsPath(rel_path)
    if windows.drive or windows.root or normalized.startswith("/"):
        raise ValueError("絶対パスは使用できません。")
    parts = normalized.split("/")
    if len(parts) > 2 or any(p in ("", ".", "..") for p in parts):
        raise ValueError("パスは「階層名/ファイル名」または「ファイル名」の形式で指定してください。")
    if len(parts) == 2 and validate_folder_name(parts[0]) != parts[0]:
        raise ValueError("階層名が不正です。")
    filename = parts[-1]
    if (
        _FILENAME_INVALID_CHARS.search(filename)
        or _CONTROL_CHARS.search(filename)
        or filename.endswith((" ", "."))
        or _is_reserved_device_name(filename)
        or filename.lower() in _RESERVED_FILE_NAMES
    ):
        raise ValueError("ファイル名が不正です。")
    base = Path(save_dir)
    if len(parts) == 2:
        key = parts[0].lower()
        inside = _folder_ok.get(key) if _folder_ok is not None else None
        if inside is None:
            inside = (base / parts[0]).resolve().is_relative_to(base.resolve())
            if _folder_ok is not None:
                _folder_ok[key] = inside
        if not inside:
            raise ValueError("保存先フォルダの外を指すパスは使用できません。")
    return base.joinpath(*parts)


def resolve_folder_dir(save_dir: Path, folder: str) -> Path:
    """階層名から保存先ディレクトリを返す（空文字は save_dir 自身）。

    階層の実体がシンボリックリンク等で save_dir の外を指すときは、そこへ書き込まないよう ValueError。
    """
    base = Path(save_dir)
    if folder == "":
        return base
    if validate_folder_name(folder) != folder:
        raise ValueError("階層名が不正です。")
    target = base / folder
    if not target.resolve().is_relative_to(base.resolve()):
        raise ValueError("保存先フォルダの外を指す階層は使用できません。")
    return target


def record_file_path(
    save_dir: Path, rec: dict, _folder_ok: dict[str, bool] | None = None
) -> Path | None:
    """レコードの画像ファイルのパスを返す。path が不正なときは None。"""
    try:
        return safe_record_path(save_dir, rec.get("path", ""), _folder_ok)
    except ValueError:
        return None


@lru_cache(maxsize=2048)
def _is_valid_folder_name(name: str) -> bool:
    # record_folder が全レコードに対して呼ぶため、同じ名前の検証結果は使い回す
    try:
        return validate_folder_name(name) == name
    except ValueError:
        return False


def list_folders(save_dir: Path, records: list[dict]) -> list[str]:
    """階層名の一覧を返す。generated 直下のディレクトリと、レコードが参照する階層の和集合。

    metadata.json を正としつつ、中身が空の階層も一覧に出すためにディスクも走査する。
    大文字小文字違いは同一視し、ディスク上の名前を優先する。
    """
    found: dict[str, str] = {}
    try:
        with os.scandir(save_dir) as it:
            entries = sorted(it, key=lambda e: e.name.lower())
            for entry in entries:
                if entry.is_dir(follow_symlinks=False) and _is_valid_folder_name(entry.name):
                    found.setdefault(entry.name.lower(), entry.name)
    except OSError:
        pass
    for r in records:
        folder = record_folder(r)
        if folder and _is_valid_folder_name(folder):
            found.setdefault(folder.lower(), folder)
    return sorted(found.values(), key=str.lower)


def group_by_folder(
    records: list[dict], indices: list[int], folders: list[str]
) -> dict[str, list[int]]:
    """indices を階層ごとに振り分ける。キー "" がルート。folders はすべてキーに含める。"""
    canonical = {f.lower(): f for f in folders}
    groups: dict[str, list[int]] = {"": []}
    for f in folders:
        groups[f] = []
    for i in indices:
        folder = record_folder(records[i])
        groups[canonical.get(folder.lower(), "") if folder else ""].append(i)
    return groups


def find_move_collisions(
    records: list[dict], indices: list[int], dest_folder: str
) -> list[tuple[int, int]]:
    """移動先に同一コード（重複判定は has_duplicate と同じ）がある組を (移動元, 移動先) の位置で返す。"""
    # 大量移動でも総当たりにならないよう、移動先のレコードを (text, type) で索引化する
    dest_by_key: dict[tuple[str, str], list[int]] = {}
    for i, r in enumerate(records):
        if same_folder_name(record_folder(r), dest_folder):
            dest_by_key.setdefault((r["text"], r["type"]), []).append(i)
    collisions: list[tuple[int, int]] = []
    for src in indices:
        r = records[src]
        # すでに移動先にあるレコードは動かないので、移動元ではなく衝突相手として扱う
        if same_folder_name(record_folder(r), dest_folder):
            continue
        is_qr = r["type"] == "Q"
        ec = r.get("error_correction") if is_qr else None
        enc = r.get("encoding", "UTF-8") if is_qr else None
        for j in dest_by_key.get((r["text"], r["type"]), []):
            if _is_same_code(records[j], r["text"], r["type"], ec, enc):
                collisions.append((src, j))
    return collisions


def create_folder(save_dir: Path, name: str, records: list[dict]) -> str:
    """階層（ディレクトリ）を作成して正規化済みの名前を返す。

    不正な名前は ValueError、既存の階層と（大文字小文字違いを含め）同名なら FolderExistsError。
    """
    validated = validate_folder_name(name)
    if any(same_folder_name(validated, f) for f in list_folders(save_dir, records)):
        raise FolderExistsError(validated)
    target = Path(save_dir) / validated
    try:
        target.mkdir()
    except FileExistsError:
        if target.is_dir():
            raise FolderExistsError(validated) from None
        raise ValueError("同名のファイルが存在するため作成できません。") from None
    return validated


def sanitize_open_folders(value) -> list[str]:
    """settings.json の展開状態を検証し、不正な階層名・重複を取り除いたリストを返す。"""
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not _is_valid_folder_name(item):
            continue
        if item.lower() in seen:
            continue
        seen.add(item.lower())
        result.append(item)
    return result


def _unique_filename(name: str, taken: set[str]) -> str:
    if name.lower() not in taken:
        return name
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 1
    while f"{stem}_{n}{suffix}".lower() in taken:
        n += 1
    return f"{stem}_{n}{suffix}"


def _resolve_dest_folder(dest_folder: str, save_dir: Path, records: list[dict]) -> str:
    if dest_folder == "":
        return ""
    validated = validate_folder_name(dest_folder)
    for existing in list_folders(save_dir, records):
        if same_folder_name(existing, validated):
            return existing
    return validated


def _next_order(records: list[dict], dest: str, excluded: set[int]) -> int:
    """カスタム順で移動先の末尾に並ぶ order 値。ソートの既定値（位置）と同じ基準で求める。"""
    orders = [
        o for i, r in enumerate(records)
        if id(r) not in excluded and same_folder_name(record_folder(r), dest)
        for o in [r.get("order", i)]
        if isinstance(o, int)
    ]
    return max(orders, default=-1) + 1


def _rollback_renames(done: list[tuple[Path, Path]]) -> list[tuple[Path, Path]]:
    """移動済みファイルを元の場所へ戻す。戻せなかった (元, 現在) の組を返す。"""
    stuck: list[tuple[Path, Path]] = []
    for src, dst in reversed(done):
        try:
            os.rename(dst, src)
        except OSError:
            stuck.append((src, dst))
    return stuck


def _undo_failed_move(
    plan: list[tuple[dict, Path, Path, str]],
    done: list[tuple[Path, Path]],
    records: list[dict],
    metadata_path: Path,
) -> list[Path]:
    """失敗した移動を取り消す。戻せなかったファイルは、レコードを実際の場所に合わせて整合させる。"""
    stuck = _rollback_renames(done)
    if stuck:
        stuck_dst = {dst for _src, dst in stuck}
        for rec, _src, dst, new_rel in plan:
            if dst in stuck_dst:
                rec["path"] = new_rel
        try:
            save_metadata(records, metadata_path)
        except OSError:
            pass
    return [dst for _src, dst in stuck]


def move_records(
    records: list[dict],
    indices: list[int],
    dest_folder: str,
    save_dir: Path,
    metadata_path: Path,
    overwrite: bool = False,
) -> MoveResult:
    """records[indices] の画像ファイルを dest_folder（空文字ならルート）へ移動する。

    - 移動先に同一コードがあり overwrite=False なら MoveCollisionError（何も変更しない）。
    - overwrite=True なら、移動先の同一コードのレコードとファイルを削除する。
      置き換える画像が無いのに既存を消さないよう、移動元の画像が欠損しているときは ValueError。
      取り消せない削除は、移動とメタデータ保存が成功した後の最後に行う。
    - 途中で失敗した場合は、移動済みファイルと path を元に戻して例外を再送出する。
      ファイルを戻せなかったときは、レコードを実際の場所に合わせたうえで OSError にする。
    - records は呼び出し元のリストを直接更新する（レコードの辞書は置き換えない）。
    """
    save_dir = Path(save_dir)
    dest = _resolve_dest_folder(dest_folder, save_dir, records)
    dest_dir = resolve_folder_dir(save_dir, dest)
    unique = list(dict.fromkeys(indices))
    if any(not 0 <= i < len(records) for i in unique):
        raise ValueError("移動対象が不正です。")
    moving = [i for i in unique if not same_folder_name(record_folder(records[i]), dest)]
    if not moving:
        return MoveResult(0, 0)

    collisions = find_move_collisions(records, moving, dest)
    if collisions and not overwrite:
        raise MoveCollisionError(collisions)

    sources = {i: safe_record_path(save_dir, records[i].get("path", "")) for i in moving}
    if any(not sources[src].is_file() for src, _ in collisions):
        raise ValueError("移動元の画像ファイルが見つからないため、上書きできません。")
    removed = [records[j] for j in dict.fromkeys(j for _, j in collisions)]
    removed_ids = {id(r) for r in removed}

    # 移動先のレコードが指す画像名（実体が欠損していても）を使用済みとして扱い、移動したファイルと
    # 同じ path を指す 2 レコードができて、片方の削除でもう片方の画像まで消えるのを避ける
    taken = {p.name.lower() for p in dest_dir.iterdir()} if dest_dir.is_dir() else set()
    taken |= {
        str(r.get("path", "")).replace("\\", "/").rsplit("/", 1)[-1].lower()
        for r in records if same_folder_name(record_folder(r), dest)
    }
    plan: list[tuple[dict, Path, Path, str]] = []
    for i in moving:
        src = sources[i]
        name = _unique_filename(src.name, taken)
        taken.add(name.lower())
        plan.append((records[i], src, dest_dir / name, build_record_path(dest, name)))

    created_dir = bool(dest) and not dest_dir.exists()
    if dest:
        dest_dir.mkdir(exist_ok=True)

    def _undo_dir() -> None:
        if created_dir:
            try:
                dest_dir.rmdir()
            except OSError:
                pass

    def _abort(exc: BaseException, done: list[tuple[Path, Path]]) -> None:
        stuck = _undo_failed_move(plan, done, records, metadata_path)
        _undo_dir()
        if stuck and isinstance(exc, Exception):
            names = ", ".join(str(p.relative_to(save_dir)) for p in stuck)
            raise OSError(f"移動に失敗し、一部のファイルを元の場所へ戻せませんでした: {names}") from exc

    done: list[tuple[Path, Path]] = []
    try:
        for _rec, src, dst, _rel in plan:
            if src.is_file():
                os.rename(src, dst)
                done.append((src, dst))
    except BaseException as exc:
        _abort(exc, done)
        raise

    originals = [(rec, rec["path"], rec.get("order")) for rec, *_ in plan]
    next_order = _next_order(records, dest, removed_ids | {id(rec) for rec, *_ in plan})
    for offset, (rec, _src, _dst, new_rel) in enumerate(plan):
        rec["path"] = new_rel
        rec["order"] = next_order + offset

    kept = [r for r in records if id(r) not in removed_ids]
    try:
        save_metadata(kept, metadata_path)
    except BaseException as exc:
        for rec, old_path, old_order in originals:
            rec["path"] = old_path
            if old_order is None:
                rec.pop("order", None)
            else:
                rec["order"] = old_order
        _abort(exc, done)
        raise
    records[:] = kept

    moved_files = {dst for _rec, _src, dst, _rel in plan}
    for rec in removed:
        path = record_file_path(save_dir, rec)
        if path is not None and path not in moved_files:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    return MoveResult(len(plan), len(removed))


# ---------------------------------------------------------------------------
# 階層の名前変更・削除
# ---------------------------------------------------------------------------

class FolderNotEmptyError(Exception):
    """中身のある階層を、中身の扱いを指定せずに削除しようとしたときに送出する。"""

    def __init__(self, count: int) -> None:
        super().__init__(f"階層に {count} 件のコードがあります。")
        self.count = count


class UnmanagedFilesError(Exception):
    """階層に一覧へ表示されないファイル・フォルダがあり、削除を中止するときに送出する。"""

    def __init__(self, names: list[str]) -> None:
        super().__init__("階層に一覧へ表示されないファイルまたはフォルダがあります。")
        self.names = names


@dataclass(frozen=True)
class DeleteFolderResult:
    moved: int
    deleted: int


def _canonical_folder(save_dir: Path, name: str, records: list[dict]) -> str:
    """既存の階層（大文字小文字は同一視）の表記を返す。存在しなければ ValueError。"""
    for existing in list_folders(save_dir, records):
        if same_folder_name(existing, name):
            return existing
    raise ValueError("指定した階層が存在しません。")


def _filename_of(rec: dict) -> str:
    return str(rec.get("path", "")).replace("\\", "/").rsplit("/", 1)[-1]


def rename_folder(
    save_dir: Path,
    old: str,
    new: str,
    records: list[dict],
    metadata_path: Path,
) -> int:
    """階層 old を new に改名し、その階層のレコードの path を更新して保存する。更新したレコード数を返す。

    - 新しい名前は validate_folder_name で検証する。大文字小文字だけの変更は許可する。
    - 別の階層と同名（大文字小文字違いを含む）なら FolderExistsError、同名のファイルがあれば ValueError。
    - ディレクトリが無くレコードだけが参照する階層は、path だけを更新する。
    - 保存に失敗したときは、フォルダ名と path を元に戻す。戻せなかったときは、レコードを実際の
      フォルダ名に合わせたうえで OSError にする。
    """
    save_dir = Path(save_dir)
    old_name = _canonical_folder(save_dir, old, records)
    new_name = validate_folder_name(new)
    if new_name == old_name:
        return 0
    case_only = same_folder_name(new_name, old_name)
    new_dir = save_dir / new_name
    if not case_only:
        if any(same_folder_name(new_name, f) for f in list_folders(save_dir, records)):
            raise FolderExistsError(new_name)
        if new_dir.exists():
            raise ValueError("同名のファイルまたはフォルダが存在するため変更できません。")
    old_dir = resolve_folder_dir(save_dir, old_name)

    renamed = old_dir.is_dir()
    if renamed:
        os.rename(old_dir, new_dir)

    targets = [r for r in records if same_folder_name(record_folder(r), old_name)]
    originals = [(r, r["path"]) for r in targets]
    for r in targets:
        r["path"] = build_record_path(new_name, _filename_of(r))
    try:
        save_metadata(records, metadata_path)
    except BaseException as exc:
        for r, old_path in originals:
            r["path"] = old_path
        if renamed:
            try:
                os.rename(new_dir, old_dir)
            except OSError:
                # 戻せなかったので、レコードを実際のフォルダ名に合わせる
                for r in targets:
                    r["path"] = build_record_path(new_name, _filename_of(r))
                try:
                    save_metadata(records, metadata_path)
                except OSError:
                    pass
                if isinstance(exc, Exception):
                    raise OSError(
                        f"階層名の変更に失敗し、元の名前へ戻せませんでした。現在のフォルダ名は「{new_name}」です。"
                    ) from exc
        raise
    return len(targets)


def folder_unmanaged_entries(save_dir: Path, folder: str, records: list[dict]) -> list[str]:
    """階層の中にある、レコードが指していないファイル・フォルダの名前を返す（階層を削除してよいかの判定に使う）。"""
    target = resolve_folder_dir(save_dir, folder)
    if not target.is_dir():
        return []
    managed = {
        _filename_of(r).lower() for r in records if same_folder_name(record_folder(r), folder)
    }
    with os.scandir(target) as it:
        return sorted(e.name for e in it if e.name.lower() not in managed)


def delete_records(
    records: list[dict],
    indices: list[int],
    save_dir: Path,
    metadata_path: Path,
) -> int:
    """records[indices] を削除し、削除した件数を返す。records は呼び出し元のリストを直接更新する。

    取り消せない画像ファイルの削除は、メタデータの保存に成功した後に行う（失敗したら何も消えない）。
    path が不正なレコードはレコードだけを消し、save_dir の外のファイルには触れない。
    """
    unique = sorted(set(indices))
    if any(not 0 <= i < len(records) for i in unique):
        raise ValueError("削除対象が不正です。")
    removed = [records[i] for i in unique]
    removed_ids = {id(r) for r in removed}
    kept = [r for r in records if id(r) not in removed_ids]
    save_metadata(kept, metadata_path)
    records[:] = kept

    folder_ok: dict[str, bool] = {}
    in_use = {
        path for r in kept if (path := record_file_path(save_dir, r, folder_ok)) is not None
    }
    for rec in removed:
        path = record_file_path(save_dir, rec, folder_ok)
        if path is not None and path not in in_use:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    return len(removed)


def _check_folder_removable(
    save_dir: Path, name: str, records: list[dict]
) -> tuple[Path, list[int]]:
    """階層を取り除いてよいか検査し、(ディレクトリ, 中のレコードの位置) を返す。何も変更しない。

    リンクになっている階層、一覧に表示されないファイル・フォルダがある階層、
    パスが不正または画像ファイルではないものを指すコードがある階層は拒否する。
    """
    target = resolve_folder_dir(save_dir, name)
    if target.exists() and os.path.normcase(str(target.resolve())) != os.path.normcase(
        str(save_dir.resolve() / target.name)
    ):
        raise ValueError("リンクになっている階層は削除できません。")
    unmanaged = folder_unmanaged_entries(save_dir, name, records)
    if unmanaged:
        raise UnmanagedFilesError(unmanaged)

    indices = [i for i, r in enumerate(records) if same_folder_name(record_folder(r), name)]
    folder_ok: dict[str, bool] = {}
    for i in indices:
        path = record_file_path(save_dir, records[i], folder_ok)
        if path is None:
            raise ValueError("パスが不正なコードがあるため、階層を削除できません。")
        if path.exists() and not path.is_file():
            raise ValueError("画像ファイルではないものがあるため、階層を削除できません。")
    return target, indices


def delete_folder(
    save_dir: Path,
    folder: str,
    records: list[dict],
    metadata_path: Path,
    contents: str = "error",
    overwrite: bool = False,
) -> DeleteFolderResult:
    """階層を削除する。中身（レコード）の扱いは contents で指定する。

    - "error": 中身があれば FolderNotEmptyError（何も変更しない）。
    - "move_to_root": 中のコードをルートへ戻す（move_records と同じ。衝突時は MoveCollisionError）。
    - "delete": 中のコードを画像ファイルごと削除する。
    階層に一覧へ表示されないファイル・フォルダがあるときは、何も変更せず UnmanagedFilesError にする
    （フォルダを再帰的に消すことはしない）。
    """
    if contents not in ("error", "move_to_root", "delete"):
        raise ValueError("中身の扱いが不正です。")
    save_dir = Path(save_dir)
    name = _canonical_folder(save_dir, folder, records)
    target, indices = _check_folder_removable(save_dir, name, records)
    moved = deleted = 0
    if indices:
        if contents == "error":
            raise FolderNotEmptyError(len(indices))
        if contents == "move_to_root":
            moved = move_records(records, indices, "", save_dir, metadata_path, overwrite=overwrite).moved
        else:
            deleted = delete_records(records, indices, save_dir, metadata_path)
    if target.is_dir():
        try:
            target.rmdir()
        except OSError as exc:
            if moved or deleted:
                raise OSError(
                    f"コードの{'移動' if moved else '削除'}は完了しましたが、"
                    f"階層フォルダを削除できませんでした: {exc}"
                ) from exc
            raise
    return DeleteFolderResult(moved, deleted)


@dataclass(frozen=True)
class MergeFolderResult:
    moved: int
    overwritten: int
    left: int  # 移動先に同じコードがあり、元の階層に残したコードの件数
    removed_source: bool


def merge_folder_collisions(
    save_dir: Path, src: str, dest: str, records: list[dict]
) -> list[tuple[int, int]]:
    """src を dest へ統合するとき、dest に同じコードがある組を (src 側, dest 側) の位置で返す。"""
    name = _canonical_folder(save_dir, src, records)
    target = _canonical_folder(save_dir, dest, records)
    indices = [i for i, r in enumerate(records) if same_folder_name(record_folder(r), name)]
    return find_move_collisions(records, indices, target)


def merge_folder(
    save_dir: Path,
    src: str,
    dest: str,
    records: list[dict],
    metadata_path: Path,
    overwrite: set[int] | None = None,
) -> MergeFolderResult:
    """階層 src の中身を、既にある階層 dest へ統合する（dest の既存のコードはそのまま残る）。

    dest に同じコードがある src のコードは、位置が overwrite に含まれていれば dest 側を置き換え、
    含まれていなければ移動せず src に残す（その場合 src は削除されない）。
    src に一覧へ表示されないファイル・フォルダがあるときは何も変更せず UnmanagedFilesError。
    """
    save_dir = Path(save_dir)
    name = _canonical_folder(save_dir, src, records)
    target_name = _canonical_folder(save_dir, dest, records)
    if same_folder_name(name, target_name):
        raise ValueError("同じ階層には統合できません。")
    target, indices = _check_folder_removable(save_dir, name, records)
    overwrite = overwrite or set()
    colliding = {s for s, _ in find_move_collisions(records, indices, target_name)}
    chosen = colliding & overwrite
    moving = [i for i in indices if i not in colliding or i in chosen]
    left = len(indices) - len(moving)
    result = MoveResult(0, 0)
    if moving:
        result = move_records(records, moving, target_name, save_dir, metadata_path, overwrite=bool(chosen))
    removed = False
    if left == 0 and target.is_dir():
        try:
            target.rmdir()
            removed = True
        except OSError as exc:
            raise OSError(
                f"コードの統合は完了しましたが、元の階層フォルダを削除できませんでした: {exc}"
            ) from exc
    return MergeFolderResult(result.moved, result.overwritten, left, removed)
