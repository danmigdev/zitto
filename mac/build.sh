#!/bin/sh
# Builds zitto.app and its dmg in build/mac. On a Mac with Apple Silicon and the tools of Xcode.
# Inside there are only zitto and uv, which downloads all the rest the first time (see install.sh).
set -eu
cd "$(dirname "$0")/.."
PYTHON=3.12.16
UV=https://files.pythonhosted.org/packages/f3/a7/a8d7b074798c7fb3b26b37584b8fe2e29df2ccdc24ce7902caab2a53edbf/uv-0.13.9-py3-none-macosx_11_0_arm64.whl
UV_HASH=66c512266d633bef48384a1f53003f41f2a8e7b36fe6e7fbfd3e0fd52d96fa63

version="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' zitto/__init__.py)"
out=build/mac
app="$out/dmg/zitto.app/Contents"
rm -rf "$out"
mkdir -p "$app/MacOS" "$app/Helpers" "$app/Resources/licenses/uv" "$out/icon.iconset"

clang -fobjc-arc -O2 -Wall -Wextra -Wno-unused-parameter -Werror -arch arm64 -mmacosx-version-min=14.0 \
    -framework Cocoa mac/launcher.m -o "$app/MacOS/zitto"
sed "s/VERSION/$version/" mac/Info.plist > "$app/Info.plist"
plutil -lint "$app/Info.plist"

curl -fsSL "$UV" -o "$out/uv.whl"
echo "$UV_HASH  $out/uv.whl" | shasum -a 256 -c -
unzip -q -j "$out/uv.whl" '*/scripts/uv' -d "$app/Helpers"
unzip -q -j "$out/uv.whl" '*/licenses/*' -d "$app/Resources/licenses/uv"
chmod 755 "$app/Helpers/uv"
"$app/Helpers/uv" --version

"$app/Helpers/uv" build --no-config --python "$PYTHON" --wheel --out-dir "$out/wheel" .
cp "$out"/wheel/zitto-*.whl mac/install.sh mac/requirements.txt LICENSE.md NOTICE.md "$app/Resources/"
echo "$PYTHON" > "$app/Resources/python"
cat "$app"/Resources/install.sh "$app"/Resources/requirements.txt "$app"/Resources/python "$app"/Resources/*.whl \
    | shasum -a 256 | cut -d ' ' -f 1 > "$app/Resources/id"

for size in 16 32 128 256 512; do
    sips -z $size $size mac/icon.png --out "$out/icon.iconset/icon_${size}x$size.png" > /dev/null
    sips -z $((size * 2)) $((size * 2)) mac/icon.png --out "$out/icon.iconset/icon_${size}x$size@2x.png" > /dev/null
done
iconutil -c icns "$out/icon.iconset" -o "$app/Resources/icon.icns"

# Without a certificate of Apple: it is only whole, not known. The first time macOS asks to allow it.
codesign --force --sign - "$app/Helpers/uv"
codesign --force --sign - "$out/dmg/zitto.app"
codesign --verify --deep --strict --verbose=2 "$out/dmg/zitto.app"

ln -s /Applications "$out/dmg/Applications"
cp "mac/Read me.txt" "$out/dmg/"
for attempt in 1 2 3; do  # now and then "Resource busy"
    hdiutil create -volname zitto -srcfolder "$out/dmg" -fs HFS+ -format UDZO -ov "$out/zitto-$version-mac.dmg" && break
    [ $attempt -lt 3 ] && sleep 5
done
ls -l "$out"/*.dmg
shasum -a 256 "$out"/*.dmg
