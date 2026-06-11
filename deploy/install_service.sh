#!/bin/bash
# Run this after setup.sh to install the systemd service and nginx config.
# Usage: bash deploy/install_service.sh

set -e

APP_DIR=/opt/contact-automation

echo "=== Installing systemd service ==="
cp "$APP_DIR/deploy/contact-automation.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable contact-automation
systemctl restart contact-automation
echo "Service status:"
systemctl status contact-automation --no-pager

echo ""
echo "=== Installing nginx config ==="
cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/contact-automation
ln -sf /etc/nginx/sites-available/contact-automation /etc/nginx/sites-enabled/contact-automation
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl restart nginx

echo ""
echo "✅ Done. App is running at http://$(curl -s ifconfig.me)"
