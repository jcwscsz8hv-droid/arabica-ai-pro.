"""Integration tests of the persistent backend against a fake llama-server (no model weights)."""
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from arabica.backend import BackendError, Cancelled, EngineConfig, LlamaServerEngine
from arabica.glossary import LocalStore
from arabica.service import Translator

FAKE = Path(__file__).with_name("fake_llama_server.py")


class EngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="арабика путь ")  # Cyrillic + space in path
        root = Path(cls.tmp.name)
        cls.model = root / "модель.gguf"
        cls.model.write_bytes(b"GGUF" + b"\0" * 64)
        cls.engine = LlamaServerEngine(EngineConfig(FAKE, cls.model, threads=2, launcher=(sys.executable,),
                                                    start_timeout_s=30, stall_timeout_s=10),
                                       log_path=root / "logs" / "server.log")
        cls.engine.start()

    @classmethod
    def tearDownClass(cls):
        cls.engine.stop()
        cls.tmp.cleanup()

    def test_persistent_process(self):
        pid = self.engine.proc.pid
        t = Translator(self.engine)
        t.translate("Первое предложение.", "ru-ar")
        t.translate("Второе предложение.", "ru-ar")
        self.assertEqual(self.engine.proc.pid, pid, "model must not be reloaded per request")

    def test_both_directions_and_numbers(self):
        t = Translator(self.engine)
        r = t.translate("Выделено 120 школ.", "ru-ar")
        self.assertIn("120", r.text)
        self.assertFalse([w for w in r.warnings if w.code == "numbers"])
        r2 = t.translate("وقعت الدولتان 3 اتفاقيات.", "ar-ru")
        self.assertIn("3", r2.text)

    def test_short_input(self):
        r = Translator(self.engine).translate("Книга", "ru-ar")
        self.assertEqual(r.text, "كتاب")

    def test_negation_flag(self):
        r = Translator(self.engine).translate("Он пришёл вовремя.", "ru-ar")
        self.assertFalse([w for w in r.warnings if w.code == "negation"])

    def test_paragraphs_and_context(self):
        src = "Первый абзац с числом 5.\n\nВторой абзац с числом 7."
        r = Translator(self.engine).translate(src, "ru-ar")
        self.assertEqual(r.chunks, 1)  # short text stays one segment
        self.assertIn("5", r.text)

    def test_protected_url(self):
        r = Translator(self.engine).translate("Подробнее: https://example.org/a?b=1 сегодня.", "ru-ar")
        # the fake model drops placeholders when translating into Arabic -> restore appends, issue raised
        self.assertIn("https://example.org/a?b=1", r.text)

    def test_cancel(self):
        ev = threading.Event()
        t = Translator(self.engine)
        threading.Timer(0.5, ev.set).start()
        t0 = time.monotonic()
        with self.assertRaises(Cancelled):
            t.translate("SLOW " + "слово " * 40, "ru-ar", cancel=ev)
        self.assertLess(time.monotonic() - t0, 5)
        # engine still usable after cancel
        self.assertTrue(Translator(self.engine).translate("Снова.", "ru-ar").text)

    def test_unauthorized_rejected(self):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.engine.port, timeout=5)
        c.request("POST", "/v1/chat/completions", body=b"{}", headers={"Content-Type": "application/json"})
        self.assertEqual(c.getresponse().status, 401)

    def test_word_card_and_glossary(self):
        with tempfile.TemporaryDirectory() as td:
            store = LocalStore(Path(td) / "g.sqlite3")
            t = Translator(self.engine, store)
            card = t.word_card("كتاب", "ar-ru")
            self.assertEqual(card["senses"][0]["translation"], "книга")
            self.assertTrue(card["unverified"])
            self.assertTrue(card["transcription"])
            store.put_term("переговоры", "مفاوضات", "ru-ar", "diplomatic")
            r = t.translate("Переговоры продолжатся.", "ru-ar")
            self.assertTrue(any(w.code == "glossary" for w in r.warnings))  # fake model ignores glossary
            self.assertEqual(len(store.recent()), 1)

    def test_diacritize_letters_preserved(self):
        res = Translator(self.engine).diacritize("كتب الطالب")
        self.assertTrue(res["letters_preserved"])
        self.assertTrue(res["transcription"])


class EngineFailureTest(unittest.TestCase):
    def test_corrupt_model(self):
        with tempfile.TemporaryDirectory() as td:
            m = Path(td) / "bad.gguf"
            m.write_bytes(b"NOPE")
            e = LlamaServerEngine(EngineConfig(FAKE, m, launcher=(sys.executable,)))
            with self.assertRaises(BackendError) as cm:
                e.start()
            self.assertIn("GGUF", str(cm.exception))

    def test_crash_reports(self):
        with tempfile.TemporaryDirectory() as td:
            m = Path(td) / "m.gguf"
            m.write_bytes(b"GGUF")
            e = LlamaServerEngine(EngineConfig(FAKE, m, launcher=(sys.executable,), stall_timeout_s=10),
                                  log_path=Path(td) / "s.log")
            e.start()
            with self.assertRaises(BackendError):
                e.chat([{"role": "system", "content": "x"}, {"role": "user", "content": "CRASH"}])
            e.stop()

    def test_missing_binary(self):
        with tempfile.TemporaryDirectory() as td:
            e = LlamaServerEngine(EngineConfig(Path(td) / "none.exe", Path(td) / "m.gguf"))
            with self.assertRaises(BackendError):
                e.start()


if __name__ == "__main__":
    unittest.main()
