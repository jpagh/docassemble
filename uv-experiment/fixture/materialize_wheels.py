#!/usr/bin/env python3
"""Retain image-built fixture wheels so ordinary frozen boot never builds.

Local unpublished sources are fixture inputs, not a new production archiving
policy. Source-built runtime-fault remains solely for the interruption proof.
"""

from email.parser import BytesParser
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tomllib
from zipfile import ZipFile


def main():
    bundle = Path(sys.argv[1])
    project = bundle / 'pyproject.toml'
    text = project.read_text()
    sources = tomllib.loads(text)['tool']['uv']['sources']
    wheels = bundle / 'wheels'
    wheels.mkdir(exist_ok=True)
    if len(sys.argv) > 2 and sys.argv[2] == '--retain-built':
        locked = {package['name']: package for package in tomllib.loads((bundle / 'uv.lock').read_text())['package']}
        origins = {}
        # Pinned-uv fixture-only extraction of wheels actually built from
        # registry sdists. Cold boot must not resolve/build them again, and
        # native rebuilds need not be byte-for-byte reproducible.
        retained = set()
        for built in sorted(Path('/var/cache/docassemble/uv/sdists-v9/pypi').rglob('*.whl')):
            with ZipFile(built) as archive:
                metadata = BytesParser().parsebytes(archive.read(next(name for name in archive.namelist() if name.endswith('.dist-info/METADATA'))))
            name = metadata['Name'].lower().replace('_', '-')
            version = metadata['Version']
            if locked.get(name, {}).get('version') != version or name in retained:
                continue
            retained.add(name)
            shutil.copy2(built, wheels / built.name)
            origins[built.name] = locked[name]
            text = text.replace('dependencies = [', 'dependencies = [\n  "' + name + '==' + version + '",', 1)
            text += '\n"' + name + '" = { path = "wheels/' + built.name + '" }\n'
        project.write_text(text)
        (bundle / 'source-artifacts.json').write_text(json.dumps(origins, indent=2, sort_keys=True) + '\n')
        return
    for name, source in sources.items():
        subprocess.run(['uv', 'build', '--wheel', '--out-dir', str(wheels), str(bundle / source['path'])], check=True)
        stem = name.replace('.', '_').replace('-', '_') + '-'
        wheel = next(path for path in wheels.glob('*.whl') if path.name.startswith(stem))
        text = text.replace('path = "' + source['path'] + '"', 'path = "wheels/' + wheel.name + '"')
    project.write_text(text)


if __name__ == '__main__':
    main()
