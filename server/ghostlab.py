#!/usr/bin/env python3
"""
ghostlab - manage the drivers on your Ghost Lab server.

  sudo ghostlab list             everyone's port, access code and links
  sudo ghostlab add NAME         add a driver (gets the next free UDP port)
  sudo ghostlab show NAME        a ready-to-send message with NAME's link and console settings
  sudo ghostlab code NAME        give NAME a new access code (their old link stops working)
  sudo ghostlab remove NAME      remove a driver (their saved laps are kept)
  sudo ghostlab pages URL        your GitHub Pages address, so links open the app from there
  sudo ghostlab set-drivers "Alex:code,Sam"   replace the list (keeps ports and codes of people kept)
  sudo ghostlab status           is everything running?
  sudo ghostlab update           download the latest Ghost Lab and restart

Settings live in /etc/ghostlab, never in the git repository.
"""
import json
import os
import re
import secrets
import socket
import subprocess
import sys

ENV_FILE = os.environ.get("GHOSTLAB_ENV", "/etc/ghostlab/ghostlab.env")
PORT_FIRST, PORT_LAST = 20777, 20799
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"        # no 0/o/1/l mix-ups when typing a code
APP_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def die(msg):
    print(f"ghostlab: {msg}", file=sys.stderr)
    sys.exit(1)


def read_env():
    env = {}
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"')
    except FileNotFoundError:
        die(f"{ENV_FILE} not found - run the installer first:  sudo bash server/install.sh")
    except PermissionError:
        die("permission denied - run it with sudo, e.g.  sudo ghostlab list")
    return env


def write_env_value(key, value):
    with open(ENV_FILE, encoding="utf-8") as f:
        lines = [l for l in f.read().splitlines() if not l.startswith(key + "=")]
    if value:
        lines.append(f"{key}={value}")
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def drivers_path(env):
    return env.get("GHOSTLAB_DRIVERS", "/etc/ghostlab/drivers.json")


RETIRED = []


def load(env):
    path = drivers_path(env)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        RETIRED[:] = data.get("retired", [])
        return data.get("drivers", [])
    except FileNotFoundError:
        return []
    except PermissionError:
        die("permission denied - run it with sudo")
    except ValueError as e:
        die(f"{path} is damaged: {e}")


def save(env, drivers):
    path = drivers_path(env)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"drivers": drivers, "retired": sorted(set(RETIRED) - {d["port"] for d in drivers})}, f, indent=2)
        f.write("\n")
    try:                                   # keep the original owner/group so the service can read it
        st = os.stat(path)
        os.chown(tmp, st.st_uid, st.st_gid)
        os.chmod(tmp, st.st_mode & 0o777)
    except (FileNotFoundError, PermissionError):
        os.chmod(tmp, 0o640)
    os.replace(tmp, path)


def restart():
    if os.environ.get("GHOSTLAB_NO_RESTART"):
        return
    r = subprocess.run(["systemctl", "restart", "ghostlab"], capture_output=True, text=True)
    if r.returncode:
        print(f"(couldn't restart the service: {r.stderr.strip() or r.returncode})")


def find(drivers, name):
    for d in drivers:
        if d["name"].lower() == name.lower():
            return d
    die(f"no driver called {name!r}. Drivers: {', '.join(d['name'] for d in drivers) or 'none yet'}")


def new_key(drivers):
    taken = {d["key"] for d in drivers}
    while True:
        k = "".join(secrets.choice(ALPHABET) for _ in range(8))
        if k not in taken:
            return k


def lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def resolve(host):
    try:
        return socket.gethostbyname(host) if host else None
    except OSError:
        return None


def links(env, d):
    """(link to send, link on the local network)"""
    key, port = d["key"], env.get("GHOSTLAB_HTTP_PORT", "8000")
    public, https = env.get("GHOSTLAB_PUBLIC", ""), env.get("GHOSTLAB_HTTPS") == "1"
    pages = env.get("GHOSTLAB_PAGES", "").rstrip("/")
    cloud = env.get("GHOSTLAB_CLOUD") == "1"
    if public and https and pages:
        main = f"{pages}/?server={public}&key={key}"
    elif public and https:
        main = f"https://{public}/?key={key}"
    elif public:
        main = f"http://{public}:{port}/?key={key}"
    else:
        main = None
    home = None if cloud else f"http://{lan_ip()}:{port}/?key={key}"
    return main or home, (home if main else None)


