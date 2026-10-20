"""Existing configured error email trigger: honest guidance, never fictitious success."""
from dataclasses import replace

import pytest

from docassemble_executor.error_notification import configured_recipients, notify
from docassemble_executor.paths import ExecutorPaths


def config_paths(tmp_path, text):
    config_file = tmp_path / 'config.yml'
    config_file.write_text(text, encoding='utf-8')
    return replace(ExecutorPaths.from_env(), config_file=config_file)


class RecordingSMTP:
    def __init__(self, server, port, timeout=None, **kwargs):
        self.server, self.port, self.started, self.login_used = server, port, False, None
        self.options = kwargs
        RecordingSMTP.connection = self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self, context=None):
        self.started = True
        self.tls_context = context

    def login(self, username, password):
        self.login_used = username
        self.password_used = password

    def send_message(self, message):
        RecordingSMTP.last = message


def test_missing_address_returns_honest_guidance(tmp_path):
    paths = config_paths(tmp_path, 'db:\n  name: docassemble\n')
    assert configured_recipients({'error notification email': 'ops@example.test'}) == ['ops@example.test']
    outcome = notify(paths, subject='docassemble', body='body')
    assert outcome['sent'] is False and outcome['reason'] == 'no-error-notification-email'
    assert 'error notification email' in outcome['guidance']
    # Never imports the runtime nor falls back to an unconfigured transport.


def test_configured_address_uses_configured_transport(tmp_path):
    paths = config_paths(tmp_path, '\n'.join([
        'error notification email: ops@example.test',
        'mail:',
        '  - name: default',
        '    server: mail.example.test',
        '    port: 2525',
        '    use tls: false',
        '    default sender: da@example.test',
    ]) + '\n')
    outcome = notify(paths, subject='docassemble managed recovery', body='recovery body',
                     smtp_factory=RecordingSMTP)
    assert outcome == {'sent': True, 'reason': None, 'recipients': ['ops@example.test']}
    assert RecordingSMTP.last['To'] == 'ops@example.test'
    assert RecordingSMTP.last['Subject'] == 'docassemble managed recovery'
    assert RecordingSMTP.connection.server == 'mail.example.test'
    assert RecordingSMTP.connection.port == 2525
    assert RecordingSMTP.connection.started is False


@pytest.mark.parametrize('as_list', [False, True])
@pytest.mark.parametrize('implicit_tls,starttls', [(True, True), (True, False), (False, True), (False, False)])
def test_supported_mail_forms_select_transport_authentication_and_verified_tls(tmp_path, monkeypatch, as_list, implicit_tls, starttls):
    import ssl
    import yaml
    mail = {'server': 'configured.example.test', 'port': 465 if implicit_tls else 587,
            'use ssl': implicit_tls, 'use tls': starttls, 'username': 'configured-user',
            'password': 'configured-password', 'default sender': 'configured@example.test'}
    paths = config_paths(tmp_path, yaml.safe_dump({'error notification email': 'ops@example.test',
                                                 'mail': [dict(mail, name='default')] if as_list else mail}))
    selected = []
    def factory(kind):
        def make(*a, **kw):
            selected.append(kind)
            return RecordingSMTP(*a, **kw)
        return make
    monkeypatch.setattr('docassemble_executor.error_notification.smtplib.SMTP', factory('smtp'))
    monkeypatch.setattr('docassemble_executor.error_notification.smtplib.SMTP_SSL', factory('ssl'))
    assert notify(paths, subject='recovery', body='restricted')['sent'] is True
    assert selected == ['ssl' if implicit_tls else 'smtp']
    connection = RecordingSMTP.connection
    assert (connection.server, connection.port) == (mail['server'], mail['port'])
    assert (connection.login_used, connection.password_used) == (mail['username'], mail['password'])
    assert connection.started is (starttls and not implicit_tls)
    if implicit_tls or starttls:
        context = connection.options['context'] if implicit_tls else connection.tls_context
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert RecordingSMTP.last['From'] == mail['default sender']


def test_unavailable_transport_returns_guidance(tmp_path):
    class Unreachable:
        def __init__(self, *args, **kwargs):
            raise OSError('connection refused')

    paths = config_paths(tmp_path, 'error notification email: ops@example.test\n')
    outcome = notify(paths, subject='docassemble', body='body', smtp_factory=Unreachable)
    assert outcome['sent'] is False and outcome['reason'] == 'email-unavailable'
    assert outcome['guidance']


def test_unreadable_configuration_returns_guidance(tmp_path):
    paths = replace(ExecutorPaths.from_env(), config_file=tmp_path / 'absent.yml')
    outcome = notify(paths, subject='docassemble', body='body')
    assert outcome['sent'] is False and outcome['reason'] == 'configuration-unreadable'
