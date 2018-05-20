FROM docker.io/alpine:3.7

ENV UID=1337 \
    GID=1337

COPY . /opt/mautrixtelegram
RUN apk add --no-cache \
      python3-dev \
      py3-virtualenv \
      build-base \
      zlib-dev \
      jpeg-dev \
      libxslt-dev \
      libxml2-dev \
      libmagic \
      ffmpeg \
      bash \
      ca-certificates \
      su-exec \
      s6 \
 && cd /opt/mautrixtelegram \
 && cp -r docker/root/* / \
 && rm docker -rf \
 && virtualenv -p /usr/bin/python3 .venv \
 && source .venv/bin/activate \
 && pip install -r requirements.txt -r optional-requirements.txt

VOLUME /data

CMD ["/bin/s6-svscan", "/etc/s6.d"]
