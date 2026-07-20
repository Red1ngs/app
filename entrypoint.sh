#!/bin/sh
set -e
chown -R appuser:appuser /app/data /app/logs
exec setpriv --reuid=appuser --regid=appuser --init-groups "$@"