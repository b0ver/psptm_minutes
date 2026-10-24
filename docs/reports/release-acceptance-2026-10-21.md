# Матрица приёмки кандидата альфы — 21 октября 2026

Цель спринта остаётся незавершённой. Предыдущий этап дал продуктовый commit и проверяемый PR; текущий этап усиливает доказательство безопасной остановки очередей. Ни зелёный CI, ни этот документ не разрешают production delivery.

| Условие | Подтверждение / результат | Остаток и ответственный |
| --- | --- | --- |
| Сохранение и синхронизация продукта | PR #2 merged, main cd2e047; правка длинных встреч в отдельном PR #3. Лабораторное дерево сохранено вне release diff | Review PR #3 — владелец репозитория |
| Локальные integration/security/restore gates | [CI c5ab1b7](https://github.com/b0ver/psptm_minutes/actions/runs/39647601435) success: 230 integration, benchmark contracts, staging HTTP upload/restart/restore, synthetic restore, recorder controls/crash recovery, оба Python audit; без настоящего GPU | Пять queue guard регрессий проверены локально и в CI; OS/Chromium/CUDA scan отдельно |
| Барьер остановки consumers | Проверяется отдельным процессом с настоящими PostgreSQL table locks: busy в каждой из трёх очередей, блокирование FOR UPDATE SKIP LOCKED до освобождения, обработка незавершённого claim | Это не испытание полного deploy/rollback на сервере |
| Длинные встречи | В коде bounded merge/render, сохранение пунктов/цитат, предупреждение о проверке статусов между частями | Реальный повтор 30/60/85 минут и человеческая проверка — отдельное GPU-окно, владелец |
| Мощность | Прежние два успешных примера: 6:17/11:40; 85 минут failed | Устойчивая суточная квота не доказана; полный corpus/soak по capacity.md |
| Внешний пользовательский E2E | Ретроспективно 10 календарных цепочек с отчётами, из них 5 complete=true | Два тестовых Telegram-пользователя, Markdown, утверждение поручения, перенос/отмена, согласованный live-звонок — владелец/тестировщик |
| Рабочий backup/restore | Synthetic drill прошёл | Защищённая копия рабочей БД + записей + calendar_key, восстановление отдельно и проверка отправок/offset — оператор |
| Окружения/runner | GitHub API: environments=[], runners=0 | По решению владельца используем Bender: CPU smoke поднят отдельно; полный staging, registry auth и доверенные runners ещё требуют приёмки |
| Защита GitHub | Admin API: main protection 404 Branch not protected; rulesets=[] | Review/status checks и deployment branch policy — владелец. Второго reviewer нет; ручная приёмка владельцем допустима по runbook, production workflow пока выключен |
| Доставка/откат | Runbook и scripts подготовлены | Три immutable digest одного commit; staging deploy и candidate→previous rollback; приёмка точного manifest — оператор + reviewer |

## Актуальная сверка 24 октября

[Безопасный receipt](alpha-rc-status-2026-10-24.json) фиксирует точные исходники и пределы доказательства. PR #3 открыт в draft, head `3fa09d4`, main `cd2e047`. [CI 3fa09d4](https://github.com/b0ver/psptm_minutes/actions/runs/40097561349) **success**: **265 passed**, 19.61 s на чистом Linux/amd64 checkout; оба Python audit, synthetic staging upload/restart/restore, synthetic backup restore и recorder controls/crash recovery passed. Локальные 292 теста включают дополнительные untracked-тесты, поэтому это другой объём проверки. Filelock pin обновлён 4.0.12→4.2.1 после трёх новых advisory; исключений audit нет.

Bender CPU staging smoke пройден 23 октября на прежнем runtime, [receipt](bender-staging-smoke-2026-10-23.json). Это не полный staging promotion/version rollback. Реальная synthetic streaming/cancel попытка показала работу клиента и штатную остановку своих контейнеров, но серверная отмена не принята; [evidence](graphics-watchdog-2026-10-23.json). Новый read-only observer проверен offline, на Bender не установлен. Corpus 85/60/30 новой bounded-reduce версии не запускался.

Вечером 24 октября SSH снова доступен: observer установлен отдельно с проверкой hashes и CPU probes; одна новая cancel-попытка `cancel-observer-3fa09d4-01` уже created. Текущий initial gate — `gpu_busy`: сторонний compute Python занимает 394 MiB даже при global GPU 0%. **Агент** повторяет проверки доступности/handles/hashes/очередей/lock в следующее разрешённое окно и использует уже созданную попытку, без дубля. [Текущий receipt](alpha-rc-status-2026-10-24.json). Подготовленные образы ecd3d10/366d0ea содержат прежние зависимости; их нельзя объявлять security-прошедшими образами нового RC или переносить на него старые capacity-результаты. Для актуального runtime нужны три immutable registry digest одного commit и приёмка конкретного manifest.

GitHub повторно прочитан: environments=[], self-hosted runners=0, production opt-in variable отсутствует (404). Release build допускает только default branch, поэтому текущий draft PR ещё не может выпустить эти образы штатным release workflow. **Владелец** принимает продуктовый diff/merge PR #3; после этого возможны сборка точного main, настройка изолированного staging и ручная приёмка manifest по runbook. Не обходить branch gate и не включать production delivery. Human E2E, качество, восстановление рабочей копии данных и full staging rollback остаются открытыми; они требуют участия владельца/оператора и доступа к стенду.

## Условия внешнего шага (история 21 октября)

Владелец разрешил будни 07:00–09:00 MSK и выходные при свободных ресурсах. Созданы [расписания чата](../capacity-schedule.md); [watchdog](../capacity-watchdog.md) прошёл CPU-only timeout/SIGKILL на Bender. Отдельный образ и created-контейнеры [подготовлены](../benchmarks/capacity-preparation-2026-10-21.md), но реальная отмена и запуск GPU новой версии ещё не выполнены. Владелец подтвердил отсутствие отдельного хоста и второго reviewer и поручил использовать имеющееся. Подготовлен отдельный CPU smoke на Bender; полный staging/rollback остаётся незакрытым. Не создавать environments с вымышленным reviewer и не регистрировать production runner для обхода отсутствующей приёмки. Настройки GitHub в этой сверке только прочитаны.

Следующая проверка capacity выполняется по [методике](../capacity.md) и [приёмке длинных встреч](../long-meeting-analysis-2026-10-21.md); deploy/restore — по [runbook](../ci-cd.md). В артефакты приёмки попадают только commit/digest, агрегаты, безопасные коды и результат. Записи, транскрипты, AI-ответы и credentials остаются вне Git/CI.
