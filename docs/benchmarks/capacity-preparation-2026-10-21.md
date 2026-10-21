# Подготовка повторного capacity-прогона — 21 октября 2026

Подготовка завершена без GPU-инференса. Это не результат замера мощности и не production release. [Безопасный manifest](capacity-candidate-2026-10-21.json) фиксирует commit, image ID, model digest и выполненные проверки.

## Образ и права исходников

На Bender из точного commit `ecd3d10b7ef6f1011dba65afaecca1aa09eb390e` собран отдельный linux/amd64 GPU-образ. Применять immutable локальный ID:

`sha256:21295ad6cb679361dbde6b7bb1ce4928e5c8f4a9313fa2fb07cfa22342f81a24`

Удобная локальная метка — `psptm-minutes-capacity-gpu:ecd3d10-r2`; она не заменяет ID. Образ не опубликован в registry, это не три release digest для доставки. Working :local и production-сервисы не менялись.

Первый образ `sha256:a4bfe213517430d811aa34ea92d001d0dd919bcd329e4da2673d046a2b6093fe` отклонён: обычная распаковка под umask 077 дала исходникам права 0600, COPY сохранил их для root, UID 10001 получил PermissionError при import. Вторая копия исходников распакована с сохранением tar permissions, group/world write сняты только с файлов кода; parent остаётся 0700, внутренние файлы/каталоги 0644/0755 с сохранением executable bits. Содержание файлов проверено по прежнему SHA256 manifest. Исходные записи, веса и production-права не менялись.

Оба build handle проверены до повторной попытки: первый завершён с exit=1 на CPU import check, второй — phase=finished, exit=0, systemd inactive. Живой build не перезапускался. Приватные каталоги: benchmarks/private/capacity/build-ecd3d10 и build-ecd3d10-r2.

## Подтверждённые проверки

- Source archive SHA256 `7c6cbe18cfa6021d4d4b0858f7c85e433055d4032fb440fe24ad55d02d0800c8`, OCI revision и amd64 совпадают с выбранным commit.
- pip check прошёл на итоговом образе. CPU imports app.worker/app.reports/faster_whisper/ctranslate2 прошли под штатным UID.
- Отдельный контейнер с runtime=runc, network=none и read-only rootfs подтвердил UID 10001 и фактическое открытие трёх corpus-файлов и трёх обязательных файлов весов. GPU-устройств в проверочном контейнере нет. Модель STT и inference не запускались, содержимое записей не выводилось.
- Digest выбранной существующей модели совпал с source allowlist. Метаданные трёх примеров однозначно сопоставлены по размеру прежнему замеру; приватный профиль упорядочен 85/60/30 минут по прежнему speech_end_seconds, это не новое измерение длительности медиа.
- Подготовлены два контейнера нового project `psptm-minutes-capacity-d845c7676d734200`, оба **created**, не running. Job — `/home/epopov/projects/psptm_minutes/benchmarks/private/capacity/prepared-ecd3d10-01/job.json`. Проверены immutable ID/ownership. Запуск watchdog на реальных часах вне окна вернул outside_window; после проверки контейнеры остались created.
- [CI ecd3d10](https://github.com/b0ver/psptm_minutes/actions/runs/39591200763) и [CI cca1246](https://github.com/b0ver/psptm_minutes/actions/runs/39591987919) success. Приложение в этом этапе не менялось; прежний полный набор — 225 passed.

## Следующий разрешённый шаг

В будни 07:00–09:00 MSK либо в выходные проверить живые handles, GPU/Ollama, production-очереди/сессии и lab.lock по [расписанию](../capacity-schedule.md). **Не запускать подготовленный полный corpus до проверки отмены настоящего inference** в отдельной синтетической попытке. Watchdog должен остановить только свой клиент/контейнер; прекращение своей CUDA/Ollama-нагрузки нужно наблюдать, чужую модель не выгружать.

После успешной отмены, если окно и ресурсы остаются доступны, стартовать подготовленный job через [watchdog](../capacity-watchdog.md). Изменившийся image/model digest или неподтверждённый live handle запрещают слепой повтор. Job содержит три полных цикла в порядке 85/60/30 минут; первый проверяет прежний отказ. Итоговые timings, private dump и человеческая проверка качества ещё отсутствуют. Квота/soak и внешняя E2E по спринту остаются незавершёнными.
