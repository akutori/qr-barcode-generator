"""core.py のユニットテスト: ストレージ / ラベル (t_wada 式 TDD)"""

import json
import os
import sys
from pathlib import Path

import pytest

import core
from core import (
    SORT_OPTION_LABELS,
    FolderExistsError,
    FolderNotEmptyError,
    MoveCollisionError,
    UnmanagedFilesError,
    apply_custom_order,
    build_record_path,
    calc_preview_size,
    clamp_panel_width,
    create_folder,
    delete_folder,
    merge_folder,
    merge_folder_collisions,
    delete_records,
    find_duplicate_indices,
    find_index,
    find_move_collisions,
    folder_unmanaged_entries,
    group_by_folder,
    has_duplicate,
    list_folders,
    list_labels,
    list_labels_with_status,
    load_metadata,
    load_settings,
    move_index,
    move_records,
    record_folder,
    rename_folder,
    resolve_folder_dir,
    safe_record_path,
    same_folder_name,
    sanitize_open_folders,
    save_metadata,
    save_settings,
    sort_records,
    suggested_filename,
    validate_folder_name,
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


# ---------------------------------------------------------------------------
# パネル幅のクランプ（settings.json の破損・手動編集への防御）
# ---------------------------------------------------------------------------

class TestClampPanelWidth:
    def test_範囲内の値はそのまま返す(self):
        assert clamp_panel_width(300) == 300

    def test_最小値未満は最小値にクランプされる(self):
        assert clamp_panel_width(50) == 220

    def test_最大値超過は最大値にクランプされる(self):
        assert clamp_panel_width(9999) == 600

    def test_負の値は最小値にクランプされる(self):
        assert clamp_panel_width(-100) == 220

    def test_最小値ちょうどはそのまま返す(self):
        assert clamp_panel_width(220) == 220

    def test_最大値ちょうどはそのまま返す(self):
        assert clamp_panel_width(600) == 600

    def test_min_wとmax_wを指定できる(self):
        assert clamp_panel_width(100, min_w=150, max_w=200) == 150
        assert clamp_panel_width(250, min_w=150, max_w=200) == 200


# ---------------------------------------------------------------------------
# 階層名の検証
# ---------------------------------------------------------------------------

class TestValidateFolderName:
    def test_通常の名前はそのまま返す(self):
        assert validate_folder_name("商品A") == "商品A"

    def test_前後の空白は除去して返す(self):
        assert validate_folder_name("  abc  ") == "abc"

    def test_途中の空白とアポストロフィは許可する(self):
        assert validate_folder_name("Tom's items") == "Tom's items"

    @pytest.mark.parametrize("name", ["", "   "])
    def test_空文字と空白のみはエラー(self, name):
        with pytest.raises(ValueError):
            validate_folder_name(name)

    @pytest.mark.parametrize("ch", list('\\/:*?"<>|'))
    def test_Windowsで使えない文字はエラー(self, ch):
        with pytest.raises(ValueError):
            validate_folder_name(f"a{ch}b")

    @pytest.mark.parametrize("name", [".", "..", "...", "abc."])
    def test_ドットのみまたは末尾ドットはエラー(self, name):
        with pytest.raises(ValueError):
            validate_folder_name(name)

    @pytest.mark.parametrize("name", [
        "CON", "nul", "Com1", "LPT9", "con.txt", "AUX", "COM¹", "lpt²", "CONIN$", "conout$",
    ])
    def test_Windows予約デバイス名はエラー(self, name):
        with pytest.raises(ValueError):
            validate_folder_name(name)

    @pytest.mark.parametrize("name", ["metadata.json", "SETTINGS.JSON", "metadata.json.bak"])
    def test_アプリの管理ファイルと同名はエラー(self, name):
        with pytest.raises(ValueError):
            validate_folder_name(name)

    @pytest.mark.parametrize("name", ["a\x00b", "a\nb", "a\tb"])
    def test_制御文字を含む名前はエラー(self, name):
        with pytest.raises(ValueError):
            validate_folder_name(name)

    def test_50文字は許可し51文字はエラー(self):
        assert validate_folder_name("あ" * 50) == "あ" * 50
        with pytest.raises(ValueError):
            validate_folder_name("あ" * 51)


class TestSameFolderName:
    def test_大文字小文字の違いは同名として扱う(self):
        assert same_folder_name("Foo", "foo") is True

    def test_異なる名前はFalse(self):
        assert same_folder_name("foo", "bar") is False


# ---------------------------------------------------------------------------
# レコードの階層・パス
# ---------------------------------------------------------------------------

class TestRecordFolder:
    def test_ファイル名のみはルート(self):
        assert record_folder({"path": "qr.png"}) == ""

    def test_階層付きパスは階層名を返す(self):
        assert record_folder({"path": "商品/qr.png"}) == "商品"

    def test_バックスラッシュ区切りも階層として扱う(self):
        assert record_folder({"path": "商品\\qr.png"}) == "商品"

    def test_2階層を超えるパスはルート扱い(self):
        assert record_folder({"path": "a/b/qr.png"}) == ""

    def test_pathキーがないときはルート(self):
        assert record_folder({}) == ""

    @pytest.mark.parametrize("path", ["../x.png", "CON/x.png", "a./x.png", "C:/x.png"])
    def test_階層名として不正なものはルート扱い(self, path):
        assert record_folder({"path": path}) == ""


class TestBuildRecordPath:
    def test_ルートはファイル名のみ(self):
        assert build_record_path("", "qr.png") == "qr.png"

    def test_階層はスラッシュ区切りで連結する(self):
        assert build_record_path("商品", "qr.png") == "商品/qr.png"


class TestSafeRecordPath:
    def test_ファイル名のみはsave_dir直下に解決する(self, tmp_path):
        assert safe_record_path(tmp_path, "qr.png") == tmp_path / "qr.png"

    def test_階層付きパスはsave_dir配下に解決する(self, tmp_path):
        assert safe_record_path(tmp_path, "商品/qr.png") == tmp_path / "商品" / "qr.png"

    def test_バックスラッシュ区切りも解決できる(self, tmp_path):
        assert safe_record_path(tmp_path, "商品\\qr.png") == tmp_path / "商品" / "qr.png"

    @pytest.mark.parametrize("rel", [
        "../x.png", "a/../x.png", "..\\x.png", "a/../../x.png", "..",
    ])
    def test_親ディレクトリ参照はエラー(self, tmp_path, rel):
        with pytest.raises(ValueError):
            safe_record_path(tmp_path, rel)

    @pytest.mark.parametrize("rel", [
        "/etc/passwd", "C:\\x.png", "C:x.png", "\\\\server\\share\\x.png", "\\x.png",
    ])
    def test_絶対パスとドライブ指定はエラー(self, tmp_path, rel):
        with pytest.raises(ValueError):
            safe_record_path(tmp_path, rel)

    def test_2階層を超えるパスはエラー(self, tmp_path):
        with pytest.raises(ValueError):
            safe_record_path(tmp_path, "a/b/qr.png")

    @pytest.mark.parametrize("rel", ["", "a//x.png", "a/", "a/x\x00.png"])
    def test_空や不正な区切りはエラー(self, tmp_path, rel):
        with pytest.raises(ValueError):
            safe_record_path(tmp_path, rel)

    def test_文字列以外はエラー(self, tmp_path):
        with pytest.raises(ValueError):
            safe_record_path(tmp_path, None)

    def test_ファイル名に代替データストリーム指定を含むとエラー(self, tmp_path):
        with pytest.raises(ValueError):
            safe_record_path(tmp_path, "qr.png:stream")

    def test_階層名が予約名のときはエラー(self, tmp_path):
        with pytest.raises(ValueError):
            safe_record_path(tmp_path, "con/qr.png")

    @pytest.mark.parametrize("rel", [
        "metadata.json", "SETTINGS.JSON", "metadata.json.bak", "settings.json.tmp", "a/metadata.json",
    ])
    def test_アプリの管理ファイルを指すpathはエラー(self, tmp_path, rel):
        """手編集された metadata.json でも、削除・移動の対象に設定ファイルやメタデータ自身を含めない。"""
        with pytest.raises(ValueError):
            safe_record_path(tmp_path, rel)

    @pytest.mark.parametrize("rel", ["NUL.png", "a/com1.png", "qr.png.", "qr.png "])
    def test_ファイル名が予約デバイス名または末尾ドット空白のときはエラー(self, tmp_path, rel):
        with pytest.raises(ValueError):
            safe_record_path(tmp_path, rel)

    def test_階層の実体確認の結果を呼び出し間で使い回せる(self, tmp_path):
        checked: dict[str, bool] = {}
        safe_record_path(tmp_path, "Foo/a.png", checked)
        assert checked == {"foo": True}
        safe_record_path(tmp_path, "qr.png", checked)
        assert checked == {"foo": True}

    @pytest.mark.skipif(sys.platform == "win32", reason="シンボリックリンク作成権限が必要")
    def test_save_dir外を指す階層の確認結果も使い回してエラーにする(self, tmp_path):
        save_dir = tmp_path / "generated"
        save_dir.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (save_dir / "link").symlink_to(outside, target_is_directory=True)
        checked: dict[str, bool] = {}
        for _ in range(2):
            with pytest.raises(ValueError):
                safe_record_path(save_dir, "link/qr.png", checked)
        assert checked == {"link": False}

    @pytest.mark.skipif(sys.platform == "win32", reason="シンボリックリンク作成権限が必要")
    def test_save_dir外を指す階層はエラー(self, tmp_path):
        save_dir = tmp_path / "generated"
        save_dir.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (save_dir / "link").symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError):
            safe_record_path(save_dir, "link/qr.png")


