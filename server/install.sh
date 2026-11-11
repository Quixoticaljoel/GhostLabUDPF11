#!/usr/bin/env bash
# Ghost Lab server installer for a cloud server (e.g. Oracle Always Free, Ubuntu).
# Normally run for you by the script the setup helper makes (web/setup.html).
#
#   sudo bash server/install.sh --cloud --domain you.duckdns.org --duckdns-token TOKEN
#
# Other options:
#   --driver NAME     name of the first driver (default: your user name)
#   --pages URL       your GitHub Pages address, so links open the app from there
#   --drivers LIST    starting drivers, e.g. "Alex:alexcode1,Sam,Jo" (code optional)
#   --admin-code CODE admin code that sees every driver (made up if not given)
#   --web-admin on|off  let the admin add/remove drivers from the website (on for --cloud)
#
# Safe to run again: drivers, access codes and laps are kept. Settings live in
# /etc/ghostlab (never in the git repository).
set -euo pipefail

DOMAIN="" TOKEN="" CLOUD=0 DRIVER="" PAGES="" HTTP_PORT=8000 DRIVER_LIST="" ADMIN="" WEB_ADMIN=""
while [ $# -gt 0 ]; do
  case "$1" in
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --duckdns-token) TOKEN="${2:-}"; shift 2 ;;
    --cloud) CLOUD=1; shift ;;
    --driver) DRIVER="${2:-}"; shift 2 ;;
    --pages) PAGES="${2:-}"; shift 2 ;;
    --drivers) DRIVER_LIST="${2:-}"; shift 2 ;;
    --admin-code) ADMIN="${2:-}"; shift 2 ;;
    --web-admin) WEB_ADMIN="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1 (try --help)"; exit 1 ;;
  esac
done

ETC=/etc/ghostlab
ENV_FILE=$ETC/ghostlab.env
DRIVERS=$ETC/drivers.json
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UDP_RANGE="20777:20799"

say()  { printf '\033[1;35m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m  %s\n' "$*"; }
fail() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "run this with sudo:  sudo bash server/install.sh"
[ -f "$APP_DIR/bridge.py" ] && [ -f "$APP_DIR/web/index.html" ] || fail "run this from inside the ghost-lab folder"
command -v systemctl >/dev/null || fail "this system doesn't use systemd"
DOMAIN="$(printf '%s' "$DOMAIN" | tr '[:upper:]' '[:lower:]')"
if [ -n "$DOMAIN" ]; then
  [[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]] || fail "--domain should look like you.duckdns.org"
