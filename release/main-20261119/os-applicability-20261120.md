# Применимость OS advisories к основному кандидату

Оценка относится к трем образам commit c5d0059 и закрытому пилоту. Условия: два подтвержденных оператора Telegram, web через loopback/SSH, фиксированные адреса Телемоста и CalDAV. Открытие общего web или расширение способов обработки требует новой оценки. Production еще не обновлен.

Native inventory получен из итоговых образов. Дополнительно импортированы настоящие модули API, worker, Telegram и календаря. На каждом образе декодированы 16000 синтетических PCM samples. Модели, GPU, рабочая БД и записи не подключались. Отдельно исследованы библиотеки настоящего Chrome. Это инженерная оценка применимости, а не заявление об отсутствии всех уязвимостей.

## Проверенные версии и ограничения

- Python lock audits: 0 известных advisory в runtime и recorder. Этот результат не покрывает все встроенные библиотеки wheels.
- System OpenSSL: 3.0.22. Cryptography использует OpenSSL 4.0.3. Psycopg binary содержит отдельный OpenSSL 3.4.1.
- Рабочий PostgreSQL использует ssl=off. Libpq соединяется с фиксированным внутренним сервером. TLS, DANE, DTLS и QUIC не используются в этом соединении.
- Календарный Fernet использует AES-CBC и HMAC. Он не использует CMS, QUIC или AES-GCM-SIV. HTTP clients используют Python SSL; сервер API работает как HTTP за SSH.
- Chrome 158.0.8127.36 соответствует Linux Stable от 17 ноября. Подписанные метаданные Google и SHA256 DEB проверены. Native probes подтвердили версию, отключение фонового обновления/синхронизации/телеметрии, managed policies и отсутствие Widevine. [Google](https://chromereleases.googleblog.com/2026/11/).

Свежий Debian tracker дает 161 открытое source/advisory сопоставление для runtime/GPU и 365 для recorder. Это не число подтвержденных эксплуатируемых дефектов приложения. Доступные исправления закреплены в OS manifest. Оставшиеся сопоставления не переименованы в fixed и не скрыты общим ignore.

## Разбор путей

| Семейство | Проверенный путь и вывод |
|---|---|
| librtmp / libcurl | Recorder содержит DEB, но библиотеки не загружены в исследованных Chrome/Python процессах. Код не использует pycurl или RTMP client. HTTP идет через requests/httpx. PyAV ограничен file,pipe. Нативное сохранение браузера использует Chrome download API. Условия RTMP peer и curl handle reuse в этих путях отсутствуют. |
| libsndfile / OpenJPEG / dav1d | libsndfile и OpenJPEG DEB отсутствуют. Приложение использует FFmpeg/PyAV для аудио; sndfile и JPEG2000 encoding не вызываются. Загруженный dav1d относится к wheel; аудио-путь не декодирует AV1 video. Нельзя переносить Debian source version на bundled библиотеку. |
| libxml2 | DEB есть в recorder, но не загружен в исследованных процессах. CalDAV использует defusedxml.ElementTree, а не libxml2. Нет вызовов XML catalog, XPointer или libxml2 Python bindings. |
| glibc regex / wide scanf | POSIX regex imports найдены в ALSA, CUPS, GBM и Kerberos. В рабочем пути это локальная конфигурация устройств, печати, драйверов и аутентификации; текст встречи не задает их patterns. ALSA device input/печать не используются. Wide scanf imports не обнаружены. Это оценка путей, а не исправление glibc. |
| iconv / libstdc++ | Native imports сохранены в inventory. Приложение не принимает пользовательскую locale, параметры iconv или модели. Декодирование выбирает audio=0, а не subtitles; параметры кодировки и веса фиксированы. Условия описанных CP949//IGNORE, ISO-2022-CN-EXT, IBM1390/1399, parallel search и переполнений размеров не установлены для этих входов. |
| OpenSSL | Указанные выше фактические consumers не используют caller-supplied PEM stacks, CMS recipient encryption, Suite B TLS server или QUIC server. Для нескольких QUIC/GCM-SIV/Suite B advisory свежий tracker отдельно указывает Bookworm not-affected. Старый shortlist не заменяет эту сверку. |
| NSS | Seed NSS содержит публичный доверенный CA. Приложение не использует NSS private-key RSA decrypt/ECDSA signing или PK11_Encrypt in-place ChaCha20. Chrome embedded crypto нельзя считать Debian OpenSSL/NSS по одному наличию пакетов. |
| Локальные утилиты и privilege paths | Рабочие процессы имеют UID10001, read-only root, cap_drop ALL и no-new-privileges. Приложение не передает пользовательские команды shell/CLI. Условия setuid/setgid loader advisory и интерактивных административных утилит не входят в рабочий API-путь. |

Выводы о путях - ограниченные инженерные выводы из кода, фактических imports и native probes. Они действуют только при сохранении условий пилота. Общая уязвимость пакета может оставаться открытой. Новые reachable high/critical advisories требуют остановки выпуска и отдельного исправления или конкретного согласованного решения.

Основные первичные материалы: [Debian tracker](https://security-tracker.debian.org/tracker/), [glibc regex](https://security-tracker.debian.org/tracker/CVE-2026-100290), [wide scanf](https://security-tracker.debian.org/tracker/CVE-2026-115819), [OpenSSL 17 ноября](https://openssl-library.org/news/secadv/20261117.txt), [RTMP](https://security-tracker.debian.org/tracker/CVE-2026-112060). Снимок tracker: SHA256 ea0a1fa0ac0ab7b70b733b60eb3dddb41e84613708de66108fbdf780b0840aea.
