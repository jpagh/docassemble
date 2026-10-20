"""Managed bootstrap for the actual incoming handler, with its Flask context.

Executed as www-data under the finite helper guardian. Legacy launch remains
unchanged; the real handler and its downstream queueing are not replaced.
"""
import sys

import docassemble.base.config

docassemble.base.config.load(arguments=sys.argv)
from docassemble.webapp.server import app  # noqa: E402
from docassemble.base.thread_context import empty_globals, global_context  # noqa: E402

with app.app_context(), global_context(empty_globals()):
    from docassemble.webapp.process_email import main
    main()
