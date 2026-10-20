#!/bin/bash
source "$(dirname "${BASH_SOURCE[0]}")/managed-consumer.sh" watchdog || exit 75

export DA_ROOT="${DA_ROOT:-/usr/share/docassemble}"
export DA_DEFAULT_LOCAL="local3.14"

export DA_ACTIVATE="${DA_PYTHON:-${DA_ROOT}/${DA_DEFAULT_LOCAL}}/bin/activate"
source "${DA_ACTIVATE}"

python -m docassemble.webapp.watchdog &

WATCHDOGPID=%1

function stopfunc {
    kill -SIGTERM $WATCHDOGPID
    exit 0
}

trap stopfunc SIGINT SIGTERM

wait $WATCHDOGPID
