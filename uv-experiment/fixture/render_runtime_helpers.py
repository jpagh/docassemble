"""Image-only rendering of OS job runtime paths from protected executor tooling."""
from pathlib import Path

from docassemble_executor.paths import ExecutorPaths


def main():
    runtime = ExecutorPaths.from_env().runtime_venv
    assert (runtime / 'bin/python').exists(), 'managed runtime interpreter missing'
    fixture = Path('/opt/docassemble-fixture')
    for source, destination in [('helper-crontab', '/etc/crontab'),
                                ('helper-exim.conf', '/etc/exim4/exim4.conf')]:
        template = (fixture / source).read_text()
        assert '@MANAGED_RUNTIME@' in template, 'runtime helper placeholder missing'
        rendered = template.replace('@MANAGED_RUNTIME@', str(runtime))
        assert '@MANAGED_RUNTIME@' not in rendered, 'unrendered managed runtime'
        Path(destination).write_text(rendered)


if __name__ == '__main__':
    main()
