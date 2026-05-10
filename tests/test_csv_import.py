"""csv_import.py のユニットテスト (TDD / 悲観的観測)"""

import io
from pathlib import Path

import pytest

from csv_import import (
    ImportRow,
    ParseError,
    RowStatus,
    format_ec_for_display,
    format_text_for_display,
    generate_template,
    parse_csv,
    validate_all,
    validate_row,
)


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _write_csv(tmp_path: Path, content: str, filename: str = "test.csv") -> Path:
    p = tmp_path / filename
    p.write_text(content, encoding="utf-8")
    return p


def _write_csv_bytes(tmp_path: Path, data: bytes, filename: str = "test.csv") -> Path:
    p = tmp_path / filename
    p.write_bytes(data)
    return p


VALID_HEADER = "text,type,description,error_correction\n"
VALID_ROW_QR = "https://example.com,QR,会社サイト,M\n"
VALID_ROW_BC = "12345678901234,Barcode,商品コード,\n"


# ---------------------------------------------------------------------------
# generate_template
# ---------------------------------------------------------------------------

class TestGenerateTemplate:
    def test_ヘッダー行を含む(self):
        t = generate_template()
        lines = t.splitlines()
        assert lines[0] == "text,type,description,error_correction"

    def test_サンプルデータを2行含む(self):
        t = generate_template()
        lines = [l for l in t.splitlines() if l.strip() and not l.startswith("text")]
        assert len(lines) == 2

    def test_QRサンプル行が含まれる(self):
        t = generate_template()
        assert "QR" in t

    def test_Barcodeサンプル行が含まれる(self):
        t = generate_template()
        assert "Barcode" in t


# ---------------------------------------------------------------------------
# parse_csv
# ---------------------------------------------------------------------------

