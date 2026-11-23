#!/usr/bin/env bash
# Film takes in order and finish each one; stops at the first failed take.
#   bash film.sh class-02-paladin class-03-dark-knight ...
# Needs BOTSTORY_ROOT (the folder that holds FriendServer) and MAPLE_CAMERA_CHARACTER; PY overrides the python.
set -uo pipefail
CUA="$(cd "$(dirname "$0")/.." && pwd)"
cd "${BOTSTORY_ROOT:?set BOTSTORY_ROOT}"
PY="${PY:-python}"
VIDEO="FriendServer/BotStory Server/docs/agents/evidence/video/2026-09-26"
for take in "$@"; do
  echo "=== $take $(date +%H:%M:%S)"
  if [ "$take" = "chat-02-asks-then-learns" ] || [ "$take" = "chat-04-party-rebuff" ]; then
    bash "$CUA/takes/forget-demo-phrases.sh" > /dev/null
  fi
  if ! "$PY" "$CUA/maple_client.py" run "$CUA/takes/$take.take"; then
    echo "=== FAILED $take"
    exit 1
  fi
  "$PY" "$CUA/post.py" clip "$VIDEO/raw/$take.mp4" "$VIDEO/$take.mp4" || { echo "=== POST FAILED $take"; exit 1; }
  # What a viewer sees: no GM/debug text in chat, and every showcase callout the server logged made it on screen.
  "$PY" "$CUA/video_audit.py" check "$VIDEO/$take.mp4" || { echo "=== AUDIT FAILED $take"; exit 1; }
done
echo "=== all done $(date +%H:%M:%S)"
