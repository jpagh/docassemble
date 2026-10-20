"""Disposable fixture-image API-writer ownership and root-reader/startup proof."""
import json
import os
import subprocess


SCRIPT = r'''set -euo pipefail
python=/usr/share/docassemble/executor-venv/bin/python
runuser -u www-data -- "$python" - <<'PY'
import io
import os
from pathlib import Path
from docassemble.base.managed_artifacts import file_digest, retain_artifact
root = Path(os.environ['DA_MANAGED_ARTIFACT_ROOT'])
assert os.getuid() == int(os.environ['DA_MANAGED_ARTIFACT_UID']) == 33
assert root.stat().st_uid == 33 and root.stat().st_mode & 0o777 == 0o700
source = io.BytesIO(b'private-api-writer-artifact')
reference = file_digest(source)
path = retain_artifact(root, reference, source)
assert path.stat().st_uid == 33 and path.stat().st_mode & 0o777 == 0o600
print('PASS: API writer UID 33 retained private artifact')
PY
"$python" - <<'PY'
import os
from pathlib import Path
from docassemble.base.managed_artifacts import verify_artifact
from docassemble.base.managed_preparation import PreparationError
root = Path(os.environ['DA_MANAGED_ARTIFACT_ROOT'])
reference = next(root.iterdir()).name
assert os.getuid() == 0 and verify_artifact(root, reference).read_bytes() == b'private-api-writer-artifact'
for uid in (None, '0'):
    if uid is None:
        os.environ.pop('DA_MANAGED_ARTIFACT_UID')
    else:
        os.environ['DA_MANAGED_ARTIFACT_UID'] = uid
    try:
        verify_artifact(root, reference)
    except PreparationError:
        pass
    else:
        raise AssertionError('root reader bypassed configured writer ownership')
print('PASS: root reader verified configured API writer; missing/wrong UID refused')
PY
mkdir /tmp/startup-proof
printf '#!/bin/bash\necho independent "$@"\n' > /tmp/startup-proof/da-executor
printf '#!/bin/bash\nexec /usr/share/docassemble/executor-venv/bin/python "$@"\n' > /tmp/startup-proof/python
chmod 755 /tmp/startup-proof/*
output=$(DA_EXECUTOR_ENTRYPOINT=/tmp/startup-proof/da-executor bash -c 'source /opt/docassemble-fixture/managed-startup.sh')
test "$output" = 'independent boot'
if DA_MANAGED_ARTIFACT_ROOT='' DA_EXECUTOR_ENTRYPOINT=/tmp/startup-proof/da-executor bash -c 'source /opt/docassemble-fixture/managed-startup.sh'; then
    echo 'startup accepted missing artifact root' >&2
    exit 1
fi
echo 'PASS: image managed startup validates explicit artifact root/UID; no default'
'''


if __name__ == '__main__':
    image = os.environ.get('PROOF_IMAGE', 'da-uv-09b:local')
    identity = subprocess.check_output(['docker', 'image', 'inspect', image, '--format', '{{.Id}}'], text=True).strip()
    subprocess.run(['docker', 'run', '--rm', '--name', 'da-09b-artifact-wiring', '--entrypoint', '/bin/bash',
                    image, '-c', SCRIPT], check=True)
    print(json.dumps({'image': image, 'identity': identity, 'writer_uid': 33,
        'restricted_artifact_store': True, 'root_reads': True, 'startup_checked': True}, sort_keys=True))
