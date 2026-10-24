# Подготовка контролируемой паузы STT consumer

24 октября установлено: idle CUDA-контекст 394 MiB принадлежит рабочему `psptm-minutes-worker-1`. Отсутствие queued/processing не означает освобождения CUDA. Нельзя исключать этот PID как графический фон. Production consumer пока не останавливался.

## Проверяемая основа восстановления

`scripts/ops/capacity_worker_pause_probe.py` проверяет **только синтетическую паузу/восстановление**. CLI не имеет production режима: допускает лишь project `psptm-minutes-pause-probe-{run_id}` с собственным label, immutable container/image IDs и hash исполняемого инструмента. Production project отвергается до Docker mutations, в том числе непосредственно функцией восстановления.

Probe создаёт два новых CPU-only контейнера (worker/control), network=none, runtime=runc, read-only rootfs, cap-drop ALL, без GPU DeviceRequests, по 0.5 CPU/64 MiB. Закрытый manifest и journal содержат только handles/статусы/hashes. Перед stop синтетического worker сохраняется `armed` с originally_running=true. systemd RuntimeMaxSec ограничивает unit; ExecStopPost повторяет идемпотентное восстановление того же immutable worker после timeout/SIGKILL. Проверяются фактический unit Result, состояние worker/control и journal; после сохранения receipt удаляются только собственные синтетические контейнеры. Данные, БД, секреты, Ollama и production API не подключаются.

После CI точного commit разместить инструмент и проверенный соседний capacity_watchdog.py в отдельном защищённом каталоге, не менять их до завершения units. Пример CPU-only проверки на Bender:

```sh
python3 /ABSOLUTE/tools/capacity_worker_pause_probe.py probe /ABSOLUTE/private/new-timeout --image sha256:IMMUTABLE_IMAGE_ID --failure timeout
python3 /ABSOLUTE/tools/capacity_worker_pause_probe.py probe /ABSOLUTE/private/new-sigkill --image sha256:IMMUTABLE_IMAGE_ID --failure sigkill
```

Directory каждого probe должен отсутствовать; существующий результат/handle требует осмотра, не нового запуска поверх него. Имя/путь image из примера не является разрешённым digest: передавать заранее проверенный Python image ID без сборки моделей.

Локально: **301 Docker/PostgreSQL тест passed**, 13.20 s, одно предупреждение Starlette; cleanup выполнен. Это рабочее дерево с дополнительными untracked-тестами, чистый CI считается отдельно. Новые регрессии проверяют отказ production project/public manifest/смене кода или image, отсутствие restore без journal, журнал до stop, восстановление после исключения, идемпотентность и проверку running после старта. Результаты systemd drills на Bender приведены ниже.

## Что ещё обязательно до паузы production worker

Этот probe не реализует production maintenance lease и не разрешает GPU-прогон. Нужны единый проверенный контроллер и серверное восстановление, которые:

1. Привязаны к точным handles рабочего worker и отдельного capacity job; сверяют window, code/image hashes и состояние старых attempts.
2. Удерживают общий lab.lock всю операцию и не конфликтуют с вложенным capacity watchdog/observer.
3. Подтверждают закрытый операторский приём, все durable agent states и очереди; под настоящими queue locks исключают claim между проверкой idle и остановкой consumer. Не удерживают table locks весь долгий benchmark.
4. Останавливают только согласованный idle worker и фиксируют исходное running-состояние до mutation; запускать после проверки восстановления и конкретного maintenance-плана.
5. При новой production-задаче прекращают свой benchmark и возвращают consumer; deadline/timeout/обрыв SSH/SIGKILL также заканчиваются проверенным восстановлением. Не перезапускают заменённый/чужой контейнер по имени.
6. Для cancel-приёмки оставляют observer непрерывное окно до возврата CUDA consumer; иначе собственное восстановление внесёт конкурирующий compute PID и результат должен быть rejected. Прекращение клиентского процесса не доказывает серверную отмену.
7. При неподтверждённой остановке/восстановлении сохраняют safe error и требуют разбора; не обещают гарантии при отказе Docker/хоста/питания. Только после реальных отказных drills конкретного полного контроллера переходят к GPU.

Подготовленные cancel/corpus jobs остаются created, старые manifests не меняются. Security-кандидат `3fa09d4` подготовлен отдельно; сборка и новая GPU-приёмка ещё не выполнены. [Контракт отмены](ollama-cancellation-acceptance.md), [правила мощности](capacity-schedule.md).

## Подтверждение на Bender 24 октября

[CI 993472b](https://github.com/b0ver/psptm_minutes/actions/runs/40110730282) **success**, чистый Linux/amd64 checkout: **274 passed**, 21.29 s; оба Python audits, synthetic staging/restore и recorder gates passed. Инструмент с проверенным hash установлен отдельно в `benchmarks/private/capacity/pause-tools-993472b`; соседний watchdog не изменялся.

Настоящие user-systemd drills **timeout** и **SIGKILL main** прошли на собственных CPU-only контейнерах. В обоих случаях совпал ожидаемый unit Result, journal стал restored, worker и control фактически работали при проверке. После фиксации receipt все созданные контейнеры удалены. Рабочий STT consumer остался running с прежним compute PID21405/cgroup, GPU не запрашивался. [Безопасный receipt](reports/worker-pause-probe-2026-10-24.json).

Не повторять эти успешные drills на тех же hashes без причины. Они подтверждают восстановление синтетического worker через ExecStopPost, включая смерть контроллера без finally. Полный production lease, queue claim/admission и его связь с cancel observer **ещё не реализованы/не приняты**; следующий этап — собрать и проверить единый контроллер по перечню выше. Рабочая пауза и GPU-прогон этим receipt не разрешаются.