fi
if [ -n "$PAGES" ] && [[ ! "$PAGES" =~ ^https:// ]]; then fail "--pages should be your full https:// GitHub Pages address"; fi
if [ -n "$ADMIN" ] && [[ ! "$ADMIN" =~ ^[A-Za-z0-9_-]{6,64}$ ]]; then fail "--admin-code: 6-64 letters and numbers"; fi

RUN_USER="${SUDO_USER:-root}"
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
LAPS_DIR="${RUN_HOME:-/root}/ghostlab-laps"
DRIVER="${DRIVER:-$RUN_USER}"
[ "$DRIVER" = "root" ] && DRIVER="Driver"

# ---- packages ----------------------------------------------------------------
apt_install() { if command -v apt-get >/dev/null; then DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@" >/dev/null; elif command -v dnf >/dev/null; then dnf install -y -q "$@"; else fail "please install: $*"; fi; }
if ! command -v python3 >/dev/null || ! command -v curl >/dev/null; then
  say "Installing Python and curl"
  command -v apt-get >/dev/null && apt-get update -qq
  apt_install python3 curl
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)' || fail "Python 3.7 or newer is needed (use the Ubuntu 22.04 or 24.04 image)"

# ---- settings (keep existing values unless new ones were given) --------------
old() { [ -f "$ENV_FILE" ] && sed -n "s/^$1=//p" "$ENV_FILE" | head -n1 || true; }
PUBLIC="${DOMAIN:-$(old GHOSTLAB_PUBLIC)}"
HTTPS="$(old GHOSTLAB_HTTPS)"; [ -n "$DOMAIN" ] && HTTPS=1
PAGES="${PAGES:-$(old GHOSTLAB_PAGES)}"
FPS="$(old GHOSTLAB_FPS)"; FPS="${FPS:-30}"
[ "$CLOUD" = 1 ] || CLOUD="$(old GHOSTLAB_CLOUD)"; CLOUD="${CLOUD:-0}"
if [ "$CLOUD" = 1 ] && [ -z "$PUBLIC" ]; then
  say "Finding this server's public IP"
  PUBLIC="$(curl -fsS --max-time 6 https://api.ipify.org 2>/dev/null || curl -fsS --max-time 6 https://ifconfig.me 2>/dev/null || true)"
  [[ "$PUBLIC" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || fail "couldn't detect the public IP - use --domain, or try again"
fi
ADMIN="${ADMIN:-$(old GHOSTLAB_ADMIN_KEY)}"
[ -n "$ADMIN" ] || ADMIN="$(python3 -c 'import secrets; a="abcdefghjkmnpqrstuvwxyz23456789"; print("".join(secrets.choice(a) for _ in range(10)))')"
case "$WEB_ADMIN" in on|yes|1) WEB_ADMIN=1 ;; off|no|0) WEB_ADMIN=0 ;; *) WEB_ADMIN="$(old GHOSTLAB_WEB_ADMIN)"; [ -n "$WEB_ADMIN" ] || WEB_ADMIN="$CLOUD" ;; esac

say "Writing settings to $ETC"
install -d -m 750 -o root -g "$RUN_USER" "$ETC"
cat > "$ENV_FILE" <<EOF
# Ghost Lab settings - written by server/install.sh
GHOSTLAB_DRIVERS=$DRIVERS
GHOSTLAB_PUBLIC=$PUBLIC
GHOSTLAB_HTTPS=${HTTPS:-0}
GHOSTLAB_CLOUD=$CLOUD
GHOSTLAB_PAGES=$PAGES
GHOSTLAB_HTTP_PORT=$HTTP_PORT
GHOSTLAB_FPS=$FPS
GHOSTLAB_ADMIN_KEY=$ADMIN
GHOSTLAB_WEB_ADMIN=$WEB_ADMIN
EOF
chown root:"$RUN_USER" "$ENV_FILE"; chmod 640 "$ENV_FILE"
install -d -o "$RUN_USER" "$LAPS_DIR"
chmod +x "$APP_DIR/server/ghostlab.py"
ln -sf "$APP_DIR/server/ghostlab.py" /usr/local/bin/ghostlab

if [ ! -s "$DRIVERS" ]; then
  printf '{"drivers": []}\n' > "$DRIVERS"
  chown root:"$RUN_USER" "$DRIVERS"; chmod 640 "$DRIVERS"
fi
if [ -n "$DRIVER_LIST" ]; then
  say "Setting up drivers: $(printf '%s' "$DRIVER_LIST" | sed 's/:[^,]*//g; s/,/, /g')"
  GHOSTLAB_NO_RESTART=1 /usr/local/bin/ghostlab set-drivers "$DRIVER_LIST" >/dev/null
elif ! grep -q '"name"' "$DRIVERS"; then
  say "Creating the first driver: $DRIVER"
  GHOSTLAB_NO_RESTART=1 /usr/local/bin/ghostlab add "$DRIVER" >/dev/null
fi
if [ "$WEB_ADMIN" = 1 ]; then                       # the service writes drivers.json when you add people online
  chmod 770 "$ETC"; chmod 660 "$DRIVERS"
fi

# ---- DuckDNS: keeps your name pointing at this server's IP ------------------------
if [ -n "$DOMAIN" ] && [ -n "$TOKEN" ]; then
  [[ "$DOMAIN" == *.duckdns.org ]] || fail "--duckdns-token only works with a .duckdns.org name"
  say "Setting up DuckDNS for $DOMAIN"
  umask 077
  printf 'DUCKDNS_DOMAIN=%s\nDUCKDNS_TOKEN=%s\n' "${DOMAIN%.duckdns.org}" "$TOKEN" > $ETC/duckdns.env
  umask 022
  cat > /etc/systemd/system/duckdns.service <<'EOF'
[Unit]
Description=Update DuckDNS with this server's public IP
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
EnvironmentFile=/etc/ghostlab/duckdns.env
ExecStart=/usr/bin/curl -fsS --max-time 20 "https://www.duckdns.org/update?domains=${DUCKDNS_DOMAIN}&token=${DUCKDNS_TOKEN}&ip="
EOF
  cat > /etc/systemd/system/duckdns.timer <<'EOF'
[Unit]
Description=Update DuckDNS every 5 minutes
[Timer]
OnBootSec=1min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now duckdns.timer >/dev/null 2>&1
  REPLY_DDNS="$(curl -fsS --max-time 20 "https://www.duckdns.org/update?domains=${DOMAIN%.duckdns.org}&token=$TOKEN&ip=" 2>/dev/null || true)"
  [ "$REPLY_DDNS" = "OK" ] || warn "DuckDNS said '${REPLY_DDNS:-no reply}'. Check the name and token on duckdns.org."
elif [ -n "$DOMAIN" ] && [ ! -f $ETC/duckdns.env ]; then
  warn "No --duckdns-token given: make sure $DOMAIN already points at your public IP."
fi

# ---- HTTPS with Caddy (free certificate, renews itself) ---------------------
if [ "${HTTPS:-0}" = 1 ] && [ -n "$PUBLIC" ]; then
  if ! command -v caddy >/dev/null; then
    say "Installing Caddy for HTTPS"
    command -v apt-get >/dev/null && apt-get update -qq
    if ! apt_install caddy 2>/dev/null; then
      case "$(uname -m)" in
        aarch64|arm64) ARCH="arch=arm64" ;; x86_64) ARCH="arch=amd64" ;; *) fail "no Caddy build for $(uname -m)" ;;
      esac
      say "Downloading Caddy from caddyserver.com"
      curl -fsSL --max-time 300 -o /usr/local/bin/caddy "https://caddyserver.com/api/download?os=linux&$ARCH" || fail "couldn't download Caddy"
      chmod 755 /usr/local/bin/caddy
      getent group caddy >/dev/null || groupadd --system caddy
      id caddy >/dev/null 2>&1 || useradd --system --gid caddy --create-home --home-dir /var/lib/caddy --shell /usr/sbin/nologin caddy
      cat > /etc/systemd/system/caddy.service <<'UNIT'
