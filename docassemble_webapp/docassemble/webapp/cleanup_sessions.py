# pylint: disable=wrong-import-position
from docassemble.base.managed import reject_legacy_operation

if __name__ == '__main__':
    reject_legacy_operation('direct session-cleanup CLI')

from datetime import timedelta
from simplekv.memory.redisstore import RedisStore
from docassemblekvsession import KVSessionExtension
from docassemble.webapp.config import daconfig
from docassemble.webapp.daredis import r_store
from docassemble.webapp.app_object import flaskapp as app_stub

if 'session lifetime seconds' in daconfig:
    app_stub.config['PERMANENT_SESSION_LIFETIME'] = timedelta(seconds=daconfig['session lifetime seconds'])

kvsession = KVSessionExtension()
kvsession.init_app(app_stub, session_kvstore=RedisStore(r_store))

with app_stub.app_context():
    kvsession.cleanup_sessions()