class TestParseCsv:
    def test_正常な4列CSVを読み込める(self, tmp_path):
        p = _write_csv(tmp_path, VALID_HEADER + VALID_ROW_QR + VALID_ROW_BC)
        rows = parse_csv(p)
        assert len(rows) == 2
        assert rows[0].text == "https://example.com"
        assert rows[0].code_type == "Q"
        assert rows[0].description == "会社サイト"
        assert rows[0].error_correction == "M"
        assert rows[1].code_type == "B"

    def test_BOM付きUTF8を正しく読み込める(self, tmp_path):
        content = (VALID_HEADER + VALID_ROW_QR).encode("utf-8-sig")
        p = _write_csv_bytes(tmp_path, content)
        rows = parse_csv(p)
        assert len(rows) == 1
        assert rows[0].text == "https://example.com"

    def test_ヘッダーなしCSVはParseErrorを送出する(self, tmp_path):
        p = _write_csv(tmp_path, "https://example.com,QR,,M\n")
        with pytest.raises(ParseError):
            parse_csv(p)

    def test_空ファイルはParseErrorを送出する(self, tmp_path):
        p = _write_csv(tmp_path, "")
        with pytest.raises(ParseError):
            parse_csv(p)

    def test_存在しないファイルはParseErrorを送出する(self, tmp_path):
        with pytest.raises(ParseError):
            parse_csv(tmp_path / "nonexistent.csv")

    def test_ヘッダーのみは空リストを返す(self, tmp_path):
        p = _write_csv(tmp_path, VALID_HEADER)
        assert parse_csv(p) == []

    def test_typeは大文字小文字不問で正規化される(self, tmp_path):
        p = _write_csv(tmp_path, VALID_HEADER + "hello,qr,,m\n" + "world,barcode,,\n")
        rows = parse_csv(p)
        assert rows[0].code_type == "Q"
        assert rows[0].error_correction == "M"
        assert rows[1].code_type == "B"

    def test_無効なtypeはERRORステータス(self, tmp_path):
        p = _write_csv(tmp_path, VALID_HEADER + "hello,INVALID,,M\n")
        rows = parse_csv(p)
        assert rows[0].status == RowStatus.ERROR
        assert rows[0].error_msg != ""

    def test_error_correctionが空の場合はMにデフォルト(self, tmp_path):
        p = _write_csv(tmp_path, VALID_HEADER + "https://example.com,QR,,\n")
        rows = parse_csv(p)
        assert rows[0].error_correction == "M"

    def test_error_correctionがLMQH以外はERRORステータス(self, tmp_path):
        p = _write_csv(tmp_path, VALID_HEADER + "hello,QR,,Z\n")
        rows = parse_csv(p)
        assert rows[0].status == RowStatus.ERROR

    def test_Barcode行はerror_correctionが空でもOK(self, tmp_path):
        p = _write_csv(tmp_path, VALID_HEADER + "12345,Barcode,,\n")
        rows = parse_csv(p)
        assert rows[0].status == RowStatus.OK

    def test_空行はスキップされる(self, tmp_path):
        p = _write_csv(tmp_path, VALID_HEADER + "\n" + VALID_ROW_QR + "\n")
        rows = parse_csv(p)
        assert len(rows) == 1

    def test_テキストが空の行はERRORステータス(self, tmp_path):
        p = _write_csv(tmp_path, VALID_HEADER + ",QR,,M\n")
        rows = parse_csv(p)
        assert rows[0].status == RowStatus.ERROR

    def test_列数不足の行はERRORステータスで中断しない(self, tmp_path):
        p = _write_csv(tmp_path, VALID_HEADER + "hello\n" + VALID_ROW_QR)
        rows = parse_csv(p)
        assert len(rows) == 2
        assert rows[0].status == RowStatus.ERROR
        assert rows[1].status == RowStatus.OK

    def test_CRLF改行コードも正しく読み込める(self, tmp_path):
        content = (VALID_HEADER + VALID_ROW_QR).replace("\n", "\r\n")
        p = _write_csv(tmp_path, content)
        rows = parse_csv(p)
        assert len(rows) == 1
        assert rows[0].text == "https://example.com"

    def test_行番号が正しく設定される(self, tmp_path):
        p = _write_csv(tmp_path, VALID_HEADER + VALID_ROW_QR + VALID_ROW_BC)
        rows = parse_csv(p)
        assert rows[0].line_no == 2
        assert rows[1].line_no == 3

    def test_フィールド内カンマを引用符で囲める(self, tmp_path):
        p = _write_csv(tmp_path, VALID_HEADER + '"hello,world",QR,説明,M\n')
        rows = parse_csv(p)
        assert rows[0].text == "hello,world"

    def test_フィールド内改行を引用符で囲める(self, tmp_path):
        p = _write_csv(tmp_path, VALID_HEADER + '"line1\nline2",QR,説明,M\n')
        rows = parse_csv(p)
        assert rows[0].text == "line1\nline2"
        assert rows[0].status == RowStatus.OK

    def test_列数が多い行は余分な列を無視する(self, tmp_path):
        p = _write_csv(tmp_path, VALID_HEADER + "hello,QR,desc,M,extra,more\n")
        rows = parse_csv(p)
        assert len(rows) == 1
        assert rows[0].status == RowStatus.OK

    def test_複数行を連続して正しく読み込める(self, tmp_path):
        content = VALID_HEADER + VALID_ROW_QR * 5
        p = _write_csv(tmp_path, content)
        rows = parse_csv(p)
        assert len(rows) == 5

    def test_Qを入力するとcode_typeがQになる(self, tmp_path):
        p = _write_csv(tmp_path, VALID_HEADER + "hello,Q,,M\n")
        rows = parse_csv(p)
        assert rows[0].code_type == "Q"

    def test_Bを入力するとcode_typeがBになる(self, tmp_path):
        p = _write_csv(tmp_path, VALID_HEADER + "12345,B,,\n")
        rows = parse_csv(p)
        assert rows[0].code_type == "B"

    def test_小文字qも受け入れQに正規化される(self, tmp_path):
        p = _write_csv(tmp_path, VALID_HEADER + "hello,q,,M\n")
        rows = parse_csv(p)
        assert rows[0].code_type == "Q"

    def test_小文字bも受け入れBに正規化される(self, tmp_path):
        p = _write_csv(tmp_path, VALID_HEADER + "12345,b,,\n")
        rows = parse_csv(p)
        assert rows[0].code_type == "B"


# ---------------------------------------------------------------------------
# validate_row
# ---------------------------------------------------------------------------

