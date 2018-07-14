FROM docker.io/alpine:3.8

ENV UID=1337 \
    GID=1337 \
    FFMPEG_BINARY=/usr/bin/ffmpeg

COPY . /opt/mautrix-telegram
WORKDIR /opt/mautrix-telegram
RUN apk add --no-cache \
      python3-dev \
      build-base \
      py3-virtualenv \
      py3-pillow \
      py3-aiohttp \
      py3-lxml \
      py3-magic \
      py3-numpy \
      py3-asn1crypto \
      py3-sqlalchemy \
      py3-markdown \
      ffmpeg \
      ca-certificates \
      su-exec \
 && pip3 install -r requirements.txt -r optional-requirements.txt

VOLUME /data

CMD ["/opt/mautrix-telegram/run.sh"]
