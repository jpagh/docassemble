#!/bin/bash
# Certificate/control hooks are unsupported by the managed experimental topology.
da_gate="${DA_ROOT:-/usr/share/docassemble}/webapp/managed-consumer.sh"
[ -f "$da_gate" ] || da_gate="$(dirname "${BASH_SOURCE[0]}")/managed-consumer.sh"
source "$da_gate" certbot || exit 75
# The experimental image replaces the inherited runtime entrypoint before its
# Python shebang can import Certbot. Unadopted installs keep the original tool.
if [ -x "${BASH_SOURCE[0]}.unmanaged" ]; then
    exec "${BASH_SOURCE[0]}.unmanaged" "$@"
fi
exec certbot "$@"
