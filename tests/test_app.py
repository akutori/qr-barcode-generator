"""app.py のユニットテスト (t_wada 式 TDD)"""

from pathlib import Path

import pytest

import app as app_module
from app import _app_dir, _description_for_copy, _filter_overwrite, _overwrite_predicate, _read_version


# ---------------------------------------------------------------------------
# バージョン読み込み
# ---------------------------------------------------------------------------

class TestReadVersion:
    def test_文字列を返す(self):
        assert isinstance(_read_version(), str)

    def test_空文字列を返さない(self):
        assert _read_version() != ""

    def test_セマンティックバージョニング形式またはunknown(self):
        v = _read_version()
        if v == "unknown":
            return
        parts = v.split(".")
        assert len(parts) >= 2
        assert all(p.isdigit() for p in parts)


# ---------------------------------------------------------------------------
# アプリディレクトリ
# ---------------------------------------------------------------------------

class TestAppDir:
    def test_Pathオブジェクトを返す(self):
        assert isinstance(_app_dir(), Path)

    def test_スクリプト実行時はapp_pyの親ディレクトリを返す(self):
        assert (_app_dir() / "app.py").exists()


# ---------------------------------------------------------------------------
# モジュールレベル定数
# ---------------------------------------------------------------------------

class TestConstants:
    def test_SAVE_DIRはgeneratedディレクトリ(self):
        assert app_module.SAVE_DIR.name == "generated"

    def test_METADATA_FILEはSAVE_DIR配下(self):
        assert app_module.METADATA_FILE.parent == app_module.SAVE_DIR

    def test_SETTINGS_FILEはSAVE_DIR配下(self):
        assert app_module.SETTINGS_FILE.parent == app_module.SAVE_DIR

    def test_左パネル幅は正の整数(self):
        assert app_module.LEFT_W > 0

    def test_最小ウィンドウサイズは正の整数(self):
        assert app_module.WIN_MIN_W > 0
        assert app_module.WIN_MIN_H > 0


# ---------------------------------------------------------------------------
# 上書き時レコード除去フィルタ
# ---------------------------------------------------------------------------

class TestFilterOverwrite:
    def test_一致するQRレコードを除去する(self):
        records = [{"text": "hello", "type": "Q", "path": "...", "error_correction": "M"}]
        assert _filter_overwrite(records, "hello", "Q", "M") == []

    def test_テキストが異なる場合は除去しない(self):
        records = [{"text": "hello", "type": "Q", "path": "...", "error_correction": "M"}]
        result = _filter_overwrite(records, "world", "Q", "M")
        assert result == records

    def test_種別が異なる場合は除去しない(self):
        records = [{"text": "hello", "type": "Q", "path": "...", "error_correction": "M"}]
        result = _filter_overwrite(records, "hello", "B", None)
        assert result == records

    def test_QRは誤り訂正レベルが異なる場合は除去しない(self):
        records = [{"text": "hello", "type": "Q", "path": "...", "error_correction": "M"}]
        result = _filter_overwrite(records, "hello", "Q", "H")
        assert result == records

    def test_バーコードはec無関係で除去する(self):
        records = [{"text": "12345", "type": "B", "path": "..."}]
        assert _filter_overwrite(records, "12345", "B", None) == []

    def test_複数レコードから対象のみ除去する(self):
        records = [
            {"text": "hello", "type": "Q", "path": "...", "error_correction": "M"},
            {"text": "world", "type": "Q", "path": "...", "error_correction": "M"},
        ]
        result = _filter_overwrite(records, "hello", "Q", "M")
        assert len(result) == 1
        assert result[0]["text"] == "world"

    def test_空リストは空リストを返す(self):
        assert _filter_overwrite([], "hello", "Q", "M") == []

    def test_error_correctionなしの旧レコードはQR上書き対象になる(self):
        """error_correction フィールドなしの旧データも上書き除去される"""
        records = [{"text": "hello", "type": "Q", "path": "..."}]
        result = _filter_overwrite(records, "hello", "Q", "M")
        assert result == []

    def test_エンコードが異なる場合は除去しない(self):
        """encoding が UTF-8 のレコードに SJIS で上書きしようとしても除去されない"""
        records = [{"text": "hello", "type": "Q", "path": "...",
                    "error_correction": "M", "encoding": "UTF-8"}]
        result = _filter_overwrite(records, "hello", "Q", "M", encoding="SJIS")
        assert result == records

    def test_エンコードが一致する場合は除去する(self):
        """encoding が一致するレコードは上書き対象として除去される"""
        records = [{"text": "hello", "type": "Q", "path": "...",
                    "error_correction": "M", "encoding": "SJIS"}]
        assert _filter_overwrite(records, "hello", "Q", "M", encoding="SJIS") == []