class TestResolveFolderDir:
    def test_空文字はsave_dir自身(self, tmp_path):
        assert resolve_folder_dir(tmp_path, "") == tmp_path

    def test_階層名はsave_dir配下のディレクトリになる(self, tmp_path):
        assert resolve_folder_dir(tmp_path, "商品") == tmp_path / "商品"

    @pytest.mark.parametrize("name", ["..", "a/b", "CON", " a "])
    def test_不正な階層名はエラー(self, tmp_path, name):
        with pytest.raises(ValueError):
            resolve_folder_dir(tmp_path, name)

    @pytest.mark.skipif(sys.platform == "win32", reason="シンボリックリンク作成権限が必要")
    def test_save_dir外を指す階層はエラー(self, tmp_path):
        save_dir = tmp_path / "generated"
        save_dir.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (save_dir / "link").symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError):
            resolve_folder_dir(save_dir, "link")


# ---------------------------------------------------------------------------
# 階層の一覧・グルーピング
# ---------------------------------------------------------------------------

class TestListFolders:
    def test_サブディレクトリがなければ空(self, tmp_path):
        assert list_folders(tmp_path, []) == []

    def test_存在しないsave_dirは空(self, tmp_path):
        assert list_folders(tmp_path / "none", []) == []

    def test_ディレクトリのみを名前順に返しファイルは無視する(self, tmp_path):
        (tmp_path / "b").mkdir()
        (tmp_path / "A").mkdir()
        (tmp_path / "metadata.json").write_text("[]")
        assert list_folders(tmp_path, []) == ["A", "b"]

    def test_レコードが参照する階層はディレクトリがなくても含める(self, tmp_path):
        records = [{"text": "x", "type": "Q", "path": "gone/qr.png"}]
        assert list_folders(tmp_path, records) == ["gone"]

    def test_大文字小文字違いは同一視しディスク上の名前を優先する(self, tmp_path):
        (tmp_path / "Foo").mkdir()
        records = [{"text": "x", "type": "Q", "path": "foo/qr.png"}]
        assert list_folders(tmp_path, records) == ["Foo"]

    def test_不正な名前のディレクトリは除外する(self, tmp_path):
        (tmp_path / "CON").mkdir()
        (tmp_path / "bad.").mkdir()
        (tmp_path / "ok").mkdir()
        assert list_folders(tmp_path, []) == ["ok"]


class TestGroupByFolder:
    def test_ルートと階層に振り分ける(self):
        records = [
            {"text": "a", "type": "Q", "path": "a.png"},
            {"text": "b", "type": "Q", "path": "F/b.png"},
            {"text": "c", "type": "Q", "path": "F/c.png"},
        ]
        assert group_by_folder(records, [0, 1, 2], ["F"]) == {"": [0], "F": [1, 2]}

    def test_空の階層もキーに含める(self):
        assert group_by_folder([], [], ["F"]) == {"": [], "F": []}

    def test_indicesの順序を保つ(self):
        records = [{"text": str(i), "type": "Q", "path": "F/x.png"} for i in range(3)]
        assert group_by_folder(records, [2, 0, 1], ["F"])["F"] == [2, 0, 1]

    def test_大文字小文字違いは正規名にまとめる(self):
        records = [{"text": "a", "type": "Q", "path": "f/a.png"}]
        assert group_by_folder(records, [0], ["F"]) == {"": [], "F": [0]}

    def test_未知の階層を指すレコードはルート扱い(self):
        records = [{"text": "a", "type": "Q", "path": "unknown/a.png"}]
        assert group_by_folder(records, [0], []) == {"": [0]}


# ---------------------------------------------------------------------------
# 重複・衝突の検出
# ---------------------------------------------------------------------------

class TestFindDuplicateIndices:
    def test_一致するレコードの位置を返す(self):
        records = [
            {"text": "hello", "type": "Q", "path": "a.png", "error_correction": "M"},
            {"text": "other", "type": "Q", "path": "b.png", "error_correction": "M"},
            {"text": "hello", "type": "Q", "path": "c.png", "error_correction": "M"},
        ]
        assert find_duplicate_indices("hello", "Q", records, "M", "UTF-8") == [0, 2]

    def test_一致なしは空リスト(self):
        assert find_duplicate_indices("hello", "Q", []) == []


