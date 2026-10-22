"""Small valid wheels and original-source identities for preparation tests."""
import csv
import hashlib
import io
import zipfile

from docassemble_executor.bundles import sha256_file
from docassemble_executor.image_baseline import ImageRegistryWheel


def wheel(root, name='numpy', version='2.2.6', tag='py3-none-any', metadata_name=None, metadata_version=None,
          metadata_bytes=None):
    root.mkdir(parents=True, exist_ok=True)
    filename = name.replace('-', '_') + '-' + version + '-' + tag + '.whl'
    path = root / filename
    info = name.replace('-', '_') + '-' + version + '.dist-info/'
    contents = {name.replace('-', '_') + '/__init__.py': b'VALUE = 1\n',
        info + 'METADATA': metadata_bytes if metadata_bytes is not None else (
            'Metadata-Version: 2.1\nName: ' + (metadata_name or name) +
            '\nVersion: ' + (metadata_version or version) + '\n').encode(),
        info + 'WHEEL': ('Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: ' + tag + '\n').encode()}
    record = io.StringIO()
    writer = csv.writer(record)
    for member, data in contents.items():
        import base64
        writer.writerow([member, 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('='), len(data)])
    writer.writerow([info + 'RECORD', '', ''])
    contents[info + 'RECORD'] = record.getvalue().encode()
    with zipfile.ZipFile(path, 'w') as archive:
        for member, data in contents.items():
            archive.writestr(member, data)
    return path


def origin(name='numpy', version='2.2.6', data=b'original sdist'):
    return {'name': name, 'version': version, 'source': {'registry': 'https://pypi.org/simple'},
            'sdist': {'url': 'https://files.pythonhosted.org/' + name + '-' + version + '.tar.gz',
                      'hash': 'sha256:' + hashlib.sha256(data).hexdigest()}}


def reusable(root, name='numpy', version='2.2.6'):
    path = wheel(root, name, version)
    return ImageRegistryWheel(path, sha256_file(path), origin(name, version),
        {'image_target': 'current-qualified-image', 'bundle': 'runtime-r1', 'bundle_digest': 'a' * 64})


# Identical payload and identities; only the METADATA format evidence differs.
METADATA_CASES = {
    'well-formed': b'Metadata-Version: 2.1\nName: numpy\nVersion: 2.2.6\n\nnot-a-header\nRequires-Dist: hidden\n',
    'parser-defect': b'Metadata-Version: 2.1\nName: numpy\nVersion: 2.2.6\nnot-a-header\nRequires-Dist: hidden\n',
    'missing': b'Name: numpy\nVersion: 2.2.6\n',
    'duplicate': b'Metadata-Version: 2.1\nMetadata-Version: 2.1\nName: numpy\nVersion: 2.2.6\n',
}


def version_metadata(value):
    return ('Metadata-Version: ' + value + '\nName: numpy\nVersion: 2.2.6\n').encode()
