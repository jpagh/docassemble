#!/bin/bash
# Existing backup work, callable without activating the application runtime.
# Callers supply the environment normally emitted by docassemble.base.read_config.
# Sourcing this file only defines operations; it does not run a backup.
# Each phase deliberately isolates its environment; no changes flow between phases.
# shellcheck disable=SC2030,SC2031

# Run best-effort, accumulating errors in the calling phase's local status.
# Returning 0 here lets other components run; the public phase returns failure.
_docassemble_backup_run() {
    local component="$1" result
    shift
    if "$@"; then
        return 0
    else
        result=$?
        echo "backup: ${component} failed (exit ${result})" >&2
        backup_status=1
        return 0
    fi
}

_docassemble_backup_azure_tree() {
    local source="${1%/}" prefix="$2" listing file relative
    if ! listing=$(mktemp); then
        echo "backup: azure-list temporary file failed" >&2
        backup_status=1
        return
    fi
    if find "$source" -type f -print0 > "$listing"; then
        while IFS= read -r -d '' file; do
            relative="${file#"${source}/"}"
            # The existing certificate/config branch flattens Apache site names.
            if [ "$prefix" == "apache" ]; then
                relative="${file##*/}"
            fi
            _docassemble_backup_run "azure-${prefix}" az storage blob upload --no-progress --overwrite true --only-show-errors --output none --container-name "${AZURECONTAINER}" -f "$file" -n "${prefix}/${relative}"
        done < "$listing"
    else
        echo "backup: azure-${prefix} file listing failed" >&2
        backup_status=1
    fi
    _docassemble_backup_run azure-list-cleanup rm -f "$listing"
}

_docassemble_backup_expire() {
    local root="$1" azure_prefix="${2:-}" listing files directory file relative
    if ! listing=$(mktemp); then
        echo "backup: retention temporary file failed" >&2
        backup_status=1
        return
    fi
    _docassemble_backup_run retention-list find "$root" -maxdepth 1 -path '*[0-9][0-9]-[0-9][0-9]' -type d -mtime "+${DABACKUPDAYS:-14}" -print0 > "$listing"
    while IFS= read -r -d '' directory; do
        if [ -n "$azure_prefix" ]; then
            if files=$(mktemp); then
                _docassemble_backup_run azure-retention-list find "$directory" -type f -print0 > "$files"
                while IFS= read -r -d '' file; do
                    relative="${file#"${root}/"}"
                    _docassemble_backup_run azure-retention-delete az storage blob delete --only-show-errors --output none --container-name "${AZURECONTAINER}" -n "${azure_prefix}/${relative}"
                done < "$files"
                _docassemble_backup_run retention-list-cleanup rm -f "$files"
            else
                echo "backup: retention temporary file failed" >&2
                backup_status=1
            fi
        fi
        _docassemble_backup_run retention-remove rm -rf "$directory"
    done < "$listing"
    _docassemble_backup_run retention-list-cleanup rm -f "$listing"
}

