"""Disposable real-worker workloads; never a production dependency."""
import os
import time

from redis import Redis
from docassemble.webapp.tasks.app import celery_app


def _complete(task, run, label, detached=False):
    control = Redis(db=2, decode_responses=True)
    control.rpush(run + ':started', label)
    while not control.exists(run + ':release:' + label):
        time.sleep(0.05)
    if detached and os.fork() == 0:
        os.setsid()
        os.closerange(3, 1024)
        child_control = Redis(db=2, decode_responses=True)
        child_control.rpush(run + ':child-started', label)
        while not child_control.exists(run + ':child-release'):
            time.sleep(0.05)
        child_control.rpush(run + ':child-completed', label)
        os._exit(0)
    control.rpush(run + ':completed', label)
    return {'label': label, 'uid': os.geteuid(), 'result': 'accepted work finished'}


@celery_app.task(bind=True, name='proof.late', acks_late=True)
def late(task, run, label, detached=False):
    return _complete(task, run, label, detached)


@celery_app.task(bind=True, name='proof.early', acks_late=False)
def early(task, run, label, detached=False):
    return _complete(task, run, label, detached)


@celery_app.task(bind=True, name='proof.service', acks_late=True)
def service(task, run, label, detached=False):
    import sys
    sys.path.insert(0, '/opt/docassemble-fixture')
    from service_probe_hooks import nested
    control = Redis(db=2, decode_responses=True)
    control.rpush(run + ':started', label)
    while not control.exists(run + ':release:' + label):
        time.sleep(0.05)
    result = nested(label)
    control.rpush(run + ':completed', label)
    return {'label': label, 'uid': os.getuid(), 'result': result}