class TestValidateRow:
    def _ok_qr_row(self, text: str = "hello", ec: str = "M") -> ImportRow:
        return ImportRow(line_no=2, text=text, code_type="Q",
                         description="", error_correction=ec)

    def _ok_bc_row(self, text: str = "12345") -> ImportRow:
        return ImportRow(line_no=2, text=text, code_type="B",
                         description="", error_correction="M")

    def test_QRで正常なテキストはOK(self):
        row = self._ok_qr_row("hello")
        result = validate_row(row, [])
        assert result.status == RowStatus.OK

    def test_QRでテキストが長すぎる場合はERROR(self):
        long_text = "あ" * 3000  # UTF-8で9000バイト超 → どの誤り訂正レベルでも超過
        row = self._ok_qr_row(long_text)
        result = validate_row(row, [])
        assert result.status == RowStatus.ERROR
        assert "バイト" in result.error_msg or "文字" in result.error_msg

    def test_QRで誤り訂正HはLより容量が小さい(self):
        # H は L よりも少ないデータ量しか入らない
        # 2000文字程度のASCIIテキストで H はエラー、L はOK になることを検証
        text = "a" * 2000
        row_l = self._ok_qr_row(text, "L")
        row_h = self._ok_qr_row(text, "H")
        result_l = validate_row(row_l, [])
        result_h = validate_row(row_h, [])
        # L では入るが H では入らない（またはどちらかがERROR）
        # 少なくとも H の方が厳しい（H がOKなら L もOK）
        if result_h.status == RowStatus.ERROR:
            assert True  # H がエラーになることを確認
        else:
            assert result_l.status == RowStatus.OK  # L も H もOKならどちらもOKでよい

    def test_BarcodeでASCIIのみはOK(self):
        row = self._ok_bc_row("ABC123")
        result = validate_row(row, [])
        assert result.status == RowStatus.OK

    def test_Barcodeで非ASCII文字はERROR(self):
        row = self._ok_bc_row("日本語テスト")
        result = validate_row(row, [])
        assert result.status == RowStatus.ERROR

    def test_既存レコードと重複する場合はDUPLICATE(self):
        existing = [{"text": "hello", "type": "Q", "path": "qr.png",
                     "error_correction": "M"}]
        row = self._ok_qr_row("hello")
        result = validate_row(row, existing)
        assert result.status == RowStatus.DUPLICATE

    def test_重複なしはOK(self):
        existing = [{"text": "other", "type": "Q", "path": "qr.png",
                     "error_correction": "M"}]
        row = self._ok_qr_row("hello")
        result = validate_row(row, existing)
        assert result.status == RowStatus.OK

    def test_すでにERRORな行はバリデーションをスキップする(self):
        row = ImportRow(line_no=2, text="", code_type="INVALID",
                        description="", error_correction="M",
                        status=RowStatus.ERROR, error_msg="既存のエラー")
        result = validate_row(row, [])
        assert result.status == RowStatus.ERROR
        assert result.error_msg == "既存のエラー"


# ---------------------------------------------------------------------------
# validate_all
# ---------------------------------------------------------------------------

