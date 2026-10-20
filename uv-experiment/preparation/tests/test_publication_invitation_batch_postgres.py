"""Multi-recipient invitation publication and per-delivery compensation."""
import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from test_preparation_postgres import protocol  # noqa: F401
from test_publication_writers_postgres import publisher  # noqa: F401
from test_publication_account_writers_postgres import account_models  # noqa: F401
from test_publication_profile_views_postgres import profile_models  # noqa: F401
from test_publication_registration_postgres import registration_models, invoke, snapshot  # noqa: F401


def test_invite_batch_tokens_visible_before_first_send_and_only_failed_delivery_revoked(publisher, registration_models):  # noqa: F811
    calls = []
    def deliver(invitation, link):
        with publisher.engine.connect() as connection:
            rows = list(connection.execute(text('SELECT id,email,token FROM test_user_invite ORDER BY id')))
        if not calls:
            assert [row.email for row in rows] == ['first@example.invalid', 'second@example.invalid']
            assert len({row.token for row in rows}) == 2
            assert all(row.token == 'signed-' + str(row.id) for row in rows)
        calls.append(invitation.email)
        if invitation.email == 'second@example.invalid':
            raise RuntimeError('uncertain second delivery')
    with Session(publisher.engine) as session:
        invoke('invite_view', session, registration_models, recipients='first@example.invalid\nsecond@example.invalid', effect=deliver)
    assert calls == ['first@example.invalid', 'second@example.invalid']
    with publisher.engine.connect() as connection:
        rows = list(connection.execute(text('SELECT id,email,token FROM test_user_invite')))
    assert len(rows) == 1
    assert rows[0].email == 'first@example.invalid'
    assert rows[0].token == 'signed-' + str(rows[0].id)


def test_invite_batch_precommit_failure_persists_neither_invitation(publisher, registration_models):  # noqa: F811
    before = snapshot(publisher)
    calls = []
    def fail(session):
        session.flush()
        assert session.scalar(text('SELECT count(*) FROM test_user_invite WHERE token IS NOT NULL')) == 2
        with publisher.engine.connect() as connection:
            assert connection.scalar(text('SELECT count(*) FROM test_user_invite')) == 0
        raise RuntimeError('precommit failure')
    with Session(publisher.engine) as session:
        event.listen(session, 'before_commit', fail)
        with pytest.raises(RuntimeError, match='precommit failure'):
            invoke('invite_view', session, registration_models, recipients='first@example.invalid\nsecond@example.invalid', effect=lambda *args: calls.append(1))
    assert snapshot(publisher) == before
    assert not calls
