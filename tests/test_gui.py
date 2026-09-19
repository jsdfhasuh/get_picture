import json
import threading

import numpy as np
from PIL import Image
from PySide6.QtCore import QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest

from capture_tool.models import Roi
from capture_tool.preview import PreviewView
from capture_tool.window import MainWindow
from capture_tool import storage


def test_preview_zoom_controls_live_refresh_wheel_and_original_roi(app, wait, tmp_path):
    settings = QSettings(str(tmp_path / "zoom.ini"), QSettings.Format.IniFormat)
    window = MainWindow(demo=True, settings=settings)
    window.directory.setText(str(tmp_path / "images"))
    window.show()
    try:
        wait(lambda: window.device.count())
        window.connect_button.click()
        wait(lambda: window.save_one.isEnabled())
        view = window.preview
        roi = Roi(100, 80, 200, 160)
        view.set_roi(roi)
        original_scale = view.transform().m11()
        window.zoom_in.click()
        enlarged = view.transform().m11()
        assert enlarged > original_scale
        assert window.zoom_label.text() == f"{enlarged * 100:.1f}%"
        old_frame = window._frame.block_id
        wait(lambda: window._frame.block_id > old_frame + 2)
        assert abs(view.transform().m11() - enlarged) < 1e-9
        window.zoom_out.click()
        assert abs(view.transform().m11() - original_scale) < 1e-9
        pos = QPointF(view.viewport().rect().center())
        wheel = QWheelEvent(pos, QPointF(view.viewport().mapToGlobal(pos.toPoint())),
            QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False)
        app.sendEvent(view.viewport(), wheel)
        assert view.transform().m11() > original_scale
        assert view.roi == roi
        window.format.setCurrentIndex(1)
        window.save_one.click()
        wait(lambda: window._count == 1)
        assert Image.open(next((tmp_path / "images").glob("*.png"))).size == (200, 160)
        for _ in range(80):
            view.zoom_in()
        assert abs(view.transform().m11() - 32) < 1e-9
        for _ in range(100):
            view.zoom_out()
        assert abs(view.transform().m11() - .01) < 1e-9
        view.actual_size()
        assert window.zoom_label.text() == "100.0%"
        view.fit_image()
        assert view._fit
        assert view.roi == roi
    finally:
        window.close()
        wait(lambda: window._ready_to_close)


def test_roi_drag_at_fit_scale_and_move(app):
    view = PreviewView()
    view.resize(700, 500)
    view.show()
    app.processEvents()
    view.set_pixels(np.zeros((1000, 1500, 3), dtype=np.uint8))
    view.set_draw_mode(True)
    start = view.mapFromScene(QPointF(300, 200))
    end = view.mapFromScene(QPointF(1000, 800))
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    QTest.mouseMove(view.viewport(), start)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    assert abs(view.roi.x - 300) < 4
    assert abs(view.roi.y - 200) < 4
    assert abs(view.roi.width - 700) < 5
    roi = view.roi
    start = view.mapFromScene(QPointF(500, 400))
    end = view.mapFromScene(QPointF(550, 450))
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), end)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert view.roi.width == roi.width and view.roi.height == roi.height
    assert view.roi.x > roi.x
    view.close()


def test_demo_end_to_end_manual_timed_compare_settings_close(app, wait, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(demo=True, settings=settings)
    window.directory.setText(str(tmp_path / "图片"))
    window.show()
    try:
        wait(lambda: window.device.count() == 1)
        window.connect_button.click()
        wait(lambda: window.save_one.isEnabled())
        window.preview.set_roi(Roi(100, 80, 200, 160))
        window.format.setCurrentIndex(1)
        window.save_one.click()
        wait(lambda: window._count == 1)
        first = next((tmp_path / "图片").glob("*.png"))
        assert Image.open(first).size == (200, 160)
        window.compare.click()
        wait(lambda: bool(window._dialogs))
        window._dialogs[0].close()
        window.interval.setValue(0.1)
        window.timed.click()
        wait(lambda: window._count >= 4)
        window.timed.click()
        window.param_controls["exposure"][1].setValue(15000)
        window.param_controls["exposure"][2].click()
        wait(lambda: window._params["exposure"]["value"] == 15000)
        window.stream_button.click()
        wait(lambda: window._state == "connected")
        assert not window.save_one.isEnabled()
        assert not window.capture_timer.isActive()
        settings.sync()
    finally:
        window.close()
        wait(lambda: window._ready_to_close)
        assert window.camera.wait(2000)
        assert window.writer.wait(2000)
    rois = json.loads(settings.value("rois"))
    assert rois["DEMO-001:1280x720"] == {"x": 100, "y": 80, "width": 200, "height": 160}


def test_invalid_roi_and_no_stale_save_after_disconnect(app, wait, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(demo=True, settings=settings)
    window.show()
    try:
        wait(lambda: window.device.count())
        window.connect_button.click()
        wait(lambda: window.save_one.isEnabled())
        window.roi_fields["x"].setValue(1200)
        window.roi_fields["width"].setValue(300)
        window.apply_roi.click()
        assert window.preview.roi is None
        assert "范围" in window.notice.text()
        window.connect_button.click()
        wait(lambda: window._state == "disconnected")
        assert not window.save_one.isEnabled()
        assert window._frame is None
    finally:
        window.close()
        wait(lambda: window._ready_to_close)


def test_slow_writer_queue_limit_keeps_preview_alive_and_close_drains(app, wait, tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    real_encode = storage.encode_image
    def delayed_encode(*args, **kwargs):
        started.set()
        assert release.wait(10), "test did not release delayed encoder"
        return real_encode(*args, **kwargs)
    monkeypatch.setattr(storage, "encode_image", delayed_encode)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(demo=True, settings=settings)
    window.directory.setText(str(tmp_path / "saved"))
    window.writer.max_items = 1
    window.show()
    try:
        wait(lambda: window.device.count())
        window.connect_button.click()
        wait(lambda: window.save_one.isEnabled())
        window.save_one.click()
        wait(lambda: started.is_set())
        old_frame = window._frame.block_id
        wait(lambda: window._frame.block_id > old_frame + 2)
        window.interval.setValue(.1)
        window.timed.click()
        wait(lambda: "队列已满" in window.notice.text())
        assert not window.capture_timer.isActive()
        assert window._count == 0
        window.close()
        wait(lambda: not window.camera.isRunning())
        assert not window._ready_to_close
        release.set()
        wait(lambda: window._ready_to_close)
        assert window._count == 1
        assert len(list((tmp_path / "saved").glob("*.jpg"))) == 1
    finally:
        release.set()
        window.close()
        wait(lambda: window._ready_to_close)


def test_missing_sdk_reports_error_and_keeps_window_responsive(app, wait, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    def unavailable():
        raise RuntimeError("SDK missing for test")
    window = MainWindow(settings=settings, camera_factory=unavailable)
    window.show()
    try:
        wait(lambda: "SDK missing" in window.notice.text())
        assert window.camera.isRunning()
        assert window._state == "disconnected"
        assert window.refresh.isEnabled()
        assert not window.save_one.isEnabled()
    finally:
        window.close()
        wait(lambda: window._ready_to_close)
