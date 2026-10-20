"""Legacy setup hook that can slow down wheel builds for fault injection.

The image build and uv lock leave ``SLOWFIXTURE_BUILD_DELAY`` unset so metadata
preparation is fast.  The interrupted-installation scenario sets it, which makes
the frozen sync spend enough time in this build to be killed deliberately.
"""

import os
import time

delay = float(os.environ.get("SLOWFIXTURE_BUILD_DELAY", "0") or "0")
if delay:
    time.sleep(delay)

from setuptools import setup  # noqa: E402

setup()
