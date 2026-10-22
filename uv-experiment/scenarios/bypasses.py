#!/usr/bin/env python3
"""04b3a: real legacy shell/module, installer/intent and queued-task fences."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path

from harness import ContainerHttp, Docker, Evidence, check
from maintenance import wait

NAME = 'da-uv-bypass-proof'
STATE = '/var/lib/docassemble/managed'
SCRIPT = '/opt/docassemble-fixture/bypass_runtime.py'
CONFIG = '/var/run/docassemble/managed-supervisor/supervisord.conf'
AUDIT = '/var/log/docassemble-uv-invocations.log'
SHELLS = ('reset.sh', 'update.sh', 'sync.sh', 'run-uwsgi.sh', 'run-apache.sh',
          'run-nginx.sh', 'restart-nginx.sh', 'restart-post-logrotate.sh', 'run-certbot.sh',
          'run-postgresql.sh', 'run-redis.sh', 'run-syslogng.sh', 'run-rabbitmq.sh', 'run-nascent.sh')
MODULES = tuple('docassemble.webapp.' + name for name in (
    'update', 'restart', 'create_tables', 'cron', 'cleanup_sessions', 'process_email', 'socketserver', 'watchdog',
)) + ('docassemble.base.pattern_server', 'docassemble.base.read_config')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', default='da-uv-bypasses:local')
    parser.add_argument('--evidence-dir')
    args = parser.parse_args()
    root = Path(args.evidence_dir) if args.evidence_dir else Path(__file__).resolve().parents[1] / 'bypasses/evidence' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    evidence, results = Evidence(root), []
    docker = Docker(evidence)
    http = ContainerHttp(docker, NAME)

    def status():
        return json.loads(http.body('/status.json'))

    def capture(label):
        evidence.write_json(label + '/status.json', status())
        evidence.write(label + '/logs.txt', docker.logs(NAME))
        evidence.write(label + '/processes.txt', docker.exec(NAME, ['ps', '-eo', 'user,pid,ppid,args']).stdout)
        evidence.write(label + '/supervisor.txt', docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'status']).stdout)
        for file in ('desired.json', 'applied.json', 'lifecycle.json', 'admission.json'):
            evidence.write(label + '/' + file, docker.read_file(NAME, STATE + '/' + file))
        evidence.write(label + '/uv-invocations.txt', docker.read_file(NAME, AUDIT))
        results.append({'scenario': label, 'ok': True})
        evidence.note('PASS ' + label)

    def fences(label):
        before = {file: docker.read_file(NAME, file) for file in (AUDIT, STATE + '/desired.json', STATE + '/applied.json')}
        output = []
        for flag in ('true', 'false'):
            for command in ([['bash', '/usr/share/docassemble/webapp/' + name] for name in SHELLS]
                            + [[RUNTIME, '-m', name] for name in MODULES]
                            + [['certbot', '--version'], ['/usr/share/docassemble/local3.14/bin/certbot', '--version']]):
                result = docker.exec(NAME, command, env={'DA_UV_MANAGED': flag, 'PYTHONDONTWRITEBYTECODE': '1'}, check_result=False)
                output.append(json.dumps({'command': command, 'flag': flag, 'code': result.returncode,
                                          'stdout': result.stdout, 'stderr': result.stderr}))
                check(result.returncode != 0 and ('unsupported in managed mode' in result.stderr
                                                  or 'flag-off legacy launch refused' in result.stderr),
                      'legacy entrypoint passed or failed accidentally: ' + str(command) + result.stderr)
            result = docker.exec(NAME, ['da-executor', 'init-db'], env={'DA_UV_MANAGED': flag}, check_result=False)
            check(result.returncode != 0, 'direct executor init-db bypassed adopted schema gate')
        for name in ('reset', 'update', 'sync', 'nginx', 'apache2', 'uwsgi'):
            result = docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'start', name], check_result=False)
            check(result.returncode != 0 and 'no such process' in result.stdout, 'stock Supervisor control was registered: ' + name)
        check(before == {file: docker.read_file(NAME, file) for file in before}, 'refused entrypoint changed installed intent or invoked uv')
        evidence.write(label + '/entrypoints.jsonl', '\n'.join(output) + '\n')

    def runtime(case='all', *options):
        return docker.exec(NAME, ['da-executor', 'run-consumer', '--', RUNTIME, SCRIPT, '--case', case, *options],
                           check_result=False)

    def job(pool, label):
        prefix = '/tmp/bypass-' + label
        output = pool.submit(runtime, 'all', '--barrier', prefix, '--flag-off')
        wait(lambda: docker.exec(NAME, ['test', '-f', prefix + '.ready'], check_result=False).returncode == 0)
        return prefix, output

    def finish(label, output):
        evidence.write(label + '/runtime-calls.txt', output.stdout + output.stderr)
        check(output.returncode == 0 and 'PROOF_OK uid=33' in output.stdout, 'real legacy call proof failed: ' + output.stdout + output.stderr)

    try:
        evidence.write('image-id.txt', docker.run(['image', 'inspect', args.image, '--format', '{{.Id}}']).stdout)
        legacy_certbot = docker.run(['run', '--rm', '--entrypoint', '/bin/bash',
                                     '-e', 'DA_UV_MANAGED=false', '-e', 'DA_MANAGED_STATE_DIR=/tmp/unadopted',
                                     args.image, '-c', 'timeout 10 certbot --version'], check_result=False)
        evidence.write('unadopted-certbot.txt', legacy_certbot.stdout + legacy_certbot.stderr)
        check(legacy_certbot.returncode == 0 and 'certbot 5.7.0' in legacy_certbot.stdout,
              'unadopted Certbot entrypoint no longer reaches the inherited tool')
        docker.start(NAME, args.image)
        layout = docker.runtime_layout(NAME)
        RUNTIME = layout.python
        wait(lambda: http.status('/health_check?ready=1') == 200, 900)
        qualification = status()['consumer_qualification']
        check(qualification['complete'] is True
              and qualification.get('bypass_fence_proof') == 'legacy-cli-intent-stale-task',
              'full qualification lost the 04b3a fence evidence')
        fences('normal')
        finish('normal', runtime())
        finish('normal-flag-off', runtime('all', '--flag-off'))
        capture('normal-and-durable-flag-off-fences')

        with ThreadPoolExecutor(max_workers=2) as pool:
            prefix, accepted = job(pool, 'timeout')
            before, applied = docker.read_file(NAME, AUDIT), docker.read_file(NAME, STATE + '/applied.json')
            operation = pool.submit(docker.exec, NAME, ['da-executor', 'apply-runtime', '--revision', 'r1'],
                                    env={'DA_DRAIN_TIMEOUT': '2'}, check_result=False)
            wait(lambda: status().get('admission', {}).get('mode') == 'draining')
            denied = docker.exec(NAME, ['bash', '/usr/share/docassemble/webapp/reset.sh'], check_result=False)
            check(denied.returncode == 75, 'reset passed maintenance')
            check(operation.result(timeout=90).returncode == 3, 'held admitted work did not abort drain')
            check(docker.read_file(NAME, AUDIT) == before and docker.read_file(NAME, STATE + '/applied.json') == applied,
                  'timeout mutated the unchanged environment')
            check(http.status('/health_check?ready=1') == 200, 'unchanged service did not resume')
            docker.exec(NAME, ['touch', prefix + '.release'])
            finish('timeout', accepted.result(timeout=90))
            capture('drain-timeout-preserves-accepted-callers')

            prefix, accepted = job(pool, 'drain')
            before = docker.read_file(NAME, AUDIT)
            operation = pool.submit(docker.exec, NAME, ['da-executor', 'apply-runtime', '--revision', 'r1'],
                                    env={'DA_DRAIN_TIMEOUT': '180'}, check_result=False)
            wait(lambda: status().get('admission', {}).get('mode') == 'draining')
            fences('draining')
            check(http.status('/health_check') == 200 and http.status('/user/sign-in') == 503,
                  'liveness/normal admission were confused')
            check(docker.exec(NAME, ['pg_isready', '-q']).returncode == 0
                  and docker.exec(NAME, ['redis-cli', 'ping']).stdout.strip() == 'PONG', 'independent infrastructure stopped')
            docker.exec(NAME, ['touch', prefix + '.release'])
            finish('draining', accepted.result(timeout=90))
            result = operation.result(timeout=300)
            evidence.write('apply.txt', result.stdout + result.stderr)
            check(result.returncode == 0, 'controlled apply failed: ' + result.stdout + result.stderr)
            check(len(docker.read_file(NAME, AUDIT).splitlines()) == len(before.splitlines()) + 1,
                  'natural drain did not precede exactly one frozen sync')
            capture('closed-gate-refusals-and-natural-drain')

        # Test actual old deliveries to both queues, not just eager calls. The
        # existing worker strategy retains them without executing or acknowledging.
        published = docker.exec(NAME, ['da-executor', 'run-consumer', '--', RUNTIME,
                                       '/opt/docassemble-fixture/worker_client.py', 'publish'],
                                env={'DA_WORKER_PROOF_ARGS': json.dumps({'run': 'proof:bypasses', 'tasks': [
                                    {'queue': queue, 'label': task, 'task': task}
                                    for queue in ('celery', 'single') for task in ('tasks.update_packages', 'tasks.reset_server')]})})
        ids = json.loads(published.stdout.split('PROOF_JSON=')[-1])['ids']

        def queued():
            return {queue: [json.loads(line)['headers']['id'] for line in docker.exec(
                NAME, ['redis-cli', '-n', '1', '--raw', 'LRANGE', queue, '0', '-1']).stdout.splitlines() if line]
                    for queue in ('celery', 'single')}

        wait(lambda: all('unqualified management task retained:' in docker.read_file(
            NAME, '/usr/share/docassemble/log/managed-worker-' + kind + '.log')
                         for kind in ('normal', 'single')), 90)
        wait(lambda: set(ids) <= set(sum(queued().values(), [])), 90)
        check(all(docker.exec(NAME, ['redis-cli', '--raw', 'EXISTS', 'celery-task-meta-' + task_id]).stdout.strip() == '0'
                  for task_id in ids), 'stale deliveries produced a task result')
        evidence.write_json('stale-queued.json', {'ids': ids, 'queued': queued()})
        capture('both-queues-retain-stale-management-deliveries')
        result = docker.exec(NAME, ['da-executor', 'recover', '--reason', 'bypass-proof'], check_result=False)
        check(result.returncode == 0 and status()['recovery_available'] is True, 'restricted recovery failed')
        fences('recovery')
        check(runtime().returncode == 75, 'recovery admitted new runtime calls')
        check(set(ids) <= set(sum(queued().values(), [])), 'recovery lost stale broker deliveries')
        check(http.status('/health_check?ready=1') == 400 and status()['normal_ready'] is False,
              'recovery did not report unready status')
        capture('recovery-refusals-and-stale-delivery-retention')
        return 0
    except Exception as error:
        evidence.write('failure.txt', str(error) + '\n' + docker.logs(NAME))
        raise
    finally:
        evidence.write_json('results.json', results)
        docker.rm(NAME)


if __name__ == '__main__':
    raise SystemExit(main())
