"""core.py のユニットテスト: ストレージ / ラベル (t_wada 式 TDD)"""

import json
from pathlib import Path

import pytest

from core import (
    SORT_OPTION_LABELS,
    apply_custom_order,
    calc_preview_size,
    find_index,
    has_duplicate,
    list_labels,
    list_labels_with_status,
    load_metadata,
    load_settings,
    move_index,
    save_metadata,
    save_settings,
    sort_records,
    suggested_filename,
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

    def test_同じテキストでエンコードが異なる場合はDuplicateにならない(self):
        """UTF-8 と SJIS は異なる QR なので重複にならない"""
        records = [{"text": "日本語", "type": "Q", "path": "qr.png",
                    "error_correction": "M", "encoding": "UTF-8"}]
        assert has_duplicate("日本語", "Q", records,
                             error_correction="M", encoding="SJIS") is False

    def test_同じテキストとエンコードが一致する場合はDuplicate(self):
        records = [{"text": "日本語", "type": "Q", "path": "qr.png",
                    "error_correction": "M", "encoding": "SJIS"}]
        assert has_duplicate("日本語", "Q", records,
                             error_correction="M", encoding="SJIS") is True

    def test_encodingフィールドなし旧レコードはUTF8として扱い一致する場合はDuplicate(self):
        """encoding フィールドのない旧レコードは UTF-8 として扱う"""
        records = [{"text": "hello", "type": "Q", "path": "qr.png",
                    "error_correction": "M"}]
        assert has_duplicate("hello", "Q", records,
                             error_correction="M", encoding="UTF-8") is True

    def test_encodingフィールドなし旧レコードはUTF8として扱いSJIS指定では一致しない(self):
        """encoding フィールドのない旧レコードは UTF-8 扱いなので SJIS とは重複しない"""
        records = [{"text": "hello", "type": "Q", "path": "qr.png",
                    "error_correction": "M"}]
        assert has_duplicate("hello", "Q", records,
                             error_correction="M", encoding="SJIS") is False


# ---------------------------------------------------------------------------
# ラベルユーティリティ
# ---------------------------------------------------------------------------

class TestListLabelsWithStatus:
    """path はファイル名のみを保持し、save_dir と結合して存在確認する（フォルダ移動耐性のため）。"""

    def test_ファイルが存在するレコードはそのまま返す(self, tmp_path):
        (tmp_path / "qr.png").write_bytes(b"dummy")
        records = [{"text": "hello", "type": "Q", "path": "qr.png"}]
        assert list_labels_with_status(records, tmp_path) == ["[QR]  hello"]

    def test_ファイルが存在しないレコードには警告記号を付ける(self, tmp_path):
        records = [{"text": "hello", "type": "Q", "path": "missing.png"}]
        assert list_labels_with_status(records, tmp_path) == ["⚠[QR]  hello"]

    def test_存在するものと欠損が混在する場合に正しく区別する(self, tmp_path):
        (tmp_path / "ok.png").write_bytes(b"dummy")
        records = [
            {"text": "ok", "type": "Q", "path": "ok.png"},
            {"text": "missing", "type": "B", "path": "gone.png"},
        ]
        result = list_labels_with_status(records, tmp_path)
        assert result[0] == "[QR]  ok"
        assert result[1] == "⚠[Barcode]  missing"

    def test_空リストは空リストを返す(self, tmp_path):
        assert list_labels_with_status([], tmp_path) == []

    def test_QRに誤り訂正レベルが含まれる場合は角括弧内に表示される(self, tmp_path):
        (tmp_path / "qr.png").write_bytes(b"dummy")
        records = [{"text": "hello", "type": "Q", "path": "qr.png", "error_correction": "L"}]
        assert list_labels_with_status(records, tmp_path) == ["[QR:L]  hello"]

    def test_欠損レコードでもQRの誤り訂正レベルが表示される(self, tmp_path):
        records = [{"text": "hello", "type": "Q",
                    "path": "missing.png", "error_correction": "H"}]
        assert list_labels_with_status(records, tmp_path) == ["⚠[QR:H]  hello"]

    def test_descriptionがある場合はえんぴつプレフィックスと説明文で表示される(self, tmp_path):
        (tmp_path / "qr.png").write_bytes(b"dummy")
        records = [{"text": "https://example.com", "type": "Q",
                    "path": "qr.png", "description": "商品A"}]
        assert list_labels_with_status(records, tmp_path) == ["✎[QR]  商品A"]

    def test_descriptionがある場合にファイル欠損なら両方のプレフィックスが付く(self, tmp_path):
        records = [{"text": "hello", "type": "Q",
                    "path": "missing.png", "description": "説明"}]
        assert list_labels_with_status(records, tmp_path) == ["⚠✎[QR]  説明"]

    def test_descriptionが空文字列ならプレフィックスなしでテキスト先頭行を表示(self, tmp_path):
        (tmp_path / "qr.png").write_bytes(b"dummy")
        records = [{"text": "hello", "type": "Q",
                    "path": "qr.png", "description": ""}]
        assert list_labels_with_status(records, tmp_path) == ["[QR]  hello"]

    def test_異なるsave_dirに同じファイル名があっても正しく解決される(self, tmp_path):
        """フォルダを移動しても save_dir を差し替えるだけで正しく解決できることの確認。"""
        moved_dir = tmp_path / "moved"
        moved_dir.mkdir()
        (moved_dir / "qr.png").write_bytes(b"dummy")
        records = [{"text": "hello", "type": "Q", "path": "qr.png"}]
        assert list_labels_with_status(records, moved_dir) == ["[QR]  hello"]

    def test_改行付きテキストは先頭行のみ表示される(self, tmp_path):
        (tmp_path / "qr.png").write_bytes(b"dummy")
        records = [{"text": "line1\nline2\nline3", "type": "Q", "path": "qr.png"}]
        assert list_labels_with_status(records, tmp_path) == ["[QR]  line1"]


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

    def test_SJISレコードのtype_labelはSJISサフィックスが付く(self):
        records = [{"text": "日本語", "type": "Q", "path": "...",
                    "error_correction": "M", "encoding": "SJIS"}]
        assert list_labels(records) == ["[QR:M:SJIS]  日本語"]

    def test_UTF8レコードのtype_labelにSJISサフィックスは付かない(self):
        records = [{"text": "日本語", "type": "Q", "path": "...",
                    "error_correction": "M", "encoding": "UTF-8"}]
        assert list_labels(records) == ["[QR:M]  日本語"]

    def test_encodingフィールドなし旧レコードはSJISサフィックスなし(self):
        """encoding フィールドのない旧レコードは UTF-8 扱いでサフィックスなし"""
        records = [{"text": "hello", "type": "Q", "path": "...",
                    "error_correction": "M"}]
        assert list_labels(records) == ["[QR:M]  hello"]

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
            "custom",
        ]
        assert SORT_OPTION_LABELS["date_new"] == "追加日 新しい順"
        assert SORT_OPTION_LABELS["type_bc"] == "種別 Barcode先"
        assert SORT_OPTION_LABELS["custom"] == "カスタム順"

    def test_カスタム順はorderフィールドの昇順(self, sample_records):
        sample_records[0]["order"] = 2
        sample_records[1]["order"] = 0
        sample_records[2]["order"] = 1
        indices = [0, 1, 2]
        result = sort_records(sample_records, indices, "custom")
        assert result == [1, 2, 0]

    def test_カスタム順でorderフィールドのない旧レコードはインデックス値にフォールバックする(self, sample_records):
        indices = [0, 1, 2]
        result = sort_records(sample_records, indices, "custom")
        assert result == [0, 1, 2]

    def test_カスタム順でorderありなしが混在する場合(self, sample_records):
        sample_records[0]["order"] = 5
        indices = [0, 1, 2]
        result = sort_records(sample_records, indices, "custom")
        assert result == [1, 2, 0]  # order: 1, 2, 5 の昇順（1,2はインデックス値フォールバック）


