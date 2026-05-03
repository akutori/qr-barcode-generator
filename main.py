import os
import subprocess
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from core import (
    generate_barcode_file,
    generate_pdf_grid,
    generate_qr,
    list_labels,
    load_metadata,
    save_metadata,
)

def _app_dir() -> Path:
    """実行ファイルと同じディレクトリを返す。
    PyInstaller でバイナリ化した場合は .exe の場所、
    スクリプト実行時は main.py の場所。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _bundled(relative: str) -> Path:
    """PyInstaller の展開先 (_MEIPASS) またはスクリプトの親ディレクトリを返す。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / relative  # type: ignore[attr-defined]
    return Path(__file__).parent / relative


SAVE_DIR = _app_dir() / "generated"
METADATA_FILE = SAVE_DIR / "metadata.json"
LEFT_W = 310        # 左パネル固定幅 (px)
_FONT = "Meiryo"
WIN_MIN_W = 560
WIN_MIN_H = 380


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

    # 画像ラベル: 残りスペースを全て使う (主ウィンドウと同じ方式)
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
        # img_label のサイズ変化時のみ再描画 (主ウィンドウと同じ方式)
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

    img_label.bind("<Configure>", redraw)  # top ではなく img_label を監視
    top.after(50, redraw)


# ---------------------------------------------------------------------------
# メインアプリ
# ---------------------------------------------------------------------------