class TestFindMoveCollisions:
    @staticmethod
    def _rec(text, path, code_type="Q", **extra):
        rec = {"text": text, "type": code_type, "path": path}
        if code_type == "Q":
            rec.setdefault("error_correction", "M")
        rec.update(extra)
        return rec

    def test_移動先に同一コードがあれば移動元と移動先の位置を返す(self):
        records = [self._rec("hello", "a.png"), self._rec("hello", "F/b.png")]
        assert find_move_collisions(records, [0], "F") == [(0, 1)]

    def test_別の階層にある同一コードは衝突しない(self):
        records = [self._rec("hello", "a.png"), self._rec("hello", "G/b.png")]
        assert find_move_collisions(records, [0], "F") == []

    def test_ルートへの移動でルートの同一コードと衝突する(self):
        records = [self._rec("hello", "F/a.png"), self._rec("hello", "b.png")]
        assert find_move_collisions(records, [0], "") == [(0, 1)]

    def test_移動対象同士は衝突として扱わない(self):
        records = [self._rec("hello", "a.png"), self._rec("hello", "b.png")]
        assert find_move_collisions(records, [0, 1], "F") == []

    def test_すでに移動先にあるレコードは対象外(self):
        records = [self._rec("hello", "F/a.png"), self._rec("hello", "F/b.png")]
        assert find_move_collisions(records, [0], "F") == []

    def test_移動対象に移動先の既存レコードが混ざっていてもそれは衝突相手になる(self):
        """移動先にいるレコードは動かないため、選択に含まれていても移動元からは除外し衝突相手に残す。"""
        records = [self._rec("hello", "a.png"), self._rec("hello", "F/b.png")]
        assert find_move_collisions(records, [0, 1], "F") == [(0, 1)]

    def test_QRで誤り訂正レベルが異なれば衝突しない(self):
        records = [self._rec("hello", "a.png"),
                   self._rec("hello", "F/b.png", error_correction="H")]
        assert find_move_collisions(records, [0], "F") == []

    def test_QRでエンコードが異なれば衝突しない(self):
        records = [self._rec("日本語", "a.png", encoding="UTF-8"),
                   self._rec("日本語", "F/b.png", encoding="SJIS")]
        assert find_move_collisions(records, [0], "F") == []

    def test_種別が異なれば衝突しない(self):
        records = [self._rec("12345", "a.png", "Q"), self._rec("12345", "F/b.png", "B")]
        assert find_move_collisions(records, [0], "F") == []

    def test_バーコードは同一テキストで衝突する(self):
        records = [self._rec("12345", "a.png", "B"), self._rec("12345", "F/b.png", "B")]
        assert find_move_collisions(records, [0], "F") == [(0, 1)]

    def test_階層名の大文字小文字は同一視する(self):
        records = [self._rec("hello", "a.png"), self._rec("hello", "f/b.png")]
        assert find_move_collisions(records, [0], "F") == [(0, 1)]


# ---------------------------------------------------------------------------
# 階層への移動
# ---------------------------------------------------------------------------

def _make_rec(save_dir: Path, folder: str, filename: str, text: str,
              code_type: str = "Q", content: bytes = b"png", **extra) -> dict:
    """実ファイル付きのレコードを作る。folder が空文字ならルート。"""
    target = save_dir / folder if folder else save_dir
    target.mkdir(parents=True, exist_ok=True)
    (target / filename).write_bytes(content)
    rec = {"text": text, "type": code_type, "path": build_record_path(folder, filename)}
    if code_type == "Q":
        rec["error_correction"] = "M"
    rec.update(extra)
    return rec


@pytest.fixture
def save_dir(tmp_path):
    d = tmp_path / "generated"
    d.mkdir()
    return d


@pytest.fixture
def meta(save_dir):
    return save_dir / "metadata.json"


