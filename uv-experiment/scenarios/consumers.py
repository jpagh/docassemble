#!/usr/bin/env python3
"""04b1: real Supervisor, guarded finite jobs, helper fences and drain abort."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path

from harness import ContainerHttp, Docker, Evidence, check
from maintenance import wait

NAME = 'da-uv-consumer-proof'
STATE = '/var/lib/docassemble/managed'
AUDIT = '/var/log/docassemble-uv-invocations.log'
CONFIG = '/var/run/docassemble/managed-supervisor/supervisord.conf'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', default='da-uv-consumers:local')
    parser.add_argument('--evidence-dir')
    args = parser.parse_args()
    root = Path(args.evidence_dir) if args.evidence_dir else Path(__file__).resolve().parents[1] / 'consumers' / 'evidence' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    evidence, results = Evidence(root), []
    docker = Docker(evidence)
    http = ContainerHttp(docker, NAME)

    def status():
        return json.loads(http.body('/status.json'))

    def capture(label):
        evidence.write(label + '/logs.txt', docker.logs(NAME))
        evidence.write_json(label + '/status.json', status())
        evidence.write(label + '/processes.txt', docker.exec(NAME, ['ps', '-eo', 'user,pid,ppid,args']).stdout)
        evidence.write(label + '/supervisor.txt', docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'status']).stdout)
        for file in ('admission.json', 'lifecycle.json', 'applied.json'):
            evidence.write(label + '/' + file, docker.read_file(NAME, STATE + '/' + file))
        evidence.write(label + '/uv-invocations.txt', docker.read_file(NAME, AUDIT))
        results.append({'scenario': label, 'ok': True})
        evidence.note('PASS ' + label)

    def start_job(pool):
        docker.exec_shell(NAME, 'rm -f /tmp/consumer-started /tmp/consumer-release /tmp/consumer-completed')
        job = pool.submit(docker.exec, NAME, ['da-executor', 'run-consumer', '--', RUNTIME, '-c', '''
from pathlib import Path
import time
import docassemble.base
Path('/tmp/consumer-started').touch()
while not Path('/tmp/consumer-release').exists():
    time.sleep(0.05)
Path('/tmp/consumer-completed').write_text('accepted runtime job completed')
'''], user='www-data', check_result=False)
        wait(lambda: docker.exec(NAME, ['test', '-f', '/tmp/consumer-started'], check_result=False).returncode == 0)
        return job

    def apply(pool, timeout):
        return pool.submit(docker.exec, NAME, ['da-executor', 'apply-runtime', '--revision', 'r1'],
                           env={'DA_DRAIN_TIMEOUT': str(timeout)}, check_result=False)

    try:
        evidence.write('image-id.txt', docker.run(['image', 'inspect', args.image, '--format', '{{.Id}}']).stdout)
        docker.start(NAME, args.image)
        layout = docker.runtime_layout(NAME)
        RUNTIME = layout.python
        wait(lambda: http.status('/health_check?ready=1') == 200, 900)
        check(status()['consumer_qualification']['complete'] is True and status()['normal_ready'], 'full integration is not ready')
        check(docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'status']).returncode == 0, 'independent Supervisor unavailable')
        capture('normal-supervised-partial-service')
        with ThreadPoolExecutor(max_workers=3) as pool:
            before = docker.read_file(NAME, AUDIT)
            applied = docker.read_file(NAME, STATE + '/applied.json')
            job = start_job(pool)
            operation = apply(pool, 2)
            wait(lambda: status().get('admission', {}).get('mode') == 'draining')
            denied = docker.exec(NAME, ['da-executor', 'run-consumer', '--', '/usr/bin/touch', '/tmp/consumer-denied'],
                                 user='www-data', check_result=False)
            check(denied.returncode == 75, 'manual consumer passed closed admission')
            check(http.status('/health_check') == 200 and http.status('/user/sign-in') == 503, 'liveness/admission confused')
            check(docker.exec(NAME, ['pg_isready', '-q']).returncode == 0, 'PostgreSQL stopped')
            check(docker.exec(NAME, ['redis-cli', 'ping']).stdout.strip() == 'PONG', 'Redis stopped')
            check(operation.result(timeout=90).returncode == 3, 'job drain timeout did not abort')
            check(docker.read_file(NAME, AUDIT) == before, 'job drain abort invoked uv')
            check(docker.read_file(NAME, STATE + '/applied.json') == applied, 'job drain abort changed applied state')
            check(http.status('/health_check?ready=1') == 200, 'unchanged service did not resume')
            docker.exec(NAME, ['touch', '/tmp/consumer-release'])
            check(job.result(timeout=30).returncode == 0, 'accepted runtime job was destroyed')
            check(docker.read_file(NAME, '/tmp/consumer-completed') == 'accepted runtime job completed', 'job did not finish')
            capture('job-timeout-resumes-unchanged-service')

            before = docker.read_file(NAME, AUDIT)
            job = start_job(pool)
            operation = apply(pool, 20)
            wait(lambda: status().get('admission', {}).get('mode') == 'draining')
            # The actual Supervisor restarts its independent wrapper, not runtime
            # code. Finite work remains accepted, so mutation is still excluded.
            check(docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'restart', 'watchdog']).returncode == 0, 'Supervisor restart failed')
            check(docker.read_file(NAME, AUDIT) == before, 'sync started while a helper job was active')
            for script in ('run-celery.sh', 'run-celery-single.sh', 'run-websockets.sh', 'run-nltk.sh',
                           'run-cron.sh', 'process-email.sh', 'run-unoserver.sh'):
                rejected = docker.exec(NAME, ['bash', '/usr/share/docassemble/webapp/' + script], check_result=False)
                check(rejected.returncode == 75, 'consumer launcher passed closed admission: ' + script)
            for schedule in ('hourly', 'daily', 'weekly', 'monthly'):
                rejected = docker.exec(NAME, ['bash', '/etc/cron.' + schedule + '/docassemble'], check_result=False)
                check(rejected.returncode == 75, 'scheduled launcher passed: ' + schedule)
            docker.exec(NAME, ['touch', '/tmp/consumer-release'])
            check(job.result(timeout=30).returncode == 0, 'helper job did not drain naturally')
            output = operation.result(timeout=300)
            evidence.write('apply-output.txt', output.stdout + output.stderr)
            check(output.returncode == 0, 'controlled apply failed: ' + output.stdout + output.stderr)
            check(len(docker.read_file(NAME, AUDIT).splitlines()) == len(before.splitlines()) + 1, 'apply did not perform one frozen sync')
            check(http.status('/health_check?ready=1') == 200, 'service did not resume after successful job drain')
            capture('natural-job-drain-and-supervisor-maintenance-fences')

        native = docker.exec(NAME, ['/usr/share/docassemble/executor-venv/bin/python', '-m', 'pytest',
                                     '/opt/docassemble-executor/tests/test_consumers.py',
                                     '/opt/docassemble-executor/tests/test_authority.py', '-q', '-p', 'no:cacheprovider'],
                             env={'DA_TEST_REPO': '/opt/da-repo'})
        evidence.write('native-consumer-tests.txt', native.stdout + native.stderr)
        capture('native-autorestart-and-detached-descendant-exclusion')
        docker.restart(NAME)
        wait(lambda: http.status('/health_check?ready=1') == 200, 900)
        capture('unchanged-supervised-container-restart')
        return 0
    except Exception as error:
        evidence.write('failure.txt', str(error) + '\n' + docker.logs(NAME))
        raise
    finally:
        evidence.write_json('results.json', results)
        docker.rm(NAME)


if __name__ == '__main__':
    raise SystemExit(main())