class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("QR & バーコード 生成ツール")
        self.root.minsize(WIN_MIN_W, WIN_MIN_H)
        self.root.geometry("760x500")

        SAVE_DIR.mkdir(exist_ok=True)
        self.records = load_metadata(METADATA_FILE)
        self.current_path: str | None = None
        self._photo = None  # ImageTk.PhotoImage の GC 防止

        self._build_ui()
        self._populate_list()
        if self.records:
            self._show_record(self.records[-1])

    # ── UI 構築 ────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # 左パネル: 固定幅、縦方向は全体に広がる
        lf = tk.Frame(self.root, width=LEFT_W)
        lf.pack(side="left", fill="y", padx=(8, 4), pady=8)
        lf.pack_propagate(False)   # 幅を固定

        tk.Label(lf, text="テキスト入力:", font=(_FONT, 11, "bold"),
                 anchor="w").pack(fill="x")

        self.entry_var = tk.StringVar()
        self.entry = tk.Entry(lf, textvariable=self.entry_var, font=(_FONT, 11))
        self.entry.pack(fill="x", pady=(0, 4))
        self.entry.bind("<Return>", lambda _: self.on_generate())

        radio_f = tk.Frame(lf)
        radio_f.pack(fill="x", pady=(0, 4))
        self.type_var = tk.StringVar(value="QR")
        tk.Radiobutton(radio_f, text="QR コード", variable=self.type_var,
                       value="QR", font=(_FONT, 10)).pack(side="left")
        tk.Radiobutton(radio_f, text="バーコード (Code128)", variable=self.type_var,
                       value="Barcode", font=(_FONT, 10)).pack(side="left")

        tk.Button(lf, text="生成して保存", font=(_FONT, 11, "bold"),
                  command=self.on_generate).pack(fill="x", pady=(0, 8))

        ttk.Separator(lf, orient="horizontal").pack(fill="x", pady=(0, 4))

        tk.Label(lf, text="生成済み一覧",
                 font=(_FONT, 10, "bold"), anchor="w").pack(fill="x")
        tk.Label(lf, text="ダブルクリック: 拡大  Ctrl+クリック: 複数選択",
                 font=(_FONT, 8), fg="gray", anchor="w").pack(fill="x")

        # Listbox: 残りの縦スペースをすべて使う
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

        self._context_menu = tk.Menu(self.root, tearoff=0)
        self._context_menu.add_command(
            label="テキストをコピー", command=self._copy_selected_text
        )

        btn_f = tk.Frame(lf)
        btn_f.pack(fill="x")
        tk.Button(btn_f, text="削除", font=(_FONT, 10), width=8,
                  command=self.on_delete).pack(side="left")
        tk.Button(btn_f, text="フォルダを開く", font=(_FONT, 10),
                  command=self.on_open_folder).pack(side="left", padx=(4, 0))

        tk.Button(lf, text="選択してPDF出力", font=(_FONT, 10),
                  command=self.on_export_pdf).pack(fill="x", pady=(4, 0))

        # 縦セパレータ
        ttk.Separator(self.root, orient="vertical").pack(side="left", fill="y")

        # 右パネル: ウィンドウの残りスペースをすべて使う
        rf = tk.Frame(self.root)
        rf.pack(side="right", expand=True, fill="both", padx=(4, 8), pady=8)

        tk.Label(rf, text="プレビュー", font=(_FONT, 11, "bold"),
                 anchor="w").pack(fill="x")

        # プレビュー画像ラベル: 右パネルの大部分を占める
        self.preview_label = tk.Label(rf, bg="white", anchor="center")
        self.preview_label.pack(expand=True, fill="both")
        # サイズ変化のたびに画像を再描画 (ウィンドウリサイズ対応)
        self.preview_label.bind("<Configure>", self._on_preview_resize)

        self.detail_label = tk.Label(rf, text="", font=(_FONT, 10),
                                      anchor="w", justify="left")
        self.detail_label.pack(fill="x", pady=(4, 0))

    # ── 内部ヘルパー ───────────────────────────────────────────────────────

    def _populate_list(self) -> None:
        self.listbox.delete(0, tk.END)
        for label in list_labels(self.records):
            self.listbox.insert(tk.END, label)

    def _show_record(self, rec: dict) -> None:
        self.current_path = rec["path"]
        self.detail_label.config(
            text=f"[{rec['type']}]  {rec['text']}\n{rec['path']}"
        )
        self._redraw_preview()

    def _redraw_preview(self) -> None:
        """現在のプレビューラベルのサイズに合わせて画像を再描画する"""
        if not self.current_path:
            return
        w = self.preview_label.winfo_width()
        h = self.preview_label.winfo_height()
        if w <= 1 or h <= 1:
            return
        try:
            img = Image.open(self.current_path).convert("RGB")
            img.thumbnail((w, h), Image.LANCZOS)
            self._photo = ImageTk.PhotoImage(img)
            self.preview_label.config(image=self._photo)
        except Exception:
            pass

    def _on_preview_resize(self, event: tk.Event) -> None:
        if event.widget is self.preview_label:
            self._redraw_preview()

    # ── イベントハンドラ ──────────────────────────────────────────────────

    def _on_list_select(self, _: tk.Event) -> None:
        sel = self.listbox.curselection()
        if not sel or sel[-1] >= len(self.records):
            return
        self._show_record(self.records[sel[-1]])

    def _on_list_double(self, _: tk.Event) -> None:
        sel = self.listbox.curselection()
        if not sel or sel[0] >= len(self.records):
            return
        show_enlarged(self.records[sel[0]], self.root)

    def _on_list_right_click(self, event: tk.Event) -> None:
        idx = self.listbox.nearest(event.y)
        if idx < 0 or idx >= len(self.records):
            return
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(idx)
        self._context_menu.tk_popup(event.x_root, event.y_root)

    def _copy_selected_text(self) -> None:
        sel = self.listbox.curselection()
        if not sel or sel[0] >= len(self.records):
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.records[sel[0]]["text"])

    def on_generate(self) -> None:
        text = self.entry_var.get().strip()
        if not text:
            messagebox.showwarning("入力エラー", "テキストを入力してください。",
                                   parent=self.root)
            return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        try:
            if self.type_var.get() == "QR":
                fp = SAVE_DIR / f"qr_{ts}.png"
                generate_qr(text, fp)
                code_type = "QR"
            else:
                fp = generate_barcode_file(text, SAVE_DIR / f"bar_{ts}")
                code_type = "Barcode"

            rec = {"text": text, "type": code_type, "path": str(fp)}
            self.records.append(rec)
            save_metadata(self.records, METADATA_FILE)

            self._populate_list()
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(tk.END)
            self.listbox.see(tk.END)
            self._show_record(rec)
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
        indices = [i for i in sel if i < len(self.records)]
        count = len(indices)
        msg = f"{count} 件削除しますか？" if count > 1 else f"削除しますか？\n{list_labels(self.records)[indices[0]]}"
        if messagebox.askyesno("確認", msg, parent=self.root):
            for i in sorted(indices, reverse=True):
                try:
                    Path(self.records[i]["path"]).unlink(missing_ok=True)
                except Exception:
                    pass
                self.records.pop(i)
            save_metadata(self.records, METADATA_FILE)
            self._populate_list()
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
        selected = [self.records[i] for i in sel if i < len(self.records)]
        path = filedialog.asksaveasfilename(
            parent=self.root,
            defaultextension=".pdf",
            filetypes=[("PDF ファイル", "*.pdf")],
            title="PDFを保存",
        )
        if not path:
            return
        try:
            generate_pdf_grid(selected, Path(path))
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


# ---------------------------------------------------------------------------

def main() -> None:
    root = tk.Tk()
    icon = _bundled("assets/icon.ico")
    if icon.exists():
        root.iconbitmap(str(icon))
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
