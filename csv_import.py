"""CSV一括インポート: パース・バリデーション（tkinter依存なし）"""

from __future__ import annotations

import csv
import dataclasses
import io
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import segno

from core import has_duplicate

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

TEMPLATE_HEADER = "text,type,description,error_correction,encoding"
TEMPLATE_EXAMPLE_ROWS = [
    "https://example.com,QR,説明（省略可）,M,UTF-8",
    "12345678901234,Barcode,説明（省略可）,,",
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


def parse_csv(path: Path) -> list[ImportRow]:
    """
    CSVファイルを読み込んで ImportRow リストを返す。

    - BOM 付き UTF-8 対応（Excel の既定保存形式）
    - ヘッダー行必須（なければ ParseError）
    - 5 列目 encoding は省略可（省略時は "UTF-8"）
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
            f"期待: text,type,description,error_correction[,encoding]\n"
            f"実際: {','.join(raw_header)}"
        )
    has_encoding_col = len(header) >= 5 and header[4] == "encoding"

    rows: list[ImportRow] = []
    for line_no, raw_row in enumerate(reader, start=2):
        # 空行スキップ
        if not any(cell.strip() for cell in raw_row):
            continue

        row = _parse_row(line_no, raw_row, has_encoding_col=has_encoding_col)
        rows.append(row)

    return rows


def _parse_row(
    line_no: int,
    raw: list[str],
    has_encoding_col: bool = False,
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

    # encoding 列の解析（5列目、省略可）
    encoding = "UTF-8"
    if has_encoding_col and len(raw) >= 5:
        raw_enc = raw[4].strip().upper()
        if raw_enc:
            normalized_enc = _ENCODING_ALIASES.get(raw_enc)
            if normalized_enc is None:
                return ImportRow(
                    line_no=line_no, text=text, code_type=normalized_type,
                    description=description, error_correction=ec,
                    status=RowStatus.ERROR,
                    error_msg=f"不正なエンコード: '{raw[4].strip()}'（UTF-8 または SJIS を指定してください）",
                )
            encoding = normalized_enc

    return ImportRow(
        line_no=line_no, text=text, code_type=normalized_type,
        description=description, error_correction=ec, encoding=encoding,
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


_SEGNO_ENC_MAP: dict[str, str] = {"UTF-8": "utf-8", "SJIS": "shift_jis"}


def _check_qr_capacity(
    text: str,
    error_correction: str,
    encoding: str = "UTF-8",
) -> str:
    """QR コードにデータが収まるか確認する（画像生成なし）。
    収まらない場合はエラーメッセージを返す。収まる場合は空文字を返す。
    SJIS で表現できない文字もここで検出する。
    """
    segno_enc = _SEGNO_ENC_MAP.get(encoding, "utf-8")
    ec = _EC_MAP.get(error_correction, "m")
    try:
        segno.make_qr(text, encoding=segno_enc, eci=True, error=ec)
        return ""
    except UnicodeEncodeError:
        return "テキストに Shift-JIS で表現できない文字が含まれています。"
    except segno.encoder.DataOverflowError:
        byte_len = len(text.encode(segno_enc, errors="replace"))
        return f"テキストが長すぎてQRコードに収まりません。（{byte_len} バイト）"


def validate_all(
    rows: list[ImportRow],
    existing_records: list[dict],
) -> list[ImportRow]:
    """
    全行をバリデーションする。
    CSV 内の重複も検出する（先着優先）。
    """
    # 既存レコードのコピーを作り、CSV内の先行行を順次追加して重複チェック
    working_records: list[dict] = list(existing_records)
    result: list[ImportRow] = []

    for row in rows:
        validated = validate_row(row, working_records)
        result.append(validated)
        # OK な行だけ working_records に追加して後続行の重複チェックに使う
        if validated.status == RowStatus.OK:
            rec: dict = {
                "text": validated.text,
                "type": validated.code_type,
                "path": "",
            }
            if validated.code_type == "Q":
                rec["error_correction"] = validated.error_correction
                rec["encoding"] = validated.encoding
            working_records.append(rec)

    return result
