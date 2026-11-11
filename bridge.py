#!/usr/bin/env python3
"""
Ghost Lab bridge - F1 26 / F1 25 UDP telemetry for your phone and browser.

The game sends telemetry as UDP packets, which browsers can't receive. This
small server runs on a PC on the same network as your PC/console and phone:

  * receives the game's packets on UDP 20777
  * picks out you, your personal-best ghost and the rival ghost
  * saves every complete lap as a CSV in ./laps
  * serves the Ghost Lab web app and streams live data to it (one port, 8000)

Standard library only - nothing to pip install. Python 3.9+.

    python bridge.py              # listen for the game

Then open the address it prints - on your phone, use the "On your phone" one.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import socket
import struct
import sys
import time
import urllib.parse
import webbrowser

VERSION = "2.2"
HERE = os.path.dirname(os.path.abspath(__file__))

# --------------------------------------------------------------------------
# Packet layouts (F1 23 / 24 / 25 / 26 share the 29-byte header)
# --------------------------------------------------------------------------
NUM_CARS = 24                             # room for the largest grid (2026 format)
HEADER = struct.Struct("<HBBBBBQfIIBB")   # packetFormat ... secondaryPlayerCarIndex
MOTION = struct.Struct("<6f6h6f")         # CarMotionData up to 2025, 60 bytes
TELEM_HEAD = struct.Struct("<H3fBbHB")    # first 19 bytes of CarTelemetryData (all formats)
TT_SET = struct.Struct("<BBIIIIBBBBBB")   # TimeTrialDataSet up to 2025, 24 bytes


class Layout:
    """Per-format differences in the game's packets."""
    def __init__(self, cars, motion, g_scale, tt, name_off, sector_off):
        self.cars, self.motion, self.g_scale, self.tt = cars, motion, g_scale, tt
        self.name_off = name_off          # where the name starts in ParticipantData
        self.sector_off = sector_off      # sector 2/3 start distances in the Session packet (None = last 8 bytes)


LAYOUT_OLD = Layout(22, MOTION, 1.0, TT_SET, 7, None)
# F1 26 / F1 25 2026 Season Pack (format 2026): 24 cars, quantised g-forces,
# 16-bit driver/team ids, and the session packet grew past the sector distances
LAYOUT_2026 = Layout(24, struct.Struct("<6f9h3f"), 1 / 1000, struct.Struct("<BHIIIIBBBBBB"), 10, 745)


def layout_for(fmt):
    return LAYOUT_2026 if fmt >= 2026 else LAYOUT_OLD

PKT_MOTION, PKT_SESSION, PKT_LAP = 0, 1, 2
PKT_PARTICIPANTS, PKT_TELEMETRY, PKT_TIMETRIAL = 4, 6, 14
USED_PACKETS = frozenset((PKT_MOTION, PKT_SESSION, PKT_LAP, PKT_PARTICIPANTS, PKT_TELEMETRY, PKT_TIMETRIAL))

FIELDS = ["t", "d", "x", "y", "z", "speed", "throttle", "brake", "steer",
          "gear", "rpm", "drs", "fx", "fy", "fz"]
LIVE_KEYS = ["x", "y", "z", "fx", "fy", "fz", "speed", "throttle", "brake",
             "steer", "gear", "rpm", "drs", "d", "lapNum", "sector", "invalid",
             "curLap", "lastLap", "s1", "s2", "gLat", "gLon"]
ROLES = ("player", "pb", "rival")
ROLE_LABEL = {"player": "You", "pb": "Personal best", "rival": "Rival"}

TRACKS = {
    0: "Melbourne", 1: "Paul Ricard", 2: "Shanghai", 3: "Sakhir", 4: "Catalunya",
    5: "Monaco", 6: "Montreal", 7: "Silverstone", 8: "Hockenheim", 9: "Hungaroring",
    10: "Spa", 11: "Monza", 12: "Singapore", 13: "Suzuka", 14: "Abu Dhabi",
    15: "Texas", 16: "Brazil", 17: "Austria", 18: "Sochi", 19: "Mexico", 20: "Baku",
    21: "Sakhir Short", 22: "Silverstone Short", 23: "Texas Short", 24: "Suzuka Short",
    25: "Hanoi", 26: "Zandvoort", 27: "Imola", 28: "Portimao", 29: "Jeddah",
    30: "Miami", 31: "Las Vegas", 32: "Losail", 39: "Silverstone (Reverse)",
    40: "Austria (Reverse)", 41: "Zandvoort (Reverse)",
}
SESSIONS_24 = {0: "Unknown", 1: "Practice 1", 2: "Practice 2", 3: "Practice 3", 4: "Short Practice",
               5: "Qualifying 1", 6: "Qualifying 2", 7: "Qualifying 3", 8: "Short Qualifying",
               9: "One-Shot Qualifying", 10: "Sprint Shootout 1", 11: "Sprint Shootout 2",
               12: "Sprint Shootout 3", 13: "Short Sprint Shootout", 14: "One-Shot Sprint Shootout",
               15: "Race", 16: "Race 2", 17: "Race 3", 18: "Time Trial"}
SESSIONS_23 = {0: "Unknown", 1: "Practice 1", 2: "Practice 2", 3: "Practice 3", 4: "Short Practice",
               5: "Qualifying 1", 6: "Qualifying 2", 7: "Qualifying 3", 8: "Short Qualifying",
               9: "One-Shot Qualifying", 10: "Race", 11: "Race 2", 12: "Race 3", 13: "Time Trial"}
GAMES = {2023: "F1 23", 2024: "F1 24", 2025: "F1 25", 2026: "F1 26"}


def track_name(tid):
    if tid is None or tid < 0:
        return ""
    return TRACKS.get(tid, f"Track {tid}")


def game_name(fmt):
    return GAMES.get(fmt, f"F1 ({fmt})")


def fmt_lap(ms):
    m, s = divmod(ms / 1000, 60)
    return f"{int(m)}:{s:06.3f}"


