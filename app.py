import json
import os
import shutil
import subprocess
import sys
import tkinter as tk
import tomllib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageDraw, ImageTk

from core import (
    SORT_OPTION_LABELS,
    FolderExistsError,
    FolderNotEmptyError,
    MoveCollisionError,
    UnmanagedFilesError,
    apply_custom_order,
    build_record_path,
    clamp_panel_width,
    create_folder,
    delete_folder,
    find_move_collisions,
    folder_unmanaged_entries,
    group_by_folder,
    has_duplicate,
    list_folders,
    list_labels,
    list_labels_with_status,
    load_metadata,
    load_settings,
    move_records,
    record_file_path,
    record_folder,
    merge_folder,
    merge_folder_collisions,
    rename_folder,
    resolve_folder_dir,
    same_folder_name,
    save_metadata,
    save_settings,
    sort_records,
    suggested_filename,
    type_label,
    validate_folder_name,
)
from csv_import import (
    ImportRow,
    ParseError,
    RowStatus,
    format_ec_for_display,
    format_encoding_for_display,
    format_folder_for_display,
    format_text_for_display,
    generate_template,
    parse_csv,
    validate_all,
)
from generators import generate_barcode_file, generate_pdf_grid, generate_qr

_SORT_LABEL_TO_KEY: dict[str, str] = {v: k for k, v in SORT_OPTION_LABELS.items()}


def _read_version() -> str:
    """pyproject.toml からバージョンを読む。開発時・PyInstaller exe 時いずれも対応。"""
    p = Path(__file__).parent / "pyproject.toml"
    try:
        return tomllib.loads(p.read_text(encoding="utf-8"))["project"]["version"]
    except Exception:
        return "unknown"


_VERSION = _read_version()

_FONT = "Meiryo"
LEFT_W = 310        # 左パネル既定幅 (px)。ドラッグでリサイズ可能
WIN_MIN_W = 560
WIN_MIN_H = 420

_DRAG_SCROLL_MARGIN = 20     # この px 数以内にカーソルがあると自動スクロール
_DRAG_SCROLL_INTERVAL_MS = 80


