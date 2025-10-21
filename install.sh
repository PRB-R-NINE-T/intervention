#!/bin/bash
set -e

cd agent && uv python install 3.11 && uv venv .venv -p 3.11 && source .venv/bin/activate && uv pip install -e . && uv pip install -e third_party/DynamixelSDK/python && cd .. && cd ui && yarn install && yarn run build

echo "Installation complete"