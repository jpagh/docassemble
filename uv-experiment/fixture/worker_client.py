#!/usr/bin/env python3
"""Disposable proof client: real broker queues/acknowledgements and results."""
import argparse
import json
import os

from redis import Redis
import docassemble.base.config

docassemble.base.config.load(in_celery=True)
from docassemble.webapp.tasks.app import celery_app  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['publish', 'snapshot', 'release'])
    args = parser.parse_args()
    payload = json.loads(os.environ['DA_WORKER_PROOF_ARGS'])
    run = payload['run']
    control = Redis(db=2, decode_responses=True)
    if args.action == 'publish':
        ids = []
        for item in payload['tasks']:
            result = celery_app.send_task(item.get('task', 'proof.' + item.get('ack', 'late')),
                                          args=[run, item['label'], item.get('detached', False)],
                                          queue=item['queue'], countdown=item.get('countdown'))
            ids.append(result.id)
        value = {'ids': ids}
    elif args.action == 'release':
        for label in payload['labels']:
            control.set(run + ':release:' + label, 'allowed to finish')
        if payload.get('child'):
            control.set(run + ':child-release', 'allowed to finish')
        value = {'released': payload['labels']}
    else:
        inspector = celery_app.control.inspect(timeout=1)
        broker = Redis.from_url(celery_app.conf.broker_url, decode_responses=True)
        value = {
            'stats': inspector.stats() or {},
            'active': inspector.active() or {},
            'reserved': inspector.reserved() or {},
            'scheduled': inspector.scheduled() or {},
            'queues': inspector.active_queues() or {},
            'broker_queued': {queue: [json.loads(raw)['headers']['id'] for raw in broker.lrange(queue, 0, -1)]
                              for queue in ('celery', 'single')},
            'broker_unacked': [json.loads(raw)[0]['headers']['id'] for raw in broker.hvals('unacked')],
            'started': control.lrange(run + ':started', 0, -1),
            'completed': control.lrange(run + ':completed', 0, -1),
            'child_started': control.lrange(run + ':child-started', 0, -1),
            'child_completed': control.lrange(run + ':child-completed', 0, -1),
            'results': {task_id: {'state': celery_app.AsyncResult(task_id).state,
                                 'result': celery_app.AsyncResult(task_id).result}
                        for task_id in payload.get('ids', [])},
        }
    print('PROOF_JSON=' + json.dumps(value, sort_keys=True, default=str))


if __name__ == '__main__':
    main()
