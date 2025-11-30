#!/bin/sh

# Usage:
# bash scripts/stopserver.sh


PID=`ps -ef | grep python | grep blacktokki-notebook-agent | awk '{print $2}'`
if [ -n "$PID" ]
then
    echo "=====$var is running at" $PID
    kill -9 $PID
else
    echo "=====$var isn't running====="
fi