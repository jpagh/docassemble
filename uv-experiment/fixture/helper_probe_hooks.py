"""Disposable barriers around actual incoming process_email.main dispatch.

Installed only by the proof as sitecustomize. No fake handler or fake mail queue.
Service hooks keep the actual Pattern/UNO receiver receipts for nested work.
"""
from datetime import datetime, timezone
import email
import importlib.abc
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, '/opt/docassemble-fixture')
import service_probe_hooks  # noqa: E402

ROOT = Path('/tmp/da-helper-proof')


def profile(frame, event, arg):
    if (event == 'return' and frame.f_code.co_name == 'delay' and
            frame.f_code.co_filename.endswith('/celery/canvas.py') and
            sys.argv[0].endswith('/mail_runtime.py')):
        subject = email.message_from_bytes(Path(sys.argv[1]).read_bytes()).get('Subject', '')
        if subject == 'hold:mail-commit':
            (ROOT / 'mail-commit.published').touch()
            while not (ROOT / 'mail-commit.commit-release').exists():
                time.sleep(0.05)
    if frame.f_code.co_name != 'main' or not frame.f_code.co_filename.endswith('/process_email.py'):
        return
    if event == 'call':
        subject = email.message_from_bytes(Path(sys.argv[1]).read_bytes()).get('Subject', '')
        if not subject.startswith('hold:'):
            return
        label = subject.split(':', 1)[1]
        (ROOT / (label + '.started')).write_text(str(os.getpid()))
        while not (ROOT / (label + '.release')).exists():
            time.sleep(0.05)
        if label.endswith('-nested'):
            service_probe_hooks.nested(label)
        if label.endswith('-output'):
            print('managed-mail-output:' + 'x' * 40000, flush=True)
    elif event == 'return':
        subject = email.message_from_bytes(Path(sys.argv[1]).read_bytes()).get('Subject', '')
        if subject.startswith('hold:'):
            from docassemble_executor.admission import Admission
            from docassemble.webapp.config import in_cron
            state = Admission(Path('/var/lib/docassemble/managed')).state()
            (ROOT / (subject.split(':', 1)[1] + '.completed')).write_text(
                json.dumps({'uid': os.getuid(), 'mode': state['mode'], 'in_cron': in_cron}))


if (os.environ.get('DA_HELPER_PROOF_DETACHED') == 'true' and
        os.environ.get('DA_HELPER_PROOF_SOURCE_OVERLAY') == '1'):
    class HelperSourceOverlay(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path, target=None):
            if fullname != 'docassemble.sentinel_fixture.helper_work':
                return None
            installed = importlib.machinery.PathFinder.find_spec(fullname, path)
            if installed is None or installed.origin is None:
                raise ImportError('detached helper installed origin is unavailable')
            spec = importlib.util.spec_from_file_location(
                fullname, '/opt/docassemble-fixture/sentinel-user/docassemble/sentinel_fixture/helper_work.py')
            spec.loader_state = {'installed_origin': installed.origin}
            return spec

    sys.meta_path.insert(0, HelperSourceOverlay())


if (os.environ.get('DA_HELPER_PROOF_DETACHED') == 'true' and
        os.environ.get('DA_HELPER_PROOF_LABEL') == 'detached-helper' and
        sys.argv[0].endswith('/authority.py')):
    original_waitpid = os.waitpid

    def guardian_record(**details):
        try:
            record = {'v': 1, 'utc': datetime.now(timezone.utc).isoformat(),
                      'monotonic_ns': time.monotonic_ns(), 'guardian_pid': os.getpid(), **details}
            with (ROOT / 'detached-helper.guardian-reaps.jsonl').open('a') as output:
                output.write(json.dumps(record) + '\n')
        except BaseException:
            pass

    try:
        stat = Path('/proc/self/stat').read_text()
        guardian_record(event='installed', guardian_starttime=int(stat[stat.rfind(')') + 2:].split()[19]))
    except BaseException:
        pass

    def guardian_waitpid(pid, options):
        result = original_waitpid(pid, options)
        try:
            reaped_pid, status = result
            if reaped_pid > 0:
                exited, signalled = os.WIFEXITED(status), os.WIFSIGNALED(status)
                guardian_record(requested_pid=pid, options=options, reaped_pid=reaped_pid,
                                raw_status=status, exited=exited,
                                exit_code=os.WEXITSTATUS(status) if exited else None,
                                signalled=signalled, signal=os.WTERMSIG(status) if signalled else None,
                                core_dumped=os.WCOREDUMP(status) if hasattr(os, 'WCOREDUMP') else None)
        except BaseException:
            pass
        return result

    os.waitpid = guardian_waitpid


sys.setprofile(profile)