def _app_dir() -> Path:
    """実行ファイルと同じディレクトリを返す。
    PyInstaller でバイナリ化した場合は .exe の場所、
    スクリプト実行時は app.py の場所。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


SAVE_DIR = _app_dir() / "generated"
METADATA_FILE = SAVE_DIR / "metadata.json"
SETTINGS_FILE = SAVE_DIR / "settings.json"

_TYPE_DISP: dict[str, str] = {"Q": "QR", "B": "Barcode"}


def _overwrite_predicate(
    text: str,
    code_type: str,
    ec: str | None,
    encoding: str | None = None,
    folder: str | None = None,
) -> Callable[[dict], bool]:
    """上書きモードで置き換え対象になるレコードかを判定する関数を返す。

    QR の場合: ec と encoding が一致するレコード、および ec フィールドなしの旧レコードが対象。
    encoding=None のとき encoding チェックはスキップ（既存動作と互換）。
    folder が None でなければ、その階層（空文字はルート）のレコードだけを対象にする。
    大量のレコードを行ごとに走査するため、安価な比較（テキスト・種別）を先に行う。
    """
    def _matches(r: dict) -> bool:
        if r["text"] != text or r["type"] != code_type:
            return False
        if folder is not None and not same_folder_name(record_folder(r), folder):
            return False
        if code_type != "Q":
            return True
        if "error_correction" in r and r["error_correction"] != ec:
            return False
        if encoding is not None:
            rec_enc = r.get("encoding", "UTF-8")
            if rec_enc != encoding:
                return False
        return True

    return _matches


def _filter_overwrite(
    records: list[dict],
    text: str,
    code_type: str,
    ec: str | None,
    encoding: str | None = None,
    folder: str | None = None,
) -> list[dict]:
    """上書きモード時に対象レコードを除去した新しいリストを返す。"""
    matches = _overwrite_predicate(text, code_type, ec, encoding, folder)
    return [r for r in records if not matches(r)]


def _strip_indicator(layout: list) -> list:
    """ttk のレイアウトから、標準の開閉マーク（インジケータ）の要素を取り除いた新しいレイアウトを返す。

    標準の開閉マークは、子を持たないルート直下の行にも空きを確保する。ルートのコードが右にずれて
    階層内のコードに見えるのを防ぐため、開閉マークは階層行だけに自前で表示する。
    """
    result = []
    for name, opts in layout:
        if name.endswith("indicator"):
            continue
        opts = dict(opts)
        if "children" in opts:
            opts["children"] = _strip_indicator(opts["children"])
        result.append((name, opts))
    return result


_FOLDER_ICON_SIZE = 14
_FOLDER_ICON_KINDS = ("closed", "open", "empty")


def _draw_folder_icon(kind: str, scale: int = 1) -> Image.Image:
    """階層行の先頭に出す開閉マークを描く。closed=▶ / open=▼ / empty=中身がない階層の点。"""
    if kind not in _FOLDER_ICON_KINDS:
        raise ValueError(f"不明なアイコン種別: {kind}")
    size = _FOLDER_ICON_SIZE * scale
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    dark, light = (60, 60, 60, 255), (150, 150, 150, 255)
    if kind == "closed":
        draw.polygon([(4 * scale, 2 * scale), (4 * scale, 11 * scale), (10 * scale, 6.5 * scale)], fill=dark)
    elif kind == "open":
        draw.polygon([(2 * scale, 4 * scale), (11 * scale, 4 * scale), (6.5 * scale, 10 * scale)], fill=dark)
    else:
        draw.ellipse([4 * scale, 4 * scale, 9 * scale, 9 * scale], fill=light)
    return img


def _description_for_copy(rec: dict) -> str | None:
    """説明をコピーする文字列を返す。説明がない・空の場合は None を返す。"""
    desc = rec.get("description", "")
    return desc if desc else None


# ---------------------------------------------------------------------------
# 拡大表示ウィンドウ
# ---------------------------------------------------------------------------

def show_enlarged(record: dict, root: tk.Tk, save_dir: Path) -> None:
    resolved_path = record_file_path(save_dir, record)
    if resolved_path is None:
        messagebox.showerror("エラー", "画像ファイルのパスが不正です。", parent=root)
        return
    try:
        orig_img = Image.open(resolved_path).convert("RGB")
    except Exception as e:
        messagebox.showerror("エラー", f"画像を読み込めません:\n{e}", parent=root)
        return

    top = tk.Toplevel(root)
    top.title("拡大表示")
    top.geometry("320x500")
    top.minsize(220, 320)
    top.resizable(True, True)

    # ── ボタン → info → 画像 の順に bottom から積む ──────────────────────────
    # 先に bottom 側を確保することで、画像が expand しても閉じるボタンが常に見える
    btn_f = tk.Frame(top)
    btn_f.pack(side="bottom", fill="x", padx=6, pady=(0, 6))
    tk.Button(btn_f, text="閉じる", width=10, command=top.destroy,
              font=(_FONT, 10)).pack(pady=3)

    info = tk.Frame(top)
    info.pack(side="bottom", fill="x", padx=6, pady=(0, 2))

    tk.Label(info, text=f"保存先: {resolved_path}",
             font=(_FONT, 8), fg="gray", anchor="w", justify="left").pack(fill="x")
    tk.Label(info, text=f"[{type_label(record)}]",
             font=(_FONT, 10, "bold"), anchor="w").pack(fill="x")
    desc = record.get("description", "")
    if desc:
        tk.Label(info, text=f"説明: {desc}",
                 font=(_FONT, 9), fg="gray", anchor="w").pack(fill="x")

    # テキスト内容: 4行固定でスクロール可能（読み取り専用）
    txt_f = tk.Frame(info)
    txt_f.pack(fill="x", pady=(1, 2))
    _sb = tk.Scrollbar(txt_f, orient="vertical")
    _sb.pack(side="right", fill="y")
    _txt = tk.Text(txt_f, height=3, font=(_FONT, 10),
                   yscrollcommand=_sb.set, wrap="word",
                   relief="flat", bd=0, bg=top.cget("bg"),
                   state="normal")
    _txt.insert("1.0", record["text"])
    _txt.config(state="disabled")
    _txt.pack(side="left", fill="x", expand=True)
    _sb.config(command=_txt.yview)

    img_label = tk.Label(top, bg="white", anchor="center")
    img_label.pack(expand=True, fill="both", padx=6, pady=(6, 0))

    _photo = [None]

    def redraw(event: tk.Event | None = None) -> None:
        if event and event.widget is not img_label:
            return
        w = img_label.winfo_width()
        h = img_label.winfo_height()
        if w <= 1 or h <= 1:
            return
        img = orig_img.copy()
        img.thumbnail((w, h), Image.LANCZOS)
        _photo[0] = ImageTk.PhotoImage(img)
        img_label.configure(image=_photo[0])

    img_label.bind("<Configure>", redraw)
    top.after(50, redraw)


# ---------------------------------------------------------------------------
# メインアプリ
# ---------------------------------------------------------------------------

class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("QR & バーコード 生成ツール")
        self.root.minsize(WIN_MIN_W, WIN_MIN_H)
        self.root.geometry("730x510")

        SAVE_DIR.mkdir(exist_ok=True)
        self.records = self._load_metadata_safe()
        self.settings = load_settings(SETTINGS_FILE)
        self.current_path: str | None = None
        self._photo = None  # ImageTk.PhotoImage の GC 防止
        self._current_rec_idx: int | None = None
        self._current_rec: dict | None = None  # プレビュー中のレコード（self.records の再代入・削除で位置がずれても追従する）
        self._tooltip_win: tk.Toplevel | None = None
        self._tooltip_after: str | None = None
        self._tooltip_rec_idx: int = -1
        self._groups: dict[str, list[int]] = {"": []}  # 表示中のレコード位置（階層ごと・ソート済み。"" がルート）
        self._folders: list[str] = []
        self._iid_folder: dict[str, str] = {}          # 階層行の iid → 階層名
        self._tree_recs: list[dict] = []               # 直近のツリー構築時のレコード（iid の r{n} は n 番目）
        self._open_names: dict[str, str] = {f.lower(): f for f in self.settings["open_folders"]}  # 展開中の階層（小文字名 → 表示名）
        self._context_rec_idx: int | None = None
        self._folder_icons: dict[str, ImageTk.PhotoImage] = {}  # 階層行の開閉マーク（GC 防止を兼ねる）
        self._context_folder: str | None = None                 # 右クリックされた階層名
        self._drag_iid: str | None = None
        self._drag_moved: bool = False
        self._dragging: bool = False
        self._drag_scroll_after_id: str | None = None
        self._drag_last_y: int = 0

        self._build_menu()
        self._build_ui()
        self._filter_records()
        if self.records:
            self._show_record(self.records[-1])

    # ── メニュー ──────────────────────────────────────────────────────────────

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="CSVインポート", command=self._open_import_dialog)
        file_menu.add_command(label="階層を作成...", command=self.on_create_folder)
        file_menu.add_command(label="フォルダを開く", command=self.on_open_folder)
        file_menu.add_separator()
        file_menu.add_command(label="終了", command=self.root.quit)
        menubar.add_cascade(label="ファイル", menu=file_menu)

        opt_menu = tk.Menu(menubar, tearoff=0)
        self._warn_var = tk.BooleanVar(value=self.settings.get("warn_on_duplicate", True))
        opt_menu.add_checkbutton(
            label="重複確認ダイアログを表示する",
            variable=self._warn_var,
            command=self._on_warn_toggle,
        )
        self._auto_open_var = tk.BooleanVar(value=self.settings.get("auto_open_pdf", False))
        opt_menu.add_checkbutton(
            label="PDF出力後に自動で開く",
            variable=self._auto_open_var,
            command=self._on_auto_open_toggle,
        )
        opt_menu.add_separator()
        opt_menu.add_command(label="パネル幅をリセット", command=self._reset_panel_width)
        menubar.add_cascade(label="オプション", menu=opt_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="バージョン情報", command=self._show_about)
        menubar.add_cascade(label="ヘルプ", menu=help_menu)

        self.root.config(menu=menubar)

    def _on_warn_toggle(self) -> None:
        self.settings["warn_on_duplicate"] = self._warn_var.get()
        save_settings(self.settings, SETTINGS_FILE)

    def _on_auto_open_toggle(self) -> None:
        self.settings["auto_open_pdf"] = self._auto_open_var.get()
        save_settings(self.settings, SETTINGS_FILE)

    def _show_about(self) -> None:
        messagebox.showinfo(
            "バージョン情報",
            f"QR & バーコード 生成ツール\nバージョン {_VERSION}",
            parent=self.root,
        )

    # ── CSVインポートダイアログ ────────────────────────────────────────────────

    def _open_import_dialog(self) -> None:
        if hasattr(self, "_import_dlg") and self._import_dlg.winfo_exists():
            self._import_dlg.lift()
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("CSVインポート")
        dlg.geometry("840x520")
        dlg.minsize(600, 400)
        dlg.resizable(True, True)
        self._import_dlg = dlg

        _parsed: list[ImportRow] = []   # CSV を読み込んだ直後の行（階層・重複の検証前）
        _rows: list[ImportRow] = []     # 検証済みの行
        _dup_mode = tk.StringVar(value="skip")
        _folder_mode = tk.StringVar(value="create")  # 存在しない階層: create=自動作成 / error=エラー

        # ── ボタン行（上部）──────────────────────────────────────────────────
        top_f = tk.Frame(dlg)
        top_f.pack(fill="x", padx=8, pady=(8, 4))

        def _save_template() -> None:
            path = filedialog.asksaveasfilename(
                parent=dlg, defaultextension=".csv",
                filetypes=[("CSV ファイル", "*.csv")],
                title="テンプレートを保存",
                initialfile="import_template.csv",
            )
            if not path:
                return
            try:
                Path(path).write_text(generate_template(), encoding="utf-8-sig")
                if sys.platform == "win32":
                    os.startfile(path)
                elif sys.platform == "darwin":
                    subprocess.run(["open", path])
                else:
                    subprocess.run(["xdg-open", path])
            except OSError as e:
                messagebox.showerror("エラー", f"保存に失敗しました:\n{e}", parent=dlg)

        tk.Button(top_f, text="テンプレートを保存して開く", font=(_FONT, 9),
                  command=_save_template).pack(side="left")

        def _select_file() -> None:
            path = filedialog.askopenfilename(
                parent=dlg,
                filetypes=[("CSV ファイル", "*.csv"), ("すべてのファイル", "*.*")],
                title="CSVファイルを選択",
            )
            if not path:
                return
            try:
                parsed = parse_csv(Path(path))
            except ParseError as e:
                messagebox.showerror("CSVエラー", str(e), parent=dlg)
                return
            _parsed[:] = parsed
            if not parsed:
                # ヘッダーだけの CSV を選び直したときに、前のファイルのプレビューを残さない
                _rows.clear()
                _refresh_preview()
                return
            _revalidate(force=True)

        def _revalidate(force: bool = False) -> None:
            """階層・重複の検証をやり直す（ファイル読み込み時・「存在しない階層」の切り替え時・取り込み直前）。"""
            if not _parsed:
                return
            # folder 列のない CSV は「存在しない階層」の設定で結果が変わらないため、切り替え時の再検証を省く
            if not force and not any(r.folder for r in _parsed):
                return
            # レコードだけが参照していてディレクトリが無い階層は、取り込み時に保存先として使えないため既存扱いにしない
            folders = [f for f in list_folders(SAVE_DIR, self.records) if (SAVE_DIR / f).is_dir()]
            _rows[:] = validate_all(
                _parsed, self.records, folders,
                create_missing_folders=_folder_mode.get() == "create",
            )
            _refresh_preview()

        tk.Button(top_f, text="CSVファイルを選択...", font=(_FONT, 9),
                  command=_select_file).pack(side="left", padx=(6, 0))

        # ── 下部ボタン（bottom から先に確保してウィンドウ縮小で潰れないようにする）──
        btn_f = tk.Frame(dlg)
        btn_f.pack(side="bottom", fill="x", padx=8, pady=(0, 8))
        tk.Button(btn_f, text="キャンセル", font=(_FONT, 10),
                  command=dlg.destroy).pack(side="right", padx=(4, 0))
        import_btn = tk.Button(btn_f, text="インポート開始", font=(_FONT, 10, "bold"),
                               state="disabled", command=lambda: _do_import())
        import_btn.pack(side="right")

        # ── 重複オプション ───────────────────────────────────────────────────
        dup_f = tk.Frame(dlg)
        dup_f.pack(side="bottom", fill="x", padx=8, pady=(2, 4))
        tk.Label(dup_f, text="重複時:", font=(_FONT, 9)).pack(side="left")
        tk.Radiobutton(dup_f, text="スキップ", variable=_dup_mode,
                       value="skip", font=(_FONT, 9)).pack(side="left", padx=(4, 0))
        tk.Radiobutton(dup_f, text="上書き", variable=_dup_mode,
                       value="overwrite", font=(_FONT, 9)).pack(side="left", padx=(4, 0))
        tk.Radiobutton(dup_f, text="そのまま追加", variable=_dup_mode,
                       value="add", font=(_FONT, 9)).pack(side="left", padx=(4, 0))

        # ── 存在しない階層のオプション ─────────────────────────────────────────
        folder_f = tk.Frame(dlg)
        folder_f.pack(side="bottom", fill="x", padx=8, pady=(2, 0))
        tk.Label(folder_f, text="存在しない階層:", font=(_FONT, 9)).pack(side="left")
        tk.Radiobutton(folder_f, text="自動作成", variable=_folder_mode,
                       value="create", font=(_FONT, 9)).pack(side="left", padx=(4, 0))
        tk.Radiobutton(folder_f, text="エラーにする", variable=_folder_mode,
                       value="error", font=(_FONT, 9)).pack(side="left", padx=(4, 0))
        _folder_mode.trace_add("write", lambda *_: _revalidate())

        # ── サマリーラベル ───────────────────────────────────────────────────
        summary_var = tk.StringVar(value="CSVファイルを選択してください。")
        tk.Label(dlg, textvariable=summary_var, font=(_FONT, 9),
                 anchor="w").pack(side="bottom", fill="x", padx=8)

        # ── ヒント行 ─────────────────────────────────────────────────────────
        hint_f = tk.Frame(dlg)
        hint_f.pack(fill="x", padx=8, pady=(0, 2))
        tk.Label(hint_f,
                 text="種別: QR（または Q）/ Barcode（または B）  ｜  誤り訂正: L / M / Q / H（空欄=M）  ｜  エンコード: UTF-8 / SJIS（省略=UTF-8）\n"
                      "folder: 取り込み先の階層名（省略・空欄=ルート。1 階層まで）",
                 font=(_FONT, 8), fg="#666666", anchor="w", justify="left").pack(fill="x")

        # ── プレビュー（Treeview）────────────────────────────────────────────
        cols = ("status", "type", "folder", "text", "description", "ec", "encoding", "error")
        tree_f = tk.Frame(dlg)
        tree_f.pack(fill="both", expand=True, padx=8, pady=4)

        vsb = tk.Scrollbar(tree_f, orient="vertical")
        vsb.pack(side="right", fill="y")
        hsb = tk.Scrollbar(tree_f, orient="horizontal")
        hsb.pack(side="bottom", fill="x")
        tree = ttk.Treeview(tree_f, columns=cols, show="headings",
                            yscrollcommand=vsb.set, xscrollcommand=hsb.set, height=12)
        vsb.config(command=tree.yview)
        hsb.config(command=tree.xview)

        tree.heading("status",   text="状態")
        tree.heading("type",     text="種別")
        tree.heading("folder",   text="階層")
        tree.heading("text",     text="テキスト")
        tree.heading("description", text="説明")
        tree.heading("ec",       text="誤り訂正")
        tree.heading("encoding", text="エンコード")
        tree.heading("error",    text="エラー詳細")
        tree.column("status",   width=50,  stretch=False, anchor="center")
        tree.column("type",     width=70,  stretch=False, anchor="center")
        tree.column("folder",   width=140, stretch=False)
        tree.column("text",     width=220, stretch=False)
        tree.column("description", width=110, stretch=False)
        tree.column("ec",       width=60,  stretch=False, anchor="center")
        tree.column("encoding", width=70,  stretch=False, anchor="center")
        tree.column("error",    width=240, stretch=False)
        tree.pack(fill="both", expand=True)
        tree.tag_configure("ok",  background="#e8f5e9")
        tree.tag_configure("dup", background="#fff9c4")
        tree.tag_configure("err", background="#ffebee")

        def _refresh_preview() -> None:
            for item in tree.get_children():
                tree.delete(item)
            n_ok = n_dup = n_err = 0
            for r in _rows:
                if r.status == RowStatus.OK:
                    icon, tag = "✅", "ok";  n_ok += 1
                elif r.status == RowStatus.DUPLICATE:
                    icon, tag = "⚠", "dup"; n_dup += 1
                else:
                    icon, tag = "❌", "err"; n_err += 1
                text_disp = format_text_for_display(r.text)
                tree.insert("", "end", tags=(tag,), values=(
                    icon, _TYPE_DISP.get(r.code_type, r.code_type),
                    format_folder_for_display(r),
                    text_disp, r.description,
                    format_ec_for_display(r), format_encoding_for_display(r),
                    r.error_msg,
                ))
            total = len(_rows)
            new_folders = {r.folder.lower() for r in _rows if r.new_folder and r.status != RowStatus.ERROR}
            folder_note = f"  📁 新規階層 {len(new_folders)}件" if new_folders else ""
            summary_var.set(
                f"全{total}件：✅ {n_ok}件  ⚠ {n_dup}件  ❌ {n_err}件{folder_note}"
                if total else "データがありません。"
            )
            import_btn.config(state="normal" if total > 0 else "disabled")

        def _do_import() -> None:
            # このダイアログは開いたまま本体を操作できるため、取り込み直前の状態で検証し直す
            _revalidate(force=True)
            mode = _dup_mode.get()
            create_folders = _folder_mode.get() == "create"
            n_ok = n_dup = n_err = 0
            ts_base = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            original = list(self.records)
            overwritten: list[dict] = []
            created_files: set[Path] = set()
            used_folders: set[str] = set()
            created_folders: set[str] = set()
            for i, row in enumerate(_rows):
                if row.status == RowStatus.ERROR:
                    n_err += 1
                    continue
                match: Callable[[dict], bool] | None = None
                replaced: list[dict] = []
                if row.status == RowStatus.DUPLICATE:
                    if mode == "skip":
                        n_dup += 1
                        continue
                    elif mode == "overwrite":
                        # 取り込み先の階層内の既存レコードを置き換える。新しいコードを作れてから反映する
                        ec = row.error_correction if row.code_type == "Q" else None
                        enc = row.encoding if row.code_type == "Q" else None
                        match = _overwrite_predicate(
                            row.text, row.code_type, ec, encoding=enc, folder=row.folder
                        )
                        replaced = [r for r in self.records if match(r)]
                    # mode == "add": 何もしない → そのまま追加処理へ
                ts = f"{ts_base}_{i:04d}"
                created_dir: Path | None = None
                try:
                    dest_dir = resolve_folder_dir(SAVE_DIR, row.folder)
                    if row.folder and not dest_dir.is_dir():
                        if not create_folders:
                            raise FileNotFoundError(row.folder)
                        dest_dir.mkdir()
                        created_dir = dest_dir
                    if row.code_type == "Q":
                        fp = dest_dir / f"qr_{ts}.png"
                        generate_qr(row.text, fp, error_correction=row.error_correction,
                                    encoding=row.encoding)
                        rec: dict = {
                            "text": row.text, "type": row.code_type,
                            "path": build_record_path(row.folder, fp.name),
                            "error_correction": row.error_correction,
                            "encoding": row.encoding,
                        }
                    else:
                        fp = generate_barcode_file(row.text, dest_dir / f"bar_{ts}")
                        rec = {"text": row.text, "type": row.code_type,
                               "path": build_record_path(row.folder, fp.name)}
                    if row.description:
                        rec["description"] = row.description
                    if match is not None:
                        self.records = [r for r in self.records if not match(r)]
                        overwritten.extend(replaced)
                    self.records.append(rec)
                    created_files.add(fp)
                    if created_dir is not None:
                        created_folders.add(row.folder)
                    if row.folder:
                        used_folders.add(row.folder)
                    n_ok += 1
                except Exception:
                    if created_dir is not None:
                        try:
                            created_dir.rmdir()  # 失敗した行のために作った空の階層は残さない
                        except OSError:
                            pass
                    n_err += 1

            try:
                save_metadata(self.records, METADATA_FILE)
            except Exception as e:
                # 保存できなかったときは、メモリ・画像・作成した階層を取り込み前に戻す
                self.records = original
                for fp in created_files:
                    try:
                        fp.unlink(missing_ok=True)
                    except OSError:
                        pass
                for folder in created_folders:
                    try:
                        (SAVE_DIR / folder).rmdir()
                    except OSError:
                        pass
                self._filter_records()
                messagebox.showerror(
                    "エラー", f"メタデータの保存に失敗したため、取り込みを取り消しました:\n{e}", parent=dlg,
                )
                return
            # 取り消せない削除は、メタデータの保存が済んでから行う。
            # 取り込み後のレコードが使っている画像は消さない（旧画像と新画像が同じ名前になった場合を含む）。
            # 同じ取り込みの中で先に作って後の行に置き換えられた画像は、どのレコードも使わないため消す
            in_use = {
                path for r in self.records
                if (path := record_file_path(SAVE_DIR, r)) is not None
            }
            for old in overwritten:
                old_path = record_file_path(SAVE_DIR, old)
                if old_path is not None and old_path not in in_use:
                    try:
                        old_path.unlink(missing_ok=True)
                    except OSError:
                        pass
            for folder in sorted(used_folders):
                self._remember_open(folder, True)  # 取り込んだコードが折りたたまれた階層に隠れないようにする
            self._filter_records()
            dlg.destroy()
            dup_label = {"skip": "重複スキップ", "overwrite": "重複上書き",
                         "add": "重複追加"}[mode]
            folder_line = f"\n新規階層: {len(created_folders)}件" if created_folders else ""
            messagebox.showinfo(
                "インポート完了",
                f"成功: {n_ok}件\n{dup_label}: {n_dup}件\nエラー: {n_err}件{folder_line}",
                parent=self.root,
            )

    # ── 設定変更コールバック ──────────────────────────────────────────────────

    def _on_type_change(self) -> None:
        t = self.type_var.get()
        if t == "Q":
            # B → Q: Entry の内容を qr_text に引き継いで切り替え
            current = self.entry_var.get()
            self.entry.pack_forget()
            self.qr_text.pack(fill="x")   # コンテナ内のため before= 不要
            if current:
                self.qr_text.delete("1.0", "end")
                self.qr_text.insert("1.0", current)
            self._ec_frame.pack(fill="x", pady=(0, 4), after=self._radio_f)
            self._enc_frame.pack(fill="x", pady=(0, 4), after=self._ec_frame)
        else:
            # Q → B: qr_text の先頭行を Entry に引き継いで切り替え
            first_line = self.qr_text.get("1.0", "end-1c").split("\n")[0].strip()
            self.qr_text.pack_forget()
            self.entry.pack(fill="x")     # コンテナ内のため before= 不要
            self.entry_var.set(first_line)
            self._ec_frame.pack_forget()
            self._enc_frame.pack_forget()
        self.settings["default_type"] = t
        save_settings(self.settings, SETTINGS_FILE)

    def _on_ec_change(self, *_) -> None:
        self.settings["qr_error_correction"] = self._ec_var.get()
        save_settings(self.settings, SETTINGS_FILE)

    def _on_enc_change(self, *_) -> None:
        self.settings["qr_encoding"] = self._enc_var.get()
        save_settings(self.settings, SETTINGS_FILE)

    def _on_pdf_cols_change(self, *_) -> None:
        try:
            self.settings["pdf_cols"] = self._pdf_cols_var.get()
            save_settings(self.settings, SETTINGS_FILE)
        except tk.TclError:
            pass

    def _reset_panel_width(self) -> None:
        """左パネル幅を既定値に戻す（サッシュのダブルクリック・オプションメニューの両方から呼ばれる）"""
        self._paned.sash_place(0, LEFT_W, 0)
        self.settings["left_panel_w"] = LEFT_W
        save_settings(self.settings, SETTINGS_FILE)

    def _on_paned_double_click(self, event: tk.Event) -> None:
        ident = self._paned.identify(event.x, event.y)
        if ident and ident[1] in ("sash", "handle"):
            self._reset_panel_width()

    def _on_paned_sash_release(self, _: tk.Event) -> None:
        try:
            x, _y = self._paned.sash_coord(0)
        except tk.TclError:
            return
        self.settings["left_panel_w"] = clamp_panel_width(x)
        save_settings(self.settings, SETTINGS_FILE)

    def _load_metadata_safe(self) -> list[dict]:
        try:
            return load_metadata(METADATA_FILE)
        except json.JSONDecodeError:
            answer = messagebox.askyesno(
                "データエラー",
                "metadata.json が破損しているため読み込めませんでした。\n\n"
                "バックアップを作成して一覧を空にしますか？\n"
                "（「いいえ」を選択するとアプリを終了します）",
                parent=self.root,
            )
            if answer:
                backup = METADATA_FILE.with_suffix(".json.bak")
                shutil.copy2(METADATA_FILE, backup)
                records: list[dict] = []
                save_metadata(records, METADATA_FILE)
                messagebox.showinfo(
                    "バックアップ完了",
                    f"バックアップを作成しました。\n{backup}",
                    parent=self.root,
                )
                return records
            else:
                self.root.destroy()
                raise SystemExit

    # ── UI 構築 ────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        ttk.Style().configure("Treeview", rowheight=24)

        self._paned = tk.PanedWindow(
            self.root, orient=tk.HORIZONTAL, sashwidth=6, sashrelief="raised",
            bg="#d9d9d9",
        )
        self._paned.pack(expand=True, fill="both", padx=4, pady=4)
        self._paned.bind("<Double-Button-1>", self._on_paned_double_click)
        self._paned.bind("<ButtonRelease-1>", self._on_paned_sash_release)

        lf = tk.Frame(self._paned)
        initial_w = clamp_panel_width(self.settings.get("left_panel_w", LEFT_W))
        self._paned.add(lf, width=initial_w, minsize=220)

        tk.Label(lf, text="テキスト入力:", font=(_FONT, 11, "bold"),
                 anchor="w").pack(fill="x")

        # ── 入力コンテナ（常時表示）──────────────────────────────────────────
        # ヒントを先に bottom で詰め、入力ウィジェットが上に配置される
        self._input_frame = tk.Frame(lf)
        self._input_frame.pack(fill="x", pady=(0, 4))

        tk.Label(
            self._input_frame,
            text="Ctrl+Enter で生成",
            font=(_FONT, 8), fg="gray", anchor="e",
        ).pack(side="bottom", fill="x")

        # ── QR 用: 2行テキストエリア（改行対応） ───────────────────────────────
        self.qr_text = tk.Text(self._input_frame, font=(_FONT, 11), height=2,
                               wrap="word", relief="sunken", bd=2)
        self.qr_text.bind("<Control-Return>", lambda e: (self.on_generate(), "break")[1])

        # ── Barcode 用: 単行 Entry ───────────────────────────────────────────
        self.entry_var = tk.StringVar()
        self.entry = tk.Entry(self._input_frame, textvariable=self.entry_var, font=(_FONT, 11))
        self.entry.bind("<Control-Return>", lambda _: self.on_generate())

        self._radio_f = radio_f = tk.Frame(lf)
        radio_f.pack(fill="x", pady=(0, 2))
        self.type_var = tk.StringVar(value=self.settings.get("default_type", "Q"))
        tk.Radiobutton(radio_f, text="QR コード", variable=self.type_var,
                       value="Q", font=(_FONT, 10),
                       command=self._on_type_change).pack(side="left")
        tk.Radiobutton(radio_f, text="バーコード (Code128)", variable=self.type_var,
                       value="B", font=(_FONT, 10),
                       command=self._on_type_change).pack(side="left")

        # 初期タイプに応じて入力ウィジェットを表示
        if self.type_var.get() == "Q":
            self.qr_text.pack(fill="x")
        else:
            self.entry.pack(fill="x")

        # 誤り訂正レベル（QR 選択時のみ表示）
        self._ec_frame = tk.Frame(lf)
        self._ec_var = tk.StringVar(value=self.settings.get("qr_error_correction", "M"))
        self._ec_var.trace_add("write", self._on_ec_change)
        tk.Label(self._ec_frame, text="誤り訂正:",
                 font=(_FONT, 9), fg="gray").pack(side="left")
        ttk.Combobox(self._ec_frame, textvariable=self._ec_var,
                     values=["L", "M", "Q", "H"], width=3,
                     state="readonly", font=(_FONT, 9)).pack(side="left", padx=(4, 0))
        tk.Label(self._ec_frame, text="L=低  M=中  Q=高  H=最高",
                 font=(_FONT, 8), fg="gray").pack(side="left", padx=(6, 0))
        if self.type_var.get() == "Q":
            self._ec_frame.pack(fill="x", pady=(0, 4))

        # エンコード（QR 選択時のみ表示）
        self._enc_frame = tk.Frame(lf)
        self._enc_var = tk.StringVar(value=self.settings.get("qr_encoding", "UTF-8"))
        self._enc_var.trace_add("write", self._on_enc_change)
        tk.Label(self._enc_frame, text="エンコード:",
                 font=(_FONT, 9), fg="gray").pack(side="left")
        ttk.Combobox(self._enc_frame, textvariable=self._enc_var,
                     values=["UTF-8", "SJIS"], width=6,
                     state="readonly", font=(_FONT, 9)).pack(side="left", padx=(4, 0))
        tk.Label(self._enc_frame, text="SJIS=業務スキャナー向け",
                 font=(_FONT, 8), fg="gray").pack(side="left", padx=(6, 0))
        if self.type_var.get() == "Q":
            self._enc_frame.pack(fill="x", pady=(0, 4))

        tk.Button(lf, text="生成して保存", font=(_FONT, 11, "bold"),
                  command=self.on_generate).pack(fill="x", pady=(0, 8))

        ttk.Separator(lf, orient="horizontal").pack(fill="x", pady=(0, 4))

        tk.Label(lf, text="生成済み一覧",
                 font=(_FONT, 10, "bold"), anchor="w").pack(fill="x")
        tk.Label(lf, text="ダブルクリック: 拡大  Ctrl+クリック: 複数選択\n右クリック: 階層へ移動・コピー",
                 font=(_FONT, 8), fg="gray", anchor="w", justify="left").pack(fill="x")

        search_row = tk.Frame(lf)
        search_row.pack(fill="x", pady=(2, 0))
        search_row.columnconfigure(1, weight=1)

        tk.Label(search_row, text="検索:", font=(_FONT, 10)).grid(
            row=0, column=0, sticky="w", padx=(0, 4))
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._filter_records())
        tk.Entry(search_row, textvariable=self._search_var, font=(_FONT, 10)).grid(
            row=0, column=1, sticky="ew")
        saved_key = self.settings.get("sort_order", "date_new")
        self._sort_var = tk.StringVar(
            value=SORT_OPTION_LABELS.get(saved_key, SORT_OPTION_LABELS["date_new"]))
        ttk.Combobox(
            search_row, textvariable=self._sort_var,
            values=list(SORT_OPTION_LABELS.values()), state="readonly", width=12,
            font=(_FONT, 9),
        ).grid(row=0, column=2, sticky="e", padx=(4, 0))
        self._sort_var.trace_add("write", lambda *_: self._on_sort_change())

        # ── ボタン群を先に bottom で確保（リストボックスが縮んでもボタンが常に表示される）──
        pdf_f = tk.Frame(lf)
        pdf_f.pack(side="bottom", fill="x", pady=(4, 0))
        tk.Button(pdf_f, text="選択してPDF出力", font=(_FONT, 9),
                  command=self.on_export_pdf).pack(side="left", fill="x", expand=True)
        tk.Label(pdf_f, text="列:", font=(_FONT, 9), fg="gray").pack(side="left", padx=(6, 2))
        self._pdf_cols_var = tk.IntVar(value=self.settings.get("pdf_cols", 3))
        self._pdf_cols_var.trace_add("write", self._on_pdf_cols_change)
        tk.Spinbox(pdf_f, textvariable=self._pdf_cols_var,
                   from_=1, to=6, width=3, font=(_FONT, 13)).pack(side="left")

        btn_f = tk.Frame(lf)
        btn_f.pack(side="bottom", fill="x")
        # 左パネルの最小幅でも 3 つ目のボタンが切れないよう、短い「削除」は固定幅にして
        # 他の 2 つだけを grid の重みで伸縮させる
        for col, (label, command) in enumerate([
            ("削除", self.on_delete),
            ("フォルダを開く", self.on_open_folder),
            ("階層を作成", self.on_create_folder),
        ]):
            btn_f.columnconfigure(col, weight=0 if col == 0 else 1)
            tk.Button(btn_f, text=label, font=(_FONT, 9), command=command).grid(
                row=0, column=col, sticky="ew", padx=(0 if col == 0 else 4, 0))

        # ── 生成済み一覧（階層ツリー。残り領域を占有）──────────────────────────
        lb_f = tk.Frame(lf)
        lb_f.pack(expand=True, fill="both", pady=(2, 4))

        sb = tk.Scrollbar(lb_f)
        sb.pack(side="right", fill="y")
        style = ttk.Style()
        scale = max(1, round(self.root.winfo_fpixels("1i") / 96))
        style.layout("Records.Treeview.Item", _strip_indicator(style.layout("Treeview.Item")))
        style.configure("Records.Treeview", font=("Consolas", 10), rowheight=max(20, 16 * scale),
                        indent=18 * scale)
        self._folder_icons = {
            kind: ImageTk.PhotoImage(_draw_folder_icon(kind, scale)) for kind in _FOLDER_ICON_KINDS
        }
        self.tree = ttk.Treeview(lb_f, show="tree", selectmode="extended",
                                 style="Records.Treeview", yscrollcommand=sb.set)
        self.tree.column("#0", stretch=True, minwidth=150, width=250)
        self.tree.tag_configure("folder", font=("Consolas", 10, "bold"))
        self.tree.pack(expand=True, fill="both")
        sb.config(command=self.tree.yview)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<<TreeviewOpen>>", lambda _: self._on_tree_open_close(True))
        self.tree.bind("<<TreeviewClose>>", lambda _: self._on_tree_open_close(False))
        self.tree.bind("<Double-Button-1>", self._on_tree_double)
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        self.tree.bind("<Motion>", self._on_tree_hover)
        self.tree.bind("<Leave>", lambda _: self._hide_tooltip())
        self.tree.bind("<Button-1>", self._on_tree_press, add="+")
        self.tree.bind("<B1-Motion>", self._on_tree_drag_motion)
        self.tree.bind("<ButtonRelease-1>", self._on_tree_release)

        self._context_menu = tk.Menu(self.root, tearoff=0)
        self._context_menu.add_command(
            label="テキストをコピー", command=self._copy_selected_text
        )
        self._context_menu.add_command(
            label="説明をコピー", command=self._copy_selected_description
        )
        self._context_menu.add_command(
            label="画像をコピー", command=self._copy_selected_image
        )
        self._context_menu.add_command(
            label="画像を保存...", command=self._save_selected_image
        )
        self._context_menu.add_separator()
        self._move_menu = tk.Menu(self._context_menu, tearoff=0)
        self._context_menu.add_cascade(label="階層へ移動", menu=self._move_menu)

        self._folder_menu = tk.Menu(self.root, tearoff=0)
        self._folder_menu.add_command(label="名前を変更...", command=self.on_rename_folder)
        self._folder_menu.add_command(label="削除...", command=self.on_delete_folder)

        rf = tk.Frame(self._paned)
        self._paned.add(rf, minsize=300)

        tk.Label(rf, text="プレビュー", font=(_FONT, 11, "bold"),
                 anchor="w").pack(fill="x")

        # detail_label → 説明フィールド → preview_label の順で bottom から積む
        self.detail_label = tk.Label(rf, text="", font=(_FONT, 10),
                                      anchor="w", justify="left")
        self.detail_label.pack(side="bottom", fill="x", pady=(4, 0))

        desc_outer = tk.Frame(rf)
        desc_outer.pack(side="bottom", fill="x", pady=(2, 0))
        desc_row = tk.Frame(desc_outer)
        desc_row.pack(fill="x")
        _desc_lbl = tk.Label(desc_row, text="説明:", font=(_FONT, 10), cursor="question_arrow")
        _desc_lbl.pack(side="left")
        _desc_lbl.bind("<Enter>", self._on_desc_label_enter)
        _desc_lbl.bind("<Leave>", lambda e: self._hide_tooltip())
        self._desc_var = tk.StringVar()
        self._desc_entry = tk.Entry(desc_row, textvariable=self._desc_var, font=(_FONT, 10))
        self._desc_entry.pack(side="left", fill="x", expand=True, padx=(4, 4))
        self._desc_entry.bind("<FocusOut>", lambda e: self._save_description())
        self._desc_entry.bind("<Return>",   lambda e: self._on_desc_return())
        tk.Button(desc_row, text="↩", font=(_FONT, 9), width=3,
                  command=self._reset_description).pack(side="left")

        self.preview_label = tk.Label(rf, bg="white", anchor="center")
        self.preview_label.pack(expand=True, fill="both")
        self.preview_label.bind("<Configure>", self._on_preview_resize)

    # ── 内部ヘルパー ───────────────────────────────────────────────────────

    def _filter_records(self) -> None:
        query = self._search_var.get().strip().lower()
        if query:
            indices = [
                i for i, r in enumerate(self.records)
                if query in r["text"].lower() or query in r.get("description", "").lower()
            ]
        else:
            indices = list(range(len(self.records)))
        key = _SORT_LABEL_TO_KEY.get(self._sort_var.get(), "date_new")
        self._folders = list_folders(SAVE_DIR, self.records)
        groups = group_by_folder(self.records, indices, self._folders)
        self._groups = {f: sort_records(self.records, idxs, key) for f, idxs in groups.items()}
        self._populate_list()

    def _on_sort_change(self) -> None:
        key = _SORT_LABEL_TO_KEY.get(self._sort_var.get(), "date_new")
        self.settings["sort_order"] = key
        save_settings(self.settings, SETTINGS_FILE)
        self._filter_records()

    def _is_searching(self) -> bool:
        return bool(self._search_var.get().strip())

    def _rec_idx_of(self, iid: str) -> int | None:
        """レコード行の iid からレコード位置を返す。階層行・空文字は None。"""
        if iid and iid not in self._iid_folder and iid.startswith("r"):
            return int(iid[1:])
        return None

    def _is_open(self, iid: str) -> bool:
        return str(self.tree.item(iid, "open")).lower() in ("1", "true")

    def _walk_viewable(self):
        """画面に見えている行（親の階層が展開済みの行）を上から順に返す。

        折りたたんだ階層の中の行が選択されたまま残っても、削除などの対象にしないために使う。
        """
        for top in self.tree.get_children(""):
            yield top
            if top in self._iid_folder and self._is_open(top):
                yield from self.tree.get_children(top)

    def _selected_record_indices(self, expand_folders: bool = False) -> list[int]:
        """選択中のレコード位置を表示順で返す。expand_folders=True なら階層行は配下の全レコードを含める。"""
        selected = set(self.tree.selection())
        result: list[int] = []
        seen: set[int] = set()

        def add(idx: int | None) -> None:
            if idx is not None and idx not in seen:
                seen.add(idx)
                result.append(idx)

        for iid in self._walk_viewable():
            if iid not in selected:
                continue
            if iid in self._iid_folder:
                if expand_folders:
                    for child in self.tree.get_children(iid):
                        add(self._rec_idx_of(child))
            else:
                add(self._rec_idx_of(iid))
        return result

    def _selected_folder_name(self) -> str | None:
        """選択中の階層行のうち表示順で先頭の階層名。なければ None。"""
        selected = set(self.tree.selection())
        for iid in self.tree.get_children(""):
            if iid in selected and iid in self._iid_folder:
                return self._iid_folder[iid]
        return None

    def _current_dest_folder(self) -> str:
        """新規生成の保存先階層。選択中の階層行、または選択中のコードが属する階層。未選択ならルート。"""
        selected = set(self.tree.selection())
        for iid in self._walk_viewable():
            if iid not in selected:
                continue
            if iid in self._iid_folder:
                return self._iid_folder[iid]
            idx = self._rec_idx_of(iid)
            if idx is not None:
                return record_folder(self.records[idx])
        return ""

    def _select_folder(self, name: str) -> None:
        for iid, folder in self._iid_folder.items():
            if same_folder_name(folder, name):
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.tree.see(iid)
                return

    def _remember_open(self, folder: str, opened: bool) -> None:
        """階層の展開状態を記憶し settings.json に保存する。"""
        key = folder.lower()
        if (key in self._open_names) == opened:
            return
        if opened:
            self._open_names[key] = folder
        else:
            del self._open_names[key]
        # 作成直後の階層は self._folders に未反映のため key を加え、削除済みの階層は保存対象から外す
        existing = {f.lower() for f in self._folders} | {key}
        self.settings["open_folders"] = [n for k, n in self._open_names.items() if k in existing]
        save_settings(self.settings, SETTINGS_FILE)

    def _persist_open_folders(self) -> None:
        """展開状態を、実在する階層に絞って settings.json に保存する。"""
        existing = {f.lower() for f in self._folders}
        self.settings["open_folders"] = [n for k, n in self._open_names.items() if k in existing]
        save_settings(self.settings, SETTINGS_FILE)

    def _set_folder_icon(self, iid: str, opened: bool | None = None) -> None:
        """階層行の開閉マークを状態に合わせて設定する。中身がない階層は点を表示する。"""
        if opened is None:
            opened = self._is_open(iid)
        if not self.tree.get_children(iid):
            kind = "empty"
        else:
            kind = "open" if opened else "closed"
        self.tree.item(iid, image=self._folder_icons[kind])

    def _toggle_folder(self, iid: str) -> None:
        opened = not self._is_open(iid)
        self.tree.item(iid, open=opened)
        self._set_folder_icon(iid, opened)
        if not self._is_searching():
            self._remember_open(self._iid_folder[iid], opened)

    def _click_folder(self, iid: str) -> str:
        """階層行のクリック: その行を選択し、開閉を切り替える。

        標準の処理（Press / DoubleClick）には渡さない。連続クリックがダブルクリックとして無視されて
        開閉できなくなるのを防ぎ、クリック位置にかかわらず 1 回ごとに必ず切り替えるため。
        """
        self.tree.focus_set()
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self._toggle_folder(iid)
        return "break"

    def _populate_list(self) -> None:
        """self._folders / self._groups の内容でツリーを作り直す。選択とスクロール位置は維持する。"""
        tree = self.tree
        if self._current_rec is not None:
            self._current_rec_idx = next(
                (i for i, r in enumerate(self.records) if r is self._current_rec), None
            )
        selected = tree.selection()
        # 削除などでレコードの位置がずれても別のコードを選択し直さないよう、位置ではなくレコード自体で復元する
        selected_recs = [
            self._tree_recs[i] for i in map(self._rec_idx_of, selected)
            if i is not None and i < len(self._tree_recs)
        ]
        selected_folders = {self._iid_folder[i] for i in selected if i in self._iid_folder}
        view = tree.yview()

        tree.delete(*tree.get_children(""))
        self._iid_folder = {}
        labels = list_labels_with_status(self.records, SAVE_DIR)
        searching = self._is_searching()
        for n, folder in enumerate(self._folders):
            idxs = self._groups.get(folder, [])
            if searching and not idxs:
                continue
            fiid = f"d{n}"
            self._iid_folder[fiid] = folder
            # 検索中は該当コードを隠さないよう、保存済みの展開状態にかかわらず開く
            tree.insert("", "end", iid=fiid, text=f"{folder}  ({len(idxs)})", tags=("folder",),
                        open=searching or folder.lower() in self._open_names)
            for i in idxs:
                tree.insert(fiid, "end", iid=f"r{i}", text=labels[i])
            self._set_folder_icon(fiid, searching or folder.lower() in self._open_names)
        for i in self._groups.get("", []):
            tree.insert("", "end", iid=f"r{i}", text=labels[i])

        self._tree_recs = list(self.records)
        position = {id(r): i for i, r in enumerate(self._tree_recs)}
        restore = [f"r{position[id(r)]}" for r in selected_recs if id(r) in position]
        restore = [iid for iid in restore if tree.exists(iid)]
        restore += [iid for iid, f in self._iid_folder.items() if f in selected_folders]
        if restore:
            tree.selection_set(restore)
        tree.yview_moveto(view[0])

    def _refresh_detail_label(self, rec: dict) -> None:
        """detail_label をレコード内容で更新する（説明欄フォーカス復帰時にも使用）。"""
        lines = rec["text"].split("\n")
        _MAX_PREVIEW_LINES = 3
        if len(lines) > _MAX_PREVIEW_LINES:
            text_display = "\n".join(lines[:_MAX_PREVIEW_LINES]) + f"\n … (+{len(lines) - _MAX_PREVIEW_LINES}行)"
        else:
            text_display = rec["text"]
        tag = f"[{type_label(rec)}]"
        desc = rec.get("description", "")
        if desc:
            label_text = f"{tag}  {desc}\n{text_display}\n{rec['path']}"
        else:
            label_text = f"{tag}  {text_display}\n{rec['path']}"
        self.detail_label.config(text=label_text)

    def _show_record(self, rec: dict) -> None:
        path = record_file_path(SAVE_DIR, rec)
        self.current_path = str(path) if path is not None else None
        self._current_rec = rec
        self._current_rec_idx = next(
            (i for i, r in enumerate(self.records) if r is rec), None
        )
        self._refresh_detail_label(rec)
        self._desc_var.set(rec.get("description", ""))
        if path is None:
            self.preview_label.config(image="", text="ファイルが見つかりません",
                                       font=(_FONT, 11), fg="gray")
            self._photo = None
            return
        self._redraw_preview()

    def _redraw_preview(self) -> None:
        if not self.current_path:
            return
        if not Path(self.current_path).exists():
            self.preview_label.config(image="", text="ファイルが見つかりません",
                                       font=(_FONT, 11), fg="gray")
            self._photo = None
            return
        w = self.preview_label.winfo_width()
        h = self.preview_label.winfo_height()
        if w <= 1 or h <= 1:
            return
        try:
            img = Image.open(self.current_path).convert("RGB")
            img.thumbnail((w, h), Image.LANCZOS)
            self._photo = ImageTk.PhotoImage(img)
            self.preview_label.config(image=self._photo, text="", fg="black")
        except Exception:
            pass

    def _on_preview_resize(self, event: tk.Event) -> None:
        if event.widget is self.preview_label:
            self._redraw_preview()

    # ── イベントハンドラ ──────────────────────────────────────────────────

    def _on_tree_press(self, event: tk.Event) -> str | None:
        self._drag_iid = None
        self._drag_moved = False
        self._dragging = False
        iid = self.tree.identify_row(event.y)
        if not iid:
            return None
        modifiers = event.state & 0x0005  # Shift | Control
        if iid in self._iid_folder and not modifiers:
            return self._click_folder(iid)
        if self._rec_idx_of(iid) is not None and not modifiers and not self._is_searching():
            self._drag_iid = iid  # 検索中・修飾キー併用時はドラッグ並び替えを無効化
        return None

    def _on_tree_drag_motion(self, event: tk.Event) -> None:
        if self._drag_iid is None:
            return
        self._dragging = True
        self._hide_tooltip()
        self._drag_last_y = event.y
        self._update_drag_autoscroll(event.y)
        self._apply_drag_target(event.y)

    def _apply_drag_target(self, y: int) -> None:
        """ドラッグ中の行を、同じ階層内（ルートならルート直下のコード同士）でカーソル位置へ移動する。"""
        drag = self._drag_iid
        target = self.tree.identify_row(y)
        if drag is None or not target or target == drag or target in self._iid_folder:
            return
        parent = self.tree.parent(drag)
        if self.tree.parent(target) != parent:
            return
        self.tree.move(drag, parent, self.tree.index(target))
        self._drag_moved = True

    def _update_drag_autoscroll(self, y: int) -> None:
        """カーソルが一覧の上端・下端付近にあれば自動スクロールを開始し、離れたら止める。"""
        height = self.tree.winfo_height()
        if y < _DRAG_SCROLL_MARGIN:
            direction = -1
        elif y > height - _DRAG_SCROLL_MARGIN:
            direction = 1
        else:
            direction = 0

        if direction == 0:
            self._cancel_drag_autoscroll()
        elif self._drag_scroll_after_id is None:
            self._run_drag_autoscroll(direction)

    def _run_drag_autoscroll(self, direction: int) -> None:
        self.tree.yview_scroll(direction, "units")
        self._apply_drag_target(self._drag_last_y)
        self._drag_scroll_after_id = self.root.after(
            _DRAG_SCROLL_INTERVAL_MS, lambda: self._run_drag_autoscroll(direction)
        )

    def _cancel_drag_autoscroll(self) -> None:
        if self._drag_scroll_after_id is not None:
            self.root.after_cancel(self._drag_scroll_after_id)
            self._drag_scroll_after_id = None

    def _on_tree_release(self, _event: tk.Event) -> None:
        self._cancel_drag_autoscroll()
        drag_iid, moved = self._drag_iid, self._drag_moved
        self._drag_iid = None
        self._drag_moved = False
        self._dragging = False
        if moved and drag_iid is not None:
            self._finish_drag_reorder(drag_iid)

    def _finish_drag_reorder(self, drag_iid: str) -> None:
        """ドラッグで並べ替えた階層内の順序をカスタム順として保存する。"""
        parent = self.tree.parent(drag_iid)
        ordered = [
            idx for idx in map(self._rec_idx_of, self.tree.get_children(parent))
            if idx is not None
        ]
        apply_custom_order(self.records, ordered)
        save_metadata(self.records, METADATA_FILE)

        custom_label = SORT_OPTION_LABELS["custom"]
        if self._sort_var.get() != custom_label:
            self._sort_var.set(custom_label)  # trace_add 経由で _on_sort_change → _filter_records
        else:
            self._filter_records()  # 既にカスタム順選択中は trace が発火しないため手動で再描画

    def _on_tree_open_close(self, opened: bool) -> None:
        """キー操作による展開/折りたたみを、開閉マークと展開状態に反映する。"""
        iid = self.tree.focus()
        if iid not in self._iid_folder:
            return
        self._set_folder_icon(iid, opened)
        if not self._is_searching():
            self._remember_open(self._iid_folder[iid], opened)

    def _on_tree_hover(self, event: tk.Event) -> None:
        if self._dragging:
            return
        rec_idx = self._rec_idx_of(self.tree.identify_row(event.y))
        if rec_idx is None:
            self._hide_tooltip()
            return
        if rec_idx == self._tooltip_rec_idx:
            return  # 同じ行のまま移動 → 再スケジュール不要
        self._hide_tooltip()
        self._tooltip_rec_idx = rec_idx
        x, y = event.x_root + 14, event.y_root + 14
        self._tooltip_after = self.root.after(
            400, lambda: self._show_list_tooltip(self.records[rec_idx], x, y)
        )

    def _show_list_tooltip(self, rec: dict, x: int, y: int) -> None:
        """生成済一覧ホバー時のツールチップ。説明があれば説明＋元テキストを、なければ元テキストを表示。
        SJIS エンコードの場合はその旨を先頭に表示する。
        """
        lines = []
        if rec.get("encoding") == "SJIS":
            lines.append("エンコード: Shift-JIS")
        desc = rec.get("description", "")
        if desc:
            lines.extend([desc, "─" * 28, rec["text"]])
        else:
            lines.append(rec["text"])
        self._show_tooltip("\n".join(lines), x, y)

    def _show_tooltip(self, text: str, x: int, y: int) -> None:
        lines = text.split("\n")
        _MAX_TT_LINES = 20
        if len(lines) > _MAX_TT_LINES:
            display = "\n".join(lines[:_MAX_TT_LINES]) + f"\n… (+{len(lines) - _MAX_TT_LINES}行)"
        else:
            display = text
        self._tooltip_win = tk.Toplevel(self.root)
        self._tooltip_win.wm_overrideredirect(True)
        self._tooltip_win.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self._tooltip_win,
            text=display,
            font=(_FONT, 9),
            bg="#ffffcc",
            relief="solid",
            bd=1,
            wraplength=400,
            justify="left",
            padx=4,
            pady=3,
        ).pack()

    def _hide_tooltip(self) -> None:
        if self._tooltip_after is not None:
            self.root.after_cancel(self._tooltip_after)
            self._tooltip_after = None
        if self._tooltip_win is not None:
            self._tooltip_win.destroy()
            self._tooltip_win = None
        self._tooltip_rec_idx = -1

    def _on_tree_select(self, _: tk.Event) -> None:
        indices = self._selected_record_indices()
        if not indices:
            return
        self._save_description()
        self._show_record(self.records[indices[-1]])

    def _on_tree_double(self, event: tk.Event) -> str | None:
        iid = self.tree.identify_row(event.y)
        if iid in self._iid_folder:
            if event.state & 0x5:  # Shift / Ctrl 併用は標準の選択操作に任せる
                return None
            # 連続クリックの 2 回目以降も、1 回目と同じく開閉を切り替える
            return self._click_folder(iid)
        idx = self._rec_idx_of(iid)
        if idx is None:
            return None
        show_enlarged(self.records[idx], self.root, SAVE_DIR)
        return None

    def _on_tree_right_click(self, event: tk.Event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid in self._iid_folder:
            if iid not in self.tree.selection():
                self.tree.selection_set(iid)
            self._context_folder = self._iid_folder[iid]
            self._folder_menu.tk_popup(event.x_root, event.y_root)
            return
        idx = self._rec_idx_of(iid)
        if idx is None:
            return
        if iid not in self.tree.selection():
            self.tree.selection_set(iid)
        self._context_rec_idx = idx
        rec = self.records[idx]
        state = tk.NORMAL if _description_for_copy(rec) else tk.DISABLED
        self._context_menu.entryconfig("説明をコピー", state=state)
        path = record_file_path(SAVE_DIR, rec)
        img_state = tk.NORMAL if path is not None and path.exists() else tk.DISABLED
        self._context_menu.entryconfig("画像を保存...", state=img_state)
        self._rebuild_move_menu()
        self._context_menu.tk_popup(event.x_root, event.y_root)

    def _rebuild_move_menu(self) -> None:
        """右クリックメニューの「階層へ移動」を、現在の階層一覧と選択内容で作り直す。"""
        self._move_menu.delete(0, "end")
        indices = self._selected_record_indices()
        sources = {record_folder(self.records[i]).lower() for i in indices}

        def add(label: str, dest: str) -> None:
            same = sources == {dest.lower()}
            self._move_menu.add_command(
                label=label, state=tk.DISABLED if same or not indices else tk.NORMAL,
                command=lambda: self._move_selected_to(dest),
            )

        add("（ルート）", "")
        for folder in self._folders:
            add(folder, folder)

    def _on_desc_label_enter(self, event: tk.Event) -> None:
        """「説明:」ラベルホバー時: 生成済一覧と同じポップアップで説明を表示する。"""
        x, y = event.x_root + 14, event.y_root + 14
        self._hide_tooltip()
        self._tooltip_after = self.root.after(
            300,
            lambda: self._show_tooltip(
                "一覧・PDF に表示される独自の説明文を入力できます\n"
                "Enter で確定  ／  ↩ ボタンで削除\n"
                "（PDF では先頭25文字まで表示）",
                x, y,
            ),
        )

    def _on_desc_return(self) -> None:
        """Enter キー確定: 保存してフォーカスを外す。"""
        self._save_description()
        self.root.focus_set()

    def _save_description(self) -> None:
        if self._current_rec_idx is None:
            return
        rec = self.records[self._current_rec_idx]
        new_desc = self._desc_var.get().strip()
        if rec.get("description", "") != new_desc:
            rec["description"] = new_desc
            save_metadata(self.records, METADATA_FILE)
            self._populate_list()

    def _reset_description(self) -> None:
        if self._current_rec_idx is None:
            return
        rec = self.records[self._current_rec_idx]
        self._desc_var.set("")
        if rec.get("description", "") != "":
            rec["description"] = ""
            save_metadata(self.records, METADATA_FILE)
            self._populate_list()

    def _context_record(self) -> dict | None:
        """右クリックされたコードのレコード。"""
        idx = self._context_rec_idx
        return self.records[idx] if idx is not None and idx < len(self.records) else None

    def _copy_selected_text(self) -> None:
        rec = self._context_record()
        if rec is None:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(rec["text"])

    def _copy_selected_description(self) -> None:
        rec = self._context_record()
        if rec is None:
            return
        desc = _description_for_copy(rec)
        if desc is None:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(desc)

    def _copy_selected_image(self) -> None:
        # clip.exe はテキスト専用のため画像コピー不可。
        # pywin32 依存を避けるため PowerShell の SetImage を使用。
        # [Windows.Forms.Clipboard]::SetImage() が依存追加なしの最小構成。
        rec = self._context_record()
        if rec is None:
            return
        path = record_file_path(SAVE_DIR, rec)
        if path is None or not path.exists():
            messagebox.showerror("エラー", "画像ファイルが見つかりません。", parent=self.root)
            return
        # 階層名・フォルダ名に ' や ; が含まれてもコマンドとして解釈されないよう、
        # パスはコマンド文字列に埋め込まず環境変数で渡す
        env = {**os.environ, "QR_COPY_IMAGE_PATH": str(path)}
        try:
            subprocess.run(
                ["powershell", "-command",
                 "Add-Type -A System.Windows.Forms,System.Drawing;"
                 "[Windows.Forms.Clipboard]::SetImage([Drawing.Image]::FromFile($env:QR_COPY_IMAGE_PATH))"],
                check=True, env=env,
            )
        except Exception as e:
            messagebox.showerror("エラー", f"画像のコピーに失敗しました:\n{e}", parent=self.root)

    def _save_selected_image(self) -> None:
        rec = self._context_record()
        if rec is None:
            return
        src = record_file_path(SAVE_DIR, rec)
        if src is None or not src.exists():
            messagebox.showerror("エラー", "画像ファイルが見つかりません。", parent=self.root)
            return
        dest = filedialog.asksaveasfilename(
            parent=self.root,
            title="画像を保存",
            initialfile=suggested_filename(rec),
            defaultextension=src.suffix,
            filetypes=[("PNG画像", "*.png"), ("すべてのファイル", "*.*")],
        )
        if not dest:
            return
        try:
            shutil.copy2(src, dest)
        except Exception as e:
            messagebox.showerror("エラー", f"画像の保存に失敗しました:\n{e}", parent=self.root)

    def _ask_duplicate(self, text: str, code_type: str, error_correction: str | None = None,
                       folder: str = "") -> bool:
        """重複確認ダイアログを表示し、生成を続けるか返す。「これ以降は表示しない」で警告を無効化できる。"""
        top = tk.Toplevel(self.root)
        top.title("重複確認")
        top.resizable(False, False)
        top.grab_set()

        disp_type = _TYPE_DISP.get(code_type, code_type)
        type_label = f"{disp_type}:{error_correction}" if code_type == "Q" and error_correction else disp_type
        where = f"階層「{folder}」に " if folder else ""
        tk.Label(top, text=f"{where}[{type_label}]  {text}\nはすでに存在します。追加しますか？",
                 font=(_FONT, 10), padx=16, pady=12, justify="left").pack()

        no_warn_var = tk.BooleanVar(value=False)
        tk.Checkbutton(top, text="これ以降は表示しない", variable=no_warn_var,
                       font=(_FONT, 9)).pack(pady=(0, 8))

        result = [False]

        def on_ok() -> None:
            result[0] = True
            top.destroy()

        def on_cancel() -> None:
            top.destroy()

        btn_f = tk.Frame(top)
        btn_f.pack(pady=(0, 12))
        tk.Button(btn_f, text="追加する", font=(_FONT, 10), width=10,
                  command=on_ok).pack(side="left", padx=6)
        tk.Button(btn_f, text="キャンセル", font=(_FONT, 10), width=10,
                  command=on_cancel).pack(side="left", padx=6)

        self.root.wait_window(top)

        if no_warn_var.get():
            self.settings["warn_on_duplicate"] = False
            save_settings(self.settings, SETTINGS_FILE)
            self._warn_var.set(False)

        return result[0]

    def on_generate(self) -> None:
        self._save_description()
        code_type = self.type_var.get()
        if code_type == "Q":
            text = self.qr_text.get("1.0", "end-1c").strip()
        else:
            text = self.entry_var.get().strip()
        if not text:
            messagebox.showwarning("入力エラー", "テキストを入力してください。",
                                   parent=self.root)
            return

        dest = self._current_dest_folder()
        ec = self._ec_var.get() if code_type == "Q" else None
        enc = self._enc_var.get() if code_type == "Q" else None
        in_dest = [r for r in self.records if same_folder_name(record_folder(r), dest)]
        if self.settings.get("warn_on_duplicate", True) and has_duplicate(
            text, code_type, in_dest, error_correction=ec, encoding=enc
        ):
            if not self._ask_duplicate(text, code_type, error_correction=ec, folder=dest):
                return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        try:
            dest_dir = resolve_folder_dir(SAVE_DIR, dest)
            if dest:
                dest_dir.mkdir(exist_ok=True)
            if code_type == "Q":
                fp = dest_dir / f"qr_{ts}.png"
                generate_qr(text, fp, error_correction=self._ec_var.get(),
                            encoding=self._enc_var.get())
                rec = {"text": text, "type": code_type,
                       "path": build_record_path(dest, fp.name),
                       "error_correction": self._ec_var.get(),
                       "encoding": self._enc_var.get()}
            else:
                fp = generate_barcode_file(text, dest_dir / f"bar_{ts}")
                rec = {"text": text, "type": code_type,
                       "path": build_record_path(dest, fp.name)}
            self.records.append(rec)
            save_metadata(self.records, METADATA_FILE)

            if dest:
                self._remember_open(dest, True)  # 生成したコードが折りたたまれた階層に隠れないようにする
            self._search_var.set("")  # 検索をクリアして新規アイテムを確実に表示
            self._filter_records()
            iid = f"r{len(self.records) - 1}"
            if self.tree.exists(iid):
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.tree.see(iid)
            self._show_record(rec)
            if code_type == "Q":
                self.qr_text.delete("1.0", "end")
            else:
                self.entry_var.set("")

        except Exception as e:
            messagebox.showerror("エラー", f"生成に失敗しました:\n{e}",
                                 parent=self.root)

    def on_delete(self) -> None:
        rec_indices = self._selected_record_indices()
        if not rec_indices:
            messagebox.showinfo("削除", "削除するアイテムを選択してください。",
                                parent=self.root)
            return
        count = len(rec_indices)
        msg = f"{count} 件削除しますか？" if count > 1 else f"削除しますか？\n{list_labels(self.records)[rec_indices[0]]}"
        if messagebox.askyesno("確認", msg, parent=self.root):
            for i in sorted(rec_indices, reverse=True):
                path = record_file_path(SAVE_DIR, self.records[i])
                if path is not None:
                    try:
                        path.unlink(missing_ok=True)
                    except Exception:
                        pass
                self.records.pop(i)
            save_metadata(self.records, METADATA_FILE)
            self._filter_records()
            self._clear_preview()

    def on_export_pdf(self) -> None:
        # 階層行を選択した場合は、その階層のコードをすべて出力する
        selected = [self.records[i] for i in self._selected_record_indices(expand_folders=True)]
        if not selected:
            messagebox.showinfo("PDF出力", "出力するアイテムを選択してください。\n(Ctrl+クリックで複数選択)",
                                parent=self.root)
            return
        default_name = f"qr_barcode_{datetime.now().strftime('%Y%m%d')}.pdf"
        path = filedialog.asksaveasfilename(
            parent=self.root,
            defaultextension=".pdf",
            filetypes=[("PDF ファイル", "*.pdf")],
            title="PDFを保存",
            initialfile=default_name,
        )
        if not path:
            return
        try:
            generate_pdf_grid(selected, Path(path), SAVE_DIR, cols=self._pdf_cols_var.get())
            if self.settings.get("auto_open_pdf"):
                if sys.platform == "win32":
                    os.startfile(path)
                elif sys.platform == "darwin":
                    subprocess.run(["open", path])
                else:
                    subprocess.run(["xdg-open", path])
            messagebox.showinfo("PDF出力", f"{len(selected)} 件を保存しました。\n{path}",
                                parent=self.root)
        except Exception as e:
            messagebox.showerror("エラー", f"PDF出力に失敗しました:\n{e}", parent=self.root)

    def on_open_folder(self) -> None:
        target = SAVE_DIR
        folder = self._selected_folder_name()
        if folder and (SAVE_DIR / folder).is_dir():
            target = SAVE_DIR / folder  # 階層行を選択中はその階層を開く
        resolved = str(target.resolve())
        if sys.platform == "win32":
            os.startfile(resolved)
        elif sys.platform == "darwin":
            subprocess.run(["open", resolved])
        else:
            subprocess.run(["xdg-open", resolved])

    # ── 階層の作成・移動 ──────────────────────────────────────────────────────

    def on_create_folder(self) -> None:
        initial = ""
        while True:
            name = simpledialog.askstring(
                "階層を作成", "階層名を入力してください:", initialvalue=initial, parent=self.root,
            )
            if name is None:
                return
            try:
                created = create_folder(SAVE_DIR, name, self.records)
            except FolderExistsError:
                messagebox.showinfo(
                    "階層を作成", f"「{name.strip()}」は既に作成されています。", parent=self.root,
                )
                self._search_var.set("")
                self._filter_records()
                self._select_folder(name.strip())
                return
            except ValueError as e:
                messagebox.showerror("階層を作成", str(e), parent=self.root)
                initial = name
                continue
            except OSError as e:
                messagebox.showerror("階層を作成", f"作成に失敗しました:\n{e}", parent=self.root)
                return
            self._remember_open(created, True)
            self._search_var.set("")  # 空の階層は検索中だと表示されないため、検索をクリアして表示する
            self._filter_records()
            self._select_folder(created)
            return

    def _rename_open_state(self, old: str, new: str) -> None:
        """階層の名前変更に合わせて、展開状態の記憶を引き継ぐ。"""
        if old.lower() in self._open_names:
            del self._open_names[old.lower()]
            self._open_names[new.lower()] = new

    def _refresh_after_folder_change(self) -> None:
        """階層の名前変更・削除のあと、一覧と現在表示中のコードを更新する。"""
        self._filter_records()
        self._persist_open_folders()
        current = self._current_rec
        if current is not None and any(r is current for r in self.records):
            self._show_record(current)  # パスが変わったため詳細表示を更新する
        elif current is not None:
            self._clear_preview()

    def on_rename_folder(self) -> None:
        old = self._context_folder
        if not old:
            return
        initial = old
        while True:
            name = simpledialog.askstring(
                "階層の名前を変更", f"「{old}」の新しい名前を入力してください:",
                initialvalue=initial, parent=self.root,
            )
            if name is None:
                return
            try:
                new = validate_folder_name(name)
            except ValueError as e:
                messagebox.showerror("階層の名前を変更", str(e), parent=self.root)
                initial = name
                continue
            if new == old:
                return
            if not same_folder_name(new, old):
                existing = next((f for f in self._folders if same_folder_name(new, f)), None)
                if existing is not None:
                    self._merge_folder_into(old, existing)
                    return
            break

        count = sum(1 for r in self.records if same_folder_name(record_folder(r), old))
        if not messagebox.askyesno(
            "階層の名前を変更",
            f"階層「{old}」の名前を「{new}」に変更しますか？\n\n"
            f"この階層のコード {count} 件の保存場所（パス）も更新されます。",
            default=messagebox.NO, parent=self.root,
        ):
            return
        self._save_description()
        try:
            rename_folder(SAVE_DIR, old, new, self.records, METADATA_FILE)
        except FolderExistsError:
            messagebox.showinfo("階層の名前を変更", f"「{new}」は既に作成されています。", parent=self.root)
            return
        except (ValueError, OSError) as e:
            messagebox.showerror("エラー", f"名前の変更に失敗しました:\n{e}", parent=self.root)
            self._refresh_after_folder_change()
            return
        self._rename_open_state(old, new)
        self._refresh_after_folder_change()
        self._select_folder(new)

    def _merge_folder_into(self, old: str, dest: str) -> None:
        """名前の変更先が既存の階層だったとき、old の中身を dest へ統合する（dest の既存のコードは残す）。"""
        if not messagebox.askyesno(
            "階層の統合",
            f"階層「{dest}」は既にあります。\n\n「{old}」の中身を「{dest}」へ統合しますか？\n"
            f"「{dest}」にある既存のコードはそのまま残ります。\n"
            "同じコードがある場合は、続けて 1 件ずつ扱いを選びます。",
            default=messagebox.NO, parent=self.root,
        ):
            return
        self._save_description()
        overwrite: set[int] = set()
        try:
            collisions = merge_folder_collisions(SAVE_DIR, old, dest, self.records)
            if collisions:
                chosen = self._ask_merge_choices(collisions, old, dest)
                if chosen is None:
                    return
                overwrite = chosen
            result = merge_folder(SAVE_DIR, old, dest, self.records, METADATA_FILE, overwrite=overwrite)
        except UnmanagedFilesError as e:
            shown = "\n".join(e.names[:8])
            messagebox.showerror(
                "階層を統合できません",
                f"階層「{old}」には、一覧に表示されないファイルまたはフォルダがあります。\n"
                f"Explorer などで整理してから統合してください。\n\n{shown}",
                parent=self.root,
            )
            return
        except (ValueError, OSError) as e:
            messagebox.showerror("エラー", f"階層の統合に失敗しました:\n{e}", parent=self.root)
            self._refresh_after_folder_change()
            return
        if result.removed_source:
            self._open_names.pop(old.lower(), None)
        self._refresh_after_folder_change()
        self._select_folder(dest)
        if result.left:
            messagebox.showinfo(
                "階層の統合",
                f"同じコードがあった {result.left} 件は、階層「{old}」に残しています。",
                parent=self.root,
            )

    def _ask_merge_choices(
        self, collisions: list[tuple[int, int]], src: str, dest: str
    ) -> set[int] | None:
        """統合先に同じコードがある組ごとに「上書き」か「残す」を選ぶ。上書きするコード側の位置の集合を返す（キャンセルは None）。"""
        labels = list_labels(self.records)
        chosen: set[int] = set()
        bulk: str | None = None
        for n, (s_idx, _d_idx) in enumerate(collisions, 1):
            if bulk is None:
                picked = self._ask_merge_one(labels[s_idx], src, dest, n, len(collisions))
                if picked is None:
                    return None
                answer, apply_all = picked
                if apply_all:
                    bulk = answer
            else:
                answer = bulk
            if answer == "overwrite":
                chosen.add(s_idx)
        return chosen

    def _ask_merge_one(
        self, label: str, src: str, dest: str, n: int, total: int
    ) -> tuple[str, bool] | None:
        """1 件分の選択。("overwrite" か "keep", 以降すべてに適用するか) を返す。キャンセルは None。"""
        top = tk.Toplevel(self.root)
        top.title(f"同じコードがあります ({n}/{total})")
        top.resizable(False, False)
        top.transient(self.root)
        tk.Label(
            top,
            text=f"「{label}」は、階層「{dest}」にも同じ内容のコードがあります。",
            font=(_FONT, 10), padx=16, pady=12, justify="left", wraplength=480,
        ).pack()
        tk.Label(
            top,
            text=f"「上書き」は「{dest}」の既存のコード（画像ファイルを含む）を削除して置き換え、元に戻せません。\n"
                 f"「残す」は移動せず、階層「{src}」に残します。",
            font=(_FONT, 9), fg="#a00000", padx=16, justify="left", wraplength=480,
        ).pack()
        result: list[tuple[str, bool] | None] = [None]
        apply_all = tk.BooleanVar(value=False)

        def choose(value: str | None) -> None:
            result[0] = None if value is None else (value, apply_all.get())
            top.destroy()

        row1 = tk.Frame(top)
        row1.pack(pady=(12, 4))
        tk.Button(row1, text="上書き", font=(_FONT, 10), width=12,
                  command=lambda: choose("overwrite")).pack(side="left", padx=6)
        tk.Button(row1, text="残す", font=(_FONT, 10), width=12,
                  command=lambda: choose("keep")).pack(side="left", padx=6)
        if total > 1:
            tk.Checkbutton(top, text="これ以降のすべての重複に適用する", variable=apply_all,
                           font=(_FONT, 10)).pack(pady=4)
        row3 = tk.Frame(top)
        row3.pack(pady=(4, 12))
        cancel_btn = tk.Button(row3, text="キャンセル（統合を中止）", font=(_FONT, 10), width=24,
                               default="active", command=lambda: choose(None))
        cancel_btn.pack()

        def on_return(_: tk.Event) -> None:
            focused = top.focus_get()
            (focused if isinstance(focused, tk.Button) else cancel_btn).invoke()

        top.bind("<Return>", on_return)
        top.bind("<Escape>", lambda _: choose(None))
        top.protocol("WM_DELETE_WINDOW", lambda: choose(None))
        top.grab_set()
        cancel_btn.focus_force()
        self.root.wait_window(top)
        return result[0]

    def _ask_delete_folder_contents(self, folder: str, count: int) -> str | None:
        """中身のある階層の削除方法を選ぶ。"move_to_root" / "delete" / None（キャンセル）を返す。"""
        top = tk.Toplevel(self.root)
        top.title("階層を削除")
        top.resizable(False, False)
        top.transient(self.root)
        tk.Label(
            top, text=f"階層「{folder}」には {count} 件のコードがあります。\nどのように削除しますか？",
            font=(_FONT, 10), padx=16, pady=12, justify="left",
        ).pack()
        tk.Label(
            top, text="「コードごと削除」は、画像ファイルも削除され、元に戻せません。",
            font=(_FONT, 9), fg="#a00000", padx=16, justify="left",
        ).pack()

        result: list[str | None] = [None]

        def choose(value: str | None) -> None:
            result[0] = value
            top.destroy()

        btn_f = tk.Frame(top)
        btn_f.pack(pady=12)
        tk.Button(btn_f, text="ルートへ戻して階層を削除", font=(_FONT, 10),
                  command=lambda: choose("move_to_root")).pack(side="left", padx=6)
        tk.Button(btn_f, text="コードごと削除", font=(_FONT, 10),
                  command=lambda: choose("delete")).pack(side="left", padx=6)
        cancel_btn = tk.Button(btn_f, text="キャンセル", font=(_FONT, 10), width=10,
                               default="active", command=lambda: choose(None))
        cancel_btn.pack(side="left", padx=6)

        def on_return(_: tk.Event) -> None:
            focused = top.focus_get()
            (focused if isinstance(focused, tk.Button) else cancel_btn).invoke()

        top.bind("<Return>", on_return)
        top.bind("<Escape>", lambda _: choose(None))
        top.protocol("WM_DELETE_WINDOW", lambda: choose(None))
        top.grab_set()
        cancel_btn.focus_force()  # 初期フォーカスは「キャンセル」
        self.root.wait_window(top)
        return result[0]

    def on_delete_folder(self) -> None:
        folder = self._context_folder
        if not folder:
            return
        self._save_description()
        try:
            unmanaged = folder_unmanaged_entries(SAVE_DIR, folder, self.records)
        except ValueError as e:
            messagebox.showerror("エラー", str(e), parent=self.root)
            return
        if unmanaged:
            shown = "\n".join(unmanaged[:8])
            if len(unmanaged) > 8:
                shown += f"\n… 他 {len(unmanaged) - 8} 件"
            messagebox.showerror(
                "階層を削除できません",
                f"階層「{folder}」には、一覧に表示されないファイルまたはフォルダがあります。\n"
                f"中身を確認し、Explorer などで整理してから削除してください。\n\n{shown}",
                parent=self.root,
            )
            return

        indices = [i for i, r in enumerate(self.records) if same_folder_name(record_folder(r), folder)]
        if not indices:
            if not messagebox.askyesno(
                "階層を削除", f"階層「{folder}」を削除しますか？\n（コードは入っていません）",
                default=messagebox.NO, parent=self.root,
            ):
                return
            contents = "error"
        else:
            contents = self._ask_delete_folder_contents(folder, len(indices))
            if contents is None:
                return
        overwrite = False
        if contents == "move_to_root":
            collisions = find_move_collisions(self.records, indices, "")
            if collisions:
                if not self._ask_move_overwrite(collisions, ""):
                    return
                overwrite = True
        deleted_ok = False
        try:
            delete_folder(SAVE_DIR, folder, self.records, METADATA_FILE,
                          contents=contents, overwrite=overwrite)
            deleted_ok = True
        except MoveCollisionError:
            messagebox.showerror("エラー", "移動先の内容が変更されたため中止しました。", parent=self.root)
        except UnmanagedFilesError:
            messagebox.showerror("エラー", "階層の内容が変更されたため中止しました。", parent=self.root)
        except FolderNotEmptyError:
            messagebox.showerror("エラー", "階層にコードが追加されたため中止しました。", parent=self.root)
        except (ValueError, OSError) as e:
            messagebox.showerror("エラー", f"階層の削除に失敗しました:\n{e}", parent=self.root)
        if deleted_ok:
            self._open_names.pop(folder.lower(), None)
        self._refresh_after_folder_change()

    def _ask_move_overwrite(self, collisions: list[tuple[int, int]], dest: str) -> bool:
        """移動先に同一コードがあるときの上書き確認。初期フォーカスは「キャンセル」。"""
        labels = list_labels(self.records)
        targets = list(dict.fromkeys(j for _, j in collisions))
        shown = [labels[j] for j in targets[:8]]
        if len(targets) > len(shown):
            shown.append(f"… 他 {len(targets) - len(shown)} 件")
        where = f"階層「{dest}」" if dest else "ルート"

        top = tk.Toplevel(self.root)
        top.title("移動先に同じコードがあります")
        top.resizable(False, False)
        top.transient(self.root)

        message = (
            f"移動先の{where}に、同じ内容のコードが {len(targets)} 件あります。\n"
            "上書きすると、移動先の既存のコード（画像ファイルを含む）は削除され、元に戻せません。"
        )
        tk.Label(top, text=message, font=(_FONT, 10), padx=16, pady=12, justify="left").pack()
        tk.Label(top, text="\n".join(shown), font=(_FONT, 9), padx=16, justify="left",
                 anchor="w").pack(fill="x")

        result = [False]

        def on_overwrite() -> None:
            result[0] = True
            top.destroy()

        def on_cancel() -> None:
            top.destroy()

        btn_f = tk.Frame(top)
        btn_f.pack(pady=12)
        tk.Button(btn_f, text="上書き", font=(_FONT, 10), width=10,
                  command=on_overwrite).pack(side="left", padx=6)
        cancel_btn = tk.Button(btn_f, text="キャンセル", font=(_FONT, 10), width=10,
                               default="active", command=on_cancel)
        cancel_btn.pack(side="left", padx=6)

        def on_return(_: tk.Event) -> None:
            focused = top.focus_get()
            (focused if isinstance(focused, tk.Button) else cancel_btn).invoke()

        top.bind("<Return>", on_return)
        top.bind("<Escape>", lambda _: on_cancel())
        top.protocol("WM_DELETE_WINDOW", on_cancel)
        top.grab_set()
        cancel_btn.focus_force()  # ウィンドウマネージャが初期フォーカスを渡さない環境でも「キャンセル」に当てる
        self.root.wait_window(top)
        return result[0]

    def _move_selected_to(self, dest: str) -> None:
        """選択中のコードを階層 dest（空文字ならルート）へ移動する。"""
        self._save_description()
        movable = [
            i for i in self._selected_record_indices()
            if not same_folder_name(record_folder(self.records[i]), dest)
        ]
        if not movable:
            return
        moved_recs = [self.records[i] for i in movable]
        current = self.records[self._current_rec_idx] if self._current_rec_idx is not None else None

        overwrite = False
        collisions = find_move_collisions(self.records, movable, dest)
        if collisions:
            if not self._ask_move_overwrite(collisions, dest):
                return
            overwrite = True
        try:
            move_records(self.records, movable, dest, SAVE_DIR, METADATA_FILE, overwrite=overwrite)
        except MoveCollisionError:
            messagebox.showerror("エラー", "移動先の内容が変更されたため中止しました。",
                                 parent=self.root)
            return
        except (ValueError, OSError) as e:
            messagebox.showerror("エラー", f"移動に失敗しました:\n{e}", parent=self.root)
            self._filter_records()
            return

        if dest:
            self._remember_open(dest, True)
        self._filter_records()
        position = {id(r): i for i, r in enumerate(self.records)}
        iids = [f"r{position[id(r)]}" for r in moved_recs if id(r) in position]
        iids = [iid for iid in iids if self.tree.exists(iid)]
        if iids:
            self.tree.selection_set(iids)
            self.tree.see(iids[0])
        if current is not None and id(current) in position:
            self._show_record(current)  # 移動でファイルの場所が変わったためプレビューと詳細を更新する
        else:
            self._clear_preview()

    def _clear_preview(self) -> None:
        self.preview_label.config(image="")
        self._photo = None
        self.detail_label.config(text="")
        self._desc_var.set("")
        self._current_rec = None
        self._current_rec_idx = None
        self.current_path = None
