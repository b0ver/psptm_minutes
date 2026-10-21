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