[Unit]
Description=Caddy
After=network-online.target
Wants=network-online.target
[Service]
User=caddy
Group=caddy
ExecStart=/usr/local/bin/caddy run --environ --config /etc/caddy/Caddyfile
ExecReload=/usr/local/bin/caddy reload --config /etc/caddy/Caddyfile --force
TimeoutStopSec=5s
LimitNOFILE=1048576
AmbientCapabilities=CAP_NET_BIND_SERVICE
[Install]
WantedBy=multi-user.target
UNIT
      systemctl daemon-reload
    fi
  fi
  if [ -f /etc/caddy/Caddyfile ] && ! grep -q "Ghost Lab" /etc/caddy/Caddyfile; then
    cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.before-ghostlab
  fi
  mkdir -p /etc/caddy
  cat > /etc/caddy/Caddyfile <<EOF
# Ghost Lab - HTTPS for the app and live data
$PUBLIC {
	encode gzip
	reverse_proxy 127.0.0.1:$HTTP_PORT
}
EOF
  systemctl enable caddy >/dev/null 2>&1 || true
  systemctl restart caddy
fi

# ---- the Ghost Lab service ----------------------------------------------------
say "Installing the ghostlab service"
cat > /etc/systemd/system/ghostlab.service <<EOF
[Unit]
Description=Ghost Lab telemetry bridge
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
EnvironmentFile=$ENV_FILE
ExecStart=$(command -v python3) -u $APP_DIR/bridge.py --no-browser --http-port $HTTP_PORT --laps $LAPS_DIR
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable ghostlab >/dev/null 2>&1
systemctl restart ghostlab

