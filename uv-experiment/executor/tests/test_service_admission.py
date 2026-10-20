"""Accepted caller capabilities are a public local service-admission boundary."""
from docassemble_executor.admission import Admission
from docassemble_executor.capabilities import Capabilities


def test_accepted_caller_can_make_nested_requests_after_closure(tmp_path):
    admission = Admission(tmp_path)
    admission.open(consumers=True)
    capabilities = Capabilities(tmp_path)
    capabilities.prepare()
    with capabilities.accept() as caller:
        assert caller is not None
        admission.close('maintenance', mode='draining')
        with capabilities.accept(caller.name) as nested:
            assert nested is not None
            assert not capabilities.idle()
        with capabilities.accept() as fresh:
            assert fresh is None
    assert capabilities.idle()
    with capabilities.accept(caller.name) as expired:
        assert expired is None


def test_recovery_and_forged_capabilities_never_authorize_service_requests(tmp_path):
    Admission(tmp_path).open()  # restricted web only
    capabilities = Capabilities(tmp_path)
    capabilities.prepare()
    for token in ('', 'not-a-capability', '../traffic.lock', 'a' * 64):
        with capabilities.accept(token) as caller:
            assert caller is None


def test_nested_request_keeps_capability_live_after_parent_finishes(tmp_path):
    Admission(tmp_path).open(consumers=True)
    capabilities = Capabilities(tmp_path)
    capabilities.prepare()
    with capabilities.accept() as caller:
        context = capabilities.accept(caller.name)
        nested = context.__enter__()
    Admission(tmp_path).close('maintenance')
    try:
        assert nested is not None
        assert not capabilities.idle()
        assert not Admission(tmp_path).drain(0.02)
    finally:
        context.__exit__(None, None, None)
    assert capabilities.idle()
    assert Admission(tmp_path).drain(0.02)


def test_idle_service_retirement_refuses_new_work_even_if_controller_reopens(tmp_path):
    admission = Admission(tmp_path)
    admission.open(consumers=True)
    service = Capabilities(tmp_path)
    with service.accept() as caller:
        admission.close('maintenance', mode='draining')
        assert not service.begin_idle_stop()  # accepted callers must remain finishable
        with service.accept(caller.name) as nested:
            assert nested is not None
    assert service.begin_idle_stop()
    admission.open(consumers=True)  # timeout/reopen races the old service's warm exit
    with Capabilities(tmp_path).accept() as new_caller:
        assert new_caller is not None  # unchanged runtime admits new parent work
        with service.accept(new_caller.name) as refused:
            assert refused is None  # retiring listener cannot dispatch then kill it
