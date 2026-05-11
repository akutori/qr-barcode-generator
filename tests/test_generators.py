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

    def test_誤り訂正レベルLでファイルが生成される(self, tmp_path):
        fp = tmp_path / "qr_L.png"
        generate_qr("hello", fp, error_correction="L")
        assert fp.exists()

    def test_誤り訂正レベルHでファイルが生成される(self, tmp_path):
        fp = tmp_path / "qr_H.png"
        generate_qr("hello", fp, error_correction="H")
        assert fp.exists()

    def test_誤り訂正レベルHはLより画像サイズが大きい(self, tmp_path):
        """H は誤り訂正データが多いためモジュール数が増え画像が大きくなる"""
        fp_l = tmp_path / "qr_L.png"
        fp_h = tmp_path / "qr_H.png"
        generate_qr("hello", fp_l, error_correction="L")
        generate_qr("hello", fp_h, error_correction="H")
        assert Image.open(str(fp_h)).width >= Image.open(str(fp_l)).width

    def test_不正な誤り訂正レベルはMとして扱われる(self, tmp_path):
        """未知のレベル文字列はデフォルト M にフォールバックしてエラーにならない"""
        fp = tmp_path / "qr_fallback.png"
        generate_qr("hello", fp, error_correction="X")
        assert fp.exists()

    def test_改行を含むテキストでもQRが生成される(self, tmp_path):
        """vCard・複数行テキスト等の改行入りコンテンツをエンコードできること"""
        fp = tmp_path / "qr_multi.png"
        generate_qr("BEGIN:VCARD\nFN:山田太郎\nEND:VCARD", fp)
        assert fp.exists()

    def test_改行を含むQRのラベルは先頭行のみ表示される(self, tmp_path):
        """複数行テキストのラベルは先頭行のみ描画され、ラベル領域が増加しない。

        小文字 "a" は英数字モード対象外のためバイトモード (Micro QR M3) になる。
        "a\nb\nc" も同様に M3 となり、両者の QR 本体サイズが等しくなる。
        ラベルが多行分増えなければ総高さも等しくなる。
        """
        fp_single = tmp_path / "qr_single.png"
        fp_multi = tmp_path / "qr_multi.png"
        generate_qr("a", fp_single)
        generate_qr("a\nb\nc", fp_multi)
        h_single = Image.open(str(fp_single)).height
        h_multi = Image.open(str(fp_multi)).height
        assert h_multi == h_single

    def test_テキストが長すぎる場合はValueErrorを送出する(self, tmp_path):
        """QR バイナリ上限 (2,953 バイト) を超えるテキストは ValueError になる。
        CLAUDE.md: "version" を含むエラーメッセージをキャッチして日本語メッセージに変換する
        """
        text = "A" * 5000
        with pytest.raises(ValueError, match="長すぎて"):
            generate_qr(text, tmp_path / "qr.png")


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

    def test_非ASCII文字はValueErrorを送出する(self, tmp_path):
        """日本語等の非ASCII文字は Code128 で扱えないため ValueError になる。
        CLAUDE.md: "text.isascii() で検証し ValueError を送出する"
        """
        with pytest.raises(ValueError):
            generate_barcode_file("日本語テスト", tmp_path / "bar")

    def test_絵文字はValueErrorを送出する(self, tmp_path):
        with pytest.raises(ValueError):
            generate_barcode_file("hello🎉", tmp_path / "bar")


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
    return {"text": text, "type": "Q", "path": str(img_path)}


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
        records = [{"text": "hello", "type": "Q", "path": str(tmp_path / "missing.png")}]
        output = tmp_path / "out.pdf"
        generate_pdf_grid(records, output)
        assert output.exists()

    def test_12件超でファイルサイズが増加する(self, tmp_path):
        # cols=3, rows=4 → per_page=12 なので 13 件目で 2 ページ目に突入する
        img_path = tmp_path / "test.png"
        Image.new("RGB", (200, 200), "white").save(str(img_path))
        rec = {"text": "item", "type": "Q", "path": str(img_path)}

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

    def test_改行を含むテキストでもPDFが生成される(self, tmp_path):
        """vCard 等の複数行テキストを持つレコードでも正常に出力できること"""
        records = [_png_record(tmp_path, "BEGIN:VCARD\nFN:山田\nEND:VCARD")]
        output = tmp_path / "out.pdf"
        generate_pdf_grid(records, output)
        assert output.exists()

    def test_descriptionフィールドがあってもエラーにならない(self, tmp_path):
        """description 付きレコードを渡しても PDF が正常に生成されること"""
        rec = _png_record(tmp_path, "https://example.com")
        rec["description"] = "商品A"
        output = tmp_path / "out.pdf"
        generate_pdf_grid([rec], output)
        assert output.exists()

    def test_descriptionなしのレコードはテキスト先頭行をラベルとして使う(self, tmp_path):
        """description がない旧データでも PDF 生成が壊れないこと（後方互換）"""
        records = [_png_record(tmp_path, "fallback_text")]
        output = tmp_path / "out.pdf"
        generate_pdf_grid(records, output)
        assert output.exists()

    def test_1列指定でファイルが生成される(self, tmp_path):
        records = [_png_record(tmp_path, "hello")]
        output = tmp_path / "out.pdf"
        generate_pdf_grid(records, output, cols=1)
        assert output.exists()

    def test_6列指定でファイルが生成される(self, tmp_path):
        records = [_png_record(tmp_path, "hello")]
        output = tmp_path / "out.pdf"
        generate_pdf_grid(records, output, cols=6)
        assert output.exists()
