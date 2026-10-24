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

- 24 октября: подготовлен explicit idle-worker admission без остановки production: per-job SHA/container/image/StartedAt binding, 10 s global GPU=0%, free VRAM и повторные production gates, fail-closed при смене/чужом PID/новой очереди. Observer сохраняет unknown SM и отдельно учитывает whole-device zero; положительный SM противоречит quiescence. 321 локальный Docker-тест passed (12.87 s), deployment/реальная отмена pending. [Контракт](capacity-idle-worker.md).

- 24 октября: guard fb66967 прошел CI. Попытка 64af8b08b363474b завершилась с foreign_gpu_process и 2556 фрагментами потока. Оба своих контейнера остановлены. Отмена не принята. Добавлена прямая проверка cgroup неизвестного native PID. Добавлен приватный файл gpu-refusal.json. Локально прошли 325 тестов за 12.04 s. Причина прежнего отказа пока не установлена. Новый GPU-образ 3fa09d4 собран без моделей. Его ID - sha256:97077db98378e4cc6eaa7d80e8ca2a428bef3890190447fc39e82f5924c36d98. Хеши исходников в образе совпали. Версия filelock - 4.2.1.

- 24 октября: рабочий HTTP-режим - stream=false. Добавлен отдельный synthetic nonstream клиент с тремя профилями. Он сохраняет только HTTP-состояние и хеш payload. Локальный HTTP-тест подтвердил закрытие соединения после SIGTERM до ответа. Полный Docker-набор дал 333 passed за 13.44 s. Привязка idle worker теперь содержит только identity. Полный 10 s GPU gate остается перед запуском клиента после health своей CPU-БД. Попытка 735659b5297844ba получила gpu_busy до запуска клиента. Ее job выведен из использования. Новый клиент еще требует CI и GPU-приемки. [Контракт](ollama-nonstream-cancellation.md).

- 24 октября: watchdog теперь постоянно проверяет лабораторную очередь. Он читает только счетчики SQLite в режиме read-only. Queued, running и неизвестные состояния блокируют свою нагрузку. Ошибка диагностики также блокирует ее. Локальный набор дал 337 passed за 13.20 s. SQL-тест подтвердил неизменность файла базы. Новый guard требует CI до модельного прогона.


## Точное время наблюдения GPU - 24 октября

Потоковая проверка на image 97077db прошла совместный протокол отмены. Первая проверка extract-json показала 10.736 секунды покоя. Она не принята: прежняя верхняя оценка задержки составила 5.075517 секунды при пороге 5 секунд. Эта оценка включала последующие проверки SQL и Docker. Прежний receipt остается отрицательным.

Observer теперь сохраняет монотонное время самого измерения GPU. Совместный receipt должен сравнивать его с монотонным временем отправки stop на том же хосте. Порог остается 5 секунд. Интервал покоя остается не менее 10 секунд. Положительный process SM сбрасывает интервал даже при device utilization=0. Проверка отвергает будущую, нечисловую и устаревшую более чем на 3 секунды метку. Время проверки identity не заменяет время наблюдения GPU.

Локальный полный набор Docker/PostgreSQL: 342 passed за 13.63 s. Одно предупреждение Starlette. Тестовые контейнеры и сеть удалены. Новый точный commit еще требует CI и отдельной неизменной установки. Повторить можно только непринятую extract-json в новой изолированной попытке. Два профиля analysis еще не проверены. Полный corpus пока запрещен.


## Bender: точная отмена extract-json

CI 20d6869 прошел: https://github.com/b0ver/psptm_minutes/actions/runs/40120029522. Чистый набор дал 315 passed за 22.02 s. Аудит обоих lockfile не нашел известных уязвимостей. Synthetic staging/restore и recorder checks прошли. Локальный набор с сохраненными лабораторными тестами дал 342 passed.

Копия observer qualification-tools-20d6869 установлена с неверными правами клиента. Попытка extract-json-02 завершилась до модельного вызова: UID 10001 не смог прочитать файл, exit 2. Своя БД завершилась с exit 0. Старые файлы не менялись. Копия qualification-tools-20d6869-r2 содержит те же хеши. Для нее отдельно проверены чтение и compile файла под UID 10001 без сети и моделей.

Новая попытка extract-json-03 принята. До stop наблюдались незавершенный HTTP-ответ и native SM 3/64/89/89 в течение более пяти секунд. Клиент получил SIGTERM и завершился с exit 130. Своя БД завершилась с exit 0. Первое quiet-измерение GPU получено через 1.544792 секунды после stop. Непрерывный quiet-интервал составил 10.809 секунды. Рабочий STT-контейнер не останавливался. Старый отрицательный extract-json-01 receipt сохранен. Качество отчетов, полный corpus и дневная мощность этим не подтверждены. Подробности: docs/reports/cancellation-profiles-2026-10-24.json.


## Capacity 24 октября: переход к полному корпусу

Все четыре профиля отмены приняты на image 97077db/source 3fa09d4. Естественное освобождение модели подтверждено после каждого профиля. Для analysis-json первая quiet-метка: 4.291929 s, quiet-интервал: 10.759 s. Для analysis-text: 4.237954 s и 10.734 s. Порог 5 секунд не менялся. Клиенты завершились с exit 130. БД завершились с exit 0. Производственные сервисы не останавливались.

