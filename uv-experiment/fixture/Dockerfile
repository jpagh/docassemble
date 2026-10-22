# syntax=docker/dockerfile:1
# Disposable experiment image for the independent browser-recovery proof.
#
# Pinned inputs:
#   - base image by digest (--build-arg DOCASSEMBLE_OS_IMAGE=...@sha256:...)
#   - uv 0.12.21 (the image manifest records the exact binary version)
#   - CPython 3.14.4 provisioned by uv at image build
# The retained core-only and runtime bundles plus the executor venv are built
# at image-build time and recorded in
# /usr/share/docassemble/managed/bundles/manifest.json.

ARG DOCASSEMBLE_OS_IMAGE=jhpyle/docassemble-os@sha256:9ada5376be7f4fbd1a276406c2bb7304dfa6f315d63cb0094640e2fbf0ecf9eb

FROM ghcr.io/astral-sh/uv:0.12.21 AS uvbin

FROM ${DOCASSEMBLE_OS_IMAGE}

USER root

ARG DOCASSEMBLE_OS_IMAGE
ARG DA_REPO_COMMIT=unknown

COPY --from=uvbin /uv /usr/local/libexec/uv-pinned
COPY uv-experiment/fixture/uv-audit.sh /usr/local/bin/uv
RUN chmod 755 /usr/local/bin/uv /usr/local/libexec/uv-pinned \
    && touch /var/log/docassemble-uv-invocations.log
COPY --from=uvbin /uvx /usr/local/bin/uvx

ENV DA_UV_MANAGED=true \
    DA_ENVIRONMENT_USER=da-environment \
    UV_PYTHON_INSTALL_DIR=/usr/share/docassemble/uv-python \
    UV_CACHE_DIR=/var/cache/docassemble/uv \
    DA_BUNDLE_DIR=/usr/share/docassemble/managed/bundles \
    DA_CONFIG_FILE=/usr/share/docassemble/config/config.yml \
    DA_MANAGED_STATE_DIR=/var/lib/docassemble/managed \
    DA_MANAGED_ARTIFACT_ROOT=/var/lib/docassemble/artifacts \
    DA_MANAGED_ARTIFACT_UID=33 \
    DA_EXECUTOR_ARTIFACT_ROOT=/var/lib/docassemble-build/artifacts \
    DA_MANAGED_SECRET_ROOT=/var/lib/docassemble-build/secrets \
    DA_RUN_DIR=/var/run/docassemble \
    DA_RECOVERY_RUN_DIR=/var/run/docassemble/recovery \
    PATH=/usr/share/docassemble/executor-venv/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin

