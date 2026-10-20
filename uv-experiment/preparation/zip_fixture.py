"""Small real distribution archives for retained-ZIP tests and pinned-uv proof."""
import csv
import hashlib
import io
import base64
import zipfile


def distribution(path, *, name='zip_example', version='1.0', wheel=True, requires=(), extras=None):
    extras = extras or {}
    metadata = f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n'
    metadata += ''.join(f'Requires-Dist: {requirement}\n' for requirement in requires)
    for extra, requirements in extras.items():
        metadata += f'Provides-Extra: {extra}\n'
        metadata += ''.join(f'Requires-Dist: {requirement}; extra == "{extra}"\n' for requirement in requirements)
    if wheel:
        prefix = f'{name}-{version}.dist-info/'
        files = {f'{name}/__init__.py': f'__version__ = {version!r}\n',
                 prefix + 'METADATA': metadata,
                 prefix + 'WHEEL': 'Wheel-Version: 1.0\nGenerator: retained-zip-fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
        record = io.StringIO()
        writer = csv.writer(record)
        for filename, value in files.items():
            encoded = value.encode()
            digest = base64.urlsafe_b64encode(hashlib.sha256(encoded).digest()).rstrip(b'=').decode()
            writer.writerow([filename, 'sha256=' + digest, len(encoded)])
        writer.writerow([prefix + 'RECORD', '', ''])
        files[prefix + 'RECORD'] = record.getvalue()
    else:
        prefix = f'{name}-{version}/'
        files = {prefix + 'PKG-INFO': metadata,
                 prefix + 'setup.py': f'from setuptools import setup\nsetup(name={name!r}, version={version!r}, packages=[{name!r}], install_requires={list(requires)!r}, extras_require={extras!r})\n',
                 prefix + f'{name}/__init__.py': f'__version__ = {version!r}\n',
                 prefix + 'pyproject.toml': '[build-system]\nrequires = ["setuptools==80.9.0"]\nbuild-backend = "setuptools.build_meta"\n'}
    with zipfile.ZipFile(path, 'w') as archive:
        for filename, value in files.items():
            archive.writestr(filename, value)
    return path
