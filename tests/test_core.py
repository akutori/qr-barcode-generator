"""core.py のユニットテスト: ストレージ / 画像ユーティリティ / ラベル (t_wada 式 TDD)"""

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from core import (
    SORT_OPTION_LABELS,
    blank_image,
    calc_preview_size,
    find_index,
    fit_image,
    has_duplicate,
    img_to_bytes,
    list_labels,
    list_labels_with_status,
    load_metadata,
    load_settings,
    save_metadata,
    save_settings,
    sort_records,
)


# ---------------------------------------------------------------------------
# メタデータ
# ---------------------------------------------------------------------------

class TestLoadMetadata:
    def test_ファイルが存在しないとき空リストを返す(self, tmp_path):
        assert load_metadata(tmp_path / "meta.json") == []

    def test_保存したレコードをそのまま読み返せる(self, tmp_path):
        path = tmp_path / "meta.json"
        records = [{"text": "hello", "type": "Q", "path": "generated/qr.png"}]
        save_metadata(records, path)
        assert load_metadata(path) == records

    def test_日本語テキストが文字化けしない(self, tmp_path):
        path = tmp_path / "meta.json"
        records = [{"text": "日本語テスト", "type": "Q", "path": "qr.png"}]
        save_metadata(records, path)
        assert load_metadata(path)[0]["text"] == "日本語テスト"

    def test_複数レコードを保持できる(self, tmp_path):
        path = tmp_path / "meta.json"
        records = [
            {"text": "a", "type": "Q", "path": "qr.png"},
            {"text": "b", "type": "B", "path": "bar.png"},
        ]
        save_metadata(records, path)
        assert len(load_metadata(path)) == 2

    def test_破損したJSONは例外を送出する(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{ broken json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            load_metadata(path)


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

class TestLoadSettings:
    def test_ファイルが存在しないときデフォルト値を返す(self, tmp_path):
        settings = load_settings(tmp_path / "settings.json")
        assert settings["warn_on_duplicate"] is True

    def test_保存した設定をそのまま読み返せる(self, tmp_path):
        path = tmp_path / "settings.json"
        save_settings({"warn_on_duplicate": False}, path)
        assert load_settings(path)["warn_on_duplicate"] is False

    def test_ファイルに存在しないキーはデフォルト値で補完される(self, tmp_path):
        path = tmp_path / "settings.json"
        save_settings({}, path)
        assert load_settings(path)["warn_on_duplicate"] is True


# ---------------------------------------------------------------------------
# 重複チェック
# ---------------------------------------------------------------------------

class TestHasDuplicate:
    def test_同じテキストと種別が存在するときTrueを返す(self):
        records = [{"text": "hello", "type": "Q", "path": "qr.png"}]
        assert has_duplicate("hello", "Q", records) is True

    def test_テキストが異なるときFalseを返す(self):
        records = [{"text": "hello", "type": "Q", "path": "qr.png"}]
        assert has_duplicate("world", "Q", records) is False

    def test_種別が異なるときFalseを返す(self):
        records = [{"text": "hello", "type": "Q", "path": "qr.png"}]
        assert has_duplicate("hello", "B", records) is False

    def test_空リストはFalseを返す(self):
        assert has_duplicate("hello", "Q", []) is False

    def test_QRで同じテキストだが誤り訂正レベルが異なるときFalseを返す(self):
        records = [{"text": "hello", "type": "Q", "path": "qr.png", "error_correction": "M"}]
        assert has_duplicate("hello", "Q", records, error_correction="H") is False

    def test_QRで同じテキストと誤り訂正レベルが一致するときTrueを返す(self):
        records = [{"text": "hello", "type": "Q", "path": "qr.png", "error_correction": "M"}]
        assert has_duplicate("hello", "Q", records, error_correction="M") is True

    def test_誤り訂正レベルフィールドのない旧レコードは保守的にTrueを返す(self):
        """error_correction フィールドがない旧レコードはレベルを問わず重複と判定する"""
        records = [{"text": "hello", "type": "Q", "path": "qr.png"}]
        assert has_duplicate("hello", "Q", records, error_correction="H") is True


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

class TestListLabelsWithStatus:
    def test_ファイルが存在するレコードはそのまま返す(self, tmp_path):
        img = tmp_path / "qr.png"
        img.write_bytes(b"dummy")
        records = [{"text": "hello", "type": "Q", "path": str(img)}]
        assert list_labels_with_status(records) == ["[QR]  hello"]

    def test_ファイルが存在しないレコードには警告記号を付ける(self, tmp_path):
        records = [{"text": "hello", "type": "Q", "path": str(tmp_path / "missing.png")}]
        assert list_labels_with_status(records) == ["⚠[QR]  hello"]

    def test_存在するものと欠損が混在する場合に正しく区別する(self, tmp_path):
        existing = tmp_path / "ok.png"
        existing.write_bytes(b"dummy")
        records = [
            {"text": "ok", "type": "Q", "path": str(existing)},
            {"text": "missing", "type": "B", "path": str(tmp_path / "gone.png")},
        ]
        result = list_labels_with_status(records)
        assert result[0] == "[QR]  ok"
        assert result[1] == "⚠[Barcode]  missing"

    def test_空リストは空リストを返す(self):
        assert list_labels_with_status([]) == []

    def test_QRに誤り訂正レベルが含まれる場合は角括弧内に表示される(self, tmp_path):
        img = tmp_path / "qr.png"
        img.write_bytes(b"dummy")
        records = [{"text": "hello", "type": "Q", "path": str(img), "error_correction": "L"}]
        assert list_labels_with_status(records) == ["[QR:L]  hello"]

    def test_欠損レコードでもQRの誤り訂正レベルが表示される(self, tmp_path):
        records = [{"text": "hello", "type": "Q",
                    "path": str(tmp_path / "missing.png"), "error_correction": "H"}]
        assert list_labels_with_status(records) == ["⚠[QR:H]  hello"]

    def test_descriptionがある場合はえんぴつプレフィックスと説明文で表示される(self, tmp_path):
        img = tmp_path / "qr.png"
        img.write_bytes(b"dummy")
        records = [{"text": "https://example.com", "type": "Q",
                    "path": str(img), "description": "商品A"}]
        assert list_labels_with_status(records) == ["✎[QR]  商品A"]

    def test_descriptionがある場合にファイル欠損なら両方のプレフィックスが付く(self, tmp_path):
        records = [{"text": "hello", "type": "Q",
                    "path": str(tmp_path / "missing.png"), "description": "説明"}]
        assert list_labels_with_status(records) == ["⚠✎[QR]  説明"]

    def test_descriptionが空文字列ならプレフィックスなしでテキスト先頭行を表示(self, tmp_path):
        img = tmp_path / "qr.png"
        img.write_bytes(b"dummy")
        records = [{"text": "hello", "type": "Q",
                    "path": str(img), "description": ""}]
        assert list_labels_with_status(records) == ["[QR]  hello"]

    def test_改行付きテキストは先頭行のみ表示される(self, tmp_path):
        img = tmp_path / "qr.png"
        img.write_bytes(b"dummy")
        records = [{"text": "line1\nline2\nline3", "type": "Q", "path": str(img)}]
        assert list_labels_with_status(records) == ["[QR]  line1"]


class TestListLabels:
    def test_空リストは空リストを返す(self):
        assert list_labels([]) == []

    def test_型とテキストが角括弧形式でフォーマットされる(self):
        records = [
            {"text": "hello", "type": "Q", "path": "..."},
            {"text": "world", "type": "B", "path": "..."},
        ]
        assert list_labels(records) == ["[QR]  hello", "[Barcode]  world"]

    def test_QRに誤り訂正レベルが含まれる場合は角括弧内に表示される(self):
        records = [{"text": "hello", "type": "Q", "path": "...", "error_correction": "H"}]
        assert list_labels(records) == ["[QR:H]  hello"]

    def test_誤り訂正レベルのないQRは従来フォーマットで返す(self):
        records = [{"text": "hello", "type": "Q", "path": "..."}]
        assert list_labels(records) == ["[QR]  hello"]

    def test_descriptionがある場合はえんぴつプレフィックスと説明文で表示される(self):
        records = [{"text": "https://example.com", "type": "Q",
                    "path": "...", "description": "商品A"}]
        assert list_labels(records) == ["✎[QR]  商品A"]

    def test_改行付きテキストは先頭行のみ表示される(self):
        records = [{"text": "line1\nline2", "type": "Q", "path": "..."}]
        assert list_labels(records) == ["[QR]  line1"]


class TestFindIndex:
    def test_一致するラベルのインデックスを返す(self):
        records = [
            {"text": "a", "type": "Q", "path": "..."},
            {"text": "b", "type": "B", "path": "..."},
        ]
        assert find_index("[QR]  a", records) == 0
        assert find_index("[Barcode]  b", records) == 1

    def test_存在しないラベルはマイナス1を返す(self):
        records = [{"text": "hello", "type": "Q", "path": "..."}]
        assert find_index("[QR]  missing", records) == -1

    def test_空リストはマイナス1を返す(self):
        assert find_index("[QR]  hello", []) == -1

    def test_descriptionありのラベルで検索できる(self):
        records = [{"text": "https://example.com", "type": "Q",
                    "path": "...", "description": "商品A"}]
        assert find_index("✎[QR]  商品A", records) == 0

    def test_ファイル欠損プレフィックス付きでも検索できる(self):
        records = [{"text": "hello", "type": "Q", "path": "..."}]
        assert find_index("⚠[QR]  hello", records) == 0

    def test_description付きファイル欠損プレフィックスでも検索できる(self):
        records = [{"text": "hello", "type": "Q", "path": "...", "description": "説明"}]
        assert find_index("⚠✎[QR]  説明", records) == 0


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


# ---------------------------------------------------------------------------
# ソート
# ---------------------------------------------------------------------------

class TestSortRecords:
    @pytest.fixture
    def sample_records(self):
        return [
            {"text": "charlie", "type": "Q", "path": "...", "description": ""},
            {"text": "alice",   "type": "B", "path": "...", "description": "zzz"},
            {"text": "bob",     "type": "Q", "path": "...", "description": "aaa"},
        ]

    def test_追加日新しい順はindicesが逆順になる(self, sample_records):
        indices = [0, 1, 2]
        result = sort_records(sample_records, indices, "date_new")
        assert result == [2, 1, 0]

    def test_追加日古い順はindicesがそのまま(self, sample_records):
        indices = [0, 1, 2]
        result = sort_records(sample_records, indices, "date_old")
        assert result == [0, 1, 2]

    def test_表示名昇順はdisplay_text昇順(self, sample_records):
        # _item_label: 0="[QR]  charlie", 1="✎[Barcode]  zzz", 2="✎[QR]  aaa"
        # "[" (U+005B) < "✎" (U+270E) → "[QR]..." が最小
        # "✎[barcode]..." < "✎[qr]..." (lower: "b" < "q")
        # 昇順: [0, 1, 2]
        indices = [0, 1, 2]
        result = sort_records(sample_records, indices, "label_az")
        assert result == [0, 1, 2]

    def test_表示名降順はdisplay_text降順(self, sample_records):
        # 降順: "✎[QR]  aaa" > "✎[Barcode]  zzz" > "[QR]  charlie"
        # → [2, 1, 0]
        indices = [0, 1, 2]
        result = sort_records(sample_records, indices, "label_za")
        assert result == [2, 1, 0]

    def test_テキスト昇順はtext昇順(self, sample_records):
        # alice < bob < charlie
        indices = [0, 1, 2]
        result = sort_records(sample_records, indices, "text_az")
        assert result == [1, 2, 0]

    def test_テキスト降順はtext降順(self, sample_records):
        # charlie > bob > alice
        indices = [0, 1, 2]
        result = sort_records(sample_records, indices, "text_za")
        assert result == [0, 2, 1]

    def test_説明昇順は空欄が末尾(self, sample_records):
        # desc: 0="" (空), 1="zzz", 2="aaa"
        # 昇順: aaa < zzz < "" (空欄末尾)
        indices = [0, 1, 2]
        result = sort_records(sample_records, indices, "desc_az")
        assert result == [2, 1, 0]

    def test_説明降順は空欄が先頭(self, sample_records):
        # 降順: "" (空欄先頭) > zzz > aaa
        indices = [0, 1, 2]
        result = sort_records(sample_records, indices, "desc_za")
        assert result == [0, 1, 2]

    def test_種別QR先はQRが先でBarcodeが後(self, sample_records):
        # type: 0=Q, 1=B, 2=Q
        # QR先 = QR(Q)が上(先頭), B が下
        indices = [0, 1, 2]
        result = sort_records(sample_records, indices, "type_qr")
        assert result[-1] == 1  # Barcode が末尾
        assert set(result[:2]) == {0, 2}  # QR が先頭側

    def test_種別Barcode先はBarcodeが先でQRが後(self, sample_records):
        # type: 0=Q, 1=B, 2=Q
        # Barcode先 = Bが上(先頭), Q が下
        indices = [0, 1, 2]
        result = sort_records(sample_records, indices, "type_bc")
        assert result[0] == 1  # Barcode が先頭
        assert set(result[1:]) == {0, 2}  # QR が後

    def test_不明なキーはdate_newと同じ(self, sample_records):
        indices = [0, 1, 2]
        result = sort_records(sample_records, indices, "unknown_key")
        assert result == [2, 1, 0]

    def test_空リストは空リストを返す(self):
        assert sort_records([], [], "date_new") == []

    def test_フィルタ済みindicesでも正しくソート(self, sample_records):
        # indices=[0, 2] のみ (index1 はフィルタで除外済み)
        # テキスト A→Z: charlie, bob → bob(2) < charlie(0)
        indices = [0, 2]
        result = sort_records(sample_records, indices, "text_az")
        assert result == [2, 0]

    def test_SORT_OPTION_LABELSに期待するキーとラベルが含まれる(self):
        assert list(SORT_OPTION_LABELS.keys()) == [
            "date_new", "date_old",
            "label_az", "label_za",
            "text_az", "text_za",
            "desc_az", "desc_za",
            "type_qr", "type_bc",
        ]
        assert SORT_OPTION_LABELS["date_new"] == "追加日 新しい順"
        assert SORT_OPTION_LABELS["type_bc"] == "種別 Barcode先"