# --------------------------------------------------------------------------
# Telemetry state
# --------------------------------------------------------------------------
class State:
    def __init__(self, laps_dir: str, info: dict, max_laps: int = 60):
        self.laps_dir = laps_dir
        self.info = info
        self.max_laps = max_laps
        os.makedirs(laps_dir, exist_ok=True)
        self.session_uid = None
        self.packet_format = None
        self.player_idx = 0
        self.pb_idx = 255
        self.rival_idx = 255
        self.cars = [dict() for _ in range(NUM_CARS)]
        self.session: dict = {}
        self.tt = None
        self.names = [""] * NUM_CARS
        self.laps: list = []
        self.lap_seq = 0
        self.outbox: list = []
        self.frame_dirty = False
        # detection / health
        self.packets = 0
        self.sources: dict = {}
        self.last_packet = None
        self.ignored = 0
        self.ignored_format = None
        self.active_ip = None          # only one game feeds the bridge at a time
        self.active_last = 0.0
        self.blocked = 0
        self.blocked_last = 0.0
        self.rate = 0
        self._rate_count = 0
        self._rate_t = time.time()
        self._reset_roles()

    def _reset_roles(self):
        self.buf = {r: [] for r in ROLES}
        self.prev = {r: None for r in ROLES}
        self.role_idx = {r: None for r in ROLES}
        self.lap_invalid = {r: False for r in ROLES}
        self.lap_start = {r: None for r in ROLES}
        self.prev_sector = None

    def reset_session(self, uid):
        self.session_uid = uid
        self.cars = [dict() for _ in range(NUM_CARS)]
        self.session = {}
        self.tt = None
        self.laps = []
        self.pb_idx = self.rival_idx = 255
        self._reset_roles()
        self.outbox.append({"type": "reset"})

    def role_index(self, role):
        idx = {"player": self.player_idx, "pb": self.pb_idx, "rival": self.rival_idx}[role]
        return idx if idx < NUM_CARS else None

    # ---- entry point ------------------------------------------------------
    def handle(self, data: bytes, ip: str = "?"):
        if len(data) < HEADER.size:
            return
        fmt = data[0] | (data[1] << 8)                  # cheap peek: format and packet type
        now = time.time()
        src = self.sources.get(ip)
        if src is None:
            src = self.sources[ip] = {"packets": 0}
        src["format"], src["last"], src["packets"] = fmt, now, src["packets"] + 1
        self.last_packet = now
        self._rate_count += 1
        if fmt < 2023 or fmt > 2100:
            self.ignored += 1
            self.ignored_format = fmt
            return
        if data[6] not in USED_PACKETS:                 # about half the game's packets: skip early
            return
        (fmt, _yr, _maj, _min, _pv, pid, uid, stime, _fr, _ofr, pidx, _sidx) = HEADER.unpack_from(data, 0)
        if self.active_ip and ip != self.active_ip and now - self.active_last < 10:
            self.blocked += 1                           # a second game is sending: ignore it
            self.blocked_last = now
            return
        if ip != self.active_ip:
            print(f"  telemetry from {ip}")
        self.active_ip, self.active_last = ip, now
        self.packets += 1
        self.packet_format = fmt
        L = layout_for(fmt)
        if uid and uid != self.session_uid:
            self.reset_session(uid)
        if pidx < L.cars:
            self.player_idx = pidx
        b = HEADER.size
        try:
            if pid == PKT_MOTION:
                self._motion(data, b, L)
            elif pid == PKT_LAP:
                self._lap(data, b, fmt, L)
            elif pid == PKT_TELEMETRY:
                self._telemetry(data, b, stime, L)
            elif pid == PKT_SESSION:
                self._session(data, b, fmt, L)
            elif pid == PKT_PARTICIPANTS:
                self._participants(data, b, L)
            elif pid == PKT_TIMETRIAL:
                self._timetrial(data, b, L)
        except struct.error:
            pass  # truncated or unexpected layout - skip this packet

    # ---- packet parsers ---------------------------------------------------
    def wanted(self):
        """Only you and the two ghosts are ever shown, so skip decoding the other cars."""
        return {i for i in (self.player_idx, self.pb_idx, self.rival_idx) if i < NUM_CARS}

    def _motion(self, data, b, L):
        m = L.motion
        if len(data) < b + m.size * L.cars:
            return
        for i in self.wanted():
            if i >= L.cars:
                continue
            v = m.unpack_from(data, b + i * m.size)
            c = self.cars[i]
            c["x"], c["y"], c["z"] = v[0], v[1], v[2]
            c["fx"], c["fy"], c["fz"] = v[6] / 32767, v[7] / 32767, v[8] / 32767
            c["gLat"], c["gLon"] = v[12] * L.g_scale, v[13] * L.g_scale

    def _lap(self, data, b, fmt, L):
        stride = (len(data) - b - 2) // L.cars
        if stride < 36:
            return
        dist_off, pos_off = (20, 32) if fmt >= 2024 else (18, 30)   # F1 23 lacks two minute bytes
        self.pb_idx, self.rival_idx = data[-2], data[-1]
        for i in self.wanted():
            if i >= L.cars:
                continue
            o = b + i * stride
            last, cur = struct.unpack_from("<II", data, o)
            s1ms, s1m, s2ms, s2m = struct.unpack_from("<HBHB", data, o + 8)
            dist, = struct.unpack_from("<f", data, o + dist_off)
            _pos, lapnum, _pit, _npit, sector, invalid = struct.unpack_from("<6B", data, o + pos_off)
            c = self.cars[i]
            if i == self.player_idx:
                c["curLap"] = cur
            c.update(lastLap=last, gameLap=cur, s1=s1m * 60000 + s1ms,
                                s2=s2m * 60000 + s2ms, d=dist, lapNum=lapnum,
                                sector=sector, invalid=invalid)

    def _telemetry(self, data, b, stime, L):
        stride = (len(data) - b - 3) // L.cars
        if stride < TELEM_HEAD.size:
            return
        for i in self.wanted():
            if i >= L.cars:
                continue
            sp, thr, steer, brk, _cl, gear, rpm, drs = TELEM_HEAD.unpack_from(data, b + i * stride)
            self.cars[i].update(speed=sp, throttle=thr, steer=steer, brake=brk,
                                gear=gear, rpm=rpm, drs=drs)
        self._sample(stime)

    def _session(self, data, b, fmt, L):
        _w, _tt, _at, _tl, tlen, stype, tid = struct.unpack_from("<BbbBHBb", data, b)
        s2, s3 = struct.unpack_from("<ff", data, L.sector_off if L.sector_off else len(data) - 8)
        ok = 0 < s2 < s3 < tlen
        names = SESSIONS_24 if fmt >= 2024 else SESSIONS_23
        new = {"trackId": tid, "trackName": track_name(tid), "trackLength": tlen,
               "sessionType": stype, "sessionName": names.get(stype, f"Session {stype}"),
               "timeTrial": stype == (18 if fmt >= 2024 else 13),
               "sector2": round(s2, 1) if ok else self.session.get("sector2"),
               "sector3": round(s3, 1) if ok else self.session.get("sector3"),
               "packetFormat": fmt}
        if new != self.session:
            self.session = new
            self.outbox.append({"type": "session", "session": new})

    def _participants(self, data, b, L):
        stride = (len(data) - b - 1) // L.cars
        n0 = L.name_off
        if stride < n0 + 32:
            return
        for i in range(L.cars):
            o = b + 1 + i * stride
            self.names[i] = data[o + n0:o + n0 + 32].split(b"\0")[0].decode("utf-8", "replace")

    def _timetrial(self, data, b, L):
        keys = ("carIdx", "teamId", "lapTime", "s1", "s2", "s3", "tractionControl",
                "gearbox", "abs", "equalPerformance", "customSetup", "valid")
        sets = [dict(zip(keys, L.tt.unpack_from(data, b + k * L.tt.size))) for k in range(3)]
        tt = {"sessionBest": sets[0], "personalBest": sets[1], "rival": sets[2]}
        if tt != self.tt:
            self.tt = tt
            self.outbox.append({"type": "tt", "tt": tt})

    # ---- lap recording ----------------------------------------------------
    def _sample(self, stime):
        L = self.session.get("trackLength") or 0
        for role in ROLES:
            idx = self.role_index(role)
            if idx != self.role_idx[role]:          # ghost swapped or vanished
                self.role_idx[role] = idx
                self.buf[role] = []
                self.prev[role] = None
                self.lap_invalid[role] = False
                self.lap_start[role] = None
            if idx is None:
                continue
            c = self.cars[idx]
            if "x" not in c or "d" not in c or "speed" not in c:
                continue
            d, lapnum = c["d"], c["lapNum"]
            prev = self.prev[role]
            if prev is not None:
                back = prev[1] - d
                if lapnum != prev[0] or back > max(L * 0.5, 500):
                    self._finish_lap(role, idx)
                elif back > 20:                        # flashback / restart: lap is broken
                    self.buf[role] = []
                    self.lap_start[role] = None
            self.prev[role] = (lapnum, d)
            if role == "player":
                self._learn_sectors(c)
            if d < 0:
                continue                                # still before the line
            if self.lap_start[role] is None:              # crossed the line: back-date by the distance past it
                self.lap_start[role] = stime - d / max(c["speed"] / 3.6, 1)
            clock = round((stime - self.lap_start[role]) * 1000)
            if role == "player":
                t = c.get("curLap") or clock
            else:                                         # the game's timer for ghost cars isn't reliable in 2026
                t = clock
                c["curLap"] = t
            self.buf[role].append([
                int(t), round(d, 2), round(c["x"], 2), round(c["y"], 2), round(c["z"], 2),
                c["speed"], round(c["throttle"], 3), round(c["brake"], 3), round(c["steer"], 3),
                c["gear"], c["rpm"], c["drs"],
                round(c["fx"], 3), round(c["fy"], 3), round(c["fz"], 3)])
            if c.get("invalid"):
                self.lap_invalid[role] = True
        self.frame_dirty = True

    def _learn_sectors(self, c):
        """Fallback for formats whose session packet has no sector distances."""
        sec = c.get("sector")
        if self.prev_sector is not None and sec != self.prev_sector and c["d"] > 0:
            key = {1: "sector2", 2: "sector3"}.get(sec)
            if key and not self.session.get(key):
                self.session = dict(self.session, **{key: round(c["d"], 1)})
                self.outbox.append({"type": "session", "session": self.session})
        self.prev_sector = sec

    def _finish_lap(self, role, idx):
        buf, invalid = self.buf[role], self.lap_invalid[role]
        self.buf[role], self.lap_invalid[role], self.lap_start[role] = [], False, None
        if len(buf) < 120:
            return
        L = self.session.get("trackLength") or buf[-1][1]
        if buf[0][1] > 150 or buf[-1][1] < L - 150:
            return                                      # did not cover the whole lap
        last = buf[-1]
        est = last[0] + (L - last[1]) / max(last[5] / 3.6, 1) * 1000
        exact = None
        if role == "player":
            exact = self.cars[idx].get("lastLap")
        elif self.tt:
            exact = self.tt["personalBest" if role == "pb" else "rival"]["lapTime"]
        lap_time = int(exact) if exact and abs(exact - est) < 400 else int(est)
        if role != "player":                            # ghosts replay the same lap every time
            prev = next((l for l in reversed(self.laps) if l["role"] == role), None)
            if prev and abs(prev["lapTime"] - lap_time) <= 5:
                return
        self.lap_seq += 1
        lap = {"id": self.lap_seq, "role": role,
               "name": self.names[idx] or ROLE_LABEL[role],
               "lapTime": lap_time, "invalid": invalid,
               "trackId": self.session.get("trackId"),
               "trackName": self.session.get("trackName", ""),
               "created": time.time(), "samples": buf}
        self.laps.append(lap)
        del self.laps[:-self.max_laps]
        self.outbox.append({"type": "lap", "lap": lap})
        self._save(lap)
        print(f"  lap recorded: {ROLE_LABEL[role]:<13} {fmt_lap(lap_time)}{'  (invalid)' if invalid else ''}")

    def _save(self, lap):
        slug = (lap["trackName"] or "track").lower().replace(" ", "-").replace("(", "").replace(")", "")
        stamp = fmt_lap(lap["lapTime"]).replace(":", "-").replace(".", "-")
        path = os.path.join(self.laps_dir, f"{slug}_{lap['role']}_{stamp}_{int(lap['created'])}.csv")
        meta = {k: lap[k] for k in ("role", "name", "lapTime", "invalid", "trackId", "trackName")}
        meta.update({k: self.session.get(k) or "" for k in ("trackLength", "sector2", "sector3")})
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                f.write("# " + ",".join(f"{k}={str(v).replace(',', ' ')}" for k, v in meta.items()) + "\n")
                w = csv.writer(f)
                w.writerow(FIELDS)
                w.writerows(lap["samples"])
        except OSError as e:
            print(f"  could not save lap: {e}")

    # ---- outgoing messages ------------------------------------------------
    def tick_rate(self, now):
        span = now - self._rate_t
        if span >= 0.5:
            self.rate = round(self._rate_count / span)
            self._rate_count, self._rate_t = 0, now

    def status(self):
        now = time.time()
        sources = sorted(self.sources.items(), key=lambda kv: -kv[1]["last"])[:4]
        cars = {}
        for role in ROLES:
            idx = self.role_index(role)
            cars[role] = idx is not None and "x" in self.cars[idx] and "d" in self.cars[idx]
        return {
            "type": "status",
            "bridge": self.info,
            "telemetry": {
                "packets": self.packets, "rate": self.rate,
                "lastAgo": round(now - self.last_packet, 1) if self.last_packet else None,
                "sources": [{"ip": ip, "format": s["format"], "game": game_name(s["format"]),
                             "packets": s["packets"], "ago": round(now - s["last"], 1)} for ip, s in sources],
                "ignored": self.ignored, "ignoredFormat": self.ignored_format,
                "activeIp": self.active_ip, "blocked": self.blocked,
                "blockedAgo": round(now - self.blocked_last, 1) if self.blocked_last else None,
            },
            "session": self.session, "cars": cars, "laps": len(self.laps),
        }

    def hello(self):
        """Initial snapshot: per car, the latest lap and the fastest lap (keeps phones light)."""
        pick = []
        for role in ROLES:
            mine = [l for l in self.laps if l["role"] == role]
            if mine:
                pick.append(mine[-1])
                best = min(mine, key=lambda l: (l["invalid"], l["lapTime"]))
                if best is not mine[-1]:
                    pick.append(best)
        pick.sort(key=lambda l: l["id"])
        return {"type": "hello", "fields": FIELDS, "session": self.session, "tt": self.tt,
                "laps": pick, "status": self.status()}

    def drain(self):
        out, self.outbox = self.outbox, []
        if self.frame_dirty:
            self.frame_dirty = False
            cars = {}
            for role in ROLES:
                idx = self.role_index(role)
                if idx is None or "x" not in self.cars[idx]:
                    continue
                c = self.cars[idx]
                live = {k: c.get(k) for k in LIVE_KEYS}
                live["idx"] = idx
                live["name"] = self.names[idx]
                cars[role] = live
            out.append({"type": "frame", "cars": cars})
        return out


