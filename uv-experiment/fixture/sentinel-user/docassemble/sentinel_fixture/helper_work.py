"""Disposable interview code run by the actual cron/background-action handlers."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
import traceback

ROOT = Path('/tmp/da-helper-proof')


def _redact(text):
    capability = os.environ.get('DA_ACCEPTED_CALLER')
    if capability:
        text = text.replace(capability, '[redacted-capability]')
    return re.sub(r'\b[0-9a-f]{64}\b', '[redacted-capability]', text)


def _record_work_body(kind, label, trace=None):
    (ROOT / (label + '.started')).write_text(str(os.getpid()))
    if trace is not None:
        trace('started')
    if os.environ.get('DA_HELPER_PROOF_HOLD') == 'true':
        while not (ROOT / (label + '.release')).exists():
            time.sleep(0.05)
        if trace is not None:
            trace('released')
        if os.environ.get('DA_HELPER_PROOF_NESTED') == 'true':
            if trace is not None:
                trace('nested-import-enter')
            import sys
            sys.path.insert(0, '/opt/docassemble-fixture')
            from service_probe_hooks import nested
            if trace is not None:
                trace('nested-import-return', module_file=sys.modules['service_probe_hooks'].__file__)
                trace('nested-enter')
                result = nested(label, trace=trace)
                trace('nested-return', result=result)
            else:
                nested(label)
    if trace is not None:
        trace('completion-write-enter')
    (ROOT / (label + '.completed')).write_text(json.dumps({'kind': kind, 'uid': os.getuid()}))
    if trace is not None:
        trace('completion-write-return')
    if kind == 'incoming-email':
        from docassemble.base.functions import action_argument
        message = action_argument('email')
        (ROOT / ('email-event-' + message.subject + '.completed')).write_text(json.dumps(
            {'uid': os.getuid(), 'subject': message.subject, 'body': message.body_text.slurp()}))


def record_work(kind):
    label = os.environ.get('DA_HELPER_PROOF_LABEL', kind)
    detached = os.environ.get('DA_HELPER_PROOF_DETACHED') == 'true'

    def trace(phase, **details):
        try:
            def redact(value):
                if isinstance(value, str):
                    return _redact(value)
                if isinstance(value, dict):
                    return {_redact(key): redact(item) for key, item in value.items()}
                if isinstance(value, (list, tuple)):
                    return [redact(item) for item in value]
                return value

            capabilities = sys.modules.get('docassemble_executor.capabilities')
            record = {
                'v': 1, 'utc': datetime.now(timezone.utc).isoformat(),
                'monotonic_ns': time.monotonic_ns(), 'pid': os.getpid(), 'ppid': os.getppid(),
                'pgid': os.getpgrp(), 'sid': os.getsid(0), 'uid': os.getuid(),
                'label': label, 'kind': kind, 'phase': phase,
                'capability_present': bool(os.environ.get('DA_ACCEPTED_CALLER')),
                'current_capability_present': bool(capabilities.current()) if capabilities else None,
                'details': redact(details),
            }
            with (ROOT / (label + '.trace.jsonl')).open('a') as output:
                output.write(json.dumps(record) + '\n')
        except BaseException:
            pass

    if detached:
        if os.fork() != 0:
            (ROOT / (label + '.parent-exited')).touch()
            return
        try:
            os.setsid()
            os.closerange(3, 1024)
            trace('fd-closed', helper_module_file=__file__,
                  installed_origin=(getattr(__spec__, 'loader_state', None) or {}).get('installed_origin', __file__))
            # Fixture intervention, not passive reproduction: a detached caller outlives
            # the disposable wrapper whose docker-exec pipe it inherited, and stock
            # application logging raises BrokenPipeError on that dead pipe during
            # document conversion, which would kill the caller for an unrelated reason.
            # Production schedules redirect stdio to managed-schedules.log, so give the
            # child the same kind of stable sink before it does any work.
            for descriptor, stream in ((1, 'stdout'), (2, 'stderr')):
                sink = os.open(str(ROOT / (label + '.' + stream + '.txt')),
                               os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                os.dup2(sink, descriptor)
                os.close(sink)
            original = sys.stderr

            class StderrTrace:
                def __getattr__(self, name):
                    return getattr(original, name)

                def write(self, text):
                    try:
                        with (ROOT / (label + '.stderr.txt')).open('a') as output:
                            output.write(_redact(text))
                    except BaseException:
                        pass
                    try:
                        return original.write(text)
                    except BaseException as error:
                        trace('stderr-write-error', type=type(error).__name__, message=str(error))
                        raise

                def flush(self):
                    try:
                        return original.flush()
                    except BaseException as error:
                        trace('stderr-flush-error', type=type(error).__name__, message=str(error))
                        raise

            sys.stderr = StderrTrace()
            _record_work_body(kind, label, trace)
            trace('exit-enter', code=0)
            os._exit(0)
        except BaseException as error:
            try:
                text = ''.join(traceback.TracebackException.from_exception(error, capture_locals=False).format())
                with (ROOT / (label + '.traceback.txt')).open('w') as output:
                    output.write(_redact(text))
            except BaseException:
                pass
            trace('exception', type=type(error).__name__, message=str(error))
            raise
    else:
        _record_work_body(kind, label)
