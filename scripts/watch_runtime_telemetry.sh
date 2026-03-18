#!/usr/bin/env bash

set -euo pipefail

BASE_URL="${OWUI_BASE_URL:-http://127.0.0.1:8080}"
TOKEN="${OWUI_TOKEN:-}"
INTERVAL="${RUNTIME_TELEMETRY_INTERVAL:-2}"
LIMIT="${RUNTIME_TELEMETRY_LIMIT:-40}"
COMMAND="${1:-watch}"
PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
	PYTHON_BIN="$(command -v python3 || command -v python)"
fi

if [[ -z "${TOKEN}" ]]; then
	echo "OWUI_TOKEN is required." >&2
	exit 1
fi

request() {
	local method="$1"
	local path="$2"

	curl -fsS \
		-X "${method}" \
		-H "Accept: application/json" \
		-H "Content-Type: application/json" \
		-H "Authorization: Bearer ${TOKEN}" \
		"${BASE_URL}/api/v1/analytics${path}"
}

render_snapshot() {
	"${PYTHON_BIN}" - <<'PY'
import json
import os
from datetime import datetime

data = json.loads(os.environ["RUNTIME_TELEMETRY_JSON"])

def fmt_ts(value):
    if not value:
        return "-"
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")

def compact(value, size=12):
    if not value:
        return "-"
    value = str(value)
    return value if len(value) <= size else value[:size] + "..."

os.system("clear")
print("Runtime Telemetry")
print("=================")
print(
    f"state={'running' if data.get('enabled') else 'stopped'}  "
    f"started={fmt_ts(data.get('started_at'))}  "
    f"events={data.get('total_events', 0)}  "
    f"model_activity={data.get('model_activity_count', 0)}  "
    f"fallbacks={data.get('fallback_count', 0)}"
)

kind_counts = data.get("kind_counts") or {}
if kind_counts:
    rendered = "  ".join(f"{key}:{value}" for key, value in sorted(kind_counts.items()))
    print(f"kinds={rendered}")
print()

print("Recent messages")
print("---------------")
messages = data.get("recent_messages") or []
if not messages:
    print("(none)")
else:
    for item in messages[:12]:
        models = ",".join(item.get("models") or []) or "-"
        tasks = ",".join(item.get("task_kinds") or []) or "-"
        print(
            f"{compact(item.get('chat_id'))}/{compact(item.get('message_id'))}  "
            f"events={item.get('event_count', 0)}  "
            f"model_activity={item.get('model_activity_count', 0)}  "
            f"fallbacks={item.get('fallback_count', 0)}  "
            f"models={models}  tasks={tasks}  "
            f"last={fmt_ts(item.get('last_seen_at'))}"
        )
print()

print("Recent events")
print("-------------")
events = list(reversed(data.get("recent_events") or []))
if not events:
    print("(none)")
else:
    for event in events[:20]:
        payload = event.get("payload") or {}
        summary_parts = [
            payload.get("phase"),
            payload.get("operation"),
            payload.get("task_kind"),
            payload.get("tool"),
            payload.get("status"),
        ]
        summary = " | ".join(str(part) for part in summary_parts if part) or event.get("kind")
        route = " | ".join(
            str(part)
            for part in [
                payload.get("actor"),
                payload.get("model_id"),
                payload.get("active_model_id"),
                payload.get("selected_via"),
                payload.get("route_source"),
            ]
            if part
        ) or "-"
        duration = payload.get("duration_ms")
        duration_text = f"{duration}ms" if duration is not None else "-"
        print(
            f"{fmt_ts(event.get('ts'))}  #{event.get('seq')}  "
            f"{summary}  duration={duration_text}  route={route}  "
            f"chat={compact(event.get('chat_id'))} msg={compact(event.get('message_id'))}"
        )
PY
}

case "${COMMAND}" in
	start)
		request POST "/runtime/telemetry/start" | "${PYTHON_BIN}" -m json.tool
		;;
	stop)
		request POST "/runtime/telemetry/stop" | "${PYTHON_BIN}" -m json.tool
		;;
	clear)
		request POST "/runtime/telemetry/clear" | "${PYTHON_BIN}" -m json.tool
		;;
	watch)
		trap 'echo; echo "Stopping watcher."' INT TERM
		while true; do
			RUNTIME_TELEMETRY_JSON="$(request GET "/runtime/telemetry?limit=${LIMIT}")" render_snapshot
			sleep "${INTERVAL}"
		done
		;;
	*)
		echo "Usage: $0 [watch|start|stop|clear]" >&2
		exit 1
		;;
esac