# --------------------------------------------------------------------------
# Web server: static files + WebSocket on one port (standard library only)
# --------------------------------------------------------------------------
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
CORS = "Access-Control-Allow-Origin: *\r\n"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
         ".webmanifest": "application/manifest+json", ".json": "application/json",
         ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8", ".csv": "text/csv; charset=utf-8"}


def lap_meta(line: str) -> dict:
    """Parse the '# role=...,lapTime=...' first line of a saved lap CSV."""
    meta = {}
    if line.startswith("#"):
        for kv in line[1:].strip().split(","):
            k, _, v = kv.strip().partition("=")
            meta[k] = v
    out = {"role": meta.get("role", ""), "name": meta.get("name", ""), "trackName": meta.get("trackName", ""),
           "invalid": meta.get("invalid", "").lower() == "true"}
    for k, conv in (("lapTime", int), ("trackId", int), ("trackLength", int), ("sector2", float), ("sector3", float)):
        try:
            out[k] = conv(float(meta.get(k, "")))
        except ValueError:
            out[k] = None
    out["lapTime"] = out["lapTime"] or 0
    if out["role"] in ("pb", "rival") and 0 < out["lapTime"] < 20000:
        out["lapTime"], out["suspect"] = 0, True      # saved before the ghost timing fix: the app re-times it
    return out


