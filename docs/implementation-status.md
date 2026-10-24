# Состояние проекта: обновлено 21 октября 2026

23 октября SSH к Bender восстановился. На отдельном CPU-стенде **прошли** host loopback, synthetic WAV upload, persistence после restart API, восстановление аудиоархива и pg_dump в отдельную тестовую БД. Probe перед передачей/исполнением сверен по SHA256; [receipt](reports/bender-staging-smoke-2026-10-23.json). Использован уже подготовленный runtime `366d0ea`, образ не пересобирался. Это не version rollback, full release promotion или настоящий Telegram/calendar E2E.

Cancel/corpus jobs перепроверены: created, systemd units inactive; image IDs и hash клиента/guard совпадают с manifests. Два read-only watchdog check вернули `gpu_busy` (первое наблюдение: utilization 9%, free VRAM 23311 MiB). Запусков inference/STT не было; чужие процессы/Ollama и production не менялись. Общий lab.lock был доступен в момент проверки, что не отменяет GPU gate. Для capacity по-прежнему требуется доступный ресурс в разрешённое окно, принятая отмена и затем corpus.


[CI текущего кода `c5ab1b7`](https://github.com/b0ver/psptm_minutes/actions/runs/39647601435) **success**: 230 integration и все gates, включая новый staging HTTP upload/restart/restore, прошли на Linux/amd64. Merge checkout имеет идентичное head дерево (files=[]); [receipt](reports/staging-ci-2026-10-21.json). Серверная проверка Bender, полный version rollback, настоящий E2E/GPU и качество остаются незакрыты.

Добавлен воспроизводимый [CPU staging drill](reports/staging-smoke-2026-10-21.md) в CI/release: реальный HTTP upload synthetic WAV, повторный импорт, restart persistence, архив аудио и pg_dump/restore отдельной БД. Прямой probe локально на Docker/arm64 прошёл дважды; существующие ресурсы и чужой secret root корректно отклонены. На Bender Tailscale ping доступен, SSH закрывается до входа — серверная приёмка по-прежнему pending. Приложение и production не обновлялись.

[CI проверки отмены `4f08596`](https://github.com/b0ver/psptm_minutes/actions/runs/39596349525) **success**; готов отдельный cancel-job на Bender (created, не started), проверены безопасные mounts и запись статуса под UID 10001 без GPU. [Manifest/протокол](ollama-cancellation-acceptance.md). Владелец сообщил, что отдельного хоста и второго reviewer пока нет: используем существующий Bender и ручную приёмку владельцем; production workflow выключен явным opt-in gate.

Подготовлен [клиент проверки отмены Ollama](ollama-cancellation-acceptance.md), который хранит только счётчики и не заявляет серверную отмену по выходу клиента. Полный локальный Docker-набор — **230 passed**, включая реальный offline TCP/SIGTERM. GPU-отмена и corpus по-прежнему не выполнены.

Текущий кандидат: [PR #3](https://github.com/b0ver/psptm_minutes/pull/3), код ecd3d10, полный набор 225 passed и CI success. На Bender [подготовлен отдельный GPU-образ](benchmarks/capacity-preparation-2026-10-21.md), проверены pip/CPU imports и чтение копий записей/весов под UID 10001. Права упаковки, из-за которых первый образ не читал код, исправлены; первый image ID отклонён. Два benchmark-контейнера созданы, но не запущены; start вне окна проверенно отказал. Production не обновлялся.

Остаток: реальная отмена GPU/Ollama и полный цикл 85/60/30 минут в разрешённое окно, человеческое качество, внешние Telegram/calendar E2E, production restore, staging/rollback и настройки доставки. Суточная мощность не подтверждена. Далее — история промежуточных сверок; их ранние состояния не являются текущими.

На Bender создан [CPU staging smoke](reports/staging-smoke-2026-10-21.md): отдельный project/данные/новые секреты, runtime image ID точного commit `366d0ea`, DB/init/API подняты. После исправления сетевой конфигурации Compose up --wait завершился успешно. Внешняя проверка loopback, synthetic upload/restart/restore ещё не завершена: SSH затем дважды закрыл соединение. Реальный version rollback не выполнен.

## История проверок

[CI `ecd3d10`](https://github.com/b0ver/psptm_minutes/actions/runs/39591200763) **success** на Linux/amd64: integration, новый реальный Docker metadata smoke, restore, recorder probes и Python audits. Полный GPU build context этого commit подготовлен отдельно на Bender с проверенным SHA256; образ/inference ещё не запускались. Последующие правки этого статуса — документация.

Реализован [серверный watchdog capacity](capacity-watchdog.md): окна MSK с резервом, immutable ownership, приоритет production, systemd timeout/ExecStopPost, приватное сохранение dump перед остановкой tmpfs-БД. На Bender timeout и SIGKILL drills прошли без GPU; полный локальный Docker-набор — **225 passed**. Настоящий GPU/corpus/качество ещё не проверены. Старый source archive неполон для GPU build и будет заменён.

[CI `2592105`](https://github.com/b0ver/psptm_minutes/actions/runs/39587245636) **success** на Linux/amd64: полный набор с queue guard, audits, restore и recorder probes. Изолированные исходники кандидата перенесены на Bender с проверкой SHA256; GPU-образ и watchdog ещё не подготовлены, прогон не запущен.

Владелец разрешил GPU-проверки в будни 07:00–09:00 MSK и выходные при свободных ресурсах; созданы [расписания продолжения чата](capacity-schedule.md). Реальный прогон новой версии пока не выполнен: сначала нужен watchdog и изолированный checkout кандидата.

Дополнительная сверка доставки: GitHub environments и runners отсутствуют, main не защищена classic rules или rulesets. [Матрица незакрытой приёмки](reports/release-acceptance-2026-10-21.md) фиксирует доказательства и необходимые внешние шаги. Полный локальный Docker/PostgreSQL-набор — **201 passed** (9.37 s), включая пять проверок барьера остановки очередей на настоящем PostgreSQL; они не заменяют staging deploy/rollback.

Локально реализован [ограниченный merge/render длинных встреч](long-meeting-analysis-2026-10-21.md): факты сохраняются отдельным реестром, сокращается только обзор, отчёты делятся без обрезания пунктов. Конфликтующие статусы между частями требуют проверки человеком; предупреждение включено в документ. Новые регрессии синтетические; реальный повтор 85-минутного примера на GPU ещё требуется. Bender не обновлялся. Полный Docker/PostgreSQL-набор — **196 passed**, 6.73 s, одно предупреждение Starlette testclient; добавлено 16 регрессий длинных отчётов. PR #2 объединён в main (`cd2e047`); исправление длинных встреч подготовлено отдельной веткой `codex/bounded-long-meetings-20261021`. [PR #3](https://github.com/b0ver/psptm_minutes/pull/3): [CI для `90a0ba4`](https://github.com/b0ver/psptm_minutes/actions/runs/39584967068) success на Linux/amd64, включая весь integration-набор, restore, recorder probes и Python audit. Датированные записи далее сохраняют историю этапов.

После восстановления Tailscale 21 октября выполнена [read-only сверка Bender](reports/bender-followup-2026-10-21.md): сервисы доступны, активных записей нет; [capacity-run завершился с ошибкой](benchmarks/capacity-run-2026-10-14.md) на 85-минутной записи. Два меньших примера дали 5/5 отчётов за 6:17 и 11:40. Найдено ретроспективное подтверждение 10 автоматических календарных цепочек с готовыми отчётами, включая пять complete=true. Новые зависимости из PR на Bender ещё не развёрнуты; качество и суточная мощность не подтверждены.

21 октября начат [спринт релизного кандидата](sprints/2026-10-21-alpha-rc.md) в ветке `codex/alpha-readiness-sprint-20261021` и включён pursuit goal. Продуктовые изменения сохранены, PR #1 объединён с календарными deadlines. На финальных зависимостях — **180 passed**; 6 benchmark contracts, actionlint, 4 Chromium recorder controls и checkpoint normal/SIGKILL прошли. Synthetic dump/restore БД, записи и ключа повторён с проверкой под UID 10001. Зависимости runtime и агента обновлены: финальный pip-audit обоих lockfile — 0 advisory. Внешняя E2E/production restore и результат capacity-run остаются непроверенными до доступа к Bender; production delivery не активировалась.

Открыт draft [PR #2](https://github.com/b0ver/psptm_minutes/pull/2). [Первый GitHub CI](https://github.com/b0ver/psptm_minutes/actions/runs/39575667101) на Linux/amd64 для `408d12e` завершился success, включая tests, Python audit обоих lockfile, recorder probes и synthetic restore. Pursuit goal остаётся незавершённым, текущий статус blocked на внешней приёмке/доступе к Bender.

Актуальная сверка от 21 октября: [follow-up проекта](project-followup-2026-10-21.md). GitHub main содержит merged PR #1, локальная main отстаёт на два коммита, значительные изменения остаются незакоммиченными. Actions запусков нет. SSH к Bender закрывается по обоим известным адресам; текущая работоспособность и результат capacity-run сегодня не подтверждены. Ниже — исторические наблюдения, а не живой статус.

14 октября по команде владельца запущен [первый отдельный замер мощности](benchmarks/capacity-run-2026-10-14.md) на трёх существующих записях и пяти стандартных шаблонах. Перед запуском GPU и рабочие очереди свободны. Прогон выполняется: первая запись прошла STT и основной протокол, выполняется извлечение фактов. В стенде исправлен PYTHONPATH и доступ контейнера к копиям corpus. Результаты полного цикла и суточный лимит ещё не получены. Production-сервисы и GitHub-доставка не обновлялись.

Дополнение от 13 октября: выполнен [аудит готовности к корпоративной альфе](alpha-readiness-2026-10-13.md), подготовлены [CI/CD и runbook](ci-cd.md), [стенд мощности](capacity.md) и правила разработки в `AGENTS.md`. Базовый набор текущего рабочего дерева: 150 passed; после добавления ops-gates — 158 passed. pip-audit выявил 27 advisory в пяти пакетах: выпуск блокируется до исправления/разбора применимости. Production и GitHub-настройки в рамках аудита не изменялись. Суточная мощность не измерена; live-агент обслуживает один звонок одновременно. В рабочей истории есть ошибки неполного ответа модели и превышения контекста.

Проект разрабатывается и запускается в Docker. На Bender работают API/web, PostgreSQL, CUDA STT large-v3, отдельный обработчик разборов, Telegram-бот и гостевой агент Телемоста. Существующая Ollama остаётся на хосте. Записи, транскрипты и AI-обработка хранятся локально; Markdown отправляется через Telegram только по запросу владельца.

## Реализовано

- Загрузка аудио/видео через web с прогрессом и без фиксированного лимита размера файла; доступ к web через SSH-туннель.
- Транскрибация на GPU и 16 типов разборов из Telemost Meeting Kit, добавление разборов к готовым транскриптам.
- Черновики поручений с доказательной цитатой, проверка человеком, внутренний реестр и Telegram-доставка утверждённых карточек.
- Telegram: открытый вход, изоляция материалов по владельцам, меню встреч и поручений, запись Телемоста, скачивание Markdown по разделам.
- Статистика в настроенной группе: количество запросов, встреч, разборов, отчётов и поручений без раскрытия содержания материалов.
- Видимый ассистент Телемоста, штатная локальная запись, аварийное сохранение аудиофрагментов и импорт без дублей.
- Выключение камеры с проверкой UI, название из Телемоста, наблюдение имён и ролей участников. Привязка реплик по активной плитке предварительная; должности не угадываются.

- Подписка на Яндекс Календарь: CalDAV, повторы, переносы, отмены, автоматический вход, пауза и пропуск экземпляра. На Bender запущен calendar-scheduler; подключены «Мои события» владельца и прочитаны 22 события со ссылками Телемоста. Автоматический вход ожидает однократного разрешения в настройках подписки. Автоматические записи доступны в личном веб-архиве. [Подключение и проверка](yandex-calendar-2026-10-12.md).

## Проверки и ограничения

Последний набор тестов приложения: 147 passed. Также проверены checkpoint на синтетическом звуке и после SIGKILL, реактивное выключение камеры и реальный гостевой экран. Полная проверка новых наблюдений названия/спикеров на реальном звонке ещё требуется. Полноценной акустической диаризации нет.

Обновление агента прервало активную запись. Сохранённый фрагмент восстановлен, транскрипт и пять разборов готовы; фрагмент помечен неполным. Ассистент вновь допущен и продолжил запись отдельной сессией. Потерянный промежуток не восстановлен. Перед следующим развёртыванием проверку отсутствия активных сессий необходимо связать с запуском последовательной командой, останавливающейся при ошибке. Подробности: [журнал обновления](telemost-camera-speakers-title-2026-10-09.md).

Бенчмарк языковых моделей ведётся отдельно. Файлы в docs/benchmarks — датированные снимки и методика, а не живой статус и не окончательный выбор победителя. Приватные транскрипты и ответы моделей не публикуются.

Секреты, пользовательские записи, runtime-данные, веса моделей и резервные копии исключены из Git. Документы за 8 октября сохраняются как история этапов; их ранние ограничения не описывают текущее развёртывание.

[Инструкция](../README.md) · [ADR](adr/0001-docker-host-ollama-local-processing.md).

23 октября: по запросу владельца watchdog допускает подтверждённый NVIDIA pmon графический фон до 20% при свободной VRAM, отсутствии compute PID/моделей и повторной проверке гонки. Неизвестная телеметрия блокирует старт; имена процессов не whitelist. Полный локальный Docker-набор 258 passed; CI точного commit проверяется отдельно. Реальная GPU-отмена и corpus пока pending.

23 октября: graphics gate прошёл CI f5693eb. Первая реальная cancel-попытка остановлена watchdog с foreign_gpu_process на стадии requesting (0 chunks), client_cancelled, measure/db exited. Причина: контейнер ollama оказался socat-прокси, реальная Ollama — native systemd service. Runtime membership дополнен только PID из проверенной /system.slice/ollama.service cgroup, включая дочерние cgroups; чужие compute PID по-прежнему блокируются. Новый локальный Docker-набор 262 passed; реальная отмена остаётся pending.

23 октября: [диагностика графического фона и watchdog](reports/graphics-watchdog-2026-10-23.json) завершены. Три pmon-снимка только G, затем найден socat-proxy вместо Ollama в контейнере. Новый host guard b1723a7 допускает ограниченный G-фон и учитывает native cgroup Ollama; локально 262 passed, CI success. Первая cancel-попытка 0 chunks остановлена и НЕ принята; следующая создана отдельно. Старт сейчас отложен из-за чужой загруженной модели. Расписания capacity обновлены, production не менялся; реальная отмена/corpus pending.

- 23 октября 08:33 MSK: Tailscale/SSH восстановлены. Реальный synthetic streaming и остановка своего unit подтверждены; серверная отмена остаётся unaccepted (`post_cancel_process_ownership_ambiguous`). Позже модель освободилась естественно, GPU 0%, free VRAM 23848 MiB. Corpus не запускался; требуется диагностика observer и новая отдельная попытка. [Receipt](reports/graphics-watchdog-2026-10-23.json). Оба расписания обновлены без повторного старта завершённых jobs.

- 24 октября: добавлен воспроизводимый read-only `capacity_cancel_observer.py`: exact PID cgroup/starttime, unknown SM fail-closed, 10 s непрерывного покоя, безопасный однократный receipt с частичными снимками. 290 локальных Docker-тестов passed (12.88 s); точный CI отдельно. SSH требует Tailscale check; инструмент не установлен на Bender и GPU-приёмка не выполнена. [Протокол](ollama-cancellation-acceptance.md).

- Дополнительная проверка observer: пустой/header-only pmon не считается отсутствием compute; добавлены два отказных случая. Полный локальный Docker-набор **292 passed**, 12.13 s; тестовые контейнеры и сеть удалены.

- 24 октября: CI c24ab90 остановлен security gate — три advisory filelock 4.0.12. Pin обновлён до 4.2.1 в requirements.txt/lock без исключений audit. Повтор: 292 Docker-теста passed (12.43 s), pip check success, audit runtime и agent lockfile — 0 findings. Это не обновляет подготовленные образы Bender; их прежний security/capacity scope зафиксирован в docs/ci-cd.md. CI нового commit проверяется отдельно.

- 24 октября: повторная сверка точного CI 3fa09d4 — success, **265 integration passed**, 19.61 s на чистом checkout, оба audit и synthetic staging/restore/recorder gates passed. Локальные 292 включают дополнительные untracked-тесты и не подменяют count CI. [Текущая матрица](reports/release-acceptance-2026-10-21.md), [receipt](reports/alpha-rc-status-2026-10-24.json). SSH требует Tailscale check, PR #3 draft, release build ограничен main, environments/runners отсутствуют. Внешняя приёмка и manifest актуального runtime не выполнены.

- 24 октября вечером: SSH доступен. Observer 3fa09d4 установлен в отдельный immutable каталог с проверкой SHA256 и CPU probes; одна новая cancel-observer-3fa09d4-01 создана без запуска, unit minutes-capacity-64af8b08b363474b. Проверены UID storage и прежняя image policy/client imports. Gate gpu_busy из-за стороннего compute Python (394 MiB), хотя global GPU 0% и production idle; моделей не запускали. [Receipt](reports/alpha-rc-status-2026-10-24.json). Не готовить дубль; старый образ остаётся только диагностическим.

- 24 октября: CUDA PID сопоставлен по cgroup с **рабочим psptm-minutes-worker-1**, не лабораторией; 394 MiB удерживаются при idle-очередях. Consumer не останавливался; текущий watchdog не обеспечивает его паузу/восстановление, это следующий отдельный ops-шаг с CPU-проверкой. Чистый контекст security-кандидата 3fa09d4 (72 tracked-файла, archive/file hashes сверены) подготовлен на Bender без build/GPU; [receipt](reports/alpha-rc-status-2026-10-24.json).

- 24 октября: добавлена CPU-only probe паузы/восстановления synthetic consumer с pre-stop journal и systemd ExecStopPost для timeout/SIGKILL. Production project явно запрещён. Локально 301 Docker-тест passed (13.20 s); серверные drills и полный production lease с queue/GPU admission ещё pending. [Контракт](capacity-worker-pause.md).

- 24 октября: [CI 993472b](https://github.com/b0ver/psptm_minutes/actions/runs/40110730282) success, **274 passed**, 21.29 s на чистом checkout (локально301). На Bender реальные CPU-only timeout/SIGKILL drills восстановили synthetic worker через journal/ExecStopPost; control не остановлен, собственные контейнеры удалены, production worker running с прежним GPU PID. [Receipt](reports/worker-pause-probe-2026-10-24.json). Полный maintenance lease/queue admission/cancel integration ещё не готов; GPU/model calls не выполнялись.
