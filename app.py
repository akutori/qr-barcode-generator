import json
import os
import shutil
import subprocess
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from core import (
    has_duplicate,
    list_labels,
    list_labels_with_status,
    load_metadata,
    load_settings,
    save_metadata,
    save_settings,
)
from generators import generate_barcode_file, generate_pdf_grid, generate_qr

_FONT = "Meiryo"
LEFT_W = 310        # 左パネル固定幅 (px)
WIN_MIN_W = 560
WIN_MIN_H = 380


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
    top.geometry("260x360")    # 初期サイズ: 旧860x740の約30%
    top.minsize(180, 160)
    top.resizable(True, True)

    img_label = tk.Label(top, bg="white", anchor="center")
    img_label.pack(expand=True, fill="both", padx=6, pady=(6, 0))

    info = tk.Frame(top)
    info.pack(fill="x", padx=6, pady=(3, 6))
    tk.Label(info, text=f"[{record['type']}]  {record['text']}",
             font=(_FONT, 10, "bold")).pack()
    tk.Label(info, text=f"保存先: {record['path']}",
             font=(_FONT, 8), fg="gray").pack()
    tk.Button(info, text="閉じる", width=10, command=top.destroy,
              font=(_FONT, 10)).pack(pady=3)

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
        self.root.geometry("760x580")

        SAVE_DIR.mkdir(exist_ok=True)
        self.records = self._load_metadata_safe()
        self.settings = load_settings(SETTINGS_FILE)
        self.current_path: str | None = None
        self._photo = None  # ImageTk.PhotoImage の GC 防止
        self._filtered_indices: list[int] = []
        self._tooltip_win: tk.Toplevel | None = None
        self._tooltip_after: str | None = None
        self._tooltip_rec_idx: int = -1

        self._build_menu()
        self._build_ui()
        self._filter_records()
        if self.records:
            self._show_record(self.records[-1])

    # ── メニュー ──────────────────────────────────────────────────────────────

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        opt_menu = tk.Menu(menubar, tearoff=0)
        self._warn_var = tk.BooleanVar(value=self.settings.get("warn_on_duplicate", True))
        opt_menu.add_checkbutton(
            label="重複確認ダイアログを表示する",
            variable=self._warn_var,
            command=self._on_warn_toggle,
        )
        opt_menu.add_separator()
        opt_menu.add_command(label="バージョン情報", command=self._show_about)
        menubar.add_cascade(label="オプション", menu=opt_menu)
        self.root.config(menu=menubar)

    def _on_warn_toggle(self) -> None:
        self.settings["warn_on_duplicate"] = self._warn_var.get()
        save_settings(self.settings, SETTINGS_FILE)

    def _show_about(self) -> None:
        messagebox.showinfo(
            "バージョン情報",
            "QR & バーコード 生成ツール\nバージョン 1.4.0",
            parent=self.root,
        )

    # ── 設定変更コールバック ──────────────────────────────────────────────────

    def _on_type_change(self) -> None:
        t = self.type_var.get()
        if t == "QR":
            # Barcode → QR: Entry の内容を qr_text に引き継いで切り替え
            current = self.entry_var.get()
            self.entry.pack_forget()
            self.qr_text.pack(fill="x")   # コンテナ内のため before= 不要
            if current:
                self.qr_text.delete("1.0", "end")
                self.qr_text.insert("1.0", current)
            self._ec_frame.pack(fill="x", pady=(0, 4), after=self._radio_f)
        else:
            # QR → Barcode: qr_text の先頭行を Entry に引き継いで切り替え
            first_line = self.qr_text.get("1.0", "end-1c").split("\n")[0].strip()
            self.qr_text.pack_forget()
            self.entry.pack(fill="x")     # コンテナ内のため before= 不要
            self.entry_var.set(first_line)
            self._ec_frame.pack_forget()
        self.settings["default_type"] = t
        save_settings(self.settings, SETTINGS_FILE)

    def _on_ec_change(self, *_) -> None:
        self.settings["qr_error_correction"] = self._ec_var.get()
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
        self.type_var = tk.StringVar(value=self.settings.get("default_type", "QR"))
        tk.Radiobutton(radio_f, text="QR コード", variable=self.type_var,
                       value="QR", font=(_FONT, 10),
                       command=self._on_type_change).pack(side="left")
        tk.Radiobutton(radio_f, text="バーコード (Code128)", variable=self.type_var,
                       value="Barcode", font=(_FONT, 10),
                       command=self._on_type_change).pack(side="left")

        # 初期タイプに応じて入力ウィジェットを表示
        if self.type_var.get() == "QR":
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
        if self.type_var.get() == "QR":
            self._ec_frame.pack(fill="x", pady=(0, 4))

        tk.Button(lf, text="生成して保存", font=(_FONT, 11, "bold"),
                  command=self.on_generate).pack(fill="x", pady=(0, 8))

        ttk.Separator(lf, orient="horizontal").pack(fill="x", pady=(0, 4))

        tk.Label(lf, text="生成済み一覧",
                 font=(_FONT, 10, "bold"), anchor="w").pack(fill="x")
        tk.Label(lf, text="ダブルクリック: 拡大  Ctrl+クリック: 複数選択",
                 font=(_FONT, 8), fg="gray", anchor="w").pack(fill="x")

        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._filter_records())
        tk.Entry(lf, textvariable=self._search_var, font=(_FONT, 10)).pack(
            fill="x", pady=(2, 0))

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

        self._context_menu = tk.Menu(self.root, tearoff=0)
        self._context_menu.add_command(
            label="テキストをコピー", command=self._copy_selected_text
        )
        self._context_menu.add_command(
            label="画像をコピー", command=self._copy_selected_image
        )

        btn_f = tk.Frame(lf)
        btn_f.pack(fill="x")
        tk.Button(btn_f, text="削除", font=(_FONT, 10), width=8,
                  command=self.on_delete).pack(side="left")
        tk.Button(btn_f, text="フォルダを開く", font=(_FONT, 10),
                  command=self.on_open_folder).pack(side="left", padx=(4, 0))

        pdf_f = tk.Frame(lf)
        pdf_f.pack(fill="x", pady=(4, 0))
        tk.Button(pdf_f, text="選択してPDF出力", font=(_FONT, 9),
                  command=self.on_export_pdf).pack(side="left", fill="x", expand=True)
        tk.Label(pdf_f, text="列:", font=(_FONT, 9), fg="gray").pack(side="left", padx=(6, 2))
        self._pdf_cols_var = tk.IntVar(value=self.settings.get("pdf_cols", 3))
        self._pdf_cols_var.trace_add("write", self._on_pdf_cols_change)
        tk.Spinbox(pdf_f, textvariable=self._pdf_cols_var,
                   from_=1, to=6, width=3, font=(_FONT, 13)).pack(side="left")

        ttk.Separator(self.root, orient="vertical").pack(side="left", fill="y")

        rf = tk.Frame(self.root)
        rf.pack(side="right", expand=True, fill="both", padx=(4, 8), pady=8)

        tk.Label(rf, text="プレビュー", font=(_FONT, 11, "bold"),
                 anchor="w").pack(fill="x")

        # detail_label を先に bottom へ固定（preview_label が expand しても隠れなくなる）
        self.detail_label = tk.Label(rf, text="", font=(_FONT, 10),
                                      anchor="w", justify="left")
        self.detail_label.pack(side="bottom", fill="x", pady=(4, 0))

        self.preview_label = tk.Label(rf, bg="white", anchor="center")
        self.preview_label.pack(expand=True, fill="both")
        self.preview_label.bind("<Configure>", self._on_preview_resize)

    # ── 内部ヘルパー ───────────────────────────────────────────────────────

    def _filter_records(self) -> None:
        query = self._search_var.get().strip().lower()
        if query:
            self._filtered_indices = [
                i for i, r in enumerate(self.records)
                if query in r["text"].lower()
            ]
        else:
            self._filtered_indices = list(range(len(self.records)))
        self._populate_list()

    def _rec_idx(self, lb_idx: int) -> int:
        return self._filtered_indices[lb_idx]

    def _populate_list(self) -> None:
        self.listbox.delete(0, tk.END)
        labels = list_labels_with_status(self.records)
        for i in self._filtered_indices:
            self.listbox.insert(tk.END, labels[i])

    def _show_record(self, rec: dict) -> None:
        self.current_path = rec["path"]
        # 複数行テキストは先頭 3 行のみ表示（それ以降は行数を表示）
        lines = rec["text"].split("\n")
        _MAX_PREVIEW_LINES = 3
        if len(lines) > _MAX_PREVIEW_LINES:
            display = "\n".join(lines[:_MAX_PREVIEW_LINES]) + f"\n … (+{len(lines) - _MAX_PREVIEW_LINES}行)"
        else:
            display = rec["text"]
        self.detail_label.config(
            text=f"[{rec['type']}]  {display}\n{rec['path']}"
        )
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

    def _on_list_hover(self, event: tk.Event) -> None:
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
            400, lambda: self._show_tooltip(self.records[rec_idx]["text"], x, y)
        )

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
        self._context_menu.tk_popup(event.x_root, event.y_root)

    def _copy_selected_text(self) -> None:
        sel = self.listbox.curselection()
        if not sel or sel[0] >= len(self._filtered_indices):
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.records[self._rec_idx(sel[0])]["text"])

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

        type_label = f"{code_type}:{error_correction}" if code_type == "QR" and error_correction else code_type
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
        code_type = self.type_var.get()
        if code_type == "QR":
            text = self.qr_text.get("1.0", "end-1c").strip()
        else:
            text = self.entry_var.get().strip()
        if not text:
            messagebox.showwarning("入力エラー", "テキストを入力してください。",
                                   parent=self.root)
            return

        ec = self._ec_var.get() if code_type == "QR" else None
        if self.settings.get("warn_on_duplicate", True) and has_duplicate(text, code_type, self.records, error_correction=ec):
            if not self._ask_duplicate(text, code_type, error_correction=ec):
                return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        try:
            if code_type == "QR":
                fp = SAVE_DIR / f"qr_{ts}.png"
                generate_qr(text, fp, error_correction=self._ec_var.get())
                rec = {"text": text, "type": code_type, "path": str(fp),
                       "error_correction": self._ec_var.get()}
            else:
                fp = generate_barcode_file(text, SAVE_DIR / f"bar_{ts}")
                rec = {"text": text, "type": code_type, "path": str(fp)}
            self.records.append(rec)
            save_metadata(self.records, METADATA_FILE)

            self._search_var.set("")  # 検索をクリアして新規アイテムを確実に表示
            self._filter_records()
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(tk.END)
            self.listbox.see(tk.END)
            self._show_record(rec)
            if code_type == "QR":
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
