FROM docker.io/alpine:3.10

ENV UID=1337 \
    GID=1337 \
    FFMPEG_BINARY=/usr/bin/ffmpeg

COPY . /opt/mautrix-telegram
WORKDIR /opt/mautrix-telegram
RUN apk add --no-cache --virtual .build-deps \
      python3-dev \
      libffi-dev \
      build-base \
  && apk add --no-cache \
      py3-virtualenv \
      py3-pillow \
      py3-aiohttp \
      py3-magic \
      py3-sqlalchemy \
      py3-psycopg2 \
      py3-ruamel.yaml \
      # Indirect dependencies
      py3-idna \
      #commonmark
        py3-future \
      #alembic
        py3-mako \
        py3-dateutil \
        py3-markupsafe \
      #moviepy
        py3-decorator \
        #py3-tqdm \
        py3-requests \
        #imageio
          py3-numpy \
      #telethon
        py3-rsa \
        # cryptg
          py3-cffi \
      # Other dependencies
      ffmpeg \
      ca-certificates \
      su-exec \
      netcat-openbsd \
 && pip3 install .[fast_crypto,hq_thumbnails,metrics] \
 && pip3 install --upgrade 'https://github.com/LonamiWebs/Telethon/tarball/master#egg=telethon' \
 && apk del .build-deps

VOLUME /data

CMD ["/opt/mautrix-telegram/docker-run.sh"]