def card(env, d):
    send, home = links(env, d)
    public = env.get("GHOSTLAB_PUBLIC", "")
    cloud = env.get("GHOSTLAB_CLOUD") == "1"
    away_ip = resolve(public) or (f"{public}  (look up its number at duckdns.org)" if public else "")
    lines = [f"Ghost Lab - {d['name']}",
             f"  Open on your phone:  {send}",
             f"  Access code:         {d['key']}"]
    if home:
        lines.append(f"  On the local network:   {home}")
    lines += ["",
              "  Console settings (Settings > Telemetry Settings > UDP Telemetry Settings):",
              f"    UDP Telemetry On, Broadcast Mode Off, Send Rate {'30' if int(env.get('GHOSTLAB_FPS', '30') or 30) <= 15 else '60'} Hz, UDP Format 2026",
              f"    UDP Port        {d['port']}"]
    if cloud:
        lines.append(f"    UDP IP Address  {away_ip}")
    elif public:
        lines.append(f"    UDP IP Address  {away_ip}   (on the same network as the server: {lan_ip()})")
    else:
        lines.append(f"    UDP IP Address  {lan_ip()}   (consoles on this network only)")
    return "\n".join(lines)


def need_root():
    if os.geteuid() != 0 and not os.environ.get("GHOSTLAB_ENV"):
        die("this changes settings, so run it with sudo, e.g.  sudo ghostlab add Sam")


