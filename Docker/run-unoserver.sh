#!/bin/bash
source "$(dirname "${BASH_SOURCE[0]}")/managed-consumer.sh" conversion || exit 75

export DA_UNOSERVER="/usr/bin/unoserver"

set -- $LOCALE
export LANG=$1

export HOME=/var/www

${DA_UNOSERVER}
