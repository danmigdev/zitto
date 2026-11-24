#!/bin/bash
# What someone who downloads the dmg does, on a Mac where nothing is installed:
#   mac_app.sh FILE.dmg
# Pictures of the screen go to shots/.
set -u
app=/Applications/zitto.app
home="$HOME/Library/Application Support/zitto"
mkdir -p shots
say() { printf '%5d s  %s\n' $SECONDS "$*"; }
shot() { screencapture -x "shots/$1.png"; }
die() { say "FAILED: $*"; shot failed; tail -40 "$home/install.log" "$home/zitto.log" 2>/dev/null; exit 1; }
until_true() { local end=$((SECONDS + $1)); shift; until "$@" >/dev/null 2>&1; do [ $SECONDS -lt $end ] || return 1; sleep 2; done; }
running() { pgrep -f "$app/Contents/MacOS/zitto"; }
ended() { ! running && ! pgrep -f zitto.worker; }
installed() { [ -f "$home/installed" ]; }
quit() { osascript -e 'tell application "zitto" to quit'; until_true 20 ended || { ps -ax | grep -i zitto; die "it does not quit"; }; }

say "what it is"
sw_vers; spctl --status
hdiutil attach -nobrowse -readonly -mountpoint /tmp/dmg "$1" > /dev/null || die "the dmg cannot be opened"
ls -l /tmp/dmg
codesign --verify --deep --strict --verbose=2 /tmp/dmg/zitto.app || die "the signature is not valid"
spctl --assess -vv /tmp/dmg/zitto.app
ditto /tmp/dmg/zitto.app $app
hdiutil detach /tmp/dmg > /dev/null

say "first open"
open $app || die "it cannot be opened"
sleep 6; shot first-open
for minute in $(seq 15); do
  until_true 60 installed && break
  say "$(tail -1 "$home/install.log" | cut -c1-100)  $(du -sm "$home" | cut -f1) MB"
  shot installing-$minute
done
installed || die "not installed"
say "installed"; grep == "$home/install.log"
du -sm "$home"/* "$home"
sleep 25; shot window
running || die "the window did not open"
lsappinfo info -only name,bundlepath,pid zitto
say "quit"; quit

say "a song, in the window"
printf 'import sys\nsys.path.insert(0, "tests")\nfrom separation import *\nsf.write("song.wav", song(), RATE)\n' > make_song.py
"$home/venv/bin/python" make_song.py || die "no song"
open -n $app --args --player "$PWD/song.wav"
separated() { ls "$home"/library/*/stems.json; }
until_true 60 pgrep -f zitto.worker || die "the worker did not start"
pgrep -lf zitto.worker; sleep 15; shot separating
until_true 1200 separated || die "not separated"
say "separated"; sleep 8; shot separated
ls "$home"/library/*/
quit
[ ! -s "$home/zitto.log" ] || { say "what it printed:"; cat "$home/zitto.log"; }

say "command line, mp3"
env -i HOME="$HOME" PATH=/usr/bin:/bin $app/Contents/MacOS/zitto song.wav -r voice -o out.mp3 || die "command line"
"$home/bin/ffmpeg" -hide_banner -i out.mp3 2>&1 | grep Audio

say "a newer version"
echo another > "$home/installed"
open $app; until_true 180 installed || die "not updated"
say "updated"; until_true 30 running; sleep 10; shot updated; quit

say "without network"
open -n --env ZITTO_HOME=/tmp/zitto-none --env UV_OFFLINE=1 $app; sleep 12; shot no-network
pkill -f $app/Contents/MacOS/zitto; tail -3 /tmp/zitto-none/install.log

say "as downloaded from a browser: what macOS says"
ditto $app /tmp/q/zitto.app && mv $app /tmp/kept.app
xattr -r -w com.apple.quarantine "0083;$(printf %x "$(date +%s)");Safari;12345678-1234-1234-1234-123456789012" /tmp/q/zitto.app
spctl --assess -vv /tmp/q/zitto.app
(open /tmp/q/zitto.app; true) & sleep 12; shot quarantine
say "done"
