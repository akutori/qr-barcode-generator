"""CSV一括インポート: パース・バリデーション（tkinter依存なし）"""

from __future__ import annotations

import csv
import dataclasses
import io
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import segno

from core import (
    build_record_path,
    has_duplicate,
    record_folder,
    validate_folder_name,
)

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

TEMPLATE_HEADER = "text,type,description,error_correction,encoding,folder"
TEMPLATE_EXAMPLE_ROWS = [
    "https://example.com,QR,説明（省略可）,M,UTF-8,商品",
    "12345678901234,Barcode,説明（省略可）,,,",
]

_VALID_TYPES_NORMALIZED = {
    "Q":       "Q",
    "QR":      "Q",
    "B":       "B",
    "BARCODE": "B",
}
_VALID_EC = {"L", "M", "Q", "H"}

_ENCODING_ALIASES: dict[str, str] = {
    "UTF-8":     "UTF-8",
    "UTF8":      "UTF-8",
    "SJIS":      "SJIS",
    "SHIFT-JIS": "SJIS",
    "SHIFT_JIS": "SJIS",
}

_EC_MAP = {"L": "l", "M": "m", "Q": "q", "H": "h"}

# ---------------------------------------------------------------------------
# データ型
# ---------------------------------------------------------------------------

class RowStatus(Enum):
    OK = "ok"
    DUPLICATE = "duplicate"
    ERROR = "error"


@dataclass
class ImportRow:
    line_no: int
    text: str
    code_type: str        # 正規化済み "Q" / "B"
    description: str
    error_correction: str # "L"/"M"/"Q"/"H"
    encoding: str = "UTF-8"  # "UTF-8" / "SJIS"（QR のみ）
    status: RowStatus = RowStatus.OK
    error_msg: str = ""
    folder: str = ""         # 取り込み先の階層名（空はルート）
    new_folder: bool = False  # 取り込み時に作成される階層か


class ParseError(Exception):
    """CSVファイルの構造が不正なときに送出する。"""


# ---------------------------------------------------------------------------
# 表示ユーティリティ
# ---------------------------------------------------------------------------

def format_text_for_display(text: str, max_len: int = 60) -> str:
    """一覧表示用にテキストを整形する。改行を ↵ に置換し、最大長を超えたら … を付ける。"""
    t = text.replace("\n", "↵")
    return t[:max_len] + "…" if len(t) > max_len else t


def format_ec_for_display(row: "ImportRow") -> str:
    """Treeview表示用の誤り訂正レベルを返す。Barcode 行は '—'。"""
    return row.error_correction if row.code_type == "Q" else "—"


def format_encoding_for_display(row: "ImportRow") -> str:
    """Treeview表示用のエンコード名を返す。Barcode 行は '—'。"""
    return row.encoding if row.code_type == "Q" else "—"


def format_folder_for_display(row: "ImportRow") -> str:
    """Treeview表示用の取り込み先。ルートは「（ルート）」、取り込み時に作成する階層は「（新規）」を付ける。"""
    if not row.folder:
        return "（ルート）"
    return f"{row.folder}（新規）" if row.new_folder else row.folder


# ---------------------------------------------------------------------------
# テンプレート生成
# ---------------------------------------------------------------------------

def generate_template() -> str:
    lines = [TEMPLATE_HEADER] + TEMPLATE_EXAMPLE_ROWS
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# パース
# ---------------------------------------------------------------------------

_REQUIRED_HEADERS = ["text", "type", "description", "error_correction"]
_OPTIONAL_COLUMNS = ("encoding", "folder")


def _optional_column_indices(header: list[str]) -> dict[str, int]:
    """5 列目以降のヘッダーから、省略可の列（encoding / folder）の位置を列名で求める。同名は先頭を優先する。"""
    indices: dict[str, int] = {}
    for i, name in enumerate(header[4:], start=4):
        if name in _OPTIONAL_COLUMNS and name not in indices:
            indices[name] = i
    return indices


