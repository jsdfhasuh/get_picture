from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, JpegImagePlugin
import pytest

from capture_tool.models import Frame, Roi, SaveOptions
from capture_tool.storage import atomic_write, encode_image, SaveWorker


def frame(pixels):
    return Frame(pixels, 42, 123, 0, "test", "RGB8")


def test_roi_preserves_exact_pixels_and_validates_edges():
    pixels = np.arange(100 * 80 * 3, dtype=np.uint8).reshape(80, 100, 3)
    roi = Roi(70, 60, 30, 20)
    cropped = roi.crop(pixels)
    assert cropped.shape == (20, 30, 3)
    np.testing.assert_array_equal(cropped, pixels[60:80, 70:100])
    np.testing.assert_array_equal(np.array(Image.open(BytesIO(encode_image(cropped, "PNG")))), cropped)
    for invalid in [Roi(-1, 0, 1, 1), Roi(99, 0, 2, 1), Roi(0, 0, 0, 1)]:
        with pytest.raises(ValueError):
            invalid.crop(pixels)


def test_jpeg_rgb_order_no_subsampling_and_grayscale():
    rgb = np.zeros((48, 80, 3), dtype=np.uint8)
    rgb[:, :40] = [255, 0, 0]
    rgb[:, 40:] = [0, 0, 255]
    image = Image.open(BytesIO(encode_image(rgb, "JPEG")))
    assert image.size == (80, 48)
    assert JpegImagePlugin.get_sampling(image) == 0
    assert image.getpixel((10, 10))[0] > 250
    assert image.getpixel((60, 10))[2] > 250
    gray = Image.open(BytesIO(encode_image(rgb[:, :, 0], "JPEG")))
    assert gray.mode == "L"


def test_atomic_unicode_collision_and_failure(tmp_path):
    directory = tmp_path / "中文目录"
    first = atomic_write(directory, "图片", "png", b"first")
    second = atomic_write(directory, "图片", "png", b"second")
    assert first != second
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"
    assert not list(directory.glob("*.tmp"))
    bad = tmp_path / "file"
    bad.write_bytes(b"not a folder")
    with pytest.raises(OSError):
        atomic_write(bad, "image", "png", b"data")


def test_save_worker_roi_and_memory_bound(app, wait, tmp_path):
    pixels = np.zeros((100, 100, 3), dtype=np.uint8)
    pixels[:, :, 1] = 180
    options = SaveOptions(tmp_path, format="PNG", roi=Roi(10, 20, 30, 40), expected_size=(100, 100))
    worker = SaveWorker(max_items=1, max_bytes=4000)
    results = []
    worker.saved.connect(results.append)
    assert worker.submit(frame(pixels), options)
    assert not worker.submit(frame(pixels), options)
    worker.start()
    try:
        wait(lambda: len(results) == 1)
        output = np.array(Image.open(results[0].path))
        np.testing.assert_array_equal(output, pixels[20:60, 10:40])
        assert results[0].block_id == 42
    finally:
        worker.finish()
        assert worker.wait(5000)


def test_write_failure_cancels_waiting_jobs(app, wait, tmp_path):
    bad = tmp_path / "not-directory"
    bad.write_text("x")
    worker = SaveWorker()
    failures, successes = [], []
    worker.failed.connect(failures.append)
    worker.saved.connect(successes.append)
    f = frame(np.zeros((12, 12), dtype=np.uint8))
    options = SaveOptions(bad)
    assert worker.submit(f, options)
    assert worker.submit(f, options)
    worker.start()
    try:
        wait(lambda: failures)
        assert not successes
        assert worker._items == 0 and worker._bytes == 0
        assert not worker.submit(f, SaveOptions(tmp_path / "good"))
        worker.acknowledge_error()
        assert worker.submit(f, SaveOptions(tmp_path / "good"))
        wait(lambda: successes)
    finally:
        worker.finish()
        assert worker.wait(5000)


def test_size_change_rejected(tmp_path):
    f = frame(np.zeros((10, 10), dtype=np.uint8))
    with pytest.raises(ValueError):
        SaveOptions(tmp_path, expected_size=(20, 20)).pixels_from(f)
