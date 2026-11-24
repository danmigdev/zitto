#!/bin/bash
# What someone who downloads the dmg does, on a Mac where nothing is installed:
#   mac_app.sh FILE.dmg
# Pictures of the screen go to shots/.
set -u
before=https://github.com/danmigdev/zitto/releases/download/v0.4.0/zitto-0.4.0-mac.dmg
app=/Applications/zitto.app
home="$HOME/Library/Application Support/zitto"
lead=mel_band_roformer_karaoke_becruily.ckpt drums=MDX23C-DrumSep-aufr33-jarredou.ckpt wind=17_HP-Wind_Inst-UVR.pth
guitar=MVSep-Mega-53-acoustic-guitar.npz
mkdir -p shots
say() { printf '%5d s  %s\n' $SECONDS "$*"; }
shot() { screencapture -x "shots/$1.png"; }
die() { say "FAILED: $*"; shot failed; tail -40 "$home/install.log" "$home/zitto.log" 2>/dev/null | grep -v '%|'; exit 1; }
until_true() { local end=$((SECONDS + $1)); shift; until "$@" >/dev/null 2>&1; do [ $SECONDS -lt $end ] || return 1; sleep 2; done; }
running() { pgrep -f "$app/Contents/MacOS/zitto"; }
left() { pgrep -f 'install.sh|Helpers/uv|MacOS/zitto|zitto.worker'; }
ended() { ! left; }
installed() { cmp -s "$home/installed" $app/Contents/Resources/id; }
in_window() { osascript -e "tell application \"System Events\" to tell window 1 of process \"zitto\" to $1"; }
has() { [ "$(in_window "get exists button \"$1\"" 2>/dev/null)" = true ]; }
click() { until_true 20 in_window "click $1" || die "cannot click $1"; }
ticks() { for what in Lead Pieces Acoustic Wind; do in_window "get value of first checkbox whose title starts with \"$what\""; done | xargs; }
tick() { click "(first checkbox whose title starts with \"$1\")"; }
asked() { until_true 20 has Install; }
settings() { osascript -e 'tell application "System Events" to tell process "zitto" to click (first menu item of menu 1 of menu bar item 2 of menu bar 1 whose (title starts with "Settings" or title starts with "Preferences"))'; }
quit() { osascript -e "tell application \"$app\" to quit"; until_true 20 ended || { ps -ax | grep -i zitto; die "it does not quit"; }; }
free() { df -m "$HOME" | awk 'NR == 2 {print $4}'; }
models() { ls "$home/models" | grep -E '\.(ckpt|pth|npz)$' | xargs; }
stems() { ls "$home"/library/"$1"/ | grep flac | xargs; }
in_dmg() { hdiutil attach -nobrowse -readonly -mountpoint /tmp/dmg "$1" > /dev/null || die "the dmg cannot be opened"; }

say "what it is"
sw_vers; spctl --status
curl -fsSL "$before" -o /tmp/before.dmg || die "the version before cannot be downloaded"
in_dmg "$1"
ls -l /tmp/dmg
codesign --verify --deep --strict --verbose=2 /tmp/dmg/zitto.app || die "the signature is not valid"
spctl --assess -vv /tmp/dmg/zitto.app
ditto /tmp/dmg/zitto.app $app
hdiutil detach /tmp/dmg > /dev/null

# How much of the disk is used, at each moment.
start=$(free)
(while :; do echo $((start - $(free))) >> /tmp/used; sleep 0.5; done) & watching=$!
disown
used() { say "$1: $(tail -1 /tmp/used) MB on the disk, $(sort -n /tmp/used | tail -1) while it was installed"; : > /tmp/used; }

say "first open"
open $app || die "it cannot be opened"
asked || die "it does not ask"
shot first-open; ticks
[ "$(ls "$home" | xargs)" = "" ] || { ls "$home"; die "it did not wait for an answer"; }
for what in Lead Pieces Acoustic Wind; do tick $what; done
shot only-what-is-needed; ticks
click 'button "Install"'
for minute in $(seq 15); do
  until_true 60 installed && break
  say "$(tail -1 "$home/install.log" | cut -c1-100)"; shot installing-$minute
done
installed || die "not installed"
say "installed"; grep '^==' "$home/install.log"; grep -E '^[0-9]+[[:space:]]' "$home/install.log"
used "only what is needed"
cat "$home/settings.json"; models
[ "$(models)" = BS-Roformer-SW.ckpt ] || die "not only the model that is needed"
sleep 20; shot window
running || die "the window did not open"
lsappinfo info -only name,bundlepath,pid zitto
say "quit, and open again at once"; quit
open $app; sleep 10; running > /dev/null || die "it does not open again"
quit

song() {  # separates a generated song in the window
  printf 'import sys\nsys.path.insert(0, "tests")\nfrom separation import *\nsf.write("%s.wav", song(), RATE)\n' $1 > make_song.py
  "$home/venv/bin/python" make_song.py || die "no song"
  before_songs=$(ls -d "$home"/library/*/ 2>/dev/null | wc -l)
  open -n $app --args --player "$PWD/$1.wav"
  until_true 60 pgrep -f zitto.worker || die "the worker did not start"
  sleep 15; shot separating-$1
  separated() { [ "$(ls "$home"/library/*/stems.json 2>/dev/null | wc -l)" -gt "$before_songs" ]; }
  until_true 1200 separated || die "not separated"
  say "separated"; sleep 8; shot separated-$1
  made=$(basename "$(ls -td "$home"/library/*/ | head -1)"); stems $made
}
say "a song, with only what is needed"
song few
[ "$(stems $made)" = "bass.flac drums.flac guitar.flac other.flac piano.flac voice.flac" ] || die "not those parts"

say "all the models, from the menu"
osascript -e 'tell application "System Events" to tell process "zitto" to get title of menu items of menu 1 of menu bar item 2 of menu bar 1'
settings || die "no Settings in the menu"
until_true 30 has Apply || die "the window of the models does not open"
sleep 2; shot settings; ticks
[ "$(ticks)" = "0 0 0 0" ] || die "the choice was not remembered"
for what in Lead Pieces Acoustic Wind; do tick $what; done
click 'button "Apply"'
sleep 20; shot more-models
opened_with() { [ "$(models | wc -w | xargs)" = "$1" ] && ! pgrep -f install.sh && running; }
until_true 600 opened_with 5 || die "the models did not come"
sleep 15; shot window-all; used "everything"; models; quit
say "a song, with everything"
song all
[ "$(stems $made)" = "acoustic.flac backing.flac bass.flac cymbals.flac electric.flac kick.flac lead.flac other.flac piano.flac snare.flac toms.flac wind.flac" ] || die "not those parts"

say "the guitars, whatever there is in that song"
ZITTO_HOME="$home" PATH="$home/bin:$PATH" "$home/venv/bin/python" -P -c '
import tempfile, time, soundfile as sf
from pathlib import Path
from zitto import engine, models
x, rate = sf.read("all.wav", dtype="float32", always_2d=True)
start = time.time()
found = engine.Engine().stems(x, models.GUITAR, ["acoustic"], Path(tempfile.mkdtemp()))["acoustic"]
assert found.shape == x.shape and abs(found).max() < 2
print("acoustic %.4f, in %.0f s" % ((found ** 2).mean() ** 0.5, time.time() - start))' 2>&1 | grep -vE 'Warning|autocast|_VF' | tail -5
[ "${PIPESTATUS[0]}" = 0 ] || die "the guitars"
say "one model less, and Cancel"
settings; until_true 30 has Apply || die "the window of the models does not open"
[ "$(ticks)" = "1 1 1 1" ] || die "the choice was not remembered"
tick Wind; click 'button "Cancel"'; sleep 10; running > /dev/null || die "Cancel does not bring back the window"
[ -f "$home/models/$wind" ] || die "Cancel did it"
settings; until_true 30 has Apply || die "the window of the models does not open"
tick Wind; click 'button "Apply"'
until_true 60 opened_with 4 || die "it is still there"
models; sleep 8; shot fewer-models; quit
[ ! -s "$home/zitto.log" ] || { say "what it printed:"; cat "$home/zitto.log"; }
grep -h . "$HOME/Library/Logs/DiagnosticReports"/zitto* 2>/dev/null | head -5

say "command line, mp3"
env -i HOME="$HOME" PATH=/usr/bin:/bin $app/Contents/MacOS/zitto all.wav -r voice -o out.mp3 > cli.txt 2>&1 || { cat cli.txt; die "command line"; }
grep -v '%|' cli.txt
"$home/bin/ffmpeg" -hide_banner -i out.mp3 2>&1 | grep Audio

say "a newer version keeps the choice, and does not ask"
echo another > "$home/installed"
open $app; until_true 180 installed || die "not updated"
say "updated"; until_true 30 running; sleep 10; shot updated; models; quit
[ "$(models | wc -w | xargs)" = 4 ] || die "it changed the models"

say "without network"
open -n --env ZITTO_HOME=/tmp/zitto-none --env UV_OFFLINE=1 $app
asked || die "it does not ask"; click 'button "Install"'
until_true 30 has Back || die "it does not say that it stopped"
shot no-network; click 'button "Back"'; sleep 1; shot no-network-back; tail -3 /tmp/zitto-none/install.log; quit

say "on a disk that is too small"
hdiutil create -size 4g -fs APFS -volname small -type SPARSE /tmp/small > /dev/null && hdiutil attach -nobrowse /tmp/small.sparseimage > /dev/null
open -n --env ZITTO_HOME=/Volumes/small/zitto $app; asked || die "it does not ask"
shot no-space; [ "$(in_window 'get enabled of button "Install"')" = false ] || die "it would not fit"
tick Lead; sleep 1; shot enough-space; [ "$(in_window 'get enabled of button "Install"')" = true ] || die "it would fit"
quit; hdiutil detach -force /Volumes/small > /dev/null

mv $app /tmp/kept.app; mkdir -p /tmp/old /tmp/new; in_dmg /tmp/before.dmg
ditto /tmp/dmg/zitto.app /tmp/old/zitto.app; ditto /tmp/kept.app /tmp/new/zitto.app; hdiutil detach /tmp/dmg > /dev/null
say "who has the version before, complete"
app=/tmp/old/zitto.app home=/tmp/zitto-old
open -n --env ZITTO_HOME=$home $app; asked || die "it does not ask"; click 'button "Install"'
until_true 400 installed || die "the version before does not install"
until_true 30 running; sleep 8; quit; models
app=/tmp/new/zitto.app; say "the new one"
open -n --env ZITTO_HOME=$home $app; until_true 180 installed || die "it does not update by itself"
until_true 30 running; sleep 10; shot from-before; models; quit; grep '^==' $home/install.log
[ "$(models | wc -w | xargs)" = 5 ] && [ -e $home/models/$guitar ] || die "not the models it should have"
say "who stopped the version before in the middle"
app=/tmp/old/zitto.app home=/tmp/zitto-old-half
open -n --env ZITTO_HOME=$home $app; asked || die "it does not ask"; click 'button "Install"'
at_models() { grep -q "Step 3" $home/install.log; }
until_true 200 at_models || die "the version before does not install"
sleep 5; quit; ls $home
app=/tmp/new/zitto.app; say "the new one"
open -n --env ZITTO_HOME=$home $app; asked || die "it does not ask"
shot from-half; tick Lead; click 'button "Install"'
until_true 300 installed || die "it does not go on"
until_true 30 running; sleep 8; models; quit; grep '^==' $home/install.log
[ ! -e $home/models/$lead ] && [ -e $home/models/$drums ] && [ -e $home/models/$guitar ] || die "not the models that were chosen"

say "as downloaded from a browser, and allowed by the user; quit in the middle, and again"
from_browser() { ditto /tmp/kept.app "$2"; xattr -r -w com.apple.quarantine "$1;$(printf %x "$(date +%s)");Safari;12345678-1234-1234-1234-123456789012" "$2"; }
app=/tmp/allowed/zitto.app; home=/tmp/zitto-half
from_browser 01c3 $app
open -n --env ZITTO_HOME=$home $app; asked || die "it does not ask"; tick Lead; click 'button "Install"'
libraries() { grep -q "Step 2" $home/install.log && [ "$(du -sm $home/cache | cut -f1)" -gt 200 ]; }
until_true 60 libraries || die "the app that was allowed does not install"
shot half; du -sm $home/cache; quit
open -n --env ZITTO_HOME=$home $app; asked || die "it does not ask"; ticks
[ "$(ticks)" = "0 1 1 1" ] || die "the choice was not remembered"
click 'button "Install"'
until_true 120 at_models || die "it does not go on"
grep '^==' $home/install.log; sleep 5; quit
ls $home $home/models; [ ! -e $home/cache ] || die "the temporary files are still there"
say "and again"
open -n --env ZITTO_HOME=$home $app; asked || die "it does not ask"; click 'button "Install"'
until_true 120 installed || die "it does not go on"; grep '^==' $home/install.log; until_true 30 running; sleep 8; quit

say "what macOS says the first time"
kill $watching
from_browser 0083 /tmp/q/zitto.app
spctl --assess -vv /tmp/q/zitto.app
(open /tmp/q/zitto.app; true) & sleep 12; shot quarantine
say "done"
