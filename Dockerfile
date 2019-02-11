FROM docker.io/alpine:3.8

ENV UID=1337 \
    GID=1337 \
    FFMPEG_BINARY=/usr/bin/ffmpeg

COPY . /opt/mautrix-telegram
WORKDIR /opt/mautrix-telegram
RUN apk add --no-cache \
      py3-virtualenv \
      py3-pillow \
      py3-aiohttp \
      py3-lxml \
      py3-magic \
      py3-sqlalchemy \
      py3-markdown \
      py3-psycopg2 \
      # Indirect dependencies
      py3-numpy \
      py3-asn1crypto \
      py3-future \
      py3-markupsafe \
      py3-mako \
      py3-decorator \
      py3-dateutil \
      py3-idna \
      py3-six \
      py3-asn1 \
      py3-rsa \
      # Other dependencies
      python3-dev \
      build-base \
      ffmpeg \
      ca-certificates \
      su-exec \
 && pip3 install .[all]

VOLUME /data

CMD ["/opt/mautrix-telegram/docker-run.sh"]
