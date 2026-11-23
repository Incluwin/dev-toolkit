#!/usr/bin/env bash
# Before a chat-02 or chat-04 take: forget the demo phrases so the bot has to judge them again,
# then have the running server reload its phrase memory.
set -euo pipefail
cd "${BOTSTORY_ROOT:?set BOTSTORY_ROOT}/FriendServer"
PW=$(grep -E '^DB_PASS(WORD)?=' .env | head -1 | cut -d= -f2- | tr -d '\r"')
TOK=$(grep -E '^ADMIN_CONTROL_TOKEN=' .env | cut -d= -f2- | tr -d '\r"')
"/c/Program Files/MySQL/MySQL Server 8.0/bin/mysql.exe" -uroot -p"$PW" -P3306 -h127.0.0.1 cosmic \
  -e "DELETE FROM living_learned_phrase WHERE normalized_text LIKE '%plenty%ease off%' OR normalized_text LIKE '%head back over%' OR normalized_text LIKE '%come get buffed%'; SELECT ROW_COUNT() AS still_matching FROM DUAL;" 2>&1 | grep -v "Using a password"
curl -s -m 10 -X POST -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" -d '{}' \
  http://127.0.0.1:8480/actions/living-reload
echo
