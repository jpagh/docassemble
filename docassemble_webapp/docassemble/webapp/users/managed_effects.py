"""Explicit split for managed built-in SQL and postcommit extension effects."""
import copy
from functools import partial
from importlib import import_module
from docassemble.base.plugin_manager import pm
from docassemble.base.thread_context import this_thread


BUILTIN_SQL_HOOKS = {
    'manage_global_objects': 'docassemble.webapp.daglobal.hooks',
    'manage_chat_logs': 'docassemble.webapp.monitor.hooks',
    'manage_tts_objects': 'docassemble.webapp.tts.hooks',
    'manage_email_server_objects': 'docassemble.webapp.emailserver.helpers',
}


def sql_hook(name, mode, effects, **kwargs):
    # Managed built-in authority is mandatory even if a firstresult plugin would
    # replace it in legacy dispatch. Extensions remain additive, postcommit only.
    builtin = import_module(BUILTIN_SQL_HOOKS[name])
    getattr(builtin, name)(mode, dict(kwargs, transaction_owned=True, effects=effects))
    plugins = pm.subset_hook_caller(name, remove_plugins=[builtin])
    info = copy.deepcopy(getattr(this_thread, 'current_info', {}))
    effects.append(partial(run_plugin_hook, plugins, mode, kwargs, info))


def run_plugin_hook(plugins, mode, kwargs, info):
    previous = getattr(this_thread, 'current_info', {})
    try:
        this_thread.current_info = info
        plugins(mode=mode, kwargs=kwargs)
    finally:
        this_thread.current_info = previous


def run_effects(effects):
    for effect in effects:
        effect()
