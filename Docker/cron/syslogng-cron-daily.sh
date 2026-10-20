#! /bin/bash

export CONTAINERROLE=":${CONTAINERROLE:-all}:"

if [ "${DASUPERVISORUSERNAME:-null}" != "null" ]; then
    SUPERVISORCMD="supervisorctl --serverurl http://localhost:9001 --username ${DASUPERVISORUSERNAME} --password ${DASUPERVISORPASSWORD}"
else
    SUPERVISORCMD="supervisorctl --serverurl http://localhost:9001"
fi

if [[ $CONTAINERROLE =~ .*:(log):.* ]] && [ -d /etc/syslog-ng ]; then
    ${SUPERVISORCMD} restart syslogng
fi