class TestMoveRecords:
    def test_ルートから階層へファイルとpathが移動する(self, save_dir, meta):
        records = [_make_rec(save_dir, "", "qr1.png", "hello")]
        result = move_records(records, [0], "a", save_dir, meta)
        assert result.moved == 1
        assert (save_dir / "a" / "qr1.png").exists()
        assert not (save_dir / "qr1.png").exists()
        assert records[0]["path"] == "a/qr1.png"
        assert load_metadata(meta)[0]["path"] == "a/qr1.png"

    def test_階層からルートへ移動できる(self, save_dir, meta):
        records = [_make_rec(save_dir, "a", "qr1.png", "hello")]
        move_records(records, [0], "", save_dir, meta)
        assert (save_dir / "qr1.png").exists()
        assert records[0]["path"] == "qr1.png"

    def test_階層から別の階層へ移動できる(self, save_dir, meta):
        records = [_make_rec(save_dir, "a", "qr1.png", "hello")]
        move_records(records, [0], "b", save_dir, meta)
        assert (save_dir / "b" / "qr1.png").exists()
        assert not (save_dir / "a" / "qr1.png").exists()
        assert records[0]["path"] == "b/qr1.png"

    def test_複数件をまとめて移動できる(self, save_dir, meta):
        records = [
            _make_rec(save_dir, "", "qr1.png", "one"),
            _make_rec(save_dir, "", "qr2.png", "two"),
            _make_rec(save_dir, "", "qr3.png", "three"),
        ]
        result = move_records(records, [0, 2], "a", save_dir, meta)
        assert result.moved == 2
        assert [r["path"] for r in records] == ["a/qr1.png", "qr2.png", "a/qr3.png"]

    def test_すでに移動先にあるレコードは移動しない(self, save_dir, meta):
        records = [_make_rec(save_dir, "a", "qr1.png", "hello")]
        result = move_records(records, [0], "a", save_dir, meta)
        assert result.moved == 0
        assert records[0]["path"] == "a/qr1.png"
        assert (save_dir / "a" / "qr1.png").exists()

    def test_移動先に存在しない階層は作成する(self, save_dir, meta):
        records = [_make_rec(save_dir, "", "qr1.png", "hello")]
        move_records(records, [0], "new", save_dir, meta)
        assert (save_dir / "new").is_dir()

    @pytest.mark.parametrize("dest", ["..", "a/b", "a\\b", "CON", "x:y"])
    def test_不正な階層名はエラーで何も変更しない(self, save_dir, meta, dest):
        records = [_make_rec(save_dir, "", "qr1.png", "hello")]
        with pytest.raises(ValueError):
            move_records(records, [0], dest, save_dir, meta)
        assert records[0]["path"] == "qr1.png"
        assert (save_dir / "qr1.png").exists()

    def test_衝突があり上書き未指定ならエラーで何も変更しない(self, save_dir, meta):
        records = [
            _make_rec(save_dir, "", "qr1.png", "hello"),
            _make_rec(save_dir, "a", "qr2.png", "hello"),
        ]
        with pytest.raises(MoveCollisionError) as exc:
            move_records(records, [0], "a", save_dir, meta)
        assert exc.value.collisions == [(0, 1)]
        assert len(records) == 2
        assert [r["path"] for r in records] == ["qr1.png", "a/qr2.png"]
        assert (save_dir / "qr1.png").exists()
        assert (save_dir / "a" / "qr2.png").exists()

    def test_上書き指定で移動先の既存レコードとファイルを削除する(self, save_dir, meta):
        records = [
            _make_rec(save_dir, "", "qr1.png", "hello"),
            _make_rec(save_dir, "a", "qr2.png", "hello"),
        ]
        result = move_records(records, [0], "a", save_dir, meta, overwrite=True)
        assert (result.moved, result.overwritten) == (1, 1)
        assert len(records) == 1
        assert records[0]["path"] == "a/qr1.png"
        assert (save_dir / "a" / "qr1.png").exists()
        assert not (save_dir / "a" / "qr2.png").exists()
        assert [r["path"] for r in load_metadata(meta)] == ["a/qr1.png"]

    def test_上書き対象のpathが不正でもsave_dir外のファイルは削除しない(self, save_dir, meta, tmp_path):
        victim = tmp_path / "victim.png"
        victim.write_bytes(b"keep")
        records = [
            _make_rec(save_dir, "a", "qr1.png", "hello"),
            {"text": "hello", "type": "Q", "path": "../victim.png", "error_correction": "M"},
        ]
        result = move_records(records, [0], "", save_dir, meta, overwrite=True)
        assert result.overwritten == 1
        assert victim.exists()

    def test_移動先の欠損レコードと同名でも別名にして同じpathを指さない(self, save_dir, meta):
        """後で欠損側のレコードを削除したとき、移動したファイルまで消えないようにする。"""
        records = [
            _make_rec(save_dir, "", "a.png", "one", content=b"MINE"),
            {"text": "two", "type": "Q", "path": "F/a.png", "error_correction": "M"},
        ]
        (save_dir / "F").mkdir()
        move_records(records, [0], "F", save_dir, meta)
        assert records[0]["path"] != records[1]["path"]
        assert (save_dir / records[0]["path"]).read_bytes() == b"MINE"

    def test_移動元ファイルが欠損していてもpathだけ更新する(self, save_dir, meta):
        records = [{"text": "hello", "type": "Q", "path": "gone.png"}]
        result = move_records(records, [0], "a", save_dir, meta)
        assert result.moved == 1
        assert records[0]["path"] == "a/gone.png"

    def test_移動先に同名の別ファイルがあるときは別名にして上書きしない(self, save_dir, meta):
        records = [_make_rec(save_dir, "", "qr1.png", "hello", content=b"mine")]
        (save_dir / "a").mkdir()
        (save_dir / "a" / "qr1.png").write_bytes(b"unrelated")
        move_records(records, [0], "a", save_dir, meta)
        assert (save_dir / "a" / "qr1.png").read_bytes() == b"unrelated"
        assert records[0]["path"] != "a/qr1.png"
        assert (save_dir / records[0]["path"]).read_bytes() == b"mine"

    def test_同名ファイルを複数件まとめて移動しても互いに上書きしない(self, save_dir, meta):
        records = [
            _make_rec(save_dir, "", "qr1.png", "one", content=b"one"),
            _make_rec(save_dir, "b", "qr1.png", "two", content=b"two"),
        ]
        move_records(records, [0, 1], "a", save_dir, meta)
        assert records[0]["path"] != records[1]["path"]
        assert (save_dir / records[0]["path"]).read_bytes() == b"one"
        assert (save_dir / records[1]["path"]).read_bytes() == b"two"

    def test_ファイル移動が途中で失敗したら移動済みファイルを元に戻す(self, save_dir, meta, monkeypatch):
        records = [
            _make_rec(save_dir, "", "qr1.png", "one"),
            _make_rec(save_dir, "", "qr2.png", "two"),
            _make_rec(save_dir, "", "qr3.png", "three"),
        ]
        real_rename = os.rename
        calls = []

        def flaky_rename(src, dst):
            calls.append(src)
            if len(calls) == 3:
                raise PermissionError("locked")
            return real_rename(src, dst)

        monkeypatch.setattr(os, "rename", flaky_rename)
        with pytest.raises(PermissionError):
            move_records(records, [0, 1, 2], "a", save_dir, meta)
        monkeypatch.undo()
        assert [r["path"] for r in records] == ["qr1.png", "qr2.png", "qr3.png"]
        for name in ("qr1.png", "qr2.png", "qr3.png"):
            assert (save_dir / name).exists()
            assert not (save_dir / "a" / name).exists()

    def test_メタデータ保存に失敗したらファイルとpathを元に戻し上書き対象も残す(
        self, save_dir, meta, monkeypatch
    ):
        records = [
            _make_rec(save_dir, "", "qr1.png", "hello"),
            _make_rec(save_dir, "a", "qr2.png", "hello"),
        ]

        def failing_save(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(core, "save_metadata", failing_save)
        with pytest.raises(OSError):
            move_records(records, [0], "a", save_dir, meta, overwrite=True)
        assert len(records) == 2
        assert [r["path"] for r in records] == ["qr1.png", "a/qr2.png"]
        assert (save_dir / "qr1.png").exists()
        assert (save_dir / "a" / "qr2.png").exists()

    def test_pathが不正なレコードを含むときはエラーで何も移動しない(self, save_dir, meta):
        records = [
            _make_rec(save_dir, "", "qr1.png", "one"),
            {"text": "evil", "type": "Q", "path": "../evil.png"},
        ]
        with pytest.raises(ValueError):
            move_records(records, [0, 1], "a", save_dir, meta)
        assert records[0]["path"] == "qr1.png"
        assert (save_dir / "qr1.png").exists()

    def test_範囲外のindexはエラー(self, save_dir, meta):
        records = [_make_rec(save_dir, "", "qr1.png", "one")]
        with pytest.raises(ValueError):
            move_records(records, [5], "a", save_dir, meta)

    def test_移動したレコードは移動先の末尾順になる(self, save_dir, meta):
        records = [
            _make_rec(save_dir, "a", "qr1.png", "one", order=0),
            _make_rec(save_dir, "a", "qr2.png", "two", order=1),
            _make_rec(save_dir, "", "qr3.png", "three", order=7),
            _make_rec(save_dir, "", "qr4.png", "four", order=8),
        ]
        move_records(records, [2, 3], "a", save_dir, meta)
        assert records[2]["order"] == 2
        assert records[3]["order"] == 3

    def test_上書き対象の画像が欠損していて同名でも移動したファイルを削除しない(self, save_dir, meta):
        records = [
            _make_rec(save_dir, "", "a.png", "hello", content=b"IMPORTANT"),
            {"text": "hello", "type": "Q", "path": "F/a.png", "error_correction": "M"},
        ]
        (save_dir / "F").mkdir()
        move_records(records, [0], "F", save_dir, meta, overwrite=True)
        assert len(records) == 1
        assert (save_dir / records[0]["path"]).read_bytes() == b"IMPORTANT"

    def test_移動元の画像が欠損しているときは上書きを拒否して移動先を残す(self, save_dir, meta):
        records = [
            {"text": "hello", "type": "Q", "path": "gone.png", "error_correction": "M"},
            _make_rec(save_dir, "F", "good.png", "hello", content=b"GOOD"),
        ]
        with pytest.raises(ValueError):
            move_records(records, [0], "F", save_dir, meta, overwrite=True)
        assert len(records) == 2
        assert (save_dir / "F" / "good.png").read_bytes() == b"GOOD"

    @pytest.mark.skipif(sys.platform == "win32", reason="シンボリックリンク作成権限が必要")
    def test_移動先階層がsave_dir外を指すときはエラーで書き込まない(self, save_dir, meta, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (save_dir / "link").symlink_to(outside, target_is_directory=True)
        records = [_make_rec(save_dir, "", "qr1.png", "hello")]
        with pytest.raises(ValueError):
            move_records(records, [0], "link", save_dir, meta)
        assert list(outside.iterdir()) == []
        assert (save_dir / "qr1.png").exists()

    def test_巻き戻しにも失敗したときは実ファイルの位置にレコードを合わせてエラーにする(
        self, save_dir, meta, monkeypatch
    ):
        records = [
            _make_rec(save_dir, "", "1.png", "one"),
            _make_rec(save_dir, "", "2.png", "two"),
        ]
        real_rename = os.rename
        calls = []

        def flaky_rename(src, dst):
            calls.append(src)
            if len(calls) >= 2:  # 2 件目の移動と、1 件目の巻き戻しの両方が失敗する
                raise PermissionError("locked")
            return real_rename(src, dst)

        monkeypatch.setattr(os, "rename", flaky_rename)
        with pytest.raises(OSError, match="戻せませんでした"):
            move_records(records, [0, 1], "A", save_dir, meta)
        monkeypatch.undo()
        assert (save_dir / "A" / "1.png").exists()
        assert [r["path"] for r in records] == ["A/1.png", "2.png"]
        assert [r["path"] for r in load_metadata(meta)] == ["A/1.png", "2.png"]

    def test_複数の移動元が同じ移動先レコードと衝突しても上書き件数は重複して数えない(self, save_dir, meta):
        records = [
            _make_rec(save_dir, "", "a.png", "hello"),
            _make_rec(save_dir, "B", "b.png", "hello"),
            _make_rec(save_dir, "F", "c.png", "hello"),
        ]
        result = move_records(records, [0, 1], "F", save_dir, meta, overwrite=True)
        assert (result.moved, result.overwritten) == (2, 1)

    def test_pathキーのないレコードを含むときはエラーで何も移動しない(self, save_dir, meta):
        records = [_make_rec(save_dir, "", "qr1.png", "one"), {"text": "x", "type": "Q"}]
        with pytest.raises(ValueError):
            move_records(records, [0, 1], "a", save_dir, meta)
        assert (save_dir / "qr1.png").exists()

    def test_移動したレコードの識別性を保つ(self, save_dir, meta):
        """app 側が現在選択中のレコードを参照で追跡するため、辞書オブジェクトは置き換えない。"""
        rec = _make_rec(save_dir, "", "qr1.png", "hello")
        records = [rec]
        move_records(records, [0], "a", save_dir, meta)
        assert records[0] is rec


# ---------------------------------------------------------------------------
# 階層の作成
# ---------------------------------------------------------------------------

class TestCreateFolder:
    def test_ディレクトリを作成して階層名を返す(self, save_dir):
        assert create_folder(save_dir, "  商品A ", []) == "商品A"
        assert (save_dir / "商品A").is_dir()

    def test_同名の階層が既にあればFolderExistsError(self, save_dir):
        (save_dir / "商品A").mkdir()
        with pytest.raises(FolderExistsError):
            create_folder(save_dir, "商品A", [])

    def test_大文字小文字違いの同名もFolderExistsError(self, save_dir):
        (save_dir / "Foo").mkdir()
        with pytest.raises(FolderExistsError):
            create_folder(save_dir, "foo", [])

    def test_レコードだけが参照している階層も存在扱いにする(self, save_dir):
        records = [{"text": "x", "type": "Q", "path": "Foo/qr.png"}]
        with pytest.raises(FolderExistsError):
            create_folder(save_dir, "foo", records)

    @pytest.mark.parametrize("name", ["", "..", "a/b", "CON", "a:b"])
    def test_不正な名前はValueErrorで作成しない(self, save_dir, name):
        with pytest.raises(ValueError):
            create_folder(save_dir, name, [])
        assert list(save_dir.iterdir()) == []

    def test_FolderExistsErrorはValueErrorではない(self):
        assert not issubclass(FolderExistsError, ValueError)


# ---------------------------------------------------------------------------
# 展開状態の設定（settings.json の手動編集・破損への防御）
# ---------------------------------------------------------------------------

class TestSanitizeOpenFolders:
    def test_リスト以外は空リスト(self):
        assert sanitize_open_folders("abc") == []
        assert sanitize_open_folders(None) == []
        assert sanitize_open_folders({"a": 1}) == []

    def test_正しい階層名はそのまま残す(self):
        assert sanitize_open_folders(["商品", "b"]) == ["商品", "b"]

    def test_不正な名前と文字列以外は取り除く(self):
        assert sanitize_open_folders(["ok", "..", "a/b", "C:\\x", 5, None, "../../etc"]) == ["ok"]

    def test_大文字小文字違いの重複は先勝ちで1件にする(self):
        assert sanitize_open_folders(["Foo", "foo"]) == ["Foo"]

    def test_前後に空白のある名前は不正として取り除く(self):
        assert sanitize_open_folders([" a "]) == []


class TestLoadSettingsOpenFolders:
    def test_デフォルトは空リスト(self, tmp_path):
        assert load_settings(tmp_path / "settings.json")["open_folders"] == []

    def test_保存した展開状態を読み返せる(self, tmp_path):
        path = tmp_path / "settings.json"
        save_settings({"open_folders": ["a", "b"]}, path)
        assert load_settings(path)["open_folders"] == ["a", "b"]

    def test_不正なパスを含む設定値は読み込み時に取り除く(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"open_folders": ["a", "../outside", "C:\\Windows"]}),
                        encoding="utf-8")
        assert load_settings(path)["open_folders"] == ["a"]

    def test_デフォルト値のリストは呼び出し間で共有されない(self, tmp_path):
        first = load_settings(tmp_path / "none.json")
        first["open_folders"].append("x")
        assert load_settings(tmp_path / "none.json")["open_folders"] == []


