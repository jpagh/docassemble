"""Runtime-only Celery adapter, executed after independent launch admission.

Use the real application app/prefork pool. Cancel broker consumption reversibly
while accepted active, reserved, ETA and rate-limited requests finish. Warm exit
only when these requests finish; the guardian still owns surviving descendants.
This module is never imported by the executor/controller.
"""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import signal
import socket
import sys
from urllib.parse import urlsplit

# The management code stays image-owned, not installed into the runtime venv.
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from celery import bootsteps, signals  # noqa: E402
from celery.worker import state, strategy  # noqa: E402
from celery.worker.control import inspect_command  # noqa: E402
from docassemble_executor.admission import Admission  # noqa: E402
from docassemble_executor.paths import ExecutorPaths  # noqa: E402

logger = logging.getLogger(__name__)
# Launch qualification must not enable the legacy package/reset control plane.
# Broader direct CLI, intent and stale-task fences remain 04b3.
UNQUALIFIED_TASKS = {'tasks.update_packages', 'tasks.reset_server'}


def admitted_strategy(task, app, consumer, **kwargs):
    handler = strategy.default(task, app, consumer, **kwargs)
    admission = Admission(ExecutorPaths.from_env().state_dir)

    def receive(message, body, ack, reject, callbacks, **options):
        with admission.lease(consumer=True) as lease:
            if lease is None or task.name in UNQUALIFIED_TASKS:
                # No ack and no user task execution. Cancel before returning the
                # delivery to the broker to avoid a closed-gate redelivery loop.
                consumer.managed_drain.pause(consumer)
                if task.name in UNQUALIFIED_TASKS:
                    consumer.managed_drain.blocked = True
                    logger.error('managed worker: unqualified management task retained: %s', task.name)
                message.reject(requeue=True)
                return
            return handler(message, body, ack, reject, callbacks, **options)

    return receive


@inspect_command()
def managed_health(worker_state, **kwargs):
    drain = worker_state.consumer.managed_drain
    try:
        pool = worker_state.consumer.pool.info
        pids = pool.get('processes', [])
        alive = len(pids) == pool.get('max-concurrency') and bool(pids)
        for pid in pids:
            stat = (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[-1].split()
            alive = alive and stat[0] not in ('Z', 'T', 't')
    except (OSError, AttributeError, TypeError, IndexError):
        alive = False
    return {'ready': alive and not drain.stopping and not drain.blocked}


class ManagedDrain(bootsteps.StartStopStep):
    requires = {'celery.worker.consumer.tasks:Tasks'}

    def __init__(self, consumer, **kwargs):
        self.admission = Admission(ExecutorPaths.from_env().state_dir)
        self.queues = list(consumer.app.amqp.queues.consume_from)
        self.accepted: dict = {}
        self.paused = False
        self.blocked = False
        self.stopping = False
        self.timer = None
        consumer.managed_drain = self

    def start(self, consumer):
        signals.task_received.connect(self.received, sender=consumer, weak=False)
        # Broker channels and request state belong to the consumer's event loop,
        # not the prefork pool or the timer thread.
        self.timer = consumer.timer.call_repeatedly(0.1, consumer.call_soon,
                                                   (self.tick, consumer))
        consumer.call_soon(self.tick, consumer)

    def received(self, request, **kwargs):
        self.accepted[request.id] = request

    def pause(self, consumer):
        for queue in self.queues:
            if consumer.task_consumer.consuming_from(queue):
                consumer.cancel_task_queue(queue)
        self.paused = True

    def tick(self, consumer):
        if self.stopping:
            return
        admission = self.admission.state()
        opened = admission['mode'] == 'open' and admission.get('consumers') is True
        if not opened:
            self.pause(consumer)
        elif self.paused and not self.blocked:
            for queue in self.queues:
                consumer.add_task_queue(queue)
            self.paused = False
        in_pool = {req.id for req in state.active_requests | state.reserved_requests}
        # ETA/rate-limited requests are not necessarily in reserved_requests.
        # Keep their actual Request until it is acknowledged/rejected AND no
        # longer active/reserved. Early ack is never task completion authority.
        self.accepted = {key: req for key, req in self.accepted.items()
                         if key in in_pool or not req.acknowledged}
        if not opened and admission['mode'] != 'preparing' and not self.accepted:
            self.stopping = True
            # Main process only, after idle: Celery's warm shutdown closes its
            # consumer and joins the pool. Never TERM the pool/process group.
            os.kill(os.getpid(), signal.SIGTERM)

    def stop(self, consumer):
        if self.timer is not None:
            self.timer.cancel()
        signals.task_received.disconnect(self.received, sender=consumer)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--kind', choices=['normal', 'single'], required=True)
    kind = parser.parse_args().kind
    from docassemble.webapp.worker import celery_app
    from docassemble.webapp.config import daconfig

    broker = celery_app.conf.broker_url
    from docassemble_executor.checkpoint_restore import require_checkpoint_broker
    require_checkpoint_broker(ExecutorPaths.from_env(), celery_app.conf.broker_read_url or broker)
    scheme = urlsplit(broker).scheme if isinstance(broker, str) else 'multiple'
    if scheme != 'redis' or ';' in broker:
        raise ValueError('unqualified Celery broker backend: ' + scheme)
    # Celery may normally cancel late-ack tasks on broker loss. That is not an
    # acceptable managed drain protocol: accepted work retains authority.
    celery_app.conf.worker_cancel_long_running_tasks_on_connection_loss = False
    celery_app.steps['consumer'].add(ManagedDrain)
    for task in celery_app.tasks.values():
        if task.Strategy != 'celery.worker.strategy:default':
            raise ValueError('managed worker cannot qualify a custom task strategy: ' + task.name)
        task.Strategy = admitted_strategy
    os.environ.pop('REMAP_SIGTERM', None)  # never turn idle warm shutdown into QUIT
    configured = daconfig.get('celery processes', min(os.cpu_count() or 1,
                                                    int(daconfig.get('max celery processes', 2))))
    # Preserve the legacy normal-worker derivation; the single queue stays one.
    count = 1 if kind == 'single' else max(1, int(configured) - 1)
    worker = celery_app.Worker(queues=['single' if kind == 'single' else 'celery'],
                              hostname='da-' + kind + '@' + socket.gethostname(),
                              concurrency=count, pool='prefork', loglevel='INFO')
    worker.start()
    return worker.exitcode or 0


if __name__ == '__main__':
    raise SystemExit(main())
