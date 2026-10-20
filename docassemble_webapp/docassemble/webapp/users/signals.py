import datetime
from flask import request
from sqlalchemy import select
from docassemble_flask_user import (
    user_logged_in,
    user_changed_password,
    user_registered,
)
from docassemble.base.managed import managed_installation
from docassemble.webapp.database import dbtableprefix
from docassemble.webapp.users.managed import lock_authority
from docassemble.webapp.users.managed_effects import run_effects
from docassemble.webapp.extensions import db
from docassemble.webapp.interview.helpers import fix_secret
from .helpers import update_last_login, login_or_register, assign_registration_role
from .models import Role

def on_user_login(sender, user, **extra):
    if managed_installation():
        if getattr(request, '_managed_login_migrated_user_id', None) == user.id:
            return
        lock_authority(db.session.connection(), dbtableprefix)
        db.session.expire_all()
        effects = []
        user.last_login = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        login_or_register(sender, user, 'login', effects=effects, **extra)
        db.session.commit()
        run_effects(effects)
        return
    # logmessage("on user login")
    update_last_login(user)
    login_or_register(sender, user, 'login', **extra)
    # flash(word('You have signed in successfully.'), 'success')


def on_password_change(sender, user, **extra):  # pylint: disable=unused-argument
    # logmessage("on password change")
    fix_secret(user=user)

# @user_reset_password.connect_via(app)
# def _on_password_reset(sender, user, **extra):
#     # logmessage("on password reset")
#     fix_secret(user=user)


def on_register_hook(sender, user, **extra):
    if managed_installation():
        if extra.get('migration_committed', False):
            return
        lock_authority(db.session.connection(), dbtableprefix)
        db.session.expire_all()
        effects = []
        if not extra.get('authority_committed', False):
            assign_registration_role(user, extra.get('user_invite'))
        login_or_register(sender, user, 'register', effects=effects, **extra)
        db.session.commit()
        run_effects(effects)
        return
    # why did I not just import it globally?
    # from docassemble.webapp.users.models import Role
    user_invite = extra.get('user_invite', None)
    this_user_role = None
    if user_invite is not None:
        this_user_role = db.session.execute(select(Role).filter_by(id=user_invite.role_id)).scalar()
    if this_user_role is None:
        this_user_role = db.session.execute(select(Role).filter_by(name='user')).scalar()
    roles_to_remove = []
    for role in user.roles:
        roles_to_remove.append(role)
    for role in roles_to_remove:
        user.roles.remove(role)
    user.roles.append(this_user_role)
    db.session.commit()
    update_last_login(user)
    login_or_register(sender, user, 'register', **extra)


def register_signals(app):
    user_logged_in.connect(on_user_login, app)
    user_changed_password.connect(on_password_change, app)
    user_registered.connect(on_register_hook, app)
