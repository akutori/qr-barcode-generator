# QR & バーコード 生成ツール

テキストを入力して QR コードまたはバーコード (Code128) を生成・保存する Windows 向け GUI アプリケーションです。

## スクリーンショット

![アプリ画面](docs/images/image.png)

## 機能

### コード生成

- QR コード / バーコード (Code128) を PNG 保存
- QR: 誤り訂正レベル選択（L / M / Q / H）
- QR: 複数行テキスト対応（改行を含む QR コード生成可）
- Barcode: ASCII 文字のみ対応

### 生成済み一覧

- テキスト検索（入力テキスト・説明文をリアルタイムフィルタリング）
- ソート（追加日・表示名・テキスト・説明・種別 の昇降順、計 10 パターン）
- 説明フィールド（各コードに独自の説明文を付与、PDF にも反映）
- ダブルクリックで拡大表示（複数ウィンドウ同時表示可）
- 右クリック → テキスト / 画像をクリップボードにコピー
- ホバーで全テキストをツールチップ表示
- ファイル欠損時に ⚠ マーク表示

### 出力

- 複数選択して A4 PDF グリッド出力（列数 1〜6 を選択可、複数ページ対応）
- CSV 一括インポート（CSV / テキストファイルから複数コードをまとめて生成）
- PDF 出力後に自動でファイルを開くオプション

### その他

- 単一 `.exe` として配布可能（インストール不要）
- ポータブル設計（生成画像・履歴・設定がすべて `generated/` 以下に集約）

## 使い方

### バイナリ版（配布用）

1. [GitHub Releases](https://github.com/akutori/qr-barcode-generator/releases/latest) から `QR-Barcode-GUI.exe` をダウンロードして任意の場所に置く
2. ダブルクリックで起動
3. テキストを入力 → コード種別を選択 → **生成して保存**

生成した画像は `.exe` と同じフォルダの `generated/` に保存されます。

### スクリプト版（開発用）

**必要なもの:** Python 3.13+、[uv](https://docs.astral.sh/uv/)

```bash
# 依存関係のインストール
uv sync

# 起動
uv run main.py
```

## 開発

```bash
# テストの実行
uv run pytest

# アイコン生成
uv run python create_icon.py

# バイナリのビルド
uv run pyinstaller --onefile --noconsole --add-data "core.py;." --add-data "assets/icon.ico;assets" --add-data "pyproject.toml;." --icon assets/icon.ico --name QR-Barcode-GUI main.py
```

## プロジェクト構成

```
QR-Barcode-GUI/
├── main.py          # エントリポイント
├── app.py           # GUI (tkinter)
├── core.py          # メタデータ I/O・画像ユーティリティ・ラベル処理
├── generators.py    # QR・バーコード・PDF 生成ロジック
├── csv_import.py    # CSV パース・バリデーション
├── tests/
│   ├── test_core.py
│   └── test_generators.py
├── pyproject.toml
└── generated/       # 生成画像・設定・履歴の保存先（実行時に自動作成）
```

## 依存ライブラリ

| ライブラリ | 用途 |
|---|---|
| [qrcode](https://github.com/lincolnloop/python-qrcode) | QR コード生成 |
| [python-barcode](https://github.com/WhyNotHugo/python-barcode) | バーコード生成 |
| [Pillow](https://python-pillow.org/) | 画像描画・表示・PDF 出力 |