COPY uv-experiment/executor /opt/docassemble-executor
COPY uv-experiment/fixture /opt/docassemble-fixture
COPY Docker/managed-startup.sh Docker/initialize.sh /opt/docassemble-fixture/
COPY Docker /opt/da-repo/Docker
RUN cp /opt/da-repo/Docker/*.sh /usr/share/docassemble/webapp/ \
    && cp /opt/da-repo/Docker/cron/docassemble-cron-hourly.sh /etc/cron.hourly/docassemble \
    && cp /opt/da-repo/Docker/cron/docassemble-cron-daily.sh /etc/cron.daily/docassemble \
    && cp /opt/da-repo/Docker/cron/docassemble-cron-weekly.sh /etc/cron.weekly/docassemble \
    && cp /opt/da-repo/Docker/cron/docassemble-cron-monthly.sh /etc/cron.monthly/docassemble
# This inherited tool's entrypoint otherwise imports the mutable legacy Python
# before any repository hook can refuse its server-control fan-out.
RUN mv /usr/share/docassemble/local3.14/bin/certbot /usr/share/docassemble/local3.14/bin/certbot.unmanaged \
    && install -m 755 /opt/da-repo/Docker/run-certbot.sh /usr/share/docassemble/local3.14/bin/certbot \
    && printf '#!/bin/bash\nexec /usr/share/docassemble/local3.14/bin/certbot "$@"\n' > /usr/local/bin/certbot \
    && chmod 755 /usr/local/bin/certbot
COPY Docker/config/config.yml.dist /opt/docassemble-fixture/config/config.yml.dist
COPY docassemble_webapp/docassemble.wsgi /usr/share/docassemble/webapp/docassemble.wsgi
# Preserve the executor wheel's repository-relative shared-schema include.
COPY docassemble_base/docassemble/base/managed_python.py /docassemble_base/docassemble/base/managed_python.py
COPY docassemble_base/docassemble/base/managed_preparation.py /docassemble_base/docassemble/base/managed_preparation.py
COPY docassemble_base/docassemble/base/managed_migration.py /docassemble_base/docassemble/base/managed_migration.py
COPY docassemble_base/docassemble/base/managed_adoption.py /docassemble_base/docassemble/base/managed_adoption.py
COPY docassemble_base/docassemble/base/managed_publication.py /docassemble_base/docassemble/base/managed_publication.py
COPY docassemble_base/docassemble/base/managed_checkpoint.py /docassemble_base/docassemble/base/managed_checkpoint.py
COPY docassemble_base/docassemble/base/managed_release.py /docassemble_base/docassemble/base/managed_release.py
COPY docassemble_base/docassemble/base/managed_rollback.py /docassemble_base/docassemble/base/managed_rollback.py
COPY docassemble_base/docassemble/base/managed_config.py /docassemble_base/docassemble/base/managed_config.py
COPY docassemble_base/docassemble/base/managed_git.py /docassemble_base/docassemble/base/managed_git.py
COPY docassemble_base/docassemble/base/managed_direct.py /docassemble_base/docassemble/base/managed_direct.py
COPY docassemble_base/docassemble/base/managed_artifacts.py /docassemble_base/docassemble/base/managed_artifacts.py
COPY docassemble_base/docassemble/base/managed_records.py /docassemble_base/docassemble/base/managed_records.py
COPY docassemble_base/docassemble/base/managed_qualification.py /docassemble_base/docassemble/base/managed_qualification.py
COPY docassemble_base /opt/da-sources/docassemble_base
COPY docassemble_demo /opt/da-sources/docassemble_demo
COPY docassemble_webapp /opt/da-sources/docassemble_webapp

RUN install -d -m 700 -o www-data -g www-data "${DA_MANAGED_ARTIFACT_ROOT}" \
    && test "$(id -u www-data)" = "${DA_MANAGED_ARTIFACT_UID}" \
    && chmod +x /opt/docassemble-fixture/image-build.sh /opt/docassemble-fixture/entrypoint.sh \
    && chown www-data:www-data /usr/share/docassemble/webapp/docassemble.wsgi \
    && DOCASSEMBLE_OS_IMAGE="${DOCASSEMBLE_OS_IMAGE}" DA_REPO_COMMIT="${DA_REPO_COMMIT}" \
       bash /opt/docassemble-fixture/image-build.sh \
    && install -d -m 700 -o da-environment -g da-environment "${DA_EXECUTOR_ARTIFACT_ROOT}" "${DA_MANAGED_SECRET_ROOT}"

# Only this disposable topology is qualified: never enable installed certbot,
# anacron or miscellaneous root control jobs by starting stock cron wholesale.
RUN rm -f /etc/cron.d/* \
    && install -m 644 /opt/docassemble-fixture/helper-crontab /etc/crontab \
    && install -d -m 755 /etc/docassemble \
    && install -m 600 /opt/docassemble-fixture/helper-backup.json /etc/docassemble/managed-backup.json \
    && install -m 640 -g Debian-exim /opt/docassemble-fixture/helper-exim.conf /etc/exim4/exim4.conf \
    && install -m 644 /opt/da-repo/Docker/config/exim4-filter /etc/exim4/docassemble-filter \
    && /usr/share/docassemble/executor-venv/bin/python /opt/docassemble-fixture/render_runtime_helpers.py

EXPOSE 80

ENTRYPOINT ["/opt/docassemble-fixture/entrypoint.sh"]