# ---- daily update from your GitHub repo (cloud servers have nobody logging in) ----
if [ "$CLOUD" = 1 ] && [ -d "$APP_DIR/.git" ]; then
  say "Turning on daily updates from GitHub"
  cat > /etc/systemd/system/ghostlab-update.service <<'UNIT'
[Unit]
Description=Ghost Lab: update from GitHub (restarts only if something changed)
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/bin/ghostlab update
UNIT
  cat > /etc/systemd/system/ghostlab-update.timer <<'UNIT'
[Unit]
Description=Ghost Lab: check GitHub for updates once a day
[Timer]
OnBootSec=3min
OnCalendar=*-*-* 04:00:00
RandomizedDelaySec=1h
Persistent=true
[Install]
WantedBy=timers.target
UNIT
  systemctl daemon-reload
  systemctl enable --now ghostlab-update.timer >/dev/null 2>&1 || true
fi

# ---- this machine's firewall ----------------------------------------------------
say "Opening ports on this machine"
TCP_PORTS="$HTTP_PORT"; [ "${HTTPS:-0}" = 1 ] && TCP_PORTS="$HTTP_PORT 80 443"
if command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
  for p in $TCP_PORTS; do firewall-cmd -q --permanent --add-port="$p/tcp"; done
  firewall-cmd -q --permanent --add-port="${UDP_RANGE/:/-}/udp"; firewall-cmd -q --reload
fi
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  for p in $TCP_PORTS; do ufw allow "$p/tcp" >/dev/null; done
  ufw allow "$UDP_RANGE/udp" >/dev/null
fi
if command -v iptables >/dev/null && iptables -S INPUT 2>/dev/null | grep -q -- "-j REJECT"; then
  for p in $TCP_PORTS; do iptables -C INPUT -p tcp --dport "$p" -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport "$p" -j ACCEPT; done
  iptables -C INPUT -p udp --dport "$UDP_RANGE" -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p udp --dport "$UDP_RANGE" -j ACCEPT
  if command -v netfilter-persistent >/dev/null; then netfilter-persistent save >/dev/null 2>&1 || true
  elif [ -d /etc/iptables ]; then iptables-save > /etc/iptables/rules.v4; fi
fi

# ---- check ---------------------------------------------------------------------
sleep 2
systemctl is-active --quiet ghostlab || { journalctl -u ghostlab -n 20 --no-pager || true; fail "the service didn't start (log above)"; }
curl -fsS --max-time 5 "http://127.0.0.1:$HTTP_PORT/" >/dev/null || fail "the service is running but not answering on port $HTTP_PORT"

echo
echo "  ┌──────────────────────────────────────────────────────────────┐"
echo "     Ghost Lab is running"
echo "  └──────────────────────────────────────────────────────────────┘"
echo
ghostlab list | sed 's/^/  /'
echo
cat <<EOF
  Also open these in your cloud provider's firewall (Oracle: the subnet's Security List):
    UDP $(echo $UDP_RANGE | tr : -)   and   TCP $([ "${HTTPS:-0}" = 1 ] && echo "80, 443" || echo "$HTTP_PORT")

  Handy commands (only if you ever log in):
    sudo ghostlab add Sam      add a driver (prints their link and console settings)
    sudo ghostlab show Sam     show someone's details again
    sudo ghostlab status       is everything running?
    sudo ghostlab update       get the latest Ghost Lab from GitHub

EOF