_docassemble_backup_certificates() {
    local destination="$1" archive_dir archive
    if [[ ! $CONTAINERROLE =~ .*:(all):.* ]] || [ "${USEHTTPS:-false}" != "true" ] || [ "${USELETSENCRYPT:-false}" != "true" ] || [ ! -f /etc/letsencrypt/da_using_lets_encrypt ]; then
        return
    fi
    if ! archive_dir=$(mktemp -d); then
        echo "backup: certificate-temporary-directory failed" >&2
        backup_status=1
        return
    fi
    archive="${archive_dir}/letsencrypt.tar.gz"
    _docassemble_backup_run certificates-archive tar -C / -zcf "$archive" etc/letsencrypt
    if [ -n "$destination" ]; then
        _docassemble_backup_run rolling-certificates cp "$archive" "${destination}/"
    elif [ "${S3ENABLE:-false}" == "true" ]; then
        _docassemble_backup_run certificates-upload s4cmd -f put "$archive" "s3://${S3BUCKET}/letsencrypt.tar.gz"
        if [ "${DAWEBSERVER:-nginx}" == "apache" ]; then
            _docassemble_backup_run apache-sites s4cmd dsync /etc/apache2/sites-available "s3://${S3BUCKET}/apache"
        fi
    elif [ "${AZUREENABLE:-false}" == "true" ]; then
        _docassemble_backup_run certificates-upload az storage blob upload --no-progress --overwrite true --only-show-errors --output none --container-name "${AZURECONTAINER}" -f "$archive" -n letsencrypt.tar.gz
        if [ "${DAWEBSERVER:-nginx}" == "apache" ]; then
            _docassemble_backup_azure_tree /etc/apache2/sites-available apache
        fi
    else
        # As before, the certificate copy does not consult the read-only flag.
        _docassemble_backup_run backup-directory mkdir -p "${DA_ROOT}/backup"
        _docassemble_backup_run certificates-copy cp "$archive" "${DA_ROOT}/backup/letsencrypt.tar.gz"
    fi
    _docassemble_backup_run certificates-cleanup rm -rf "$archive_dir"
}

docassemble_backup_certificates() (
    local backup_status=0 DA_ROOT="${DA_ROOT:-/usr/share/docassemble}"
    local CONTAINERROLE=":${CONTAINERROLE:-all}:"
    _docassemble_backup_certificates ""
    return "$backup_status"
)

_docassemble_backup_remote_redis() {
    # Preserve native output suppression, while the caller reports failure status.
    # Intentional splitting: read_config supplies a command plus CLI options.
    # shellcheck disable=SC2086
    $REDISCLI --rdb "$1" &> /dev/null
}

_docassemble_backup_webserver_logs() {
    local source prefix component
    local -a copy_options=(-auq)
    case "${DAWEBSERVER:-nginx}" in
        apache)
            source=/var/log/apache2
            prefix=apachelogs
            component=apache
            copy_options+=(--delete)
            ;;
        nginx)
            source=/var/log/nginx
            prefix=nginxlogs
            component=nginx
            ;;
        *) return ;;
    esac
    if [ "${S3ENABLE:-false}" == "true" ]; then
        _docassemble_backup_run "${component}-logs" s4cmd dsync "$source" "s3://${S3BUCKET}/${prefix}"
    elif [ "${AZUREENABLE:-false}" == "true" ]; then
        _docassemble_backup_azure_tree "$source" "$prefix"
    elif [ "${DAREADONLYFILESYSTEM:-false}" == "false" ]; then
        _docassemble_backup_run "${component}-directory" mkdir -p "${DA_ROOT}/backup/${prefix}"
        _docassemble_backup_run "${component}-logs" rsync "${copy_options[@]}" "${source}/" "${DA_ROOT}/backup/${prefix}/"
    fi
}

# The managed application dump and its object manifest travel together through
# the existing postgres current/dated/cloud destinations. Other DBs stay legacy.
_docassemble_backup_managed_dump() {
    local destination="$1"
    local executor="${DA_EXECUTOR_ENTRYPOINT:-/usr/share/docassemble/executor-venv/bin/da-executor}"
    "$(dirname "$executor")/python" -m docassemble_executor.managed_backup --dump "$destination"
}

