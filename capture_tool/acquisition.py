from __future__ import annotations

import queue
import threading
from time import monotonic

from PySide6.QtCore import QThread, Signal

from .camera import DahuaCamera, DemoCamera


class CameraWorker(QThread):
    devices = Signal(object)
    state = Signal(str)
    parameters = Signal(object)
    captured = Signal(int, object, object, bool)
    failed = Signal(str)
    capture_failed = Signal(int, str)

    def __init__(self, factory=None):
        super().__init__()
        self._factory = factory
        self._commands = queue.Queue(maxsize=32)
        self._lock = threading.Lock()
        self._latest = None
        self._pending = None
        self._camera = None
        self._exit = threading.Event()

    def command(self, name: str, *args) -> bool:
        try:
            self._commands.put_nowait((name, args))
            return True
        except queue.Full:
            self.failed.emit("相机操作队列已满，请稍后重试。")
            return False

    def latest(self):
        with self._lock:
            return self._latest

    def shutdown(self):
        self._exit.set()

    def _clear_latest(self):
        with self._lock:
            self._latest = None

    def _cancel_pending(self, reason: str):
        if self._pending:
            self.capture_failed.emit(self._pending[0], reason)
            self._pending = None

    def _disconnect(self):
        self._cancel_pending("相机已停止或断开")
        self._clear_latest()
        if self._camera:
            camera, self._camera = self._camera, None
            camera.close()

    def _create(self, demo: bool, sdk: str):
        return self._factory() if self._factory else (DemoCamera() if demo else DahuaCamera(sdk))

    def _handle(self, name, args):
        if name == "enumerate":
            if self._camera:
                raise RuntimeError("刷新设备列表前请先断开相机")
            demo, sdk = args
            camera = self._create(demo, sdk)
            self.devices.emit(camera.enumerate())
        elif name == "connect":
            self._disconnect()
            demo, sdk, key, ip = args
            camera = self._create(demo, sdk)
            self._camera = camera
            try:
                parameters = camera.open(key, ip)
                self.parameters.emit(parameters)
                self.state.emit("connected")
            except Exception:
                self._disconnect()
                self.state.emit("disconnected")
                raise
        elif name == "disconnect":
            try:
                self._disconnect()
            finally:
                self.state.emit("disconnected")
        elif name == "start":
            self._camera.start()
            self._last_frame_at = monotonic()
            self.state.emit("streaming")
        elif name == "stop":
            self._cancel_pending("预览已停止")
            self._camera.stop()
            self._clear_latest()
            self.state.emit("connected")
        elif name == "refresh_parameters":
            self.parameters.emit(self._camera.parameters())
        elif name == "apply":
            self._cancel_pending("调整参数时已取消等待中的保存请求")
            self.parameters.emit(self._camera.apply(*args))
            self._last_frame_at = monotonic()
        elif name == "capture":
            token, options, compare = args
            if not self._camera or not self._camera.grabbing:
                self.capture_failed.emit(token, "请先开始预览")
                return
            if self._pending:
                self.capture_failed.emit(token, "上一张图像仍在等待中")
                return
            self._camera.clear()
            self._pending = (token, options, compare, monotonic() + 10.0)

    def run(self):
        self._last_frame_at = monotonic()
        try:
            while not self._exit.is_set():
                streaming = self._camera and self._camera.grabbing
                try:
                    name, args = self._commands.get(timeout=0.001 if streaming else 0.1)
                except queue.Empty:
                    name = None
                if name:
                    try:
                        self._handle(name, args)
                    except Exception as error:
                        self._cancel_pending(str(error))
                        if name == "capture":
                            self.capture_failed.emit(args[0], str(error))
                        try:
                            if self._camera:
                                self._camera.stop()
                                if name == "apply" and self._camera.opened:
                                    self.parameters.emit(self._camera.parameters())
                        except Exception as cleanup:
                            self.failed.emit(f"停止相机失败：{cleanup}")
                        self.state.emit("connected" if self._camera and self._camera.opened else "disconnected")
                        self.failed.emit(str(error))
                if self._camera and self._camera.grabbing:
                    try:
                        frame = self._camera.grab(100)
                        if frame is not None:
                            self._last_frame_at = monotonic()
                            with self._lock:
                                self._latest = frame
                            if self._pending:
                                token, options, compare, _ = self._pending
                                self._pending = None
                                self.captured.emit(token, frame, options, compare)
                        elif monotonic() - self._last_frame_at > 10:
                            raise RuntimeError("连续 10 秒没有收到新图像，请检查相机连接和曝光设置。")
                        if self._pending and monotonic() > self._pending[3]:
                            self._cancel_pending("等待新图像超时，本次没有保存")
                    except Exception as error:
                        self.failed.emit(str(error))
                        try:
                            self._disconnect()
                        except Exception as cleanup:
                            self.failed.emit(str(cleanup))
                        self.state.emit("disconnected")
        finally:
            try:
                self._disconnect()
            except Exception as error:
                self.failed.emit(str(error))
            self.state.emit("disconnected")