def ws_frame(payload: bytes, opcode: int = 1) -> bytes:
    n = len(payload)
    if n < 126:
        head = struct.pack("!BB", 0x80 | opcode, n)
    elif n < 65536:
        head = struct.pack("!BBH", 0x80 | opcode, 126, n)
    else:
        head = struct.pack("!BBQ", 0x80 | opcode, 127, n)
    return head + payload


async def ws_read(reader):
    b1, b2 = await reader.readexactly(2)
    op, n = b1 & 0x0F, b2 & 0x7F
    if n == 126:
        n, = struct.unpack("!H", await reader.readexactly(2))
    elif n == 127:
        n, = struct.unpack("!Q", await reader.readexactly(8))
    if n > 1 << 20:
        raise ConnectionError("frame too large")
    mask = await reader.readexactly(4) if b2 & 0x80 else b"\0\0\0\0"
    data = bytearray(await reader.readexactly(n))
    for i in range(n):
        data[i] ^= mask[i & 3]
    return op, bytes(data)


class Hub:
    def __init__(self, web_dir: str, fleet):
        self.web_dir = os.path.realpath(web_dir)
        self.fleet = fleet
        self.drivers = fleet.drivers    # [{"name", "key", "port", "state"}] - shared, changes live
        self.clients = {}               # writer -> State it watches
        self.admins = set()             # writers using the admin code

    def access(self, query: str):
        """(State, is_admin) for an access code. The admin code can view any driver (?driver=NAME)."""
        q = urllib.parse.parse_qs(query)
        if len(self.drivers) == 1 and not self.drivers[0]["key"] and not self.fleet.admin_key:
            return self.drivers[0]["state"], False
        given = q.get("key", [""])[0].strip().lower().encode()
        found = None
        for d in self.drivers:            # compare against every code so timing gives nothing away
            if d["key"] and hmac.compare_digest(given, d["key"].lower().encode()):
                found = d["state"]
        admin = bool(self.fleet.admin_key) and hmac.compare_digest(given, self.fleet.admin_key.lower().encode())
        if admin:
            want = q.get("driver", [""])[0].strip().lower()
            pick = next((d for d in self.drivers if d["name"].lower() == want), None) or (self.drivers[0] if self.drivers else None)
            return (pick["state"] if pick else None), True
        return found, False

    async def handle(self, reader, writer):
        try:
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 15)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ConnectionError):
                return
            lines = head.decode("latin-1").split("\r\n")
            parts = lines[0].split(" ")
            if len(parts) < 2:
                return
            method, target = parts[0].upper(), parts[1]
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            url = urllib.parse.urlsplit(target)
            path = url.path
            state, admin = self.access(url.query)
            if method == "OPTIONS" and path.startswith("/api/"):
                await self.reply(writer, 204, b"", "text/plain", extra=CORS + "Access-Control-Allow-Methods: GET, POST\r\n"
                                 "Access-Control-Allow-Headers: Content-Type\r\n")
            elif path == "/ws" and "websocket" in headers.get("upgrade", "").lower():
                await self.websocket(reader, writer, headers, state, admin)
            elif path.startswith("/api/admin/"):
                if not admin:
                    await self.reply(writer, 401, b'{"error":"admin code required"}', "application/json", extra=CORS)
                else:
                    body = b""
                    if method == "POST":
                        n = min(int(headers.get("content-length", "0") or 0), 4096)
                        body = await asyncio.wait_for(reader.readexactly(n), 10) if n else b""
                    await self.admin_api(writer, method, path, body)
            elif path.startswith("/api/"):
                if state is None:
                    await self.reply(writer, 401, b'{"error":"access code required"}', "application/json",
                                     extra="Access-Control-Allow-Origin: *\r\n")
                else:
                    await self.api(writer, method, path, state, admin)
            else:
                await self.static(writer, method, path)
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def reply(self, writer, code, body: bytes, ctype, head_only=False, extra=""):
        reason = {200: "OK", 204: "No Content", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
                  404: "Not Found", 405: "Method Not Allowed"}.get(code, "OK")
        hdr = (f"HTTP/1.1 {code} {reason}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
               f"Cache-Control: no-cache\r\n{extra}Connection: close\r\n\r\n")
        writer.write(hdr.encode() + (b"" if head_only else body))
        await writer.drain()

    async def static(self, writer, method, path):
        if method not in ("GET", "HEAD"):
            return await self.reply(writer, 405, b"Method not allowed", "text/plain")
        rel = urllib.parse.unquote(path).lstrip("/")
        full = os.path.realpath(os.path.join(self.web_dir, rel))
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        if not full.startswith(self.web_dir + os.sep) or not os.path.isfile(full):
            return await self.reply(writer, 404, b"Not found", "text/plain")
        ext = os.path.splitext(full)[1].lower()
        ctype = TYPES.get(ext) or mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            body = f.read()
        await self.reply(writer, 200, body, ctype, head_only=method == "HEAD")

    def library(self, state, admin):
        """Every saved lap, grouped by track. Drivers see their own; the admin code sees everyone's."""
        root = os.path.realpath(self.fleet.args.laps)
        folders = []                                     # (driver, folder path, url prefix)
        if admin and self.fleet.multi:
            names = {slugify(d["name"]): d["name"] for d in self.drivers}
            try:
                subs = sorted(os.listdir(root))
            except OSError:
                subs = []
            for sub in subs:
                full = os.path.join(root, sub)
                if os.path.isdir(full) and not sub.startswith("."):
                    folders.append((names.get(sub, sub), full, f"/api/lib/{urllib.parse.quote(sub)}/"))
        else:
            folders.append((state.info.get("driver") or "", os.path.realpath(state.laps_dir), "/api/saved/"))
        tracks = {}
        count = 0
        for driver, folder, prefix in folders:
            try:
                files = os.listdir(folder)
            except OSError:
                continue
            for name in files:
                if not name.lower().endswith(".csv") or count >= 5000:
                    continue
                full = os.path.join(folder, name)
                try:
                    mtime = os.stat(full).st_mtime
                    with open(full, encoding="utf-8", errors="replace") as f:
                        meta = lap_meta(f.readline())
                except OSError:
                    continue
                count += 1
                tname = meta.get("trackName") or "Unknown track"
                t = tracks.setdefault(tname, {"trackName": tname, "trackId": meta.get("trackId"), "trackLength": None,
                                              "sector2": None, "sector3": None, "laps": [], "last": 0})
                for k in ("trackLength", "sector2", "sector3"):
                    t[k] = t[k] or meta.get(k)
                t["last"] = max(t["last"], round(mtime))
                t["laps"].append(dict(meta, driver=driver, url=prefix + urllib.parse.quote(name), modified=round(mtime)))
        out = sorted(tracks.values(), key=lambda t: -t["last"])
        for t in out:
            t["laps"].sort(key=lambda l: (l["lapTime"] or 9e9))
            t["drivers"] = sorted({l["driver"] for l in t["laps"] if l["driver"]})
            valid = [l["lapTime"] for l in t["laps"] if l["lapTime"] and not l["invalid"] and l["role"] == "player"]
            t["best"] = min(valid) if valid else None
        return {"admin": bool(admin), "tracks": out}

    async def api(self, writer, method, path, state, admin=False):
        """Saved laps: GET /api/saved (list), /api/saved/<file>.csv and /api/library (grouped by track)"""
        cors = "Access-Control-Allow-Origin: *\r\n"
        if method not in ("GET", "HEAD"):
            return await self.reply(writer, 405, b"Method not allowed", "text/plain", extra=cors)
        head_only = method == "HEAD"
        laps_dir = os.path.realpath(state.laps_dir)
        if path.rstrip("/") == "/api/saved":
            items = []
            try:
                names = os.listdir(laps_dir)
            except OSError:
                names = []
            for name in names:
                if not name.lower().endswith(".csv"):
                    continue
                full = os.path.join(laps_dir, name)
                try:
                    st = os.stat(full)
                    with open(full, encoding="utf-8", errors="replace") as f:
                        first = f.readline()
                except OSError:
                    continue
                items.append(dict(lap_meta(first), file=name, size=st.st_size, modified=round(st.st_mtime)))
            items.sort(key=lambda m: -m["modified"])
            body = json.dumps(items[:500], separators=(",", ":")).encode()
            return await self.reply(writer, 200, body, "application/json", head_only, cors)
        if path.rstrip("/") == "/api/library":
            body = json.dumps(self.library(state, admin), separators=(",", ":")).encode()
            return await self.reply(writer, 200, body, "application/json", head_only, cors)
        if path.startswith("/api/lib/") and admin:          # admin: any driver's folder, including removed drivers
            rest = urllib.parse.unquote(path[len("/api/lib/"):])
            folder, _, name = rest.partition("/")
            root = os.path.realpath(self.fleet.args.laps)
            full = os.path.realpath(os.path.join(root, folder, name))
            if (not folder or folder.startswith(".") or "/" in name or "\\" in name or name.startswith(".")
                    or not name.lower().endswith(".csv") or os.path.dirname(full) != os.path.join(root, folder)
                    or not os.path.isfile(full)):
                return await self.reply(writer, 404, b"Not found", "text/plain", extra=cors)
            with open(full, "rb") as f:
                body = f.read()
            return await self.reply(writer, 200, body, "text/csv; charset=utf-8", head_only, cors)
        if path.startswith("/api/saved/"):
            name = urllib.parse.unquote(path[len("/api/saved/"):])
            full = os.path.realpath(os.path.join(laps_dir, name))
            if ("/" in name or "\\" in name or name.startswith(".") or not name.lower().endswith(".csv")
                    or not full.startswith(laps_dir + os.sep) or not os.path.isfile(full)):
                return await self.reply(writer, 404, b"Not found", "text/plain", extra=cors)
            with open(full, "rb") as f:
                body = f.read()
            return await self.reply(writer, 200, body, "text/csv; charset=utf-8", head_only, cors)
        return await self.reply(writer, 404, b"Not found", "text/plain", extra=cors)

    async def admin_api(self, writer, method, path, body):
        """GET /api/admin/drivers: everyone's port, code and status.
        POST /api/admin/drivers {"action": "add"|"remove"|"code", "name": ...}: manage drivers."""
        if path.rstrip("/") != "/api/admin/drivers":
            return await self.reply(writer, 404, b'{"error":"not found"}', "application/json", extra=CORS)
        if method == "POST":
            if not self.fleet.web_admin:
                return await self.reply(writer, 403, json.dumps({"error": self.fleet.why_no_web_admin}).encode(),
                                        "application/json", extra=CORS)
            try:
                req = json.loads(body.decode("utf-8") or "{}")
                action, name = str(req.get("action", "")), str(req.get("name", "")).strip()
                if action == "add":
                    await self.fleet.add(name)
                elif action == "remove":
                    self.fleet.remove(name)
                elif action == "code":
                    self.fleet.new_code(name)
                else:
                    raise ValueError("unknown action")
            except ValueError as e:
                return await self.reply(writer, 400, json.dumps({"error": str(e)}).encode(), "application/json", extra=CORS)
        elif method not in ("GET", "HEAD"):
            return await self.reply(writer, 405, b"{}", "application/json", extra=CORS)
        body = json.dumps(self.fleet.summary(full=True), separators=(",", ":")).encode()
        await self.reply(writer, 200, body, "application/json", method == "HEAD", CORS)

    async def websocket(self, reader, writer, headers, state, admin=False):
        key = headers.get("sec-websocket-key")
        if not key:
            return await self.reply(writer, 400, b"Bad request", "text/plain")
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        sock = writer.get_extra_info("socket")
        if sock is not None:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
        peer = (writer.get_extra_info("peername") or ["?"])[0]
        if state is None and not admin:              # tell the app to ask for the access code
            writer.write(ws_frame(b'{"type":"auth","ok":false}') + ws_frame(struct.pack("!H", 4001), 8))
            await writer.drain()
            print(f"  app refused (wrong or missing access code): {peer}")
            return
        who = (state.info.get("driver") or "") if state else ""
        print(f"  app connected: {peer}{' (' + who + ')' if who else ''}{' [admin]' if admin else ''}")
        if state is not None:
            self.clients[writer] = state
        if admin:
            self.admins.add(writer)
        try:
            hello = state.hello() if state else {"type": "hello", "fields": FIELDS, "session": {}, "tt": None, "laps": [],
                                                  "status": {"type": "status", "bridge": {}, "telemetry": {}, "cars": {}}}
            if admin:                               # admin sees who's who and can switch driver
                hello["admin"] = self.fleet.summary()
            writer.write(ws_frame(json.dumps(hello, separators=(",", ":")).encode()))
            await writer.drain()
            while True:
                op, data = await ws_read(reader)
                if op == 8:                      # close
                    writer.write(ws_frame(data[:2], 8))
                    break
                if op == 9:                      # ping
                    writer.write(ws_frame(data, 10))
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            self.clients.pop(writer, None)
            self.admins.discard(writer)
            print(f"  app disconnected: {peer}")

    def send_admins(self, msg):
        if not self.admins:
            return
        frame = ws_frame(json.dumps(msg, separators=(",", ":")).encode())
        for w in list(self.admins):
            if w.is_closing():
                self.admins.discard(w)
                continue
            try:
                w.write(frame)
            except Exception:
                self.admins.discard(w)

    def broadcast(self, state, msgs):
        watchers = [w for w, st in self.clients.items() if st is state]
        if not watchers or not msgs:
            return
        for m in msgs:
            frame = ws_frame(json.dumps(m, separators=(",", ":")).encode())
            droppable = m.get("type") == "frame"
            for w in watchers:
                if w.is_closing():
                    self.clients.pop(w, None)
                    continue
                if droppable and w.transport.get_write_buffer_size() > 256_000:
                    continue                      # slow phone: skip a live frame, never a lap
                try:
                    w.write(frame)
                except Exception:
                    self.clients.pop(w, None)


