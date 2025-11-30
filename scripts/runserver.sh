#!/bin/sh

# Usage:
# bash scripts/runserver.sh

BASE_DIR="$( cd "$( dirname "$0" )" && pwd -P )/.."
PYTHON_BIN="$BASE_DIR/../.venv/bin"


PID=`ps -ef | grep python | grep blacktokki-notebook-agent | awk '{print $2}'`
if [ -n "$PID" ]
    then
        echo "=====$var is running at" $PID
else
    echo "=====$var isn't running====="
    (cd $BASE_DIR && nohup $PYTHON_BIN/python3.10 __init__.py > /dev/null 2>&1 &)
fi
