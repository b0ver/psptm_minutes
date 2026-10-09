# Корневой сертификат для соединения с Телемостом

Источник: https://gu-st.ru/content/Other/doc/russian_trusted_root_ca.cer (скачан по проверенному HTTPS 2026-10-08), портал https://www.gosuslugi.ru/crt.

Russian Trusted Root CA, SHA-256 отпечаток DER:
D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31

В тесте Chromium отклонял mdd.yandex.net и hdrc.yandex.net с net::ERR_CERT_AUTHORITY_INVALID. Публичный сертификат mdd.yandex.net выдан Russian Trusted Sub CA; срок 2026-08-14 — 2026-11-12. Добавлен корневой сертификат в системное и NSS хранилища только контейнера агента. TLS-проверка не отключена. Сертификаты macOS и хостового Linux не менялись.

NSS: https://chromium.googlesource.com/chromium/src.git/+/master/docs/linux/cert_management.md. Доверие к SSL server CA — C,,. Хранилище копируется в writable tmpfs пользователя recorder при запуске; исходный seed входит в образ. Сборка воспроизводит закреплённый публичный сертификат из репозитория, не скачивает обновления автоматически.
