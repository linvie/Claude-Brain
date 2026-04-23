#!/bin/bash
# PostToolUse / Stop hook: append trajectory event to JSONL log.
# Reads hook event JSON from stdin, writes one JSON line per invocation.
# Failure is silently swallowed (|| true) to never block CC.

set -o pipefail

{
  # Read the hook event from stdin
  EVENT_JSON=$(cat)

  # Require CCBRAIN_TASK_ID
  [ -z "$CCBRAIN_TASK_ID" ] && exit 0

  # Prepare output directory
  TRAJ_DIR="$HOME/.ccbrain/trajectories"
  mkdir -p "$TRAJ_DIR"

  # Use python3 to parse input and produce output in one pass
  echo "$EVENT_JSON" | python3 -c "
import sys, json, datetime, os

try:
    d = json.load(sys.stdin)
except Exception:
    d = {}

entry = {
    'ts': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
    'task_id': os.environ.get('CCBRAIN_TASK_ID', ''),
    'event': d.get('hook_event_name', d.get('event', 'unknown')),
    'tool': d.get('tool_name', d.get('tool', '')),
    'cwd': os.getcwd(),
}

# input_preview: truncate to 500 chars
inp = d.get('tool_input', d.get('input', ''))
if isinstance(inp, dict):
    inp = json.dumps(inp, ensure_ascii=False)
entry['input_preview'] = str(inp)[:500]

# optional fields
ec = d.get('tool_exit_code', d.get('exit_code'))
if ec is not None and ec != '':
    try:
        entry['exit_code'] = int(ec)
    except (ValueError, TypeError):
        entry['exit_code'] = ec

dm = d.get('duration_ms')
if dm is not None and dm != '':
    try:
        entry['duration_ms'] = int(dm)
    except (ValueError, TypeError):
        entry['duration_ms'] = dm

traj_dir = os.path.expanduser('~/.ccbrain/trajectories')
task_id = os.environ.get('CCBRAIN_TASK_ID', 'unknown')
with open(os.path.join(traj_dir, f'{task_id}.jsonl'), 'a') as f:
    f.write(json.dumps(entry, ensure_ascii=False) + '\n')
" 2>/dev/null

} || true

exit 0
