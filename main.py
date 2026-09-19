from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description="大华 / 华睿相机采图工具")
    parser.add_argument("--demo", action="store_true", help="使用模拟相机，不依赖 SDK")
    args = parser.parse_args()
    from PySide6.QtCore import QStandardPaths
    from PySide6.QtWidgets import QApplication
    from capture_tool.window import MainWindow

    app = QApplication(sys.argv)
    app.setOrganizationName("GetPicture")
    app.setApplicationName("CaptureTool")
    log_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[RotatingFileHandler(log_dir / "capture.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")])
    window = MainWindow(demo=args.demo)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
