import sys
import tkinter as tk
from pathlib import Path

from app import App


def _bundled(relative: str) -> Path:
    """PyInstaller の展開先 (_MEIPASS) またはスクリプトの親ディレクトリを返す。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / relative  # type: ignore[attr-defined]
    return Path(__file__).parent / relative


def main() -> None:
    root = tk.Tk()
    icon = _bundled("assets/icon.ico")
    if icon.exists():
        root.iconbitmap(str(icon))
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
