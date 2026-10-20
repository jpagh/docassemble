# pre-load
"""Real user hook, registered during normal configured module preload."""

import os
import sys
from docassemble.base.plugin_manager import pm
from docassemble.webapp.hooks.impl import hookimpl

SENTINEL_DIR = os.environ.get("DA_SENTINEL_DIR", "/tmp/da-sentinels")

try:
    os.makedirs(SENTINEL_DIR, exist_ok=True)
    with open(os.path.join(SENTINEL_DIR, "sentinel-hook-module"), "w", encoding="utf-8") as handle:
        handle.write("docassemble.sentinel_fixture.hook_sentinel imported\n")
except OSError:
    pass


@hookimpl(tryfirst=True)
def get_default_timezone():
    with open(os.path.join(SENTINEL_DIR, "sentinel-hook-executed"), "w", encoding="utf-8") as handle:
        handle.write("user get_default_timezone hook executed\n")
    return "America/New_York"


pm.register(sys.modules[__name__])

# Disposable HTTP caller proof; never part of the production application.
from docassemble.webapp.flask_app import flaskapp  # noqa: E402


@flaskapp.route('/proof/service-nested')
def service_nested():
    from flask import request
    sys.path.insert(0, '/opt/docassemble-fixture')
    from service_probe_hooks import barrier, nested
    label = request.args['label']
    barrier(label)
    return {'result': nested(label)}
