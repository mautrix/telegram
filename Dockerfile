FROM docker.io/alpine:3.10 AS lottieconverter

WORKDIR /build

RUN apk add --no-cache git build-base cmake \
  && git clone https://github.com/Samsung/rlottie.git \
  && cd rlottie \
  && mkdir build \
  && cd build \
  && cmake .. \
  && make -j2 \
  && make install \
  && cd ../..

RUN apk add --no-cache libpng libpng-dev zlib zlib-dev \
  && git clone https://github.com/Eramde/LottieConverter.git \
  && cd LottieConverter \
  && git checkout 543c1d23ac9322f4f03c7fb6612ea7d026d44ac0 \
  && make

FROM docker.io/alpine:3.10

ENV UID=1337 \
    GID=1337 \
    FFMPEG_BINARY=/usr/bin/ffmpeg

COPY --from=lottieconverter /usr/lib/librlottie* /usr/lib/
COPY --from=lottieconverter /build/LottieConverter/dist/Debug/GNU-Linux/lottieconverter /usr/local/bin/lottieconverter

COPY . /opt/mautrix-telegram
WORKDIR /opt/mautrix-telegram
RUN apk add --no-cache --virtual .build-deps \
      python3-dev \
      libffi-dev \
      build-base \
      git \
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
      # lottieconverter
      zlib libpng \
 && pip3 install .[speedups,hq_thumbnails,metrics] \
 && apk del .build-deps

VOLUME /data

CMD ["/opt/mautrix-telegram/docker-run.sh"]
