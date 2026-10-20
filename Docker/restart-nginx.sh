#! /bin/bash

source "$(dirname "${BASH_SOURCE[0]}")/managed-consumer.sh" restart-nginx || exit 75
if [ "${DASUPERVISORUSERNAME:-null}" != "null" ]; then
    SUPERVISORCMD="supervisorctl --serverurl http://localhost:9001 --username ${DASUPERVISORUSERNAME} --password ${DASUPERVISORPASSWORD}"
else
    SUPERVISORCMD="supervisorctl --serverurl http://localhost:9001"
fi

${SUPERVISORCMD} restart nginx > /dev/null
