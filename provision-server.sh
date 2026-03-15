#!/bin/bash
# Full server provisioning script — rebuilds everything from scratch
# Usage: ./provision-server.sh <server-ip>
# Requires: SSH key at ~/.ssh/hetzner_ed25519 with root access

set -e

IP="${1:?Usage: ./provision-server.sh <server-ip>}"
SSH="ssh -o StrictHostKeyChecking=accept-new -i ~/.ssh/hetzner_ed25519 root@$IP"
RSYNC="rsync -avz -e 'ssh -i ~/.ssh/hetzner_ed25519'"

echo "=== Provisioning server at $IP ==="

# ─── 1. Create user and install packages ─────────────────────────────────────
echo "[1/7] Creating user and installing packages..."
$SSH bash <<'REMOTE'
set -e
id edede 2>/dev/null || useradd -m -s /bin/bash edede
mkdir -p /home/edede/.ssh
cp /root/.ssh/authorized_keys /home/edede/.ssh/
chown -R edede:edede /home/edede/.ssh
chmod 700 /home/edede/.ssh
chmod 600 /home/edede/.ssh/authorized_keys
echo "edede ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/edede

apt-get update -qq
apt-get install -y -qq nginx mariadb-server \
  php-fpm php-mysql php-xml php-mbstring php-curl php-gd php-zip php-intl php-imagick \
  python3 python3-pip python3-venv > /dev/null 2>&1

# Secure MariaDB
systemctl enable mariadb
systemctl start mariadb

# Set home dir permissions for nginx
chmod 755 /home/edede
echo "Packages installed"
REMOTE

# ─── 2. Deploy Weather Dashboard ─────────────────────────────────────────────
echo "[2/7] Deploying Weather Edge dashboard..."
eval $RSYNC --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' \
  --exclude 'node_modules' --exclude '.DS_Store' --exclude 'daemon.pid' \
  --exclude 'logs/' --exclude '.worktrees' --exclude '.venv' \
  ~/Projects/polymarket-weather-bot/ edede@$IP:~/polymarket-weather-bot/

$SSH bash <<'REMOTE'
set -e
cd /home/edede/polymarket-weather-bot
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
mkdir -p logs
chown -R edede:edede /home/edede/polymarket-weather-bot

cat > /etc/systemd/system/weather-dashboard.service << 'EOF'
[Unit]
Description=Weather Edge Dashboard
After=network.target

[Service]
User=edede
WorkingDirectory=/home/edede/polymarket-weather-bot
ExecStart=/home/edede/polymarket-weather-bot/.venv/bin/python -m uvicorn dashboard.api:app --host 127.0.0.1 --port 8501
Restart=always
RestartSec=5
EnvironmentFile=/home/edede/polymarket-weather-bot/.env

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable weather-dashboard
systemctl start weather-dashboard
echo "Weather dashboard running"
REMOTE

# ─── 3. Deploy WordPress (ethanede.com) ──────────────────────────────────────
echo "[3/7] Deploying WordPress..."
eval $RSYNC "/Users/edede/Local Sites/ethanede/app/public/" edede@$IP:~/ethanede.com/public/

$SSH bash <<'REMOTE'
set -e
chown -R edede:www-data /home/edede/ethanede.com
find /home/edede/ethanede.com/public -type d -exec chmod 755 {} \;
find /home/edede/ethanede.com/public -type f -exec chmod 644 {} \;
chmod 640 /home/edede/ethanede.com/public/wp-config.php
chmod -R 775 /home/edede/ethanede.com/public/wp-content
echo "WordPress files deployed"
REMOTE

# ─── 4. Set up WordPress database ────────────────────────────────────────────
echo "[4/7] Setting up WordPress database..."
DB_PASS=$(openssl rand -base64 24)

$SSH bash <<REMOTE
set -e
mysql -u root <<SQL
CREATE DATABASE IF NOT EXISTS ethanede_wp DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'ethanede_wp'@'localhost' IDENTIFIED BY '$DB_PASS';
GRANT ALL PRIVILEGES ON ethanede_wp.* TO 'ethanede_wp'@'localhost';
FLUSH PRIVILEGES;
SQL
echo "Database created"
REMOTE

# Import SQL dump if exists
SQL_FILE=$(ls "/Users/edede/Local Sites/ethanede/app/sql/"*.sql 2>/dev/null | head -1)
if [ -n "$SQL_FILE" ]; then
    echo "Importing SQL dump: $SQL_FILE"
    eval $RSYNC "$SQL_FILE" root@$IP:/tmp/wp-import.sql
    $SSH bash <<'REMOTE'
mysql -u root ethanede_wp < /tmp/wp-import.sql
# Update URLs from local to production
mysql -u root ethanede_wp -e "UPDATE wp_options SET option_value='https://ethanede.com' WHERE option_name IN ('siteurl','home');"
rm /tmp/wp-import.sql
echo "SQL imported and URLs updated"
REMOTE
fi