# ---------------------------------------------------------------------------
# アトミック書き込み（書き込み中の失敗で既存ファイルを壊さない）
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    def test_メタデータ保存後に一時ファイルが残らない(self, tmp_path):
        path = tmp_path / "metadata.json"
        save_metadata([{"text": "a"}], path)
        assert [p.name for p in tmp_path.iterdir()] == ["metadata.json"]

    def test_メタデータのシリアライズ失敗で既存ファイルを壊さない(self, tmp_path):
        path = tmp_path / "metadata.json"
        save_metadata([{"text": "keep"}], path)
        with pytest.raises(TypeError):
            save_metadata([{"text": object()}], path)
        assert load_metadata(path) == [{"text": "keep"}]
        assert [p.name for p in tmp_path.iterdir()] == ["metadata.json"]

    def test_設定保存後に一時ファイルが残らない(self, tmp_path):
        path = tmp_path / "settings.json"
        save_settings({"pdf_cols": 2}, path)
        assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]

    def test_設定のシリアライズ失敗で既存ファイルを壊さない(self, tmp_path):
        path = tmp_path / "settings.json"
        save_settings({"pdf_cols": 2}, path)
        with pytest.raises(TypeError):
            save_settings({"pdf_cols": object()}, path)
        assert json.loads(path.read_text(encoding="utf-8")) == {"pdf_cols": 2}
        assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]


