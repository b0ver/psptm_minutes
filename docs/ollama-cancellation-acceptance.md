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
5. Наблюдать после остановки GPU utilization и PID, API/agent/очереди, model digest и отсутствие конкурирующей работы. Для этой попытки требуется не менее 10 секунд последовательных наблюдений GPU utilization=0 при остановленном клиенте, после подтверждённого активного потока. Фиксировать фактическое время освобождения. При новой production/чужой нагрузке результат observation_contended — отмена не принята. Это наблюдение прекращения вычислений конкретной попытки, не универсальная гарантия API или центральный admission control.
6. Дождаться естественного освобождения загруженной модели. Не отправлять keep_alive:0 и не перезапускать Ollama. Пока /api/ps не пуст и free VRAM недостаточно, initial gate полного corpus должен отказывать. Если окно заканчивается, отложить corpus.
7. Сохранить агрегат приёмки: image/model digest, hash клиента/guard, два счётчика до отмены, статусы/handles после неё, интервал quiescence, время/результат и наличие конкуренции. Не публиковать ответы модели. Только при подтверждённой отмене, свободном ресурсе и достаточном окне стартовать подготовленный job 85/60/30 по [отчёту подготовки](benchmarks/capacity-preparation-2026-10-21.md).

## Уже проверено без GPU

Полный Docker/PostgreSQL-набор — **230 passed**, 12.25 s, одно предупреждение Starlette testclient. Пять новых тестов проверяют отсутствие generated content в статусе/выводе, отказ неправильному digest до HTTP, обязательный ACK, отказ вне окна до сети и SIGTERM на настоящем локальном streaming HTTP-соединении. В последнем тесте synthetic server наблюдает разрыв соединения, а клиент не заявляет server_cancellation_verified. Используется test-only замена часов для offline loopback-сервера; CLI не имеет флага обхода окна.

Эти результаты не доказывают поведение настоящего Ollama/GPU. Настоящая проверка остаётся pending до отдельного разрешённого окна.