def valid_name(name):
    name = name.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.'-]{0,23}", name):
        die("use a short name with letters and numbers, e.g.  sudo ghostlab add Sam")
    return name


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return
    cmd, args = argv[0], argv[1:]
    env = read_env()
    drivers = load(env)

    if cmd in ("list", "ls"):
        if not drivers:
            print("No drivers yet. Add one with:  sudo ghostlab add NAME")
            return
        for d in drivers:
            send, home = links(env, d)
            print(f"{d['name']:<14} UDP {d['port']}   code {d['key']}")
            print(f"{'':<14} {send}")
            if home:
                print(f"{'':<14} {home}  (local network)")
        admin = env.get("GHOSTLAB_ADMIN_KEY", "")
        if admin:
            send, _ = links(env, {"key": admin, "port": 0})
            print(f"\n{'ADMIN':<14} code {admin}   (sees every driver" + (", can add/remove drivers on the website)" if env.get("GHOSTLAB_WEB_ADMIN") == "1" else ")"))
            print(f"{'':<14} {send}")
        print("\nSend someone their details with:  sudo ghostlab show NAME")

    elif cmd == "add":
        need_root()
        if not args:
            die("who? e.g.  sudo ghostlab add Sam")
        name = valid_name(" ".join(a for a in args if not a.startswith("--port")))
        if any(d["name"].lower() == name.lower() for d in drivers):
            die(f"{name} already exists - see them with:  sudo ghostlab show {name}")
        used = {d["port"] for d in drivers}
        free = [p for p in range(PORT_FIRST, PORT_LAST + 1) if p not in used]
        port = next((p for p in free if p not in RETIRED), free[0] if free else None)   # old ports last
        for a in args:
            if a.startswith("--port="):
                port = int(a.split("=", 1)[1])
        if port is None:
            die(f"all ports {PORT_FIRST}-{PORT_LAST} are taken")
        if port in used:
            die(f"port {port} is already used")
        d = {"name": name, "port": port, "key": new_key(drivers)}
        drivers.append(d)
        save(env, drivers)
        restart()
        print(f"Added {name}.\n")
        print(card(env, d))
        if not (PORT_FIRST <= port <= PORT_LAST):
            print(f"\nRemember to forward UDP {port} on your router too.")

    elif cmd == "set-drivers":
        need_root()
        if not args:
            die('e.g.  sudo ghostlab set-drivers "Alex:alexcode1,Sam,Jo"')
        wanted = []
        for item in " ".join(args).split(","):
            if not item.strip():
                continue
            name, _, code = item.partition(":")
            name, code = valid_name(name), code.strip()
            if code and not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", code):
                die(f"code for {name}: 4-64 letters and numbers")
            wanted.append((name, code))
        keep = {n.lower() for n, _ in wanted}
        old = {d["name"].lower(): d for d in drivers}
        RETIRED.extend(d["port"] for d in drivers if d["name"].lower() not in keep)
        result = []
        for name, code in wanted:
            d = old.get(name.lower())
            if d is None:
                used = {x["port"] for x in result} | {x["port"] for x in drivers if x["name"].lower() in keep}
                free = [p for p in range(PORT_FIRST, PORT_LAST + 1) if p not in used]
                if not free:
                    die("all ports are taken")
                d = {"name": name, "port": next((p for p in free if p not in RETIRED), free[0]), "key": ""}
            d = dict(d, name=name)
            if code:
                d["key"] = code
            if not d["key"] or any(x["key"].lower() == d["key"].lower() for x in result):
                d["key"] = new_key(result + drivers)
            result.append(d)
        if not result:
            die("give at least one driver")
        save(env, result)
        restart()
        for d in result:
            print(f"{d['name']:<14} UDP {d['port']}   code {d['key']}")

    elif cmd in ("show", "card"):
        if not args:
            die("whose details? e.g.  sudo ghostlab show Sam")
        print(card(env, find(drivers, " ".join(args))))

    elif cmd in ("code", "newcode"):
        need_root()
        if not args:
            die("whose code? e.g.  sudo ghostlab code Sam")
        d = find(drivers, " ".join(args))
        d["key"] = new_key(drivers)
        save(env, drivers)
        restart()
        print(f"New access code for {d['name']}. Their old link no longer works.\n")
        print(card(env, d))

    elif cmd in ("remove", "rm", "delete"):
        need_root()
        if not args:
            die("who? e.g.  sudo ghostlab remove Sam")
        d = find(drivers, " ".join(args))
        if len(drivers) == 1:
            die("can't remove the last driver - add someone else first")
        drivers.remove(d)
        RETIRED.append(d["port"])
        save(env, drivers)
        restart()
        print(f"Removed {d['name']}. Their saved laps are still on the server.")

    elif cmd == "pages":
        need_root()
        if not args:
            print(env.get("GHOSTLAB_PAGES") or "Not set. e.g.  sudo ghostlab pages https://you.github.io/ghost-lab/")
            return
        url = "" if args[0] in ("off", "none", "-") else args[0].rstrip("/") + "/"
        if url and not url.startswith("https://"):
            die("use your full GitHub Pages address, starting with https://")
        write_env_value("GHOSTLAB_PAGES", url)
        print(f"Links now open the app from {url}" if url else "Links now open the app from this server")
        if url and env.get("GHOSTLAB_HTTPS") != "1":
            print("Note: the GitHub Pages app can only reach this server once HTTPS is set up (install.sh --domain).")

    elif cmd == "status":
        for unit in ("ghostlab", "caddy", "duckdns.timer", "ghostlab-update.timer"):
            r = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True)
            state = r.stdout.strip() or "not installed"
            enabled = subprocess.run(["systemctl", "is-enabled", unit], capture_output=True, text=True).stdout.strip()
            if unit == "ghostlab":
                state += "  (starts on boot)" if enabled == "enabled" else "  (NOT set to start on boot: run the installer again)"
            if unit == "ghostlab" or state != "inactive" or enabled == "enabled":
                print(f"{unit:<15} {state}")
        public = env.get("GHOSTLAB_PUBLIC", "")
        print(f"{'drivers':<15} {len(drivers)}")
        print(f"{'home address':<15} {lan_ip()}")
        if public:
            print(f"{'public address':<15} {public} -> {resolve(public) or 'not resolving yet'}")

    elif cmd == "restart":
        need_root()
        restart()
        print("Restarted.")

    elif cmd == "update":
        need_root()
        owner = os.stat(APP_DIR).st_uid
        as_owner = {"user": owner} if os.geteuid() == 0 else {}
        head = lambda: subprocess.run(["git", "-C", APP_DIR, "rev-parse", "HEAD"], capture_output=True, text=True, **as_owner).stdout.strip()
        before = head()
        r = subprocess.run(["git", "-C", APP_DIR, "pull", "--ff-only", "--quiet"], **as_owner)
        if r.returncode:
            die("git pull failed - see above")
        if head() == before:
            print("Already up to date.")
            return
        restart()                                   # only restart when something actually changed
        print("Updated and restarted.")

    else:
        die(f"unknown command {cmd!r}. Try:  ghostlab help")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except BrokenPipeError:            # e.g. piped into head
        pass
    except KeyboardInterrupt:
        sys.exit(130)
