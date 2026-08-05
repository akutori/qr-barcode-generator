import sys
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageTk

from app import App


def _bundled(relative: str) -> Path:
    """PyInstaller の展開先 (_MEIPASS) またはスクリプトの親ディレクトリを返す。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / relative  # type: ignore[attr-defined]
    return Path(__file__).parent / relative


def _set_windows_app_id() -> None:
    """タスクバーのアイコンが Python の既定アイコンにグルーピングされるのを防ぐ。

    Windows は実行ファイルを AppUserModelID 単位でタスクバーにグルーピングするため、
    これを明示的に設定しないと本アプリのアイコンではなく Python の既定アイコンが
    タスクバーに表示されてしまう（tk.Tk() 生成前に呼ぶ必要がある）。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "akutori.qr-barcode-gui"
        )
    except Exception:
        pass


def _set_window_icon(root: tk.Tk, icon_path: Path) -> None:
    """ウィンドウ・タスクバーのアイコンを設定する。

    root.iconbitmap() は Tk 内蔵の古い .ico パーサに依存しており、256px 等で使われる
    PNG 圧縮フレームを含むモダンな .ico を解析できず無言で失敗することがある
    （別PCでタスクバーが tkinter の既定アイコンになる不具合の原因）。
    Pillow でデコードした画像を iconphoto() で設定することでこれを回避する。

    .ico に含まれる全サイズを個別に読み込んで渡す。256px 等 1 サイズだけを渡すと、
    タスクバーの小さい枠に対して正しく縮小されず、画像が欠けたように表示される。
    Windows では iconphoto の第一引数を True にすると小さいアイコンが無視され
    大きいアイコンだけがタスクバーにも使われてしまう既知の問題があるため False を指定する
    （代わりに拡大表示・ダイアログ等の Toplevel は個別にアイコンを継承しない）。
    """
    try:
        base = Image.open(icon_path)
        sizes = sorted(base.info.get("sizes") or [base.size])
        photos = []
        for size in sizes:
            frame = Image.open(icon_path)
            frame.size = size
            frame.load()
            photos.append(ImageTk.PhotoImage(frame))
        root.iconphoto(False, *photos)
        root._icon_photo_refs = photos  # PhotoImage の GC 防止
    except Exception:
        pass


def main() -> None:
    _set_windows_app_id()
    root = tk.Tk()
    icon = _bundled("assets/icon.ico")
    if icon.exists():
        _set_window_icon(root, icon)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
