import sys
import tkinter as tk
from pathlib import Path

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


def main() -> None:
    _set_windows_app_id()
    root = tk.Tk()
    icon = _bundled("assets/icon.ico")
    if icon.exists():
        root.iconbitmap(str(icon))
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
