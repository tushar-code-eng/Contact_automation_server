#!/bin/bash
# Run this ONCE on a fresh Hetzner Ubuntu 22.04 server as root.
# Usage: bash setup.sh

set -e

echo "=== [1/7] System update ==="
apt-get update && apt-get upgrade -y

echo "=== [2/7] Install Python 3.12 + pip + venv ==="
apt-get install -y python3.12 python3.12-venv python3-pip git curl

echo "=== [3/7] Install Nginx ==="
apt-get install -y nginx
systemctl enable nginx

echo "=== [4/7] Create app directory and user ==="
useradd --system --no-create-home --shell /bin/false automation || true
mkdir -p /opt/contact-automation
chown automation:automation /opt/contact-automation

echo "=== [5/7] Clone / copy project ==="
# If using git:
#   git clone https://github.com/YOUR_USERNAME/Contact_Automation_Server.git /opt/contact-automation
# Otherwise upload files via scp (see deploy guide) and skip this block.

echo "=== [6/7] Python virtualenv + dependencies ==="
cd /opt/contact-automation
python3.12 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "=== [7/7] Install Playwright browsers ==="
playwright install chromium
playwright install-deps chromium

echo "=== Setting up nightly DB backup (2am daily) ==="
(crontab -l 2>/dev/null; echo "0 2 * * * cd /opt/contact-automation && venv/bin/python scripts/backup_db.py >> /var/log/contact-automation-backup.log 2>&1") | crontab -

echo ""
echo "✅ Setup complete."
echo "Next steps:"
echo "  1. Copy your .env file to /opt/contact-automation/.env"
echo "  2. Run: bash /opt/contact-automation/deploy/install_service.sh"
