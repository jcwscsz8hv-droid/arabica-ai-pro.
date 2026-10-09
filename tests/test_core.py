import tempfile
import threading
import unittest
from pathlib import Path
from arabica.backend import BackendError, Cancelled, EngineConfig, check_files, clean_generated_text
from arabica.glossary import LocalStore
from arabica.linguistics import segment, inferred_language, extract_numbers
from arabica.prompts import make_prompt
from arabica.service import Translator
from arabica.quality import check_translation


class FakeEngine:
    def __init__(self, result='هذه ترجمة.'): self.result = result; self.prompts = []
    def chat(self, messages, cancel=None, **kw):
        self.prompts.append(messages)
        return self.result


class TestCore(unittest.TestCase):
    def test_language_detection(self):
        self.assertEqual(inferred_language('مرحبا بكم في العالم'), 'ar')
        self.assertEqual(inferred_language('Добрый день, товарищи'), 'ru')

    def test_segment_exactly_preserves_text(self):
        text = ('Это длинная фраза. ' * 250) + '\n\n' + ('وهذه جملة طويلة. ' * 150)
        chunks = segment(text, max_chars=200)
        self.assertTrue(all(len(c) <= 200 for c in chunks))
        self.assertEqual(''.join(chunks), text)
        self.assertGreater(len(chunks), 2)

    def test_digit_extraction_arabic(self):
        self.assertEqual(extract_numbers('في عام ٢٠٢٦ وأيضاً ٤٢'), ['2026', '42'])

    def test_prompt_fusha_only(self):
        p = make_prompt('Важное соглашение', 'ru-ar', 'professional')
        self.assertIn('Modern Standard Arabic', p)
        self.assertIn('Важное соглашение', p)

    def test_quality_numbers(self):
        w = check_translation('Сейчас 2026 год', 'إنه عام ٢٠٢٥', 'ru-ar')
        self.assertTrue(any('числа' in str(x) for x in w))

    def test_quality_same_numbers(self):
        self.assertEqual(check_translation('Сейчас 2026 год', 'نحن في عام ٢٠٢٦', 'ru-ar'), [])

    def test_store_glossary_and_history(self):
        with tempfile.TemporaryDirectory() as td:
            s = LocalStore(Path(td) / 'db.sqlite')
            s.put_term('переговоры', 'مفاوضات', 'ru-ar', 'diplomatic')
            self.assertEqual(s.terms('ru-ar')[0]['target'], 'مفاوضات')
            s.log('Привет', 'مرحبا', 'ru-ar', 'accurate')
            self.assertEqual(len(s.recent()), 1)
            s.clear_history()
            self.assertEqual(s.recent(), [])

    def test_translator_with_fake_engine(self):
        engine = FakeEngine()
        result = Translator(engine).translate('Привет мир', 'ru-ar')
        self.assertEqual(result.text, 'هذه ترجمة.')
        self.assertEqual(result.chunks, 1)
        self.assertEqual(len(engine.prompts), 1)

    def test_translator_cancelled(self):
        event = threading.Event(); event.set()
        with self.assertRaises(Cancelled):
            Translator(FakeEngine()).translate('Текст', 'ru-ar', cancel=event)

    def test_model_absence_explained(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(BackendError):
                check_files(EngineConfig(Path(td)/'missing.exe', Path(td)/'model.gguf'))

    def test_clean_hidden_reasoning(self):
        self.assertEqual(clean_generated_text('<think>ignore</think>Привет'), 'Привет')
        with self.assertRaises(BackendError):
            clean_generated_text('<think>no closing tag')


if __name__ == '__main__': unittest.main()
