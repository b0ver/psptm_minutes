FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CHROMIUM_EXECUTABLE_PATH=/opt/google/chrome/chrome
WORKDIR /agent
COPY release/main-20261119 /tmp/minutes-release
RUN test "$(dpkg --print-architecture)" = amd64 \
    && apt-get update && apt-get install -y --no-install-recommends fonts-liberation libnss3-tools ca-certificates \
    && python /tmp/minutes-release/install_os.py recorder \
    && ln -s /opt/google/chrome/chrome /usr/bin/chromium \
    && mkdir -p /etc/opt/chrome/policies/managed \
    && cp /tmp/minutes-release/chrome-policy.json /etc/opt/chrome/policies/managed/minutes-privacy.json \
    && rm -f /etc/apt/sources.list.d/google-chrome.list /etc/cron.daily/google-chrome /etc/cron.daily/google-chrome-stable \
    && rm -rf /opt/google/chrome/WidevineCdm /tmp/minutes-release /var/lib/apt/lists/*
COPY integrations/telemost/requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock && useradd --uid 10001 --create-home recorder
COPY integrations/telemost ./
RUN cp certs/russian_trusted_root_ca.crt /usr/local/share/ca-certificates/telemost-root.crt && update-ca-certificates && mkdir /agent/nssdb && certutil -N -d sql:/agent/nssdb --empty-password && certutil -A -d sql:/agent/nssdb -n 'Telemost Russian Trusted Root CA' -t 'C,,' -i certs/russian_trusted_root_ca.crt && chown -R recorder:recorder /agent/nssdb
RUN python -m py_compile service.py && python -c "import wave; w=wave.open('/agent/silence.wav','wb'); w.setparams((1,2,48000,0,'NONE','none')); w.writeframes(bytes(96000)); w.close()"
USER recorder
CMD ["uvicorn", "service:app", "--host", "0.0.0.0", "--port", "8090", "--no-access-log"]