# ---------------------------------------------------------------------------
# 階層内ファイルの存在確認ラベル
# ---------------------------------------------------------------------------

class TestListLabelsWithStatusFolders:
    def test_階層内のファイルが存在するレコードに警告を付けない(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "qr.png").write_bytes(b"x")
        records = [{"text": "hello", "type": "Q", "path": "a/qr.png"}]
        assert list_labels_with_status(records, tmp_path) == ["[QR]  hello"]

    def test_save_dir外を指す不正なpathは欠損扱いにする(self, tmp_path):
        save_dir = tmp_path / "generated"
        save_dir.mkdir()
        (tmp_path / "outside.png").write_bytes(b"x")
        records = [{"text": "hello", "type": "Q", "path": "../outside.png"}]
        assert list_labels_with_status(records, save_dir) == ["⚠[QR]  hello"]


# ---------------------------------------------------------------------------
# 階層の名前変更
# ---------------------------------------------------------------------------

class TestRenameFolder:
    def test_フォルダ名とレコードのpathを変更して保存する(self, save_dir, meta):
        records = [
            _make_rec(save_dir, "A", "a1.png", "one"),
            _make_rec(save_dir, "A", "a2.png", "two"),
            _make_rec(save_dir, "B", "b1.png", "three"),
            _make_rec(save_dir, "", "r1.png", "four"),
        ]
        n = rename_folder(save_dir, "A", "商品", records, meta)
        assert n == 2
        assert not (save_dir / "A").exists()
        assert (save_dir / "商品" / "a1.png").exists() and (save_dir / "商品" / "a2.png").exists()
        assert [r["path"] for r in records] == ["商品/a1.png", "商品/a2.png", "B/b1.png", "r1.png"]
        assert [r["path"] for r in load_metadata(meta)] == ["商品/a1.png", "商品/a2.png", "B/b1.png", "r1.png"]

    def test_前後の空白は除去した名前で変更する(self, save_dir, meta):
        records = [_make_rec(save_dir, "A", "a1.png", "one")]
        rename_folder(save_dir, "A", "  新  ", records, meta)
        assert (save_dir / "新").is_dir()

    def test_同じ名前のときは何もしない(self, save_dir, meta):
        records = [_make_rec(save_dir, "A", "a1.png", "one")]
        assert rename_folder(save_dir, "A", "A", records, meta) == 0
        assert records[0]["path"] == "A/a1.png"

    def test_大文字小文字だけの変更は許可する(self, save_dir, meta):
        records = [_make_rec(save_dir, "Foo", "a1.png", "one")]
        n = rename_folder(save_dir, "Foo", "FOO", records, meta)
        assert n == 1
        assert records[0]["path"] == "FOO/a1.png"
        assert [p.name for p in save_dir.iterdir() if p.is_dir()] == ["FOO"]

    def test_大文字小文字違いのold指定でも既存の階層を変更できる(self, save_dir, meta):
        records = [_make_rec(save_dir, "Foo", "a1.png", "one")]
        rename_folder(save_dir, "foo", "Bar", records, meta)
        assert records[0]["path"] == "Bar/a1.png"

    @pytest.mark.parametrize("new", ["", "..", "a/b", "CON", "x:y", "あ" * 51])
    def test_不正な新しい名前はValueErrorで何も変更しない(self, save_dir, meta, new):
        records = [_make_rec(save_dir, "A", "a1.png", "one")]
        with pytest.raises(ValueError):
            rename_folder(save_dir, "A", new, records, meta)
        assert (save_dir / "A" / "a1.png").exists()
        assert records[0]["path"] == "A/a1.png"

    def test_ほかの階層と同名ならFolderExistsError(self, save_dir, meta):
        records = [_make_rec(save_dir, "A", "a1.png", "one"), _make_rec(save_dir, "B", "b1.png", "two")]
        with pytest.raises(FolderExistsError):
            rename_folder(save_dir, "A", "b", records, meta)
        assert (save_dir / "A" / "a1.png").exists() and (save_dir / "B" / "b1.png").exists()
        assert records[0]["path"] == "A/a1.png"

    def test_同名のファイルがあるときはValueError(self, save_dir, meta):
        records = [_make_rec(save_dir, "A", "a1.png", "one")]
        (save_dir / "memo").write_text("x")
        with pytest.raises(ValueError):
            rename_folder(save_dir, "A", "memo", records, meta)
        assert (save_dir / "A" / "a1.png").exists()

    def test_存在しない階層はValueError(self, save_dir, meta):
        with pytest.raises(ValueError):
            rename_folder(save_dir, "None", "X", [], meta)

    def test_ディレクトリが無くレコードだけが参照する階層はpathだけ更新する(self, save_dir, meta):
        records = [{"text": "x", "type": "Q", "path": "Ghost/x.png", "error_correction": "M"}]
        n = rename_folder(save_dir, "Ghost", "Real", records, meta)
        assert n == 1
        assert records[0]["path"] == "Real/x.png"
        assert not (save_dir / "Real").exists()

    def test_メタデータ保存に失敗したらフォルダ名とpathを元に戻す(self, save_dir, meta, monkeypatch):
        records = [_make_rec(save_dir, "A", "a1.png", "one")]

        def failing_save(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(core, "save_metadata", failing_save)
        with pytest.raises(OSError):
            rename_folder(save_dir, "A", "B", records, meta)
        assert (save_dir / "A" / "a1.png").exists() and not (save_dir / "B").exists()
        assert records[0]["path"] == "A/a1.png"

    def test_戻せなかったときは実際の場所にレコードを合わせてエラーにする(self, save_dir, meta, monkeypatch):
        records = [_make_rec(save_dir, "A", "a1.png", "one")]
        real_rename = os.rename
        calls = []

        def flaky_rename(src, dst):
            calls.append(src)
            if len(calls) >= 2:  # 変更後の巻き戻しが失敗する
                raise PermissionError("locked")
            return real_rename(src, dst)

        def failing_save(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(os, "rename", flaky_rename)
        monkeypatch.setattr(core, "save_metadata", failing_save)
        with pytest.raises(OSError, match="戻せませんでした"):
            rename_folder(save_dir, "A", "B", records, meta)
        monkeypatch.undo()
        assert (save_dir / "B" / "a1.png").exists()
        assert records[0]["path"] == "B/a1.png"

    def test_フォルダ名の変更に失敗したらレコードは変更しない(self, save_dir, meta, monkeypatch):
        records = [_make_rec(save_dir, "A", "a1.png", "one")]

        def failing_rename(src, dst):
            raise PermissionError("locked")

        monkeypatch.setattr(os, "rename", failing_rename)
        with pytest.raises(PermissionError):
            rename_folder(save_dir, "A", "B", records, meta)
        monkeypatch.undo()
        assert records[0]["path"] == "A/a1.png"
        assert (save_dir / "A" / "a1.png").exists()

    @pytest.mark.skipif(sys.platform == "win32", reason="シンボリックリンク作成権限が必要")
    def test_save_dir外を指す階層はValueError(self, save_dir, meta, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (save_dir / "L").symlink_to(outside, target_is_directory=True)
        records = [{"text": "x", "type": "Q", "path": "L/x.png", "error_correction": "M"}]
        with pytest.raises(ValueError):
            rename_folder(save_dir, "L", "M", records, meta)
        assert outside.exists()


# ---------------------------------------------------------------------------
# 階層の削除
# ---------------------------------------------------------------------------

class TestFolderUnmanagedEntries:
    def test_レコードが指すファイルだけなら空(self, save_dir):
        records = [_make_rec(save_dir, "A", "a1.png", "one")]
        assert folder_unmanaged_entries(save_dir, "A", records) == []

    def test_レコードにないファイルとフォルダを返す(self, save_dir):
        records = [_make_rec(save_dir, "A", "a1.png", "one")]
        (save_dir / "A" / "memo.txt").write_text("x")
        (save_dir / "A" / "sub").mkdir()
        assert folder_unmanaged_entries(save_dir, "A", records) == ["memo.txt", "sub"]

    def test_ディレクトリが無ければ空(self, save_dir):
        assert folder_unmanaged_entries(save_dir, "Ghost", []) == []

    def test_ファイル名の大文字小文字違いは同じファイルとして扱う(self, save_dir):
        """Windows は大文字小文字を区別しないため、レコードの path と綴りが違っても管理下のファイルとみなす。"""
        (save_dir / "A").mkdir()
        (save_dir / "A" / "PHOTO.PNG").write_bytes(b"x")
        records = [{"text": "x", "type": "Q", "path": "A/photo.png", "error_correction": "M"}]
        assert folder_unmanaged_entries(save_dir, "A", records) == []


class TestDeleteRecords:
    def test_レコードと画像ファイルを削除する(self, save_dir, meta):
        records = [_make_rec(save_dir, "", "a.png", "a"), _make_rec(save_dir, "", "b.png", "b"),
                   _make_rec(save_dir, "", "c.png", "c")]
        n = delete_records(records, [0, 2], save_dir, meta)
        assert n == 2
        assert [r["text"] for r in records] == ["b"]
        assert not (save_dir / "a.png").exists() and not (save_dir / "c.png").exists()
        assert (save_dir / "b.png").exists()
        assert [r["text"] for r in load_metadata(meta)] == ["b"]

    def test_メタデータ保存に失敗したら何も削除しない(self, save_dir, meta, monkeypatch):
        records = [_make_rec(save_dir, "", "a.png", "a")]

        def failing_save(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(core, "save_metadata", failing_save)
        with pytest.raises(OSError):
            delete_records(records, [0], save_dir, meta)
        assert len(records) == 1 and (save_dir / "a.png").exists()

    def test_pathが不正なレコードはレコードだけ消してsave_dir外のファイルは消さない(self, save_dir, meta, tmp_path):
        victim = tmp_path / "victim.png"
        victim.write_bytes(b"keep")
        records = [{"text": "x", "type": "Q", "path": "../victim.png", "error_correction": "M"}]
        assert delete_records(records, [0], save_dir, meta) == 1
        assert victim.exists() and records == []

    def test_残るレコードが使っている画像は消さない(self, save_dir, meta):
        records = [_make_rec(save_dir, "", "a.png", "a"),
                   {"text": "b", "type": "Q", "path": "a.png", "error_correction": "M"}]
        delete_records(records, [0], save_dir, meta)
        assert (save_dir / "a.png").exists()

    def test_範囲外のindexはValueError(self, save_dir, meta):
        with pytest.raises(ValueError):
            delete_records([], [0], save_dir, meta)


class TestDeleteFolder:
    def test_空の階層を削除する(self, save_dir, meta):
        (save_dir / "A").mkdir()
        result = delete_folder(save_dir, "A", [], meta)
        assert (result.moved, result.deleted) == (0, 0)
        assert not (save_dir / "A").exists()

    def test_中身があるのに扱いを指定しないとFolderNotEmptyErrorで何も変更しない(self, save_dir, meta):
        records = [_make_rec(save_dir, "A", "a1.png", "one")]
        with pytest.raises(FolderNotEmptyError):
            delete_folder(save_dir, "A", records, meta)
        assert (save_dir / "A" / "a1.png").exists() and records[0]["path"] == "A/a1.png"

    def test_ルートへ戻して階層を削除する(self, save_dir, meta):
        records = [_make_rec(save_dir, "A", "a1.png", "one"), _make_rec(save_dir, "A", "a2.png", "two"),
                   _make_rec(save_dir, "B", "b1.png", "three")]
        result = delete_folder(save_dir, "A", records, meta, contents="move_to_root")
        assert (result.moved, result.deleted) == (2, 0)
        assert [r["path"] for r in records] == ["a1.png", "a2.png", "B/b1.png"]
        assert (save_dir / "a1.png").exists() and (save_dir / "a2.png").exists()
        assert not (save_dir / "A").exists()
        assert [r["path"] for r in load_metadata(meta)] == ["a1.png", "a2.png", "B/b1.png"]

    def test_ルートに同一コードがあり上書き未指定ならMoveCollisionErrorで何も変更しない(self, save_dir, meta):
        records = [_make_rec(save_dir, "A", "a1.png", "same"), _make_rec(save_dir, "", "r1.png", "same")]
        with pytest.raises(MoveCollisionError):
            delete_folder(save_dir, "A", records, meta, contents="move_to_root")
        assert (save_dir / "A" / "a1.png").exists() and len(records) == 2

    def test_上書き指定ならルートの同一コードを置き換えて階層を削除する(self, save_dir, meta):
        records = [_make_rec(save_dir, "A", "a1.png", "same"), _make_rec(save_dir, "", "r1.png", "same")]
        result = delete_folder(save_dir, "A", records, meta, contents="move_to_root", overwrite=True)
        assert result.moved == 1
        assert [r["path"] for r in records] == ["a1.png"]
        assert not (save_dir / "r1.png").exists() and not (save_dir / "A").exists()

    def test_コードごと削除する(self, save_dir, meta):
        records = [_make_rec(save_dir, "A", "a1.png", "one"), _make_rec(save_dir, "B", "b1.png", "two")]
        result = delete_folder(save_dir, "A", records, meta, contents="delete")
        assert (result.moved, result.deleted) == (0, 1)
        assert [r["text"] for r in records] == ["two"]
        assert not (save_dir / "A").exists() and (save_dir / "B" / "b1.png").exists()
        assert [r["text"] for r in load_metadata(meta)] == ["two"]

    def test_管理外のファイルがあるときはUnmanagedFilesErrorで何も変更しない(self, save_dir, meta):
        records = [_make_rec(save_dir, "A", "a1.png", "one")]
        (save_dir / "A" / "memo.txt").write_text("x")
        for contents in ("error", "move_to_root", "delete"):
            with pytest.raises(UnmanagedFilesError) as exc:
                delete_folder(save_dir, "A", records, meta, contents=contents)
            assert exc.value.names == ["memo.txt"]
        assert (save_dir / "A" / "a1.png").exists() and (save_dir / "A" / "memo.txt").exists()
        assert records[0]["path"] == "A/a1.png"

    @pytest.mark.skipif(sys.platform == "win32", reason="symlink 作成に権限が要る")
    def test_別階層へのリンクになっている階層は何も消さず拒否する(self, save_dir, meta):
        records = [_make_rec(save_dir, "B", "x.png", "bee")]
        os.symlink(save_dir / "B", save_dir / "A", target_is_directory=True)
        records.append({"text": "ay", "type": "Q", "path": "A/x.png", "error_correction": "M"})
        with pytest.raises(ValueError):
            delete_folder(save_dir, "A", records, meta, contents="delete")
        assert (save_dir / "B" / "x.png").exists() and len(records) == 2

    def test_パスが不正なコードがある階層は何も変更せず拒否する(self, save_dir, meta):
        records = [_make_rec(save_dir, "A", "a1.png", "one"),
                   {"text": "bad", "type": "Q", "path": "A/CON", "error_correction": "M"}]
        with pytest.raises(ValueError):
            delete_folder(save_dir, "A", records, meta, contents="delete")
        assert len(records) == 2 and (save_dir / "A" / "a1.png").exists()

    def test_コードの指す名前がディレクトリなら何も変更せず拒否する(self, save_dir, meta):
        (save_dir / "A" / "x.png").mkdir(parents=True)
        (save_dir / "A" / "x.png" / "keep.txt").write_text("k")
        records = [{"text": "x", "type": "Q", "path": "A/x.png", "error_correction": "M"}]
        with pytest.raises(ValueError):
            delete_folder(save_dir, "A", records, meta, contents="delete")
        assert len(records) == 1

    def test_rmdirに失敗したらコード処理済みと分かるメッセージにする(self, save_dir, meta, monkeypatch):
        records = [_make_rec(save_dir, "A", "a1.png", "one")]

        def failing_rmdir(self):
            raise PermissionError("locked")

        monkeypatch.setattr(Path, "rmdir", failing_rmdir)
        with pytest.raises(OSError, match="削除は完了しました"):
            delete_folder(save_dir, "A", records, meta, contents="delete")

    def test_ディレクトリが無くレコードだけが参照する階層もコードごと削除できる(self, save_dir, meta):
        records = [{"text": "x", "type": "Q", "path": "Ghost/x.png", "error_correction": "M"}]
        result = delete_folder(save_dir, "Ghost", records, meta, contents="delete")
        assert result.deleted == 1 and records == []

    def test_コードごと削除でメタデータ保存に失敗したら何も消さない(self, save_dir, meta, monkeypatch):
        records = [_make_rec(save_dir, "A", "a1.png", "one")]

        def failing_save(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(core, "save_metadata", failing_save)
        with pytest.raises(OSError):
            delete_folder(save_dir, "A", records, meta, contents="delete")
        assert (save_dir / "A" / "a1.png").exists() and len(records) == 1

    def test_存在しない階層はValueError(self, save_dir, meta):
        with pytest.raises(ValueError):
            delete_folder(save_dir, "None", [], meta)

    def test_不正な中身の扱いはValueError(self, save_dir, meta):
        (save_dir / "A").mkdir()
        records = [_make_rec(save_dir, "A", "a1.png", "one")]
        with pytest.raises(ValueError):
            delete_folder(save_dir, "A", records, meta, contents="bogus")
        assert (save_dir / "A" / "a1.png").exists()

    @pytest.mark.skipif(sys.platform == "win32", reason="シンボリックリンク作成権限が必要")
    def test_save_dir外を指す階層はValueErrorで外側を触らない(self, save_dir, meta, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("x")
        (save_dir / "L").symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError):
            delete_folder(save_dir, "L", [], meta)
        assert (outside / "keep.txt").exists()


class TestMergeFolder:
    def test_重複がなければ全て移動し元の階層を削除する(self, save_dir, meta):
        records = [_make_rec(save_dir, "A", "a1.png", "one"), _make_rec(save_dir, "B", "b1.png", "two")]
        result = merge_folder(save_dir, "A", "B", records, meta)
        assert (result.moved, result.overwritten, result.left, result.removed_source) == (1, 0, 0, True)
        assert not (save_dir / "A").exists()
        assert {r["text"]: r["path"].split("/")[0] for r in records} == {"one": "B", "two": "B"}
        assert (save_dir / "B" / "b1.png").exists()

    def test_重複は既定で動かさず元の階層に残す(self, save_dir, meta):
        records = [_make_rec(save_dir, "A", "a1.png", "same", content=b"new"),
                   _make_rec(save_dir, "A", "a2.png", "only"),
                   _make_rec(save_dir, "B", "b1.png", "same", content=b"old")]
        assert merge_folder_collisions(save_dir, "A", "B", records) == [(0, 2)]
        result = merge_folder(save_dir, "A", "B", records, meta)
        assert (result.moved, result.overwritten, result.left, result.removed_source) == (1, 0, 1, False)
        assert (save_dir / "B" / "b1.png").read_bytes() == b"old"
        assert (save_dir / "A" / "a1.png").read_bytes() == b"new"
        assert records[0]["path"] == "A/a1.png" and records[1]["path"].startswith("B/")

    def test_重複ごとに上書きを選べる(self, save_dir, meta):
        records = [_make_rec(save_dir, "A", "a1.png", "s1", content=b"new1"),
                   _make_rec(save_dir, "A", "a2.png", "s2", content=b"new2"),
                   _make_rec(save_dir, "B", "b1.png", "s1", content=b"old1"),
                   _make_rec(save_dir, "B", "b2.png", "s2", content=b"old2")]
        result = merge_folder(save_dir, "A", "B", records, meta, overwrite={0})
        assert (result.moved, result.overwritten, result.left) == (1, 1, 1)
        texts = sorted((r["text"], r["path"].split("/")[0]) for r in records)
        assert texts == [("s1", "B"), ("s2", "A"), ("s2", "B")]
        assert not any(p.read_bytes() == b"old1" for p in (save_dir / "B").iterdir())
        assert (save_dir / "A" / "a2.png").exists()

    def test_全て上書きすると元の階層も消える(self, save_dir, meta):
        records = [_make_rec(save_dir, "A", "a1.png", "s1"), _make_rec(save_dir, "B", "b1.png", "s1")]
        result = merge_folder(save_dir, "A", "B", records, meta, overwrite={0})
        assert result.removed_source and not (save_dir / "A").exists() and len(records) == 1
        assert [r["text"] for r in load_metadata(meta)] == ["s1"]

    def test_管理外のファイルがあれば何も変更しない(self, save_dir, meta):
        records = [_make_rec(save_dir, "A", "a1.png", "one")]
        (save_dir / "B").mkdir()
        (save_dir / "A" / "memo.txt").write_text("x")
        with pytest.raises(UnmanagedFilesError):
            merge_folder(save_dir, "A", "B", records, meta)
        assert records[0]["path"] == "A/a1.png"

    def test_同じ階層への統合はValueError(self, save_dir, meta):
        records = [_make_rec(save_dir, "A", "a1.png", "one")]
        with pytest.raises(ValueError):
            merge_folder(save_dir, "A", "a", records, meta)

    def test_空の階層も統合して削除できる(self, save_dir, meta):
        (save_dir / "A").mkdir()
        (save_dir / "B").mkdir()
        result = merge_folder(save_dir, "A", "B", [], meta)
        assert result.removed_source and not (save_dir / "A").exists()
