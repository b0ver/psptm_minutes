# Матрица приёмки кандидата альфы — 21 октября 2026

Цель спринта остаётся незавершённой. Предыдущий этап дал продуктовый commit и проверяемый PR; текущий этап усиливает доказательство безопасной остановки очередей. Ни зелёный CI, ни этот документ не разрешают production delivery.

| Условие | Подтверждение / результат | Остаток и ответственный |
| --- | --- | --- |
| Сохранение и синхронизация продукта | PR #2 merged, main cd2e047; правка длинных встреч в отдельном PR #3. Лабораторное дерево сохранено вне release diff | Review PR #3 — владелец репозитория |
| Локальные integration/security/restore gates | [CI 2592105](https://github.com/b0ver/psptm_minutes/actions/runs/39587245636) success: 201 integration, benchmark contracts, synthetic restore, recorder controls/crash recovery, оба Python audit; без настоящего GPU | Пять queue guard регрессий проверены локально и в CI; OS/Chromium/CUDA scan отдельно |
| Барьер остановки consumers | Проверяется отдельным процессом с настоящими PostgreSQL table locks: busy в каждой из трёх очередей, блокирование FOR UPDATE SKIP LOCKED до освобождения, обработка незавершённого claim | Это не испытание полного deploy/rollback на сервере |
| Длинные встречи | В коде bounded merge/render, сохранение пунктов/цитат, предупреждение о проверке статусов между частями | Реальный повтор 30/60/85 минут и человеческая проверка — отдельное GPU-окно, владелец |
| Мощность | Прежние два успешных примера: 6:17/11:40; 85 минут failed | Устойчивая суточная квота не доказана; полный corpus/soak по capacity.md |
| Внешний пользовательский E2E | Ретроспективно 10 календарных цепочек с отчётами, из них 5 complete=true | Два тестовых Telegram-пользователя, Markdown, утверждение поручения, перенос/отмена, согласованный live-звонок — владелец/тестировщик |
| Рабочий backup/restore | Synthetic drill прошёл | Защищённая копия рабочей БД + записей + calendar_key, восстановление отдельно и проверка отправок/offset — оператор |
| Окружения/runner | GitHub API: environments=[], runners=0 | Отдельный staging-хост, DEPLOY_ROOT, secrets, registry auth и доверенные runners — оператор |
| Защита GitHub | Admin API: main protection 404 Branch not protected; rulesets=[] | Review/status checks, назначенный production reviewer, запрет самоутверждения и deployment branch policy — владелец |
| Доставка/откат | Runbook и scripts подготовлены | Три immutable digest одного commit; staging deploy и candidate→previous rollback; приёмка точного manifest — оператор + reviewer |

## Условия внешнего шага

Владелец разрешил будни 07:00–09:00 MSK и выходные при свободных ресурсах. Созданы [расписания чата](../capacity-schedule.md); [watchdog](../capacity-watchdog.md) прошёл CPU-only timeout/SIGKILL на Bender. Запуск GPU новой версии ещё не выполнен. Отдельный staging и назначенный reviewer запрошены, но пока не определены. Не создавать environments с вымышленным reviewer и не регистрировать production runner для обхода отсутствующей приёмки. Настройки GitHub в этой сверке только прочитаны.

Следующая проверка capacity выполняется по [методике](../capacity.md) и [приёмке длинных встреч](../long-meeting-analysis-2026-10-21.md); deploy/restore — по [runbook](../ci-cd.md). В артефакты приёмки попадают только commit/digest, агрегаты, безопасные коды и результат. Записи, транскрипты, AI-ответы и credentials остаются вне Git/CI.
