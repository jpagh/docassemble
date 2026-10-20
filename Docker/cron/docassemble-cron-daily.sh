#! /bin/bash
# Backup phase-local environment changes intentionally do not affect this scheduler.
# shellcheck disable=SC2031

da_gate="${DA_ROOT:-/usr/share/docassemble}/webapp/managed-consumer.sh"
[ -f "$da_gate" ] || da_gate="$(dirname "${BASH_SOURCE[0]}")/../managed-consumer.sh"
source "$da_gate" cron-daily || exit 75
unset da_gate

export DA_ROOT="${DA_ROOT:-/usr/share/docassemble}"
export DA_DEFAULT_LOCAL="local3.14"

export DA_ACTIVATE="${DA_PYTHON:-${DA_ROOT}/${DA_DEFAULT_LOCAL}}/bin/activate"
# shellcheck source=/dev/null
source "${DA_ACTIVATE}" || exit 1
export DA_CONFIG_FILE="${DA_CONFIG:-${DA_ROOT}/config/config.yml}"
export CONTAINERROLE=":${CONTAINERROLE:-all}:"
if ! CONFIG_EXPORTS=$(su -c "source \"${DA_ACTIVATE}\" && python -m docassemble.base.read_config \"${DA_CONFIG_FILE}\"" www-data); then
    echo "daily: backup configuration load failed" >&2
    exit 1
fi
# shellcheck source=/dev/null
source /dev/stdin <<< "$CONFIG_EXPORTS" || exit 1
unset CONFIG_EXPORTS
export LOGDIRECTORY="${LOGDIRECTORY:-${DA_ROOT}/log}"

# read_config supplies a locale followed by its encoding; preserve that split.
# shellcheck disable=SC2086
set -- $LOCALE
export LANG=$1

# shellcheck source=Docker/backup.sh
source "${DA_ROOT}/webapp/backup.sh" || exit 1
BACKUP_STATUS=0

# Renew Let's Encrypt certificate; back up the certificates; copy certificates to exim
if [[ $CONTAINERROLE =~ .*:(all):.* ]] && [ "${USEHTTPS:-false}" == "true" ] && [ "${USELETSENCRYPT:-false}" == "true" ] && [ -f /etc/letsencrypt/da_using_lets_encrypt ]; then
    export USE_PYTHON_3=1
    if [ "${DAWEBSERVER:-nginx}" = "apache" ]; then
	"${DA_ROOT}/webapp/run-certbot.sh" renew --apache --cert-name "${DAHOSTNAME}" --pre-hook "supervisorctl ${DASUPERVISOROPTS}--serverurl http://localhost:9001 stop apache2" --post-hook "/etc/init.d/apache2 stop; supervisorctl ${DASUPERVISOROPTS}--serverurl http://localhost:9001 start apache2"
    fi
    if [ "${DAWEBSERVER:-nginx}" = "nginx" ]; then
	"${DA_ROOT}/webapp/run-certbot.sh" renew --nginx --cert-name "${DAHOSTNAME}" --pre-hook "supervisorctl ${DASUPERVISOROPTS}--serverurl http://localhost:9001 stop nginx" --post-hook "nginx -s stop &> /dev/null; supervisorctl ${DASUPERVISOROPTS}--serverurl http://localhost:9001 start nginx"
    fi
    docassemble_backup_certificates || BACKUP_STATUS=1
    if [ "$ENABLEEMAILSERVER" == "true" ]; then
	if [ ! -f /etc/ssl/docassemble/exim.crt ] && [ ! -f /etc/ssl/docassemble/exim.key ]; then
	    cp "/etc/letsencrypt/live/${DAHOSTNAME}/fullchain.pem" /etc/exim4/exim.crt
	    cp "/etc/letsencrypt/live/${DAHOSTNAME}/privkey.pem" /etc/exim4/exim.key
	    chown root:Debian-exim /etc/exim4/exim.crt
	    chown root:Debian-exim /etc/exim4/exim.key
	    chmod 640 /etc/exim4/exim.crt
	    chmod 640 /etc/exim4/exim.key
	    # read_config supplies supervisor option words, not one argument.
	    # shellcheck disable=SC2086
	    supervisorctl ${DASUPERVISOROPTS}--serverurl http://localhost:9001 stop exim4
	    # shellcheck disable=SC2086
	    supervisorctl ${DASUPERVISOROPTS}--serverurl http://localhost:9001 start exim4
	fi
    fi
fi

# Run cron jobs in docassemble interviews, as before. Standalone backup never does this.
if [[ $CONTAINERROLE =~ .*:(all|cron):.* ]]; then
    "${DA_ROOT}/webapp/run-cron.sh" cron_daily
fi

docassemble_backup_data || BACKUP_STATUS=1
exit "$BACKUP_STATUS"
