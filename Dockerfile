FROM dock.mau.dev/tulir/lottieconverter:alpine-3.13

ARG TARGETARCH=amd64

#RUN echo $'\
#@edge http://dl-cdn.alpinelinux.org/alpine/edge/main\n\
#@edge http://dl-cdn.alpinelinux.org/alpine/edge/testing\n\
#@edge http://dl-cdn.alpinelinux.org/alpine/edge/community' >> /etc/apk/repositories

RUN apk add --no-cache \
      python3 py3-pip py3-setuptools py3-wheel \
      py3-virtualenv \
      py3-pillow \
      py3-aiohttp \
      py3-magic \
      py3-sqlalchemy \
      py3-telethon-session-sqlalchemy \
      py3-alembic \
      py3-psycopg2 \
      py3-ruamel.yaml \
      py3-commonmark \
      # Indirect dependencies
      py3-idna \
      #moviepy
        py3-decorator \
        py3-tqdm \
        py3-requests \
        #imageio
          py3-numpy \
      #py3-telethon@edge \ (outdated)
        # Optional for socks proxies
        py3-pysocks \
        # cryptg
          py3-cffi \
	  py3-qrcode \
      py3-brotli \
      # Other dependencies
      ffmpeg \
      ca-certificates \
      su-exec \
      netcat-openbsd \
      # encryption
      olm-dev \
      py3-pycryptodome \
      py3-unpaddedbase64 \
      py3-future \
      bash \
      curl \
      jq \
      yq

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
RUN apk add git && pip3 install .[speedups,hq_thumbnails,metrics,e2be] && apk del git \
  # This doesn't make the image smaller, but it's needed so that the `version` command works properly
  && cp mautrix_telegram/example-config.yaml . && rm -rf mautrix_telegram

VOLUME /data
ENV UID=1337 GID=1337 \
    FFMPEG_BINARY=/usr/bin/ffmpeg

CMD ["/opt/mautrix-telegram/docker-run.sh"]
