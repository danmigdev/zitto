#!/bin/sh
# Everything that zitto needs, in ZITTO_HOME. The app runs it the first time that a version is opened,
# again if it did not end, and when other models are chosen:
#   install.sh all|models MEGABYTES      how much the models in use take
# It says where it is on the standard output, for the window of the app:
#   STEP text folder megabytes           the folder that grows, and how much it will
# separated by tabs. All the rest is in install.log.
set -eu
what=$1 megabytes=$2
here="$(cd "$(dirname "$0")" && pwd)"
uv="$here/../Helpers/uv"
version="$(cat "$here/python")"
cd "$ZITTO_HOME"
exec 3>&1 >>install.log 2>&1
step() { echo "== $(date '+%F %T') $1"; printf 'STEP\t%s\t%s\t%s\n' "$1" "$ZITTO_HOME/$2" "$3" >&3; }

# Only what is here, and only what is written here.
unset VIRTUAL_ENV
export UV_NO_CONFIG=1 UV_PYTHON_INSTALL_DIR="$ZITTO_HOME/python" UV_PYTHON_PREFERENCE=only-managed
export UV_CACHE_DIR="$ZITTO_HOME/cache" UV_HTTP_TIMEOUT=120

if [ "$what" = all ]; then
    step "Step 1 of 3: Python" python 75
    "$uv" python install --no-bin "$version"
    python="$("$uv" python find "$version")"

    step "Step 2 of 3: the libraries" cache 1300
    # What is complete is updated. What is not is done again, from what is in the cache.
    if { [ -f venv/complete ] || [ -f installed ]; } &&
       [ "$(venv/bin/python -c 'import platform; print(platform.python_version())')" = "$version" ]; then
        rm -f installed venv/complete
    else
        rm -rf installed venv
        "$uv" venv --no-project --python "$python" venv
    fi
    "$uv" pip install --python venv/bin/python --no-deps --only-binary :all: --require-hashes \
        --compile-bytecode -r "$here/requirements.txt"
    "$uv" pip install --python venv/bin/python --no-deps --no-index --reinstall --compile-bytecode "$here"/zitto-*.whl
    mkdir -p bin
    ln -sf "$(venv/bin/python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')" bin/ffmpeg
    ln -sf "$(venv/bin/python -c 'import sys; print(sys.base_prefix)')/lib/libpython3.12.dylib" libpython.dylib
    bin/ffmpeg -version
    du -sm cache python venv
    cp "$here/id" venv/complete
    rm -rf cache  # before the models: less free space is needed
fi

step "Step 3 of 3: the models" models "$megabytes"
rm -f models/*.partial  # of downloads that were stopped
venv/bin/python -P -m zitto.worker --models
du -sm models
cp "$here/id" installed
echo "== $(date '+%F %T') done"