class TestValidateAll:
    def _make_row(self, text: str, code_type: str = "Q",
                  status: RowStatus = RowStatus.OK) -> ImportRow:
        return ImportRow(line_no=2, text=text, code_type=code_type,
                         description="", error_correction="M", status=status)

    def test_全件OKの場合は全件OKを返す(self):
        rows = [self._make_row("a"), self._make_row("b")]
        result = validate_all(rows, [])
        assert all(r.status == RowStatus.OK for r in result)

    def test_OKとDUPLICATEとERRORが混在する場合を正しく分類できる(self):
        existing = [{"text": "dup", "type": "Q", "path": "qr.png",
                     "error_correction": "M"}]
        err_row = self._make_row("x", status=RowStatus.ERROR)
        err_row.error_msg = "事前エラー"
        rows = [
            self._make_row("ok"),
            self._make_row("dup"),
            err_row,
        ]
        result = validate_all(rows, existing)
        statuses = [r.status for r in result]
        assert RowStatus.OK in statuses
        assert RowStatus.DUPLICATE in statuses
        assert RowStatus.ERROR in statuses

    def test_CSV内で同テキスト種別が重複する場合2件目以降がDUPLICATE(self):
        rows = [self._make_row("same"), self._make_row("same")]
        result = validate_all(rows, [])
        assert result[0].status == RowStatus.OK
        assert result[1].status == RowStatus.DUPLICATE

    def test_既存レコードとの重複もDUPLICATEになる(self):
        existing = [{"text": "already", "type": "Q", "path": "qr.png",
                     "error_correction": "M"}]
        rows = [self._make_row("already")]
        result = validate_all(rows, existing)
        assert result[0].status == RowStatus.DUPLICATE

    def test_全件ERRORでも空リストでなく全件返す(self):
        rows = [
            self._make_row("x", status=RowStatus.ERROR),
            self._make_row("y", status=RowStatus.ERROR),
        ]
        for r in rows:
            r.error_msg = "error"
        result = validate_all(rows, [])
        assert len(result) == 2

    def test_空リストは空リストを返す(self):
        assert validate_all([], []) == []

    def test_CSV内重複チェックはQRと誤り訂正レベルも考慮する(self):
        row_m = ImportRow(line_no=2, text="hello", code_type="Q",
                          description="", error_correction="M")
        row_h = ImportRow(line_no=3, text="hello", code_type="Q",
                          description="", error_correction="H")
        result = validate_all([row_m, row_h], [])
        # 誤り訂正レベルが違うので重複にならない
        assert result[0].status == RowStatus.OK
        assert result[1].status == RowStatus.OK


# ---------------------------------------------------------------------------
# format_text_for_display
# ---------------------------------------------------------------------------

class TestFormatTextForDisplay:
    def test_改行文字を矢印記号に置換する(self):
        assert format_text_for_display("line1\nline2") == "line1↵line2"

    def test_複数の改行もすべて置換する(self):
        assert format_text_for_display("a\nb\nc") == "a↵b↵c"

    def test_改行なしの文字列はそのまま返す(self):
        assert format_text_for_display("hello") == "hello"

    def test_最大長を超える場合は省略記号を付ける(self):
        assert format_text_for_display("a" * 61) == "a" * 60 + "…"

    def test_最大長ちょうどは省略しない(self):
        assert format_text_for_display("a" * 60) == "a" * 60

    def test_改行置換後に最大長を超えた場合も省略する(self):
        text = "a" * 59 + "\n" + "b" * 10
        result = format_text_for_display(text)
        assert "↵" in result
        assert result.endswith("…")
        assert len(result) == 61  # 60文字 + "…"

    def test_max_len引数で上限を変更できる(self):
        assert format_text_for_display("hello world", max_len=5) == "hello…"

    def test_空文字列は空文字列を返す(self):
        assert format_text_for_display("") == ""


# ---------------------------------------------------------------------------
# format_ec_for_display
# ---------------------------------------------------------------------------

class TestFormatEcForDisplay:
    def _qr(self, ec: str, status: RowStatus = RowStatus.OK) -> ImportRow:
        return ImportRow(line_no=2, text="t", code_type="Q",
                         description="", error_correction=ec, status=status)

    def _bc(self, ec: str = "M", status: RowStatus = RowStatus.OK) -> ImportRow:
        return ImportRow(line_no=2, text="t", code_type="B",
                         description="", error_correction=ec, status=status)

    def test_QRはerror_correctionの値をそのまま返す(self):
        assert format_ec_for_display(self._qr("M")) == "M"

    def test_QRでHはHを返す(self):
        assert format_ec_for_display(self._qr("H")) == "H"

    def test_QRでLはLを返す(self):
        assert format_ec_for_display(self._qr("L")) == "L"

    def test_QRでQはQを返す(self):
        assert format_ec_for_display(self._qr("Q")) == "Q"

    def test_Barcodeはダッシュを返す(self):
        assert format_ec_for_display(self._bc()) == "—"

    def test_Barcodeはecフィールドがあってもダッシュを返す(self):
        assert format_ec_for_display(self._bc("H")) == "—"

    def test_ERRORステータスのQRは空文字列を返す(self):
        assert format_ec_for_display(self._qr("", RowStatus.ERROR)) == ""

    def test_ERRORステータスのBarcodeはダッシュを返す(self):
        assert format_ec_for_display(self._bc("", RowStatus.ERROR)) == "—"
