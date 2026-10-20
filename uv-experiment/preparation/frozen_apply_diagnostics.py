"""Credential-redacted child output for the standalone frozen-apply proofs."""
import base64
import re
from subprocess import CompletedProcess
from urllib.parse import quote, quote_plus, unquote

from docassemble.base.managed_direct import redact_direct_url


_URL = re.compile(r'\b[A-Za-z][A-Za-z0-9+.\-]*://[^\s<>"\']+')
_ESCAPED_SLASH = re.compile(r'\\(?:/|u002f)', re.IGNORECASE)
_SENSITIVE_FIELD = re.compile(
    r'''(?i)(?P<key_quote>["']?)\b(?P<name>(?:[\w-]*-)?authorization|x-auth-[\w-]+|set-cookie|cookie)'''
    r'''(?P=key_quote)\s*[:=][ \t]*''')
_QUOTED_VALUE = re.compile(r'"(?:\\.|[^"\\])*"' r"|'(?:\\.|[^'\\])*'", re.DOTALL)
_FOLD = re.compile(r'\r?\n[ \t]+')
_USERINFO = re.compile(r'''[^\s<>"'/{}]+@[^\s<>"'/{}?,;]+(?:[/?][^\s<>"'{}]*)?''')
_ENCODED_TEXT = re.compile(r'''\\(?:u([0-9a-fA-F]{4})|U([0-9a-fA-F]{8})|x([0-9a-fA-F]{2})|(.|$))''', re.DOTALL)
_COOKIE_ATTRIBUTES = frozenset(('path', 'domain', 'expires', 'httponly', 'secure', 'samesite'))
_REDACTED_DIAGNOSTIC = '[redacted diagnostic]'


def _quoted_end(value: str, offset: int) -> int:
    match = _QUOTED_VALUE.match(value, offset)
    if match is None:
        raise ValueError('unterminated sensitive value')
    return match.end()


def _unquoted_end(value: str, offset: int) -> int:
    """Scan parameters without mistaking a quoted brace for a field boundary."""
    while offset < len(value):
        char = value[offset]
        if char in ('"', "'"):
            offset = _quoted_end(value, offset)
            continue
        if char == '\\' and value[offset + 1:offset + 2] in ('\r', '\n'):
            raise ValueError('ambiguous sensitive continuation')
        if char in ('\r', '\n'):
            fold = _FOLD.match(value, offset)
            if fold is None:
                if char == '\r' and value[offset + 1:offset + 2] != '\n':
                    raise ValueError('ambiguous sensitive continuation')
                return offset
            offset = fold.end()
            continue
        if char == '}':
            return offset
        if char in (',', ';') and _SENSITIVE_FIELD.match(value, offset + 1 + len(
                value[offset + 1:]) - len(value[offset + 1:].lstrip())):
            return offset
        offset += 1
    return offset


def _field_end(value: str, offset: int, *, quoted_key: bool) -> int:
    if value[offset:offset + 1] not in ('"', "'"):
        return _unquoted_end(value, offset)
    end = _quoted_end(value, offset)
    after_spaces = end + len(value[end:]) - len(value[end:].lstrip(' \t'))
    fold = _FOLD.match(value, after_spaces)
    if fold:
        return _unquoted_end(value, fold.end())
    tail = value[after_spaces:]
    if tail.startswith((',', ';')):
        next_field = after_spaces + 1
        next_field += len(value[next_field:]) - len(value[next_field:].lstrip(' \t'))
        if _SENSITIVE_FIELD.match(value, next_field):
            return end
        if quoted_key and tail.startswith(','):
            key = _QUOTED_VALUE.match(value, next_field)
            if key and value[key.end():].lstrip(' \t').startswith(':'):
                return end
        # A closing quote alone does not prove that remaining parameters are
        # outside the sensitive value; consume them too.
        return _unquoted_end(value, after_spaces)
    if tail and tail[0] not in ',;}\r\n]':
        raise ValueError('ambiguous quoted sensitive value')
    if tail.startswith('\r') and not tail.startswith('\r\n'):
        raise ValueError('ambiguous sensitive continuation')
    return end


def _redact_cookie(value: str, *, attributes: bool) -> str:
    """Only Set-Cookie has attributes; its first pair is always a cookie."""
    parts, offset, start = [], 0, 0
    while offset <= len(value):
        if offset < len(value) and value[offset] in ('"', "'"):
            offset = _quoted_end(value, offset)
            continue
        if offset == len(value) or value[offset] == ';':
            part = value[start:offset]
            name, separator, content = part.partition('=')
            if attributes and parts and name.strip().lower() in _COOKIE_ATTRIBUTES:
                parts.append(part)
            elif separator and name.strip():
                leading = content[:len(content) - len(content.lstrip())]
                trailing = content[len(content.rstrip()):]
                content = content.strip()
                mark = content[0] if content.startswith(('"', "'")) else ''
                parts.append(name + '=' + leading + mark + '[credential]' + mark + trailing)
            elif part.strip():
                raise ValueError('unrecognized cookie value')
            else:
                parts.append(part)
            start = offset + 1
        offset += 1
    return ';'.join(parts)


