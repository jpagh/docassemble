"""Tight real-SMTP regression for the managed incoming handler bootstrap."""
import json
import os
from pathlib import Path
import smtplib
import subprocess
import time
import uuid

before = len(Path('/var/log/exim4/mainlog').read_text())
subject = 'bootstrap-' + uuid.uuid4().hex
with smtplib.SMTP('127.0.0.1') as smtp:
    smtp.sendmail('sender@helper.test', 'helper@helper.test',
                  'From: sender@helper.test\nTo: helper@helper.test\nSubject: ' + subject + '\n\nactual body')
for _attempt in range(10):
    command = ['/usr/share/docassemble/local3.12/bin/python', '/opt/docassemble-fixture/helper_client.py', 'messages']
    if os.geteuid() == 0:
        command = ['/usr/sbin/runuser', '-u', 'www-data', '--', *command]
    result = subprocess.run(command, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                            capture_output=True, text=True)
    records = json.loads(result.stdout.split('PROOF_JSON=')[-1])
    message = next((item for item in records if item['subject'] == subject), None)
    if message:
        assert [part['index'] for part in message['attachments']] == [0, 1], 'Header/body attachments are not linked to the actual message'
        assert bytes.fromhex(message['attachments'][1]['data']) == b'actual body\n\n', 'Actual incoming body was not preserved'
        event = Path('/tmp/da-helper-proof/email-event-' + subject + '.completed')
        if not event.exists():
            time.sleep(0.5)
            continue
        received = json.loads(event.read_text())
        assert received == {'uid': 33, 'subject': subject, 'body': 'actual body\n\n'}, 'Actual incoming-email action did not receive the real mail as www-data'
        print('PASS actual SMTP handler persisted headers/body and executed incoming_email ' + subject)
        break
    log = Path('/var/log/exim4/mainlog').read_text()[before:]
    assert '** |/usr/share/docassemble/webapp/process-email.sh' not in log, 'Actual incoming handler bounced instead of persisting mail'
    time.sleep(0.5)
else:
    raise AssertionError('Actual incoming handler did not persist/dispatch mail; message=' + str(message) +
                         '; events=' + str(list(Path('/tmp/da-helper-proof').glob('email-event-*'))) +
                         '; worker=' + Path('/usr/share/docassemble/log/managed-worker-normal.log').read_text()[-2500:])
