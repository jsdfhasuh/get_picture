from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import logging

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QPushButton, QScrollArea, QSpinBox, QSplitter, QTextEdit, QVBoxLayout, QWidget)

from .acquisition import CameraWorker
from .models import Roi, SaveOptions
from .preview import PreviewView, image_from_array
from .storage import SaveWorker


STYLE = """
QMainWindow, QDialog { background: #f1f5f9; }
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: #24354b; }
QGroupBox { font-weight: 600; border: 1px solid #d7e0eb; border-radius: 9px;
    margin-top: 13px; padding: 15px 10px 8px; background: white; }
QGroupBox::title { subcontrol-origin: margin; left: 13px; padding: 0 5px; }
QPushButton { background: white; border: 1px solid #cbd6e3; border-radius: 6px; padding: 7px 12px; }
QPushButton:hover { background: #e8f1fb; border-color: #81acd7; }
QPushButton:checked { background: #d4f5ed; border-color: #12a58a; }
QPushButton:disabled { color: #a0aaba; background: #eef1f5; border-color: #dce3eb; }
QPushButton#primary { background: #147e70; color: white; border: none; font-weight: 600; }
QPushButton#primary:hover { background: #116c60; }
QPushButton#primary:disabled { background: #a1bbb7; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: white; border: 1px solid #cad5e2;
    border-radius: 5px; padding: 5px; min-height: 21px; }
QLineEdit:disabled, QComboBox:disabled, QDoubleSpinBox:disabled { color: #94a3b8; background: #eef2f6; }
QTextEdit { background: #edf2f7; border: 1px solid #d7e0eb; border-radius: 5px; font-size: 12px; }
QLabel#title { font-size: 23px; font-weight: 700; color: #173d49; }
QLabel#muted { color: #687b91; }
QLabel#error { color: #b43d35; }
QScrollArea { border: none; background: transparent; }
QStatusBar { background: #e6edf5; }
"""


def human_size(value: int) -> str:
    return f"{value / 1024 / 1024:.2f} MB" if value >= 1024 * 1024 else f"{value / 1024:.1f} KB"


