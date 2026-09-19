from __future__ import annotations

from datetime import datetime
from io import BytesIO
import os
from pathlib import Path
import tempfile
import threading
from uuid import uuid4

import numpy as np
from PIL import Image
from PySide6.QtCore import QThread, Signal

from .models import Frame, SaveOptions, SaveResult


def encode_image(pixels: np.ndarray, format: str, quality: int = 95) -> bytes:
    if pixels.dtype != np.uint8 or not (pixels.ndim == 2 or
            (pixels.ndim == 3 and pixels.shape[2] == 3)):
        raise ValueError("保存仅支持 8 位灰度或 RGB 图像。")
    stream = BytesIO()
    image = Image.fromarray(pixels)
    if format == "JPEG":
        image.save(stream, format="JPEG", quality=quality, subsampling=0, optimize=True)
    elif format == "PNG":
        image.save(stream, format="PNG", compress_level=6)
    else:
        raise ValueError("不支持的图片格式")
    return stream.getvalue()


def atomic_write(directory: Path, stem: str, extension: str, data: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".capture-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Hard-link is an atomic no-overwrite publish on NTFS and POSIX.
        # FAT/exFAT use Windows rename, which also refuses an existing target.
        for suffix in range(1000):
            name = stem if suffix == 0 else f"{stem}_{suffix}"
            target = directory / f"{name}.{extension}"
            try:
                if os.name == "nt":
                    os.rename(temporary, target)
                else:
                    os.link(temporary, target)
                return target
            except FileExistsError:
                continue
        raise OSError("无法生成不重名的文件名。")
    finally:
        Path(temporary).unlink(missing_ok=True)


class SaveWorker(QThread):
    saved = Signal(object)
    failed = Signal(str)
    backlog = Signal(int, int)
    compared = Signal(object)

    def __init__(self, max_items: int = 8, max_bytes: int = 256 * 1024 * 1024):
        super().__init__()
        self.max_items, self.max_bytes = max_items, max_bytes
        self._condition = threading.Condition()
        self._jobs: list[tuple] = []
        self._bytes = self._items = 0
        self._closing = False
        self._failed = False
        self._sequence = 0
        self._session_id = uuid4().hex[:8]

    def submit(self, frame: Frame, options: SaveOptions, compare: bool = False) -> bool:
        options.validate()
        # Reserve before copying the ROI to avoid transient unbounded allocations.
        h, w = frame.pixels.shape[:2]
        if options.expected_size is not None and options.expected_size != (w, h):
            raise ValueError("图像尺寸已变化，请重新确认保存区域。")
        if options.roi:
            options.roi.validate(w, h)
        count = (options.roi.width * options.roi.height if options.roi else w * h)
        size = count * (3 if frame.pixels.ndim == 3 else 1)
        with self._condition:
            if self._closing or self._failed or self._items >= self.max_items or self._bytes + size > self.max_bytes:
                return False
            pixels = options.pixels_from(frame)
            self._jobs.append((pixels, frame.block_id, options, compare))
            self._items += 1
            self._bytes += pixels.nbytes
            self.backlog.emit(self._items, self._bytes)
            self._condition.notify()
            return True

    def acknowledge_error(self) -> None:
        with self._condition:
            self._failed = False

    def finish(self) -> None:
        with self._condition:
            self._closing = True
            self._condition.notify_all()

    def run(self) -> None:
        while True:
            with self._condition:
                while not self._jobs and not self._closing:
                    self._condition.wait()
                if not self._jobs:
                    return
                pixels, block, options, compare = self._jobs.pop(0)
            try:
                if compare:
                    jpeg = encode_image(pixels, "JPEG", options.quality)
                    png = encode_image(pixels, "PNG")
                    # Carry only a 512-pixel center crop to the comparison UI.
                    decoded = np.array(Image.open(BytesIO(jpeg)))
                    h, w = pixels.shape[:2]
                    x, y = max(0, (w - 512) // 2), max(0, (h - 512) // 2)
                    self.compared.emit({"jpeg_bytes": len(jpeg), "png_bytes": len(png),
                        "original": pixels[y:y+512, x:x+512].copy(),
                        "jpeg": decoded[y:y+512, x:x+512].copy(), "quality": options.quality,
                        "width": w, "height": h})
                else:
                    data = encode_image(pixels, options.format, options.quality)
                    self._sequence += 1
                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    stem = f"{options.prefix}_{stamp}_{self._session_id}_{self._sequence:06d}"
                    path = atomic_write(options.directory, stem, "jpg" if options.format == "JPEG" else "png", data)
                    self.saved.emit(SaveResult(path, len(data), pixels.shape[1], pixels.shape[0], block))
            except Exception as error:
                with self._condition:
                    cancelled = len(self._jobs)
                    for job in self._jobs:
                        self._bytes -= job[0].nbytes
                        self._items -= 1
                    self._jobs.clear()
                    self._failed = True
                self.failed.emit(f"保存失败：{error}。已取消 {cancelled} 个等待任务；请检查目录与磁盘后重试。")
            finally:
                with self._condition:
                    self._items -= 1
                    self._bytes -= pixels.nbytes
                    self.backlog.emit(self._items, self._bytes)
