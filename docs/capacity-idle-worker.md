# Допуск idle CUDA-контекста рабочего STT consumer

24 октября: production worker держит 394 MiB CUDA-контекста при нулевой наблюдаемой загрузке, idle очередях и свободных 23494 MiB VRAM. Само наличие compute PID не доказывает активную обработку. По [NVIDIA](https://docs.nvidia.com/deploy/nvidia-smi/index.html#utilization), GPU utilization измеряет долю времени выполнения ядер за sample period (примерно 1/6–1 s), а занятая память учитывается отдельно.

Принято техническое решение использовать явный допуск идентифицированного idle consumer без его остановки. Это соответствует разрешению на проверки при доступном ресурсе и не создаёт production maintenance window. [Synthetic restore drills](capacity-worker-pause.md) остаются проверкой отдельного возможного maintenance-механизма; для этого режима пауза worker не используется.

## Явная привязка и admission

По умолчанию watchdog по-прежнему отвергает любой compute PID перед стартом. Для конкретного private job после CI нового guard оператор выполняет:

```sh
python3 /ABSOLUTE/immutable-tools/capacity_watchdog.py bind-idle-worker /ABSOLUTE/private/job.json
```

Команда читает Docker identity и production probes, пишет рядом с job **новый** private `idle-worker.json`: SHA256 неизменённого job.json, immutable container ID, image ID и StartedAt рабочего `psptm-minutes-worker-1`. Она не меняет старый job/mounts/images/production. Оба benchmark-контейнера должны быть created; существующую привязку не перезаписывает. После создания сразу проверяет resource gate; при отказе файл не является доказательством допуска. Каждый subsequent check/start заново проверяет identity и ресурсы.

Для допуска с resident compute нужны **10 секунд** последовательных наблюдений: global GPU utilization ровно 0%, free VRAM≥20000 MiB, пустой Ollama /api/ps, все production/agent gates idle, GPU PID принадлежат только привязанному worker. Разрыв между observations >3 s, новый/исчезнувший compute PID, неизвестная телеметрия, смена identity/StartedAt или очередь означают отказ. Нельзя принять десять секунд без наблюдений за непрерывный простой. Бюджет admission 30 s между probes; каждый внешний probe ограничен timeout.

При существующей привязке её identity проверяется и при отсутствии compute PID. Во время benchmark runtime gate по-прежнему проверяет все рабочие состояния/очереди, model digest, принадлежность compute PID своему measure/native Ollama; дополнительно допускает только PID привязанного worker. Новая production работа, чужая нагрузка, смена worker или ошибка probe прекращают свою попытку. Это polling с приоритетом рабочей очереди, **не** exclusive GPU reservation или атомарный центральный admission. Возможен короткий overlap до следующего probe/остановки; настоящий server cancellation обязателен до corpus.

## Независимое доказательство покоя после отмены

Observer проверяет cgroup/starttime каждого compute PID, включая точный Docker cgroup привязанного worker. Unknown pmon SM (`-`) сохраняется как null. При этом десять секунд измеренного **whole-device GPU=0%** являются отдельным основанием quiescence (`whole_device_zero`); null не превращается в numeric SM=0. Положительный process SM противоречит этому основанию и сбрасывает интервал. При device utilization>0 unknown SM не подтверждает покой. Старое основание numeric SM=0 у всех compute PID при graphics utilization≤20% сохраняется; отсутствие compute PID также наблюдается отдельно.

Повторные identity/production checks обрамляют telemetry. Несогласованные PID, новая рабочая очередь/модель или изменение cgroup означают отказ. Это измерение с разрешением драйвера, не доказательство отсутствия любого короткого kernel между sample windows. Для принятия конкретной отмены дополнительно нужны растущие streaming-счётчики/SM>0 до stop своего unit, свежий client_cancelled, непрерывный post-stop интервал, отсутствие конкуренции и затем естественное освобождение модели. Поздний idle после TTL прежней попытки не переоценивается как успешная отмена. `server_cancellation_verified` самого observer остаётся false.

Локально: **321 passed**, 12.87 s, одно предупреждение Starlette, Docker/PostgreSQL cleanup выполнен. Локальные untracked-тесты считаются отдельно от чистого CI. На этом этапе новый режим на Bender не установлен/не запускался; реальная приёмка pending.

## Отказ первой попытки с привязкой

Guard fb66967 прошел CI. Команда bind-idle-worker подтвердила доступность ресурса. Попытка `64af8b08b363474b` завершилась с кодом foreign_gpu_process. Клиент сохранил 2556 фрагментов и done=false. Оба своих контейнера остановлены. Серверная отмена не принята.

Последующий снимок показал только worker PID21405 и native Ollama PID4118562. Причина прежнего отказа по этому снимку не установлена. Нельзя считать его доказательством отсутствия краткой конкурирующей работы.

Новая проверка уточняет принадлежность PID, которого нет в снимке cgroup.procs. Она читает ActiveState, ControlGroup и MainPID сервиса Ollama. Затем она проверяет cgroup и время старта самого GPU PID. Cgroup - группа процессов Linux. Неизвестный PID остается отказом. При отказе guard сохраняет gpu-refusal.json в приватном каталоге job. Этот файл содержит только технические данные PID и cgroup.

Локальный повтор дал 325 passed за 12.04 s. Контейнеры тестов удалены. Новая версия guard еще требует CI и реальной проверки. Старая попытка не перезапускается.
