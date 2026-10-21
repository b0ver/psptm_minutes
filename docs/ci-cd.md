# CI/CD и эксплуатация корпоративной альфы

Спринт 21 октября: [план и результаты](sprints/2026-10-21-alpha-rc.md). Добавлены bounded retries анализа, отдельный Python lock агента, аудит обоих lockfile и synthetic restore drill в CI/release. Исторические результаты от 13 октября ниже не описывают итог повторной проверки 21 октября.

Повторная проверка 21 октября: 196 integration + 6 benchmark tests passed; recorder controls и checkpoint normal/SIGKILL, synthetic restore и аудит обоих Python lockfile прошли в [CI `ff63d1c`](https://github.com/b0ver/psptm_minutes/actions/runs/39585463301) на Linux/amd64. Локальная проверка выполнена на arm64. Дополнительные PostgreSQL-регрессии queue guard описаны в [матрице приёмки](reports/release-acceptance-2026-10-21.md). Ни staging promotion, ни восстановление production-данных этими проверками не подтверждаются.

Сверка GitHub API 21 октября под учётной записью с admin-доступом: environments отсутствуют, зарегистрированных runners — 0, classic protection main отсутствует (404 Branch not protected), rulesets — пусто. Это фактические незакрытые условия доставки, а не настройки, уже применённые YAML. При настройке нужны отдельный staging, назначенный reviewer и явное решение о защите ветки. До их проверки Release alpha не запускать.

## Состояние

В репозитории подготовлены `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `scripts/ops/*` и изолированный capacity-стенд. **Это подготовленная реализация процесса, а не уже активированная доставка.** GitHub environments, защита ветки, registry-доступ, runners и управляемый production baseline требуют настройки. Полный deploy/rollback на staging ещё не выполнен. Существующий сервер в ходе аудита не обновлялся.

Локальная проверка 13 октября: 158 integration-тестов (включая восемь новых ops-gates) и шесть benchmark contracts прошли; actionlint 1.7.12, Compose config и синтаксис инструментов прошли. pip-audit завершился с exit 1 и 27 advisory в пяти runtime-пакетах; следовательно, новый release workflow сейчас должен остановиться на security gate. Образы production не выпускались.

Путь изменения: PR → CI на hosted runner → merge в default branch → ручной `Release alpha` → повтор тестов точного commit и security gate → три образа linux/amd64 в GHCR → staging → ручная приёмка → environment approval → те же image digest в production. GPU-образ собирается в CI без запуска CUDA; реальные CUDA kernels проверяются на хосте перед запуском consumers. Первый этап — continuous integration и контролируемая delivery; автоматическую production-публикацию включать только после стабильного пилота.

CI запускает integration-тесты с настоящим изолированным PostgreSQL и подставными внешними сервисами, базовые benchmark contracts, проверку эксплуатационных инструментов, сборку агента Телемоста и аудит runtime lockfile. При известных уязвимостях или недоступности vulnerability service выпуск блокируется. Исключение возможно только отдельным reviewed изменением с ID, обоснованием применимости, ответственным и сроком; не добавлять общий `continue-on-error`.

## Настроить GitHub один раз

1. Выбрать default branch; потребовать PR, успешный `CI / tests` и review. Запретить прямые push/bypass для штатных изменений. Дополнительно CODEOWNERS для workflow, model policy и ops при наличии назначенных владельцев.
2. Создать environments `alpha-staging` и `alpha-production`. В production настроить required reviewer, запрет самоутверждения и только default branch. Reviewer принимает конкретный release после настоящего E2E, restore и проверки AI-качества, не только зелёных тестов. Убедиться, что тариф и visibility репозитория поддерживают требуемые protection rules; если нет — не включать production job до альтернативного контролируемого approval.
3. На изолированном staging runner задать labels `self-hosted,linux,x64,minutes-staging`; на production — `self-hosted,linux,x64,minutes-production`. Ограничить runner group этим репозиторием и доверенными workflow. PR и fork никогда не исполнять на этих runners. Checkout доверенного commit на production всё равно даёт ему доступ к хосту: права Docker фактически административные.
4. Environment variable `DEPLOY_ROOT`: абсолютный каталог, например `/srv/psptm-minutes-staging` и `/srv/psptm-minutes`. Разные машины/volumes, API-порты и Telegram-токены; staging не получает production calendar credentials и не записывает встречи сотрудников. Изоляция Compose не изолирует общий GPU: выделить отдельный хост или согласованное окно.
5. GHCR: сборка публикует с `GITHUB_TOKEN`, доступ package привязать к репозиторию. Каждый deploy host заранее авторизовать read-only credential с `read:packages` через `docker login --password-stdin`. Токен хранить вне workflow logs; production secrets не передавать в GitHub.
6. Подготовить `DEPLOY_ROOT/shared/.env` и `shared/secrets/{db_password,calendar_key,telegram_token}`. Корневой каталог и shared — 0700; файлы secrets должны читаться UID 10001 внутри контейнера в соответствии с текущим Compose secrets. Установить STT_MODEL_DIR/PATH на заранее имеющиеся веса, зафиксировать разрешённую модель/digest. Настроить лимитированные Telegram ID и согласованный режим календаря. Никогда не печатать `compose config` без `--quiet`.

Секреты staging создаются bootstrap отдельно. Пароль БД должен соответствовать реальному volume. Для production использовать существующий calendar_key: генерация нового ключа ломает расшифровку подключений. Никакого повторного bootstrap поверх рабочих credentials.

## Принять текущее развёртывание под управление

На production скрипт требует `DEPLOY_ROOT/current` — symlink на предыдущий управляемый release. Это намеренно блокирует первый автоматический deploy поверх нынешней установки без проверенного rollback. Поскольку сервисы сейчас используют разные локальные image ID, нельзя просто записать один старый тег `:local` как baseline.

Оператор должен в окно обслуживания:

- закрыть приём, убедиться в отсутствии активных записей и AI работ, сделать защищённую копию БД, recordings и всех secrets;
- сохранить текущие Compose-файлы и **точный образ каждого сервиса**. Если у локального образа нет RepoDigest, экспортировать `docker image save` и сохранить hash либо опубликовать его в закрытом registry и записать полученный digest; не подменять все image ID одним тегом;
- подготовить baseline-каталог с четырьмя Compose-файлами, shared-ссылками и `compose.release.yaml`, задающим действующий образ каждому сервису;
- проверить восстановление baseline и данных на отдельном стенде; только затем установить `current` и разрешить promotion.

Production Compose project остаётся `psptm-minutes`, чтобы использовать прежние volumes. До adoption проверить их имена/labels и mounts; менять project name на production нельзя. Рядом не должно работать второго экземпляра Telegram с тем же токеном или второго агента с тем же recordings volume.

Для первого staging запуска скрипт допускает отсутствие `current`, только если у `psptm-minutes-staging` нет контейнеров и volumes. Он создаёт новую схему. Ошибка первичной установки требует ручного разбора: автоматический rollback пустой среды не реализован. Повтор не перезаписывает существующий release-каталог.

## Выпуск

Перед запуском workflow закрыть операторский web/CLI-приём и не запускать прямые запросы к агенту в окно доставки. Telegram и календарь скрипт останавливает сам. Это эксплуатационное условие: отдельного технического maintenance endpoint пока нет. Без закрытия операторского приёма возможна гонка между проверкой агента и новым ручным стартом.

`deploy.sh` удерживает host `flock`; создаёт неизменяемый release-каталог и manifest трёх image digest, выполняет pull и проверку Compose. Затем:

1. Останавливает producers (Telegram/calendar), проверяет **все** durable-состояния агента, включая неопределённые импорты и неподтверждённые частичные записи; при блокере отменяет deploy и возобновляет прежние producers.
2. Проверяет отсутствие processing и удерживает EXCLUSIVE locks таблиц очереди при остановке worker/analysis-worker. Код SQL-барьера находится в `scripts/ops/queue_guard.py`; `freeze_workers.py` выполняет именно этот код внутри текущего API-контейнера. Это закрывает гонку, при которой worker мог взять queued-задачу после проверки. При активной AI-задаче deploy откладывается, её не убивают.
3. Останавливает API/агент, делает согласованную копию БД, recordings, secrets и environment; сохраняет manifest старых образов и checksums.
4. Проверяет схему candidate: отсутствующие/изменённые колонки или nullability блокируют deploy. Проверка не покрывает indexes, constraints, defaults и семантику приложения. Такие изменения требуют отдельного плана миграции/совместимости и review.
5. Проверяет Ollama policy и настоящий CUDA STT, запускает API/агент с ожиданием health, затем consumers и producers. Проверяет все семь долгоживущих сервисов, сохраняет их технический снимок, меняет `current` только при успехе.

Обновление выполняется с downtime на backup, проверки и запуск; zero downtime на одном агенте и singleton workers не обещается. При сбое до остановки API прежние consumers/producers возвращаются. После начала изменения среды producers останавливаются; уже принятую работу нужно проверить и завершить по runbook перед контролируемым rollback. Скрипт не пытается автоматически откатить БД или повторно отправить сообщения. Снимок `running` через 5 секунд не заменяет heartbeat или smoke Telegram. Никаких новых звонков/сообщений реальным пользователям из workflow.

Все образы собираются на hosted runner; `--no-build` используется на хосте. Digest, а не mutable SHA tag, определяет release. Pull может потребовать интернет, AI остаётся локальным. Первый GPU-check и работа staging на общей GPU должны проходить при доступной VRAM.

### Приёмка staging перед production approval

Тестовый allowlisted Telegram user: меню → запуск согласованного тестового звонка → запись → транскрипт и все выбранные отчёты → Markdown → утверждение/статус поручения. Второй user не видит его материалы. Календарь: реальное тестовое событие, перенос и отмена. Проверить безопасный отказ занятого агента, потерянный ответ импорта и продолжение после restart. Выполнить restore и rollback candidate → previous. Сохранить безопасный отчёт приёмки с commit/digest и результатами; содержание материалов остаётся локально. Пороги мощности/качества — [capacity.md](capacity.md) и [оценка допуска](alpha-readiness-2026-10-13.md).

## Rollback и восстановление

Для проверки механизма на полностью синтетических материалах выполнить:

```sh
bash scripts/ops/restore-drill.sh
```

Команда создаёт собственный Compose project с префиксом `psptm-minutes-restore-drill-`, использует только `compose.test.yaml`, создаёт синтетические Meeting/Report/CalendarSubscription и WAV. Выполняются настоящий pg_dump/pg_restore в другую БД, архивация записи и ключа, удаление исходных файлов и восстановление с проверкой под UID 10001. Проверяются отчёт, транскрипт, байты аудио и расшифровка календарного credential. После успеха удаляются только собственные контейнеры/volumes и временные synthetic-файлы. При ошибке сохраняются технические данные. Production .env, secrets и volumes не подключаются. Это не подтверждает backup/restore рабочей базы, внешнее хранилище, RPO/RTO или перенос всех Telegram-состояний.

1. Закрыть приём. По сохранённому manifest **candidate** остановить Telegram/calendar, убедиться, что новые live/AI-задачи отсутствуют, остановить workers, API и агент. Если candidate уже принял работу, дождаться её окончания; не менять образ поверх записи. Использовать те же четыре Compose-файла и `-p psptm-minutes`, `--no-build`; не использовать `down -v`.
2. Если схема/данные совместимы, запустить previous manifest с существующими volumes: init не применять как миграцию, сначала DB/API/агент с health, затем workers и producers. Проверить очередь, ownership и ключ календаря. Вернуть `current` только после smoke. Не выполнять blind rollback после несовместимой миграции.
3. Если нужен restore, сначала восстановить **в отдельном проекте/хосте** из `database.dump` (через `pg_restore --exit-on-error --no-owner -U minutes -d minutes` в пустую БД), `recordings.tar` и сохранённых secrets. Архив содержит каталог `data`; извлекать его в новый recordings volume с UID 10001, проверить mounts и ссылки файлов. `calendar_key` должен соответствовать credentials БД. Проверить SHA256SUMS, полноту БД, записи, расшифровку календаря и работоспособность приложения.
4. Проверенная копия передаётся в production в согласованное окно. БД нельзя восстанавливать отдельно от recordings/secrets. Старый snapshot теряет изменения после backup и может повторить уже доставленные Telegram-события: перед включением producers сверить отправки, offset, outbox и пользовательские действия вручную. Внешнюю доставку нельзя откатить восстановлением БД.

Автоматическая резервная копия перед deploy — защищённая локальная копия; отказ диска/хоста она не покрывает. Политика для согласования: ежедневная зашифрованная off-host копия, 7 ежедневных + 4 еженедельных, пробный restore еженедельно, предложенный RPO ≤24 ч / RTO ≤2 ч. Эти цели ещё не подтверждены. Настроить штатный scheduler/backup-сервис после выбора хранилища и retention; удаление рабочих материалов по TTL согласовать отдельно.

Для разрешённых capacity-проверок отдельно реализован [серверный watchdog](capacity-watchdog.md) с проверкой ownership и остановкой своих контейнеров. Он не заменяет безопасную остановку production и приёмку deploy/rollback.

## Контроль эксплуатации

Назначить владельца сервиса, дежурного и порядок обращения сотрудников. На пилоте ежедневно смотреть: failed-коды и незавершённые импорты, возраст queued/processing, полноту разделов, tick календаря, недоставленные Telegram-события, диск и GPU. Начальные пороги для настройки: диск <20% свободно; processing дольше 2× измеренного p95; backlog выше утверждённой квоты; отсутствие heartbeat >60 секунд; неуспешный backup или просроченный restore drill. Это предлагаемые настройки, текущих автоматических alerts нет.

Следующие технические этапы: heartbeat и метрики длительности фаз; admission/квоты; Alembic; SBOM и сканирование OS-пакетов всех трёх образов; воспроизводимая фиксация транзитивных зависимостей агента/apt; retention/delete; технический maintenance gate; резервирование общего GPU. Текущий pip-audit покрывает Python lockfile runtime и агента, не Chromium/apt/CUDA.

Основания настройки процесса: [GitHub — secure use](https://docs.github.com/en/actions/reference/security/secure-use), [GitHub — environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments), [Docker Compose production](https://docs.docker.com/compose/how-tos/production/). Actions закреплены полными SHA, проверенными по remote tags 13 октября 2026. GitHub protection rules настраиваются отдельно от YAML.
