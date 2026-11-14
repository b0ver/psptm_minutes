# Стенд на имеющемся Bender — 21 октября 2026

Владелец подтвердил отсутствие дополнительного хоста и второго reviewer: работать на имеющемся. Подготовлен CPU-only Compose [compose.staging-smoke.yaml](../../compose.staging-smoke.yaml), project `psptm-minutes-staging-smoke`, каталог `/home/epopov/projects/psptm_minutes-staging`, порт `127.0.0.1:18191`. Он намеренно отличается от project полного release staging, чтобы не мешать first-deploy gate в deploy.sh.

Собственный PostgreSQL, recordings volume и новые случайные DB password/calendar_key. Production secrets, записи, БД и модели не подключались. Закрытые каталоги 0700; два файла секрета 0444 внутри закрытого каталога для чтения Docker secrets под UID 10001. Не копировать эти тестовые credentials в production. Telegram, календарь, recorder и AI workers отсутствуют; GPU devices не запрашиваются. Каждый сервис ограничен 1 CPU/512 MiB. БД в internal network; API также имеет frontend bridge для loopback публикации. Это не полная блокировка исходящей сети API.

Runtime собран на Bender из tracked source commit `366d0eabcb61fcc0a0908f26afab364ef6959c7a`, source tar SHA256 `2380bb3d60e47b8a76f28ee22de856fa38a07ec838c303ae68ec92e02da3133d` проверен после передачи. Local image ID `sha256:a5c6c8d5f8513b8d8cf55cd2ecc08bb012f7d6e5cae0a35caae111e8c384e958`, OCI revision проверен. Это один локальный образ, не manifest трёх registry digest и не production release. Права исходников нормализованы; приватные данные не переупаковывались.

## Выполнено и остаток

- Сборка runtime завершилась exit 0; Compose config --quiet и up --no-build --wait api завершились успешно. DB/init/API созданы отдельно.
- Первый health внутри API прошёл, но запрос с хоста выявил отсутствие port forwarding для единственной internal network на этом Docker. Добавлен отдельный frontend bridge только API; повторный up --wait успешен.
- Повторная проверка host loopback и сценарий synthetic upload → restart → проверка WAV → pg_dump/restore ещё **не выполнены**: SSH закрыл соединение до начала сценария, повторная попытка также закрыта. Не считать их passed по health.
- Полный staging deploy с тремя digest, настоящий candidate→previous rollback, AI/GPU, Telegram/calendar E2E и рабочий restore остаются открыты. Рабочее развёртывание не менялось.

Продолжить после восстановления SSH: проверить health через host loopback, что DB/API используют только собственные volumes и loopback binding, загрузить короткий синтетический WAV, сверить запись БД и hash файла до/после restart API, выполнить pg_dump/restore только собственной БД в новую тестовую БД. Сохранять только агрегаты. Не подключать production credentials и не запускать AI workers в рабочее время.

Команды управления стендом на Bender:

```sh
docker compose --env-file /home/epopov/projects/psptm_minutes-staging/staging.env \
  -p psptm-minutes-staging-smoke \
  -f /home/epopov/projects/psptm_minutes-staging/compose.staging-smoke.yaml ps
```

В `staging.env` записан точный image ID и путь к тестовым secrets. Не выводить этот файл или развёрнутый compose config в logs. Не запускать стенд с production project name. Остановка — `stop` с теми же аргументами Compose, без удаления volumes. Веб-доступ только через SSH-туннель на loopback, не публиковать порт наружу.

## Повторная проверка и воспроизводимый drill

Вечером 21 октября Tailscale показывал Bender Online, прямой ping прошёл, но SSH по Tailscale IP и сохранённому alias закрывался до входа; LAN SSH недоступен. Это не доказательство остановки стенда: удалённые контейнеры повторно не осмотрены и не перезапускались.

Добавлен `scripts/ops/staging_smoke_probe.py`. Он проверяет project/service labels, отсутствие GPU, собственные volumes/сети и read-only secrets из указанного staging-каталога, фактическую loopback-публикацию. После этого выполняет идемпотентный импорт фиксированного synthetic WAV, сравнение SHA256, restart только проверенного API container ID, повторную сверку записи/файла и восстановление синтетического аудиоархива. Настоящий pg_dump восстанавливается в новую БД только своего PostgreSQL, проверяется синтетическая строка; временная БД удаляется. В receipt — только технические идентификаторы и boolean-результаты; секреты/HTTP-ответы/данные dump не печатаются.

Локальный Docker/arm64 стенд из runtime source `366d0ea`: host loopback, upload, повторный импорт, persistence после restart, audio archive restore и database dump/restore **прошли**. Повтор тем же probe прошёл без второй встречи; bootstrap отказал при уже существующих ресурсах, неправильный secret root отклонён. Это локальное доказательство; Bender/amd64 ещё требует того же сценария. Version rollback и настоящий E2E остаются pending.

Для повторяемого полностью синтетического CI drill:

```sh
bash scripts/ops/staging-smoke-drill.sh
```

Drill отказывается работать при существующих контейнерах/volumes `psptm-minutes-staging-smoke`, собирает runtime, создаёт новые тестовые secrets, выполняет probe и удаляет только собственные синтетические ресурсы. Не запускать его вместо probe поверх уже поднятого Bender-стенда. Сценарий добавлен в CI и release gates; синтаксис shell/Python и actionlint проверены локально. Результат нового GitHub запуска фиксируется отдельно после завершения.

Для существующего Bender-стенда после восстановления SSH передать проверенный probe и запустить:

```sh
python3 scripts/ops/staging_smoke_probe.py --root /home/epopov/projects/psptm_minutes-staging
```

## Результат Linux/amd64 CI

[CI `c5ab1b7`](https://github.com/b0ver/psptm_minutes/actions/runs/39647601435) **success** на Linux/amd64: **230 passed**, 17.48 s, одно предупреждение; новый настоящий HTTP staging smoke, synthetic restore с calendar credential, recorder controls/crash recovery, metadata smoke и оба Python audits прошли. GitHub checkout использовал merge commit `f85f309c86f57e2622c5569ca485e36345ec5f6d`; GitHub compare с head `c5ab1b7761308b401e4417d49ce441ac0ffac07b` подтвердил files=[]. [Безопасный receipt](staging-ci-2026-10-21.json) привязывает результат к исходникам и image ID. Это CI-стенд, не Bender, не release promotion и не version rollback.

## Приёмка Bender 23 октября

23 октября SSH к Bender восстановился. На отдельном CPU-стенде **прошли** host loopback, synthetic WAV upload, persistence после restart API, восстановление аудиоархива и pg_dump в отдельную тестовую БД. Probe перед передачей/исполнением сверен по SHA256; [receipt](bender-staging-smoke-2026-10-23.json). Использован уже подготовленный runtime `366d0ea`, образ не пересобирался. Это не version rollback, full release promotion или настоящий Telegram/calendar E2E.

Cancel/corpus jobs перепроверены: created, systemd units inactive; image IDs и hash клиента/guard совпадают с manifests. Два read-only watchdog check вернули `gpu_busy` (первое наблюдение: utilization 9%, free VRAM 23311 MiB). Запусков inference/STT не было; чужие процессы/Ollama и production не менялись. Общий lab.lock был доступен в момент проверки, что не отменяет GPU gate. Для capacity по-прежнему требуется доступный ресурс в разрешённое окно, принятая отмена и затем corpus.
