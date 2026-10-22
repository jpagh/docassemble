"""Small harness for the disposable-container recovery proof.

Only stdlib is used so the scenario driver can run from any Python 3.10+
interpreter on the host.
"""

from __future__ import annotations

import http.cookiejar
import json
import re
import shlex
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeLayout:
    venv: str
    python: str
    site_packages: str


class Failure(Exception):
    """A scenario assertion failed."""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise Failure(message)


class Evidence:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.log_lines: list[str] = []

    def note(self, message: str) -> None:
        line = time.strftime("%H:%M:%S") + " " + message
        print(line, flush=True)
        self.log_lines.append(line)

    def write(self, name: str, content: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write_json(self, name: str, payload: object) -> None:
        self.write(name, json.dumps(payload, indent=2, sort_keys=True) + "\n")


class Docker:
    def __init__(self, evidence: Evidence):
        self.evidence = evidence
        self.owned_containers: set[str] = set()

    def run(self, args: list[str], timeout: int = 3600, check_result: bool = True, input_text: str | None = None):
        completed = subprocess.run(
            ["docker"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_text,
        )
        if check_result and completed.returncode != 0:
            raise Failure(
                "docker " + " ".join(args) + " failed (" + str(completed.returncode) + "): " + completed.stderr.strip()
            )
        return completed

    def start(self, name: str, image: str, port: int | None = None, network: str | None = None, env: dict[str, str] | None = None, cold_setup: str | None = None):
        present = self.run(['ps', '-a', '--format', '{{.Names}}']).stdout.splitlines()
        if name in present and name not in self.owned_containers:
            raise Failure('pre-existing proof container must be excluded first: ' + name)
        self.rm(name)
        args = ["run", "-d", "--name", name]
        if network:
            args += ["--network", network]
        if port:
            args += ["-p", "127.0.0.1:" + str(port) + ":80"]
        args += ["-e", "DA_ADMIN_EMAIL=admin@example.com", "-e", "DA_ADMIN_PASSWORD=FixtureAdmin101!"]
        args += ["-e", "UV_HTTP_TIMEOUT=10", "-e", "UV_CONCURRENT_DOWNLOADS=4"]
        for key, value in (env or {}).items():
            args += ["-e", key + "=" + value]
        if cold_setup:
            args += ['--entrypoint', '/bin/bash']
        args.append(image)
        if cold_setup:
            args += ['-c', 'set -euo pipefail; ' + cold_setup + '; exec /opt/docassemble-fixture/entrypoint.sh']
        self.run(args)
        self.owned_containers.add(name)

    def exec(self, name: str, command: list[str], timeout: int = 900, check_result: bool = True, env: dict[str, str] | None = None, user: str | None = None):
        args = ["exec"]
        if user:
            args += ["--user", user]
        for key, value in (env or {}).items():
            args += ["-e", key + "=" + value]
        args += [name] + command
        return self.run(args, timeout=timeout, check_result=check_result)

    def exec_shell(self, name: str, script: str, timeout: int = 900, check_result: bool = True):
        return self.exec(name, ["bash", "-lc", script], timeout=timeout, check_result=check_result)

    def runtime_layout(self, container: str) -> RuntimeLayout:
        """Observe protected tooling before damage, never run the runtime Python."""
        script = ('import json; from docassemble_executor.paths import ExecutorPaths; '
                  'from docassemble.base.managed_python import python_site_packages; '
                  'p=ExecutorPaths.from_env().runtime_venv; '
                  'print(json.dumps({"venv": str(p), "python": str(p / "bin/python"), '
                  '"site_packages": str(p / python_site_packages())}))')
        payload = json.loads(self.exec(container, [
            '/usr/share/docassemble/executor-venv/bin/python', '-c', script]).stdout)
        layout = RuntimeLayout(**payload)
        check(all(isinstance(value, str) and Path(value).is_absolute()
                  for value in (layout.venv, layout.python, layout.site_packages)),
              'container runtime layout is invalid')
        self.evidence.write_json('runtime-layout.json', payload)
        return layout

    def restart(self, name: str, timeout: int = 120):
        self.run(["restart", name], timeout=timeout)

    def rm(self, name: str):
        if name not in self.owned_containers:
            return
        present = self.run(['ps', '-a', '--format', '{{.Names}}']).stdout.splitlines()
        if name in present:
            self.run(["rm", "-f", name])
        if name in self.run(['ps', '-a', '--format', '{{.Names}}']).stdout.splitlines():
            raise Failure('proof container cleanup failed: ' + name)
        self.owned_containers.remove(name)

    def commit(self, name: str, image: str):
        self.run(["commit", name, image])

    def logs(self, name: str, tail: int = 400) -> str:
        completed = self.run(["logs", "--tail", str(tail), name], check_result=False)
        return completed.stdout + completed.stderr

    def read_file(self, name: str, path: str) -> str:
        completed = self.exec(name, ["cat", path], check_result=False)
        return completed.stdout if completed.returncode == 0 else ""

    def wait_for_exec(self, name: str, script: str, timeout: int = 900, interval: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.exec_shell(name, script, check_result=False).returncode == 0:
                return True
            time.sleep(interval)
        return False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Http:
    def __init__(self, base: str, timeout: float = 60.0):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), NoRedirect())
        self.following_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, method: str, path: str, data: dict[str, str] | None = None, follow: bool = False, timeout: float | None = None):
        url = self.base + path
        body = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
        request = urllib.request.Request(url, data=body, method=method)
        if body is not None:
            request.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            opener = self.following_opener if follow else self.opener
            with opener.open(request, timeout=timeout or self.timeout) as response:
                return response.status, dict(response.headers), response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read().decode("utf-8", "replace")
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as error:
            raise Failure("HTTP " + method + " " + path + " failed: " + str(error)) from error

    def get(self, path: str, timeout: float | None = None):
        return self.request("GET", path, timeout=timeout)

    def post(self, path: str, data: dict[str, str]):
        return self.request("POST", path, data=data)

    @staticmethod
    def csrf(html: str) -> str:
        match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
        if not match:
            match = re.search(r'value="([^"]+)"[^>]*name="csrf_token"', html)
        if not match:
            raise Failure("csrf token not found in page")
        return match.group(1)

    def login(self, email: str, password: str, timeout: float | None = None) -> tuple[int, str]:
        status, _, html = self.get("/user/sign-in", timeout=timeout)
        if status in (302, 303):
            self.get("/user/sign-out")
            status, _, html = self.get("/user/sign-in", timeout=timeout)
        check(status == 200, "login page returned " + str(status))
        token = self.csrf(html)
        self.csrf_token = token
        status, headers, body = self.post(
            "/user/sign-in",
            {"csrf_token": token, "email": email, "password": password, "submit": "Sign in"},
        )
        return status, headers.get("Location", "")

    def register(self, email: str, password: str) -> tuple[int, str]:
        status, _, html = self.get("/user/register")
        check(status == 200, "register page returned " + str(status))
        token = self.csrf(html)
        status, headers, body = self.post(
            "/user/register",
            {
                "csrf_token": token,
                "email": email,
                "password": password,
                "retype_password": password,
                "first_name": "Fixture",
                "last_name": "Nonadmin",
                "timezone": "America/New_York",
                "submit": "Register",
            },
        )
        return status, headers.get("Location", "")

    def wait_for(self, path: str, expected=(200,), timeout: int = 900, interval: float = 3.0) -> tuple[int, str]:
        deadline = time.monotonic() + timeout
        last_status = 0
        while time.monotonic() < deadline:
            try:
                status, _, body = self.get(path, timeout=10)
                last_status = status
                if status in expected:
                    return status, body
            except Failure:
                pass
            time.sleep(interval)
        raise Failure("timed out waiting for " + path + " to return " + str(expected) + "; last status " + str(last_status))


class ContainerHttp:
    """HTTP checks against a container with no published ports (--network none)."""

    def __init__(self, docker: Docker, name: str):
        self.docker = docker
        self.name = name

    def status(self, path: str) -> int:
        completed = self.docker.exec(
            self.name,
            ["bash", "-lc", "curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://localhost" + shlex.quote(path)],
            check_result=False,
            timeout=60,
        )
        output = completed.stdout.strip()
        try:
            return int(output)
        except ValueError:
            return 0

    def body(self, path: str) -> str:
        completed = self.docker.exec(
            self.name,
            ["bash", "-lc", "curl -s --max-time 10 http://localhost" + shlex.quote(path)],
            check_result=False,
            timeout=60,
        )
        return completed.stdout
