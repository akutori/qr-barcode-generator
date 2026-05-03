"""generators.py のユニットテスト: QR / バーコード / PDF 生成 (t_wada 式 TDD)"""

from pathlib import Path

import pytest
from PIL import Image

from generators import (
    _truncate_label,
    generate_barcode_file,
    generate_pdf_grid,
    generate_qr,
)


# ---------------------------------------------------------------------------
# QR コード生成
# ---------------------------------------------------------------------------

class TestGenerateQR:
    def test_ファイルが生成される(self, tmp_path):
        fp = tmp_path / "test.png"
        generate_qr("hello", fp)
        assert fp.exists()

    def test_有効なPNG画像である(self, tmp_path):
        fp = tmp_path / "test.png"
        generate_qr("hello", fp)
        img = Image.open(str(fp))
        assert img.format == "PNG"

    def test_テキスト追加により縦長になる(self, tmp_path):
        """テキストをQR下部に追加するため正方形でなく縦長になる"""
        fp = tmp_path / "test.png"
        generate_qr("hello", fp)
        img = Image.open(str(fp))
        assert img.height > img.width

    def test_空文字列でも生成できる(self, tmp_path):
        fp = tmp_path / "empty.png"
        generate_qr("", fp)
        assert fp.exists()

    def test_日本語テキストでも生成できる(self, tmp_path):
        fp = tmp_path / "jp.png"
        generate_qr("日本語テスト", fp)
        assert fp.exists()


# ---------------------------------------------------------------------------
# バーコード生成
# ---------------------------------------------------------------------------

class TestGenerateBarcodeFile:
    def test_ファイルが生成される(self, tmp_path):
        result = generate_barcode_file("12345", tmp_path / "bar")
        assert result.exists()

    def test_有効なPNG画像である(self, tmp_path):
        result = generate_barcode_file("12345", tmp_path / "bar")
        img = Image.open(str(result))
        assert img.format == "PNG"

    def test_テキストが長いほどバーコードの幅が広くなる(self, tmp_path):
        short = generate_barcode_file("1", tmp_path / "s")
        long_ = generate_barcode_file("12345678901234567890", tmp_path / "l")
        assert Image.open(str(long_)).width > Image.open(str(short)).width

    def test_返り値はPathオブジェクトである(self, tmp_path):
        result = generate_barcode_file("12345", tmp_path / "bar")
        assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# ラベル切り詰め
# ---------------------------------------------------------------------------

class TestTruncateLabel:
    def test_25文字以下はそのまま返す(self):
        assert _truncate_label("a" * 25) == "a" * 25

    def test_26文字以上は25文字で切り詰めて省略記号を付ける(self):
        result = _truncate_label("a" * 26)
        assert result == "a" * 25 + "…"

    def test_空文字列はそのまま返す(self):
        assert _truncate_label("") == ""

    def test_日本語テキストも文字数で切り詰める(self):
        text = "あ" * 30
        result = _truncate_label(text)
        assert result == "あ" * 25 + "…"


# ---------------------------------------------------------------------------
# PDF グリッド出力
# ---------------------------------------------------------------------------

def _png_record(tmp_path: Path, text: str, filename: str = "test.png") -> dict:
    img_path = tmp_path / filename
    if not img_path.exists():
        Image.new("RGB", (200, 200), "white").save(str(img_path))
    return {"text": text, "type": "QR", "path": str(img_path)}


class TestGeneratePdfGrid:
    def test_ファイルが生成される(self, tmp_path):
        records = [_png_record(tmp_path, "hello")]
        output = tmp_path / "out.pdf"
        generate_pdf_grid(records, output)
        assert output.exists()

    def test_空リストのときファイルを生成しない(self, tmp_path):
        output = tmp_path / "out.pdf"
        generate_pdf_grid([], output)
        assert not output.exists()

    def test_有効なPDFである(self, tmp_path):
        records = [_png_record(tmp_path, "hello")]
        output = tmp_path / "out.pdf"
        generate_pdf_grid(records, output)
        assert output.read_bytes()[:4] == b"%PDF"

    def test_存在しない画像パスでもエラーにならない(self, tmp_path):
        records = [{"text": "hello", "type": "QR", "path": str(tmp_path / "missing.png")}]
        output = tmp_path / "out.pdf"
        generate_pdf_grid(records, output)
        assert output.exists()

    def test_12件超でファイルサイズが増加する(self, tmp_path):
        # cols=3, rows=4 → per_page=12 なので 13 件目で 2 ページ目に突入する
        img_path = tmp_path / "test.png"
        Image.new("RGB", (200, 200), "white").save(str(img_path))
        rec = {"text": "item", "type": "QR", "path": str(img_path)}

        out_1 = tmp_path / "one_page.pdf"
        out_2 = tmp_path / "two_page.pdf"
        generate_pdf_grid([rec] * 1, out_1)
        generate_pdf_grid([rec] * 13, out_2)
        assert out_2.stat().st_size > out_1.stat().st_size

    def test_25文字を超えるテキストでもエラーにならない(self, tmp_path):
        records = [_png_record(tmp_path, "a" * 50)]
        output = tmp_path / "out.pdf"
        generate_pdf_grid(records, output)
        assert output.exists()
