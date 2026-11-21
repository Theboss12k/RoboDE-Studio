#!/usr/bin/env bash
# RoboDE Studio on a Compute Engine VM (Ubuntu 24.04). Runs as the GCE startup-script on every boot; safe to re-run:
#   sudo google_metadata_script_runner startup
# Log: /var/log/robode-setup.log
set -euo pipefail
exec > >(tee -a /var/log/robode-setup.log) 2>&1
echo "== RoboDE setup $(date -Is)"

REPO_URL="${REPO_URL:-https://github.com/Theboss12k/RoboDE-Studio.git}"
BRANCH="${BRANCH:-main}"
APP=/opt/robode-studio
DATA=/var/lib/robode-studio
export DEBIAN_FRONTEND=noninteractive

md() { curl -fsS -H 'Metadata-Flavor: Google' "http://metadata.google.internal/computeMetadata/v1/$1"; }

# --- packages -------------------------------------------------------------------------------------------------------
apt-get update -qq
apt-get install -y -qq curl ca-certificates gnupg git openssl apt-transport-https debian-keyring debian-archive-keyring \
  python3.12 python3.12-venv python3-pip build-essential libgl1 libglib2.0-0 libgomp1 libusb-1.0-0 ffmpeg

install -d -m 0755 /etc/apt/keyrings
if [ ! -f /etc/apt/sources.list.d/adoptium.list ]; then
  curl -fsSL https://packages.adoptium.net/artifactory/api/gpg/key/public | gpg --dearmor --yes -o /etc/apt/keyrings/adoptium.gpg
  echo "deb [signed-by=/etc/apt/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb noble main" > /etc/apt/sources.list.d/adoptium.list
fi
if [ ! -f /etc/apt/sources.list.d/nodesource.list ]; then
  curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor --yes -o /etc/apt/keyrings/nodesource.gpg
  echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" > /etc/apt/sources.list.d/nodesource.list
fi
if [ ! -f /etc/apt/sources.list.d/caddy-stable.list ]; then
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
fi
apt-get update -qq
apt-get install -y -qq temurin-25-jdk nodejs caddy

# --- user and code --------------------------------------------------------------------------------------------------
id robode >/dev/null 2>&1 || useradd --system --create-home --home-dir /home/robode --shell /usr/sbin/nologin robode
install -d -o robode -g robode "$DATA" "$DATA/runtime" /opt
if [ -d "$APP/.git" ]; then
  runuser -u robode -- git -C "$APP" fetch --quiet origin "$BRANCH"
  runuser -u robode -- git -C "$APP" checkout --quiet "$BRANCH"
  runuser -u robode -- git -C "$APP" pull --ff-only --quiet origin "$BRANCH"
else
  install -d -o robode -g robode "$APP"
  runuser -u robode -- git clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP"
fi

# --- build (as robode) ----------------------------------------------------------------------------------------------
runuser -u robode -- bash -euo pipefail -c "
  cd '$APP'
  npm ci --no-audit --no-fund
  [ -x .venv/bin/python ] || python3.12 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
  # Site-scan packages (CPU). open3d needs libusb-1.0-0 and libgl1, installed above. Optional: a failure here is not fatal.
  .venv/bin/pip install --quiet -r requirements-scan.txt || echo 'WARNING: requirements-scan.txt failed; site scans are unavailable'
  (cd server-java && ./mvnw -q -DskipTests package)
  npm run build
"
JAR="$(ls "$APP"/server-java/target/studio-server-*.jar | grep -v '\.original$' | head -n1)"
[ -n "$JAR" ] || { echo "ERROR: jar not built"; exit 1; }

# --- configuration --------------------------------------------------------------------------------------------------
TOKEN="$(md instance/attributes/studio-token || true)"
if [ -z "$TOKEN" ]; then echo "ERROR: instance metadata attribute studio-token is missing"; exit 1; fi
PROJECT="$(md project/project-id)"
EXTERNAL_IP="$(md instance/network-interfaces/0/access-configs/0/external-ip)"
HOST="${EXTERNAL_IP//./-}.sslip.io"

umask 077
cat > /etc/robode-studio.env <<ENV
STUDIO_HOST=127.0.0.1
STUDIO_PORT=8800
STUDIO_TOKEN=$TOKEN
STUDIO_RUNTIME=$DATA/runtime
STUDIO_PYTHON=$APP/.venv/bin/python
STUDIO_REPO_ROOT=$APP
STUDIO_LLM_DEFAULT=vertex
GOOGLE_CLOUD_PROJECT=$PROJECT
ENV
chown root:robode /etc/robode-studio.env
chmod 0640 /etc/robode-studio.env
umask 022

# Role -> Vertex profile mapping, only for a first install (never overwrites what the user saved in Settings).
PROFILES="$DATA/runtime/llm/profiles.json"
if [ ! -f "$PROFILES" ]; then
  install -d -o robode -g robode "$DATA/runtime/llm"
  PROJECT="$PROJECT" PROFILES="$PROFILES" PYTHONPATH="$APP/server" "$APP/.venv/bin/python" - <<'PY'
import json, os
from studio.llm import registry
profiles = []
for p in registry.default_profiles():
    if p['kind'] == 'vertex_gemini':
        p = dict(p, project=os.environ['PROJECT'])
    profiles.append(p)
with open(os.environ['PROFILES'], 'w', encoding='utf-8') as f:
    json.dump({'profiles': profiles, 'roles': dict(registry.VERTEX_ROLES)}, f, indent=2)
PY
  chown robode:robode "$PROFILES"
fi

cat > /etc/systemd/system/robode-studio.service <<UNIT
[Unit]
Description=RoboDE Studio
After=network-online.target
Wants=network-online.target

[Service]
User=robode
Group=robode
WorkingDirectory=$APP
EnvironmentFile=/etc/robode-studio.env
Environment=PYTHONPATH=$APP/server
ExecStart=/usr/bin/java -Xms1g -Xmx4g -XX:+UseG1GC -jar $JAR
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/caddy/Caddyfile <<CADDY
$HOST {
	encode zstd gzip
	header {
		Strict-Transport-Security "max-age=31536000"
		X-Content-Type-Options nosniff
		X-Frame-Options DENY
		Referrer-Policy no-referrer
		-Server
	}
	request_body {
		max_size 200MB
	}
	reverse_proxy 127.0.0.1:8800 {
		flush_interval -1
		transport http {
			response_header_timeout 1h
		}
	}
}
CADDY

systemctl daemon-reload
systemctl enable robode-studio caddy
systemctl restart robode-studio
systemctl reload-or-restart caddy
echo "== RoboDE ready: https://$HOST/  (open https://$HOST/#token=<studio-token> once)"
