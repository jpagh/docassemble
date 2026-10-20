#!/bin/sh
# Image-owned wrapper records every real pinned-uv invocation for verification.
# This diagnostic log is not lifecycle authority (builds can write their log).
printf '%s\t%s\n' "$(id -u)" "$*" >> /var/log/docassemble-uv-invocations.log
exec /usr/local/libexec/uv-pinned "$@"
