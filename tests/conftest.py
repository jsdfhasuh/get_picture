import os
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def app():
    instance = QApplication.instance() or QApplication([])
    if not QFontDatabase.families():
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    instance.setQuitOnLastWindowClosed(False)
    return instance


@pytest.fixture
def wait(app):
    def until(predicate, timeout=5000):
        deadline = monotonic() + timeout / 1000
        while monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            QTest.qWait(10)
        raise AssertionError("等待异步操作超时")
    return until