class UDPReceiver(asyncio.DatagramProtocol):
    def __init__(self, state: State, forward):
        self.state = state
        self.forward = forward
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.state.handle(data, addr[0])
        for target in self.forward:
            self.transport.sendto(data, target)


def local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "driver"


def load_drivers(path):
    """drivers.json: {"drivers": [{"name": "Sam", "port": 20777, "key": "k7m2xq9p"}, ...]}"""
    try:
        with open(path, encoding="utf-8") as f:
            specs = json.load(f).get("drivers", [])
    except (OSError, ValueError) as e:
        sys.exit(f"Can't read the drivers file {path}: {e}")
    if not specs:
        sys.exit(f"No drivers in {path} - add one with:  sudo ghostlab add NAME")
    names, ports, keys = set(), set(), set()
    for d in specs:
        name, port, key = str(d.get("name", "")).strip(), d.get("port"), str(d.get("key", "")).strip()
        if not name or not isinstance(port, int) or not key:
            sys.exit(f"Every driver in {path} needs a name, a port and a key: {d}")
        if name.lower() in names or port in ports or key.lower() in keys:
            sys.exit(f"Driver names, ports and keys must all be different in {path} ({name})")
        names.add(name.lower()); ports.add(port); keys.add(key.lower())
    return [{"name": str(d["name"]).strip(), "port": d["port"], "key": str(d["key"]).strip()} for d in specs]


