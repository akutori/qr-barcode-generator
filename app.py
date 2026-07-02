import json
import os
import shutil
import subprocess
import sys
import tkinter as tk
import tomllib
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from core import (
    SORT_OPTION_LABELS,
    apply_custom_order,
    has_duplicate,
    list_labels,
    list_labels_with_status,
    load_metadata,
    load_settings,
    move_index,
    save_metadata,
    save_settings,
    sort_records,
    type_label,
)
from csv_import import (
    ImportRow,
    ParseError,
    RowStatus,
    format_ec_for_display,
    format_encoding_for_display,
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
LEFT_W = 310        # 左パネル固定幅 (px)
WIN_MIN_W = 560
WIN_MIN_H = 420


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


def _filter_overwrite(
    records: list[dict],
    text: str,
    code_type: str,
    ec: str | None,
    encoding: str | None = None,
) -> list[dict]:
    """上書きモード時に対象レコードを除去した新しいリストを返す。

    QR の場合: ec と encoding が一致するレコード、および ec フィールドなしの旧レコードを除去する。
    encoding=None のとき encoding チェックはスキップ（既存動作と互換）。
    """
    def _matches(r: dict) -> bool:
        if r["text"] != text or r["type"] != code_type:
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

    return [r for r in records if not _matches(r)]


def _description_for_copy(rec: dict) -> str | None:
    """説明をコピーする文字列を返す。説明がない・空の場合は None を返す。"""
    desc = rec.get("description", "")
    return desc if desc else None


# ---------------------------------------------------------------------------
# 拡大表示ウィンドウ
# ---------------------------------------------------------------------------

def show_enlarged(record: dict, root: tk.Tk) -> None:
    try:
        orig_img = Image.open(record["path"]).convert("RGB")
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

    tk.Label(info, text=f"保存先: {record['path']}",
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
        self._filtered_indices: list[int] = []
        self._current_rec_idx: int | None = None
        self._tooltip_win: tk.Toplevel | None = None
        self._tooltip_after: str | None = None
        self._tooltip_rec_idx: int = -1
        self._drag_start_lb_idx: int | None = None
        self._drag_current_lb_idx: int | None = None
        self._dragging: bool = False

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
        dlg.geometry("720x500")
        dlg.minsize(600, 400)
        dlg.resizable(True, True)
        self._import_dlg = dlg

        _rows: list[ImportRow] = []
        _dup_mode = tk.StringVar(value="skip")

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
            validated = validate_all(parsed, self.records)
            _rows.clear()
            _rows.extend(validated)
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

        # ── サマリーラベル ───────────────────────────────────────────────────
        summary_var = tk.StringVar(value="CSVファイルを選択してください。")
        tk.Label(dlg, textvariable=summary_var, font=(_FONT, 9),
                 anchor="w").pack(side="bottom", fill="x", padx=8)

        # ── ヒント行 ─────────────────────────────────────────────────────────
        hint_f = tk.Frame(dlg)
        hint_f.pack(fill="x", padx=8, pady=(0, 2))
        tk.Label(hint_f,
                 text="種別: QR（または Q）/ Barcode（または B）  ｜  誤り訂正: L / M / Q / H（空欄=M）  ｜  エンコード: UTF-8 / SJIS（省略=UTF-8）",
                 font=(_FONT, 8), fg="#666666", anchor="w").pack(fill="x")

        # ── プレビュー（Treeview）────────────────────────────────────────────
        cols = ("status", "type", "text", "description", "ec", "encoding", "error")
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
        tree.heading("text",     text="テキスト")
        tree.heading("description", text="説明")
        tree.heading("ec",       text="誤り訂正")
        tree.heading("encoding", text="エンコード")
        tree.heading("error",    text="エラー詳細")
        tree.column("status",   width=50,  stretch=False, anchor="center")
        tree.column("type",     width=70,  stretch=False, anchor="center")
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
                    text_disp, r.description,
                    format_ec_for_display(r), format_encoding_for_display(r),
                    r.error_msg,
                ))
            total = len(_rows)
            summary_var.set(
                f"全{total}件：✅ {n_ok}件  ⚠ {n_dup}件  ❌ {n_err}件"
                if total else "データがありません。"
            )
            import_btn.config(state="normal" if total > 0 else "disabled")

        def _do_import() -> None:
            mode = _dup_mode.get()
            n_ok = n_dup = n_err = 0
            ts_base = datetime.now().strftime("%Y%m%d_%H%M%S")
            for i, row in enumerate(_rows):
                if row.status == RowStatus.ERROR:
                    n_err += 1
                    continue
                if row.status == RowStatus.DUPLICATE:
                    if mode == "skip":
                        n_dup += 1
                        continue
                    elif mode == "overwrite":
                        # 既存レコードを削除してから追加
                        ec = row.error_correction if row.code_type == "Q" else None
                        enc = row.encoding if row.code_type == "Q" else None
                        self.records = _filter_overwrite(
                            self.records, row.text, row.code_type, ec, encoding=enc
                        )
                    # mode == "add": 何もしない → そのまま追加処理へ
                ts = f"{ts_base}_{i:04d}"
                try:
                    if row.code_type == "Q":
                        fp = SAVE_DIR / f"qr_{ts}.png"
                        generate_qr(row.text, fp, error_correction=row.error_correction,
                                    encoding=row.encoding)
                        rec: dict = {
                            "text": row.text, "type": row.code_type,
                            "path": str(fp), "error_correction": row.error_correction,
                            "encoding": row.encoding,
                        }
                    else:
                        fp = generate_barcode_file(row.text, SAVE_DIR / f"bar_{ts}")
                        rec = {"text": row.text, "type": row.code_type, "path": str(fp)}
                    if row.description:
                        rec["description"] = row.description
                    self.records.append(rec)
                    n_ok += 1
                except Exception:
                    n_err += 1

            save_metadata(self.records, METADATA_FILE)
            self._filter_records()
            dlg.destroy()
            dup_label = {"skip": "重複スキップ", "overwrite": "重複上書き",
                         "add": "重複追加"}[mode]
            messagebox.showinfo(
                "インポート完了",
                f"成功: {n_ok}件\n{dup_label}: {n_dup}件\nエラー: {n_err}件",
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
        lf = tk.Frame(self.root, width=LEFT_W)
        lf.pack(side="left", fill="y", padx=(8, 4), pady=8)
        lf.pack_propagate(False)

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
        tk.Label(lf, text="ダブルクリック: 拡大  Ctrl+クリック: 複数選択",
                 font=(_FONT, 8), fg="gray", anchor="w").pack(fill="x")

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
        tk.Button(btn_f, text="削除", font=(_FONT, 10), width=8,
                  command=self.on_delete).pack(side="left")
        tk.Button(btn_f, text="フォルダを開く", font=(_FONT, 10),
                  command=self.on_open_folder).pack(side="left", padx=(4, 0))

        # ── リストボックス（残り領域を占有）──────────────────────────────────
        lb_f = tk.Frame(lf)
        lb_f.pack(expand=True, fill="both", pady=(2, 4))

        sb = tk.Scrollbar(lb_f)
        sb.pack(side="right", fill="y")
        self.listbox = tk.Listbox(lb_f, font=("Consolas", 10),
                                   yscrollcommand=sb.set, selectmode="extended",
                                   activestyle="dotbox")
        self.listbox.pack(expand=True, fill="both")
        sb.config(command=self.listbox.yview)
        self.listbox.bind("<<ListboxSelect>>", self._on_list_select)
        self.listbox.bind("<Double-Button-1>", self._on_list_double)
        self.listbox.bind("<Button-3>", self._on_list_right_click)
        self.listbox.bind("<Motion>", self._on_list_hover)
        self.listbox.bind("<Leave>", lambda _: self._hide_tooltip())
        self.listbox.bind("<Button-1>", self._on_list_drag_start, add="+")
        self.listbox.bind("<B1-Motion>", self._on_list_drag_motion)
        self.listbox.bind("<ButtonRelease-1>", self._on_list_drag_release)

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

        ttk.Separator(self.root, orient="vertical").pack(side="left", fill="y")

        rf = tk.Frame(self.root)
        rf.pack(side="right", expand=True, fill="both", padx=(4, 8), pady=8)

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
        self._filtered_indices = sort_records(self.records, indices, key)
        self._populate_list()

    def _on_sort_change(self) -> None:
        key = _SORT_LABEL_TO_KEY.get(self._sort_var.get(), "date_new")
        self.settings["sort_order"] = key
        save_settings(self.settings, SETTINGS_FILE)
        self._filter_records()

    def _rec_idx(self, lb_idx: int) -> int:
        return self._filtered_indices[lb_idx]

    def _populate_list(self) -> None:
        self.listbox.delete(0, tk.END)
        labels = list_labels_with_status(self.records)
        for i in self._filtered_indices:
            self.listbox.insert(tk.END, labels[i])

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
        self.current_path = rec["path"]
        self._current_rec_idx = next(
            (i for i, r in enumerate(self.records) if r is rec), None
        )
        self._refresh_detail_label(rec)
        self._desc_var.set(rec.get("description", ""))
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

    def _on_list_drag_start(self, event: tk.Event) -> None:
        idx = self.listbox.nearest(event.y)
        if idx < 0 or idx >= len(self._filtered_indices):
            return
        if self._search_var.get().strip():
            return  # 検索中はドラッグ並び替えを無効化
        self._drag_start_lb_idx = idx
        self._drag_current_lb_idx = idx

    def _on_list_drag_motion(self, event: tk.Event) -> None:
        if self._drag_start_lb_idx is None:
            return
        self._dragging = True
        self._hide_tooltip()
        target = self.listbox.nearest(event.y)
        if target < 0 or target >= len(self._filtered_indices):
            return
        if target == self._drag_current_lb_idx:
            return
        self._filtered_indices = move_index(
            self._filtered_indices, self._drag_current_lb_idx, target
        )
        self._drag_current_lb_idx = target
        self._populate_list()
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(target)

    def _on_list_drag_release(self, _: tk.Event) -> None:
        if self._drag_start_lb_idx is None:
            return
        moved = self._dragging and self._drag_current_lb_idx != self._drag_start_lb_idx
        self._drag_start_lb_idx = None
        self._drag_current_lb_idx = None
        self._dragging = False
        if not moved:
            return

        apply_custom_order(self.records, self._filtered_indices)
        save_metadata(self.records, METADATA_FILE)

        custom_label = SORT_OPTION_LABELS["custom"]
        if self._sort_var.get() != custom_label:
            self._sort_var.set(custom_label)  # trace_add 経由で _on_sort_change → _filter_records
        else:
            self._filter_records()  # 既にカスタム順選択中は trace が発火しないため手動で再描画

    def _on_list_hover(self, event: tk.Event) -> None:
        if self._dragging:
            return
        idx = self.listbox.nearest(event.y)
        if idx < 0 or idx >= len(self._filtered_indices):
            self._hide_tooltip()
            return
        rec_idx = self._rec_idx(idx)
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

    def _on_list_select(self, _: tk.Event) -> None:
        sel = self.listbox.curselection()
        if not sel or sel[-1] >= len(self._filtered_indices):
            return
        self._save_description()
        self._show_record(self.records[self._rec_idx(sel[-1])])

    def _on_list_double(self, _: tk.Event) -> None:
        sel = self.listbox.curselection()
        if not sel or sel[0] >= len(self._filtered_indices):
            return
        show_enlarged(self.records[self._rec_idx(sel[0])], self.root)

    def _on_list_right_click(self, event: tk.Event) -> None:
        idx = self.listbox.nearest(event.y)
        if idx < 0 or idx >= len(self._filtered_indices):
            return
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(idx)
        rec = self.records[self._rec_idx(idx)]
        state = tk.NORMAL if _description_for_copy(rec) else tk.DISABLED
        self._context_menu.entryconfig("説明をコピー", state=state)
        self._context_menu.tk_popup(event.x_root, event.y_root)

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

    def _copy_selected_text(self) -> None:
        sel = self.listbox.curselection()
        if not sel or sel[0] >= len(self._filtered_indices):
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.records[self._rec_idx(sel[0])]["text"])

    def _copy_selected_description(self) -> None:
        sel = self.listbox.curselection()
        if not sel or sel[0] >= len(self._filtered_indices):
            return
        desc = _description_for_copy(self.records[self._rec_idx(sel[0])])
        if desc is None:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(desc)

    def _copy_selected_image(self) -> None:
        # clip.exe はテキスト専用のため画像コピー不可。
        # pywin32 依存を避けるため PowerShell の SetImage を使用。
        # [Windows.Forms.Clipboard]::SetImage() が依存追加なしの最小構成。
        sel = self.listbox.curselection()
        if not sel or sel[0] >= len(self._filtered_indices):
            return
        path = self.records[self._rec_idx(sel[0])]["path"]
        try:
            subprocess.run(
                ["powershell", "-command",
                 f"Add-Type -A System.Windows.Forms,System.Drawing;"
                 f"[Windows.Forms.Clipboard]::SetImage([Drawing.Image]::FromFile('{path}'))"],
                check=True,
            )
        except Exception as e:
            messagebox.showerror("エラー", f"画像のコピーに失敗しました:\n{e}", parent=self.root)

    def _ask_duplicate(self, text: str, code_type: str, error_correction: str | None = None) -> bool:
        """重複確認ダイアログを表示し、生成を続けるか返す。「これ以降は表示しない」で警告を無効化できる。"""
        top = tk.Toplevel(self.root)
        top.title("重複確認")
        top.resizable(False, False)
        top.grab_set()

        disp_type = _TYPE_DISP.get(code_type, code_type)
        type_label = f"{disp_type}:{error_correction}" if code_type == "Q" and error_correction else disp_type
        tk.Label(top, text=f"[{type_label}]  {text}\nはすでに存在します。追加しますか？",
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

        ec = self._ec_var.get() if code_type == "Q" else None
        enc = self._enc_var.get() if code_type == "Q" else None
        if self.settings.get("warn_on_duplicate", True) and has_duplicate(
            text, code_type, self.records, error_correction=ec, encoding=enc
        ):
            if not self._ask_duplicate(text, code_type, error_correction=ec):
                return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        try:
            if code_type == "Q":
                fp = SAVE_DIR / f"qr_{ts}.png"
                generate_qr(text, fp, error_correction=self._ec_var.get(),
                            encoding=self._enc_var.get())
                rec = {"text": text, "type": code_type, "path": str(fp),
                       "error_correction": self._ec_var.get(),
                       "encoding": self._enc_var.get()}
            else:
                fp = generate_barcode_file(text, SAVE_DIR / f"bar_{ts}")
                rec = {"text": text, "type": code_type, "path": str(fp)}
            self.records.append(rec)
            save_metadata(self.records, METADATA_FILE)

            self._search_var.set("")  # 検索をクリアして新規アイテムを確実に表示
            self._filter_records()
            new_rec_idx = len(self.records) - 1
            lb_pos = (self._filtered_indices.index(new_rec_idx)
                      if new_rec_idx in self._filtered_indices else 0)
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(lb_pos)
            self.listbox.see(lb_pos)
            self._show_record(rec)
            if code_type == "Q":
                self.qr_text.delete("1.0", "end")
            else:
                self.entry_var.set("")

        except Exception as e:
            messagebox.showerror("エラー", f"生成に失敗しました:\n{e}",
                                 parent=self.root)

    def on_delete(self) -> None:
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("削除", "削除するアイテムを選択してください。",
                                parent=self.root)
            return
        rec_indices = [self._rec_idx(i) for i in sel if i < len(self._filtered_indices)]
        count = len(rec_indices)
        msg = f"{count} 件削除しますか？" if count > 1 else f"削除しますか？\n{list_labels(self.records)[rec_indices[0]]}"
        if messagebox.askyesno("確認", msg, parent=self.root):
            for i in sorted(rec_indices, reverse=True):
                try:
                    Path(self.records[i]["path"]).unlink(missing_ok=True)
                except Exception:
                    pass
                self.records.pop(i)
            save_metadata(self.records, METADATA_FILE)
            self._filter_records()
            self.preview_label.config(image="")
            self._photo = None
            self.detail_label.config(text="")
            self._desc_var.set("")
            self._current_rec_idx = None
            self.current_path = None

    def on_export_pdf(self) -> None:
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("PDF出力", "出力するアイテムを選択してください。\n(Ctrl+クリックで複数選択)",
                                parent=self.root)
            return
        selected = [self.records[self._rec_idx(i)] for i in sel if i < len(self._filtered_indices)]
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
            generate_pdf_grid(selected, Path(path), cols=self._pdf_cols_var.get())
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
        resolved = str(SAVE_DIR.resolve())
        if sys.platform == "win32":
            os.startfile(resolved)
        elif sys.platform == "darwin":
            subprocess.run(["open", resolved])
        else:
            subprocess.run(["xdg-open", resolved])
