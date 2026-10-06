"""Configuratie uit config.yaml (pad via MESHTRACK_CONFIG, standaard ./config.yaml)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Config:
    mesh_host: str = "127.0.0.1"
    mesh_port: int = 5051
    keepalive_s: int = 600            # openHop verbreekt na 8 u stilte
    http_host: str = "0.0.0.0"
    http_port: int = 8090
    db_path: str = "meshtrack.sqlite3"
    tiles_dir: str = "tiles"
    auth_user: str = "admin"
    auth_password_hash: str = ""      # leeg = inloggen onmogelijk
    session_secret: str = ""
    session_days: int = 30
    map_center: tuple[float, float] = (5.33, 50.93)   # lon, lat
    map_zoom: float = 10
    region_bbox: tuple[float, float, float, float] = (2.0, 49.0, 7.8, 54.0)
    max_hdop: float = 5.0
    stale_after_s: int = 25 * 3600    # grijs op de kaart (2x heartbeat + marge)
    retention_days: int = 90
    openhop_db: str = "/var/lib/openhop_repeater/repeater.db"   # adverts die openHop als observer zag
    dedup_window_s: int = 24 * 3600
    extra: dict = field(default_factory=dict)


def load(path: str | None = None) -> Config:
    p = Path(path or os.environ.get("MESHTRACK_CONFIG", "config.yaml"))
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    raw = raw or {}
    mesh = raw.get("mesh", {})
    http = raw.get("http", {})
    auth = raw.get("auth", {})
    mp = raw.get("map", {})
    c = Config()
    c.mesh_host = mesh.get("host", c.mesh_host)
    c.mesh_port = int(mesh.get("port", c.mesh_port))
    c.keepalive_s = int(mesh.get("keepalive_s", c.keepalive_s))
    c.http_host = http.get("host", c.http_host)
    c.http_port = int(http.get("port", c.http_port))
    c.db_path = raw.get("db_path", c.db_path)
    c.tiles_dir = raw.get("tiles_dir", c.tiles_dir)
    c.auth_user = auth.get("user", c.auth_user)
    c.auth_password_hash = auth.get("password_hash", c.auth_password_hash)
    c.session_secret = auth.get("session_secret", c.session_secret)
    c.session_days = int(auth.get("session_days", c.session_days))
    if "center" in mp:
        c.map_center = tuple(mp["center"])
    c.map_zoom = float(mp.get("zoom", c.map_zoom))
    if "region_bbox" in raw:
        c.region_bbox = tuple(raw["region_bbox"])
    c.max_hdop = float(raw.get("max_hdop", c.max_hdop))
    c.stale_after_s = int(raw.get("stale_after_s", c.stale_after_s))
    c.retention_days = int(raw.get("retention_days", c.retention_days))
    c.openhop_db = raw.get("openhop_db", c.openhop_db)
    c.extra = raw
    return c
