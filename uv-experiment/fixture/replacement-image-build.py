"""Build two explicit coherent shipped-core fixtures, not published releases."""
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from packaging.utils import canonicalize_name, parse_wheel_filename
from docassemble_executor.bundles import hash_project
from docassemble_executor.inventory import inspect_environment, inspect_material


def main():
    version = sys.argv[1]
    root = Path('/usr/share/docassemble/managed/bundles')
    runtime = Path('/usr/share/docassemble/local3.12')
    for bundle_name in ('core-r1', 'runtime-r1'):
        directory = root / bundle_name
        project = (directory / 'pyproject.toml').read_text()
        for wheel in list((directory / 'wheels').glob('*.whl')):
            name, old, *_ = parse_wheel_filename(wheel.name)
            name = canonicalize_name(name)
            if name not in ('docassemble-base', 'docassemble-demo', 'docassemble-webapp', 'docassemble-sentinel-fixture'):
                continue
            output_version = str(old) if name == 'docassemble-sentinel-fixture' else version
            files = {}
            with zipfile.ZipFile(wheel) as archive:
                for member in archive.namelist():
                    if member.endswith('/') or member.endswith('.dist-info/RECORD'):
                        continue
                    target = member.replace('-' + str(old) + '.dist-info/', '-' + output_version + '.dist-info/')
                    data = archive.read(member)
                    if member.endswith('.dist-info/METADATA'):
                        data = data.replace(('Version: ' + str(old) + '\n').encode(), ('Version: ' + output_version + '\n').encode())
                        for dependency in ('base', 'demo'):
                            data = data.replace(('docassemble.' + dependency + '==1.10.13').encode(),
                                                ('docassemble.' + dependency + '==' + version).encode())
                    files[target] = data
            if name == 'docassemble-base':
                for path in Path('/ticket/base').glob('managed*.py'):
                    files['docassemble/base/' + path.name] = path.read_bytes()
            if name == 'docassemble-webapp':
                for path in Path('/ticket/webapp').rglob('*.py'):
                    files['docassemble/webapp/' + path.relative_to('/ticket/webapp').as_posix()] = path.read_bytes()
            record_name = next(path.rsplit('/', 1)[0] + '/RECORD' for path in files if path.endswith('.dist-info/METADATA'))
            record = io.StringIO()
            writer = csv.writer(record)
            for path, data in files.items():
                writer.writerow([path, 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('='), len(data)])
            writer.writerow([record_name, '', ''])
            files[record_name] = record.getvalue().encode()
            output = wheel.with_name(wheel.name.replace(str(old), output_version))
            wheel.unlink()
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
                for path, data in files.items():
                    archive.writestr(path, data)
            project = project.replace(wheel.name, output.name)
        (directory / 'pyproject.toml').write_text(project)
        (directory / 'uv.lock').unlink()
        subprocess.run(['uv', 'lock', '--offline', '--no-config'], cwd=directory, check=True)
        subprocess.run(['uv', 'sync', '--frozen', '--offline', '--no-build', '--no-config'], cwd=directory,
            env=dict(os.environ, UV_PROJECT_ENVIRONMENT=str(runtime)), check=True)
        (directory / 'inventory.json').write_text(json.dumps({'distributions': inspect_environment(runtime),
            'files': inspect_material(runtime)}, sort_keys=True))
    manifest = json.loads((root / 'manifest.json').read_text())
    for name in ('core-r1', 'runtime-r1'):
        manifest['bundles'][name] = hash_project(root / name)
        manifest['inventories'][name] = json.loads((root / name / 'inventory.json').read_text())
    manifest['identity']['core_versions'] = {'docassemble.' + name: version for name in ('base', 'demo', 'webapp')}
    manifest['identity']['repo_commit'] = 'ticket-19-coherent-shipped-core-fixture-' + version
    manifest['executor_inventory'] = {'distributions': inspect_environment(Path('/usr/share/docassemble/executor-venv')),
        'files': inspect_material(Path('/usr/share/docassemble/executor-venv'))}
    (root / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2))
    subprocess.run(['chown', '-R', 'da-environment:da-environment', str(runtime)], check=True)
    # No descriptor is baked: the initial disposable fixture first boots normally;
    # each deployment explicitly provisions its own exact image descriptor.
    shutil.rmtree('/etc/docassemble/qualified-image', ignore_errors=True)


if __name__ == '__main__':
    main()