Исторический ecd3d10 job выведен из подготовки. Его два незапущенных контейнера удалены после проверки ownership. Данные и manifest сохранены. Первая текущая подготовка отказала при дополнительном compute PID. PID исчез до проверки принадлежности. Приемка не ослаблена. Ее клиент не запускался. Вторая попытка завершилась stt_weights_missing для трех входов: путь указывал корень кэша вместо snapshot. Это ошибка конфигурации стенда. Дневная мощность не вычислена. Все отрицательные receipts, дампы и данные сохранены. Удалены только собственные terminal контейнеры и пустые сети. Docker address-pool exhaustion устранен освобождением этих сетей; prune не применялся.

Третья попытка prepared-current-3fa09d4-03 запущена. Unit minutes-capacity-29938a168bd54e6b, отдельный orchestrator minutes-capacity-orchestrator-29938a168bd54e6b. Проверены 72 файла чистых исходников, immutable image/model/tool hashes, три входа и 11 файлов STT-весов. Проверка app.settings.MODEL_PATH под UID 10001 подтвердила обязательные файлы модели. Образ не пересобирался. Веса не менялись. Перед стартом добавлен read-only preflight существующего guard; финальный admission внутри watchdog сохранен.

Фактический старт measure: 2026-10-24T18:47:05.428182649Z. Последний снимок: measure running, DB healthy, одна собственная встреча processing, runtime resource gate passed. Это начало STT первого входа 84.66 минуты. Порядок остальных входов: 58.86 и 29.37 минуты. Результат корпуса еще отсутствует. Не перезапускать живой job. Проверять точные unit/PID/start ticks/container IDs. Полный успех, качество, суточная мощность, human E2E, рабочий restore и staging rollback остаются отдельными условиями.


## Recap: обнаруженный независимый GPU consumer

24 октября в 21:50 МСК watchdog остановил текущий corpus с foreign_gpu_process. Причина подтверждена cgroup: дополнительный compute PID принадлежал recap-stt-1, project recap/service stt. Это отдельный рабочий потребитель GPU. Прогон остановился до завершения первого примера. Полных метрик корпуса нет. Его результат нельзя считать успехом STT или анализа. Все свои контейнеры завершились. Приватные logs/dump/data сохранены. Проект recap не изменялся.

Read-only диагностика установила контракт GET http://127.0.0.1:18080/health: queue_depth, oldest_pending_sec и stt.active_leases. Эти счетчики происходят из очереди jobs. В момент диагностики все три равны нулю. Это снимок, а не гарантия отсутствия следующих задач. Проверка чужих GPU PID остается обязательной.

В capacity_watchdog.py добавлен recap_idle. Он проверяет running/healthy и Compose labels bot/stt, точную loopback-привязку порта, db/ollama health, типы и значения счетчиков. Queue/backoff/active lease дают recap_busy. Ошибка, неизвестная схема или ответ больше 64 KiB дают recap_probe_unavailable. Проверка выполняется до запуска и при каждом production_idle во время inference и post-stop observation. Вызов только GET, timeout5s. Остановка и изменение Recap запрещены этим инструментом. Общий GPU lock не управляет независимыми producers Recap; при новой задаче свой прогон должен уступить.

Локальный полный Docker/PostgreSQL-набор: 364 passed за 12.91 s, одно предупреждение Starlette. Контейнеры и сеть удалены. Тесты проверяют очередь без compute PID, malformed/oversize/недоступный health, identity и loopback, включение gate в admission, остановку только своих контейнеров с сохранением lock до cleanup. Новый точный commit еще требует CI и отдельного immutable каталога. Прежние guard/mounts не менять. Runtime image и модели не менялись.


## Проверенный guard Recap и новая попытка 04

CI 49350a7 прошел: https://github.com/b0ver/psptm_minutes/actions/runs/40122951864. Чистый checkout: 337 passed за 20.58 s. Оба audit без известных уязвимостей. Все остальные gates прошли. На Bender установлена новая неизменная копия qualification-tools-49350a7. Guard SHA256 71229898864ccb71495aa24cb2180ab729e03f3580ae7a92b8ca2447a6bedfee. Observer и оба клиента не менялись. Runtime image, модель Ollama, STT-веса и код приложения не менялись. Ранее принятые transport receipts сохраняют свой точный scope и hashes; новая правка добавляет только внешний priority gate.

Read-only production_idle с новым Recap gate прошел на Bender без вызовов модели. После проверки terminal handles попытки03 сохранены dump/logs/data и удалены только ее terminal контейнеры и пустые собственные сети. Новая попытка prepared-current-3fa09d4-04 использует свежие БД, пароль и данные. UID 10001, app.settings.MODEL_PATH, source72 и все входные hashes проверены заново.

Unit minutes-capacity-ecf31efb88164bba и orchestrator minutes-capacity-orchestrator-ecf31efb88164bba активны. Фактический measure StartedAt 2026-10-24T19:00:03.047211983Z. Снимок 22:00:53 МСК: первая встреча processing, DB healthy, GPU93%, один compute PID собственного measure, runtime resource gate passed. Это подтверждение фактической STT-нагрузки. Это еще не успешный первый пример. Watchdog stop_at=1792875586.132746. Не запускать новый corpus и не повторять завершенную cancel-матрицу. Следующий шаг - получить итог этого unit и безопасные JSON-агрегаты. Общий lock не заставляет Recap откладывать его собственные задачи; наш guard уступает им, но не гарантирует исключительное резервирование GPU.
