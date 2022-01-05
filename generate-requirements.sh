#!/bin/bash
TAG="${1:-latest}"

docker run --rm dock.mau.dev/mautrix/telegram:$TAG pip freeze