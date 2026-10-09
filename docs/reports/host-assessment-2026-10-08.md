# Обследование хоста для psptm_minutes

Дата: 8 октября 2026 года, около 14:38 MSK (UTC+03:00).
Доступ: SSH, `epopov@192.168.1.90:22`, с проверкой известного ключа хоста.
Проект: [b0ver/psptm_minutes](https://github.com/b0ver/psptm_minutes).
Режим: только чтение на сервере. Документы сохранены на локальном компьютере.

## Вывод

Ресурсы и установленное окружение позволяют планировать Docker-приложение с существующей Ollama на хосте. Это оценка по инвентаризации, не результат нагрузочного теста. Приложение ещё не реализовано; доступ из его будущей Docker-сети и качество распознавания/протоколирования не проверены.

GitHub API сообщил, что репозиторий пустой; кода, Dockerfile, Compose и инструкций проекта для анализа нет. Локальный каталог рабочей области отсутствовал и создан только для этих документов. GitHub и файловая система сервера не изменялись.

## ОС и ресурсы

| Параметр | Наблюдение |
|---|---|
| ОС | Ubuntu 24.04.5 LTS, Noble Numbat |
| Ядро | 7.0.0-34-generic |
| Архитектура | x86_64 |
| CPU | AMD Ryzen 7 7800X3D: 8 ядер, 16 потоков |
| RAM | 124 GiB; занято около 14 GiB; доступно около 110 GiB |
| Swap | 8 GiB; использовано 0 |
| Корневой диск | 915 GiB; занято 463 GiB; доступно 407 GiB |
| /mnt/models | 1.9 TiB; занято 339 GiB; доступно около 1.5 TiB |
| Дополнительный носитель | /dev/sda: 22 GiB, почти пустой; назначение не исследовалось |
| GPU | NVIDIA GeForce RTX 4090 |
| VRAM | 24564 MiB; занято 673 MiB в первом снимке |
| Драйвер NVIDIA | 580.178.04 |
| GPU utilization | 4% в первом снимке |
| Дополнительная графика | AMD Raphael |
| CUDA compute-процессы | Во втором снимке список пуст |

Доступная память и нагрузка — моментальные значения. Наличие драйвера не подтверждает работу выбранной STT-модели в будущем контейнере. Версия CUDA toolkit не проверялась.

## Docker и Compose

- Docker Client/Server: 29.8.1; Docker Compose: v5.5.1.
- Docker Engine доступен пользователю epopov без sudo.
- Storage driver: overlayfs; cgroup v2; default runtime: runc.
- В списке runtimes есть nvidia; доступны nvidia-container-runtime и nvidia-ctk. CUDA-проверка контейнером не выполнялась.
- Обнаружено 13 Compose-проектов и 16 Docker-сетей.
- Всего 40 контейнеров: 38 со статусом Up, 1 Restarting, 1 Exited. Это не означает, что все 38 прошли healthcheck.
- `docker-nginx-1` уже находился в Restarting (код 1). Причина не исследовалась: журналы и настройки не читались.
- `docker-init_permissions-1` завершён с кодом 0 около шести месяцев назад.
- Уже существуют recap (STT/LLM), ai-gateway (Whisper/LiteLLM), Dify и другие приложения. Они могут конкурировать за GPU; факт наличия не доказывает их пригодность для нового проекта.

### Контейнеры

Статусы приведены на момент снимка, время Up не является текущим состоянием.

| Контейнер | Образ | Состояние | Опубликованные порты / expose |
|---|---|---|---|
| monday-vault-web-1 | monday-vault:local | Up 10 minutes (healthy) | 192.168.1.90:8095->8080/tcp |
| monday-vault-extractor-1 | monday-vault:local | Up 10 minutes (healthy) | 8080/tcp |
| 152fz-funnel-funnel-1 | 152fz-funnel-funnel | Up 9 days (healthy) | Не опубликованы |
| recap-bot-1 | recap-bot | Up 9 days (healthy) | 127.0.0.1:18080->8080/tcp |
| recap-stt-1 | recap-stt | Up 9 days (healthy) | Не опубликованы |
| recap-inbox-1 | recap-inbox | Up 9 days | Не опубликованы |
| recap-llm-1 | recap-llm | Up 9 days (healthy) | Не опубликованы |
| avianesail-app-1 | avianesail-app | Up 9 days (healthy) | 192.168.1.90:8082->8080/tcp |
| avianesail-dev-app-1 | avianesail-dev-app | Up 9 days (healthy) | 192.168.1.90:8083->8080/tcp |
| ai-gateway-whisper-1 | fedirz/faster-whisper-server:latest-cuda | Up 9 days | 0.0.0.0:8001->8000/tcp, [::]:8001->8000/tcp |
| ollama | alpine/socat | Up 9 days | Не опубликованы |
| fpv-lms-bot-1 | fpv-lms:prod | Up 9 days (healthy) | Не опубликованы |
| fpv-lms-webview-1 | fpv-lms:prod | Up 9 days (healthy) | 127.0.0.1:8765->8765/tcp |
| seopal-runner-1 | seopal-runner | Up 9 days (healthy) | 127.0.0.1:58000->8000/tcp |
| avianesail-dev-db-1 | postgres:16-alpine | Up 9 days (healthy) | 5432/tcp |
| avianesail-db-1 | postgres:16-alpine | Up 9 days (healthy) | 5432/tcp |
| seopal-metabase-1 | metabase/metabase:v0.63.1.5 | Up 9 days (healthy) | 127.0.0.1:53000->3000/tcp |
| seopal-postgres-1 | postgres:16 | Up 9 days (healthy) | 127.0.0.1:55432->5432/tcp |
| seopal-n8n-1 | n8nio/n8n:2.31.3 | Up 9 days | 127.0.0.1:55678->5678/tcp |
| cabinmatch-web | cabinmatch-web | Up 9 days | 0.0.0.0:8091->80/tcp, [::]:8091->80/tcp |
| searxng | searxng/searxng:latest | Up 9 days | 0.0.0.0:8888->8080/tcp, [::]:8888->8080/tcp |
| searxng-redis | valkey/valkey:8-alpine | Up 9 days (healthy) | 6379/tcp |
| ai-gateway-litellm-1 | ghcr.io/berriai/litellm:main-stable | Up 9 days | 0.0.0.0:4000->4000/tcp, [::]:4000->4000/tcp |
| leadmagnet_backend | eta_leadmagnet_slave-backend | Up 9 days (healthy) | 0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp |
| leadmagnet_frontend | eta_leadmagnet_slave-frontend | Up 9 days | 0.0.0.0:8081->80/tcp, [::]:8081->80/tcp |
| leadmagnet_postgres | postgres:16 | Up 9 days (healthy) | 0.0.0.0:5433->5432/tcp, [::]:5433->5432/tcp |
| docker-plugin_daemon-1 | langgenius/dify-plugin-daemon:0.5.3-local | Up 9 days | Не опубликованы |
| postgres | postgres:17 | Up 9 days | 5432/tcp |
| docker-nginx-1 | nginx:latest | Restarting (1) 11 seconds ago | Не опубликованы |
| docker-api-1 | langgenius/dify-api:1.13.3 | Up 9 days | 5001/tcp |
| docker-worker-1 | langgenius/dify-api:1.13.3 | Up 9 days | 5001/tcp |
| docker-worker_beat-1 | langgenius/dify-api:1.13.3 | Up 9 days | 5001/tcp |
| docker-db_postgres-1 | postgres:15-alpine | Up 9 days (healthy) | 5432/tcp |
| docker-redis-1 | redis:6-alpine | Up 9 days (healthy) | 6379/tcp |
| docker-web-1 | langgenius/dify-web:1.13.3 | Up 9 days | 3000/tcp |
| docker-init_permissions-1 | busybox:latest | Exited (0) 6 months ago | Не опубликованы |
| docker-sandbox-1 | langgenius/dify-sandbox:0.2.14 | Up 9 days (healthy) | Не опубликованы |
| docker-weaviate-1 | semitechnologies/weaviate:1.27.0 | Up 9 days | Не опубликованы |
| docker-ssrf_proxy-1 | ubuntu/squid:latest | Up 9 days | 3128/tcp |
| qdrant | qdrant/qdrant | Up 9 days | 0.0.0.0:6333-6334->6333-6334/tcp, [::]:6333-6334->6333-6334/tcp |

### Compose-проекты

152fz-funnel, ai-gateway, avianesail, avianesail-dev, cabinmatch, docker (Dify), eta_leadmagnet_slave, fpv-lms, monday-vault, recap, searxng, seopal, stack.

Содержимое Compose-файлов и конфигураций с переменными окружения не выгружалось.

## Ollama на хосте

| Параметр | Наблюдение |
|---|---|
| API version | 0.33.3 |
| Исполняемый файл | /usr/local/bin/ollama |
| Служба | ollama.service: active/running, enabled |
| Пользователь / группа службы | ollama / ollama |
| Unit | /etc/systemd/system/ollama.service |
| OLLAMA_HOST | 0.0.0.0:11434 |
| OLLAMA_MODELS | /mnt/models/ollama |
| OLLAMA_FLASH_ATTENTION | 1 |
| OLLAMA_NO_CLOUD | Не найден в Environment службы; другие источники конфигурации не проверялись |
| API localhost | GET /api/version, /api/tags, /api/ps успешны |
| API LAN-адреса | GET http://192.168.1.90:11434/api/version успешен с самого хоста |
| Загруженные модели | /api/ps вернул пустой список |

Это существующая служба хоста. Контейнер `ollama` использует `alpine/socat`, подключён к `stack_default`, адрес 172.18.0.2, без опубликованных портов. Это не контейнерный сервер Ollama. Его команда и назначение маршрута не проверялись; новый проект не должен зависеть от него.

Default bridge: 172.17.0.0/16, gateway 172.17.0.1. Whisper подключён к ai-gateway_ai-net, recap STT/LLM — к recap_default. Новый проект должен иметь свою сеть.

Служба слушает все интерфейсы; запрос API с хоста не требовал авторизации. Доступность из других устройств, правила firewall и доступность из новой Docker-сети не установлены. Перенастройка Ollama, прокси и firewall не выполнялась.

## Полный список моделей

30 тегов: 29 локальных тегов, 28 различных локальных digest и одна облачная ссылка. `llama3.2:3b` и `llama3.2:latest` имеют одинаковый digest. Размер ниже — десятичные GB файлов модели, а не необходимая VRAM.

| Модель | Размер | Квантизация | Размещение |
|---|---|---|---|
| qwen3.6:27b | 17.77 GB | Q4_K_M | Локальная |
| qwen3-coder:30b-a3b-q4_K_M | 18.56 GB | Q4_K_M | Локальная |
| devstral-small-2:24b | 15.18 GB | Q4_K_M | Локальная |
| qwen3.6:latest | 23.94 GB | Q4_K_M | Локальная |
| qwen3.5:4b | 3.39 GB | Q4_K_M | Локальная |
| qwen3.5:9b | 6.59 GB | Q4_K_M | Локальная |
| bge-m3:latest | 1.16 GB | F16 | Локальная |
| qwen2.5:14b-instruct | 8.99 GB | Q4_K_M | Локальная |
| qwen3:30b-nothink | 18.56 GB | Q4_K_M | Локальная |
| gemma4:31b | 19.87 GB | Q4_K_M | Локальная |
| mistral-small3.1:24b | 15.49 GB | Q4_K_M | Локальная |
| command-r:35b | 18.72 GB | Q4_0 | Локальная |
| mistral-small:24b | 14.33 GB | Q4_K_M | Локальная |
| qwen3.5:35b | 23.87 GB | Q4_K_M | Локальная |
| glm-5.1:cloud | Облачная ссылка | — | Запрещена для проекта |
| deepseek-coder-v2:16b | 8.91 GB | Q4_0 | Локальная |
| deepseek-r1:14b | 8.99 GB | Q4_K_M | Локальная |
| gemma2:27b | 15.63 GB | Q4_0 | Локальная |
| llama3.1:8b | 4.92 GB | Q4_K_M | Локальная |
| mistral:7b | 4.37 GB | Q4_K_M | Локальная |
| llama3.2:3b | 2.02 GB | Q4_K_M | Локальная |
| qwen2.5-coder:32b | 19.85 GB | Q4_K_M | Локальная |
| qwen3:32b | 20.20 GB | Q4_K_M | Локальная |
| gpt-oss:20b | 13.79 GB | MXFP4 | Локальная |
| qwen3:30b | 18.56 GB | Q4_K_M | Локальная |
| llama3.2:latest | 2.02 GB | Q4_K_M | Локальная |
| codellama:34b | 19.05 GB | Q4_0 | Локальная |
| nomic-embed-text:latest | 0.27 GB | F16 | Локальная |
| glm-4.7-flash:latest | 19.02 GB | Q4_K_M | Локальная |
| qwen2.5:32b | 19.85 GB | Q4_K_M | Локальная |

Точные размеры и digest сохранены в [инвентаризации моделей](ollama-models-2026-10-08.json).
`glm-5.1:cloud` содержит remote_model=glm-5.1 и remote_host=https://ollama.com:443. Её использование нарушит требование локальной AI-обработки. Локальный URL Ollama сам по себе не исключает обращение к облачной модели.

STT-модель не выбрана. Наличие faster-whisper контейнера не подтверждает, что его веса доступны офлайн и что он подходит для проекта. Исходные записи и содержимое существующих проектов не читались.

## Следствия для архитектуры

Решение подробно описано в [ADR-0001](../adr/0001-docker-host-ollama-local-processing.md).

1. Разработка, тесты и запуск приложения — в Docker/Compose; Ollama и её модели остаются на хосте.
2. Отдельная bridge-сеть; адрес Ollama через host.docker.internal и host-gateway. Проверка доступности отложена до разрешённого запуска.
3. Все записи, транскрипты, протоколы и внутренний реестр — локально. AI использует только явно разрешённые локальные модели с проверкой digest и remote-полей.
4. Telegram — внешний канал доставки утверждённых поручений. Аудио, транскрипты и полные протоколы туда не передаются.
5. Не подключать общий LiteLLM как непрозрачный маршрут: возможные внешние провайдеры не исследованы.
6. Стартовая конкурентность AI-очереди — одна задача проекта за раз; общий доступ к RTX 4090 требует последующего измерения.
7. Порты для приложения ещё не выбраны. Уже занятые публикации перечислены выше; проверить свободные порты перед запуском.

## Границы проверки и метод

Применены: cat /etc/os-release, uname, lscpu, free, df; lspci и nvidia-smi (запрос характеристик и compute-процессов); docker version/info/ps, docker compose version/ls, docker network ls/inspect; docker inspect только избранных сетевых полей; systemctl is-active/is-enabled/show; ss -ltn; GET API Ollama.

Environment службы обрабатывался на удалённом хосте: в вывод попадал только разрешённый список параметров Ollama. Полные environment, docker inspect, журналы, ключи, токены, .env, содержимое записей и конфигурация SSH не выводились.

Не выполнялись установка, сборка образов, pull моделей/образов, запуск/остановка/перезапуск сервисов или контейнеров, docker exec, инференс, миграции и запись на сервер. SSH и запросы могут оставлять штатные записи в системных журналах. Локально созданы только документы.

Не проверены: firewall/ACL, backup и восстановление, размещение всех существующих данных, производительность AI, офлайн-доступность STT, облачная конфигурация Ollama вне Environment службы, совместимость будущего приложения.

## Источники

Факты о сервере: непосредственный SSH-снимок 2026-10-08; факты о репозитории: GitHub API.
Технические основания будущей реализации:
- [Docker Compose: services / extra_hosts](https://docs.docker.com/reference/compose-file/services/#extra_hosts).
- [Docker Engine: host-gateway](https://docs.docker.com/reference/cli/dockerd/#configure-host-gateway-ip).
- [Ollama FAQ: локальные и облачные модели, сеть, память](https://docs.ollama.com/faq).
