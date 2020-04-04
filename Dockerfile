FROM dock.mau.dev/tulir/lottieconverter:alpine-3.11

RUN echo "@edge_main http://dl-cdn.alpinelinux.org/alpine/edge/main" >> /etc/apk/repositories
RUN echo "@edge_testing http://dl-cdn.alpinelinux.org/alpine/edge/testing" >> /etc/apk/repositories
RUN echo "@edge_community http://dl-cdn.alpinelinux.org/alpine/edge/community" >> /etc/apk/repositories

RUN apk add --no-cache \
      py3-virtualenv \
      py3-pillow \
      py3-aiohttp \
      py3-magic \
      py3-sqlalchemy \
      py3-alembic@edge_testing \
      py3-psycopg2 \
      py3-ruamel.yaml \
      py3-commonmark@edge_testing \
      # Indirect dependencies
      py3-idna \
      #moviepy
        py3-decorator \
        py3-tqdm \
        py3-requests \
        #imageio
          py3-numpy \
      #telethon
        py3-rsa \
        # Optional for socks proxies
        py3-pysocks \
        # cryptg
          py3-cffi \
      py3-brotli \
      # Other dependencies
      ffmpeg \
      ca-certificates \
      su-exec \
      netcat-openbsd \
      # olm
      olm-dev@edge_community \
      # matrix-nio?
      py3-future \
      py3-atomicwrites \
      py3-pycryptodome@edge_main \
      py3-peewee@edge_community \
      py3-pyrsistent@edge_community \
      py3-jsonschema \
      py3-aiofiles \
      py3-cachetools@edge_community \
      py3-prometheus-client@edge_community \
      py3-unpaddedbase64 \
      py3-pyaes@edge_testing \
      py3-logbook@edge_testing

COPY requirements.txt /opt/mautrix-telegram/requirements.txt
COPY optional-requirements.txt /opt/mautrix-telegram/optional-requirements.txt
WORKDIR /opt/mautrix-telegram
RUN apk add --virtual .build-deps \
      python3-dev \
      libffi-dev \
      build-base \
 && sed -Ei 's/psycopg2-binary.+//' optional-requirements.txt \
 && pip3 install -r requirements.txt -r optional-requirements.txt \
 && apk del .build-deps

COPY . /opt/mautrix-telegram
RUN apk add git && pip3 install .[speedups,hq_thumbnails,metrics,e2be] && apk del git

VOLUME /data
ENV UID=1337 GID=1337 \
    FFMPEG_BINARY=/usr/bin/ffmpeg

CMD ["/opt/mautrix-telegram/docker-run.sh"]