def resolve(host):
    if not host:
        return None
    try:
        return socket.gethostbyname(host)
    except OSError:
        return None



NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.'-]{0,23}")
CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
PORT_FIRST, PORT_LAST = 20777, 20799


class Fleet:
    """All the drivers on this bridge. With the admin code (and --web-admin) drivers can be
    added, removed or given a new code from the website while everything keeps running."""

    def __init__(self, args, lan, public_host, public_ip, multi):
        self.args, self.lan, self.public_host, self.public_ip, self.multi = args, lan, public_host, public_ip, multi
        self.drivers, self.transports, self.retired = [], {}, []
        self.admin_key = (args.admin_key or "").strip()
        self.web_admin = bool(args.web_admin and multi and self.admin_key)
        self.why_no_web_admin = ("Adding drivers from the website is switched off here. "
                                 "Use: sudo ghostlab add NAME")
        self.loop, self.forward = None, []
        if multi:
            try:
                with open(args.drivers, encoding="utf-8") as f:
                    self.retired = list(json.load(f).get("retired", []))
            except (OSError, ValueError):
                pass

    def info_for(self, spec):
        a = self.args
        return {"version": VERSION, "udpPort": spec["port"], "httpPort": a.http_port,
                "lanIp": self.public_ip if a.cloud and self.public_ip else self.lan,
                "publicHost": self.public_host, "publicIp": self.public_ip, "https": bool(a.https),
                "cloud": bool(a.cloud), "protected": bool(spec["key"]),
                "driver": spec["name"]}

    def make(self, spec):
        laps = os.path.join(self.args.laps, slugify(spec["name"])) if self.multi else self.args.laps
        d = {"name": spec["name"], "key": spec["key"], "port": spec["port"], "state": State(laps, self.info_for(spec))}
        self.drivers.append(d)
        return d

    async def listen(self, d):
        transport, _ = await self.loop.create_datagram_endpoint(
            lambda st=d["state"]: UDPReceiver(st, self.forward), local_addr=(self.args.host, d["port"]))
        self.transports[d["port"]] = transport

    def find(self, name):
        d = next((d for d in self.drivers if d["name"].lower() == name.strip().lower()), None)
        if d is None:
            raise ValueError(f"no driver called {name}")
        return d

    def fresh_code(self):
        taken = {d["key"].lower() for d in self.drivers} | {self.admin_key.lower()}
        while True:
            k = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
            if k not in taken:
                return k

    async def add(self, name):
        name = name.strip()
        if not NAME_RE.fullmatch(name):
            raise ValueError("use a short name with letters and numbers")
        if any(d["name"].lower() == name.lower() for d in self.drivers):
            raise ValueError(f"{name} already exists")
        used = {d["port"] for d in self.drivers}
        free = [p for p in range(PORT_FIRST, PORT_LAST + 1) if p not in used]
        if not free:
            raise ValueError("all ports are taken")
        port = next((p for p in free if p not in self.retired), free[0])
        d = self.make({"name": name, "port": port, "key": self.fresh_code()})
        try:
            await self.listen(d)
        except OSError as e:
            self.drivers.remove(d)
            raise ValueError(f"couldn't open UDP port {port}: {e}")
        self.save()
        print(f"  driver added from the website: {name} (UDP {port})")

    def remove(self, name):
        d = self.find(name)
        if len(self.drivers) == 1:
            raise ValueError("can't remove the last driver")
        t = self.transports.pop(d["port"], None)
        if t:
            t.close()
        self.drivers.remove(d)
        self.retired = sorted(set(self.retired) | {d["port"]})
        self.save()
        print(f"  driver removed from the website: {d['name']} (laps kept)")

    def new_code(self, name):
        d = self.find(name)
        d["key"] = self.fresh_code()
        self.save()

    def save(self):
        if not self.multi:
            return
        path = self.args.drivers
        data = {"drivers": [{"name": d["name"], "port": d["port"], "key": d["key"]} for d in self.drivers],
                "retired": sorted(set(self.retired) - {d["port"] for d in self.drivers})}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        try:
            os.chmod(tmp, os.stat(path).st_mode & 0o777)
        except OSError:
            pass
        os.replace(tmp, path)

    def summary(self, full=False):
        now = time.time()
        rows = []
        for d in self.drivers:
            st = d["state"]
            row = {"name": d["name"], "port": d["port"], "laps": len(st.laps),
                   "live": bool(st.last_packet and now - st.last_packet < 3),
                   "track": st.session.get("trackName", "")}
            if full:
                row["key"] = d["key"]
            rows.append(row)
        return {"drivers": rows, "webAdmin": self.web_admin, "publicHost": self.public_host,
                "publicIp": self.public_ip, "https": bool(self.args.https), "cloud": bool(self.args.cloud)}


