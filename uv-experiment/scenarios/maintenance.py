#!/usr/bin/env python3
"""04a: actual web admission/drain, controller death, surviving uv children."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from harness import ContainerHttp, Docker, Evidence, check

NAME = 'da-uv-maintenance-proof'
STATE = '/var/lib/docassemble/managed'
AUDIT = '/var/log/docassemble-uv-invocations.log'
SLOW = '/interview?i=docassemble.sentinel_fixture:data/questions/maintenance-slow.yml'


def wait(predicate, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    raise AssertionError('timed out waiting for maintenance fixture')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', default='da-uv-maintenance:local')
    parser.add_argument('--evidence-dir')
    args = parser.parse_args()
    root = Path(args.evidence_dir) if args.evidence_dir else Path(__file__).resolve().parents[1] / 'maintenance' / 'evidence' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    evidence, results = Evidence(root), []
    docker = Docker(evidence)
    http = ContainerHttp(docker, NAME)

    def status():
        try:
            return json.loads(http.body('/status.json'))
        except ValueError:
            return {}

    def audit():
        return docker.read_file(NAME, AUDIT)

    def capture(label):
        evidence.write(label + '/logs.txt', docker.logs(NAME))
        evidence.write_json(label + '/status.json', status())
        for file in ('admission.json', 'lifecycle.json', 'applied.json', 'desired.json'):
            evidence.write(label + '/' + file, docker.read_file(NAME, STATE + '/' + file))
        evidence.write(label + '/uv-invocations.txt', audit())
        results.append({'scenario': label, 'ok': True})
        evidence.note('PASS ' + label)

    def start_work(pool):
        docker.exec_shell(NAME, 'rm -f /tmp/da-maintenance-request-* /tmp/maintenance-controller.pid /tmp/maintenance-cookies')
        future = pool.submit(docker.exec, NAME, ['curl', '-sS', '-L', '--max-time', '60',
                             '-c', '/tmp/maintenance-cookies', '-b', '/tmp/maintenance-cookies', 'http://localhost' + SLOW])
        wait(lambda: docker.exec(NAME, ['test', '-f', '/tmp/da-maintenance-request-started'], check_result=False).returncode == 0)
        return future

    def apply(pool, timeout, fault=False):
        command = 'echo $$ > /tmp/maintenance-controller.pid; exec da-executor apply-runtime --revision ' + ('r2 --fault-after-seconds 60' if fault else 'r1')
        return pool.submit(docker.exec, NAME, ['bash', '-c', command], check_result=False,
                           env={'DA_DRAIN_TIMEOUT': str(timeout), 'SLOWFIXTURE_BUILD_DELAY': '60'})

    try:
        evidence.write('image-id.txt', docker.run(['image', 'inspect', args.image, '--format', '{{.Id}}']).stdout)
        docker.start(NAME, args.image)
        wait(lambda: http.status('/health_check?ready=1') == 200, 900)
        with ThreadPoolExecutor(max_workers=3) as pool:
            # Abort before mutation, preserving the accepted real interview.
            before = audit()
            applied = docker.read_file(NAME, STATE + '/applied.json')
            work = start_work(pool)
            operation = apply(pool, 2)
            wait(lambda: status().get('admission', {}).get('mode') == 'draining')
            check(http.status('/user/sign-in') == 503, 'new normal requests were admitted')
            check(http.status('/health_check') == 200, 'independent controller liveness failed')
            check(http.status('/health_check?ready=1') == 400, 'maintenance claimed normal readiness')
            check(status().get('controller_alive') is True, 'controller status missing')
            check(docker.exec(NAME, ['pg_isready', '-q']).returncode == 0, 'PostgreSQL stopped')
            check(docker.exec(NAME, ['redis-cli', 'ping']).stdout.strip() == 'PONG', 'Redis stopped')
            check(operation.result(timeout=90).returncode == 3, 'drain timeout did not abort')
            check(audit() == before, 'timeout invoked uv')
            check(docker.read_file(NAME, STATE + '/applied.json') == applied, 'timeout changed applied state')
            check('Accepted maintenance fixture work completed' in work.result(timeout=90).stdout, 'active work lost')
            check(http.status('/health_check?ready=1') == 200, 'unchanged service did not resume')
            capture('drain-timeout-unchanged-service')

            # Complete an accepted interview before a frozen apply is permitted.
            before = audit()
            work = start_work(pool)
            operation = apply(pool, 20)
            wait(lambda: status().get('admission', {}).get('mode') == 'draining')
            check(audit() == before, 'sync started while the interview was active')
            check(http.status('/user/sign-in') == 503, 'maintenance admitted another request')
            check('Accepted maintenance fixture work completed' in work.result(timeout=90).stdout, 'drain destroyed work')
            output = operation.result(timeout=300)
            evidence.write('apply-output.txt', output.stdout + output.stderr)
            check(output.returncode == 0, 'controlled apply failed: ' + output.stdout + output.stderr)
            check(http.status('/health_check?ready=1') == 200, 'apply did not resume service')
            check(len(audit().splitlines()) == len(before.splitlines()) + 1, 'apply did not perform one frozen sync')
            capture('drain-success-frozen-apply')

            # Crash before mutation: admission stays closed until verified restart.
            before = audit()
            work = start_work(pool)
            operation = apply(pool, 20)
            wait(lambda: status().get('admission', {}).get('mode') == 'draining')
            docker.exec_shell(NAME, 'kill -KILL $(cat /tmp/maintenance-controller.pid)')
            check(operation.result(timeout=90).returncode != 0, 'controller death was not observed')
            check('Accepted maintenance fixture work completed' in work.result(timeout=90).stdout, 'controller death killed accepted work')
            time.sleep(3)
            check(http.status('/user/sign-in') == 503, 'controller death reopened admission')
            check(audit() == before, 'pre-mutation crash invoked uv')
            capture('draining-controller-interruption')
            docker.restart(NAME)
            wait(lambda: http.status('/health_check?ready=1') == 200, 900)
            check(audit() == before, 'unchanged verified restart resolved or synced')
            capture('unchanged-restart-after-drain-crash')

            # Actual unprivileged uv/build child outlives the applying controller.
            before = audit()
            operation = apply(pool, 20, fault=True)
            wait(lambda: len(audit().splitlines()) > len(before.splitlines()), 180)
            docker.exec_shell(NAME, 'kill -KILL $(cat /tmp/maintenance-controller.pid)')
            check(operation.result(timeout=90).returncode != 0, 'applying controller survived SIGKILL')
            processes = docker.exec(NAME, ['ps', '-eo', 'user,pid,ppid,args']).stdout
            evidence.write('surviving-child-processes.txt', processes)
            check('da-envi' in processes and 'uv-pinned sync' in processes, 'real unprivileged mutator did not survive')
            before = audit()
            contender = docker.exec(NAME, ['da-executor', 'apply-runtime', '--revision', 'r1'], check_result=False)
            evidence.write('contender-output.txt', contender.stdout + contender.stderr)
            check(contender.returncode != 0 and 'temporarily unavailable' in contender.stderr.lower(), 'surviving child lost authority')
            check(audit() == before, 'a second sync overlapped the surviving child')
            check(http.status('/health_check') == 200 and http.status('/user/sign-in') == 503, 'mutation liveness/admission confused')
            capture('surviving-real-uv-child-exclusion')
            docker.restart(NAME)  # All children die; durable partial journal requires one core recovery.
            wait(lambda: status().get('recovery_available') is True, 900)
            check(http.status('/health_check?ready=1') == 400, 'core recovery claimed normal readiness')
            capture('interrupted-apply-core-recovery')

        native = docker.exec(NAME, ['/usr/share/docassemble/executor-venv/bin/python', '-m', 'pytest',
                                     '/opt/docassemble-executor/tests/test_authority.py', '-q', '-p', 'no:cacheprovider'])
        evidence.write('native-child-authority-tests.txt', native.stdout + native.stderr)
        capture('detached-fd-closing-grandchild-exclusion')
        return 0
    except Exception as error:
        evidence.write('failure.txt', str(error) + '\n' + docker.logs(NAME))
        raise
    finally:
        evidence.write_json('results.json', results)
        docker.rm(NAME)


if __name__ == '__main__':
    raise SystemExit(main())
