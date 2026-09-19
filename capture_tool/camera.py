from __future__ import annotations

import ctypes
import ipaddress
import math
from pathlib import Path
from time import monotonic

import numpy as np

from .imv_binding import (ImvApi, IMV_DeviceList, IMV_OK, IMV_TIMEOUT,
                          IMV_CREATE_BY_CAMERA_KEY, IMV_CREATE_BY_IP_ADDRESS, HuarayCameraError)
from .imv_devices import _IMVDeviceInfo
from .models import Device, Frame


def check(code: int, operation: str) -> None:
    if code != IMV_OK:
        raise RuntimeError(f"{operation}失败（SDK 返回码 {code}）")


def decode(value) -> str:
    return bytes(value).split(b"\0", 1)[0].decode("utf-8", errors="replace")


class EnumEntry(ctypes.Structure):
    _fields_ = [("value", ctypes.c_uint64), ("name", ctypes.c_char * 256)]


class EnumEntries(ctypes.Structure):
    _fields_ = [("nEnumEntryBufferSize", ctypes.c_uint),
               ("pEnumEntryInfo", ctypes.POINTER(EnumEntry))]


class CameraApi(ImvApi):
    def _configureFunctions(self):
        super()._configureFunctions()
        self.clear_buffer = self._configure("IMV_ClearFrameBuffer", [ctypes.c_void_p])
        self._available = self._configure("IMV_FeatureIsAvailable",
            [ctypes.c_void_p, ctypes.c_char_p], ctypes.c_bool)
        self._writeable = self._configure("IMV_FeatureIsWriteable",
            [ctypes.c_void_p, ctypes.c_char_p], ctypes.c_bool)
        self._enum_count = self._configure("IMV_GetEnumFeatureEntryNum",
            [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint)])
        self._enum_entries = self._configure("IMV_GetEnumFeatureEntrys",
            [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(EnumEntries)])

    def devices(self) -> list[Device]:
        raw = IMV_DeviceList()
        check(int(self._enumDevices(ctypes.byref(raw), ctypes.c_uint(0))), "枚举设备")
        if raw.nDevNum and not raw.pDevInfo:
            raise RuntimeError("SDK 返回了无效设备列表")
        if raw.nDevNum > 4096:
            raise RuntimeError("SDK 返回了异常设备数量，请检查运行库版本与位数")
        data = ctypes.cast(raw.pDevInfo, ctypes.POINTER(_IMVDeviceInfo))
        result = []
        for index in range(raw.nDevNum):
            item = data[index]
            result.append(Device(decode(item.cameraKey), decode(item.modelName),
                decode(item.serialNumber), {0: "GigE", 1: "USB3"}.get(item.nCameraType, "IMV"),
                decode(item.DeviceSpecificInfo.gigeDeviceInfo.ipAddress) if item.nCameraType == 0 else ""))
        return result

    def available(self, handle, name: str) -> bool:
        return bool(self._available(handle, name.encode("ascii")))

    def writeable(self, handle, name: str) -> bool:
        return bool(self._writeable(handle, name.encode("ascii")))

    def enum_options(self, handle, name: str) -> list[str]:
        count = ctypes.c_uint()
        check(int(self._enum_count(handle, name.encode("ascii"), ctypes.byref(count))), f"读取 {name} 选项")
        if not 0 < count.value < 4096:
            return []
        buffer = (EnumEntry * count.value)()
        entries = EnumEntries(ctypes.sizeof(buffer), buffer)
        check(int(self._enum_entries(handle, name.encode("ascii"), ctypes.byref(entries))), f"读取 {name} 选项")
        return [decode(entry.name) for entry in buffer if entry.name]


