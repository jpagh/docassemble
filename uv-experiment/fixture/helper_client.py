"""Fixture setup/observations of actual cron and incoming mail domain records."""
import json
from pathlib import Path
import sys

import docassemble.base.config
docassemble.base.config.load(arguments=[])
from docassemble.webapp.server import app
from docassemble.webapp.extensions import db
from docassemble.base.parse import get_initial_dict
from docassemble.webapp.interview.helpers import save_user_dict
from docassemble.webapp.emailserver.models import Shortener, Email, EmailAttachment
from docassemble.webapp.files.savedfile import SavedFile
from docassemble.base.thread_context import empty_globals, global_context
from docassemble.webapp.users.models import UserModel
from sqlalchemy import select

FILENAME = 'docassemble.sentinel_fixture:data/questions/helper.yml'
with app.app_context(), global_context(empty_globals()):
    if sys.argv[1] == 'seed':
        user = db.session.scalars(select(UserModel).filter(UserModel.roles.any(name='cron'))).first()
        data = get_initial_dict()
        data.update(allow_cron=True, multi_user=True)
        save_user_dict('helper-proof', data, FILENAME, encrypt=False, manual_user_id=user.id)
        if db.session.scalars(select(Shortener).filter_by(short='helper')).first() is None:
            db.session.add(Shortener(short='helper', uid='helper-proof', filename=FILENAME, user_id=user.id))
            db.session.commit()
        print('PROOF_JSON=' + json.dumps({'seeded': True}))
    elif sys.argv[1] == 'messages':
        messages = []
        for item in db.session.scalars(select(Email).filter_by(short='helper')):
            attachments = []
            for part in db.session.scalars(select(EmailAttachment).filter_by(email_id=item.id).order_by(EmailAttachment.index)):
                saved = SavedFile(part.upload, extension=part.extension, fix=True)
                attachments.append({'index': part.index, 'data': Path(saved.path).read_bytes().hex()})
            messages.append({'id': item.id, 'subject': item.subject, 'attachments': attachments})
        print('PROOF_JSON=' + json.dumps(messages))
