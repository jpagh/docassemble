"""Runtime WSGI adapter: caller context is scoped to each complete response."""
from docassemble.webapp.server import application as backend
from .capabilities import caller_context


def application(environ, start_response):
    with caller_context(environ.get('HTTP_X_DA_ACCEPTED_CALLER', '')):
        response = backend(environ, start_response)
        try:
            yield from response
        finally:
            if hasattr(response, 'close'):
                response.close()
