#!/bin/sh
if [ ! -z "$MAUTRIX_DIRECT_STARTUP" ]; then
	if [ $(id -u) == 0 ]; then
		echo "|------------------------------------------|"
		echo "| Warning: running bridge unsafely as root |"
		echo "|------------------------------------------|"
	fi
	exec python3 -m mautrix_telegram -c /data/config.yaml
elif [ $(id -u) != 0 ]; then
	echo "The startup script must run as root. It will use su-exec to drop permissions before running the bridge."
	echo "To bypass the startup script, either set the `MAUTRIX_DIRECT_STARTUP` environment variable,"
	echo "or just use `python3 -m mautrix_telegram -c /data/config.yaml` as the run command."
	echo "Note that the config and registration will not be auto-generated when bypassing the startup script."
	exit 1
fi

# Define functions.
function fixperms {
	chown -R $UID:$GID /data

	# /opt/mautrix-telegram is read-only, so disable file logging if it's pointing there.
	if [[ "$(yq e '.logging.handlers.file.filename' /data/config.yaml)" == "./mautrix-telegram.log" ]]; then
		yq -I4 e -i 'del(.logging.root.handlers[] | select(. == "file"))' /data/config.yaml
		yq -I4 e -i 'del(.logging.handlers.file)' /data/config.yaml
	fi
}

cd /opt/mautrix-telegram

if [ ! -f /data/config.yaml ]; then
	cp example-config.yaml /data/config.yaml
	echo "Didn't find a config file."
	echo "Copied default config file to /data/config.yaml"
	echo "Modify that config file to your liking."
	echo "Start the container again after that to generate the registration file."
	fixperms
	exit
fi

if [ ! -f /data/registration.yaml ]; then
	python3 -m mautrix_telegram -g -c /data/config.yaml -r /data/registration.yaml || exit $?
	echo "Didn't find a registration file."
	echo "Generated one for you."
	echo "See https://docs.mau.fi/bridges/general/registering-appservices.html on how to use it."
	fixperms
	exit
fi

fixperms
exec su-exec $UID:$GID python3 -m mautrix_telegram -c /data/config.yaml
