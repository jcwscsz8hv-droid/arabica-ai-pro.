"""Heuristic quality flags, never pretend these prove semantic correctness."""
from collections import Counter
from .linguistics import extract_numbers, inferred_language


def check_translation(source: str, target: str, direction: str) -> list[str]:
    warnings: list[str] = []
    if not target.strip():
        return ["Модель вернула пустой перевод."]
    a, b = Counter(extract_numbers(source)), Counter(extract_numbers(target))
    if a != b:
        warnings.append("Проверьте числа и даты: цифровые записи в оригинале и переводе отличаются.")
    expected = "ar" if direction == "ru-ar" else "ru"
    observed = inferred_language(target)
    if observed not in (expected, "mixed", "unknown"):
        warnings.append("Возможная ошибка направления перевода: неожиданный язык результата.")
    if "<think>" in target or "</think>" in target:
        warnings.append("Обнаружена техническая разметка рассуждений модели.")
    return warnings