def _redact_sensitive_fields(value: str) -> str:
    """Drop complete header values, including quoted parameters and folded lines."""
    parts, offset = [], 0
    for match in _SENSITIVE_FIELD.finditer(value):
        if match.start() < offset:
            continue
        start = match.end()
        fold = _FOLD.match(value, start)
        if fold:
            start = fold.end()
        elif value[start:start + 1] in ('\r', '\n'):
            raise ValueError('ambiguous sensitive continuation')
        end = _field_end(value, start, quoted_key=bool(match['key_quote']))
        parts.append(value[offset:start])
        header = value[start:end]
        mark = header[0] if header.startswith(('"', "'")) else ''
        if match['name'].lower() in ('cookie', 'set-cookie'):
            # An outer JSON/string quote is not part of the cookie syntax.
            content = header[1:-1] if mark and _quoted_end(header, 0) == len(header) else header
            replacement = mark + _redact_cookie(content, attributes=match['name'].lower() == 'set-cookie') + mark
        else:
            replacement = mark + '[credential]' + mark
        parts.append(replacement)
        offset = end
    parts.append(value[offset:])
    return ''.join(parts)


def _decoded_diagnostic(value: str) -> str:
    """Decode only a validation view; ambiguous escaped source is never emitted."""
    def decode(match):
        code = next((group for group in match.groups()[:3] if group is not None), None)
        if code is not None:
            return chr(int(code, 16))
        escapes = {'"': '"', "'": "'", '\\': '\\', '/': '/',
                   'n': '\n', 'r': '\r', 't': '\t', 'b': '\b', 'f': '\f'}
        if match[4] not in escapes:
            raise ValueError('unrecognized diagnostic escape')
        return escapes[match[4]]
    while True:
        decoded = unquote(_ENCODED_TEXT.sub(decode, value), errors='strict')
        if decoded == value:
            return value
        value = decoded


def _credential_patterns(credentials: tuple[str, ...]) -> tuple[re.Pattern, ...]:
    patterns = []
    for credential in credentials:
        if not credential:
            continue
        for secret in dict.fromkeys((credential, quote(credential, safe=''), quote_plus(credential))):
            patterns.append(re.compile(re.escape(secret)))
        for material in (credential + ':', credential):
            secret = base64.b64encode(material.encode()).decode()
            patterns.append(re.compile(re.escape(secret)))
    return tuple(patterns)


def _redact_credentials(value: str, patterns: tuple[re.Pattern, ...]) -> str:
    for pattern in patterns:
        value = pattern.sub('[credential]', value)
    return value


def frozen_apply_failure(applied: CompletedProcess[str], message: str, *, credentials: tuple[str, ...] = ()) -> str:
    """Keep both child streams and exit status, not source credentials."""
    def redact_url(match):
        try:
            return redact_direct_url(match.group(0))
        except ValueError:
            return '[redacted URL]'

    def redact(value):
        try:
            patterns = _credential_patterns(credentials)
            value = _redact_credentials(value, patterns)
            value = _ESCAPED_SLASH.sub('/', value)
            value = _redact_sensitive_fields(value)
            value = _URL.sub(redact_url, value)
            # Malformed URLs and bare authorities must not bypass URL parsing.
            value = _USERINFO.sub('[redacted URL]', value)
            value = _redact_credentials(value, patterns)
            # A successful partial match is not proof of removal. Check a decoded
            # view as well, so escaped field names and URL delimiters fail closed.
            for view in (value, _decoded_diagnostic(value)):
                if (any(pattern.search(view) for pattern in patterns) or
                        _redact_sensitive_fields(view) != view or _USERINFO.search(view)):
                    return _REDACTED_DIAGNOSTIC
                for url in _URL.finditer(view):
                    if '\\' in url[0] or redact_url(url) != url[0]:
                        return _REDACTED_DIAGNOSTIC
            return value
        except Exception:
            # A diagnostic is optional; never retain source text after a redaction failure.
            return _REDACTED_DIAGNOSTIC

    return (f'{redact(message)} (exit {applied.returncode})\n'
            f'stdout:\n{redact(applied.stdout or "")}\n'
            f'stderr:\n{redact(applied.stderr or "")}')
