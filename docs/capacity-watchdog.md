# Серверная защита capacity-прогона

`python3 scripts/ops/capacity_watchdog.py` — host-side инструмент на Python standard library. Он не собирает образы, не развёртывает приложение, не меняет модели/Ollama, не подключается к production на запись. Подготовка выполняется отдельно; разрешённые часы — [capacity-schedule.md](capacity-schedule.md).

## Контракт подготовки

1. Выбрать точный commit с успешным CI. Создать отдельный защищённый каталог исходников через `git -c tar.umask=0022 archive`; включить Dockerfile, .dockerignore, **requirements.lock и requirements-gpu.txt**, app/config, capacity.py/Compose, scripts/ops и tests/db-password. Не переносить рабочее дерево, .env, secrets или лабораторные материалы. Распаковывать через `tar --no-same-owner --same-permissions -xf archive.tar -C source` в parent 0700: обычная распаковка под umask 077 может сделать код нечитаемым для UID 10001 после COPY. Внутренние файлы/каталоги кода должны читаться этим UID, group/world write убрать; права corpus/весов не менять. Старый candidate-2592105 не включает requirements-gpu.txt и не является полным GPU build context; использовать новый полный архив.
2. На Bender собрать отдельный GPU-образ из этого контекста с OCI revision выбранного commit. Не использовать production :local. Сохранить immutable image ID, SHA256 исходников и model digest. Проверить pip check и CPU imports под UID 10001 в read-only контейнере runtime=runc, network=none, без GPU; отдельно проверить чтение копий corpus/весов. Существующие STT-веса и разрешённую модель не скачивать и не менять. Сборка не должна запускать inference; тяжёлую подготовку предпочтительно выполнять в разрешённое окно.
3. Новый run_id: 16 случайных hex-символов. Compose project строго `psptm-minutes-capacity-{run_id}`, новый для каждой попытки. Все paths/env — приватные параметры benchmark, без production credentials. Из отдельного compose.capacity.yaml создать **не запуская** два контейнера (`docker compose ... create --no-build`). В приватном override для measure указать конкретный image ID, pull_policy=never и список разрешённых файлов /corpus; обоим сервисам добавить label `org.psptm.capacity.run={run_id}`. Служебные labels Compose должны соответствовать project и service measure/capacity-db. Три копии corpus уже разрешены владельцем, оригиналы не менять.
4. Сохранить полные 64-hex container ID через ограниченный inspect `.Id`; оба контейнера должны иметь state=created. Не использовать mutable имя вместо ID и не переиспользовать завершённый project. Перед повторной попыткой сохранить результаты старой, не удалять её по приблизительному имени.
5. Создать каталог попытки 0700 и job.json 0600 с шестью полями:

```json
{
  "run_id": "0123456789abcdef",
  "project": "psptm-minutes-capacity-0123456789abcdef",
  "measure": "<полный container ID, 64 hex>",
  "database": "<полный container ID, 64 hex>",
  "lab_lock": "/home/epopov/projects/psptm_minutes/benchmarks/qwen_lab/lab.lock",
  "model_digest": "6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7"
}
```

Это пример структуры, не готовый job. Скрипт отвергает неверные поля, project, ID, permissions и отсутствие общего lock.

## Запуск и остановка

```sh
python3 scripts/ops/capacity_watchdog.py check /absolute/private/run/job.json
python3 scripts/ops/capacity_watchdog.py start /absolute/private/run/job.json
```

`check` только читает состояние. `start` разрешён в окне и создаёт user-systemd unit `minutes-capacity-{run_id}`. Скрипт и job должны иметь абсолютные пути без пробелов/спецсимволов; user systemd/Docker уже должны быть доступны оператору. Не устанавливать runner/daemon автоматически и не запускать на production delivery host по аналогии с этим инструментом.

Unit содержит независимые от SSH/чата RuntimeMaxSec и ExecStopPost cleanup; максимальное время одной попытки — два часа. В будни cutoff **08:45**, в конце выходных — **23:45 воскресенья**, оставляя 15 минут на отмену/проверку. Новая попытка требует ещё ≥10 минут до cutoff: в будни после 08:35 не стартует. В субботу допускается переход в воскресенье. Часы читаются как Europe/Moscow, системные часы не подменяются.

Контроллер берёт общий lab.lock, проверяет свободные production-очереди, запускает только свою БД, ждёт health, повторяет gate и запускает свой measure. Необходимы healthy API/агент с верными Compose labels. Production probe выполняет READ ONLY SELECT с statement_timeout=2s: processing в любой из трёх очередей, queued STT и реально runnable queued analysis блокируют нагрузку. Исторические queued analysis без транскрипта не являются runnable; terminal partial_saved не блокирует capacity, upload_uncertain и нетерминальные/неизвестные состояния блокируют. Для deploy действует отдельный более строгий agent_idle gate.

