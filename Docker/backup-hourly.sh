#!/bin/bash
# Independent legacy hourly file/log copies. Trusted caller supplies the same
# resolved configuration environment as backup.sh; no runtime or control hooks.
set -u
DA_ROOT="${DA_ROOT:-/usr/share/docassemble}"
LOGDIRECTORY="${LOGDIRECTORY:-${DA_ROOT}/log}"
CONTAINERROLE=":${CONTAINERROLE:-all}:"
status=0
if [[ $CONTAINERROLE =~ .*:(all|web):.* ]]; then
    case "${DAWEBSERVER:-nginx}" in
        apache) source_directory=/var/log/apache2 ;;
        nginx) source_directory=/var/log/nginx ;;
        *) source_directory="" ;;
    esac
    if [ -n "$source_directory" ]; then
        if rsync -auq "$source_directory/" "$LOGDIRECTORY/"; then
            chown -R www-data:www-data "$LOGDIRECTORY" || status=1
        else
            status=1
        fi
    fi
fi
if [ "${S3ENABLE:-false}" == false ] && [ "${AZUREENABLE:-false}" == false ]; then
    rsync -auq --delete "${DA_ROOT}/files" "${DA_ROOT}/backup/" || status=1
fi
exit "$status"