# ---------------------------------------------------------------------------
# カスタム並び替え（ドラッグ&ドロップ）
# ---------------------------------------------------------------------------

class TestMoveIndex:
    def test_先頭要素を末尾へ移動する(self):
        assert move_index([0, 1, 2, 3], 0, 3) == [1, 2, 3, 0]

    def test_末尾要素を先頭へ移動する(self):
        assert move_index([0, 1, 2, 3], 3, 0) == [3, 0, 1, 2]

    def test_中間要素を別の中間位置へ移動する(self):
        assert move_index([0, 1, 2, 3, 4], 1, 3) == [0, 2, 3, 1, 4]

    def test_同じ位置への移動は変化しない(self):
        assert move_index([0, 1, 2], 1, 1) == [0, 1, 2]

    def test_元のリストを変更しない(self):
        original = [0, 1, 2, 3]
        move_index(original, 0, 2)
        assert original == [0, 1, 2, 3]

    def test_空リストは空リストを返す(self):
        assert move_index([], 0, 0) == []

    def test_from_posが範囲外なら現状維持で返す(self):
        assert move_index([0, 1, 2], 5, 0) == [0, 1, 2]

    def test_to_posが範囲を超える場合は末尾にクランプされる(self):
        assert move_index([0, 1, 2], 0, 99) == [1, 2, 0]

    def test_単一要素のリストは変化しない(self):
        assert move_index([0], 0, 0) == [0]