Первый GPU gate требует одного GPU, free VRAM≥20000 MiB, отсутствия compute-процессов и загруженных моделей Ollama. При utilization≤5% применяется обычный допуск. По разрешению владельца 23 октября допускается графический фон >5% и ≤20%, только если два снимка NVIDIA pmon содержат исключительно тип G на GPU 0 и хотя бы один графический PID; затем повторно проверяются отсутствие compute PID и моделей Ollama. C/C+G/неизвестный тип, пустая или неоднозначная телеметрия и загрузка >20% блокируют запуск. Имена процессов не используются как основание доверия. Во время работы каждые три секунды плюс время probes проверяются production, загруженный model digest и принадлежность compute PID своему measure/Ollama. Новая рабочая задача, чужая модель/PID, ошибка диагностики или deadline прекращают попытку. Никаких kill чужих PID, принудительной выгрузки модели или перезапуска Ollama.

Остановка каждый раз сверяет immutable ID, run label, Compose project и service. Сначала завершается свой measure и проверяется остановка; затем в закрытом каталоге сохраняются measurement.log и database.dump; только потом останавливается своя tmpfs-БД. Ошибка копирования не оставляет БД работать и отмечается отказом. ExecStopPost повторяет безопасный cleanup после смерти контроллера; исходники контроллера и job нельзя менять/удалять, пока unit существует. Контейнеры/volume не удаляются, чтобы оператор мог исследовать результат. Удаление собственной завершённой попытки — отдельно после сохранения данных.

## Наблюдение и ограничения

Проверять реальный systemd/container handle, а не один timestamp watchdog.json. Result=timeout/signal, status failed или не подтверждённый cleanup не являются успешным замером. Не запускать дубль только из-за обрыва SSH или тайм-аута наблюдения. После отмены проверить прекращение своей STT/inference-нагрузки, сохранив общую Ollama неизменной. Загруженная модель /api/ps сама по себе не означает активный inference.

Это кооперативный приоритет на общем GPU, не центральный admission control для всех проектов. Запрос другого клиента к той же модели Ollama нельзя надёжно отличить по /api/ps от собственного; между probes есть задержка. При SIGKILL lock контроллера освобождается до завершения ExecStopPost. Состояние GPU и shared lock должны учитывать все участники; ресурсную изоляцию даёт отдельный GPU/хост. Отмена настоящего Ollama-запроса и прекращение CUDA проверяются в разрешённое GPU-окно до полного corpus по [протоколу](ollama-cancellation-acceptance.md). При неподтверждённой отмене не продолжать матрицу и не объявлять ресурс освобождённым.

В Git/CI публикуются только агрегаты, коды и commit/digest. **measurement.log и database.dump могут содержать реальные материалы**: не читать их в чат, не прикладывать к CI/PR. Хранить защищённо на Bender для человеческой проверки по исходнику. Успех процесса не доказывает качество или суточную квоту.

## Проверки 21 октября

- Полный изолированный Docker/PostgreSQL-набор: **225 passed**, 10.53 s, одно предупреждение Starlette testclient. Новые проверки: окна/резерв, immutable ownership, запрет production project, закрытое окно, busy lock, уступка новым задачам, read-only runnable query, ошибка сохранения при cleanup.
- `capacity_inspect_probe.py` прошёл на настоящем Docker: created/exited контейнер без healthcheck и отказ чужому project; добавлен в CI/release. Запускает только Python pass, без сети/GPU/mounts.
- На Bender выполнены CPU-only systemd drills: RuntimeMaxSec и SIGKILL главного процесса. В обоих случаях два собственных контейнера stopped, контрольный running, настоящий pg_dump временной PostgreSQL прочитан pg_restore --list. Все синтетические контейнеры удалены. Приватные отчёты: watchdog-pg-timeout-20261021-01/probe.json и watchdog-pg-sigkill-20261021-01/probe.json внутри benchmarks/private/capacity.
- Read-only production gate проверен на живом API/агенте: idle на момент проверки. Inference не запускался.
- SHA256 проверенного capacity_watchdog.py: `ead37d065ed6981c1cba69e9cd2bf44af3a429245bcb6933e8d9ec6fd0a4f101`; probe: `b229bc95983a2272c29e1bc2368906d87fa223b550f71894484a51ebc2834c6a`.

Настоящий 85-минутный прогон, отмена GPU/LLM, человеческое качество, полная матрица и staging остаются незавершёнными.

[GitHub CI `ecd3d10`](https://github.com/b0ver/psptm_minutes/actions/runs/39591200763) success на Linux/amd64, включая новый Docker metadata smoke и остальные gates. На Bender подготовлен полный source context этого commit; дальнейшая приёмка в разрешённое окно.

Образ и контейнеры для ecd3d10 подготовлены: [фактический отчёт](benchmarks/capacity-preparation-2026-10-21.md). Реальный запуск и отмена GPU остаются отдельной приёмкой.

23 октября: на Bender три снимка pmon показали только G-процессы Xorg/GNOME Shell/Happ/Firefox, compute PID и модели Ollama отсутствовали. Прежний gate ошибочно считал графический фон 8–11% вычислительной занятостью. Добавлено ограниченное исключение с регрессиями; фактическая приёмка отмены и corpus ещё не выполнены. Старый helper в mounts cancel-client не меняется; новая host-копия guard имеет отдельный hash.

На Bender контейнер ollama содержит socat, перенаправляющий запросы в native ollama.service. При runtime gate разрешаются также PID только из активной systemd cgroup /system.slice/ollama.service и её дочерних cgroups. Пустая/неизвестная принадлежность не разрешает чужие PID. Проверка отмены всё ещё требует настоящих streaming/GPU наблюдений.
