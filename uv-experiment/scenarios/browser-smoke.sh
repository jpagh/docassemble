#!/bin/bash
# Optional real-browser supplement. First run run-proof.sh --keep-containers.
# Requires chrome-devtools and Chrome; uses its own profile and fixture container.
set -euo pipefail

EXPERIMENT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${BROWSER_PROOF_IMAGE:-da-uv-proof-snapshot:local}"
CHROME_BIN="${CHROME_BIN:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
EVIDENCE="${BROWSER_EVIDENCE_DIR:-$EXPERIMENT_DIR/evidence/run-browser-$(date -u +%Y%m%d-%H%M%S)}"
PROFILE=$(mktemp -d)
CHROME_PID=""
cleanup() {
  if [ -n "$CHROME_PID" ]; then
    chrome-devtools kill-daemon --force >/dev/null 2>&1 || true
    kill "$CHROME_PID" 2>/dev/null || true
    for _ in $(seq 1 20); do kill -0 "$CHROME_PID" 2>/dev/null || break; sleep 0.25; done
    kill -9 "$CHROME_PID" 2>/dev/null || true
    wait "$CHROME_PID" 2>/dev/null || true
  fi
  rm -rf "$PROFILE"
  docker rm -f da-uv-browser-proof >/dev/null 2>&1 || true
}
trap cleanup EXIT
command -v chrome-devtools >/dev/null
[ -x "$CHROME_BIN" ]
docker image inspect "$IMAGE" >/dev/null
mkdir -p "$EVIDENCE"
docker image inspect "$IMAGE" --format '{{.Id}}' > "$EVIDENCE/snapshot-image-id.txt"
"$CHROME_BIN" --version > "$EVIDENCE/chrome-version.txt"
docker rm -f da-uv-browser-proof >/dev/null 2>&1 || true
docker run -d --name da-uv-browser-proof -p 127.0.0.1:18082:80 "$IMAGE" --force recovery >/dev/null
python3 - <<'PY'
import time
import urllib.error
import urllib.request
end = time.monotonic() + 180
while time.monotonic() < end:
    try:
        with urllib.request.urlopen('http://127.0.0.1:18082/user/sign-in', timeout=5) as response:
            if response.status == 200:
                break
    except (OSError, urllib.error.URLError):
        pass
    time.sleep(1)
else:
    raise SystemExit('Browser fixture did not become available')
PY
# Switch the CLI daemon to our throwaway browser, never use personal cookies.
chrome-devtools kill-daemon --force >/dev/null 2>&1 || true
"$CHROME_BIN" --headless=new --remote-debugging-port=0 --user-data-dir="$PROFILE" \
  --no-first-run --no-default-browser-check about:blank > "$EVIDENCE/chrome-startup.log" 2>&1 &
CHROME_PID=$!
for _ in $(seq 1 60); do
  [ -f "$PROFILE/DevToolsActivePort" ] && break
  kill -0 "$CHROME_PID" 2>/dev/null || exit 1
  sleep 0.5
done
[ -f "$PROFILE/DevToolsActivePort" ]
CD=(chrome-devtools --user-data-dir "$PROFILE" --page 0)
"${CD[@]}" list-pages
"${CD[@]}" navigate http://127.0.0.1:18082/recovery
"${CD[@]}" fill '#email' admin@example.com
"${CD[@]}" fill '#password' FixtureAdmin101! >/dev/null
"${CD[@]}" click 'button[name="submit"]'
"${CD[@]}" wait-for 'Restricted recovery administration'
"${CD[@]}" screenshot --output "$EVIDENCE/recovery.png"
# Tall viewport keeps the large editor and submit control within the CLI's
# coordinate-click surface; this is not a product layout requirement.
"${CD[@]}" navigate http://127.0.0.1:18082/config --viewport 1280x6000
"${CD[@]}" wait-for 'Configuration'
"${CD[@]}" click '.cm-content'
"${CD[@]}" press-key 'Meta+ArrowDown'
"${CD[@]}" type-text $'\n# browser configuration smoke\n'
"${CD[@]}" click 'button[name="submit"]'
"${CD[@]}" wait-for 'Restricted recovery administration'
"${CD[@]}" navigate http://127.0.0.1:18082/logs
"${CD[@]}" wait-for 'Logs'
"${CD[@]}" console --type error --type exception
"${CD[@]}" evaluate "fetch('/health_status').then(r=>r.json()).then(s=>{if(s.normal_ready!==false||s.recovery_available!==true)throw Error('incorrect recovery readiness');return {normal_ready:s.normal_ready,recovery_available:s.recovery_available}})"
docker exec da-uv-browser-proof grep -q '^# browser configuration smoke' /usr/share/docassemble/config/config.yml
printf '%s\n' 'PASS: real browser login, CodeMirror editing/save, recovery page, and logs; normal readiness remains false.' | tee "$EVIDENCE/result.txt"
