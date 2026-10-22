#!/usr/bin/env python3
"""04b2b: actual websocket/Pattern/UNO protocol and child-lifetime evidence."""
from concurrent.futures import ThreadPoolExecutor as BasePool
from datetime import datetime, timezone
import argparse
import json
import time
from pathlib import Path

from harness import ContainerHttp, Docker, Evidence, check
from maintenance import wait

NAME = 'da-uv-service-proof'
CONFIG = '/var/run/docassemble/managed-supervisor/supervisord.conf'
ROOT = '/tmp/da-service-proof/'
SERVICES = ('websockets', 'nltk', 'conversion')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', default='da-uv-services:local')
    parser.add_argument('--evidence-dir')
    parser.add_argument('--keep-container', action='store_true')
    args = parser.parse_args()
    root = Path(args.evidence_dir) if args.evidence_dir else Path(__file__).resolve().parents[1] / 'service-sessions/evidence' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    evidence, results = Evidence(root), []
    docker = Docker(evidence)
    http = ContainerHttp(docker, NAME)

    class ThreadPoolExecutor(BasePool):
        def __exit__(self, kind, error, traceback):
            if kind is not None:
                # Failed proofs must not wait forever for deliberately held
                # accepted work. Disposable cleanup is not a successful drain.
                evidence.write('failure.txt', str(error) + '\n' + docker.logs(NAME))
                evidence.write('service-logs.txt', docker.exec_shell(
                    NAME, 'cat /usr/share/docassemble/log/managed-service-*.log', check_result=False).stdout)
                docker.rm(NAME)
            return super().__exit__(kind, error, traceback)

    def client(action, admitted=False, check_result=True, **payload):
        command = [RUNTIME, '/opt/docassemble-fixture/service_client.py', action]
        if admitted:
            command = ['da-executor', 'run-consumer', '--', *command]
        return docker.exec(NAME, command, env={'DA_SERVICE_PROOF_ARGS': json.dumps(payload)},
                           user=None if admitted else 'www-data', check_result=check_result)

    def present(file):
        return docker.exec(NAME, ['test', '-f', ROOT + file], check_result=False).returncode == 0

    def nested_receipts(*labels):
        for label in labels:
            for protocol in ('nlp', 'uno'):
                receipt = json.loads(docker.read_file(NAME, ROOT + label + '.' + protocol + '-completed'))
                check(receipt['uid'] == 33 and receipt['mode'] == 'draining',
                      label + ': actual receiver did not complete after closure as www-data')
                if protocol == 'nlp':
                    check(receipt['result'] == 'cats', label + ': actual remote Pattern result is missing')
                evidence.write_json('nested-receipts/' + label + '-' + protocol + '.json', receipt)

    def touch(*files):
        docker.exec(NAME, ['touch', *(ROOT + name for name in files)])

    def status():
        return json.loads(http.body('/status.json'))

    def uv_log():
        return docker.read_file(NAME, '/var/log/docassemble-uv-invocations.log')

    def worker_client(action, **payload):
        output = docker.exec(NAME, [RUNTIME, '/opt/docassemble-fixture/worker_client.py', action],
                             env={'DA_WORKER_PROOF_ARGS': json.dumps(dict(run='proof:service', **payload))},
                             user='www-data').stdout
        return json.loads(output.split('PROOF_JSON=')[-1])

    def service_pids():
        processes = docker.exec(NAME, ['ps', '-eo', 'pid,args']).stdout.splitlines()
        return {line.split()[-1]: int(line.split()[0]) for line in processes
                if 'service_runtime.py --kind' in line and 'authority.py' not in line}

    def capture(label):
        evidence.write_json(label + '/status.json', status())
        evidence.write(label + '/supervisor.txt', docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'status']).stdout)
        evidence.write(label + '/processes.txt', docker.exec(NAME, ['ps', '-eo', 'user,pid,ppid,args']).stdout)
        evidence.write(label + '/uv-invocations.txt', uv_log())
        evidence.write(label + '/logs.txt', docker.logs(NAME))
        evidence.write(label + '/service-logs.txt', docker.exec_shell(NAME, 'cat /usr/share/docassemble/log/managed-service-*.log', check_result=False).stdout)
        for file in ('admission.json', 'lifecycle.json', 'applied.json'):
            evidence.write(label + '/' + file, docker.read_file(NAME, '/var/lib/docassemble/managed/' + file))
        evidence.write(label + '/ledger.txt', docker.exec_shell(NAME, 'for f in /tmp/da-service-proof/*completed; do echo "$f"; cat "$f"; echo; done', check_result=False).stdout)
        results.append({'scenario': label, 'ok': True})
        evidence.note('PASS ' + label)

    def apply(timeout='90'):
        return docker.exec(NAME, ['da-executor', 'apply-runtime', '--revision', 'r1'],
                           env={'DA_DRAIN_TIMEOUT': timeout}, check_result=False)

    def normal():
        output = client('normal')
        evidence.write('normal-protocols.txt', output.stdout + output.stderr)
        check(json.loads(output.stdout.split('PROOF_JSON=')[-1])['nltk']['result'] == 'cats', 'real Pattern result is incorrect')

    try:
        evidence.write('image-id.txt', docker.run(['image', 'inspect', args.image, '--format', '{{.Id}}']).stdout)
        docker.start(NAME, args.image, cold_setup='mkdir -p /tmp/da-service-proof; chmod 777 /tmp/da-service-proof; '
                     'cp /opt/docassemble-fixture/service_probe_hooks.py /opt/docassemble-executor/src/sitecustomize.py; '
                     'export PYTHONPATH=/opt/docassemble-executor/src')
        layout = docker.runtime_layout(NAME)
        RUNTIME = layout.python
        wait(lambda: http.status('/health_check?ready=1') == 200, 900)
        qualification = status()['consumer_qualification']
        check(qualification['complete'] is True and set(SERVICES) <= set(qualification['enabled'])
              and not set(SERVICES) & set(qualification['pending'])
              and qualification['worker_broker_proof'] == 'redis-prefork',
              'service qualification is missing, complete, or lost worker qualification')
        wait(lambda: client('normal', check_result=False).returncode == 0, 120)
        normal()
        check(len(service_pids()) == 3, 'all three actual services are not supervised')
        core = client('core-conversion', admitted=True, label='core-route')
        evidence.write('core-conversion-route.txt', core.stdout + core.stderr)
        check(docker.read_file(NAME, ROOT + 'core-route.completed') == '33', 'core conversion did not use www-data UNO service')
        capture('actual-supervised-pattern-and-uno')
        with ThreadPoolExecutor(max_workers=3) as pool:
            session = pool.submit(client, 'websocket', label='ws-timeout')
            polling = pool.submit(client, 'polling', label='polling-timeout')
            wait(lambda: present('ws-timeout.connected') and present('polling-timeout.connected'), 120)
            before, processes = uv_log(), docker.read_file(NAME, ROOT + 'ws-timeout.connected')
            operation = pool.submit(apply, '8')
            wait(lambda: status()['admission']['mode'] == 'draining')
            denied = client('websocket', label='ws-refused', check_result=False)
            check(denied.returncode != 0 and not present('ws-refused.connected'), 'closed websocket handshake reached user code')
            check(client('polling', label='polling-refused', check_result=False).returncode != 0,
                  'closed polling handshake reached user code')
            touch('polling-timeout.disconnect')
            check(polling.result(timeout=35).returncode == 0, 'existing polling transport could not dispatch after closure')
            check(client('nltk', check_result=False).stdout.find('managed service admission is closed') >= 0,
                  'new NLTK request was admitted after closure')
            check(client('conversion', check_result=False).returncode != 0, 'new UNO request was admitted after closure')
            check(operation.result(timeout=90).returncode == 3, 'long-lived websocket did not abort drain')
            check(uv_log() == before, 'timeout mutated the runtime')
            touch('ws-timeout.disconnect')
            output = session.result(timeout=90)
            check(output.returncode == 0 and docker.read_file(NAME, ROOT + 'ws-timeout.connected') == processes,
                  'existing websocket could not finish on unchanged service')
        normal()
        capture('front-door-websocket-and-polling-timeout-reversible-service')

        with ThreadPoolExecutor(max_workers=2) as pool:
            session = pool.submit(client, 'websocket', label='ws-nested', background=True, do_nested=True)
            wait(lambda: present('ws-nested.connected') and present('ws-nested.started'))
            before = uv_log()
            operation = pool.submit(apply)
            wait(lambda: status()['admission']['mode'] == 'draining')
            touch('ws-nested.disconnect')
            check(session.result(timeout=90).returncode == 0, 'accepted websocket session could not finish after closure')
            check(not operation.done() and uv_log() == before, 'disconnect released unfinished background authority')
            touch('ws-nested.release')
            wait(lambda: present('ws-nested.completed'), 120)
            nested_receipts('ws-nested')
            output = operation.result(timeout=300)
            evidence.write('websocket-natural-drain.txt', output.stdout + output.stderr)
            check(output.returncode == 0, 'websocket background/nested requests did not drain naturally')
            check(docker.read_file(NAME, ROOT + 'ws-nested.completed') == 'cats:33',
                  'nested NLTK/conversion after closure failed or ran as root')
            check(len(uv_log().splitlines()) == len(before.splitlines()) + 1, 'natural drain did not precede exactly one sync')
        wait(lambda: client('normal', check_result=False).returncode == 0, 120)
        capture('websocket-disconnect-background-and-nested-natural-drain')

        # Both protocols have accepted real work before the client disappears.
        # Barriers wrap the actual Pattern/UNO calls, not synthetic servers.
        client('nltk', label='proof:nltk-lost-client', disconnect=True)
        client('conversion', label='uno-lost-client', disconnect=True)
        wait(lambda: present('nltk-lost-client.started') and present('uno-lost-client.started'))
        time.sleep(12)  # Longer than the rejected periodic-probe policy's cadence.
        check(status()['normal_ready'] and status()['admission']['mode'] == 'open',
              'healthy accepted serial work was treated as failed readiness')
        before, pids = uv_log(), service_pids()
        with ThreadPoolExecutor(max_workers=1) as pool:
            operation = pool.submit(apply, '8')
            wait(lambda: status()['admission']['mode'] == 'draining')
            check(operation.result(timeout=90).returncode == 3, 'client disconnect released in-flight service authority')
        check(service_pids() == pids and uv_log() == before, 'disconnected-client timeout restarted or mutated accepted service')
        touch('nltk-lost-client.release', 'uno-lost-client.release')
        wait(lambda: present('nltk-lost-client.completed') and present('uno-lost-client.completed'), 120)
        normal()
        capture('disconnected-pattern-and-uno-timeout-unchanged-runtime')

        client('nltk', label='proof:nltk-crash', disconnect=True)
        client('conversion', label='uno-crash', disconnect=True)
        wait(lambda: present('nltk-crash.started') and present('uno-crash.started'))
        before, pids = uv_log(), service_pids()
        with ThreadPoolExecutor(max_workers=1) as pool:
            operation = pool.submit(docker.exec_shell, NAME,
                                    'echo $$ > /tmp/service-controller.pid; exec da-executor apply-runtime --revision r1',
                                    check_result=False)
            wait(lambda: status()['admission']['mode'] == 'draining')
            for name in SERVICES:
                check(docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'restart', name]).returncode == 0,
                      'real Supervisor restart failed')
                denied = docker.exec(NAME, ['da-executor', 'run-service', '--kind', name],
                                     env={'DA_RUNTIME_VENV': '/missing-runtime'}, check_result=False)
                check(denied.returncode == 75, 'closed service CLI imported runtime')
            # Active services remain alive and singleton authority survives the
            # forced wrapper deaths. Controller loss does not reopen admission.
            check(service_pids() == pids, 'Supervisor killed or duplicated accepted service payloads')
            docker.exec_shell(NAME, 'kill -KILL $(cat /tmp/service-controller.pid)')
            check(operation.result(timeout=90).returncode != 0, 'applying controller did not die')
            check(apply('3').returncode == 3 and uv_log() == before, 'controller/wrapper death allowed mutation')
            check(http.status('/health_check') == 200 and http.status('/user/sign-in') == 503,
                  'interruption lost independent maintenance or reopened user admission')
            check(docker.exec(NAME, ['pg_isready', '-q']).returncode == 0
                  and docker.exec(NAME, ['redis-cli', 'ping']).stdout.strip() == 'PONG',
                  'service closure stopped independent infrastructure')
            capture('supervisor-wrapper-and-controller-death-exclusion')
            touch('nltk-crash.release', 'uno-crash.release')
            wait(lambda: present('nltk-crash.completed') and present('uno-crash.completed'), 120)
            check(apply().returncode == 0, 'natural protocol completion did not release authority')
        wait(lambda: client('normal', check_result=False).returncode == 0, 120)
        capture('disconnected-protocols-natural-drain-and-resume')

        with ThreadPoolExecutor(max_workers=2) as pool:
            request = pool.submit(http.body, '/proof/service-nested?label=http-nested')
            wait(lambda: present('http-nested.started') or request.done())
            check(present('http-nested.started'), 'real web caller fixture route is missing')
            before = uv_log()
            operation = pool.submit(apply)
            wait(lambda: status()['admission']['mode'] == 'draining')
            touch('http-nested.release')
            check(json.loads(request.result(timeout=120))['result'] == 'cats', 'accepted HTTP request could not make nested calls')
            nested_receipts('http-nested')
            check(operation.result(timeout=300).returncode == 0, 'accepted HTTP nested request did not drain')
            check(len(uv_log().splitlines()) == len(before.splitlines()) + 1, 'HTTP caller did not retain pre-sync authority')
        wait(lambda: client('normal', check_result=False).returncode == 0, 120)
        capture('accepted-http-nested-pattern-and-conversion-after-closure')

        ids = worker_client('publish', tasks=[{'queue': queue, 'label': 'worker-' + queue, 'task': 'proof.service'}
                                             for queue in ('celery', 'single')])['ids']
        wait(lambda: len(worker_client('snapshot', ids=ids)['started']) == 2, 120)
        before = uv_log()
        with ThreadPoolExecutor(max_workers=1) as pool:
            operation = pool.submit(apply)
            wait(lambda: status()['admission']['mode'] == 'draining')
            worker_client('release', labels=['worker-celery', 'worker-single'])
            # The snapshot helper imports the runtime. Once tasks release their
            # leases, sync may start immediately; inspect only after apply exits.
            check(operation.result(timeout=300).returncode == 0, 'actual worker nested calls did not drain')
            finished = worker_client('snapshot', ids=ids)
            check(len(finished['completed']) == 2, 'nested workers did not complete before apply returned')
            evidence.write_json('worker-nested-results.json', finished)
            check(all(item['state'] == 'SUCCESS' and item['result']['result'] == 'cats'
                      and item['result']['uid'] == 33 for item in finished['results'].values()),
                  'actual accepted workers lost nested service results or privileges')
            nested_receipts('worker-celery', 'worker-single')
            check(len(uv_log().splitlines()) == len(before.splitlines()) + 1, 'worker nested calls did not retain pre-sync authority')
        wait(lambda: client('normal', check_result=False).returncode == 0, 120)
        capture('both-prefork-workers-nested-service-calls-after-closure')

        with ThreadPoolExecutor(max_workers=2) as pool:
            tree = pool.submit(client, 'tree', admitted=True, label='detached-nested', check_result=False)
            wait(lambda: present('detached-nested.started'))
            before = uv_log()
            operation = pool.submit(apply)
            wait(lambda: status()['admission']['mode'] == 'draining')
            processes = docker.exec(NAME, ['ps', '-eo', 'pid,args']).stdout.splitlines()
            wrapper = next(int(line.split()[0]) for line in processes
                           if '/bin/da-executor run-consumer --' in line and line.endswith('/service_client.py tree'))
            docker.exec(NAME, ['kill', '-KILL', str(wrapper)])
            check(tree.result(timeout=30).returncode != 0, 'finite-job wrapper did not die')
            check(not operation.done() and uv_log() == before, 'detached child lost accepted caller authority')
            touch('detached-nested.release')
            wait(lambda: present('detached-nested.completed'), 120)
            check(docker.read_file(NAME, ROOT + 'detached-nested.completed') == 'cats:33',
                  'FD-closing detached child could not finish nested requests after closure')
            nested_receipts('detached-nested')
            check(operation.result(timeout=300).returncode == 0, 'detached nested caller did not release authority naturally')
        wait(lambda: client('normal', check_result=False).returncode == 0, 120)
        capture('detached-nested-caller-survives-wrapper-death')

        # Drain stopped wrappers' idle trees, then launch actual manual/root
        # replacements behind preparing. Full readiness cannot precede them.
        docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'stop', *SERVICES])
        scripts = ('run-websockets.sh', 'run-nltk.sh', 'run-unoserver.sh')
        with ThreadPoolExecutor(max_workers=5) as pool:
            replacement = pool.submit(apply)
            wait(lambda: status().get('admission', {}).get('mode') == 'preparing'
                 and status().get('journal_phase') == 'mutation_started', 180)
            manual = [pool.submit(docker.exec, NAME, ['bash', '/usr/share/docassemble/webapp/' + script],
                                  env={'PYTHONPATH': '/opt/docassemble-executor/src'}, check_result=False)
                      for script in scripts]
            output = replacement.result(timeout=300)
            evidence.write('manual/replacement-apply.txt', output.stdout + output.stderr)
            check(output.returncode == 0, 'idle service drainage/manual replacement failed: ' + output.stdout + output.stderr)
            wait(lambda: client('normal', check_result=False).returncode == 0, 120)
            session = pool.submit(client, 'websocket', label='manual-ws')
            wait(lambda: present('manual-ws.connected'))
            before, pids = uv_log(), service_pids()
            check(json.loads(docker.read_file(NAME, ROOT + 'manual-ws.connected'))['uid'] == 33,
                  'root manual websocket payload did not drop privileges')
            operation = pool.submit(apply)
            wait(lambda: status()['admission']['mode'] == 'draining')
            for script in scripts:
                refused = docker.exec(NAME, ['bash', '/usr/share/docassemble/webapp/' + script], check_result=False)
                check(refused.returncode == 75, 'manual shell service imported runtime after closure')
            docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'start', *SERVICES])
            check(service_pids() == pids and uv_log() == before, 'Supervisor launched duplicates over manual services')
            touch('manual-ws.disconnect')
            check(session.result(timeout=90).returncode == 0, 'manual accepted websocket did not finish')
            check(operation.result(timeout=300).returncode == 0, 'manual service trees did not drain naturally')
            check(all(job.result(timeout=30).returncode == 0 for job in manual), 'manual service launch/drain failed')
        wait(lambda: client('normal', check_result=False).returncode == 0, 120)
        capture('root-manual-services-and-supervisor-singleton-resume')

        recovery = docker.exec(NAME, ['da-executor', 'recover', '--reason', 'service-session-proof'], check_result=False)
        check(recovery.returncode == 0 and status().get('recovery_available') is True, 'restricted recovery failed')
        check(not service_pids(), 'service payload imported during restricted recovery')
        for name in SERVICES:
            docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'restart', name])
            old = int(docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'pid', name]).stdout)
            docker.exec(NAME, ['kill', '-KILL', str(old)])
            wait(lambda name=name, old=old: int(docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'pid', name]).stdout or '0') not in (0, old))
            denied = docker.exec(NAME, ['da-executor', 'run-service', '--kind', name],
                                 env={'DA_RUNTIME_VENV': '/missing-runtime'}, check_result=False)
            check(denied.returncode == 75, 'restricted web admission enabled a service launch')
        for script in scripts:
            check(docker.exec(NAME, ['bash', '/usr/share/docassemble/webapp/' + script], check_result=False).returncode == 75,
                  'recovery shell launcher enabled a service')
        check(not service_pids() and status()['consumer_qualification']['complete'] is True
              and not status()['normal_ready'],
              'autorestart imported runtime or claimed normal readiness during recovery')
        capture('recovery-manual-supervisor-and-autorestart-refusal')
        return 0
    except Exception as error:
        if not (root / 'failure.txt').exists():
            evidence.write('failure.txt', str(error) + '\n' + docker.logs(NAME))
            evidence.write_json('failure-status.json', status())
            evidence.write('failure-processes.txt', docker.exec(NAME, ['ps', '-eo', 'user,pid,ppid,args']).stdout)
            evidence.write('service-logs.txt', docker.exec_shell(NAME, 'cat /usr/share/docassemble/log/managed-service-*.log', check_result=False).stdout)
        raise
    finally:
        evidence.write_json('results.json', results)
        if not args.keep_container:
            docker.rm(NAME)


if __name__ == '__main__':
    raise SystemExit(main())
