"""Run an accepted source proof unchanged, retaining only non-secret inventories.

The audit wrapper counts actual pinned-uv invocations separately. Builder calls
include expected negative cases; a failed call is not an actual resolver count.
"""
import json
from pathlib import Path
import runpy
import sys

from docassemble_executor.registry_builder import RegistryBuilder


def main(name):
    allowed = {'config_publication', 'zip_install', 'playground_install', 'git_install',
               'private_git', 'private_index_credentials', 'direct_url'}
    if name not in allowed:
        raise ValueError('unknown source proof')
    original = RegistryBuilder.build
    results = []

    def observed(self, inputs):
        row = {'completed': False}
        results.append(row)
        try:
            result = original(self, inputs)
            row.update(completed=True, bundles={kind: {key: result[kind][key] for key in ('digest', 'inventory')}
                       for kind in ('bundle', 'core_bundle') if kind in result})
            return result
        finally:
            Path('/tmp/qualification-inventories.json').write_text(json.dumps(results, indent=2, sort_keys=True))

    RegistryBuilder.build = observed
    runpy.run_path(str(Path(__file__).with_name(name + '_proof.py')), run_name='__main__')


if __name__ == '__main__':
    main(sys.argv[-1])