async def main_async(args):
    web_dir = os.path.join(HERE, "web")
    if not os.path.isfile(os.path.join(web_dir, "index.html")):
        sys.exit(f"Can't find the web app at {web_dir} - keep bridge.py next to the 'web' folder.")
    if args.advertise:                                  # older cloud-server setting
        args.public, args.cloud = args.public or args.advertise, True
    public_host = (args.public or "").strip() or None
    public_ip = resolve(public_host)
    lan = local_ip()
    multi = bool(args.drivers)
    specs = load_drivers(args.drivers) if multi else [{"name": "", "port": args.port, "key": (args.key or "").strip()}]

    fleet = Fleet(args, lan, public_host, public_ip, multi)
    if fleet.admin_key and any(sp["key"].lower() == fleet.admin_key.lower() for sp in specs):
        sys.exit("The admin code must be different from every driver's code")
    for spec in specs:
        fleet.make(spec)
    drivers = fleet.drivers                      # the same list: drivers added later appear here too
    hub = Hub(web_dir, fleet)
    await asyncio.start_server(hub.handle, args.bind, args.http_port)

    loop = asyncio.get_running_loop()
    fleet.loop = loop
    for f in args.forward or []:
        host, port = f.rsplit(":", 1)
        fleet.forward.append((host, int(port)))
    for d in list(drivers):
        await fleet.listen(d)

    def links(key):
        q = f"?key={key}" if key else ""
        home = f"http://{lan}:{args.http_port}/{q}"
        away = (f"https://{public_host}/{q}" if args.https else f"http://{public_host}:{args.http_port}/{q}") if public_host else None
        return home, away

    kind = "cloud server" if args.cloud else f"{len(drivers)} driver{'s' if len(drivers) > 1 else ''}" if multi else ""
    print(f"\nGhost Lab bridge {VERSION}{' (' + kind + ')' if kind else ''}")
    if multi:
        for d in drivers:
            home, away = links(d["key"])
            print(f"  {d['name']:<12} UDP {d['port']}  code {d['key']}")
            if not args.cloud:
                print(f"               local:      {home}")
            if away:
                print(f"               anywhere:   {away}")
        if public_host and not args.cloud:
            print(f"  consoles at home send to {lan}; consoles elsewhere send to {public_ip or public_host}")
    else:
        key = drivers[0]["key"]
        home, away = links(key)
        if args.cloud:
            print(f"  open on any phone:  {away or home}")
        else:
            print(f"  on this PC:     http://localhost:{args.http_port}" + (f"/?key={key}" if key else ""))
            print(f"  on your phone:  {home}   (same network)")
            if away:
                print(f"  anywhere:       {away}")
        if key:
            print(f"  access code:    {key}")
        if args.cloud:
            print(f"  in the game:    UDP IP {public_ip or public_host}, port {args.port}, Broadcast Off, format 2026")
        else:
            print(f"  telemetry:      listening on UDP {args.port}")
            print(f"                  game on this PC -> UDP IP 127.0.0.1 | console -> UDP IP {lan}")
    if fleet.admin_key:
        _, away = links(fleet.admin_key)
        print(f"  admin code:     {fleet.admin_key}   (sees every driver{'; can add/remove drivers' if fleet.web_admin else ''})")
        if away:
            print(f"                  {away}")
    print(f"  laps saved to:  {os.path.abspath(args.laps)}\n")
    if not args.no_browser and not args.cloud and not multi:
        try:
            webbrowser.open(f"http://localhost:{args.http_port}" + (f"/?key={drivers[0]['key']}" if drivers[0]["key"] else ""))
        except Exception:
            pass

    last_status = 0.0
    last_hint = time.time()
    last_resolve = time.time()
    while True:
        await asyncio.sleep(1 / args.fps)
        for d in list(drivers):
            hub.broadcast(d["state"], d["state"].drain())
        now = time.time()
        if now - last_status >= 1:
            last_status = now
            for d in list(drivers):
                d["state"].tick_rate(now)
                hub.broadcast(d["state"], [d["state"].status()])
            hub.send_admins({"type": "drivers", **fleet.summary()})
        if public_host and now - last_resolve > 600:    # home IPs change; keep the console hint current
            last_resolve = now
            ip = await loop.run_in_executor(None, resolve, public_host)
            if ip:
                fleet.public_ip = ip
                for d in list(drivers):
                    d["state"].info["publicIp"] = ip
                    if args.cloud:
                        d["state"].info["lanIp"] = ip
        st = drivers[0]["state"]
        if not multi and st.packets == 0 and now - last_hint > 20:
            last_hint = now
            if st.ignored:
                print(f"  ...packets arriving in format {st.ignored_format} - set UDP Format to 2026 (or 2025)")
            else:
                print("  ...no telemetry yet - check the game's UDP settings and your firewall")