docassemble_backup_data() (
    local backup_status=0
    local DA_ROOT="${DA_ROOT:-/usr/share/docassemble}"
    local DA_CONFIG_FILE="${DA_CONFIG_FILE:-${DA_ROOT}/config/config.yml}"
    local LOGDIRECTORY="${LOGDIRECTORY:-${DA_ROOT}/log}"
    local CONTAINERROLE=":${CONTAINERROLE:-all}:"
    local BACKUPDIR BACKUPTARGET LOCAL_HOSTNAME MONTHDAY PGBACKUPDIR="" databases db dump_command
    local cloud_target_ok managed_dump_done=false

    if [ "${S3ENABLE:-false}" == "true" ]; then
        if [[ $CONTAINERROLE =~ .*:(all|log):.* ]]; then
            _docassemble_backup_run cloud-logs s4cmd dsync "${DA_ROOT}/log" "s3://${S3BUCKET}/log"
        fi
    elif [ "${AZUREENABLE:-false}" == "true" ]; then
        if [[ $CONTAINERROLE =~ .*:(all|log):.* ]]; then
            _docassemble_backup_azure_tree "${LOGDIRECTORY}" log
        fi
    elif [ "${DAREADONLYFILESYSTEM:-false}" == "false" ]; then
        _docassemble_backup_run backup-directory mkdir -p "${DA_ROOT}/backup"
        if [[ $CONTAINERROLE =~ .*:(all|web|celery|log|cron):.* ]]; then
            _docassemble_backup_run local-logs rsync -auq --delete "${LOGDIRECTORY}" "${DA_ROOT}/backup/"
        fi
        if [[ $CONTAINERROLE =~ .*:(all|cron):.* ]]; then
            _docassemble_backup_run local-config-remove rm -f "${DA_ROOT}/backup/config.yml"
            _docassemble_backup_run local-config cp "${DA_CONFIG_FILE}" "${DA_ROOT}/backup/config.yml"
            _docassemble_backup_run local-files rsync -auq --delete "${DA_ROOT}/files" "${DA_ROOT}/backup/"
        fi
    fi
    if [[ $CONTAINERROLE =~ .*:(all):.* ]]; then
        _docassemble_backup_webserver_logs
    fi

    if [[ $CONTAINERROLE =~ .*:(all|sql):.* ]]; then
        if PGBACKUPDIR=$(mktemp -d); then
            _docassemble_backup_run postgres-directory-owner chown postgres:postgres "$PGBACKUPDIR"
            if databases=$(su postgres -c 'cd /tmp; psql -Atc "SELECT datname FROM pg_database" postgres'); then
                while IFS= read -r db; do
                    # Preserve the old grep exclusions, including substring matches.
                    [[ -z "$db" || "$db" == *template* || "$db" == *postgres* ]] && continue
                    printf -v dump_command 'cd /tmp; pg_dump -F c -f %q %q' "${PGBACKUPDIR}/${db}" "$db"
                    if [ "${DA_UV_MANAGED:-false}" == "true" ] && [ "$db" == "${DBNAME:-docassemble}" ]; then
                        _docassemble_backup_run managed-postgres-dump _docassemble_backup_managed_dump "${PGBACKUPDIR}/${db}"
                        managed_dump_done=true
                    else
                        _docassemble_backup_run postgres-dump su postgres -c "$dump_command"
                    fi
                done <<< "$databases"
            else
                echo "backup: postgres-database-list failed" >&2
                backup_status=1
            fi
            if [ "${S3ENABLE:-false}" == "true" ]; then
                _docassemble_backup_run postgres-upload s4cmd dsync "$PGBACKUPDIR" "s3://${S3BUCKET}/postgres"
            elif [ "${AZUREENABLE:-false}" == "true" ]; then
                _docassemble_backup_azure_tree "$PGBACKUPDIR" postgres
            elif [ "${DAREADONLYFILESYSTEM:-false}" == "false" ]; then
                _docassemble_backup_run postgres-copy rsync -auq "$PGBACKUPDIR/" "${DA_ROOT}/backup/postgres"
            fi
        else
            echo "backup: postgres-temporary-directory failed" >&2
            backup_status=1
        fi
    fi

    if [[ $CONTAINERROLE =~ .*:(all|redis):.* ]] && [ -f /var/lib/redis/dump.rdb ]; then
        if [ "${S3ENABLE:-false}" == "true" ]; then
            _docassemble_backup_run redis-upload s4cmd -f put /var/lib/redis/dump.rdb "s3://${S3BUCKET}/redis.rdb"
        elif [ "${AZUREENABLE:-false}" == "true" ]; then
            _docassemble_backup_run redis-upload az storage blob upload --no-progress --overwrite true --only-show-errors --output none --container-name "${AZURECONTAINER}" -f /var/lib/redis/dump.rdb -n redis.rdb
        elif [ "${DAREADONLYFILESYSTEM:-false}" == "false" ]; then
            _docassemble_backup_run redis-copy cp /var/lib/redis/dump.rdb "${DA_ROOT}/backup/redis.rdb"
        fi
    fi

    if [ "${DAREADONLYFILESYSTEM:-false}" == "false" ] && [ "${DABACKUPDAYS:-14}" != "0" ]; then
        if ! MONTHDAY=$(date +%m-%d) || [[ ! $MONTHDAY =~ ^[0-9][0-9]-[0-9][0-9]$ ]]; then
            echo "backup: rolling-date failed" >&2
            backup_status=1
        else
            _docassemble_backup_run backup-directory mkdir -p "${DA_ROOT}/backup"
            BACKUPDIR="${DA_ROOT}/backup/${MONTHDAY}"
            _docassemble_backup_run rolling-remove rm -rf "${BACKUPDIR}"
            _docassemble_backup_run rolling-directory mkdir -p "${BACKUPDIR}"
            _docassemble_backup_certificates "$BACKUPDIR"
            if [[ $CONTAINERROLE =~ .*:(all|web|celery|log|cron):.* ]]; then
                if [ "${BACKUPFILESTORAGE:-true}" == "true" ] && [ "${S3ENABLE:-false}" == "false" ] && [ "${AZUREENABLE:-false}" == "false" ]; then
                    _docassemble_backup_run rolling-files rsync -auq "${DA_ROOT}/files" "${BACKUPDIR}/"
                fi
                _docassemble_backup_run rolling-config rsync -auq "${DA_ROOT}/config" "${BACKUPDIR}/"
                _docassemble_backup_run rolling-logs rsync -auq "${LOGDIRECTORY}" "${BACKUPDIR}/"
            fi
            if [[ $CONTAINERROLE =~ .*:(all|redis):.* ]]; then
                if [ -f /var/lib/redis/dump.rdb ]; then
                    _docassemble_backup_run rolling-redis cp /var/lib/redis/dump.rdb "${BACKUPDIR}/redis.rdb"
                fi
            elif [[ $CONTAINERROLE =~ .*:cron:.* ]] && [ "${REDIS:-redis://localhost}" != "redis://localhost" ]; then
                _docassemble_backup_run rolling-remote-redis _docassemble_backup_remote_redis "${BACKUPDIR}/redis.rdb"
            fi
            if [ "${DBTYPE:-}" == "postgresql" ]; then
                if [[ $CONTAINERROLE =~ .*:(all|sql):.* ]]; then
                    if [ -n "$PGBACKUPDIR" ]; then
                        _docassemble_backup_run rolling-postgres rsync -auq "$PGBACKUPDIR/" "${BACKUPDIR}/postgres"
                    fi
                elif [[ $CONTAINERROLE =~ .*:cron:.* ]] && [ "${DBHOST:-localhost}" != "localhost" ] && [ "${DBBACKUP:-true}" == "true" ]; then
                    if PGBACKUPDIR=$(mktemp -d); then
                        export PGPASSWORD="${DBPASSWORD:-abc123}"
                        [ -n "${DBPORT:-}" ] && export PGPORT="$DBPORT"
                        [ -n "${DBSSLMODE:-}" ] && export PGSSLMODE="$DBSSLMODE"
                        [ -n "${DBSSLCERT:-}" ] && export PGSSLCERT="/etc/ssl/docassemble/${DBSSLCERT}"
                        [ -n "${DBSSLKEY:-}" ] && export PGSSLKEY="/etc/ssl/docassemble/${DBSSLKEY}"
                        [ -n "${DBSSLROOTCERT:-}" ] && export PGSSLROOTCERT="/etc/ssl/docassemble/${DBSSLROOTCERT}"
                        if [ "${DA_UV_MANAGED:-false}" == "true" ]; then
                            _docassemble_backup_run managed-postgres-dump _docassemble_backup_managed_dump "${PGBACKUPDIR}/${DBNAME}"
                            managed_dump_done=true
                        else
                            _docassemble_backup_run remote-postgres-dump pg_dump -F c -f "${PGBACKUPDIR}/${DBNAME}" -h "${DBHOST}" -U "${DBUSER:-docassemble}" -w -p "${DBPORT:-5432}" "${DBNAME}"
                        fi
                        unset PGPASSWORD PGSSLMODE PGSSLCERT PGSSLKEY PGSSLROOTCERT
                        _docassemble_backup_run rolling-postgres rsync -auq "$PGBACKUPDIR/" "${BACKUPDIR}/postgres"
                    else
                        echo "backup: postgres-temporary-directory failed" >&2
                        backup_status=1
                    fi
                fi
            fi
            if [ "${AZUREENABLE:-false}" == "false" ]; then
                _docassemble_backup_expire "${DA_ROOT}/backup"
            fi
            if [ "${S3ENABLE:-false}" == "true" ] || [ "${AZUREENABLE:-false}" == "true" ]; then
                if [ "${S3ENABLE:-false}" == "true" ]; then
                    BACKUPTARGET="s3://${S3BUCKET}/backup"
                else
                    BACKUPTARGET="backup"
                fi
                cloud_target_ok=true
                if [[ ! $CONTAINERROLE =~ .*:(all):.* ]]; then
                    if [ "${EC2:-false}" == "true" ]; then
                        LOCAL_HOSTNAME=$(curl -fsS http://169.254.169.254/latest/meta-data/local-hostname) || cloud_target_ok=false
                    else
                        LOCAL_HOSTNAME=$(hostname --fqdn) || cloud_target_ok=false
                    fi
                    if [ "$cloud_target_ok" == "false" ] || [ -z "$LOCAL_HOSTNAME" ]; then
                        echo "backup: cloud-hostname failed" >&2
                        backup_status=1
                        cloud_target_ok=false
                    else
                        BACKUPTARGET="${BACKUPTARGET}/${LOCAL_HOSTNAME}"
                    fi
                fi
                if [ "$cloud_target_ok" == "true" ]; then
                    if [ "${S3ENABLE:-false}" == "true" ]; then
                        _docassemble_backup_run cloud-rolling s4cmd --delete-removed dsync "${DA_ROOT}/backup" "${BACKUPTARGET}"
                    else
                        _docassemble_backup_azure_tree "${DA_ROOT}/backup" "${BACKUPTARGET}"
                        _docassemble_backup_expire "${DA_ROOT}/backup" "$BACKUPTARGET"
                    fi
                fi
            fi
        fi
    fi
    if [ "${DA_UV_MANAGED:-false}" == "true" ]; then
        if [ "$managed_dump_done" != "true" ]; then
            echo "backup: managed objects incomplete (no application database dump selected)" >&2
            backup_status=1
        fi
        if [ "${DAREADONLYFILESYSTEM:-false}" == "true" ] && [ "${S3ENABLE:-false}" != "true" ] && [ "${AZUREENABLE:-false}" != "true" ]; then
            echo "backup: managed objects incomplete (no durable backup destination selected)" >&2
            backup_status=1
        fi
    fi
    if [ -n "$PGBACKUPDIR" ]; then
        _docassemble_backup_run postgres-cleanup rm -rf "$PGBACKUPDIR"
    fi
    return "$backup_status"
)

docassemble_backup() (
    local status=0
    docassemble_backup_certificates || status=1
    docassemble_backup_data || status=1
    return "$status"
)

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    docassemble_backup
    exit $?
fi
