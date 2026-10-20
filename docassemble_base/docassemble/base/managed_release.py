"""Coherent registry core-release selection and metadata validation.

The selected ``docassemble.webapp`` registry release's own ``Requires-Dist``
metadata is authoritative for the matching ``docassemble.base``/``docassemble.demo``
versions. Selection never falls back to another release, never substitutes an
independent Git core, and never invents a mixture from unrecognized metadata.
Nothing here performs I/O: the executor supplies catalog input.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from docassemble.base.managed_preparation import PreparationError

WEBAPP = 'docassemble-webapp'
BASE = 'docassemble-base'
DEMO = 'docassemble-demo'
CORE_FAMILY = (BASE, WEBAPP, DEMO)
MODES = ('latest-stable', 'exact', 'prerelease')

RISK_DISCLOSURE = (
    'core-release-solved-freshly-from-the-approved-registry',
    'package-rollback-does-not-restore-data',
    'optional-pre-change-checkpoints-unavailable-until-slices-21-23',
    'schema-compatibility-and-data-risk-not-yet-verified',
)


def upgrade_risk_disclosure() -> list[str]:
    """Fixed, redacted disclosure recorded in every upgrade preview result."""
    return list(RISK_DISCLOSURE)


@dataclass(frozen=True)
class ReleaseSelection:
    mode: str
    version: str | None

    def to_dict(self) -> dict[str, Any]:
        return {'mode': self.mode, 'version': self.version}


def parse_release_selection(body: Any) -> ReleaseSelection:
    if not isinstance(body, dict) or set(body) - {'mode', 'version'} or not body:
        raise PreparationError('submit one registry release selection')
    mode = body.get('mode')
    if mode not in MODES:
        raise PreparationError('release selection mode must be latest-stable, exact or prerelease')
    version = body.get('version')
    if mode == 'latest-stable':
        if version is not None:
            raise PreparationError('latest-stable takes no explicit version')
    else:
        if not isinstance(version, str) or not version or len(version) > 128:
            raise PreparationError('an explicit release version is required')
        try:
            Version(version)
        except InvalidVersion:
            raise PreparationError('invalid release version') from None
    return ReleaseSelection(mode=mode, version=version)


def _parsed_versions(versions: Iterable[str]) -> list[str]:
    parsed = []
    for value in versions:
        if not isinstance(value, str):
            raise PreparationError('invalid registry release catalog')
        try:
            parsed.append((Version(value), value))
        except InvalidVersion:
            raise PreparationError('invalid registry release catalog') from None
    return parsed


def select_release(versions: Iterable[str], selection: ReleaseSelection) -> str:
    """Resolve a selection to one exact published release, or fail closed."""
    parsed = _parsed_versions(versions)
    if not parsed:
        raise PreparationError('no registry releases are available')
    if selection.mode == 'latest-stable':
        stable = [value for version, value in parsed if not version.is_prerelease]
        if not stable:
            raise PreparationError('no stable registry release is available')
        return max(stable, key=Version)
    requested = Version(selection.version)
    if selection.mode == 'prerelease' and not requested.is_prerelease:
        raise PreparationError('prerelease selection requires a prerelease version')
    if selection.mode == 'exact' and requested.is_prerelease:
        raise PreparationError('prereleases require explicit prerelease selection')
    for version, value in parsed:
        if version == requested:
            return value
    raise PreparationError('requested release is not published')


def _exact_pin(declaration: str, name: str, expected: set[str]) -> str:
    try:
        parsed = Requirement(declaration)
    except InvalidRequirement:
        raise PreparationError('unrecognized release metadata') from None
    if canonicalize_name(parsed.name) != name:
        raise PreparationError('unrecognized release metadata')
    pins = [specifier.version for specifier in parsed.specifier if specifier.operator == '==']
    if (parsed.url is not None or parsed.extras or parsed.marker is not None or
            len(parsed.specifier) != 1 or len(pins) != 1):
        raise PreparationError('release metadata must pin ' + name + ' exactly')
    return pins[0]


def coherent_core(webapp_version: str, requires_dist: Iterable[str]) -> dict[str, str]:
    """Resolve the coherent core pins from authoritative webapp metadata.

    The webapp release must pin base and demo exactly, at its own version. Any
    missing, non-exact, inconsistent or unrecognized pin fails actionably; no
    mixture is invented and no independent Git core is substituted.
    """
    try:
        expected = str(Version(webapp_version))
    except InvalidVersion:
        raise PreparationError('invalid selected release version') from None
    pins: dict[str, str] = {}
    for declaration in requires_dist:
        if not isinstance(declaration, str):
            raise PreparationError('unrecognized release metadata')
        try:
            parsed = Requirement(declaration)
        except InvalidRequirement:
            raise PreparationError('unrecognized release metadata') from None
        name = canonicalize_name(parsed.name)
        if name not in (BASE, DEMO):
            continue
        pin = _exact_pin(declaration, name, {expected})
        if name in pins:
            raise PreparationError('release metadata pins ' + name + ' more than once')
        pins[name] = str(Version(pin))
    if set(pins) != {BASE, DEMO}:
        raise PreparationError('release metadata must pin matching base and demo versions')
    for name, pin in pins.items():
        if pin != expected:
            raise PreparationError('release metadata does not pin a coherent ' + name + ' release')
    return {WEBAPP: expected, BASE: pins[BASE], DEMO: pins[DEMO]}


def core_requirements(coherent: dict[str, str]) -> list[str]:
    return [name + '==' + coherent[name] for name in (BASE, WEBAPP, DEMO)]


def reject_downgrade(coherent: dict[str, str], current_core: dict[str, Any]) -> None:
    """Fresh browser-selected downgrades are out of scope; only newer releases."""
    current = current_core.get(WEBAPP)
    current_version = current.get('version') if isinstance(current, dict) else None
    if not isinstance(current_version, str):
        return
    try:
        selected = Version(coherent[WEBAPP])
        baseline = Version(current_version)
    except InvalidVersion:
        raise PreparationError('current core release is not comparable') from None
    if selected <= baseline:
        raise PreparationError('selected release is not newer than the current core; fresh downgrade is unsupported')