def main():
    p = argparse.ArgumentParser(description="Ghost Lab bridge for F1 26 / F1 25 UDP telemetry")
    p.add_argument("--port", type=int, default=20777, help="game UDP port (default 20777)")
    p.add_argument("--host", default="0.0.0.0", help="address to receive UDP on (default all)")
    p.add_argument("--http-port", type=int, default=8000, help="port for the web app (default 8000)")
    p.add_argument("--bind", default="0.0.0.0", help="address the web app listens on (default all)")
    p.add_argument("--laps", default=os.path.join(HERE, "laps"), help="folder for saved lap CSVs")
    p.add_argument("--forward", action="append", metavar="HOST:PORT",
                   help="also forward raw packets to another app, e.g. SimHub (repeatable)")
    p.add_argument("--no-browser", action="store_true", help="don't open the app on this PC")
    p.add_argument("--key", default=os.environ.get("GHOSTLAB_KEY", ""),
                   help="access code the app must give (for servers on the internet); env GHOSTLAB_KEY")
    p.add_argument("--drivers", default=os.environ.get("GHOSTLAB_DRIVERS", ""), metavar="FILE",
                   help="several drivers, each with their own UDP port and access code; env GHOSTLAB_DRIVERS")
    p.add_argument("--public", default=os.environ.get("GHOSTLAB_PUBLIC", ""), metavar="HOST",
                   help="address people use from outside your network (e.g. you.duckdns.org); env GHOSTLAB_PUBLIC")
    p.add_argument("--https", action="store_true", default=os.environ.get("GHOSTLAB_HTTPS", "") == "1",
                   help="the public address is served over HTTPS by a proxy such as Caddy; env GHOSTLAB_HTTPS=1")
    p.add_argument("--cloud", action="store_true", default=os.environ.get("GHOSTLAB_CLOUD", "") == "1",
                   help="cloud server with no home network; env GHOSTLAB_CLOUD=1")
    p.add_argument("--admin-key", default=os.environ.get("GHOSTLAB_ADMIN_KEY", ""), metavar="CODE",
                   help="an admin code that can view every driver; env GHOSTLAB_ADMIN_KEY")
    p.add_argument("--web-admin", action="store_true", default=os.environ.get("GHOSTLAB_WEB_ADMIN", "") == "1",
                   help="let the admin add/remove drivers from the website (needs --drivers); env GHOSTLAB_WEB_ADMIN=1")
    p.add_argument("--advertise", default=os.environ.get("GHOSTLAB_ADVERTISE", ""), help=argparse.SUPPRESS)
    p.add_argument("--fps", type=int, default=int(os.environ.get("GHOSTLAB_FPS", "30") or 30),
                   help="live updates per second sent to phones (default 30); env GHOSTLAB_FPS")
    args = p.parse_args()
    args.fps = max(5, min(60, args.fps))
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nbye")
    except OSError as e:
        sys.exit(f"Could not open a port: {e}\nIs another telemetry app (or another bridge) already "
                 "using it? Close it, or use --port / --http-port / --forward.")


if __name__ == "__main__":
    main()
