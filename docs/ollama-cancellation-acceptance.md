# Приёмка отмены настоящего Ollama-запроса

Статус на 21 октября 2026: клиент и offline TCP-проверка готовы, **настоящая GPU-отмена не выполнена**. Полный corpus не запускать до этой проверки. Окна и приоритет: [capacity-schedule.md](capacity-schedule.md), запуск/остановка: [capacity-watchdog.md](capacity-watchdog.md).

## Клиент

`scripts/ops/ollama_cancel_client.py` принимает только встроенный синтетический prompt. Он не читает записи/транскрипты и не выводит ответ модели. NDJSON streaming соответствует [официальному протоколу Ollama](https://docs.ollama.com/api/streaming); поле done означает окончание генерации, а не принятую отмену.

Перед запросом обязательны CAPACITY_CANCEL_ACK=synthetic-only, разрешённое окно с резервом, локальный endpoint, digest из policy образа и совпадающий CANCEL_PROBE_EXPECTED_DIGEST. Скрипт нельзя запускать напрямую в рабочее время. Его собственный SIGALRM ограничивает клиент двумя минутами и границей окна; SIGTERM/SIGINT закрывают client/stream. Ни keep_alive, ни глобальные настройки Ollama не меняются. При нормальном завершении возвращает код 2, при остановке клиента — 130, при отказе — 1. **Ни один результат клиента не означает подтверждённой серверной отмены**; server_cancellation_verified всегда false.

На собственном data volume сохраняется только cancel-state.json: phase, число chunks/символов, done и timestamp/безопасный error_code. Контент и thinking не сохраняются. Чтобы писать status, UID 10001 должен иметь доступ к своему /data; сохранять state до первого запроса обязательно.

## Подготовка отдельной попытки

Использовать проверенный image ID из [manifest кандидата](benchmarks/capacity-candidate-2026-10-21.json). Новый run_id/project и собственная PostgreSQL на tmpfs нужны для совместимости с watchdog; не переиспользовать prepared-контейнеры полного corpus. Probe не подключает production БД/secret, corpus или веса.

Measure-клиент создаётся с runtime=runc и **без GPU DeviceRequests**: его HTTP-запрос использует серверную Ollama через host loopback, а не локальный CUDA. Network=host разрешён только этому проверенному клиенту, слушающих портов он не создаёт. В env указать PYTHONPATH=/app, OLLAMA_BASE_URL=http://127.0.0.1:11434, выбранную OLLAMA_MODEL, ACK и expected digest. Read-only rootfs, cap-drop=ALL, no-new-privileges, собственный named volume /data.

Два проверенных файла кода монтируются read-only отдельно: ollama_cancel_client.py и capacity_watchdog.py в общий каталог /cancel. Файлы 0644, host parent 0700; не монтировать целиком приватный каталог. Entrypoint — python /cancel/ollama_cancel_client.py. Watchdog на хосте запускается из неизменной копии с известным SHA256; файлы не менять до завершения unit. Сверить labels project/service/run_id и immutable ID; job.json 0600 в parent 0700. Только create, не start при дневной подготовке.

## Выполнение в разрешённое окно

1. Сначала проверить real handles предыдущих попыток, отсутствие production-очередей/активных сессий/чужой GPU-нагрузки и моделей /api/ps. Не выгружать чужую модель для освобождения окна. Watchdog должен держать общий lab.lock весь probe.
2. Запустить отдельный job через watchdog start. До первого токена может идти загрузка модели; это ещё не проверка отмены.
3. Прочитать **только cancel-state.json** через docker exec своего immutable measure ID. Два наблюдения с интервалом около секунды должны показать рост chunks/characters, phase=streaming и done=false; одновременно подтвердить GPU-работу Ollama без чужих вычислений. Не читать HTTP response/log с текстом модели. Если генерация завершилась либо нет подтверждённого потока за две минуты — отмена не принята, full corpus отложить.
4. Остановить только свой unit `minutes-capacity-{run_id}` штатным systemctl --user stop. Watchdog/ExecStopPost останавливают свой measure/БД и сохраняют метрики. Клиент должен завершиться, а свежий статус показывать client_cancelled. Исчезновение timestamp или просто исчезновение контейнера не доказывает прекращения серверной работы.
5. Наблюдать после остановки GPU utilization и PID, API/agent/очереди, model digest и отсутствие конкурирующей работы. Для этой попытки требуется не менее 10 секунд последовательных наблюдений прекращения вычислений после подтверждённого активного потока. Без графического фона — GPU utilization=0. При подтверждённом графическом фоне по допуску watchdog от 23 октября — NVIDIA pmon должен показывать SM=0 у всех оставшихся compute PID Ollama (либо compute PID отсутствуют), без чужих C/C+G; общая utilization≤20% и только G-активность. До отмены обязательно наблюдать SM>0 у Ollama вместе с ростом streaming-счётчиков. Неизвестные/недоступные compute SM-счётчики не считать нулём. Фиксировать фактическое время освобождения. При новой production/чужой нагрузке результат observation_contended — отмена не принята. Это наблюдение прекращения вычислений конкретной попытки, не универсальная гарантия API или центральный admission control.
6. Дождаться естественного освобождения загруженной модели. Не отправлять keep_alive:0 и не перезапускать Ollama. Пока /api/ps не пуст и free VRAM недостаточно, initial gate полного corpus должен отказывать. Если окно заканчивается, отложить corpus.
7. Сохранить агрегат приёмки: image/model digest, hash клиента/guard, два счётчика до отмены, статусы/handles после неё, интервал quiescence, время/результат и наличие конкуренции. Не публиковать ответы модели. Только при подтверждённой отмене, свободном ресурсе и достаточном окне стартовать подготовленный job 85/60/30 по [отчёту подготовки](benchmarks/capacity-preparation-2026-10-21.md).

## Уже проверено без GPU

Полный Docker/PostgreSQL-набор — **230 passed**, 12.25 s, одно предупреждение Starlette testclient. Пять новых тестов проверяют отсутствие generated content в статусе/выводе, отказ неправильному digest до HTTP, обязательный ACK, отказ вне окна до сети и SIGTERM на настоящем локальном streaming HTTP-соединении. В последнем тесте synthetic server наблюдает разрыв соединения, а клиент не заявляет server_cancellation_verified. Используется test-only замена часов для offline loopback-сервера; CLI не имеет флага обхода окна.

Эти результаты не доказывают поведение настоящего Ollama/GPU. Настоящая проверка остаётся pending до отдельного разрешённого окна.

## История подготовки на Bender (21 октября)

[Безопасный manifest](benchmarks/ollama-cancel-preparation-2026-10-21.json) фиксирует source/hash и образ. Код клиента — commit 4f08596, [его CI](https://github.com/b0ver/psptm_minutes/actions/runs/39596349525) success. Образ приложения остаётся ecd3d10; проверено отсутствие изменений app/config/Dockerfile/requirements между этими commit. Это два источника операционного стенда, не production release manifest.

Файлы из Git archive (tar.umask=0022) с проверенным SHA256 размещены в `benchmarks/private/capacity/cancel-tools-4f08596/scripts/ops`. Внутри image UID 10001 проверены именно read-only mounts этих файлов: без ACK и вне окна — отказ до HTTP; использован network=none и нерабочий локальный порт, фактическая Ollama не вызывалась.

Отдельный project `psptm-minutes-capacity-aa6a06955f894f60` создан, оба контейнера в created. Job: `/home/epopov/projects/psptm_minutes/benchmarks/private/capacity/cancel-prepared-4f08596-01/job.json`. Host watchdog брать из указанной immutable копии cancel-tools, не редактировать во время unit. Source/helper SHA256 находятся в manifest. Named volume /data проверен отдельным CPU-only контейнером: запись/удаление синтетического файла под UID 10001 прошли. Клиент не имеет локальных DeviceRequests; GPU-инференс будет выполняться общей Ollama только после разрешённого старта.

**На момент подготовки 21 октября unit не запускался; результаты попыток 23 октября приведены ниже.** Не принимать успешную CPU-подготовку за допуск к полному corpus. Основной job prepared-ecd3d10-01 не использовался и остаётся отдельно подготовленным.

23 октября владелец разрешил исправить ложную блокировку графическим фоном. Новый host guard хранится отдельно в `benchmarks/private/capacity/native-guard-b1723a7/capacity_watchdog.py`, SHA256 `e8b6001aea3ecd47a176487d4c0a4869838873a7dc28641a8a7d7cf853cf7a96`. Старые client/helper mounts и оба prepared jobs не изменяются. Приёмку отмены привязывать к новому host guard и прежнему helper отдельно; старый manifest остаётся историей подготовки.

Текущее состояние 23 октября: [receipt](reports/graphics-watchdog-2026-10-23.json). Обе cancel-попытки завершены; повторно их не запускать. Первая остановлена с 0 chunks. Во второй (`cancel-native-b1723a7-01/job.json`, unit `minutes-capacity-615bb171e1f332f8`) подтверждены рост streaming 48→148 chunks при done=false и SM Ollama 89%. После остановки своего unit клиент завершился с 130, БД с 0. Однако pmon после остановки показал неизвестный compute SM, а дополнительное наблюдение прервалось на проверке принадлежности PID. Результат `post_cancel_process_ownership_ambiguous`; серверная отмена **не принята**. Последующая проверка cgroup показала PID Ollama в ожидаемом service, но не объяснила сбой непрерывного наблюдения.

В 08:33 MSK модель уже освободилась естественно: /api/ps пуст, GPU 0%, free VRAM 23848 MiB. Это не заменяет непрерывную проверку отмены. Основной corpus-job остаётся created; до cutoff 08:45 матрица уже не помещалась и не запускалась. Следующий шаг — диагностировать observer без GPU, затем подготовить одну новую изолированную попытку, сохранив прежние evidence и immutable client/helper/image. Оба расписания обновлены: они требуют проверки handles и новой подготовки, а не повторного старта завершённого job. Новый host guard b1723a7 прошёл CI; применять его также к основному corpus-job.

## Воспроизводимый наблюдатель после остановки (24 октября)

Добавлен `scripts/ops/capacity_cancel_observer.py` — read-only host tool без запуска моделей, остановки процессов или изменения Ollama. После успешного CI его нужно перенести в **отдельный** защищённый каталог вместе с проверенной копией host watchdog (импортируется из соседнего файла), сверить hashes. Не заменять старые mounts/immutable guard. На Bender пока не установлен: SSH снова требует Tailscale check.

Сразу после штатного stop собственного unit запустить `python3 /ABSOLUTE/observer-directory/capacity_cancel_observer.py /ABSOLUTE/new-attempt/job.json`. Наблюдатель требует, чтобы оба immutable owned контейнера уже были exited; удерживает общий lab.lock, читает production/agent gates и разрешённый model digest. Для каждого compute PID сравнивает /proc/PID/stat starttime до/после чтения точного cgroup `/system.slice/ollama.service`, затем повторяет проверку после pmon. Смена/исчезновение PID или расхождение NVIDIA источников даёт именованный отказ; чужой PID также блокирует. Утверждать, что именно race объяснял прошлый отказ, пока нельзя.

Требуются 10 секунд последовательных снимков без compute PID либо с numeric SM=0 у всех compute PID; utilization≤20%. SM `-` остаётся unknown даже при global utilization=0. Разрыв между снимками >3 секунд сбрасывает интервал; бюджет наблюдения 30 секунд проверяется между probes. Каждый внешний probe имеет timeout, поэтому полный wall time может быть больше. Неизвестные показания не смягчаются ради прохождения приёмки.

В защищённом каталоге job создаётся **один** `post-stop-observation.json`; существующий отчёт не перезаписывается поздним idle-интервалом. Частичные успешные снимки сохраняются при последующем отказе. `quiescence_verified=true` означает только наблюдённый покой; `server_cancellation_verified` всегда false. Оператор должен связать снимки с двумя растущими streaming-счётчиками, SM>0 до отмены, реальным временем stop и отсутствием конкуренции по основному протоколу. Позднее естественное освобождение модели не заменяет это доказательство. Клиент/guard/image полного corpus не меняются.

Локальный полный Docker/PostgreSQL-набор 24 октября: **290 passed**, 12.88 s, одно предупреждение Starlette. Это локальное рабочее дерево с сохранёнными чужими изменениями; точный CI данного commit проверяется отдельно. Тесты наблюдателя используют синтетические telemetry/часы/cgroup и проверяют unknown SM, чужой/исчезнувший PID, непрерывность, timeout, сохранение частичных данных и запрет перезаписи. Реальный GPU/E2E этим не подтверждён.

Повтор после отказа пустого/header-only pmon: **292 passed**, 12.13 s. Отсутствие строк телеметрии не трактуется как отсутствие вычислений.