class TestFilterOverwriteFolder:
    @staticmethod
    def _recs():
        return [
            {"text": "hello", "type": "Q", "path": "root.png", "error_correction": "M"},
            {"text": "hello", "type": "Q", "path": "A/a.png", "error_correction": "M"},
            {"text": "hello", "type": "Q", "path": "B/b.png", "error_correction": "M"},
        ]

    def test_階層を指定すると同じ階層のレコードだけ除去する(self):
        result = _filter_overwrite(self._recs(), "hello", "Q", "M", folder="A")
        assert [r["path"] for r in result] == ["root.png", "B/b.png"]

    def test_空文字を指定するとルートのレコードだけ除去する(self):
        result = _filter_overwrite(self._recs(), "hello", "Q", "M", folder="")
        assert [r["path"] for r in result] == ["A/a.png", "B/b.png"]

    def test_階層名の大文字小文字は区別しない(self):
        result = _filter_overwrite(self._recs(), "hello", "Q", "M", folder="a")
        assert [r["path"] for r in result] == ["root.png", "B/b.png"]

    def test_階層を指定しないときは従来どおり全階層が対象(self):
        assert _filter_overwrite(self._recs(), "hello", "Q", "M") == []


class TestOverwritePredicate:
    def test_一致するレコードだけTrueを返す(self):
        match = _overwrite_predicate("hello", "Q", "M", "UTF-8", "A")
        assert match({"text": "hello", "type": "Q", "path": "A/a.png", "error_correction": "M"}) is True
        assert match({"text": "hello", "type": "Q", "path": "B/a.png", "error_correction": "M"}) is False
        assert match({"text": "world", "type": "Q", "path": "A/a.png", "error_correction": "M"}) is False

    def test_階層の判定より先にテキストで絞り込む(self):
        """大量レコードでの上書き取り込みが遅くならないよう、安価な比較を先に行う。"""
        class Boom(dict):
            def get(self, *a, **k):
                raise AssertionError("path を参照してはいけない")
        match = _overwrite_predicate("hello", "Q", "M", "UTF-8", "A")
        assert match(Boom(text="other", type="Q")) is False


# ---------------------------------------------------------------------------
# 説明コピー文字列取得
# ---------------------------------------------------------------------------

class TestDescriptionForCopy:
    def test_説明ありは説明文字列を返す(self):
        rec = {"text": "https://example.com", "type": "Q", "path": "...", "description": "サンプルサイト"}
        assert _description_for_copy(rec) == "サンプルサイト"

    def test_バーコードでも説明ありは説明文字列を返す(self):
        rec = {"text": "12345678", "type": "B", "path": "...", "description": "商品コード"}
        assert _description_for_copy(rec) == "商品コード"

    def test_説明なしはNoneを返す(self):
        rec = {"text": "https://example.com", "type": "Q", "path": "..."}
        assert _description_for_copy(rec) is None

    def test_説明が空文字はNoneを返す(self):
        rec = {"text": "https://example.com", "type": "Q", "path": "...", "description": ""}
        assert _description_for_copy(rec) is None
