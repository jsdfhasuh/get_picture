from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import numpy as np


@dataclass(frozen=True)
class Device:
    key: str
    model: str
    serial: str
    transport: str
    ip: str = ""

    @property
    def label(self) -> str:
        return f"{self.model} · {self.serial} · {self.ip or self.transport}"


@dataclass(frozen=True)
class Frame:
    # RGB or single-channel uint8; owns its memory, never an SDK buffer.
    pixels: np.ndarray
    block_id: int
    device_timestamp: int
    received_at: float
    device_key: str
    source_format: str


@dataclass(frozen=True)
class Roi:
    x: int
    y: int
    width: int
    height: int

    def validate(self, width: int, height: int) -> None:
        if (self.x < 0 or self.y < 0 or self.width <= 0 or self.height <= 0
                or self.x + self.width > width or self.y + self.height > height):
            raise ValueError("ROI 超出图像范围，请重新选择。")

    def crop(self, pixels: np.ndarray) -> np.ndarray:
        self.validate(pixels.shape[1], pixels.shape[0])
        return pixels[self.y:self.y + self.height, self.x:self.x + self.width].copy()


@dataclass(frozen=True)
class SaveOptions:
    directory: Path
    prefix: str = "capture"
    format: str = "JPEG"
    quality: int = 95
    roi: Roi | None = None
    expected_size: tuple[int, int] | None = None

    def validate(self) -> None:
        if self.format not in {"JPEG", "PNG"}:
            raise ValueError("不支持的图片格式")
        if not 80 <= self.quality <= 100:
            raise ValueError("JPEG 质量必须为 80–100")
        if not self.prefix.strip() or re.search(r'[<>:"/\\|?*\x00-\x1f]', self.prefix):
            raise ValueError("文件名前缀不能为空，且不能包含路径或特殊字符。")
        if self.prefix.endswith((" ", ".")):
            raise ValueError("文件名前缀不能以空格或句点结尾。")

    def pixels_from(self, frame: Frame) -> np.ndarray:
        size = (frame.pixels.shape[1], frame.pixels.shape[0])
        if self.expected_size is not None and size != self.expected_size:
            raise ValueError("相机图像尺寸已改变，请重新确认 ROI 后保存。")
        return self.roi.crop(frame.pixels) if self.roi else frame.pixels.copy()


@dataclass(frozen=True)
class SaveResult:
    path: Path
    size_bytes: int
    width: int
    height: int
    block_id: int
