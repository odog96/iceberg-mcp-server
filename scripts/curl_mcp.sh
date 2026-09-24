#!/usr/bin/env bash
# Check an MCP server using nothing but curl (no MCP client library).
#
#   scripts/curl_mcp.sh <url> [health | list | call <tool> [json-args]]
#
#   scripts/curl_mcp.sh https://my-app.example.com health
#   scripts/curl_mcp.sh https://my-app.example.com list
#   scripts/curl_mcp.sh https://my-app.example.com call get_schema
#   scripts/curl_mcp.sh https://my-app.example.com call execute_query '{"query":"select 1"}'
#
# For a server that checks tokens, provide one:  TOKEN=<token>  or  TOKEN_FILE=<path>
#
# An MCP server talks over HTTP POST to /mcp and needs a short conversation first:
#   1. "initialize"                  -> the server replies and gives us a session id (header)
#   2. "notifications/initialized"   -> we confirm
#   3. "tools/list" or "tools/call"  -> the real request
# Every request must accept both JSON and event-stream replies. This script does all three steps.
set -euo pipefail

BASE="${1:?usage: curl_mcp.sh <url> [health | list | call <tool> [json-args]]}"
shift || true
BASE="${BASE%/}"
BASE="${BASE%/mcp}"
CMD="${1:-list}"
[ $# -gt 0 ] && shift

if [ "$CMD" = "health" ]; then
  curl -sS -m 30 -w '\nHTTP %{http_code}\n' "$BASE/health"
  exit 0
fi

if [ -n "${TOKEN_FILE:-}" ]; then TOKEN="$(tr -d '[:space:]' < "$TOKEN_FILE")"; fi
AUTH=()
if [ -n "${TOKEN:-}" ]; then AUTH=(-H "Authorization: Bearer $TOKEN"); fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
HDR="$TMP/headers"
BODY="$TMP/body"
SID=""

# post <json>  -> prints the HTTP status code; response headers/body are left in $HDR / $BODY
post() {
  local extra=()
  if [ -n "$SID" ]; then extra=(-H "Mcp-Session-Id: $SID"); fi
  curl -sS -m 60 -D "$HDR" -o "$BODY" -w '%{http_code}' -X POST "$BASE/mcp" \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    ${extra[@]+"${extra[@]}"} ${AUTH[@]+"${AUTH[@]}"} -d "$1"
}

# The reply is either plain JSON or an event stream ("data: {...}" lines). Print the JSON.
show() {
  local json
  json="$(grep -i '^data:' "$BODY" | sed 's/^[dD][aA][tT][aA]: *//' || true)"
  if [ -z "$json" ]; then json="$(cat "$BODY")"; fi
  if command -v python3 >/dev/null 2>&1; then
    printf '%s\n' "$json" | python3 -m json.tool 2>/dev/null || printf '%s\n' "$json"
  else
    printf '%s\n' "$json"
  fi
}

fail() {  # fail <step> <status>
  echo "FAILED at step '$1': HTTP $2" >&2
  case "$2" in
    401) echo "  The server refused the request: no token, or an invalid/expired one (set TOKEN or TOKEN_FILE)." >&2 ;;
    403) echo "  The token was understood but is not allowed (wrong permission/scope)." >&2 ;;
    404) echo "  Nothing at $BASE/mcp: check the URL." >&2 ;;
  esac
  [ -s "$BODY" ] && { echo "--- response body ---" >&2; head -c 600 "$BODY" >&2; echo >&2; }
  exit 1
}

# 1. initialize
INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl_mcp.sh","version":"1"}}}'
code="$(post "$INIT")"
[ "$code" = "200" ] || fail initialize "$code"
SID="$(grep -i '^mcp-session-id:' "$HDR" | tr -d '\r' | awk '{print $2}' || true)"

# 2. initialized notification (expects HTTP 202 Accepted)
code="$(post '{"jsonrpc":"2.0","method":"notifications/initialized"}')"
case "$code" in 200|202|204) ;; *) fail notifications/initialized "$code" ;; esac

# 3. the real request
case "$CMD" in
  list)
    code="$(post '{"jsonrpc":"2.0","id":2,"method":"tools/list"}')"
    ;;
  call)
    TOOL="${1:?usage: call <tool> [json-args]}"
    ARGS="${2:-{\}}"
    code="$(post "$(printf '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"%s","arguments":%s}}' "$TOOL" "$ARGS")")"
    ;;
  *)
    echo "unknown command '$CMD' (use: health | list | call <tool> [json-args])" >&2
    exit 2
    ;;
esac
[ "$code" = "200" ] || fail "$CMD" "$code"
show
