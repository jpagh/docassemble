"""Serialize Redis API authority mutations with internal managed publication."""
from contextlib import contextmanager
from functools import wraps

from docassemble.base.managed import managed_installation


@contextmanager
def api_key_authority():
    """Hold environment-first authority across Redis reads and writes.

    Use an independent SQL transaction: callers may commit their ORM session.
    Redis is not SQL-atomic; an interrupted write may have taken effect. Fresh
    publication authorization rereads Redis under this same environment lock.
    """
    if not managed_installation():
        yield
        return
    from docassemble.webapp.extensions import db
    from docassemble.webapp.database import dbtableprefix
    from docassemble.webapp.users.managed import lock_authority

    with db.engine.begin() as connection:
        lock_authority(connection, dbtableprefix)
        yield


def serialized_api_key_writer(function):
    @wraps(function)
    def writer(*args, **kwargs):
        with api_key_authority():
            return function(*args, **kwargs)
    return writer
