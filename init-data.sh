#!/bin/bash
echo "*** Initializing /app/data"
mkdir -p /app/data
if [ ! -f /app/data/favorites.json ]; then
    echo "[]" > /app/data/favorites.json
fi
echo "*** Updating permissions for /app/data/favorites.json"
chmod 666 /app/data/favorites.json
echo "*** /app/data permissions"
ls -l /app/data
echo "*** Checking /app/data permissions"
ls -ld /app/data
echo "*** Final /app/data permissions"
ls -l /app/data
echo "*** Running start.sh"
/start.sh
