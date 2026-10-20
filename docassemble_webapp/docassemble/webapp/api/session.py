"""API credentials are request authority, never browser-session authority."""
from flask import g, request
from flask.sessions import SessionInterface


def api_request(req):
    return (req.path == '/api' or req.path.startswith('/api/') or
            bool(req.endpoint and req.endpoint.rsplit('.', 1)[-1].startswith('api_')))


class APISessionInterface(SessionInterface):
    """Fence both reads and writes, including Blueprint endpoints and backend saves."""
    def __init__(self, browser):
        self.browser = browser

    def __getattr__(self, name):
        return getattr(self.browser, name)

    def open_session(self, app, req):
        if api_request(req):
            # Flask opens sessions before URL matching; path detection is needed.
            environ = dict(req.environ)
            environ.pop('HTTP_COOKIE', None)
            environ.pop('HTTP_X_USER_AUTH', None)
            req = app.request_class(environ)
        return self.browser.open_session(app, req)

    def save_session(self, app, session, response):
        if not api_request(request):
            self.browser.save_session(app, session, response)


def init_app(app):
    if not isinstance(app.session_interface, APISessionInterface):
        app.session_interface = APISessionInterface(app.session_interface)

        @app.before_request
        def api_anonymous_browser_identity():
            if api_request(request):
                # Flask-Login can otherwise load a remember cookie even when
                # the server-side session is empty. Only api_verify may replace
                # this request-local anonymous identity.
                g._login_user = app.login_manager.anonymous_user()
