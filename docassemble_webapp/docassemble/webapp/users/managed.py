"""SQL authority mutations for managed helper callers, environment lock first.

Caller owns permission checks and the transaction. No independent commit here.
Other legacy account/role writers remain unqualified: public publication is fenced.
"""
from sqlalchemy import table, column, Integer, Boolean, Text, select, delete, update
from docassemble.base.managed_preparation import PreparationError
from docassemble.base.managed_records import preparation_tables


def lock_authority(connection, prefix):
    environment = preparation_tables(prefix)['environment_state']
    row = connection.execute(select(environment).where(environment.c.id == 1).with_for_update()).mappings().first()
    if not row or not row['managed']:
        raise PreparationError('no explicitly managed baseline')


def change_privilege(connection, prefix, user_id, privilege, *, add):
    lock_authority(connection, prefix)
    users = table(prefix + 'user', column('id', Integer), column('social_id', Text))
    roles = table(prefix + 'role', column('id', Integer), column('name', Text))
    membership = table(prefix + 'user_roles', column('user_id', Integer), column('role_id', Integer))
    social_id = connection.scalar(select(users.c.social_id).where(users.c.id == user_id))
    if social_id is None or social_id.startswith('disabled$'):
        raise PreparationError('The specified user did not exist')
    role_id = connection.scalar(select(roles.c.id).where(roles.c.name == privilege))
    if role_id is None:
        raise PreparationError('The specified privilege does not exist.')
    condition = (membership.c.user_id == user_id) & (membership.c.role_id == role_id)
    exists = connection.execute(select(membership).where(condition)).first() is not None
    if add:
        if exists:
            raise PreparationError('The user already had that privilege.')
        connection.execute(membership.insert().values(user_id=user_id, role_id=role_id))
    else:
        if not exists:
            raise PreparationError('The user did not already have that privilege.')
        connection.execute(delete(membership).where(condition))


def deactivate_user(connection, prefix, user_id=None, email=None):
    lock_authority(connection, prefix)
    users = table(prefix + 'user', column('id', Integer), column('email', Text), column('active', Boolean))
    condition = users.c.id == user_id if user_id is not None else users.c.email == email
    identity = connection.scalar(select(users.c.id).where(condition))
    if identity is None:
        raise PreparationError('User not found')
    connection.execute(update(users).where(users.c.id == identity).values(active=False))


def delete_privilege(connection, prefix, privilege):
    lock_authority(connection, prefix)
    if privilege in ("user", "admin", "developer", "advocate", "cron"):
        raise PreparationError("The specified privilege is built-in and cannot be deleted.")
    roles = table(prefix + "role", column("id", Integer), column("name", Text))
    membership = table(prefix + "user_roles", column("user_id", Integer), column("role_id", Integer))
    role_id = connection.scalar(select(roles.c.id).where(roles.c.name == privilege))
    if role_id is None:
        raise PreparationError("The privilege " + str(privilege) + " did not exist.")
    connection.execute(delete(membership).where(membership.c.role_id == role_id))
    connection.execute(delete(roles).where(roles.c.id == role_id))


def delete_privilege_for_view(connection, prefix, privilege_id):
    """Preserve the view's default-role fallback in one authority transaction."""
    lock_authority(connection, prefix)
    roles = table(prefix + 'role', column('id', Integer), column('name', Text))
    membership = table(prefix + 'user_roles', column('user_id', Integer), column('role_id', Integer))
    users = table(prefix + 'user', column('id', Integer))
    name = connection.scalar(select(roles.c.name).where(roles.c.id == privilege_id))
    default_id = connection.scalar(select(roles.c.id).where(roles.c.name == 'user'))
    if name is None or default_id is None or name in ('user', 'admin', 'developer', 'advocate', 'cron', 'trainer'):
        raise PreparationError('The role could not be deleted.')
    affected = list(connection.scalars(select(users.c.id).where(users.c.id.in_(
        select(membership.c.user_id).where(membership.c.role_id == privilege_id)))))
    connection.execute(delete(membership).where(membership.c.role_id == privilege_id))
    for user_id in affected:
        if connection.execute(select(membership).where(membership.c.user_id == user_id)).first() is None:
            connection.execute(membership.insert().values(user_id=user_id, role_id=default_id))
    connection.execute(delete(roles).where(roles.c.id == privilege_id))
    return name
