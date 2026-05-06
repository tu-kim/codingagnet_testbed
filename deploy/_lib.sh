# shellcheck shell=bash
# Common helpers for deploy/launch_*.sh — start a process as a new session
# leader (so its PID == PGID) and stop the entire process group on teardown.
# This handles wrappers like `opencode serve` that exec child workers
# (`.opencode`, vLLM TP/PP shards) which would otherwise survive a plain
# `kill <wrapper-pid>`.

# spawn_pgid <pidfile> <logfile> [env=val ...] -- <cmd...>
# All `key=val` tokens before the literal `--` are exported into the child env.
spawn_pgid() {
  local pidf="$1" log="$2"; shift 2
  local -a envs=()
  while [[ $# -gt 0 && "$1" != "--" ]]; do
    envs+=("$1"); shift
  done
  [[ "${1:-}" == "--" ]] && shift

  # Spawn under a new session via setsid. The wrapper bash records its OWN
  # PID ($$) into the pidfile, then `exec`s the target command — since exec
  # preserves PID, that PID is the session leader and equals the PGID. We
  # cannot rely on `$!` because bash + setsid + backgrounding can introduce an
  # extra fork that desyncs $! from the actual leader.
  setsid bash -c '
    echo "$$" > "$1"
    shift
    exec env "$@"
  ' _ "$pidf" "${envs[@]}" "$@" </dev/null >"$log" 2>&1 &
  disown 2>/dev/null || true
}

# stop_pgid <pidfile> [label]
stop_pgid() {
  local pidf="$1" label="${2:-$(basename "$1" .pid)}"
  [[ -f "$pidf" ]] || { echo "[skip] no $pidf"; return 0; }
  local pgid
  pgid=$(cat "$pidf" 2>/dev/null || true)
  rm -f "$pidf"
  [[ -z "$pgid" ]] && return 0
  if ! kill -0 "$pgid" 2>/dev/null; then
    echo "[skip] $label already gone"
    return 0
  fi
  echo "[stop] $label pgid=$pgid (SIGTERM group)"
  kill -TERM -- "-$pgid" 2>/dev/null || true
  # grace period
  for _ in $(seq 1 40); do
    kill -0 "$pgid" 2>/dev/null || return 0
    sleep 0.25
  done
  echo "[stop] $label still alive after 10s; SIGKILL group"
  kill -KILL -- "-$pgid" 2>/dev/null || true
}
