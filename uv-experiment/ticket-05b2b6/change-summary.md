# 05b2b6 change summary

Commit: `fcea4ae64bb91fcdb8c6ee9eef57debbc4848a46` on `uv-implementation`.
Baseline: `ed83ba1595e81af3e1b8e30794559cef240511c2`.

Supervisor approved the single permitted bounded sub-split: old_password SQL
composite, including the exact built-in independently committing SQL hooks.

Changed production files: users/helpers.py, interview/helpers.py,
daglobal/hooks.py, tts/hooks.py, monitor/hooks.py (under webapp).
Managed set_user_info now locks before old_password work and commits password,
active/roles and secret dictionaries together. substitute_secret and built-in
SQL hook branches flush under transaction_owned; flag-off retains commits.
No schema, dependency, branch, new-system or public approval changes.

Added actual-source PostgreSQL secret writer tests: 34 passed. Full publication/
account/API-key suite: 252 passed; preparation aggregate: 365 passed (+34 intentional).
Act: 292 passed, 1 existing Supervisor skip; mypy: 51 files, no issues. Lint and
diff checks passed. Exact commands/output in committed accepted evidence logs.

Handoff TICKET-05b2b6.md lists remaining writers BY NAME. External plugins,
authentication/crypto and direct secret caller commit ownership are unqualified.
Remaining profile/account/registration/default-role composites; config save/
observation/startup/executor dependency refresh; PackageAuth/grant/name/source/
dependency-policy writer closure and races remain fenced. Public apply-producing
approve/retry remain fenced until 05c AND writer prerequisites. 05a2b4 and parent
05 acceptance unchanged; no production writer-safety claim. Review required.

This summary and diff.patch are intentionally UNTRACKED.