class DahuaCamera:
    """All methods are called by one camera worker; no UI thread SDK access."""

    def __init__(self, sdk_path: str = "", api=None):
        try:
            self.api = api or CameraApi(sdk_path or None)
        except HuarayCameraError as error:
            raise RuntimeError("无法加载大华 / 华睿 SDK。请安装 64 位 MV Viewer 运行库，"
                "或在右侧选择 MVSDKmd.dll。可勾选“模拟相机”先体验。详情：" + str(error)) from error
        self.handle = None
        self.device_key = ""
        self.opened = self.grabbing = False
        self._trigger_original: str | None = None
        self._selector_original: str | None = None
        self._selector_changed = False
        self._trigger_changed = False
        self.source_format = ""

    def enumerate(self) -> list[Device]:
        return self.api.devices()

    def open(self, key: str, ip: bool = False) -> dict:
        if ip:
            key = str(ipaddress.IPv4Address(key.strip()))
        self.api.devices()  # IMV selectors resolve against its process-local list.
        code, handle = self.api.create_handle(IMV_CREATE_BY_IP_ADDRESS if ip else IMV_CREATE_BY_CAMERA_KEY, key)
        check(code, "创建相机句柄")
        self.handle = handle
        try:
            check(self.api.open(handle), "打开相机（请先关闭 MV Viewer 中的采集）")
            self.opened = True
            code, serial = self.api.get_string(handle, "DeviceSerialNumber")
            self.device_key = serial if code == IMV_OK and serial else key
            code, self.source_format = self.api.get_enum(handle, "PixelFormat")
            check(code, "读取像素格式")
            return self.parameters()
        except Exception as error:
            try:
                self.close()
            except Exception as cleanup:
                raise RuntimeError(f"{error}；清理失败：{cleanup}") from error
            raise

    def _set_enum(self, name: str, value: str):
        check(self.api.set_enum(self.handle, name, value), f"设置 {name}")
        code, actual = self.api.get_enum(self.handle, name)
        check(code, f"回读 {name}")
        if actual != value:
            raise RuntimeError(f"{name} 回读不一致：要求 {value}，实际 {actual}")

    def start(self):
        if self.grabbing:
            return
        if self._trigger_original is None:
            if self.api.available(self.handle, "TriggerSelector"):
                code, selector = self.api.get_enum(self.handle, "TriggerSelector")
                check(code, "读取触发选择器")
                self._selector_original = selector
                if selector != "FrameStart":
                    self._selector_changed = True
                    self._set_enum("TriggerSelector", "FrameStart")
            code, original = self.api.get_enum(self.handle, "TriggerMode")
            check(code, "读取原触发模式")
            self._trigger_original = original
        if self._trigger_original != "Off":
            self._trigger_changed = True  # even a failed write may have changed hardware
        self._set_enum("TriggerMode", "Off")
        check(self.api.start_grabbing_ex(self.handle, 1), "开始采集")
        self.grabbing = True

    def stop(self):
        if self.grabbing:
            check(self.api.stop_grabbing(self.handle), "停止采集")
            self.grabbing = False

    def clear(self):
        check(int(self.api.clear_buffer(self.handle)), "清理旧图像缓冲")

    def grab(self, timeout_ms: int = 100) -> Frame | None:
        code, raw = self.api.get_frame(self.handle, timeout_ms)
        if code == IMV_TIMEOUT:
            if not self.api.is_open(self.handle):
                raise RuntimeError("相机连接已断开")
            return None
        check(code, "读取图像")
        primary = None
        result = None
        try:
            info = raw.frameInfo
            if info.status or info.width <= 0 or info.height <= 0 or not info.size or not raw.pData:
                raise RuntimeError("相机返回了不完整或无效的图像")
            mono = self.source_format.startswith("Mono")
            # Direct copy for Mono8/RGB8/BGR8; respect padded rows.
            channels = 1 if info.pixelFormat == 0x01080001 else 3
            if info.pixelFormat in {0x01080001, 0x02180014, 0x02180015}:
                row_bytes = int(info.width) * channels
                stride = row_bytes + int(info.paddingX)
                required = stride * (int(info.height) - 1) + row_bytes
                if int(info.size) < required:
                    raise RuntimeError("图像缓冲长度不足")
                flat = np.ctypeslib.as_array(raw.pData, shape=(int(info.size),))
                rows = np.ndarray((int(info.height), row_bytes), dtype=np.uint8,
                                  buffer=flat, strides=(stride, 1)).copy()
                pixels = rows.reshape((int(info.height), int(info.width)) if channels == 1
                                      else (int(info.height), int(info.width), 3))
                if info.pixelFormat == 0x02180015:
                    pixels = pixels[:, :, ::-1].copy()
            else:
                pixels = self.api.convert_frame(self.handle, raw, "gray" if mono else "bgr", "bilinear")
                if not mono:
                    pixels = pixels[:, :, ::-1].copy()
            if pixels.dtype != np.uint8:
                raise RuntimeError("SDK 像素转换没有返回 8 位图像")
            pixels.setflags(write=False)
            result = Frame(pixels, int(info.blockId), int(info.timeStamp), monotonic(), self.device_key, self.source_format)
        except Exception as error:
            primary = error
        try:
            check(self.api.release_frame(self.handle, raw), "释放 SDK 图像")
        except Exception as cleanup:
            raise RuntimeError(f"{primary or '取帧完成'}；{cleanup}") from cleanup
        if primary:
            raise primary
        return result

    def parameters(self) -> dict:
        result = {}
        for label, candidates, auto in (("exposure", ["ExposureTime"], "ExposureAuto"),
                                        ("gain", ["GainRaw", "Gain"], "GainAuto")):
            state = None
            for feature in candidates:
                if not self.api.available(self.handle, feature):
                    continue
                code, value = self.api.get_double(self.handle, feature)
                range_code, low, high = self.api.get_double_range(self.handle, feature)
                kind, increment = "float", 0
                # Some models expose GainRaw as an integer node rather than a float.
                if code or range_code:
                    code, value = self.api.get_int(self.handle, feature)
                    range_code, low, high, increment = self.api.get_int_range(self.handle, feature)
                    kind = "int"
                if code or range_code or not all(math.isfinite(x) for x in (value, low, high)) or low > high:
                    continue
                state = {"feature": feature, "value": value, "minimum": low,
                         "maximum": high, "kind": kind, "increment": max(1, increment)}
                break
            if state is None:
                result[label] = None
                continue
            modes, mode = [], ""
            if self.api.available(self.handle, auto):
                auto_code, mode = self.api.get_enum(self.handle, auto)
                if auto_code == 0:
                    try:
                        modes = self.api.enum_options(self.handle, auto)
                    except Exception:
                        modes = []
            result[label] = {**state, "auto_feature": auto, "mode": mode, "modes": modes,
                "writable": self.api.writeable(self.handle, feature),
                "auto_writable": bool(modes) and self.api.writeable(self.handle, auto)}
        return result

    def apply(self, label: str, mode: str, value: float) -> dict:
        resume = self.grabbing
        self.stop()
        # On any failure remain stopped. No blind retry or rollback of device writes.
        state = self.parameters().get(label)
        if not state:
            raise RuntimeError("相机不支持该参数或无法可靠回读")
        if mode and mode != state["mode"]:
            if mode not in state["modes"] or not state["auto_writable"]:
                raise RuntimeError("相机不支持或不能写入所选自动模式")
            self._set_enum(state["auto_feature"], mode)
        if not mode or mode == "Off":
            feature = state["feature"]
            if not math.isfinite(value) or not state["minimum"] <= value <= state["maximum"]:
                raise ValueError("参数超出相机允许范围")
            if not self.api.writeable(self.handle, feature):
                raise RuntimeError("参数当前不可写，请检查自动模式")
            if state["kind"] == "int":
                if value != int(value) or (int(value) - state["minimum"]) % state["increment"]:
                    raise ValueError(f"参数必须为整数，并符合相机步长 {state['increment']}")
                check(self.api.set_int(self.handle, feature, int(value)), f"设置 {feature}")
                code, actual = self.api.get_int(self.handle, feature)
            else:
                check(self.api.set_double(self.handle, feature, value), f"设置 {feature}")
                code, actual = self.api.get_double(self.handle, feature)
            check(code, f"回读 {feature}")
            if not math.isclose(actual, value, rel_tol=1e-6, abs_tol=1e-6):
                raise RuntimeError(f"参数回读不一致：要求 {value}，实际 {actual}")
        result = self.parameters()
        if resume:
            self.start()
        return result

    def close(self):
        errors = []
        try:
            self.stop()
        except Exception as error:
            errors.append(str(error))
        if self.opened and self._trigger_changed:
            try:
                self._set_enum("TriggerMode", self._trigger_original)
            except Exception as error:
                errors.append(f"恢复触发模式失败：{error}")
        if self.opened and self._selector_changed:
            try:
                self._set_enum("TriggerSelector", self._selector_original)
            except Exception as error:
                errors.append(f"恢复触发选择器失败：{error}")
        if self.opened:
            try:
                check(self.api.close(self.handle), "关闭相机")
            except Exception as error:
                errors.append(str(error))
        if self.handle is not None:
            try:
                check(self.api.destroy_handle(self.handle), "销毁相机句柄")
            except Exception as error:
                errors.append(str(error))
        self.handle = None
        self.opened = self.grabbing = False
        if errors:
            raise RuntimeError("；".join(errors))


