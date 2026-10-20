#!/usr/bin/env python3
"""04b2a: real prefork workers, Redis acknowledgement/queue state and drain."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path

from harness import ContainerHttp, Docker, Evidence, check
from maintenance import wait

NAME = 'da-uv-worker-proof'
CONFIG = '/var/run/docassemble/managed-supervisor/supervisord.conf'
RUNTIME = '/usr/share/docassemble/local3.12/bin/python'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', default='da-uv-workers:local')
    parser.add_argument('--evidence-dir')
    parser.add_argument('--keep-container', action='store_true')
    args = parser.parse_args()
    root = Path(args.evidence_dir) if args.evidence_dir else Path(__file__).resolve().parents[1] / 'workers' / 'evidence' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    evidence, results = Evidence(root), []
    docker = Docker(evidence)
    http = ContainerHttp(docker, NAME)
    run = 'proof:drain'

    def client(action, **payload):
        output = docker.exec(NAME, [RUNTIME, '/opt/docassemble-fixture/worker_client.py', action],
                             env={'DA_WORKER_PROOF_ARGS': json.dumps(dict(run=run, **payload))},
                             user='www-data').stdout
        return json.loads(output.split('PROOF_JSON=')[-1])

    def snapshot(ids):
        return client('snapshot', ids=ids)

    def status():
        return json.loads(http.body('/status.json'))

    def capture(label):
        evidence.write(label + '/logs.txt', docker.logs(NAME))
        evidence.write_json(label + '/status.json', status())
        evidence.write(label + '/supervisor.txt', docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'status']).stdout)
        evidence.write(label + '/processes.txt', docker.exec(NAME, ['ps', '-eo', 'user,pid,ppid,args']).stdout)
        evidence.write(label + '/uv-invocations.txt', docker.read_file(NAME, '/var/log/docassemble-uv-invocations.log'))
        evidence.write(label + '/worker-logs.txt', docker.exec_shell(NAME, 'cat /usr/share/docassemble/log/managed-worker-*.log',
                                                                 check_result=False).stdout)
        for file in ('admission.json', 'lifecycle.json', 'applied.json'):
            evidence.write(label + '/' + file, docker.read_file(NAME, '/var/lib/docassemble/managed/' + file))
        results.append({'scenario': label, 'ok': True})
        evidence.note('PASS ' + label)
    try:
        docker.start(NAME, args.image)
        wait(lambda: http.status('/health_check?ready=1') == 200, 900)
        qualification = status()['consumer_qualification']
        check(qualification['complete'] is True and qualification['worker_broker_proof'] == 'redis-prefork'
              and {'celery', 'celerysingle'} <= set(qualification['enabled'])
              and not {'celery', 'celerysingle'} & set(qualification['pending']),
              'worker proof status is missing or claims full consumer qualification')
        supervisors = docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'status']).stdout
        check('celery ' in supervisors and 'celerysingle ' in supervisors,
              'both real workers are not registered with independent Supervisor')
        evidence.write('supervisor.txt', supervisors)
        wait(lambda: len(snapshot([])['queues']) == 2, 120)
        capture('both-real-prefork-workers')
        ids = client('publish', tasks=[{'queue': queue, 'label': queue + suffix}
                                      for queue in ('celery', 'single')
                                      for suffix in ('-active', '-reserved')])['ids']
        wait(lambda: len(snapshot(ids)['started']) == 2, 120)
        accepted = snapshot(ids)
        evidence.write_json('accepted.json', accepted)
        check(len(accepted['broker_unacked']) == 4, 'real prefork reservations were not retained by broker')
        check(sum(map(len, accepted['reserved'].values())) == 2, 'both workers did not reserve work')
        before = docker.read_file(NAME, '/var/log/docassemble-uv-invocations.log')
        with ThreadPoolExecutor(max_workers=1) as pool:
            operation = pool.submit(docker.exec, NAME, ['da-executor', 'apply-runtime', '--revision', 'r1'],
                                    env={'DA_DRAIN_TIMEOUT': '90'}, check_result=False)
            wait(lambda: status().get('admission', {}).get('mode') == 'draining')
            # Accepted tasks are still active; cancellation must be reversible
            # and may not acknowledge/destroy their reserved work.
            wait(lambda: all(not queues for queues in snapshot(ids)['queues'].values()), 12)
            queued = client('publish', tasks=[{'queue': queue, 'label': queue + '-queued'}
                                              for queue in ('celery', 'single')])['ids']
            paused = snapshot(ids + queued)
            evidence.write_json('paused.json', paused)
            check(all(task_id in sum(paused['broker_queued'].values(), []) for task_id in queued),
                  'new work was consumed after closure')
            check(docker.read_file(NAME, '/var/log/docassemble-uv-invocations.log') == before,
                  'sync started over accepted active/reserved work')
            client('release', labels=[queue + suffix for queue in ('celery', 'single')
                                     for suffix in ('-active', '-reserved')])
            output = operation.result(timeout=300)
            evidence.write('apply.txt', output.stdout + output.stderr)
            check(output.returncode == 0, 'real workers did not drain: ' + output.stdout + output.stderr)
            check(len(docker.read_file(NAME, '/var/log/docassemble-uv-invocations.log').splitlines()) == len(before.splitlines()) + 1,
                  'natural worker drain did not precede exactly one frozen sync')
            wait(lambda: len(snapshot(ids + queued)['started']) == 6, 120)
            client('release', labels=[queue + '-queued' for queue in ('celery', 'single')])
            wait(lambda: len(snapshot(ids + queued)['completed']) == 6, 120)
            finished = snapshot(ids + queued)
            evidence.write_json('finished.json', finished)
            check(all(item['state'] == 'SUCCESS' and item['result']['uid'] == 33
                      for item in finished['results'].values()), 'broker results or payload privileges incorrect')
            check(len(finished['broker_unacked']) == 0, 'completed deliveries remain unacknowledged')
            check(sorted(finished['completed']) == ['celery-active', 'celery-queued', 'celery-reserved',
                                                   'single-active', 'single-queued', 'single-reserved'],
                  'natural drain duplicated or lost a delivery')
        capture('active-reserved-drain-preserves-broker-queue')

        run = 'proof:timeout'
        ids = client('publish', tasks=[{'queue': queue, 'label': queue + suffix,
                                       'ack': 'early' if suffix == '-active' else 'late'}
                                      for queue in ('celery', 'single')
                                      for suffix in ('-active', '-reserved')])['ids']
        wait(lambda: len(snapshot(ids)['started']) == 2)
        accepted = snapshot(ids)
        check(len(accepted['broker_unacked']) == 2, 'early ack positive control is not active')
        evidence.write_json('timeout/accepted.json', accepted)
        before = docker.read_file(NAME, '/var/log/docassemble-uv-invocations.log')
        applied = docker.read_file(NAME, '/var/lib/docassemble/managed/applied.json')
        with ThreadPoolExecutor(max_workers=1) as pool:
            operation = pool.submit(docker.exec, NAME, ['da-executor', 'apply-runtime', '--revision', 'r1'],
                                    env={'DA_DRAIN_TIMEOUT': '12'}, check_result=False)
            wait(lambda: status().get('admission', {}).get('mode') == 'draining')
            paused = snapshot(ids)
            check(len(paused['queues']) == 2 and all(not queues for queues in paused['queues'].values()),
                  'active workers were stopped instead of reversibly paused')
            check(operation.result(timeout=90).returncode == 3, 'active early-ack tasks did not exclude mutation')
            check(docker.read_file(NAME, '/var/log/docassemble-uv-invocations.log') == before, 'timeout invoked uv')
            check(docker.read_file(NAME, '/var/lib/docassemble/managed/applied.json') == applied, 'timeout changed applied')
            resumed = snapshot(ids)
            check({name: stats['pid'] for name, stats in resumed['stats'].items()} ==
                  {name: stats['pid'] for name, stats in accepted['stats'].items()},
                  'timeout replaced/terminated accepted workers')
            check(len(resumed['queues']) == 2 and all(queues for queues in resumed['queues'].values()),
                  'timeout did not resume both consumers')
            # Reopened admission is not takeover permission: Supervisor's forced
            # wrapper death must not launch another copy over accepted payloads.
            for name in ('celery', 'celerysingle'):
                old_pid = int(docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'pid', name]).stdout)
                docker.exec(NAME, ['kill', '-KILL', str(old_pid)])
                wait(lambda name=name, old_pid=old_pid: int(docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'pid', name]).stdout or '0') not in (0, old_pid))
            for kind, script in (('normal', 'run-celery.sh'), ('single', 'run-celery-single.sh')):
                denied = docker.exec(NAME, ['da-executor', 'run-worker', '--kind', kind],
                                     env={'DA_RUNTIME_VENV': '/missing-runtime'}, check_result=False)
                check(denied.returncode == 75, 'surviving worker singleton admitted another runtime launch')
                denied = docker.exec(NAME, ['bash', '/usr/share/docassemble/webapp/' + script], check_result=False)
                check(denied.returncode == 75, 'manual shell launch duplicated a surviving worker')
            queued = client('publish', tasks=[{'queue': queue, 'label': queue + '-after-timeout'}
                                              for queue in ('celery', 'single')])['ids']
            wait(lambda: sum(map(len, snapshot(ids + queued)['reserved'].values())) == 4)
            client('release', labels=[queue + suffix for queue in ('celery', 'single')
                                     for suffix in ('-active', '-reserved', '-after-timeout')])
            wait(lambda: len(snapshot(ids + queued)['completed']) == 6)
            finished = snapshot(ids + queued)
            check(all(item['state'] == 'SUCCESS' for item in finished['results'].values()), 'timeout lost accepted tasks')
            check(sorted(finished['completed']) == sorted([queue + suffix for queue in ('celery', 'single')
                                                         for suffix in ('-active', '-reserved', '-after-timeout')]),
                  'timeout duplicated or lost a delivery')
            evidence.write_json('timeout/resumed.json', resumed)
            evidence.write_json('timeout/finished.json', finished)
        capture('early-ack-timeout-reversible-unchanged-consumption')

        run = 'proof:eta'
        ids = client('publish', tasks=[{'queue': queue, 'label': queue, 'countdown': 35}
                                      for queue in ('celery', 'single')])['ids']
        wait(lambda: sum(map(len, snapshot(ids)['scheduled'].values())) == 2)
        before = docker.read_file(NAME, '/var/log/docassemble-uv-invocations.log')
        output = docker.exec(NAME, ['da-executor', 'apply-runtime', '--revision', 'r1'],
                             env={'DA_DRAIN_TIMEOUT': '2'}, check_result=False)
        check(output.returncode == 3, 'scheduled accepted requests were mistaken for idle workers')
        check(docker.read_file(NAME, '/var/log/docassemble-uv-invocations.log') == before, 'ETA drain abort invoked uv')
        client('release', labels=['celery', 'single'])
        wait(lambda: len(snapshot(ids)['completed']) == 2)
        evidence.write_json('eta/finished.json', snapshot(ids))
        capture('scheduled-work-retains-authority')

        run = 'proof:orphan'
        ids = client('publish', tasks=[{'queue': queue, 'label': queue, 'detached': True}
                                      for queue in ('celery', 'single')])['ids']
        wait(lambda: len(snapshot(ids)['started']) == 2)
        accepted = snapshot(ids)
        before = docker.read_file(NAME, '/var/log/docassemble-uv-invocations.log')
        with ThreadPoolExecutor(max_workers=1) as pool:
            operation = pool.submit(docker.exec, NAME,
                                    ['bash', '-c', 'echo $$ > /tmp/worker-controller.pid; exec da-executor apply-runtime --revision r1'],
                                    env={'DA_DRAIN_TIMEOUT': '90'}, check_result=False)
            wait(lambda: status().get('admission', {}).get('mode') == 'draining')
            for name in ('celery', 'celerysingle'):
                check(docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'restart', name]).returncode == 0,
                      'real Supervisor restart failed')
            surviving = snapshot(ids)
            check({name: item['pid'] for name, item in surviving['stats'].items()} ==
                  {name: item['pid'] for name, item in accepted['stats'].items()},
                  'Supervisor timeout killed or duplicated a worker payload')
            for kind in ('normal', 'single'):
                denied = docker.exec(NAME, ['da-executor', 'run-worker', '--kind', kind],
                                     env={'DA_RUNTIME_VENV': '/missing-runtime'}, check_result=False)
                check(denied.returncode == 75, 'manual worker imported runtime behind closed admission')
            for script in ('run-celery.sh', 'run-celery-single.sh'):
                denied = docker.exec(NAME, ['bash', '/usr/share/docassemble/webapp/' + script], check_result=False)
                check(denied.returncode == 75, 'worker shell launcher passed closed admission')
            docker.exec_shell(NAME, 'kill -KILL $(cat /tmp/worker-controller.pid)')
            check(operation.result(timeout=90).returncode != 0, 'applying controller did not die')
            client('release', labels=['celery', 'single'])
            wait(lambda: len(snapshot(ids)['completed']) == 2 and len(snapshot(ids)['child_started']) == 2)
            output = docker.exec(NAME, ['da-executor', 'apply-runtime', '--revision', 'r1'],
                                 env={'DA_DRAIN_TIMEOUT': '2'}, check_result=False)
            check(output.returncode == 3, 'surviving detached children lost drain authority')
            check(docker.read_file(NAME, '/var/log/docassemble-uv-invocations.log') == before,
                  'controller/wrapper death or detached children permitted a sync')
            check(http.status('/health_check') == 200 and http.status('/user/sign-in') == 503,
                  'crash reopened admission or lost independent availability')
            evidence.write_json('orphan/before-child-release.json', snapshot(ids))
            capture('wrapper-controller-death-and-detached-child-exclusion')
            client('release', labels=[], child=True)
            wait(lambda: len(snapshot(ids)['child_completed']) == 2)
            output = docker.exec(NAME, ['da-executor', 'apply-runtime', '--revision', 'r1'],
                                 env={'DA_DRAIN_TIMEOUT': '90'}, check_result=False)
            evidence.write('orphan/apply-after-child.txt', output.stdout + output.stderr)
            check(output.returncode == 0, 'child completion did not release authority')
            wait(lambda: len(snapshot(ids)['queues']) == 2)
            check(all(item['state'] == 'SUCCESS' for item in snapshot(ids)['results'].values()),
                  'interruption lost acknowledged task results')
        capture('natural-child-completion-and-verified-resume')

        # Stop wrappers, then drain their idle payloads. With complete all-role
        # readiness, root/manual replacements must launch behind preparing,
        # never after falsely claiming worker-less normal readiness.
        docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'stop', 'celery', 'celerysingle'])
        run = 'proof:manual'
        with ThreadPoolExecutor(max_workers=3) as pool:
            replacement = pool.submit(docker.exec, NAME, ['da-executor', 'apply-runtime', '--revision', 'r1'],
                                      env={'DA_DRAIN_TIMEOUT': '90'}, check_result=False)
            wait(lambda: status().get('admission', {}).get('mode') == 'preparing'
                 and status().get('journal_phase') == 'mutation_started', 180)
            manual = [pool.submit(docker.exec, NAME, ['bash', '/usr/share/docassemble/webapp/' + script],
                                  check_result=False)
                      for script in ('run-celery.sh', 'run-celery-single.sh')]
            output = replacement.result(timeout=300)
            evidence.write('manual/replacement-apply.txt', output.stdout + output.stderr)
            check(output.returncode == 0, 'idle drainage/manual replacement failed: ' + output.stdout + output.stderr)
            wait(lambda: len(snapshot([])['queues']) == 2)
            ids = client('publish', tasks=[{'queue': queue, 'label': queue}
                                          for queue in ('celery', 'single')])['ids']
            wait(lambda: len(snapshot(ids)['started']) == 2)
            operation = pool.submit(docker.exec, NAME, ['da-executor', 'apply-runtime', '--revision', 'r1'],
                                    env={'DA_DRAIN_TIMEOUT': '90'}, check_result=False)
            wait(lambda: status().get('admission', {}).get('mode') == 'draining')
            client('release', labels=['celery', 'single'])
            wait(lambda: status().get('admission', {}).get('mode') == 'preparing'
                 and status().get('journal_phase') == 'mutation_started', 180)
            docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'start', 'celery', 'celerysingle'])
            check(operation.result(timeout=300).returncode == 0, 'manual workers failed natural drain')
            check(all(job.result(timeout=30).returncode == 0 for job in manual), 'manual launch/drain failed')
            finished = snapshot(ids)
            check(all(item['state'] == 'SUCCESS' and item['result']['uid'] == 33
                      for item in finished['results'].values()), 'root manual launch lost work or executed it as root')
            evidence.write_json('manual/finished.json', finished)
        wait(lambda: len(snapshot([])['queues']) == 2)
        capture('root-manual-shell-workers-natural-drain')

        run = 'proof:unqualified'
        ids = client('publish', tasks=[{'queue': queue, 'label': queue, 'task': task}
                                      for queue, task in (('celery', 'tasks.update_packages'),
                                                          ('single', 'tasks.reset_server'))])['ids']
        wait(lambda: len(snapshot(ids)['queues']) == 2 and all(not queues for queues in snapshot(ids)['queues'].values()))
        retained = snapshot(ids)
        check(all(task_id in sum(retained['broker_queued'].values(), []) for task_id in ids),
              'unsupported management delivery was acknowledged/discarded')
        check(not retained['completed'] and all(item['state'] == 'PENDING' for item in retained['results'].values()),
              'unsupported management task executed')
        evidence.write_json('unqualified/retained.json', retained)
        capture('unqualified-worker-management-retained-not-executed')

        # Only the broker backend with an actual workload proof may be enabled.
        docker.exec(NAME, ['/usr/share/docassemble/executor-venv/bin/python', '-c',
                          "import yaml; p='/usr/share/docassemble/config/config.yml'; c=yaml.safe_load(open(p)); "
                          "c['rabbitmq']='amqp://guest:guest@127.0.0.1:1//'; yaml.safe_dump(c,open(p,'w'))"])
        output = docker.exec(NAME, ['da-executor', 'apply-runtime', '--revision', 'r1'],
                             env={'DA_DRAIN_TIMEOUT': '90', 'DA_ROLE_READY_TIMEOUT': '15'}, check_result=False)
        evidence.write('unqualified/broker-apply.txt', output.stdout + output.stderr)
        check(output.returncode != 0 and not status()['normal_ready']
              and status()['admission']['mode'] != 'open',
              'unproved broker backend claimed full normal readiness')
        broker_health = {role['name']: role['ok'] for role in status()['role_health']
                         if role['name'] in ('celery', 'celerysingle')}
        check(broker_health == {'celery': False, 'celerysingle': False}, 'unproved backend was reported healthy')
        wait(lambda: all('unqualified Celery broker backend: amqp' in docker.read_file(
                            NAME, '/usr/share/docassemble/log/managed-worker-' + kind + '.log')
                         for kind in ('normal', 'single')), 30)
        capture('unproved-broker-backend-explicitly-refused')

        output = docker.exec(NAME, ['da-executor', 'recover', '--reason', 'worker-proof'], check_result=False)
        check(output.returncode == 0, 'explicit restricted recovery failed')
        check(status().get('recovery_available') is True and not status()['normal_ready']
              and status()['consumer_qualification']['complete'] is True,
              'recovery availability was confused with normal readiness')
        for name in ('celery', 'celerysingle'):
            check(docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'restart', name]).returncode == 0,
                  'recovery Supervisor restart failed')
            old_pid = int(docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'pid', name]).stdout)
            docker.exec(NAME, ['kill', '-KILL', str(old_pid)])
            wait(lambda name=name, old_pid=old_pid: int(docker.exec(NAME, ['supervisorctl', '-c', CONFIG, 'pid', name]).stdout or '0') not in (0, old_pid))
        for kind in ('normal', 'single'):
            denied = docker.exec(NAME, ['da-executor', 'run-worker', '--kind', kind],
                                 env={'DA_RUNTIME_VENV': '/missing-runtime'}, check_result=False)
            check(denied.returncode == 75, 'restricted HTTP admission enabled a worker')
        processes = docker.exec(NAME, ['ps', '-eo', 'pid,ppid,args']).stdout
        check('worker_runtime.py' not in processes, 'runtime worker imported during restricted recovery')
        capture('recovery-manual-supervisor-and-autorestart-gates')
        return 0
    except Exception as error:
        evidence.write('failure.txt', str(error) + '\n' + docker.logs(NAME))
        evidence.write_json('failure-status.json', status())
        evidence.write('failure-processes.txt', docker.exec(NAME, ['ps', '-eo', 'user,pid,ppid,args']).stdout)
        evidence.write('worker-logs.txt', docker.exec_shell(NAME, 'cat /usr/share/docassemble/log/managed-worker-*.log',
                                                         check_result=False).stdout)
        raise
    finally:
        evidence.write_json('results.json', results)
        if not args.keep_container:
            docker.rm(NAME)


if __name__ == '__main__':
    raise SystemExit(main())
