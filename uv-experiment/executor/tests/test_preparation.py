"""Structural registry contract; these tests never import the application."""
import pytest
from docassemble_executor.preparation import parse_registry_requirement, PreparationError, Preparation


@pytest.mark.parametrize('declaration,name,extras', [
    ('Requests[security,socks]>=2.28,<3; python_version >= "3.10"', 'requests', ('security', 'socks')),
    ('My_Package.Name != 1.0, ~=2.3', 'my-package-name', ()),
    ('  Django==5.1.*  ', 'django', ()),
    ('sample; os_name == "posix" and extra != "dev"', 'sample', ()),
    ('sample===arbitrary', 'sample', ()),
])
def test_exact_registry_declaration(declaration, name, extras):
    result = parse_registry_requirement(declaration)
    assert result.declaration == declaration
    assert result.normalized_name == name
    assert result.extras == extras


@pytest.mark.parametrize('declaration', [
    '', '--index-url https://example.test simple', '-e simple', '-r requirements.txt',
    './local', '/tmp/local', 'git+ssh://example.test/repo',
    'simple @ https://user:secret@example.test/pkg.whl?token=secret',
    'simple @ https://secret@example.test/pkg.whl', 'simple @ file:///tmp/local',
    'simple; python_version > "3"; echo secret', 'simple\nother', 'simple\x00',
    'simple && echo secret', 'simple==1; os_name ==', 'simple==1 # comment',
])
def test_unsupported_not_sanitized_or_leaked(declaration):
    with pytest.raises(PreparationError) as error:
        parse_registry_requirement(declaration)
    assert declaration not in str(error.value) if declaration else True
    assert 'secret' not in str(error.value)


@pytest.mark.parametrize('source', [
    'https://user:DISPOSABLE_SECRET_TOKEN@example.invalid/repo',
    'https://DISPOSABLE_SECRET_TOKEN@example.invalid/repo',
    'https://example.invalid/repo?token=DISPOSABLE_SECRET_TOKEN',
])
def test_production_credential_injection_rejected_without_db_or_staging(source):
    module = object.__new__(Preparation)
    with pytest.raises(PreparationError) as error:
        module.inject_source_credentials(source, b'DISPOSABLE_SECRET_TOKEN')
    assert str(error.value) == 'raw source credential injection is unsupported; submit a scoped HTTPS Git declaration'


def test_approval_and_retry_are_explicitly_rejected():
    # No DB or installation activity can occur even on a partially constructed object.
    module = object.__new__(Preparation)
    with pytest.raises(PreparationError, match='approval unsupported'):
        module.approve('actor', 'candidate', 'digest', 'key')
    with pytest.raises(PreparationError, match='retry unsupported'):
        module.retry('actor', 'revision', 'key')