class DemoCamera:
    """Deterministic moving color chart. No SDK or physical camera required."""

    def __init__(self, *args, **kwargs):
        self.grabbing = False
        self.opened = False
        self.device_key = "DEMO-001"
        self.source_format = "RGB8 (模拟)"
        self._id = 0
        self._last = 0.0
        from PIL import Image, ImageDraw, ImageFont
        chart = Image.new("RGB", (1280, 720), "#d6dde2")
        draw = ImageDraw.Draw(chart)
        font_path = Path("C:/Windows/Fonts/arial.ttf")
        def font(size):
            return ImageFont.truetype(str(font_path), size) if font_path.exists() else ImageFont.load_default(size=size)
        for x in range(0, 1280, 40):
            draw.line((x, 0, x, 720), fill="#c6cfd6")
        for y in range(0, 720, 40):
            draw.line((0, y, 1280, y), fill="#c6cfd6")
        draw.rounded_rectangle((160, 80, 1120, 640), radius=22, fill="#eaf0f2", outline="#879da9", width=3)
        draw.text((205, 118), "CAPTURE LAB", fill="#234450", font=font(40))
        draw.text((206, 173), "DEMO-001    /    RGB8    /    1280 x 720", fill="#708993", font=font(20))
        draw.rounded_rectangle((200, 225, 690, 520), radius=12, fill="#173e40", outline="#8ba9a8", width=3)
        for x, y, radius in [(315, 365, 90), (540, 365, 62)]:
            draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill="#8d9ba3", outline="#ced9dd", width=5)
            draw.ellipse((x-radius+18, y-radius+18, x+radius-18, y+radius-18), fill="#263f4c", outline="#52717d", width=5)
            draw.ellipse((x-14, y-14, x+14, y+14), fill="#dce7e8")
        for x in range(220, 680, 22):
            draw.line((x, 485, x, 500 if x % 44 else 510), fill="#8eb9b0", width=2)
        draw.text((740, 235), "EDGE / COLOR", fill="#234450", font=font(24))
        for i, color in enumerate(["#e56252", "#e5bb50", "#45a68d", "#478bb1"]):
            draw.rectangle((740+i*78, 290, 807+i*78, 370), fill=color)
        for i in range(72):
            draw.line((740+i*4, 410, 740+i*4, 455), fill="black" if i % 2 else "white", width=2)
        draw.text((740, 480), "0123456789  ABCDEF", fill="#234450", font=font(22))
        draw.text((205, 568), "Full resolution  /  Software ROI  /  JPEG + PNG", fill="#516f79", font=font(24))
        self._base = np.array(chart)
        self._parameters = {key: {"feature": key, "value": value, "minimum": low, "maximum": high,
            "mode": "Off", "modes": ["Off", "Continuous"], "writable": True, "auto_writable": True}
            for key, value, low, high in [("exposure", 10000., 100., 1000000.), ("gain", 0., 0., 24.)]}

    def enumerate(self):
        return [Device(self.device_key, "模拟彩色相机 1280×720", self.device_key, "模拟")]

    def open(self, key, ip=False):
        self.opened = True
        return self.parameters()

    def parameters(self):
        return {key: dict(value) for key, value in self._parameters.items()}

    def apply(self, label, mode, value):
        self._parameters[label].update(mode=mode, value=value)
        return self.parameters()

    def start(self):
        self.grabbing = True

    def stop(self):
        self.grabbing = False

    def clear(self):
        pass

    def grab(self, timeout_ms=100):
        import time
        time.sleep(max(0, 1 / 30 - (monotonic() - self._last)))
        self._last = monotonic()
        self._id += 1
        pixels = self._base.copy()
        position = 200 + (self._id * 3 % 850)
        pixels[660:672, position:position + 28] = [25, 145, 120]
        pixels.setflags(write=False)
        return Frame(pixels, self._id, self._id * 33333, self._last, self.device_key, self.source_format)

    def close(self):
        self.opened = self.grabbing = False
