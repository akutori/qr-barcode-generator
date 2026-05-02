"""core.py のユニットテスト (t_wada 式 TDD)"""

import io
from pathlib import Path

import pytest
from PIL import Image

from core import (
    blank_image,
    calc_preview_size,
    find_index,
    fit_image,
    generate_barcode_file,
    generate_qr,
    img_to_bytes,
    list_labels,
    load_metadata,
    save_metadata,
)


# ---------------------------------------------------------------------------
# メタデータ
# ---------------------------------------------------------------------------

class TestLoadMetadata:
    def test_ファイルが存在しないとき空リストを返す(self, tmp_path):
        assert load_metadata(tmp_path / "meta.json") == []

    def test_保存したレコードをそのまま読み返せる(self, tmp_path):
        path = tmp_path / "meta.json"
        records = [{"text": "hello", "type": "QR", "path": "generated/qr.png"}]
        save_metadata(records, path)
        assert load_metadata(path) == records

    def test_日本語テキストが文字化けしない(self, tmp_path):
        path = tmp_path / "meta.json"
        records = [{"text": "日本語テスト", "type": "QR", "path": "qr.png"}]
        save_metadata(records, path)
        assert load_metadata(path)[0]["text"] == "日本語テスト"

    def test_複数レコードを保持できる(self, tmp_path):
        path = tmp_path / "meta.json"
        records = [
            {"text": "a", "type": "QR", "path": "qr.png"},
            {"text": "b", "type": "Barcode", "path": "bar.png"},
        ]
        save_metadata(records, path)
        assert len(load_metadata(path)) == 2


# ---------------------------------------------------------------------------
# 画像ユーティリティ
# ---------------------------------------------------------------------------

class TestBlankImage:
    def test_指定サイズのPNG画像を返す(self):
        data = blank_image(100, 200)
        img = Image.open(io.BytesIO(data))
        assert img.size == (100, 200)
        assert img.format == "PNG"


class TestFitImage:
    def test_出力は常に指定サイズになる(self, tmp_path):
        src = tmp_path / "src.png"
        Image.new("RGB", (400, 300), "red").save(str(src))

        data = fit_image(str(src), 200, 200)

        img = Image.open(io.BytesIO(data))
        assert img.size == (200, 200)

    def test_横長画像は上下に白パディングが付く(self, tmp_path):
        src = tmp_path / "src.png"
        Image.new("RGB", (400, 100), "blue").save(str(src))  # 4:1 比率

        data = fit_image(str(src), 200, 200)

        img = Image.open(io.BytesIO(data))
        assert img.getpixel((100, 0)) == (255, 255, 255)   # 上端 = 白
        assert img.getpixel((100, 100)) == (0, 0, 255)     # 中央 = 青

    def test_縦長画像は左右に白パディングが付く(self, tmp_path):
        src = tmp_path / "src.png"
        Image.new("RGB", (100, 400), "red").save(str(src))  # 1:4 比率

        data = fit_image(str(src), 200, 200)

        img = Image.open(io.BytesIO(data))
        assert img.getpixel((0, 100)) == (255, 255, 255)   # 左端 = 白
        assert img.getpixel((100, 100)) == (255, 0, 0)     # 中央 = 赤


# ---------------------------------------------------------------------------
# ラベルユーティリティ
# ---------------------------------------------------------------------------

class TestListLabels:
    def test_空リストは空リストを返す(self):
        assert list_labels([]) == []

    def test_型とテキストが角括弧形式でフォーマットされる(self):
        records = [
            {"text": "hello", "type": "QR", "path": "..."},
            {"text": "world", "type": "Barcode", "path": "..."},
        ]
        assert list_labels(records) == ["[QR]  hello", "[Barcode]  world"]


class TestFindIndex:
    def test_一致するラベルのインデックスを返す(self):
        records = [
            {"text": "a", "type": "QR", "path": "..."},
            {"text": "b", "type": "Barcode", "path": "..."},
        ]
        assert find_index("[QR]  a", records) == 0
        assert find_index("[Barcode]  b", records) == 1

    def test_存在しないラベルはマイナス1を返す(self):
        records = [{"text": "hello", "type": "QR", "path": "..."}]
        assert find_index("[QR]  missing", records) == -1

    def test_空リストはマイナス1を返す(self):
        assert find_index("[QR]  hello", []) == -1


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
# プレビューサイズ計算
# (バグ再発防止: プレビュー切り替え時にウィンドウが増大してはならない)
# ---------------------------------------------------------------------------

class TestCalcPreviewSize:
    def test_通常サイズのウィンドウでは利用可能幅を返す(self):
        pw, ph = calc_preview_size(win_w=1000, win_h=700, left_panel_w=350)
        assert pw == 630   # 1000 - 350 - 20
        assert ph == 620   # 700 - 80

    def test_プレビュー幅はウィンドウからはみ出さない(self):
        """バグ再発防止: この条件が崩れるとウィンドウが増大し続ける"""
        win_w, left_panel_w = 1000, 350
        pw, _ = calc_preview_size(win_w=win_w, win_h=700, left_panel_w=left_panel_w)
        assert pw + left_panel_w + 20 <= win_w

    def test_ウィンドウが極端に小さくても最小200pxを保証する(self):
        pw, ph = calc_preview_size(win_w=100, win_h=100, left_panel_w=350)
        assert pw >= 200
        assert ph >= 200

    def test_同じウィンドウサイズで何度呼んでも同じ値を返す(self):
        """純粋関数であること: 副作用がなく冪等である"""
        args = (1000, 700, 350)
        assert calc_preview_size(*args) == calc_preview_size(*args)

    def test_ウィンドウが大きいほどプレビューも大きくなる(self):
        small_pw, _ = calc_preview_size(win_w=800, win_h=600, left_panel_w=350)
        large_pw, _ = calc_preview_size(win_w=1200, win_h=600, left_panel_w=350)
        assert large_pw > small_pw
