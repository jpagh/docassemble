"""Linux direct-preparation exit authority, independent of dispatcher lifetime.

Only a subreaper's durable ECHILD receipt proves exit of the build AND all its
(descendant, including double-forked/setsid) processes. A missing receipt or dead
guardian never grants exit. Restricted receipts are trusted operator state.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import fcntl
import json
import os
from pathlib import Path
import sys
from typing import Iterator

from .preparation import Preparation, PreparationError
from .preparation_containers import ContainerWorkers


def _subreaper() -> None:
    if sys.platform != 'linux' or ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise PreparationError('direct preparation requires Linux descendant exit authority')


class ProcessBuild:
    def __init__(self, pid: int, directory: Path):
        self.pid, self.directory = pid, directory

    def result(self) -> dict:
        _, status = os.waitpid(self.pid, 0)
        if status != 0 or not (self.directory / 'exit.json').is_file():
            raise PreparationError('direct preparation exit authority unavailable')
        try:
            return json.loads((self.directory / 'result.json').read_text())
        except (OSError, ValueError):
            raise PreparationError('direct preparation worker failed') from None


class ProcessWorkers:
    def __init__(self, preparation: Preparation, receipts: Path):
        self.preparation = preparation
        receipts.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.receipts = preparation._secret_root(receipts)

    @contextmanager
    def build_claim(self, claim, build) -> Iterator[ProcessBuild]:
        """Start before renewal threads; retain flock through terminal DB writes.

        The guardian inherits the flock, keeping orphan reconciliation excluded
        until descendant exit is proven even if the dispatcher itself crashes.
        """
        if sys.platform != 'linux':
            raise PreparationError('direct preparation requires Linux descendant exit authority')
        directory = self.receipts / claim.worker_id
        directory.mkdir(mode=0o700)
        with (directory / 'lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            ContainerWorkers._write(directory / 'claim.json', {'worker': claim.worker_id})
            pid = os.fork()
            if pid == 0:
                try:
                    _subreaper()
                    child = os.fork()
                    if child == 0:
                        lock.close()  # Descendants must not retain the authority's flock.
                        try:
                            self.preparation.engine.dispose(close=False)
                            ContainerWorkers._write(directory / 'result.json', build())
                            os._exit(0)
                        except BaseException:
                            os._exit(1)
                    # Builder return/exit is insufficient: wait for every adopted
                    # descendant, including daemonized grandchildren, to exit.
                    while True:
                        try:
                            os.waitpid(-1, 0)
                        except InterruptedError:
                            continue
                        except ChildProcessError:
                            break
                    ContainerWorkers._write(directory / 'exit.json', {'worker': claim.worker_id})
                    os._exit(0)
                except BaseException:
                    os._exit(1)  # No proof, even if the guardian itself has exited.
            yield ProcessBuild(pid, directory)

    def reconcile(self) -> int:
        exited = 0
        for directory in sorted(self.receipts.iterdir()):
            if not directory.is_dir() or not (directory / 'claim.json').is_file():
                continue
            directory = self.preparation._secret_root(directory)
            with (directory / 'lock').open('a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                proof = directory / 'exit.json'
                if not proof.is_file():
                    continue
                claim = json.loads((directory / 'claim.json').read_text())
                if json.loads(proof.read_text()) != claim or claim != {'worker': directory.name}:
                    raise PreparationError('untrusted direct preparation exit receipt')
                self.preparation.worker_exited(claim['worker'])
                # The flock excludes terminal writes and ECHILD excludes every
                # descendant. Disposable results can now be removed on replay.
                result = directory / 'result.json'
                for staging in (result, result.with_suffix('.pending')):
                    staging.unlink(missing_ok=True)
                fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
                exited += 1
        return exited
