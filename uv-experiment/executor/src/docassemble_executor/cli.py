"""Command line interface for the independent lifecycle executor."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import NoReturn

from .controller import (
    _create_tables,
    apply_runtime,
    boot,
    load_context,
    log,
    run_recovery,
    status,
)
from .authority import MutationLock, RuntimeLock
from .health import probe_runtime
from .paths import ExecutorPaths
from .startup import managed_requested


def build_parser() -> argparse.ArgumentParser:
    parser = _CommandArgumentParser(prog="da-executor", description="uv-managed docassemble lifecycle executor")
    sub = parser.add_subparsers(dest="command", required=True)

    cache_parser = sub.add_parser('cache', help='operator/root reusable uv cache usage, clear or prune (no history GC)')
    cache_parser.add_argument('action', choices=['usage', 'clear', 'prune'])

    restore_parser = sub.add_parser('restore', help='independent cold restore before target selection (empty replacement DB only)')
    restore_parser.add_argument('--dump', required=True)
    restore_parser.add_argument('--files', required=True)
    restore_parser.add_argument('--authorization', required=True)
    restore_parser.add_argument('--prefix', default='')

    checkpoint_parser = sub.add_parser('restore-checkpoint', help='operator-only FULL data rewind; never package-only rollback')
    checkpoint_parser.add_argument('--checkpoint', required=True)
    checkpoint_parser.add_argument('--authorization', required=True)
    checkpoint_parser.add_argument('--prefix', default='')
    checkpoint_parser.add_argument('--environment')

    boot_parser = sub.add_parser("boot", help="boot into normal service, restricted recovery, or maintenance")
    boot_parser.add_argument("--force", choices=["recovery"], default=None)

    apply_parser = sub.add_parser("apply-runtime", help="apply a frozen runtime revision")
    apply_parser.add_argument("--revision", required=True)
    apply_parser.add_argument(
        "--fault-after-seconds",
        type=float,
        default=None,
        help="fixture fault injection: kill the frozen sync after N seconds (leaves the journal at mutation_started)",
    )

    recover_parser = sub.add_parser("recover", help="restore the retained core once and serve restricted recovery")
    recover_parser.add_argument("--reason", default="operator-requested")

    consumer_parser = sub.add_parser('run-consumer', help='launch a finite helper under normal admission')
    consumer_parser.add_argument('--wait', action='store_true', help='wait independently behind closed admission')
    consumer_parser.add_argument('payload', nargs=argparse.REMAINDER)
    sub.add_parser('run-watchdog', help='supervised, safely stoppable runtime observer')
    worker_parser = sub.add_parser('run-worker', help='launch a qualified Celery worker under normal admission')
    worker_parser.add_argument('--kind', choices=['normal', 'single'], required=True)
    worker_parser.add_argument('--wait', action='store_true', help='wait outside runtime for admission and singleton authority')

    service_parser = sub.add_parser('run-service', help='launch a qualified session/request service')
    service_parser.add_argument('--kind', choices=['websockets', 'nltk', 'conversion'], required=True)
    service_parser.add_argument('--wait', action='store_true', help='wait independently behind admission')

    helper_parser = sub.add_parser('run-helper', help='run one admitted cron/cleanup/mail phase; never replay missed jobs')
    helper_parser.add_argument('--kind', choices=['cron', 'hourly', 'daily', 'weekly', 'monthly', 'mail'], required=True)
    helper_parser.add_argument('--cron-type', choices=['cron_hourly', 'cron_daily', 'cron_weekly', 'cron_monthly'], default='cron_daily')

    schedule_parser = sub.add_parser('run-schedule', help='run separated managed schedule phases once')
    schedule_parser.add_argument('--kind', choices=['hourly', 'daily', 'weekly', 'monthly'], required=True)
    backup_parser = sub.add_parser('run-backup', help='independent backup with trusted resolved environment (no runtime imports)')
    backup_parser.add_argument('--environment', default='/etc/docassemble/managed-backup.json')
    backup_parser.add_argument('--phase', choices=['full', 'hourly'], default='full')

    development_parser = sub.add_parser('development-install', help='exclusive, non-root local development override (not locked convergence)')
    development_parser.add_argument('--source', required=True)
    sub.add_parser('reload-web', help='ordinary graceful web reload; no install, sync or schema work')
    shutdown_parser = sub.add_parser('shutdown', help='drain first, then stop infrastructure with OS tooling only')
    shutdown_parser.add_argument('--timeout', type=float, default=30)

    installation_parser = sub.add_parser('poll-installation', help='DB frozen materialization; normal apply ONLY for explicitly configured qualified image bridge')
    installation_parser.add_argument('--store', required=True)
    installation_parser.add_argument('--baseline', required=True, help='trusted image/operator baseline JSON')
    installation_parser.add_argument('--prefix', default='')
    installation_parser.add_argument('--once', action='store_true')

    durable_parser = sub.add_parser('run-durable-work', help='Supervisor-owned continuous qualified-bridge durable work')
    durable_parser.add_argument('--kind', choices=['preparation', 'installation'], required=True)

    sub.add_parser("status", help="print the last written status document")

    sub.add_parser("probe", help="probe the runtime environment only")

    sub.add_parser("init-db", help="fixture setup: role/database, normal-mode schema initialization")

    return parser


class _CommandArgumentParser(argparse.ArgumentParser):
    def parse_args(self, args=None, namespace=None):
        arguments = sys.argv[1:] if args is None else args
        self._restore_invocation = bool(arguments and arguments[0] in ('restore', 'restore-checkpoint'))
        return super().parse_args(args, namespace)

    def error(self, message: str) -> NoReturn:
        if self.prog.endswith((' restore', ' restore-checkpoint')) or getattr(self, '_restore_invocation', False):
            from .cold_restore import RESTORE_ARGUMENT_ERROR
            self.exit(2, RESTORE_ARGUMENT_ERROR)
        else:
            super().error(message)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = ExecutorPaths.from_env()
    if args.command == 'cache':
        from .cache import cache_command
        return cache_command(paths, args.action)
    if args.command == 'restore':
        from .cold_restore import main as restore_main
        return restore_main(['--dump', args.dump, '--files', args.files, '--authorization', args.authorization,
                             '--prefix', args.prefix])
    if args.command == 'restore-checkpoint':
        from .checkpoint_restore import main as checkpoint_main
        arguments = ['--checkpoint', args.checkpoint, '--authorization', args.authorization, '--prefix', args.prefix]
        if args.environment:
            arguments += ['--environment', args.environment]
        return checkpoint_main(arguments)
    if args.command == 'run-durable-work':
        from .durable_work import run_durable_work
        run_durable_work(paths, args.kind)
        return 0
    if args.command == 'poll-installation':
        from pathlib import Path
        from sqlalchemy import create_engine
        from .installation import Installation, InstallationPoller

        engine = create_engine(os.environ['DA_PREPARATION_DATABASE_URL'])
        try:
            poller = InstallationPoller(Installation(engine, lambda *_: False, args.prefix),
                                        paths, Path(args.store), Path(args.baseline))
            if args.once:
                print(poller.run_once(), flush=True)
            else:
                poller.run()
            return 0
        except Exception:
            log('installation polling stopped; uncertain work remains fenced')
            return 1
        finally:
            engine.dispose()
    if args.command == 'run-consumer':
        from .consumers import run_consumer

        command = args.payload[1:] if args.payload[:1] == ['--'] else args.payload
        if not command:
            raise ValueError('run-consumer requires a command after --')
        return run_consumer(paths, command, wait=args.wait)
    if args.command == 'run-watchdog':
        from .consumers import run_watchdog

        return run_watchdog(paths)
    if args.command == 'run-worker':
        from .consumers import run_worker

        return run_worker(paths, args.kind, wait=args.wait)
    if args.command == 'run-service':
        from .consumers import run_service

        return run_service(paths, args.kind, wait=args.wait)
    if args.command == 'run-helper':
        from .helpers import run_helper

        return run_helper(paths, args.kind, cron_type=args.cron_type)
    if args.command == 'run-schedule':
        from .helpers import run_schedule

        return run_schedule(paths, args.kind)
    if args.command == 'run-backup':
        from pathlib import Path
        from .scheduled_backup import run_backup

        return run_backup(paths, Path(args.environment), phase=args.phase)
    if args.command == 'development-install':
        from pathlib import Path
        from .development import install

        return install(paths, Path(args.source))
    if args.command == 'reload-web':
        from .development import reload_web

        return reload_web(paths)
    if args.command == 'shutdown':
        from .infrastructure import shutdown

        return shutdown(paths, args.timeout)
    if args.command == "boot":
        return boot(paths, force=args.force)
    if args.command == "apply-runtime":
        return apply_runtime(paths, args.revision, fault_after_seconds=args.fault_after_seconds)
    if args.command == "recover":
        if not managed_requested(os.environ.get('DA_UV_MANAGED') == 'true', paths.state_dir):
            raise ValueError('managed recovery requires DA_UV_MANAGED=true')
        context = load_context(paths, start_infrastructure=False)
        mode = run_recovery(context, args.reason)
        print(json.dumps({"mode": mode}))
        return 0 if mode == 'recovery' else 1
    if args.command == "status":
        print(json.dumps(status(paths), indent=2, sort_keys=True))
        return 0
    if not managed_requested(os.environ.get('DA_UV_MANAGED') == 'true', paths.state_dir):
        raise ValueError('managed runtime command requires DA_UV_MANAGED=true')
    with MutationLock(paths.state_dir), RuntimeLock(paths.state_dir, shared=True):
        return _runtime_command(args, paths)


def _runtime_command(args, paths: ExecutorPaths) -> int:
    if args.command == "probe":
        context = load_context(paths, start_infrastructure=False)
        result = probe_runtime(paths.runtime_venv, paths.config_file, context.expected_core_versions)
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0 if result.ok else 1
    if args.command == "init-db":
        if any((paths.state_dir / name).exists() or (paths.state_dir / name).is_symlink()
               for name in ('initialized.json', 'applied.json', 'lifecycle.json', 'admission.json')):
            raise ValueError('direct schema initialization is unsupported after managed adoption')
        context = load_context(paths)
        log("infrastructure checks: " + "; ".join(check.name + "=" + str(check.ok) for check in context.infra_checks))
        if not all(check.ok for check in context.infra_checks):
            return 1
        check = _create_tables(context)
        log(check.name + ": " + ("ok" if check.ok else check.detail))
        if not check.ok:
            return 1
        if not context.applied.revision:
            context.applied.mark(context.desired.revision or context.default_runtime_revision, context.core_baseline)
            context.applied.save(paths.applied_file)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
