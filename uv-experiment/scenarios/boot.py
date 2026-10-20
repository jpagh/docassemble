#!/usr/bin/env python3
"""Ticket 03: real frozen boot, flag policy, inventories and fail-closed gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import ContainerHttp, Docker, Evidence, Failure, check  # noqa: E402

NAME = 'da-uv-boot-proof'
SNAPSHOT = 'da-uv-boot-snapshot:local'
STATE = '/var/lib/docassemble/managed'
RUNTIME = '/usr/share/docassemble/local3.12'
AUDIT = '/var/log/docassemble-uv-invocations.log'


def wait(docker: Docker, predicate, timeout=900):
    http = ContainerHttp(docker, NAME)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            payload = json.loads(http.body('/health_status'))
        except (ValueError, Failure):
            time.sleep(2)
            continue
        if predicate(payload):
            return payload
        if 'refused' in (payload.get('detail') or '') or payload.get('journal_phase') == 'recovery_failed':
            raise Failure('boot failed closed: ' + json.dumps(payload))
        time.sleep(2)
    raise Failure('boot status did not converge: ' + docker.logs(NAME)[-2000:])


def capture(docker: Docker, evidence: Evidence, scenario: str):
    evidence.write(scenario + '/logs.txt', docker.logs(NAME))
    for filename in ('desired.json', 'applied.json', 'initialized.json', 'lifecycle.json'):
        evidence.write(scenario + '/' + filename, docker.read_file(NAME, STATE + '/' + filename))
    evidence.write(scenario + '/uv-invocations.txt', docker.read_file(NAME, AUDIT))
    raw = ContainerHttp(docker, NAME).body('/health_status')
    evidence.write(scenario + '/status.json', raw)


def counts(docker: Docker):
    lines = [line.split('\t', 1)[-1] for line in docker.read_file(NAME, AUDIT).splitlines()]
    solves = [line for line in lines if line.startswith(('lock', 'build', 'pip compile')) or (line.startswith('sync') and '--frozen' not in line)]
    syncs = [line for line in lines if line.startswith('sync')]
    return len(solves), len(syncs)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', default='da-uv-boot:local')
    parser.add_argument('--evidence-dir')
    args = parser.parse_args()
    directory = Path(args.evidence_dir) if args.evidence_dir else Path(__file__).resolve().parents[1] / 'evidence' / ('boot-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    evidence = Evidence(directory)
    docker = Docker(evidence)
    results = []
    def passed(name, detail):
        capture(docker, evidence, name)
        results.append({'scenario': name, 'ok': True, 'detail': detail})
        evidence.note('PASS ' + name + ': ' + detail)
    try:
        evidence.write('image-id.txt', docker.run(['image', 'inspect', args.image, '--format', '{{.Id}}']).stdout)
        docker.start(NAME, args.image)
        wait(docker, lambda p: p.get('normal_ready') is True)
        evidence.write('manifest.json', docker.read_file(NAME, '/usr/share/docassemble/managed/bundles/manifest.json'))
        # Audit is emitted by the wrapper which execs the actual pinned uv.
        before = counts(docker)
        check(before[0] == 21, 'only seven locks and fourteen explicit wheel builds at image construction may resolve: ' + str(before))
        check(before[1] == 6, 'five image frozen syncs plus one fresh boot sync expected: ' + str(before))
        passed('fresh-true-frozen', 'fresh fixture seeded from retained full lock; HTTP ready after verified initialization')
        python = '/usr/share/docassemble/executor-venv/bin/python'
        inventory = docker.exec(NAME, [python, '-c', 'import json; from pathlib import Path; from docassemble_executor.inventory import inspect_environment; print(json.dumps(inspect_environment(Path("' + RUNTIME + '")), sort_keys=True))']).stdout
        manifest = json.loads(docker.read_file(NAME, '/usr/share/docassemble/managed/bundles/manifest.json'))
        check(json.loads(inventory) == manifest['inventories']['runtime-r1']['distributions'], 'actual full inventory differs from image')
        evidence.write('actual-runtime-inventory.json', inventory)
        protected = ['/usr/share/docassemble/executor-venv', '/opt/docassemble-executor', '/usr/local/bin/uv', '/usr/local/libexec/uv-pinned', '/usr/share/docassemble/managed/bundles', '/usr/share/docassemble/uv-python', STATE]
        for user in ('www-data', 'da-environment'):
            for path in protected:
                check(docker.exec(NAME, ['test', '-w', path], user=user, check_result=False).returncode != 0,
                      user + ' can modify protected tooling/state: ' + path)
        check(docker.exec(NAME, ['test', '-w', RUNTIME], user='www-data', check_result=False).returncode != 0, 'application can modify runtime')
        evidence.write_json('ownership.json', {'protected_paths': protected, 'denied_users': ['www-data', 'da-environment'], 'runtime_owner': docker.exec(NAME, ['stat', '-c', '%U', RUNTIME]).stdout.strip()})
        # Keep the prepared core cache for the later safety-recovery cases.
        # The separate cold-cache restart below proves retention, not recovery.
        docker.commit(NAME, SNAPSHOT)
        # Required already-installed material survives no network and empty cache.
        docker.exec_shell(NAME, 'rm -rf /var/cache/docassemble/uv; rm -rf /tmp/da-sentinels')
        docker.run(['network', 'disconnect', 'bridge', NAME])
        check(json.loads(docker.run(['inspect', NAME, '--format', '{{json .NetworkSettings.Networks}}']).stdout) == {}, 'offline test still has a network')
        docker.restart(NAME)
        wait(docker, lambda p: p.get('normal_ready') is True)
        check(counts(docker) == before, 'converged offline restart invoked uv: ' + str(counts(docker)))
        passed('offline-converged-restart', 'empty cache/no network; zero startup resolver or sync calls; installed inventory validated')
        pending = "printf '%s\\n' '{\"revision\":\"r2\",\"core_baseline\":\"r1\",\"packages\":[\"slowfixture\"]}' > " + STATE + '/desired.json'
        for mode in ('initial', 'false'):
            docker.start(NAME, SNAPSHOT, network='none', env={'DAUPDATEONSTART': mode}, cold_setup=pending)
            wait(docker, lambda p: p.get('normal_ready') is True)
            check(json.loads(docker.read_file(NAME, STATE + '/applied.json'))['revision'] == 'r1', 'mode converged pending desired unexpectedly')
            check(counts(docker) == before, 'retaining startup invoked uv')
            passed('pending-' + mode, 'verified r1 retained; desired r2 unchanged; no sync/solve')
        docker.start(NAME, args.image, env={'DAUPDATEONSTART': 'initial'})
        wait(docker, lambda p: p.get('normal_ready') is True)
        initial_counts = counts(docker)
        docker.exec_shell(NAME, 'rm -rf /var/cache/docassemble/uv')
        docker.run(['network', 'disconnect', 'bridge', NAME])
        docker.restart(NAME)
        wait(docker, lambda p: p.get('normal_ready') is True)
        check(counts(docker) == initial_counts, 'initial repeated initialization')
        passed('fresh-initial-and-restart', 'initial sync once; restart retains verified environment offline')
        # true convergence uses an existing image lock, not a new candidate solve.
        docker.start(NAME, SNAPSHOT, env={'DAUPDATEONSTART': 'true'}, cold_setup=pending)
        wait(docker, lambda p: p.get('normal_ready') is True)
        check(json.loads(docker.read_file(NAME, STATE + '/applied.json'))['revision'] == 'r2', 'true did not converge')
        check(counts(docker) == (before[0], before[1] + 1), 'true did not use exactly one frozen sync')
        uid = docker.read_file(NAME, AUDIT).splitlines()[-1].split('\t')[0]
        expected_uid = docker.exec(NAME, ['id', '-u', 'da-environment']).stdout.strip()
        check(uid == expected_uid and uid != '0', 'installer did not run as environment account: ' + uid)
        evidence.write('actual-installer-uid.txt', uid + '\n')
        passed('pending-true-frozen', 'exact r2 wheel sync ran unprivileged; zero boot resolver/build calls')
        for mode in ('true', 'initial', 'false'):
            docker.start(NAME, SNAPSHOT, network='none', env={'DAUPDATEONSTART': mode, 'DAALLOWUPDATES': 'false'},
                         cold_setup='rm -rf /tmp/da-sentinels ' + RUNTIME + '/lib/python3.12/site-packages/docassemble/webapp')
            payload = wait(docker, lambda p: p.get('recovery_available') is True)
            check(payload.get('normal_ready') is False and payload.get('recovery_attempt') == 1,
                  'safety recovery did not remain restricted')
            check(counts(docker) == (before[0], before[1] + 1), 'safety recovery solved or retried')
            passed('safety-recovery-' + mode, 'unhealthy verified runtime restores core once from retained prepared cache without network even with DAALLOWUPDATES=false')
        gates = [
            ('fresh-false', args.image, {'DAUPDATEONSTART': 'false'}, '', 'verified'),
            ('split-role', SNAPSHOT, {'CONTAINERROLE': 'web'}, '', 'all-role'),
            ('read-only-configured', SNAPSHOT, {'DAREADONLYFILESYSTEM': 'true'}, '', 'writable'),
            ('unwritable-runtime', SNAPSHOT, {}, 'chmod a-w ' + RUNTIME, 'unwritable'),
            ('unwritable-state', SNAPSHOT, {}, 'chmod a-w ' + STATE, 'unwritable'),
            ('invalid-startup-mode', SNAPSHOT, {'DAUPDATEONSTART': 'sometimes'}, '', 'DAUPDATEONSTART'),
            ('direct-flag-off', SNAPSHOT, {}, 'export DA_UV_MANAGED=false; exec /usr/share/docassemble/executor-venv/bin/da-executor boot', 'disabled'),
            ('corrupt-desired', SNAPSHOT, {}, "echo corrupt > " + STATE + '/desired.json', 'refused'),
            ('corrupt-journal', SNAPSHOT, {}, "echo corrupt > " + STATE + '/lifecycle.json', 'refused'),
            ('empty-journal', SNAPSHOT, {}, "echo '{}' > " + STATE + '/lifecycle.json', 'corrupt'),
            ('corrupt-applied', SNAPSHOT, {}, "echo '{}' > " + STATE + '/applied.json', 'disagree'),
            ('corrupt-initialization', SNAPSHOT, {}, "echo '{}' > " + STATE + '/initialized.json', 'migration'),
            ('missing-desired', SNAPSHOT, {}, 'rm ' + STATE + '/desired.json', 'adoption'),
            ('legacy-adoption-gated', SNAPSHOT, {}, 'rm ' + STATE + '/initialized.json ' + STATE + '/lifecycle.json ' + STATE + '/applied.json', 'adoption'),
            ('image-migration-gated', SNAPSHOT, {}, "sed -i 's/\"target_id\": \"[^\"]*\"/\"target_id\": \"different-image\"/' " + STATE + '/initialized.json', 'migration'),
        ]
        for name, image, env, setup, message in gates:
            cold = 'rm -rf /tmp/da-sentinels' + ('; ' + setup if setup else '')
            docker.start(NAME, image, network='none', env=env, cold_setup=cold)
            payload = wait(docker, lambda p, message=message: message in (p.get('detail') or '') and 'refused' in (p.get('detail') or ''))
            check(not payload.get('normal_ready') and not payload.get('recovery_available'), 'failed gate advertised readiness')
            check(docker.exec_shell(NAME, 'test ! -e /var/run/docassemble/ready && test ! -e /tmp/da-sentinels', check_result=False).returncode == 0,
                  'failed gate activated user code or readiness')
            check(ContainerHttp(docker, NAME).status('/health_check') == 200, 'independent liveness unavailable')
            check(counts(docker)[0] == 21, 'failed gate invoked resolver/build')
            passed(name, 'static independent failure status; no user imports or normal readiness')
        for record in ('desired.json', 'lifecycle.json'):
            docker.start(NAME, SNAPSHOT, env={'DA_UV_MANAGED': 'false'}, cold_setup='echo corrupt > ' + STATE + '/' + record)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if 'legacy boot refused' in docker.logs(NAME):
                    break
                time.sleep(1)
            check('legacy boot refused' in docker.logs(NAME), 'flag-off escaped durable managed safety')
            evidence.write('flag-off-' + record + '.txt', docker.logs(NAME))
        results.append({'scenario': 'flag-off-durable-evidence', 'ok': True, 'detail': 'corrupt records still refuse legacy boot'})
        evidence.note('PASS flag-off-durable-evidence')
    except Exception as error:
        evidence.write('failure.txt', str(error) + '\n' + docker.logs(NAME))
        results.append({'scenario': 'verification', 'ok': False, 'detail': str(error)})
        evidence.note('FAIL: ' + str(error))
    finally:
        docker.rm(NAME)
        docker.run(['image', 'rm', SNAPSHOT], check_result=False)
        evidence.write_json('results.json', results)
    return 0 if all(result['ok'] for result in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
