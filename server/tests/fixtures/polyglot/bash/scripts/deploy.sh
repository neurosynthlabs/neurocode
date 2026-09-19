#!/usr/bin/env bash
source ./lib.sh

function deploy {
  if [ -n "$1" ]; then
    log "deploying $1"
  fi
}
