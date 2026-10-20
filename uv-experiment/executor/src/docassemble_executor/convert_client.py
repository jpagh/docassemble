#!/usr/bin/python3
"""OS unoconvert CLI with a live caller held across its info/convert requests."""
from pathlib import Path
import os
import sys
from xmlrpc.client import ServerProxy

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from docassemble_executor.capabilities import Capabilities, HEADER, current  # noqa: E402


def main():
    from unoserver import client
    state = Path(os.environ.get('DA_MANAGED_STATE_DIR', '/var/lib/docassemble/managed'))
    with Capabilities(state).accept(current()) as caller:
        if caller is None:
            print('managed conversion admission is closed', file=sys.stderr)
            return 75

        def proxy(*args, **kwargs):
            kwargs['headers'] = [(HEADER, caller.name)]
            return ServerProxy(*args, **kwargs)

        client.ServerProxy = proxy
        client.converter_main()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
