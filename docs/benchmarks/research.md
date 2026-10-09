# Исследовательские гипотезы — 8 октября 2026

Это предварительные гипотезы исполнителя, не измеренный рейтинг. Model cards
описывают исходные веса; локальные GGUF, template, quantization и параметры
фиксируются через /api/show в models.json. Без хешей исходных upstream weights
название тега не доказывает происхождение. Для нестандартной qwen3.6:27b parent
указывает MTP-сборку; эквивалентность официальной 27B остаётся непроверенной.
Число моделей и матрицу уточняет manifest серверного freeze.

| Локальный тег | Гипотеза для русского отчёта | Проверяемое ограничение | Первичный источник |
|---|---|---|---|
| qwen3.6:27b | Кандидат на точную свёртку и итоговые статусы | Неустановленная цепочка происхождения MTP-сборки, VRAM | [Qwen](https://huggingface.co/Qwen/Qwen3.6-27B) |
| qwen3.6:latest | MoE может дать качество крупной модели с быстрым decode | Почти 24GB веса, вероятен offload, тег latest нестабилен | [Qwen](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) |
| qwen3.5:4b | Кандидат на быстрый MAP/fallback | Потери фактов и ложное принятие предложения | [Qwen](https://huggingface.co/Qwen/Qwen3.5-4B) |
| qwen3.5:9b | Baseline, многоязычный MAP и render | Production уже показывал чувствительность к лимиту JSON | [Qwen](https://huggingface.co/Qwen/Qwen3.5-9B) |
| qwen3.5:35b | MoE-кандидат на полноту | Память weights + KV при 32768, длинный REDUCE | [Qwen](https://huggingface.co/Qwen/Qwen3.5-35B-A3B) |
| qwen2.5:14b-instruct | Контроль structured JSON и русского | Карта заявляет JSON и русский; старое семейство | [Qwen](https://huggingface.co/Qwen/Qwen2.5-14B-Instruct) |
| qwen2.5:32b | Возможное повышение полноты над 14B | VRAM и latency; улучшение не следует из размера | [Qwen](https://huggingface.co/Qwen/Qwen2.5-32B-Instruct) |
| qwen3:30b | Контроль reasoning/nonreasoning MoE | think=false требует проверки реального ответа | [Qwen](https://huggingface.co/Qwen/Qwen3-30B-A3B) |
| qwen3:30b-nothink | Отдельная сборка может стабильнее отдавать JSON | Разница template и отдельный digest, не дубликат | [Qwen](https://huggingface.co/Qwen/Qwen3-30B-A3B) |
| qwen3:32b | Dense-кандидат на неоднозначные договорённости | Память и общая temperature=0 отличаются от sampling карты | [Qwen](https://huggingface.co/Qwen/Qwen3-32B) |
| qwen3-coder:30b-a3b-q4_K_M | Coding instruction following может помочь схеме | Кодовые результаты не оценивают русские протоколы | [Qwen](https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct) |
| qwen2.5-coder:32b | Контроль специализированного structured output | Проверить русский и жанровую релевантность | [Qwen](https://huggingface.co/Qwen/Qwen2.5-Coder-32B-Instruct) |
| devstral-small-2:24b | Инструкционная coding-модель может следовать шаблону | Карта про SWE; metadata контекста может отличаться от карты | [Mistral](https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512) |
| gemma4:31b | Независимый многоязычный кандидат на REDUCE | Thinking, template и память проверять пилотом | [Google](https://ai.google.dev/gemma/docs/core/model_card_4) |
| gemma2:27b | Независимый контроль качества и судья | Карта ориентирована на английский, context 8192 | [Google](https://huggingface.co/google/gemma-2-27b-it) |
| mistral-small3.1:24b | Основной независимый кандидат на русский | Карта включает русский; latency и JSON локально неизвестны | [Mistral](https://huggingface.co/mistralai/Mistral-Small-3.1-24B-Instruct-2503) |
| mistral-small:24b | Контроль предыдущего поколения универсальной модели | Точное соответствие 2501 проверить metadata/template | [Mistral](https://huggingface.co/mistralai/Mistral-Small-24B-Instruct-2501) |
| mistral:7b | Быстрый независимый fallback | Небольшой размер, tag не фиксирует instruct revision | [Mistral](https://huggingface.co/mistralai/Mistral-7B-Instruct-v0.3) |
| command-r:35b | Grounded generation может помочь evidence | Русский и structured JSON измерить, Q4_0 и KV footprint | [Cohere](https://huggingface.co/CohereLabs/c4ai-command-r-v01) |
| deepseek-coder-v2:16b | MoE-инструкционная контрольная модель | Coding-специализация и Q4_0 | [DeepSeek](https://huggingface.co/deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct) |
| deepseek-r1:14b | Reasoning может помогать конфликтам статусов | Distill Qwen: для исключения self-judging считать семейством Qwen | [DeepSeek](https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-14B) |
| llama3.1:8b | Независимый компактный generalist | Русский не входит в основной поддерживаемый список карты | [Meta](https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct) |
| llama3.2:3b | Проверка минимального fallback | Ограничение русского и полноты маленькой модели | [Meta](https://huggingface.co/meta-llama/Llama-3.2-3B-Instruct) |
| codellama:34b | Контроль старой coding-модели | Контекст 16384, русский и chat template под вопросом | [Meta](https://huggingface.co/codellama/CodeLlama-34b-Instruct-hf) |
| gpt-oss:20b | Независимый MoE с native structured outputs | Нужен Harmony и reasoning low вместо false | [OpenAI](https://huggingface.co/openai/gpt-oss-20b) |
| glm-4.7-flash:latest | Независимый MoE-кандидат на качество/скорость | Reasoning/coding benchmark не доказывает качество русского | [Z.ai](https://huggingface.co/zai-org/GLM-4.7-Flash) |

Проверены основные публичные model cards перечисленных семейств. Два размерных
варианта Qwen (4B и 32B) используют гипотезу семейства; их отдельные страницы
нужно дополнительно сверить перед tuned-track. Возможные различия context в
карте и GGUF не исправляются предположением: пилот фиксирует фактический профиль.
Все coding-модели остаются в полной матрице.

Исследовательский приоритет: Qwen3.6-27B, Gemma4-31B, Mistral Small 3.1,
Qwen2.5-14B и рабочая Qwen3.5-9B. Это выбор кандидатов для внимания при анализе,
не сокращение тестов. Command R и старые модели могут выиграть отдельную стадию;
такой вывод допустим лишь после измерений.
