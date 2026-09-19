from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView

from .models import Roi


def image_from_array(pixels: np.ndarray) -> QImage:
    data = np.ascontiguousarray(pixels)
    fmt = QImage.Format.Format_Grayscale8 if data.ndim == 2 else QImage.Format.Format_RGB888
    return QImage(data.data, data.shape[1], data.shape[0], data.strides[0], fmt).copy()


class PreviewView(QGraphicsView):
    roi_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self._image = QGraphicsPixmapItem()
        self.scene().addItem(self._image)
        self._box = QGraphicsRectItem()
        pen = QPen(QColor("#38e0bd"), 2)
        pen.setCosmetic(True)
        self._box.setPen(pen)
        self._box.setBrush(QColor(56, 224, 189, 24))
        self._box.setZValue(10)
        self.scene().addItem(self._box)
        self._box.hide()
        self._width = self._height = 0
        self.roi: Roi | None = None
        self.draw_mode = False
        self._fit = True
        self._action = None
        self._anchor = QPointF()
        self._original = None
        self.setBackgroundBrush(QColor("#111b29"))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setMinimumSize(420, 300)

    def set_pixels(self, pixels: np.ndarray):
        h, w = pixels.shape[:2]
        changed = (w, h) != (self._width, self._height)
        self._width, self._height = w, h
        self._image.setPixmap(QPixmap.fromImage(image_from_array(pixels)))
        self.scene().setSceneRect(0, 0, w, h)
        if changed:
            self.set_roi(None)
        if changed or self._fit:
            self.fit_image()

    def fit_image(self):
        self._fit = True
        if self._width:
            self.fitInView(QRectF(0, 0, self._width, self._height), Qt.AspectRatioMode.KeepAspectRatio)

    def actual_size(self):
        self._fit = False
        self.resetTransform()

    def set_draw_mode(self, enabled: bool):
        self.draw_mode = enabled
        self.setDragMode(QGraphicsView.DragMode.NoDrag if enabled else QGraphicsView.DragMode.ScrollHandDrag)
        self.viewport().setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.OpenHandCursor)

    def set_roi(self, roi: Roi | None, emit=True):
        if roi:
            roi.validate(self._width, self._height)
        self.roi = roi
        self._box.setVisible(roi is not None)
        if roi:
            self._box.setRect(roi.x, roi.y, roi.width, roi.height)
        if emit:
            self.roi_changed.emit(roi)

    def _clamp(self, point):
        return QPointF(max(0, min(self._width, point.x())), max(0, min(self._height, point.y())))

    def mousePressEvent(self, event):
        if not self.draw_mode or event.button() != Qt.MouseButton.LeftButton or not self._width:
            return super().mousePressEvent(event)
        p = self._clamp(self.mapToScene(event.position().toPoint()))
        self._anchor = p
        self._original = self.roi
        self._action = "draw"
        if self.roi:
            r = self._box.rect()
            threshold = 10 / max(self.transform().m11(), 0.001)
            corners = [r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()]
            for i, corner in enumerate(corners):
                if abs(p.x() - corner.x()) <= threshold and abs(p.y() - corner.y()) <= threshold:
                    self._anchor = corners[3 - i]
                    self._action = "resize"
                    break
            else:
                if r.contains(p):
                    self._action = "move"
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._action:
            return super().mouseMoveEvent(event)
        p = self._clamp(self.mapToScene(event.position().toPoint()))
        if self._action == "move":
            r = self._original
            x = max(0, min(self._width - r.width, r.x + round(p.x() - self._anchor.x())))
            y = max(0, min(self._height - r.height, r.y + round(p.y() - self._anchor.y())))
            roi = Roi(x, y, r.width, r.height)
        else:
            x = max(0, math.floor(min(p.x(), self._anchor.x())))
            y = max(0, math.floor(min(p.y(), self._anchor.y())))
            right = min(self._width, math.ceil(max(p.x(), self._anchor.x())))
            bottom = min(self._height, math.ceil(max(p.y(), self._anchor.y())))
            if right <= x or bottom <= y:
                return
            roi = Roi(x, y, right - x, bottom - y)
        self.set_roi(roi)
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._action and event.button() == Qt.MouseButton.LeftButton:
            self._action = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        self._fit = False
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        current = self.transform().m11()
        if 0.01 <= current * factor <= 32:
            self.scale(factor, factor)
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit:
            self.fit_image()
