#!/bin/bash
# Run this every time you push a code update to the server.
# Usage: bash deploy/deploy.sh

set -e

APP_DIR=/opt/contact-automation

echo "=== Pulling latest code ==="
cd "$APP_DIR"
git pull

echo "=== Installing/updating dependencies ==="
source venv/bin/activate
pip install -r requirements.txt -q

echo "=== Restarting service ==="
systemctl restart contact-automation
sleep 2
systemctl status contact-automation --no-pager

echo ""
echo "✅ Deployment complete."