# Update wp-config.php on server
$SSH bash <<REMOTE
set -e
cd /home/edede/ethanede.com/public
# Update DB credentials
sed -i "s/define( *'DB_NAME'.*/define('DB_NAME', 'ethanede_wp');/" wp-config.php
sed -i "s/define( *'DB_USER'.*/define('DB_USER', 'ethanede_wp');/" wp-config.php
sed -i "s/define( *'DB_PASSWORD'.*/define('DB_PASSWORD', '$DB_PASS');/" wp-config.php
sed -i "s/define( *'DB_HOST'.*/define('DB_HOST', 'localhost');/" wp-config.php

# Add Cloudflare SSL detection if not present
grep -q 'HTTP_X_FORWARDED_PROTO' wp-config.php || sed -i "1a\\
if (isset(\\\$_SERVER['HTTP_X_FORWARDED_PROTO']) && \\\$_SERVER['HTTP_X_FORWARDED_PROTO'] === 'https') { \\\$_SERVER['HTTPS'] = 'on'; }" wp-config.php

echo "wp-config.php updated"
REMOTE

# ─── 5. Build and deploy TypeForge ───────────────────────────────────────────
echo "[5/7] Building and deploying TypeForge..."
cd "/Users/edede/Local Sites/typeforge"
npm install --silent 2>/dev/null
npm run build 2>/dev/null
eval $RSYNC "/Users/edede/Local Sites/typeforge/dist/" edede@$IP:~/type.ethanede.com/public/
cd ~/Projects/polymarket-weather-bot

$SSH bash <<'REMOTE'
chown -R edede:edede /home/edede/type.ethanede.com
echo "TypeForge deployed"
REMOTE

# ─── 6. Configure nginx ──────────────────────────────────────────────────────
echo "[6/7] Configuring nginx..."
$SSH bash <<'REMOTE'
set -e

# Weather Edge
cat > /etc/nginx/sites-available/weather-edge << 'NGINX'
server {
    listen 80;
    server_name 5.78.146.1;
    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGINX

# WordPress
cat > /etc/nginx/sites-available/ethanede.com << 'NGINX'
server {
    listen 80;
    server_name ethanede.com www.ethanede.com;
    root /home/edede/ethanede.com/public;
    index index.php index.html;

    client_max_body_size 64M;

    set_real_ip_from 173.245.48.0/20;
    set_real_ip_from 103.21.244.0/22;
    set_real_ip_from 103.22.200.0/22;
    set_real_ip_from 103.31.4.0/22;
    set_real_ip_from 141.101.64.0/18;
    set_real_ip_from 108.162.192.0/18;
    set_real_ip_from 190.93.240.0/20;
    set_real_ip_from 188.114.96.0/20;
    set_real_ip_from 197.234.240.0/22;
    set_real_ip_from 198.41.128.0/17;
    set_real_ip_from 162.158.0.0/15;
    set_real_ip_from 104.16.0.0/13;
    set_real_ip_from 104.24.0.0/14;
    set_real_ip_from 172.64.0.0/13;
    set_real_ip_from 131.0.72.0/22;
    real_ip_header CF-Connecting-IP;

    location / {
        try_files $uri $uri/ /index.php?$args;
    }

    location ~ \.php$ {
        include snippets/fastcgi-params.conf;
        fastcgi_pass unix:/run/php/php8.3-fpm.sock;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
        include fastcgi_params;
    }

    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff2?)$ {
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    location ~ /\. { deny all; }
}
NGINX

# TypeForge
cat > /etc/nginx/sites-available/type.ethanede.com << 'NGINX'
server {
    listen 80;
    server_name type.ethanede.com;
    root /home/edede/type.ethanede.com/public;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff2?)$ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
NGINX

# Enable all sites
ln -sf /etc/nginx/sites-available/weather-edge /etc/nginx/sites-enabled/
ln -sf /etc/nginx/sites-available/ethanede.com /etc/nginx/sites-enabled/
ln -sf /etc/nginx/sites-available/type.ethanede.com /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

nginx -t && systemctl restart nginx
echo "Nginx configured — all 3 sites enabled"
REMOTE

# ─── 7. Verify ───────────────────────────────────────────────────────────────
echo "[7/7] Verifying services..."
$SSH bash <<'REMOTE'
echo "--- Service status ---"
systemctl is-active weather-dashboard && echo "weather-dashboard: OK" || echo "weather-dashboard: FAIL"
systemctl is-active nginx && echo "nginx: OK" || echo "nginx: FAIL"
systemctl is-active mariadb && echo "mariadb: OK" || echo "mariadb: FAIL"
systemctl is-active php8.3-fpm && echo "php-fpm: OK" || echo "php-fpm: FAIL"
echo ""
echo "--- HTTP checks ---"
curl -s -o /dev/null -w "weather-edge: %{http_code}\n" http://127.0.0.1:8501/
curl -s -o /dev/null -w "ethanede.com: %{http_code}\n" -H "Host: ethanede.com" http://127.0.0.1/
curl -s -o /dev/null -w "type.ethanede.com: %{http_code}\n" -H "Host: type.ethanede.com" http://127.0.0.1/
REMOTE

echo ""
echo "=== Provisioning complete ==="
echo "Update Cloudflare DNS A records to $IP:"
echo "  ethanede.com       → $IP (SSL: Full)"
echo "  type.ethanede.com  → $IP"
echo "  weather dashboard  → $IP"
