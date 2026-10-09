# Локальный benchmark psptm_minutes

На сервере с уже существующим образом psptm-minutes:local:

```
python3 benchmarks/control.py freeze
nohup python3 benchmarks/control.py gate > benchmarks/gate.log 2>&1 &
docker compose -p psptm-benchmark -f benchmarks/compose.benchmark.yaml up -d
```

Перед freeze скопировать protocol.md/research.md в benchmarks/. Freeze снимает
read-only копию пяти транскриптов и assets. Private имеет mode 700 и не
синхронизируется с Codex. Контейнер не получает production secrets, DB connection
или Docker socket. Gate на хосте имеет read-only SQL доступ через docker exec.
При отсутствии gate или занятых очередях генерации ждут. Ранее отправленный
Ollama-запрос заканчивается самостоятельно; кооперативный приоритет не является
жёстким вытеснением. Не запускать два runner одновременно.

Файлы для безопасного статуса: progress.json, gate.json, manifest.json,
results.jsonl, call-metrics.jsonl, judgments.jsonl. Реальные тексты, model responses
и evidence доступны владельцу только на сервере в private/. Не использовать
docker logs для чтения материалов; runner пишет только технические статусы.

Resume использует private/results, extractions и validated calls. Изменение
frozen assets вызывает остановку. Не менять manifest задним числом. Для нового
протокола нужен отдельный run каталог и сохранение предыдущего manifest.

Полная генерация автоматически переходит к независимым локальным судьям.
Финалистский tuned-track, повтор seeds и позиционный аудит ещё требуют отдельного
запуска после выбора по измерениям. Статус evaluation_finished_finalist_audit_pending
не означает завершение миссии. Порог критических ошибок и позиции судей требуют
локального человеческого аудита спорных примеров. Оценки судей не human ground truth.

Проверки без GPU: python3 -m unittest discover -s benchmarks/tests -v.