def parse_csv(path: Path) -> list[ImportRow]:
    """
    CSVファイルを読み込んで ImportRow リストを返す。

    - BOM 付き UTF-8 対応（Excel の既定保存形式）
    - ヘッダー行必須（なければ ParseError）
    - encoding / folder 列は省略可（5 列目以降に列名で指定。省略時は "UTF-8" / ルート）
    - 空行はスキップ
    - 列数不足・不正値の行は ERROR ステータスで返す（中断しない）
    - 列数が多い行は余分な列を無視する
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise ParseError(f"ファイルを読み込めません: {e}") from e

    if not text.strip():
        raise ParseError("ファイルが空です。")

    reader = csv.reader(io.StringIO(text))
    try:
        raw_header = next(reader)
    except StopIteration:
        raise ParseError("ファイルが空です。")

    header = [h.strip().lower() for h in raw_header]
    # 4列（旧フォーマット）または5列（encoding 付き）を受け入れる
    if header[:4] != _REQUIRED_HEADERS:
        raise ParseError(
            f"ヘッダー行が不正です。\n"
            f"期待: text,type,description,error_correction[,encoding][,folder]\n"
            f"実際: {','.join(raw_header)}"
        )
    columns = _optional_column_indices(header)

    rows: list[ImportRow] = []
    for line_no, raw_row in enumerate(reader, start=2):
        # 空行スキップ
        if not any(cell.strip() for cell in raw_row):
            continue

        row = _parse_row(line_no, raw_row, columns)
        rows.append(row)

    return rows


def _parse_row(
    line_no: int,
    raw: list[str],
    columns: dict[str, int] | None = None,
) -> ImportRow:
    """1行をパースして ImportRow を返す。不正な場合は ERROR ステータス。"""
    if len(raw) < 4:
        return ImportRow(
            line_no=line_no, text="", code_type="", description="",
            error_correction="", status=RowStatus.ERROR,
            error_msg=f"列数が不足しています（{len(raw)}列、4列必要）",
        )

    text = raw[0].strip()
    raw_type = raw[1].strip()
    description = raw[2].strip()
    raw_ec = raw[3].strip().upper()

    if not text:
        return ImportRow(
            line_no=line_no, text="", code_type="", description=description,
            error_correction="", status=RowStatus.ERROR,
            error_msg="テキストが空です。",
        )

    normalized_type = _VALID_TYPES_NORMALIZED.get(raw_type.upper())
    if normalized_type is None:
        return ImportRow(
            line_no=line_no, text=text, code_type=raw_type, description=description,
            error_correction="", status=RowStatus.ERROR,
            error_msg=f"不正な種別: '{raw_type}'（QR/Q または BARCODE/B を指定してください）",
        )

    # error_correction の検証（Barcode は任意）
    if not raw_ec:
        ec = "M"
    elif raw_ec not in _VALID_EC:
        return ImportRow(
            line_no=line_no, text=text, code_type=normalized_type,
            description=description, error_correction=raw_ec,
            status=RowStatus.ERROR,
            error_msg=f"不正な誤り訂正レベル: '{raw_ec}'（L/M/Q/H を指定してください）",
        )
    else:
        ec = raw_ec

    columns = columns or {}

    # encoding 列の解析（省略可）
    encoding = "UTF-8"
    enc_idx = columns.get("encoding")
    if enc_idx is not None and len(raw) > enc_idx:
        raw_enc = raw[enc_idx].strip().upper()
        if raw_enc:
            normalized_enc = _ENCODING_ALIASES.get(raw_enc)
            if normalized_enc is None:
                return ImportRow(
                    line_no=line_no, text=text, code_type=normalized_type,
                    description=description, error_correction=ec,
                    status=RowStatus.ERROR,
                    error_msg=f"不正なエンコード: '{raw[enc_idx].strip()}'（UTF-8 または SJIS を指定してください）",
                )
            encoding = normalized_enc

    # folder 列の解析（省略可。空欄はルート）
    folder = ""
    folder_idx = columns.get("folder")
    if folder_idx is not None and len(raw) > folder_idx:
        raw_folder = raw[folder_idx].strip()
        if raw_folder:
            try:
                folder = validate_folder_name(raw_folder)
            except ValueError as e:
                return ImportRow(
                    line_no=line_no, text=text, code_type=normalized_type,
                    description=description, error_correction=ec, encoding=encoding,
                    status=RowStatus.ERROR,
                    error_msg=f"不正な階層名: '{raw_folder}'（{e}）",
                )

    return ImportRow(
        line_no=line_no, text=text, code_type=normalized_type,
        description=description, error_correction=ec, encoding=encoding,
        folder=folder,
    )


# ---------------------------------------------------------------------------
# バリデーション
# ---------------------------------------------------------------------------

def validate_row(row: ImportRow, existing_records: list[dict]) -> ImportRow:
    """
    1行をバリデーションし、status を更新して返す。
    すでに ERROR な行はスキップする。
    """
    if row.status == RowStatus.ERROR:
        return row

    if row.code_type == "Q":
        err = _check_qr_capacity(row.text, row.error_correction, row.encoding)
        if err:
            return dataclasses.replace(row, status=RowStatus.ERROR, error_msg=err)
    else:
        if not row.text.isascii():
            return dataclasses.replace(
                row, status=RowStatus.ERROR,
                error_msg="バーコード (Code128) は ASCII 文字のみ対応しています。",
            )

    ec = row.error_correction if row.code_type == "Q" else None
    enc = row.encoding if row.code_type == "Q" else None
    if has_duplicate(row.text, row.code_type, existing_records,
                     error_correction=ec, encoding=enc):
        return dataclasses.replace(row, status=RowStatus.DUPLICATE,
                                   error_msg="既存のレコードと重複しています。")

    return row


def _check_qr_capacity(
    text: str,
    error_correction: str,
    encoding: str = "UTF-8",
) -> str:
    """QR コードにデータが収まるか確認する（画像生成なし）。
    収まらない場合はエラーメッセージを返す。収まる場合は空文字を返す。
    SJIS で表現できない文字もここで検出する。
    """
    codec = "cp932" if encoding == "SJIS" else "utf-8"
    ec = _EC_MAP.get(error_correction, "m")
    try:
        data = text.encode(codec)
    except UnicodeEncodeError:
        return "テキストに Shift-JIS で表現できない文字が含まれています。"
    try:
        segno.make_qr(data, error=ec)
        return ""
    except segno.encoder.DataOverflowError:
        return f"テキストが長すぎてQRコードに収まりません。（{len(data)} バイト）"


def _resolve_row_folder(
    row: ImportRow,
    known: dict[str, str],
    new_keys: set[str],
    create_missing_folders: bool,
) -> ImportRow:
    """行の階層を既存の表記に揃え、存在しない階層は新規扱い（または ERROR）にする。

    known は小文字名 → 表記。新規扱いにした階層は known に加え、後続の行が同じ表記を使うようにする。
    """
    if not row.folder:
        return row
    key = row.folder.lower()
    canonical = known.get(key)
    if canonical is None:
        if not create_missing_folders:
            return dataclasses.replace(
                row, status=RowStatus.ERROR,
                error_msg=f"階層「{row.folder}」が存在しません。"
                          "（先に作成するか、「存在しない階層」を自動作成にしてください）",
            )
        known[key] = canonical = row.folder
        new_keys.add(key)
    return dataclasses.replace(row, folder=canonical, new_folder=key in new_keys)


def validate_all(
    rows: list[ImportRow],
    existing_records: list[dict],
    existing_folders: list[str] | None = None,
    create_missing_folders: bool = True,
) -> list[ImportRow]:
    """
    全行をバリデーションする。
    重複は、行ごとの取り込み先の階層内だけで判定する（既存レコードと、CSV 内の先行行の両方）。
    CSV 内の重複は先着優先。

    - existing_folders: 既存の階層名。省略時は既存レコードが属する階層から求める。
    - create_missing_folders: False のとき、存在しない階層を指す行は ERROR にする。
    """
    if existing_folders is None:
        names = {record_folder(r) for r in existing_records} - {""}
    else:
        names = set(existing_folders)
    known = {n.lower(): n for n in names}
    new_keys: set[str] = set()

    # 階層ごとの既存レコード。OK な行は追加して後続行の重複チェックに使う
    by_folder: dict[str, list[dict]] = {}
    for r in existing_records:
        by_folder.setdefault(record_folder(r).lower(), []).append(r)

    result: list[ImportRow] = []
    for row in rows:
        if row.status != RowStatus.ERROR:
            row = _resolve_row_folder(row, known, new_keys, create_missing_folders)
        validated = validate_row(row, by_folder.get(row.folder.lower(), []))
        result.append(validated)
        if validated.status == RowStatus.OK:
            rec: dict = {
                "text": validated.text,
                "type": validated.code_type,
                "path": build_record_path(validated.folder, "pending.png"),
            }
            if validated.code_type == "Q":
                rec["error_correction"] = validated.error_correction
                rec["encoding"] = validated.encoding
            by_folder.setdefault(validated.folder.lower(), []).append(rec)

    return result