class TestApplyCustomOrder:
    def test_ordered_indicesの順にorderフィールドが0から振られる(self):
        records = [{"text": "a"}, {"text": "b"}, {"text": "c"}]
        apply_custom_order(records, [2, 0, 1])
        assert records[2]["order"] == 0
        assert records[0]["order"] == 1
        assert records[1]["order"] == 2

    def test_records自体は破壊的に変更される(self):
        records = [{"text": "a"}, {"text": "b"}]
        apply_custom_order(records, [1, 0])
        assert "order" in records[0]
        assert "order" in records[1]

    def test_ordered_indicesに含まれないレコードのorderは変更しない(self):
        records = [{"text": "a", "order": 99}, {"text": "b"}]
        apply_custom_order(records, [1])
        assert records[0]["order"] == 99
        assert records[1]["order"] == 0

    def test_空リストは何もしない(self):
        records = [{"text": "a"}]
        apply_custom_order(records, [])
        assert "order" not in records[0]

    def test_既存のorder値を上書きする(self):
        records = [{"text": "a", "order": 5}, {"text": "b", "order": 3}]
        apply_custom_order(records, [0, 1])
        assert records[0]["order"] == 0
        assert records[1]["order"] == 1


# ---------------------------------------------------------------------------
# 保存ダイアログ用ファイル名
# ---------------------------------------------------------------------------

class TestSuggestedFilename:
    def test_descriptionがあればdescriptionを使う(self):
        r = {"text": "https://example.com", "path": "qr_1.png", "description": "商品A"}
        assert suggested_filename(r) == "商品A.png"

    def test_descriptionがなければtext先頭行を使う(self):
        r = {"text": "hello\nworld", "path": "qr_1.png"}
        assert suggested_filename(r) == "hello.png"

    def test_拡張子は元のpathから引き継ぐ(self):
        r = {"text": "hello", "path": "bar_1.jpg"}
        assert suggested_filename(r) == "hello.jpg"

    def test_拡張子がない元pathはpngにフォールバックする(self):
        r = {"text": "hello", "path": "no_extension"}
        assert suggested_filename(r) == "hello.png"

    def test_ファイル名に使えない文字はアンダースコアに置換される(self):
        r = {"text": "https://example.com/path?x=1", "path": "qr_1.png"}
        result = suggested_filename(r)
        assert result == "https___example.com_path_x=1.png"

    def test_置換後も不正文字が残らない(self):
        r = {"text": 'a\\b/c:d*e?f"g<h>i|j', "path": "qr_1.png"}
        result = suggested_filename(r)
        for ch in '\\/:*?"<>|':
            assert ch not in result[:-4]  # 拡張子を除いた本体部分

    def test_空文字列はimageにフォールバックする(self):
        r = {"text": "", "path": "qr_1.png", "description": ""}
        assert suggested_filename(r) == "image.png"

    def test_長すぎる場合は切り詰められる(self):
        r = {"text": "a" * 100, "path": "qr_1.png"}
        result = suggested_filename(r)
        assert len(result) <= 50 + len(".png")
