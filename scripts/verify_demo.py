"""Run an offscreen GUI capture and export reviewable verification artifacts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from time import monotonic
from io import BytesIO

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QSettings
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from capture_tool.models import Roi
from capture_tool.storage import encode_image
from capture_tool.window import MainWindow


def main():
    output = ROOT / "build" / "verification"
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    if not QFontDatabase.families():
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    app.setQuitOnLastWindowClosed(False)

    def wait(predicate):
        deadline = monotonic() + 8
        while monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            QTest.qWait(20)
        raise RuntimeError("演示验证超时")

    window = MainWindow(demo=True, settings=QSettings(str(output / "demo.ini"), QSettings.Format.IniFormat))
    window.directory.setText(str(output / "captures"))
    window.resize(1440, 960)
    window.show()
    try:
        wait(lambda: window.device.count() == 1)
        window.connect_button.click()
        wait(lambda: window.save_one.isEnabled())
        window.preview.set_roi(Roi(180, 120, 780, 480))
        window.draw_roi.setChecked(True)
        window.save_one.click()
        wait(lambda: window._count == 1)
        window.grab().save(str(output / "capture-tool.png"))
        window.compare.click()
        wait(lambda: window._dialogs)
        window._dialogs[-1].grab().save(str(output / "comparison.png"))
        window._dialogs[-1].close()
    finally:
        window.close()
        wait(lambda: window._ready_to_close)

    rng = np.random.default_rng(2026)
    y, x = np.indices((1080, 1920))
    texture = np.stack([x % 256, y % 256, (x // 12 % 2) * 220], axis=2).astype(np.uint8)
    texture = np.clip(texture.astype(np.int16) + rng.integers(-10, 11, texture.shape), 0, 255).astype(np.uint8)
    image = Image.fromarray(texture)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 45)
    draw.rectangle((80, 80, 920, 280), fill="white")
    draw.text((110, 110), "DETAIL TEST 0123456789", fill="black", font=font)
    draw.text((110, 180), "RGB / edges / fine texture", fill="red", font=font)
    pixels = np.array(image)
    report = {"note": "Synthetic test image; ratios are not camera-scene guarantees.", "images": {}}
    for name, data in [("full", pixels), ("roi", pixels[80:560, 80:860])]:
        bmp = BytesIO()
        Image.fromarray(data).save(bmp, format="BMP")
        variants = {"BMP": bmp.getvalue(), "PNG": encode_image(data, "PNG")}
        for quality in [90, 95, 100]:
            variants[f"JPEG_Q{quality}"] = encode_image(data, "JPEG", quality)
        report["images"][name] = {"width": data.shape[1], "height": data.shape[0], "formats": {}}
        for format, encoded in variants.items():
            extension = "jpg" if format.startswith("JPEG") else format.lower()
            (output / f"{name}_{format}.{extension}").write_bytes(encoded)
            decoded = np.asarray(Image.open(BytesIO(encoded)))
            error = float(np.mean((decoded.astype(np.float64) - data.astype(np.float64)) ** 2))
            report["images"][name]["formats"][format] = {"bytes": len(encoded),
                "ratio_to_bmp": round(len(encoded) / len(variants["BMP"]), 4),
                "psnr_db": round(float(10 * np.log10(255 ** 2 / error)), 2) if error else None}
    (output / "compression-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Verification artifacts: {output}")


if __name__ == "__main__":
    main()
