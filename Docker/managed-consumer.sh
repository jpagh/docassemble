#!/bin/bash
# Source before application activation. Unqualified consumers fail closed.
# This is a crash-safety gate, not a sandbox for privileged manual commands.
DA_MANAGED_STATE_DIR="${DA_MANAGED_STATE_DIR:-/var/lib/docassemble/managed}"
da_consumer_managed="${DA_UV_MANAGED:-false}"
# A missing record below an unsearchable/invalid parent is not proof of legacy mode.
da_consumer_path="${DA_MANAGED_STATE_DIR%/}"
while [ -n "$da_consumer_path" ]; do
    if { [ -e "$da_consumer_path" ] || [ -L "$da_consumer_path" ]; } &&
       { [ ! -d "$da_consumer_path" ] || [ ! -x "$da_consumer_path" ]; }; then
        da_consumer_managed=true
        break
    fi
    case "$da_consumer_path" in
        /|.) break ;;
        */*) da_consumer_path="${da_consumer_path%/*}"; da_consumer_path="${da_consumer_path:-/}" ;;
        *) da_consumer_path=. ;;
    esac
done
for da_consumer_record in desired.json applied.json lifecycle.json initialized.json admission.json; do
    if [ -e "$DA_MANAGED_STATE_DIR/$da_consumer_record" ] || [ -L "$DA_MANAGED_STATE_DIR/$da_consumer_record" ]; then
        da_consumer_managed=true
    fi
done
if [ "$da_consumer_managed" = true ]; then
    if [ "${DA_UV_MANAGED:-false}" != true ]; then
        echo "managed consumer: durable managed evidence exists or cannot be inspected; flag-off legacy launch refused" >&2
        exit 75
    fi
    da_consumer_executor="${DA_EXECUTOR_ENTRYPOINT:-/usr/share/docassemble/executor-venv/bin/da-executor}"
    case "${1:-}" in
        watchdog|celery|celerysingle|websockets|nltk|conversion|cron|mail|cron-hourly|cron-daily|cron-weekly|cron-monthly|development-install|reload-web)
            if [ ! -x "$da_consumer_executor" ]; then
                echo "managed consumer: independent executor is unavailable" >&2
                exit 75
            fi
            case "$1" in
                development-install) exec "$da_consumer_executor" development-install --source "${2:?development source required}" ;;
                reload-web) exec "$da_consumer_executor" reload-web ;;
                watchdog) exec "$da_consumer_executor" run-watchdog ;;
                celery) exec "$da_consumer_executor" run-worker --kind normal ;;
                celerysingle) exec "$da_consumer_executor" run-worker --kind single ;;
                websockets|nltk|conversion) exec "$da_consumer_executor" run-service --kind "$1" ;;
                cron) exec "$da_consumer_executor" run-helper --kind cron --cron-type "${2:-cron_daily}" ;;
                mail) exec "$da_consumer_executor" run-helper --kind mail ;;
                cron-*) exec "$da_consumer_executor" run-schedule --kind "${1#cron-}" ;;
            esac
            ;;
    esac
    echo "managed consumer: ${1:-unknown} is unsupported in managed mode; launch refused" >&2
    # EX_TEMPFAIL also tells Exim to retain/defer a pipe delivery, not discard it.
    exit 75
fi
unset da_consumer_managed da_consumer_record da_consumer_path
