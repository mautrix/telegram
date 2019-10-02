#!/bin/bash
TMPDIR=$(mktemp -d)

if [ ! -e $TMPDIR ]; then
    >&2 echo "Failed to create temp directory"
    exit 1
fi

trap "exit 1"           HUP INT PIPE QUIT TERM
trap 'rm -rf "$TMPDIR"' EXIT

cd $TMPDIR

lottieconverter=$1
resolution=$2

cat > input

for i in {0..99}; do
	padded="0$i"
	$lottieconverter input frame-${padded: -2}.png png $resolution $((i+1))
done

ffmpeg -start_number 0 -framerate 30 -i frame-%02d.png -c:v libvpx-vp9 -pix_fmt yuva420p out.webm

cat out.webm
