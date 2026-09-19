import ctypes
from types import SimpleNamespace

import numpy as np
import pytest

from capture_tool.camera import DahuaCamera


class FakeApi:
    def __init__(self):
        self.events = []
        self.enums = {"PixelFormat": "BGR8", "TriggerMode": "On", "ExposureAuto": "Off", "GainAuto": "Off"}
        self.doubles = {"ExposureTime": 10000., "GainRaw": 1.}
        self.status = 0
        self.fail_readback = False
        self.fail_convert = False

    def devices(self):
        return []

    def create_handle(self, *args):
        return 0, 123

    def open(self, handle):
        return 0

    def is_open(self, handle):
        return True

    def get_string(self, handle, name):
        return 0, "camera-123"

    def get_enum(self, handle, name):
        return 0, self.enums[name]

    def set_enum(self, handle, name, value):
        self.events.append((name, value))
        self.enums[name] = value
        return 0

    def available(self, handle, name):
        return name in self.enums or name in self.doubles

    def writeable(self, handle, name):
        return True

    def enum_options(self, handle, name):
        return ["Off", "Continuous"]

    def get_double(self, handle, name):
        return 0, self.doubles[name] + (1 if self.fail_readback else 0)

    def get_double_range(self, handle, name):
        return 0, 0., 100000.

    def set_double(self, handle, name, value):
        self.events.append((name, value))
        self.doubles[name] = value
        return 0

    def start_grabbing_ex(self, handle, strategy):
        self.events.append("start")
        return 0

    def stop_grabbing(self, handle):
        self.events.append("stop")
        return 0

    def clear_buffer(self, handle):
        self.events.append("clear")
        return 0

    def get_frame(self, handle, timeout):
        # Two BGR rows, one pixel plus one padding byte per row.
        self.buffer = (ctypes.c_ubyte * 8)(3, 2, 1, 255, 6, 5, 4, 255)
        return 0, SimpleNamespace(frameInfo=SimpleNamespace(status=self.status, width=1,
            height=2, size=8, paddingX=1, pixelFormat=0x02180015, blockId=7, timeStamp=99),
            pData=ctypes.cast(self.buffer, ctypes.POINTER(ctypes.c_ubyte)))

    def release_frame(self, handle, frame):
        self.events.append("release")
        ctypes.memset(self.buffer, 0, 8)
        return 0

    def close(self, handle):
        self.events.append("close")
        return 0

    def destroy_handle(self, handle):
        self.events.append("destroy")
        return 0


def test_frame_owns_pixels_padding_channel_order_release_and_restore():
    api = FakeApi()
    camera = DahuaCamera(api=api)
    camera.open("test")
    assert not api.events  # opening does not write exposure, gain or trigger
    camera.start()
    result = camera.grab()
    assert result.block_id == 7 and result.device_timestamp == 99
    np.testing.assert_array_equal(result.pixels, [[[1, 2, 3]], [[4, 5, 6]]])
    assert api.events.count("release") == 1
    camera.close()
    assert api.enums["TriggerMode"] == "On"
    assert api.events[-4:] == ["stop", ("TriggerMode", "On"), "close", "destroy"]


def test_invalid_frame_released_and_cleanup_occurs():
    api = FakeApi()
    camera = DahuaCamera(api=api)
    camera.open("test")
    camera.start()
    api.status = 1
    with pytest.raises(RuntimeError, match="无效"):
        camera.grab()
    assert api.events.count("release") == 1
    camera.close()
    assert api.events[-1] == "destroy"


def test_parameter_apply_verifies_and_failure_stops():
    api = FakeApi()
    camera = DahuaCamera(api=api)
    camera.open("test")
    camera.start()
    result = camera.apply("exposure", "Off", 22000)
    assert result["exposure"]["value"] == 22000
    assert camera.grabbing
    api.fail_readback = True
    with pytest.raises(RuntimeError, match="回读不一致"):
        camera.apply("exposure", "Off", 33000)
    assert not camera.grabbing
    camera.close()


def test_out_of_range_not_written():
    api = FakeApi()
    camera = DahuaCamera(api=api)
    camera.open("test")
    with pytest.raises(ValueError):
        camera.apply("gain", "Off", 100001)
    assert not any(isinstance(e, tuple) and e[0] == "GainRaw" for e in api.events)
    camera.close()


def test_trigger_selector_restored_with_frame_start_mode():
    api = FakeApi()
    api.enums["TriggerSelector"] = "AcquisitionStart"
    camera = DahuaCamera(api=api)
    camera.open("test")
    camera.start()
    assert api.enums["TriggerSelector"] == "FrameStart"
    assert api.enums["TriggerMode"] == "Off"
    camera.close()
    assert api.enums["TriggerSelector"] == "AcquisitionStart"
    assert api.enums["TriggerMode"] == "On"


def test_conversion_error_releases_frame():
    api = FakeApi()
    original_get = api.get_frame
    def get_bayer(*args):
        code, frame = original_get(*args)
        frame.frameInfo.pixelFormat = 0x01080009
        return code, frame
    api.get_frame = get_bayer
    def fail_conversion(*args):
        raise RuntimeError("conversion failed")
    api.convert_frame = fail_conversion
    camera = DahuaCamera(api=api)
    camera.open("test")
    with pytest.raises(RuntimeError, match="conversion failed"):
        camera.grab()
    assert api.events.count("release") == 1
    camera.close()


def test_integer_gain_node_and_increment():
    class IntegerGainApi(FakeApi):
        def get_double(self, handle, name):
            return (-110, 0) if name == "GainRaw" else super().get_double(handle, name)

        def get_int(self, handle, name):
            return 0, int(self.doubles[name])

        def get_int_range(self, handle, name):
            return 0, 1, 25, 2

        def set_int(self, handle, name, value):
            self.doubles[name] = value
            return 0

    camera = DahuaCamera(api=IntegerGainApi())
    parameters = camera.open("test")
    assert parameters["gain"]["kind"] == "int"
    assert camera.apply("gain", "Off", 5)["gain"]["value"] == 5
    with pytest.raises(ValueError, match="步长"):
        camera.apply("gain", "Off", 6)
    camera.close()