class MainWindow(QMainWindow):
    def __init__(self, demo=False, settings=None, camera_factory=None):
        super().__init__()
        self.setWindowTitle("清晰采图 · 大华 / 华睿相机")
        self.resize(1380, 900)
        self.setMinimumSize(1040, 720)
        self.setStyleSheet(STYLE)
        self.settings = settings or QSettings("GetPicture", "CaptureTool")
        self._state = "disconnected"
        self._closing = False
        self._ready_to_close = False
        self._pending_token = None
        self._token = 0
        self._last_frame_id = None
        self._image_identity = None
        self._frame = None
        self._count = 0
        self._auto_start = False
        self._params = {}
        self._dialogs = []
        try:
            self._rois = json.loads(str(self.settings.value("rois", "{}")))
            if not isinstance(self._rois, dict):
                self._rois = {}
        except (ValueError, TypeError):
            self._rois = {}
        self.camera = CameraWorker(camera_factory)
        self.writer = SaveWorker()
        self._build(demo)
        self._wire()
        self._set_state("disconnected")
        self.camera.start()
        self.writer.start()
        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(100)
        self.preview_timer.timeout.connect(self._preview_tick)
        self.preview_timer.start()
        self.capture_timer = QTimer(self)
        self.capture_timer.timeout.connect(self._timed_tick)
        QTimer.singleShot(0, self._discover)

    def _build(self, demo):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 14, 20, 14)
        title = QLabel("清晰采图")
        title.setObjectName("title")
        subtitle = QLabel("大华 / 华睿  ·  原始分辨率  ·  自由裁剪  ·  轻量保存")
        subtitle.setObjectName("muted")
        header = QHBoxLayout()
        header.addWidget(title)
        header.addWidget(subtitle)
        header.addStretch()
        self.demo = QCheckBox("模拟相机")
        self.demo.setChecked(demo)
        header.addWidget(self.demo)
        layout.addLayout(header)

        connection = QHBoxLayout()
        self.device = QComboBox()
        self.device.setMinimumWidth(260)
        self.refresh = QPushButton("刷新设备")
        self.ip = QLineEdit(str(self.settings.value("ip", "")))
        self.ip.setPlaceholderText("或输入 GigE 相机 IP")
        self.ip.setMaximumWidth(200)
        self.connect_button = QPushButton("连接相机")
        self.stream_button = QPushButton("开始预览")
        for widget in [self.device, self.refresh, self.ip, self.connect_button, self.stream_button]:
            connection.addWidget(widget)
        connection.setStretch(0, 1)
        layout.addLayout(connection)

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 10, 0)
        toolbar = QHBoxLayout()
        self.draw_roi = QPushButton("绘制 / 调整 ROI")
        self.draw_roi.setCheckable(True)
        self.clear_roi = QPushButton("清除 ROI")
        fit = QPushButton("适应窗口")
        actual = QPushButton("1:1 像素")
        self.zoom_in = QPushButton("放大")
        self.zoom_out = QPushButton("缩小")
        self.zoom_in.setToolTip("以画面中心放大；也可在画面上向上滚动鼠标滚轮")
        self.zoom_out.setToolTip("以画面中心缩小；也可在画面上向下滚动鼠标滚轮")
        self.zoom_label = QLabel("—")
        self.zoom_label.setMinimumWidth(60)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_label.setToolTip("相对于原图像素的预览比例，仅影响显示")
        for widget in [self.draw_roi, self.clear_roi, self.zoom_out, self.zoom_in, fit, actual, self.zoom_label]:
            toolbar.addWidget(widget)
        toolbar.addStretch()
        left_layout.addLayout(toolbar)
        self.preview = PreviewView()
        left_layout.addWidget(self.preview, 1)
        self.frame_info = QLabel("连接相机后开始预览 · 滚轮缩放，拖动平移")
        self.frame_info.setObjectName("muted")
        left_layout.addWidget(self.frame_info)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(200)
        self.log.setMaximumHeight(105)
        left_layout.addWidget(self.log)
        split.addWidget(left)

        right = QWidget()
        side = QVBoxLayout(right)
        side.setContentsMargins(0, 0, 0, 0)
        roi_group = QGroupBox("保存区域")
        roi_form = QFormLayout(roi_group)
        self.roi_enabled = QCheckBox("仅保存 ROI 区域")
        roi_form.addRow(self.roi_enabled)
        self.roi_fields = {}
        for key, label in [("x", "X"), ("y", "Y"), ("width", "宽度"), ("height", "高度")]:
            spin = QSpinBox()
            spin.setRange(0 if key in {"x", "y"} else 1, 1000000)
            self.roi_fields[key] = spin
            roi_form.addRow(f"{label}（像素）", spin)
        self.apply_roi = QPushButton("应用坐标")
        roi_form.addRow(self.apply_roi)
        roi_hint = QLabel("坐标相对于相机当前输出；保存不缩放。\n绘制模式下拖框、拖动内部或四角调整。")
        roi_hint.setWordWrap(True)
        roi_hint.setObjectName("muted")
        roi_form.addRow(roi_hint)
        side.addWidget(roi_group)

        save_group = QGroupBox("图片保存")
        save_form = QFormLayout(save_group)
        self.directory = QLineEdit(str(self.settings.value("directory", str(Path.cwd() / "captures"))))
        directory_row = QHBoxLayout()
        directory_row.addWidget(self.directory)
        browse = QPushButton("…")
        browse.setMaximumWidth(35)
        directory_row.addWidget(browse)
        save_form.addRow("目录", directory_row)
        self.prefix = QLineEdit(str(self.settings.value("prefix", "capture")))
        save_form.addRow("文件名前缀", self.prefix)
        self.format = QComboBox()
        self.format.addItem("JPEG · 小体积", "JPEG")
        self.format.addItem("PNG · 无损", "PNG")
        self.format.setCurrentIndex(max(0, self.format.findData(self.settings.value("format", "JPEG"))))
        save_form.addRow("格式", self.format)
        self.quality = QSpinBox()
        self.quality.setRange(80, 100)
        self.quality.setValue(self._int_setting("quality", 95))
        save_form.addRow("JPEG 质量", self.quality)
        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.1, 3600)
        self.interval.setDecimals(1)
        self.interval.setSuffix(" 秒")
        self.interval.setValue(self._float_setting("interval", 1.0))
        save_form.addRow("采图间隔", self.interval)
        self.compare = QPushButton("同帧比较 JPEG / PNG")
        save_form.addRow(self.compare)
        hint = QLabel("JPEG 默认质量 95，彩色采用 4:4:4。\nPNG 保留输出像素；文件大小取决于画面。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        save_form.addRow(hint)
        side.addWidget(save_group)

        self.parameter_group = QGroupBox("曝光与增益")
        param_form = QFormLayout(self.parameter_group)
        self.param_controls = {}
        for key, title in [("exposure", "曝光（μs）"), ("gain", "增益（相机单位）")]:
            mode = QComboBox()
            spin = QDoubleSpinBox()
            spin.setDecimals(3)
            spin.setRange(0, 1000000000)
            apply = QPushButton("应用")
            row = QHBoxLayout()
            row.addWidget(spin, 1)
            row.addWidget(apply)
            param_form.addRow(title, mode)
            param_form.addRow(row)
            self.param_controls[key] = (mode, spin, apply)
            apply.clicked.connect(lambda checked=False, k=key: self._apply_parameter(k))
            mode.currentIndexChanged.connect(lambda index, k=key: self._parameter_mode(k))
        self.refresh_parameters = QPushButton("回读相机实际值")
        param_form.addRow(self.refresh_parameters)
        side.addWidget(self.parameter_group)

        sdk_group = QGroupBox("相机 SDK")
        sdk_form = QVBoxLayout(sdk_group)
        self.sdk = QLineEdit(str(self.settings.value("sdk", "")))
        self.sdk.setPlaceholderText("自动查找 MV Viewer；也可指定 DLL")
        sdk_browse = QPushButton("选择 MVSDKmd.dll")
        sdk_form.addWidget(self.sdk)
        sdk_form.addWidget(sdk_browse)
        side.addWidget(sdk_group)
        side.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(right)
        scroll.setMinimumWidth(330)
        split.addWidget(scroll)
        split.setStretchFactor(0, 1)
        split.setSizes([970, 360])
        layout.addWidget(split, 1)

        actions = QHBoxLayout()
        self.save_one = QPushButton("保存一张  ·  Ctrl+S")
        self.save_one.setObjectName("primary")
        self.save_one.setMinimumHeight(42)
        self.timed = QPushButton("开始定时保存")
        self.timed.setMinimumHeight(42)
        self.open_folder = QPushButton("打开保存目录")
        self.stats = QLabel("已保存 0 张")
        self.backlog = QLabel("待保存 0 张")
        for widget in [self.save_one, self.timed, self.open_folder]:
            actions.addWidget(widget)
        actions.addStretch()
        actions.addWidget(self.stats)
        actions.addWidget(self.backlog)
        layout.addLayout(actions)
        self.notice = QLabel("就绪")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.statusBar().showMessage("未连接")

        fit.clicked.connect(self.preview.fit_image)
        actual.clicked.connect(self.preview.actual_size)
        self.zoom_in.clicked.connect(self.preview.zoom_in)
        self.zoom_out.clicked.connect(self.preview.zoom_out)
        self.preview.zoom_changed.connect(lambda scale: self.zoom_label.setText(f"{scale * 100:.1f}%"))
        browse.clicked.connect(self._choose_directory)
        sdk_browse.clicked.connect(self._choose_sdk)
        self.open_folder.clicked.connect(self._open_directory)
        self.shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self.shortcut.activated.connect(lambda: self._request_save() if self.save_one.isEnabled() else None)

    def _int_setting(self, name, default):
        try:
            return int(self.settings.value(name, default))
        except (ValueError, TypeError):
            return default

    def _float_setting(self, name, default):
        try:
            return float(self.settings.value(name, default))
        except (ValueError, TypeError):
            return default

    def _wire(self):
        self.refresh.clicked.connect(self._discover)
        self.demo.toggled.connect(self._discover)
        self.connect_button.clicked.connect(self._connect_toggle)
        self.stream_button.clicked.connect(self._stream_toggle)
        self.camera.devices.connect(self._devices)
        self.camera.state.connect(self._set_state)
        self.camera.parameters.connect(self._parameters)
        self.camera.captured.connect(self._captured)
        self.camera.failed.connect(self._error)
        self.camera.capture_failed.connect(self._capture_failed)
        self.camera.finished.connect(self._camera_finished)
        self.writer.saved.connect(self._saved)
        self.writer.failed.connect(self._error)
        self.writer.backlog.connect(lambda count, size: self.backlog.setText(f"待保存 {count} 张 · {human_size(size)}"))
        self.writer.compared.connect(self._compared)
        self.writer.finished.connect(self._writer_finished)
        self.preview.roi_changed.connect(self._roi_changed)
        self.draw_roi.toggled.connect(self.preview.set_draw_mode)
        self.clear_roi.clicked.connect(lambda: self.preview.set_roi(None))
        self.apply_roi.clicked.connect(self._apply_roi)
        self.roi_enabled.toggled.connect(self._roi_enabled)
        self.save_one.clicked.connect(lambda: self._request_save())
        self.compare.clicked.connect(lambda: self._request_save(True))
        self.timed.clicked.connect(self._timed_toggle)
        self.format.currentIndexChanged.connect(lambda: self.quality.setEnabled(self.format.currentData() == "JPEG"))
        self.quality.setEnabled(self.format.currentData() == "JPEG")
        self.refresh_parameters.clicked.connect(lambda: self.camera.command("refresh_parameters"))

    def _log(self, text):
        logging.info(text)
        self.log.append(f"{datetime.now():%H:%M:%S}  {text}")

    def _message(self, text, error=False):
        self.notice.setText(text)
        self.notice.setStyleSheet("color: #b43d35;" if error else "color: #147e70;")
        self._log(text)

    def _error(self, text):
        self._stop_timed()
        self._pending_token = None
        self._auto_start = False
        self._update_actions()
        self._message(text, True)

    def _discover(self):
        if self._closing or self._state != "disconnected":
            return
        self.device.clear()
        self.camera.command("enumerate", self.demo.isChecked(), self.sdk.text().strip())

    def _devices(self, devices):
        self.device.clear()
        for device in devices:
            self.device.addItem(device.label, device.key)
        self._message(f"发现 {len(devices)} 台{'模拟' if self.demo.isChecked() else ''}相机")

    def _connect_toggle(self):
        if self._state == "disconnected":
            use_ip = bool(self.ip.text().strip()) and not self.demo.isChecked()
            key = self.ip.text().strip() if use_ip else self.device.currentData()
            if not key:
                self._error("请先刷新并选择相机，或输入 GigE 相机 IP。")
                return
            self._auto_start = True
            self.connect_button.setEnabled(False)
            self.camera.command("connect", self.demo.isChecked(), self.sdk.text().strip(), key, use_ip)
        else:
            self._stop_timed()
            self._pending_token = None
            self.connect_button.setEnabled(False)
            self.camera.command("disconnect")

    def _stream_toggle(self):
        self._stop_timed()
        self.stream_button.setEnabled(False)
        self.camera.command("stop" if self._state == "streaming" else "start")

    def _set_state(self, state):
        self._state = state
        connected = state != "disconnected"
        streaming = state == "streaming"
        for widget in [self.device, self.refresh, self.demo, self.ip, self.sdk]:
            widget.setEnabled(not connected and not self._closing)
        self.connect_button.setEnabled(not self._closing)
        self.connect_button.setText("断开相机" if connected else "连接相机")
        self.stream_button.setEnabled(connected and not self._closing)
        self.stream_button.setText("停止预览" if streaming else "开始预览")
        self.parameter_group.setEnabled(connected and not self._closing)
        self.statusBar().showMessage({"disconnected": "未连接", "connected": "已连接 · 预览停止", "streaming": "正在采集 · 输出 8 位图像"}[state])
        if not streaming:
            self._stop_timed()
            self._pending_token = None
            self._last_frame_id = None
            if not connected:
                self._frame = None
                self._image_identity = None
                self.frame_info.setText("未连接 · 画面如有保留，仅用于查看，不能保存")
        self._update_actions()
        if state == "connected" and self._auto_start and not self._closing:
            self._auto_start = False
            self.camera.command("start")

    def _update_actions(self):
        enabled = self._state == "streaming" and self._frame is not None and not self._closing
        self.save_one.setEnabled(enabled and self._pending_token is None)
        self.compare.setEnabled(enabled and self._pending_token is None)
        self.timed.setEnabled(enabled)

    def _preview_tick(self):
        if self._closing or self._state != "streaming":
            return
        frame = self.camera.latest()
        if frame is None or (frame.device_key, frame.block_id, frame.received_at) == self._last_frame_id:
            return
        self._last_frame_id = (frame.device_key, frame.block_id, frame.received_at)
        self._frame = frame
        height, width = frame.pixels.shape[:2]
        identity = f"{frame.device_key}:{width}x{height}"
        changed = identity != self._image_identity
        if changed:
            # Do not let set_pixels' clearing signal overwrite the previously saved ROI.
            self._image_identity = None
        self.preview.set_pixels(frame.pixels)
        if changed:
            self._image_identity = identity
            saved = self._rois.get(identity)
            try:
                self.preview.set_roi(Roi(**saved) if saved else None)
            except (ValueError, TypeError):
                self.preview.set_roi(None)
            for key, spin in self.roi_fields.items():
                spin.setMaximum((width if key in {"x", "width"} else height) - (1 if key in {"x", "y"} else 0))
        channels = "灰度" if frame.pixels.ndim == 2 else "彩色"
        self.frame_info.setText(f"{width} × {height}  ·  帧 {frame.block_id}  ·  {frame.source_format} → 8 位{channels}  ·  {frame.device_key}")
        self._update_actions()

    def _roi_changed(self, roi):
        self.roi_enabled.blockSignals(True)
        self.roi_enabled.setChecked(roi is not None)
        self.roi_enabled.blockSignals(False)
        if roi:
            for key, value in asdict(roi).items():
                self.roi_fields[key].setValue(value)
        if self._image_identity:
            self._rois[self._image_identity] = asdict(roi) if roi else None

    def _roi_enabled(self, checked):
        if checked:
            self._apply_roi()
        else:
            self.preview.set_roi(None)

    def _apply_roi(self):
        try:
            self.preview.set_roi(Roi(**{key: spin.value() for key, spin in self.roi_fields.items()}))
        except ValueError as error:
            self.roi_enabled.blockSignals(True)
            self.roi_enabled.setChecked(self.preview.roi is not None)
            self.roi_enabled.blockSignals(False)
            self._message(str(error), True)

    def _parameters(self, data):
        self._params = data
        for key, (mode, spin, apply) in self.param_controls.items():
            state = data.get(key)
            mode.blockSignals(True)
            mode.clear()
            if not state:
                mode.addItem("不支持 / 无法读取")
                for control in [mode, spin, apply]:
                    control.setEnabled(False)
            else:
                names = {"Off": "手动", "Once": "自动一次", "Continuous": "连续自动"}
                modes = state["modes"] or [state["mode"] or ""]
                for value in modes:
                    mode.addItem(names.get(value, value or "手动"), value)
                mode.setCurrentIndex(max(0, mode.findData(state["mode"])))
                mode.setEnabled(state["auto_writable"])
                spin.setDecimals(0 if state.get("kind") == "int" else 3)
                spin.setSingleStep(state.get("increment", 1))
                spin.setRange(state["minimum"], state["maximum"])
                spin.setValue(state["value"])
                apply.setEnabled(state["writable"] or state["auto_writable"])
            mode.blockSignals(False)
            self._parameter_mode(key)

    def _parameter_mode(self, key):
        mode, spin, _ = self.param_controls[key]
        state = self._params.get(key)
        spin.setEnabled(bool(state) and mode.currentData() in {"Off", ""})

    def _apply_parameter(self, key):
        self._stop_timed()
        mode, spin, _ = self.param_controls[key]
        self.camera.command("apply", key, mode.currentData() or "", spin.value())

    def _options(self):
        text = self.directory.text().strip()
        if not text:
            raise ValueError("请选择保存目录")
        if self._frame is None:
            raise ValueError("尚未收到图像")
        h, w = self._frame.pixels.shape[:2]
        options = SaveOptions(Path(text).expanduser().absolute(), self.prefix.text().strip(),
            self.format.currentData(), self.quality.value(), self.preview.roi, (w, h))
        options.validate()
        return options

    def _request_save(self, compare=False, automatic=False):
        if self._closing or self._pending_token is not None or self._state != "streaming":
            return
        try:
            options = self._options()
        except ValueError as error:
            self._error(str(error))
            return
        if not automatic:
            self.writer.acknowledge_error()
        self._token += 1
        self._pending_token = self._token
        if not self.camera.command("capture", self._token, options, compare):
            self._pending_token = None
        self._update_actions()

    def _captured(self, token, frame, options, compare):
        if token != self._pending_token or self._closing:
            return
        self._pending_token = None
        try:
            if not self.writer.submit(frame, options, compare):
                self._error("保存队列已满或处于错误状态，已暂停定时保存；本次未入队。请等待完成后重试。")
        except Exception as error:
            self._error(str(error))
        self._update_actions()

    def _capture_failed(self, token, error):
        if token == self._pending_token:
            self._pending_token = None
            self._error(error)

    def _timed_toggle(self):
        if self.capture_timer.isActive():
            self._stop_timed()
            self._message("定时保存已停止，已入队图片继续写入。")
        else:
            try:
                self._options()
            except ValueError as error:
                self._error(str(error))
                return
            self.writer.acknowledge_error()
            self.capture_timer.start(round(self.interval.value() * 1000))
            self.interval.setEnabled(False)
            self.timed.setText("停止定时保存")
            self._request_save(automatic=True)

    def _timed_tick(self):
        if self._pending_token is None:
            self._request_save(automatic=True)

    def _stop_timed(self):
        if hasattr(self, "capture_timer"):
            self.capture_timer.stop()
        self.timed.setText("开始定时保存")
        self.interval.setEnabled(not self._closing)

    def _saved(self, result):
        self._count += 1
        self.stats.setText(f"已保存 {self._count} 张 · 最近 {human_size(result.size_bytes)}")
        self._message(f"已保存 {result.width}×{result.height} · 帧 {result.block_id} · {result.path}")

    def _compared(self, result):
        if self._closing:
            return
        dialog = QDialog(self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle("同一帧 · 原尺寸局部画质比较")
        dialog.resize(1100, 650)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"{result['width']}×{result['height']}  |  JPEG Q{result['quality']}：{human_size(result['jpeg_bytes'])}"
            f"  |  PNG：{human_size(result['png_bytes'])}  |  JPEG / PNG：{result['jpeg_bytes'] / max(1, result['png_bytes']):.1%}"))
        layout.addWidget(QLabel("下方为保存区域中心的 1:1 像素局部。比较仅在内存进行，不产生额外文件。"))
        row = QHBoxLayout()
        for title, key in [("原图 / PNG", "original"), ("JPEG 解码后", "jpeg")]:
            column = QVBoxLayout()
            column.addWidget(QLabel(title))
            label = QLabel()
            label.setPixmap(QPixmap.fromImage(image_from_array(result[key])))
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            scroll = QScrollArea()
            scroll.setWidget(label)
            column.addWidget(scroll)
            row.addLayout(column)
        layout.addLayout(row)
        self._dialogs.append(dialog)
        dialog.destroyed.connect(lambda: self._dialogs.remove(dialog) if dialog in self._dialogs else None)
        dialog.show()

    def _choose_directory(self):
        value = QFileDialog.getExistingDirectory(self, "选择保存目录", self.directory.text())
        if value:
            self.directory.setText(value)

    def _choose_sdk(self):
        value, _ = QFileDialog.getOpenFileName(self, "选择 MVSDKmd.dll", self.sdk.text(), "大华 SDK (MVSDKmd.dll)")
        if value:
            self.sdk.setText(value)

    def _open_directory(self):
        path = Path(self.directory.text().strip())
        if path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.absolute())))
        else:
            self._message("保存目录尚不存在，保存第一张图片时会创建。", True)

    def _persist(self):
        for key, value in {"directory": self.directory.text(), "prefix": self.prefix.text(),
            "format": self.format.currentData(), "quality": self.quality.value(),
            "interval": self.interval.value(), "sdk": self.sdk.text(), "ip": self.ip.text(),
            "rois": json.dumps(self._rois)}.items():
            self.settings.setValue(key, value)
        self.settings.sync()

    def closeEvent(self, event: QCloseEvent):
        if self._ready_to_close:
            event.accept()
            return
        event.ignore()
        if self._closing:
            return
        self._closing = True
        self._persist()
        self._stop_timed()
        self._pending_token = None
        self.preview_timer.stop()
        self.centralWidget().setEnabled(False)
        self.statusBar().showMessage("正在停止相机并完成已入队图片，请稍候…")
        self.camera.shutdown()

    def _camera_finished(self):
        if self._closing:
            self.writer.finish()

    def _writer_finished(self):
        if self._closing:
            self._ready_to_close = True
            self.close()
