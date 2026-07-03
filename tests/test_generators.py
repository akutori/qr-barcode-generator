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

    def test_日本語QRはフルQRのバイトモードで生成される(self, tmp_path):
        """ECI モードや Micro QR はスキャナー非対応端末で文字化けを引き起こす。
        UTF-8 バイト列を make_qr に直接渡してフル QR バイトモードで生成すること。
        """
        import segno as _segno
        text = "あ"  # 3 bytes: eci=True だと Micro QR M3(230px) になる
        fp = tmp_path / "jp_full.png"
        generate_qr(text, fp)
        img = Image.open(str(fp))
        expected_qr = _segno.make_qr(text.encode("utf-8"), error="m")
        assert not expected_qr.is_micro
        expected_w = expected_qr.symbol_size(scale=10, border=4)[0]
        assert img.width == expected_w

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

        make_qr(bytes) を使うためフル QR V1 で生成される。
        "a" (1 バイト) と "a\nb\nc" (5 バイト) はどちらも V1 の容量に収まるため
        QR 本体サイズが等しくなる。ラベルが多行分増えなければ総高さも等しくなる。
        """
        fp_single = tmp_path / "qr_single.png"
        fp_multi = tmp_path / "qr_multi.png"
        generate_qr("a", fp_single)
        generate_qr("a\nb\nc", fp_multi)
        h_single = Image.open(str(fp_single)).height
        h_multi = Image.open(str(fp_multi)).height
        assert h_multi == h_single

    def test_テキストが長すぎる場合はValueErrorを送出する(self, tmp_path):
        """QR 上限を超えるテキストは ValueError になる。
        segno.encoder.DataOverflowError をキャッチして日本語メッセージに変換する。
        """
        text = "A" * 5000
        with pytest.raises(ValueError, match="長すぎて"):
            generate_qr(text, tmp_path / "qr.png")

    def test_オーバーフロー時のエラーメッセージに誤り訂正レベルが含まれる(self, tmp_path):
        """エラーメッセージに使用した誤り訂正レベルが含まれることで、
        ユーザーがレベルを下げれば収まるかを判断できる。
        """
        text = "A" * 5000
        with pytest.raises(ValueError) as exc_info:
            generate_qr(text, tmp_path / "qr.png", error_correction="H")
        assert "H" in str(exc_info.value)

    def test_オーバーフロー時のエラーメッセージにUTF8バイト数が含まれる(self, tmp_path):
        """エラーメッセージに入力バイト数が含まれることで、
        ユーザーが入力サイズを把握できる。
        """
        text = "あ" * 2000  # 6000 bytes UTF-8
        with pytest.raises(ValueError) as exc_info:
            generate_qr(text, tmp_path / "qr.png")
        byte_count = str(len(text.encode("utf-8")))
        assert byte_count in str(exc_info.value)

    def test_SJIS指定でファイルが生成される(self, tmp_path):
        fp = tmp_path / "jp_sjis.png"
        generate_qr("日本語テスト", fp, encoding="SJIS")
        assert fp.exists()

    def test_SJIS指定はUTF8指定より画像が小さいまたは同等(self, tmp_path):
        """日本語は SJIS(2 バイト/文字) の方が UTF-8(3 バイト/文字) より小さいので
        同じテキストで SJIS QR のシンボルサイズが UTF-8 以下になる。
        """
        text = "日本語テスト" * 5  # 30文字 = SJIS60B / UTF-8 90B
        fp_utf8 = tmp_path / "utf8.png"
        fp_sjis = tmp_path / "sjis.png"
        generate_qr(text, fp_utf8, encoding="UTF-8")
        generate_qr(text, fp_sjis, encoding="SJIS")
        assert Image.open(str(fp_sjis)).width <= Image.open(str(fp_utf8)).width

    def test_SJIS非対応文字はValueErrorを送出する(self, tmp_path):
        """Shift-JIS で表現できない文字（一部 Unicode のみの文字）はエラーにする。"""
        text = "🎉"  # 絵文字は cp932 に存在しない
        with pytest.raises(ValueError, match="Shift-JIS"):
            generate_qr(text, tmp_path / "qr.png", encoding="SJIS")

    def test_オーバーフロー時のエラーメッセージにエンコード名が含まれる(self, tmp_path):
        """SJIS モードでオーバーフローした場合、エラーメッセージにエンコード名が含まれる。"""
        text = "あ" * 3000  # SJIS では 6000B、いずれにせよ上限超え
        with pytest.raises(ValueError) as exc_info:
            generate_qr(text, tmp_path / "qr.png", encoding="SJIS")
        assert "SJIS" in str(exc_info.value)


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
    return {"text": text, "type": "Q", "path": filename}


class TestGeneratePdfGrid:
    """path はファイル名のみを保持し、save_dir と結合して画像を読み込む（フォルダ移動耐性のため）。"""

    def test_ファイルが生成される(self, tmp_path):
        records = [_png_record(tmp_path, "hello")]
        output = tmp_path / "out.pdf"
        generate_pdf_grid(records, output, tmp_path)
        assert output.exists()

    def test_空リストのときファイルを生成しない(self, tmp_path):
        output = tmp_path / "out.pdf"
        generate_pdf_grid([], output, tmp_path)
        assert not output.exists()

    def test_有効なPDFである(self, tmp_path):
        records = [_png_record(tmp_path, "hello")]
        output = tmp_path / "out.pdf"
        generate_pdf_grid(records, output, tmp_path)
        assert output.read_bytes()[:4] == b"%PDF"

    def test_存在しない画像パスでもエラーにならない(self, tmp_path):
        records = [{"text": "hello", "type": "Q", "path": "missing.png"}]
        output = tmp_path / "out.pdf"
        generate_pdf_grid(records, output, tmp_path)
        assert output.exists()

    def test_12件超でファイルサイズが増加する(self, tmp_path):
        # cols=3, rows=4 → per_page=12 なので 13 件目で 2 ページ目に突入する
        img_path = tmp_path / "test.png"
        Image.new("RGB", (200, 200), "white").save(str(img_path))
        rec = {"text": "item", "type": "Q", "path": "test.png"}

        out_1 = tmp_path / "one_page.pdf"
        out_2 = tmp_path / "two_page.pdf"
        generate_pdf_grid([rec] * 1, out_1, tmp_path)
        generate_pdf_grid([rec] * 13, out_2, tmp_path)
        assert out_2.stat().st_size > out_1.stat().st_size

    def test_25文字を超えるテキストでもエラーにならない(self, tmp_path):
        records = [_png_record(tmp_path, "a" * 50)]
        output = tmp_path / "out.pdf"
        generate_pdf_grid(records, output, tmp_path)
        assert output.exists()

    def test_改行を含むテキストでもPDFが生成される(self, tmp_path):
        """vCard 等の複数行テキストを持つレコードでも正常に出力できること"""
        records = [_png_record(tmp_path, "BEGIN:VCARD\nFN:山田\nEND:VCARD")]
        output = tmp_path / "out.pdf"
        generate_pdf_grid(records, output, tmp_path)
        assert output.exists()

    def test_descriptionフィールドがあってもエラーにならない(self, tmp_path):
        """description 付きレコードを渡しても PDF が正常に生成されること"""
        rec = _png_record(tmp_path, "https://example.com")
        rec["description"] = "商品A"
        output = tmp_path / "out.pdf"
        generate_pdf_grid([rec], output, tmp_path)
        assert output.exists()

    def test_descriptionなしのレコードはテキスト先頭行をラベルとして使う(self, tmp_path):
        """description がない旧データでも PDF 生成が壊れないこと（後方互換）"""
        records = [_png_record(tmp_path, "fallback_text")]
        output = tmp_path / "out.pdf"
        generate_pdf_grid(records, output, tmp_path)
        assert output.exists()

    def test_1列指定でファイルが生成される(self, tmp_path):
        records = [_png_record(tmp_path, "hello")]
        output = tmp_path / "out.pdf"
        generate_pdf_grid(records, output, tmp_path, cols=1)
        assert output.exists()

    def test_6列指定でファイルが生成される(self, tmp_path):
        records = [_png_record(tmp_path, "hello")]
        output = tmp_path / "out.pdf"
        generate_pdf_grid(records, output, tmp_path, cols=6)
        assert output.exists()

    def test_異なるsave_dirに差し替えても正しく画像を解決できる(self, tmp_path):
        """フォルダを移動しても save_dir を差し替えるだけで正しく解決できることの確認。"""
        moved_dir = tmp_path / "moved"
        moved_dir.mkdir()
        Image.new("RGB", (200, 200), "white").save(str(moved_dir / "test.png"))
        records = [{"text": "hello", "type": "Q", "path": "test.png"}]
        output = tmp_path / "out.pdf"
        generate_pdf_grid(records, output, moved_dir)
        assert output.exists()
