#!/bin/bash
# Sync code via mpremote
mpremote connect auto fs cp -r src/* : + cp -r lib/* :lib/
