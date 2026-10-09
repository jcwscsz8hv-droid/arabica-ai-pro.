"""Generate docs/MODEL_PROMPTS.md from arabica/prompts.py (the single source of truth)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from arabica.prompts import (MODES, PROMPT_VERSION, WORDCARD_SCHEMA, build_messages,  # noqa: E402
                             diacritize_messages, wordcard_messages)

HEAD = f"""# Production-промпты Arabica AI Pro

Версия промптов: **{PROMPT_VERSION}**. Файл сгенерирован `tools/gen_prompt_docs.py` из `arabica/prompts.py` —
править нужно код, а не этот документ.

## Общие правила вызова модели

| Параметр | Значение | Зачем |
|---|---|---|
| Шаблон чата | встроенный Jinja-шаблон GGUF (`llama-server --jinja`) | корректный формат Qwen |
| Рассуждения | `chat_template_kwargs = {{"enable_thinking": false}}` | официальный способ; `/no_think` не используется |
| Температура | 0 (`top_k=1`) | воспроизводимость, без «творчества» в фактах |
| Роли | system = правила; user = **только исходный текст** | текст — данные, не команды (защита от инъекций) |
| Порядок системного промпта | постоянные правила → (глоссарий) → (контекст) → (фон пользователя) | постоянный префикс кэшируется движком |
| Вывод | только перевод; `<think>` → отказ показа; пустой ответ → ошибка | §2 контракта |
| Словарная карточка | `response_format = json_schema` (грамматика llama.cpp) | гарантированно валидный JSON |

Модель видит **только** то, что ниже; никакие сетевые ресурсы не вызываются.
"""


def block(title: str, msgs: list[dict]) -> str:
    out = [f"### {title}", ""]
    for m in msgs:
        out += [f"**{m['role']}:**", "", "```text", m["content"], "```", ""]
    return "\n".join(out)


def main() -> None:
    parts = [HEAD]
    sample = {"ru-ar": "Министр иностранных дел заявил, что переговоры не будут возобновлены до 15 марта 2027 года.",
              "ar-ru": "لم يحضر الوفد الاجتماع بسبب الظروف الجوية."}
    names = {"accurate": "Точный", "literary": "Литературный", "professional": "Профессиональный"}
    for d, label in (("ru-ar", "RU → MSA"), ("ar-ru", "MSA → RU")):
        parts.append(f"## {label}\n")
        for mode in MODES:
            parts.append(block(f"{label} · {names[mode]}", build_messages(sample[d], d, mode)))
        parts.append(block(f"{label} · короткий ввод (слово)", build_messages("Книга" if d == "ru-ar" else "كتاب",
                                                                               d, "accurate", short=True)))
    gl = [{"source": "переговоры", "target": "مفاوضات", "note": "дипл."},
          {"source": "прекращение огня", "target": "وقف إطلاق النار"}]
    ctx = [("Стороны встретились в Каире.", "التقى الطرفان في القاهرة.")]
    parts.append("## С глоссарием и документным контекстом\n")
    parts.append(block("RU → MSA · Профессиональный · глоссарий + контекст",
                       build_messages("Переговоры о прекращении огня продолжатся завтра.", "ru-ar", "professional",
                                      gl, ctx, user_note="Текст о переговорах по Судану.")))
    parts.append("## Словарная карточка\n")
    parts.append(block("MSA → RU · карточка слова", wordcard_messages("علم", "ar-ru")))
    parts.append(block("RU → MSA · карточка слова (с контекстом)",
                       wordcard_messages("ключ", "ru-ar", "Он нашёл ключ на дне ущелья.")))
    parts.append("JSON-схема ответа (передаётся как `response_format`):\n\n```json\n"
                 + json.dumps(WORDCARD_SCHEMA, ensure_ascii=False, indent=2) + "\n```\n")
    parts.append("## Огласовка (по запросу)\n")
    parts.append(block("Огласовка MSA", diacritize_messages("كتب الطالب الدرس")))
    parts.append("После ответа программа удаляет харакаты и сравнивает буквенный скелет с исходным; "
                 "при несовпадении результат отклоняется. Транскрипция строится детерминированно "
                 "(`arabica/translit.py`), модель её не пишет.\n")
    (ROOT / "docs" / "MODEL_PROMPTS.md").write_text("\n".join(parts), encoding="utf-8")


if __name__ == "__main__":
    main()
