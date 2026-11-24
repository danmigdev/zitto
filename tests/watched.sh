#!/bin/bash
# watched.sh SECONDS COMMAND...  Runs a command saying how much memory it uses, and stops it when it
# takes too long or fills the memory: a machine without memory left loses its log too.
limit=$1; shift
"$@" 2>&1 & pid=$!
while kill -0 $pid 2>/dev/null; do
  sleep 10
  swap=$(sysctl -n vm.swapusage | awk '{print int($6)}')
  echo "    [$SECONDS s] MB in use and CPU: $(ps -o rss=,%cpu= -p $pid | awk '{print int($1 / 1024), $2}'), swap $swap MB"
  if [ $SECONDS -gt "$limit" ] || [ "$swap" -gt 5000 ]; then
    echo "stopped: more than $limit s, or too much memory"
    kill -ABRT $pid; sleep 5; kill -9 $pid 2>/dev/null
    exit 1
  fi
done
wait $pid
