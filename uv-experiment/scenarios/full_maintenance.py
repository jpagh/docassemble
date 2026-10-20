#!/usr/bin/env python3
"""04b3b: intentional override, lifecycle and full all-role maintenance proof."""
from concurrent.futures import ThreadPoolExecutor as BasePool
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

from harness import ContainerHttp, Docker, Evidence, check
from maintenance import wait

NAME = 'da-uv-full-maintenance-proof'
STATE = '/var/lib/docassemble/managed'
ROOT = '/tmp/da-development-proof'
SOURCE = '/opt/docassemble-fixture/development-package'
RUNTIME = '/usr/share/docassemble/local3.12/bin/python'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', default='da-uv-full-maintenance:local')
    parser.add_argument('--evidence-dir')
    args = parser.parse_args()
    root = Path(args.evidence_dir) if args.evidence_dir else Path(__file__).resolve().parents[1] / 'full-maintenance/evidence' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    evidence, results = Evidence(root), []
    docker = Docker(evidence)
    http = ContainerHttp(docker, NAME)

    class ThreadPoolExecutor(BasePool):
        def __exit__(self, kind, error, traceback):
            if kind is not None:
                evidence.write('failure.txt', str(error) + '\n' + docker.logs(NAME))
                evidence.write('failure-processes.txt', docker.exec(NAME, ['ps', '-eo', 'user,pid,ppid,args'], check_result=False).stdout)
                evidence.write('failure-service-worker-logs.txt', docker.exec_shell(NAME, 'tail -80 /usr/share/docassemble/log/managed-*.log', check_result=False).stdout)
                docker.rm(NAME)  # Disposable failure cleanup is never a successful drain.
            return super().__exit__(kind, error, traceback)

    def status():
        try:
            return json.loads(http.body('/status.json'))
        except ValueError:
            return {}  # No listener yet during the deliberate container restart.

    def present(file):
        return docker.exec(NAME, ['test', '-f', ROOT + '/' + file], check_result=False).returncode == 0

    def install(**environment):
        return docker.exec(NAME, ['bash', '-c', 'echo $$ > ' + ROOT + '/controller; exec da-executor development-install --source ' + SOURCE],
                           env=dict(DA_DEVELOPMENT_PROOF_ROOT=ROOT, **environment), check_result=False)

    def capture(label):
        evidence.write_json(label + '/status.json', status())
        evidence.write(label + '/logs.txt', docker.logs(NAME))
        evidence.write(label + '/processes.txt', docker.exec(NAME, ['ps', '-eo', 'user,pid,ppid,args']).stdout)
        for name in ('admission.json', 'lifecycle.json', 'applied.json', 'desired.json'):
            evidence.write(label + '/' + name, docker.read_file(NAME, STATE + '/' + name))
        results.append({'scenario': label, 'ok': True})
        evidence.note('PASS ' + label)

    try:
        evidence.write('image-id.txt', docker.run(['image', 'inspect', args.image, '--format', '{{.Id}}']).stdout)
        docker.start(NAME, args.image)
        wait(lambda: http.status('/health_check?ready=1') == 200, 900)
        state = status()
        check(state['consumer_qualification']['complete'] is True and not state['consumer_qualification']['pending'],
              'full supported consumer inventory is not qualified')
        expected = {'web', 'celery', 'celerysingle', 'websockets', 'nltk', 'conversion', 'watchdog', 'postgres', 'redis', 'cron', 'exim4', 'syslogng'}
        check(expected <= {role['name'] for role in state['role_health'] if role['ok']},
              'normal readiness omitted actual supported roles')
        capture('whole-supported-all-role-readiness')
        docker.exec_shell(NAME, 'mkdir -p ' + ROOT + '; chmod 777 ' + ROOT)
        # Copy the literal test package so a red test also runs on the old image.
        package = Path(__file__).resolve().parents[1] / 'fixture/development-package'
        docker.run(['cp', str(package), NAME + ':' + SOURCE])
        applied = docker.read_file(NAME, STATE + '/applied.json')
        desired = docker.read_file(NAME, STATE + '/desired.json')
        output = docker.exec(NAME, ['da-executor', 'development-install', '--source', SOURCE],
                             env={'DA_DEVELOPMENT_PROOF_ROOT': ROOT}, check_result=False)
        evidence.write('development-install.txt', output.stdout + output.stderr)
        check(output.returncode == 0, 'development installation failed: ' + output.stdout + output.stderr)
        check(int(docker.read_file(NAME, ROOT + '/build-uid')) != 0, 'project build ran as root')
        probe = docker.exec(NAME, ['da-executor', 'run-consumer', '--', RUNTIME, '-c',
                                  'import da_development_probe; print(da_development_probe.VALUE)'])
        check(probe.stdout.strip() == 'deliberate override', 'development package is not installed in the single runtime')
        state = status()
        check(state['runtime_state'] == 'override' and state['locked_converged'] is False,
              'development installation claimed locked convergence')
        check(docker.read_file(NAME, STATE + '/applied.json') == applied and
              docker.read_file(NAME, STATE + '/desired.json') == desired, 'override changed recorded intent/application')
        capture('non-root-exclusive-development-install')
        before = docker.read_file(NAME, '/var/log/docassemble-uv-invocations.log')
        started = json.loads(docker.exec(NAME, ['curl', '-sS', 'http://127.0.0.1:81/health_status']).stdout)['server_start_time']
        with ThreadPoolExecutor(max_workers=1) as pool:
            docker.exec_shell(NAME, 'rm -f /tmp/da-maintenance-request-* /tmp/full-reload-cookies')
            work = pool.submit(docker.exec, NAME, ['curl', '-sS', '-L', '--max-time', '90',
                               '-c', '/tmp/full-reload-cookies', '-b', '/tmp/full-reload-cookies',
                               'http://localhost/interview?i=docassemble.sentinel_fixture:data/questions/maintenance-slow.yml'])
            wait(lambda: docker.exec(NAME, ['test', '-f', '/tmp/da-maintenance-request-started'], check_result=False).returncode == 0)
            output = docker.exec(NAME, ['da-executor', 'reload-web'], check_result=False)
            check('Accepted maintenance fixture work completed' in work.result(timeout=90).stdout, 'ordinary reload destroyed accepted web work')
        reloaded = json.loads(docker.exec(NAME, ['curl', '-sS', 'http://127.0.0.1:81/health_status']).stdout)['server_start_time']
        check(reloaded != started, 'reload returned before a new actual application worker became usable')
        check(output.returncode == 0, 'ordinary reload failed: ' + output.stdout + output.stderr)
        wait(lambda: http.status('/user/sign-in') == 200, 60)
        probe = docker.exec(NAME, ['da-executor', 'run-consumer', '--', RUNTIME, '-c',
                                  'import da_development_probe; print(da_development_probe.VALUE)'])
        check(probe.stdout.strip() == 'deliberate override' and status()['runtime_state'] == 'override',
              'ordinary reload lost or hid the development override')
        check(docker.read_file(NAME, '/var/log/docassemble-uv-invocations.log') == before, 'ordinary reload invoked sync/install')
        capture('ordinary-web-reload-preserves-override-without-sync')

        # Both actual host scripts run against real exec/cp. The Apple command
        # adapter substitutes only transport/inspect syntax, not installation.
        scripts = Path(__file__).resolve().parents[2] / 'development'
        with tempfile.TemporaryDirectory() as directory:
            adapter = Path(directory) / 'container'
            adapter.write_text('#!/bin/bash\nif [ "$1" = inspect ]; then\n'
                               'docker inspect "$2" >/dev/null || exit $?\n'
                               'echo \'{"state":"running"}\'\nelse exec docker "$@"; fi\n')
            adapter.chmod(0o755)
            for transport in ('docker', 'container'):
                output = subprocess.run(['bash', str(scripts / ('install-live-package-' + transport + '.sh')),
                                         str(package), NAME], capture_output=True, text=True, timeout=180,
                                        env=dict(os.environ, CONTAINER=str(adapter)))
                evidence.write('development-script-' + transport + '.txt', output.stdout + output.stderr)
                check(output.returncode == 0 and 'supervised managed development override' in output.stdout,
                      transport + ' script bypassed or failed the managed executor')
        capture('development-docker-and-container-exec-scripts')

        with ThreadPoolExecutor(max_workers=2) as pool:
            work = pool.submit(docker.exec, NAME, ['da-executor', 'run-consumer', '--', '/bin/bash', '-c',
                               'touch ' + ROOT + '/accepted; while [ ! -f ' + ROOT + '/release ]; do sleep 0.1; done; echo accepted-finished'])
            wait(lambda: present('accepted'))
            before = docker.read_file(NAME, '/var/log/docassemble-uv-invocations.log')
            docker.exec(NAME, ['rm', '-f', ROOT + '/build-uid'])
            operation = pool.submit(install, DA_DRAIN_TIMEOUT='2')
            wait(lambda: status()['admission']['mode'] == 'draining')
            check(http.status('/user/sign-in') == 503, 'override admitted new web work during drain')
            check(operation.result(timeout=90).returncode == 3 and not present('build-uid'), 'timeout executed a project build')
            check(docker.read_file(NAME, '/var/log/docassemble-uv-invocations.log') == before, 'timeout installed or synced')
            check(status()['runtime_state'] == 'override', 'timeout lost the prior override state')
            docker.exec(NAME, ['touch', ROOT + '/release'])
            check('accepted-finished' in work.result(timeout=30).stdout, 'override timeout destroyed accepted work')
        capture('development-timeout-resumes-unchanged-override')

        with ThreadPoolExecutor(max_workers=1) as pool:
            operation = pool.submit(install, DA_DEVELOPMENT_PROOF_CHILD='true')
            def child_started():
                if operation.done() and not present('child-started'):
                    output = operation.result()
                    evidence.write('interrupted-development-output.txt', output.stdout + output.stderr)
                    raise AssertionError('development installer exited before child start: ' + output.stdout + output.stderr)
                return present('child-started')
            wait(child_started, 120)
            check(status()['runtime_state'] == 'unknown' and status()['normal_ready'] is False,
                  'unfinished development install claimed health')
            docker.exec_shell(NAME, 'kill -KILL $(cat ' + ROOT + '/controller)')
            check(operation.result(timeout=30).returncode != 0, 'development controller interruption was not observed')
            before = docker.read_file(NAME, '/var/log/docassemble-uv-invocations.log')
            for command in (['development-install', '--source', SOURCE], ['apply-runtime', '--revision', 'r1'], ['reload-web']):
                contender = docker.exec(NAME, ['da-executor', *command], check_result=False)
                check(contender.returncode != 0 and 'temporarily unavailable' in contender.stderr.lower(),
                      'surviving development descendant lost exclusion: ' + str(command))
            check(docker.read_file(NAME, '/var/log/docassemble-uv-invocations.log') == before, 'contender overlapped the surviving build child')
            docker.exec(NAME, ['touch', ROOT + '/child-release'])
            wait(lambda: present('child-completed'))
            check(int(docker.read_file(NAME, ROOT + '/child-completed')) != 0, 'surviving build descendant ran as root')
        capture('interrupted-development-detached-fd-closing-child-exclusion')
        output = docker.exec(NAME, ['da-executor', 'apply-runtime', '--revision', 'r1'], check_result=False)
        check(output.returncode == 0 and status()['runtime_state'] == 'locked', 'explicit managed sync could not leave the override')
        probe = docker.exec(NAME, ['da-executor', 'run-consumer', '--', RUNTIME, '-c', 'import da_development_probe'], check_result=False)
        check(probe.returncode != 0, 'managed sync silently retained the unlocked development package')
        capture('explicit-frozen-sync-restores-recorded-lock')

        with ThreadPoolExecutor(max_workers=2) as pool:
            docker.exec(NAME, ['rm', '-f', ROOT + '/accepted', ROOT + '/release'])
            work = pool.submit(docker.exec, NAME, ['da-executor', 'run-consumer', '--', '/bin/bash', '-c',
                               'touch ' + ROOT + '/accepted; while [ ! -f ' + ROOT + '/release ]; do sleep 0.1; done; echo shutdown-work-finished'])
            wait(lambda: present('accepted'))
            operation = pool.submit(docker.exec, NAME, ['da-executor', 'shutdown', '--timeout', '2'], check_result=False)
            check(operation.result(timeout=90).returncode == 3, 'shutdown did not abort before stopping infrastructure')
            check(docker.exec(NAME, ['pg_isready', '-q']).returncode == 0 and
                  docker.exec(NAME, ['redis-cli', 'ping']).stdout.strip() == 'PONG', 'shutdown timeout stopped required infrastructure')
            docker.exec(NAME, ['touch', ROOT + '/release'])
            check('shutdown-work-finished' in work.result(timeout=30).stdout, 'shutdown forced accepted work')
        capture('shutdown-timeout-preserves-work-and-infrastructure')

        output = install(DA_DEVELOPMENT_PROOF_FAIL='true')
        check(output.returncode != 0 and status()['runtime_state'] == 'unknown' and http.status('/user/sign-in') == 503,
              'failed development build reopened or claimed normal service')
        docker.restart(NAME)
        wait(lambda: status().get('recovery_available') is True, 900)
        check(status()['normal_ready'] is False, 'interrupted override recovery claimed normal readiness')
        capture('failed-override-one-attempt-restricted-core-recovery')
        docker.exec(NAME, ['/usr/bin/python3', '-c', "import socket; b=b'<14>1 2026-10-04T00:00:00Z localhost docassemble - - - full-proof-independent-syslog'; s=socket.create_connection(('127.0.0.1',514)); s.sendall(str(len(b)).encode()+b' '+b); s.close()"])
        wait(lambda: 'full-proof-independent-syslog' in docker.read_file(NAME, '/usr/share/docassemble/log/docassemble.log'), 30)
        capture('actual-independent-syslog-during-restricted-recovery')
        docker.exec(NAME, ['redis-cli', 'set', 'proof:shutdown-persistence', 'retained'])
        docker.exec_shell(NAME, 'rm /usr/share/docassemble/local3.12/lib/python3.12/site-packages/docassemble/webapp/server.py')
        output = docker.exec(NAME, ['da-executor', 'shutdown', '--timeout', '90'], check_result=False)
        evidence.write('broken-runtime-shutdown.txt', output.stdout + output.stderr)
        check(output.returncode == 0, 'infrastructure shutdown depended on the broken application runtime')
        check(docker.exec(NAME, ['pg_isready', '-q'], check_result=False).returncode != 0 and
              docker.exec(NAME, ['redis-cli', 'ping'], check_result=False).returncode != 0,
              'independent infrastructure did not actually stop')
        check(http.status('/health_check') == 200 and http.status('/health_check?ready=1') == 400,
              'stopped infrastructure confused controller liveness and readiness')
        capture('broken-runtime-independent-infrastructure-shutdown')
        docker.restart(NAME)
        wait(lambda: docker.exec(NAME, ['redis-cli', 'get', 'proof:shutdown-persistence'], check_result=False).stdout.strip() == 'retained', 120)
        check(docker.exec(NAME, ['pg_isready', '-q']).returncode == 0, 'independent PostgreSQL did not restart')
        capture('os-only-restart-retains-redis-and-database')

        # Cold boot: block the real Pattern health request, not a fake service.
        docker.start(NAME, args.image, cold_setup='mkdir -p /tmp/da-full-readiness-proof; chmod 777 /tmp/da-full-readiness-proof; '
                     'cp /opt/docassemble-fixture/full_maintenance_hooks.py /opt/docassemble-executor/src/sitecustomize.py; '
                     'export PYTHONPATH=/opt/docassemble-executor/src')
        wait(lambda: docker.exec(NAME, ['test', '-f', '/tmp/da-full-readiness-proof/started'], check_result=False).returncode == 0, 180)
        check(status().get('admission', {}).get('mode') == 'preparing' and http.status('/user/sign-in') == 503 and
              http.status('/health_check?ready=1') == 400, 'ordinary work/readiness opened before role protocol proof')
        check(not docker.read_file(NAME, STATE + '/applied.json'), 'applied revision advanced before role protocol health')
        for command in (['run-consumer', '--', '/usr/bin/true'], ['run-helper', '--kind', 'hourly'], ['reload-web']):
            result = docker.exec(NAME, ['da-executor', *command], check_result=False)
            check(result.returncode != 0, 'preparing admitted ordinary work: ' + str(command))
        evidence.write('preparing/status.json', json.dumps(status(), indent=2))
        docker.exec(NAME, ['touch', '/tmp/da-full-readiness-proof/release'])
        wait(lambda: http.status('/health_check?ready=1') == 200, 180)
        receipt = json.loads(docker.read_file(NAME, '/tmp/da-full-readiness-proof/completed'))
        check(receipt == {'uid': 33, 'result': 'cats', 'mode': 'preparing'}, 'actual health probe did not complete non-root behind admission')
        capture('cold-boot-real-protocol-readiness-before-applied-and-admission')
        applied = docker.read_file(NAME, STATE + '/applied.json')
        old = status()['role_processes']['nltk'][0][0]
        docker.exec(NAME, ['/bin/kill', '-KILL', old])  # Idle-role fault injection, never a drain tactic.
        wait(lambda: status().get('admission', {}).get('mode') == 'preparing', 60)
        check(http.status('/health_check?ready=1') == 400, 'missing live role retained normal readiness')
        wait(lambda: http.status('/health_check?ready=1') == 200, 180)
        check(status()['role_processes']['nltk'][0][0] != old and docker.read_file(NAME, STATE + '/applied.json') == applied,
              'role autorestart skipped protocol verification or rewrote applied state')
        capture('idle-role-death-reverified-before-normal-readiness')
        output = docker.run(['stop', '-t', '120', NAME], timeout=180)
        check(docker.run(['inspect', '--format', '{{.State.ExitCode}}', NAME]).stdout.strip() == '0', 'PID 1 shutdown did not exit cleanly')
        check('independent infrastructure shutdown completed' in docker.logs(NAME), 'PID 1 bypassed the independent drained shutdown')
        evidence.write('pid1-shutdown/logs.txt', docker.logs(NAME))
        results.append({'scenario': 'pid1-independent-drained-shutdown', 'ok': True})
        evidence.note('PASS pid1-independent-drained-shutdown')
        return 0
    except Exception as error:
        if not (evidence.root / 'failure.txt').exists():
            evidence.write('failure.txt', str(error) + '\n' + docker.logs(NAME))
            evidence.write('failure-processes.txt', docker.exec(NAME, ['ps', '-eo', 'user,pid,ppid,args'], check_result=False).stdout)
            evidence.write('failure-service-worker-logs.txt', docker.exec_shell(NAME, 'tail -80 /usr/share/docassemble/log/managed-*.log', check_result=False).stdout)
        raise
    finally:
        evidence.write_json('results.json', results)
        docker.rm(NAME)


if __name__ == '__main__':
    raise SystemExit(main())
