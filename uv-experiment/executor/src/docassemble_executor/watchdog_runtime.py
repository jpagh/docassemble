"""Fixed observer startup receipt, emitted only after its imports succeed."""
import json
import os
from pathlib import Path

from docassemble.webapp.watchdog import main

if __name__ == '__main__':
    pid = os.getpid()
    start = (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[-1].split()[19]
    path = Path(os.environ['DA_MANAGED_STATE_DIR']) / 'health' / 'watchdog.json'
    path.write_text(json.dumps({'pid': pid, 'start': start}))
    main()
