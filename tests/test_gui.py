"""GUI smoke test: real MainWindow (offscreen) + real engine client against the fake llama-server.

Runs in CI (Linux and Windows) with QT_QPA_PLATFORM=offscreen. Skipped if PySide6 is unavailable.
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="арабика gui ")
os.environ["LOCALAPPDATA"] = _TMP

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False

FAKE = Path(__file__).with_name("fake_llama_server.py")


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class GuiSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import arabica.gui as gui
        import arabica.integrity as integrity
        from arabica.backend import EngineConfig, LlamaServerEngine

        cls.app = QApplication.instance() or QApplication([])
        model = Path(_TMP) / "модель.gguf"
        model.write_bytes(b"GGUF" + b"\0" * 32)

        def fake_engine():
            return LlamaServerEngine(EngineConfig(FAKE, model, launcher=(sys.executable,), start_timeout_s=30,
                                                  stall_timeout_s=10), log_path=Path(_TMP) / "engine.log")

        gui.make_engine = fake_engine
        gui.check_memory_or_raise = lambda: None
        integrity.verify_model = lambda **kw: {"verified": True, "cached": True}
        cls.gui = gui

    def wait(self, cond, timeout=30.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            self.app.processEvents()
            if cond():
                return True
            time.sleep(0.02)
        return False

    def test_end_to_end(self):
        w = self.gui.MainWindow()
        w.show()
        try:
            self.assertTrue(self.wait(lambda: w.ready), "engine did not become ready: " + w.status.text())
            self.assertTrue(w.go.isEnabled())
            # RU -> MSA: output pane is right-to-left
            self.assertEqual(w.dst.layoutDirection(), Qt.RightToLeft)
            w.src.setPlainText("Министр заявил, что встреча состоится 15 марта.")
            w.go.click()
            self.assertTrue(self.wait(lambda: w.result is not None and not w.runner.busy))
            self.assertIn("15", w.dst.toPlainText())
            self.assertEqual(len(w.store.recent()), 1, "translation must be saved to local history")
            # swap: output becomes input, direction flips to MSA -> RU
            w.swap()
            self.assertEqual(w.direction, "ar-ru")
            self.assertEqual(w.src.layoutDirection(), Qt.RightToLeft)
            self.assertEqual(w.dst.layoutDirection(), Qt.LeftToRight)
            w.result = None
            w.go.click()
            self.assertTrue(self.wait(lambda: w.result is not None and not w.runner.busy))
            self.assertTrue(w.dst.toPlainText())
            # cancel during a slow translation keeps the app usable
            w.swap()
            w.src.setPlainText("SLOW " + "слово " * 40)
            w.result = None
            w.go.click()
            self.assertTrue(self.wait(lambda: w.runner.busy, 5))
            w.stop.click()
            self.assertTrue(self.wait(lambda: not w.runner.busy, 10))
            self.assertIn("отмен", w.status.text().lower())
            # engine process is a single persistent child bound to loopback
            self.assertTrue(w.engine.running)
        finally:
            w.close()
            self.app.processEvents()
        self.assertFalse(w.engine.running, "engine must stop with the window")


if __name__ == "__main__":
    unittest.main()
