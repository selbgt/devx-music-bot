#!/usr/bin/env python3
# ============================================================
#  🎵 DEV X CORE — SIMPLE MUSIC BOT (Song Play Only)
#  Removed: files vault, .run/.vrun, banner, rjoin, .save/.ytdl
#  Kept: .play / .nplay + pause/resume/skip/stop + queue + effects
# ============================================================

import asyncio
import os
import re
import sys
import logging
import sqlite3
import uuid
import time
import math
import hashlib
import random
import html
import functools
from io import BytesIO
from collections import deque
from datetime import datetime
import json

# ==================== EVENT LOOP (critical) ====================
LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)

import yt_dlp
import aiohttp
from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from pyrogram.enums import ChatType, ChatMemberStatus, MessageServiceType

from pyrogram.storage.memory_storage import MemoryStorage
from pyrogram.errors import (
    PhoneCodeInvalid, PhoneCodeExpired, SessionPasswordNeeded, FloodWait,
    PhoneNumberInvalid, PasswordHashInvalid, PhoneNumberBanned, PhoneNumberUnoccupied,
)

from pytgcalls import PyTgCalls, idle
from pytgcalls.types.stream import MediaStream, AudioQuality, VideoQuality, StreamEnded
from pytgcalls.exceptions import NoActiveGroupCall

# ==================== VERSION COMPAT CHECK ====================
def _ver_tuple(v):
    try:
        return tuple(int(x) for x in re.findall(r"\d+", v)[:3])
    except Exception:
        return (0, 0, 0)

def check_engine_compat():
    try:
        import pyrogram as _pg
        pgv = _ver_tuple(getattr(_pg, "__version__", "0"))
    except Exception:
        pgv = (0, 0, 0)
    try:
        from importlib.metadata import version as _dist_version
        ptv = _ver_tuple(_dist_version("py-tgcalls"))
    except Exception:
        ptv = (0, 0, 0)
    if pgv and pgv[:2] <= (2, 0) and ptv[:3] >= (2, 2, 6):
        return False, (
            "py-tgcalls {} is NOT compatible with pyrogram {} "
            "(GroupcallForbidden missing).\n"
            "Fix: pip install \"pyrogram==2.0.106\" \"py-tgcalls[pyrogram]==2.2.5\""
        ).format(".".join(map(str, ptv)), ".".join(map(str, pgv)))
    return True, ""

# ==================== CONFIG ====================
API_ID = int(os.getenv("API_ID", "31633970"))
API_HASH = os.getenv("API_HASH", "702925952d6f164b1cca78e9fa9c5488")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "5206554804"))

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()

PREFIX = "."
DEFAULT_VOLUME = 100
AUTO_UNMUTE = True
ENFORCE_INTERVAL = 5

BOT_NAME = "DEV X CORE"
BRAND = "𝐃𝐄𝐕 𝐗 𝐂𝐎𝐑𝐄"
CORE_VERSION = "5.4"
START_TIME = time.time()
SESSION_NAME = "devx_music"
SESSION_FILE = "session_string.txt"
DB_PATH = "devx_music.db"
LOGS_DIR = "logs"
TEMP_DIR = "temp"

SESSION_STRING = os.getenv("SESSION_STRING", "").strip()
if not SESSION_STRING and os.path.exists(SESSION_FILE):
    with open(SESSION_FILE, "r") as _f:
        SESSION_STRING = _f.read().strip()

for _d in (LOGS_DIR, TEMP_DIR):
    os.makedirs(_d, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOGS_DIR, "bot.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("DEVX_MUSIC")

# ==================== DATABASE (simplified: users + stats only) ====================
class Database:
    def __init__(self, path=DB_PATH):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.c = self.conn.cursor()
        self._init()

    def _init(self):
        self.c.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
                last_name TEXT, joined_at TEXT, is_active INTEGER DEFAULT 1)"""
        )
        self.c.execute(
            """CREATE TABLE IF NOT EXISTS stats (
                key TEXT PRIMARY KEY, value INTEGER DEFAULT 0)"""
        )
        self.c.execute(
            """CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY, value TEXT)"""
        )
        self.conn.commit()
        logger.info("Database ready")

    def add_user(self, uid, username, first_name, last_name=""):
        now = datetime.now().isoformat()
        self.c.execute(
            "INSERT OR IGNORE INTO users (id, username, first_name, last_name, joined_at) VALUES (?,?,?,?,?)",
            (uid, username or "", first_name or "", last_name or "", now),
        )
        if self.c.rowcount > 0:
            self.c.execute(
                "INSERT INTO stats (key, value) VALUES ('total_users', 1) "
                "ON CONFLICT(key) DO UPDATE SET value = value + 1"
            )
        self.conn.commit()

    def get_users(self, limit=5000):
        return self.c.execute("SELECT * FROM users WHERE is_active=1 LIMIT ?", (limit,)).fetchall()

    def ban_user(self, uid):
        self.c.execute("UPDATE users SET is_active=0 WHERE id=?", (uid,))
        self.conn.commit()

    def unban_user(self, uid):
        self.c.execute("UPDATE users SET is_active=1 WHERE id=?", (uid,))
        self.conn.commit()

    def bump(self, key, amount=1):
        self.c.execute(
            "INSERT INTO stats (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = value + ?",
            (key, amount, amount),
        )
        self.conn.commit()

    def get_stat(self, key):
        r = self.c.execute("SELECT value FROM stats WHERE key=?", (key,)).fetchone()
        return r[0] if r else 0

    def set_config(self, key, value):
        self.c.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value)
        )
        self.conn.commit()

    def get_config(self, key, default=None):
        r = self.c.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    def close(self):
        self.conn.close()

db = Database()

# ==================== QUEUE / STATE (in-memory only, no files) ====================
class MusicQueue:
    def __init__(self):
        self.songs = deque()
        self.current = None
        self.loop = False
        self.repeat = False
        self.boost_level = 0
        self.eco_level = 0
        self.gain_level = 0
        self.loud_level = 0
        self.treble = 0
        self.volume = DEFAULT_VOLUME
        self.silent_loop = False
        self.speed = 1.0
        self.nightcore = False
        self.bass = 0

queues = {}
active_calls = set()
awaiting = {}
search_cache = {}
search_seq = [0]
last_search = {}
current_files = {}
replay_lock = set()
err_notified = set()
_last_vol_applied = {}
_muted_state = {}
END_GUARD = {}
_vol_auto = {}
AUTO_MAX_VOLUME = 200

PLAY_TIMERS = {}

def _reset_timer(chat_id, seek=0):
    PLAY_TIMERS[chat_id] = {
        "start": time.time() - max(0, int(seek or 0)),
        "paused_elapsed": 0.0,
        "paused": False,
    }

def _pause_timer(chat_id):
    t = PLAY_TIMERS.get(chat_id)
    if t and not t["paused"]:
        t["paused_elapsed"] += time.time() - t["start"]
        t["paused"] = True

def _resume_timer(chat_id):
    t = PLAY_TIMERS.get(chat_id)
    if t and t["paused"]:
        t["start"] = time.time()
        t["paused"] = False

def get_elapsed(chat_id):
    t = PLAY_TIMERS.get(chat_id)
    if not t:
        return 0
    if t["paused"]:
        return t["paused_elapsed"]
    return t["paused_elapsed"] + (time.time() - t["start"])

def get_queue(chat_id):
    if chat_id not in queues:
        queues[chat_id] = MusicQueue()
    return queues[chat_id]

# ==================== PERMISSIONS ====================
async def chat_locked(chat_id):
    return db.get_config(f"lock:{chat_id}") == "1"

async def is_group_admin(client, chat_id, user_id):
    if user_id == OWNER_ID:
        return True
    try:
        member = await client.get_chat_member(chat_id, user_id)
        return member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)
    except Exception:
        return False

def get_core_admins():
    raw = db.get_config("core_admins", "") or ""
    ids = {int(x) for x in raw.split(",") if x.strip().isdigit()}
    ids.add(OWNER_ID)
    return ids

def is_core_admin(user_id):
    try:
        return int(user_id) in get_core_admins()
    except Exception:
        return False

def add_core_admin(user_id):
    ids = {int(x) for x in (db.get_config("core_admins", "") or "").split(",") if x.strip().isdigit()}
    ids.add(int(user_id))
    db.set_config("core_admins", ",".join(str(i) for i in sorted(ids)))

def del_core_admin(user_id):
    ids = {int(x) for x in (db.get_config("core_admins", "") or "").split(",") if x.strip().isdigit()}
    ids.discard(int(user_id))
    db.set_config("core_admins", ",".join(str(i) for i in sorted(ids)))

async def can_control(client, chat_id, user_id):
    if is_core_admin(user_id):
        return True
    try:
        from music import uid_gbanned
        if uid_gbanned(user_id):
            return False
    except Exception:
        pass
    if await chat_locked(chat_id):
        return False
    mode = db.get_config("mode", "public")
    if mode == "private":
        return False
    return await is_group_admin(client, chat_id, user_id)

def get_mode():
    return db.get_config("mode", "public")

def set_mode(mode):
    db.set_config("mode", mode)

async def _deny_reply(message):
    try:
        if get_mode() == "private":
            t = f"🔐 『{BRAND}』 — Sirf OWNER aur core admins hi bot chala sakte hain. 👑\n`.public` — mode badalne ke liye (owner)"
        else:
            t = f"🔒 『{BRAND}』 — Sirf group ke owner/admins hi control kar sakte hain.\n`.private` — sirf owner + core admins"
        await message.reply_text(t)
    except Exception:
        pass

async def _deny_answer(cb):
    try:
        txt = "🔐 Private mode — sirf owner/core admins 👑" if get_mode() == "private" else "🔒 Sirf group owner/admins (public mode)"
        await cb.answer(txt, show_alert=True)
    except Exception:
        pass

# ==================== HELPERS ====================
FONT_MAP = {
    'A': '𝐀','B': '𝐁','C': '𝐂','D': '𝐃','E': '𝐄','F': '𝐅','G': '𝐆','H': '𝐇',
    'I': '𝐈','J': '𝐉','K': '𝐊','L': '𝐋','M': '𝐌','N': '𝐍','O': '𝐎','P': '𝐏',
    'Q': '𝐐','R': '𝐑','S': '𝐒','T': '𝐓','U': '𝐔','V': '𝐕','W': '𝐖','X': '𝐗',
    'Y': '𝐘','Z': '𝐙',
    'a': 'ᴀ','b': 'ʙ','c': 'ᴄ','d': 'ᴅ','e': 'ᴇ','f': 'ғ','g': 'ɢ','h': 'ʜ',
    'i': 'ɪ','j': 'ᴊ','k': 'ᴋ','l': 'ʟ','m': 'ᴍ','n': 'ɴ','o': 'ᴏ','p': 'ᴘ',
    'q': 'ǫ','r': 'ʀ','s': 's','t': 'ᴛ','u': 'ᴜ','v': 'ᴠ','w': 'ᴡ','x': 'x',
    'y': 'ʏ','z': 'ᴢ',
}

def stylish(text):
    return "".join(FONT_MAP.get(c, c) for c in text)

RULE = "◈━━━━━━━━━━━━━━━━━━━━━━━━━━◈"

def core_card(title, body, footer=None):
    lines = [RULE, f"   {BRAND}", f"   ⌁  {stylish(title)}  ⌁", RULE, "", body.rstrip(), "", RULE]
    if footer:
        lines.append(f"   {footer}")
        lines.append(RULE)
    return "\n".join(lines)

def fmt_uptime(seconds=None):
    seconds = int(seconds if seconds is not None else (time.time() - START_TIME))
    d, rem = divmod(max(0, seconds), 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d: return f"{d}d {h}h {m}m"
    if h: return f"{h}h {m}m {s}s"
    if m: return f"{m}m {s}s"
    return f"{s}s"

def hub_home_text(extra=""):
    body = (
        f"  🟢 {stylish('status')}     online\n"
        f"  🎭 {stylish('mood')}       `{get_mode()}`\n"
        f"  📦 {stylish('version')}    `{CORE_VERSION}`\n"
        f"  ⏱️  {stylish('uptime')}     `{fmt_uptime()}`\n"
        f"  ⌨️  {stylish('prefix')}     `.`\n"
        f"  🎧 {stylish('active vc')}  `{len(active_calls)}`\n"
        "\n"
        f"  〔 01 〕  🎵 {stylish('music deck')}\n"
        f"  〔 02 〕  🎛️  {stylish('player + queue')}\n"
        f"  〔 03 〕  🛠️  {stylish('tools')}\n"
        f"  〔 04 〕  🎮 {stylish('fun')}\n"
    )
    if extra:
        body += "\n" + extra
    return core_card("userbot system", body, stylish("tap a key  ·  .alive  ·  .help"))

def page_music_text():
    body = (
        f"{stylish('PLAY')}\n"
        f"  `.play <song>`   🎵 search karke play\n"
        f"  `.nplay <song>`  🎧 natural sound\n"
        f"\n{stylish('CONTROL')}\n"
        f"  `.pause` `.resume` `.skip` `.stop`\n"
        f"  `.queue` `.now` `.volume`\n"
        f"\n{stylish('LOOP')}\n"
        f"  `.loop` `.repeat`\n"
        f"\n{stylish('SOUND (0-100)')}\n"
        f"  `.loud` `.gain` `.boost` `.eco` `.bass` `.treble` `.mix` `.max`\n"
    )
    return core_card("music deck", body, stylish(".play kesariya"))

def page_tools_text():
    body = (
        f"  `.alive` `.ping` `.status`\n"
        f"  `.id` `.chatid` `.whois`\n"
        f"  `.time` `.sysinfo`\n"
        f"\n{stylish('PROFILE')}\n"
        f"  `.setname` `.setbio` `.setpp` `.getpp`\n"
        f"\n{stylish('UTIL')}\n"
        f"  `.tts` `.qr` `.calc` `.weather`\n"
        f"  `.fancy` `.md5` `.password`\n"
        f"\n{stylish('SUPER')}\n"
        f"  `.lyrics` `.radio` `.speed` `.nightcore` `.bass`\n"
        f"  `.seek` `.gban` `.restart` `.backup` `.clean`\n"
    )
    return core_card("toolkit", body, stylish("sab kuch ek jagah"))

def page_fun_text():
    body = (
        f"  `.flip` `.dice` `.8ball`\n"
        f"  `.rps` `.joke` `.choose`\n"
        f"  `.truth` `.dare`\n"
    )
    return core_card("fun deck", body, stylish("just for the room"))

def page_admin_text():
    body = (
        f"{stylish('OWNER + ADMIN')}\n"
        f"  `.admin` (reply)  👑 admin banao\n"
        f"  `.admin add <id|@user>`  👑\n"
        f"  `.rm <id>` / `.rm all`  🗑️\n"
        f"  `.alist`           📋 admin list\n"
        f"  `.public` / `.private`  🎭\n"
        f"\n{stylish('GROUP')}\n"
        f"  `.ban` `.unban` `.kick` `.purge`\n"
        f"  `.promote` `.demote` `.pin` `.unpin`\n"
    )
    return core_card("control room", body, stylish("admins / owner"))

def hub_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("♪  Music", callback_data="hub_music"),
         InlineKeyboardButton("🎛  Player", callback_data="cb_panel")],
        [InlineKeyboardButton("🛠  Tools", callback_data="hub_tools"),
         InlineKeyboardButton("🎮  Fun", callback_data="hub_fun")],
        [InlineKeyboardButton("📖  Help", callback_data="hub_help"),
         InlineKeyboardButton("✦  Alive", callback_data="hub_alive")],
        [InlineKeyboardButton("ℹ  About", callback_data="hub_about")],
    ])

def fmt_duration(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h: return f"{h}h {m}m {s}s"
    if m: return f"{m}m {s}s"
    return f"{s}s"

def _fmt_mss(seconds):
    seconds = int(seconds or 0)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h: return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"

def fmt_vol(vol):
    return f"{vol}%"

def fmt_size(n):
    n = int(n or 0)
    if n >= 1024*1024*1024: return f"{n/(1024*1024*1024):.1f} GB"
    if n >= 1024*1024: return f"{n/(1024*1024):.1f} MB"
    if n >= 1024: return f"{n/1024:.1f} KB"
    return f"{n} B"

def is_url(text):
    return bool(re.match(r"^https?://", (text or "").strip()))

def _song_dict(info):
    return {
        "title": info.get("title") or "Unknown",
        "duration": info.get("duration") or 0,
        "uploader": info.get("uploader") or info.get("channel") or "YouTube",
        "webpage_url": info.get("webpage_url"),
        "direct_url": info.get("url") or info.get("webpage_url"),
        "thumbnail": info.get("thumbnail") or "",
        "source": "youtube",
    }

SOURCE_TAG = {"saavn": "🎧", "youtube": "📺", "soundcloud": "🟠",
              "spotify": "🟢", "deezer": "🔴"}

def search_results_text(results, query, natural=False):
    head_icon = "🎧" if natural else "🎵"
    head_title = stylish("Natural Search") if natural else stylish("Search Results")
    text = (
        f"{head_icon} 『{BRAND}』 — {head_title}\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔎 {stylish('Query')}: `{query}`\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    for i, s in enumerate(results):
        tag = SOURCE_TAG.get(s.get("source"), "🎵")
        text += f"{i+1}. {tag} **{s.get('title', 'Unknown')}**\n"
        bits = []
        if s.get("uploader"): bits.append(f"👤 {s.get('uploader')}")
        if s.get("album"): bits.append(f"💿 {s.get('album')}")
        if s.get("year"): bits.append(f"📅 {s.get('year')}")
        if s.get("quality"): bits.append(f"🔊 {s.get('quality')}")
        bits.append(f"⏱️ {_fmt_mss(s.get('duration', 0))}")
        text += "    " + " · ".join(bits) + "\n"
    text += (
        "\n👇 " + (stylish("Select — natural sound me play hoga") if natural else stylish("Select a song")) + ":\n"
        f"🔢 {stylish('reply karo')} `1` · `1,3,5` · `1-3` · `all` {stylish('ya button dabao')}\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"   ✦ {BRAND} ✦"
    )
    return text

# ==================== YOUTUBE / SEARCH ====================
COOKIES_FILE = "cookies.txt"

def _try_resolve(query):
    attempts = []
    if os.path.exists(COOKIES_FILE):
        attempts = [None]
    else:
        attempts = [
            {"youtube": {"player_client": ["android", "android_vr"]}},
            {"youtube": {"player_client": ["tv_embedded", "web_embedded"]}},
            {"youtube": {"player_client": ["ios"]}},
            None,
        ]
    last_err = None
    for ea in attempts:
        try:
            opts = {
                "format": "bestaudio/best",
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "default_search": "auto",
                "skip_download": True,
                "extractor_retries": 2,
                "retries": 2,
                "socket_timeout": 15,
                "nocheckcertificate": True,
            }
            if os.path.exists(COOKIES_FILE):
                opts["cookiefile"] = COOKIES_FILE
            if ea:
                opts["extractor_args"] = ea
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(query, download=False)
            return info
        except Exception as e:
            last_err = e
            continue
    raise last_err if last_err else RuntimeError("yt-dlp failed")

def resolve_song(query):
    if not is_url(query):
        query = f"ytsearch1:{query}"
    info = _try_resolve(query)
    if info.get("entries"):
        info = info["entries"][0]
    if not info or not info.get("webpage_url"):
        return None
    return _song_dict(info)

def resolve_songs(query, limit=5):
    if not is_url(query):
        query = f"ytsearch{limit}:{query}"
    info = _try_resolve(query)
    entries = info.get("entries", []) if info else []
    return [_song_dict(e) for e in entries if e and e.get("webpage_url")]

# ==================== JIOSAAVN ====================
SAAVN_API = "https://www.jiosaavn.com/api.php"
SAAVN_FALLBACK = "https://saavnapi-nine.vercel.app/result/"
SAAVN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://www.jiosaavn.com/",
    "Accept": "application/json, text/plain, */*",
}

async def saavn_auth_url(enc_url, bitrate=320):
    if not enc_url:
        return None
    timeout = aiohttp.ClientTimeout(total=20)
    params = {
        "__call": "song.generateAuthToken", "url": enc_url, "bitrate": str(bitrate),
        "api_version": "4", "_format": "json", "ctx": "web6dot0", "_marker": "0",
    }
    try:
        async with aiohttp.ClientSession(headers=SAAVN_HEADERS) as session:
            async with session.get(SAAVN_API, params=params, timeout=timeout) as resp:
                data = await resp.json(content_type=None)
        return (data or {}).get("auth_url")
    except Exception as e:
        logger.error("Saavn auth token error: %s", e)
        return None

def _saavn_duration(r):
    mi = r.get("more_info") if isinstance(r.get("more_info"), dict) else {}
    raw = (mi or {}).get("duration") or r.get("duration") or 0
    try:
        return int(float(raw))
    except Exception:
        return 0

def _saavn_direct_url(r):
    for key in ("media_url", "download_url", "url"):
        v = r.get(key)
        if isinstance(v, str) and v.startswith("http"):
            return v
    return None

async def _saavn_nine_raw(query):
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(headers=SAAVN_HEADERS) as session:
        async with session.get(SAAVN_FALLBACK, params={"query": query}, timeout=timeout) as resp:
            data = await resp.json(content_type=None)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("result") or data.get("results") or []
    return []

async def saavn_search(query, limit=5):
    timeout = aiohttp.ClientTimeout(total=25)
    songs = []
    try:
        raw = await _saavn_nine_raw(query)
        for r in (raw or [])[:limit]:
            if not isinstance(r, dict):
                continue
            enc = r.get("encrypted_media_url")
            direct = _saavn_direct_url(r)
            if enc and not direct:
                try:
                    direct = await saavn_auth_url(enc)
                except Exception:
                    direct = None
            if not direct:
                continue
            songs.append({
                "title": r.get("song") or r.get("name") or r.get("title") or "Unknown",
                "duration": _saavn_duration(r),
                "uploader": r.get("singers") or r.get("primary_artists") or r.get("artist") or "JioSaavn",
                "album": r.get("album") or "",
                "year": str(r.get("year") or ""),
                "quality": "320kbps" if r.get("320kbps") else (r.get("quality") or ""),
                "webpage_url": r.get("perma_url") or direct,
                "direct_url": direct,
                "enc_url": enc,
                "thumbnail": r.get("image") or r.get("thumbnail") or "",
                "source": "saavn",
            })
        if songs:
            return songs
    except Exception as e:
        logger.error("Saavn nine search error: %s", e)
    try:
        params = {
            "__call": "search.getResults", "_format": "json", "_marker": "0",
            "cc": "in", "p": "1", "n": str(max(int(limit), 5)), "q": query,
            "api_version": "4", "ctx": "web6dot0",
        }
        async with aiohttp.ClientSession(headers=SAAVN_HEADERS) as session:
            async with session.get(SAAVN_API, params=params, timeout=timeout) as resp:
                data = await resp.json(content_type=None)
        raw = []
        if isinstance(data, dict):
            raw = data.get("results") or []
            if not raw and isinstance(data.get("data"), dict):
                raw = data["data"].get("results") or []
        for r in (raw or [])[:limit]:
            if not isinstance(r, dict):
                continue
            mi = r.get("more_info") if isinstance(r.get("more_info"), dict) else {}
            enc = r.get("encrypted_media_url") or (mi or {}).get("encrypted_media_url")
            if not enc:
                continue
            auth = await saavn_auth_url(enc)
            if not auth:
                continue
            songs.append({
                "title": r.get("title") or r.get("song") or "Unknown",
                "duration": _saavn_duration(r),
                "uploader": r.get("primary_artists") or r.get("singers") or r.get("artist") or "JioSaavn",
                "album": (mi or {}).get("album") or "",
                "year": str((mi or {}).get("year") or ""),
                "quality": "320kbps" if (mi or {}).get("320kbps") else ((mi or {}).get("quality") or ""),
                "webpage_url": r.get("perma_url") or r.get("url") or auth or "",
                "direct_url": auth,
                "enc_url": enc,
                "thumbnail": r.get("image") or "",
                "source": "saavn",
            })
    except Exception as e:
        logger.error("Saavn official search error: %s", e)
    return songs

# ==================== SPOTIFY ====================
SPOTIFY_TOKEN = {"token": None, "expires": 0}

def spotify_enabled():
    return bool(SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET)

async def spotify_get_token():
    if SPOTIFY_TOKEN["token"] and time.time() < SPOTIFY_TOKEN["expires"] - 60:
        return SPOTIFY_TOKEN["token"]
    if not spotify_enabled():
        return None
    try:
        import base64 as _b64
        cred = _b64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession() as s:
            async with s.post(
                "https://accounts.spotify.com/api/token",
                data={"grant_type": "client_credentials"},
                headers={"Authorization": f"Basic {cred}", "Content-Type": "application/x-www-form-urlencoded"},
                timeout=timeout,
            ) as r:
                data = await r.json(content_type=None)
        tok = data.get("access_token")
        if tok:
            SPOTIFY_TOKEN["token"] = tok
            SPOTIFY_TOKEN["expires"] = time.time() + int(data.get("expires_in", 3600))
        return tok
    except Exception as e:
        logger.error("Spotify token error: %s", e)
        return None

def _sp_track_to_song(t):
    name = t.get("name") or "Unknown"
    artists = ", ".join(a.get("name", "") for a in (t.get("artists") or [])) or "Spotify"
    album = (t.get("album") or {}).get("name", "")
    images = (t.get("album") or {}).get("images") or []
    dur = int((t.get("duration_ms") or 0) / 1000)
    release = (t.get("album") or {}).get("release_date", "") or ""
    return {
        "title": name, "duration": dur, "uploader": artists, "album": album,
        "year": release[:4], "quality": "Spotify",
        "webpage_url": (t.get("external_urls") or {}).get("spotify") or "",
        "thumbnail": images[0].get("url", "") if images else "",
        "source": "spotify",
        "resolve_query": f"{name} {artists.split(',')[0]}",
    }

async def spotify_search(query, limit=3):
    try:
        tok = await spotify_get_token()
        if not tok:
            return []
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "https://api.spotify.com/v1/search",
                params={"q": query, "type": "track", "limit": str(max(int(limit), 3))},
                headers={"Authorization": f"Bearer {tok}"},
                timeout=timeout,
            ) as r:
                data = await r.json(content_type=None)
        items = ((data or {}).get("tracks") or {}).get("items") or []
        songs = [_sp_track_to_song(t) for t in items if isinstance(t, dict)]
        return songs[:limit]
    except Exception as e:
        logger.error("Spotify search error: %s", e)
        return []

SPOTIFY_LINK_RE = re.compile(r"open\.spotify\.com/(track|album|playlist)/([A-Za-z0-9]+)")

async def spotify_expand(query):
    m = SPOTIFY_LINK_RE.search(query or "")
    if not m:
        return None
    tok = await spotify_get_token()
    if not tok:
        raise RuntimeError("spotify_not_configured")
    kind, sid = m.group(1), m.group(2)
    timeout = aiohttp.ClientTimeout(total=30)
    headers = {"Authorization": f"Bearer {tok}"}
    items = []
    try:
        async with aiohttp.ClientSession() as s:
            if kind == "track":
                async with s.get(f"https://api.spotify.com/v1/tracks/{sid}",
                                 headers=headers, timeout=timeout) as r:
                    data = await r.json(content_type=None)
                items = [data] if data and data.get("name") else []
            else:
                url = f"https://api.spotify.com/v1/{kind}s/{sid}/tracks?limit=50"
                while url:
                    async with s.get(url, headers=headers, timeout=timeout) as r:
                        data = await r.json(content_type=None)
                    if kind == "playlist":
                        chunk = [(i.get("track") or {}) for i in (data.get("items") or [])]
                    else:
                        chunk = data.get("items") or []
                    items.extend(c for c in chunk if isinstance(c, dict) and c.get("name"))
                    url = data.get("next") or None
        songs = [_sp_track_to_song(t) for t in items]
        return songs
    except Exception as e:
        logger.error("Spotify expand error: %s", e)
        return []

# ==================== DEEZER ====================
DEEZER_API = "https://api.deezer.com"
DEEZER_LINK_RE = re.compile(r"deezer\.com/(?:[a-z]{2}/)?(track|album|playlist)/(\d+)")

def _dz_track_to_song(t):
    artist = (t.get("artist") or {}).get("name", "") if isinstance(t.get("artist"), dict) else ""
    album = (t.get("album") or {}).get("title", "") if isinstance(t.get("album"), dict) else ""
    cover = (t.get("album") or {}).get("cover_big", "") if isinstance(t.get("album"), dict) else ""
    title = t.get("title") or "Unknown"
    return {
        "title": title, "duration": int(t.get("duration") or 0),
        "uploader": artist or "Deezer", "album": album, "quality": "Deezer",
        "webpage_url": t.get("link") or "", "thumbnail": cover,
        "source": "deezer", "resolve_query": f"{title} {artist}",
    }

async def deezer_search(query, limit=3):
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{DEEZER_API}/search",
                             params={"q": query, "limit": str(max(int(limit), 3))},
                             timeout=timeout) as r:
                data = await r.json(content_type=None)
        items = ((data or {}).get("data") or []) if isinstance(data, dict) else []
        songs = [_dz_track_to_song(t) for t in items if isinstance(t, dict)]
        return songs[:limit]
    except Exception as e:
        logger.error("Deezer search error: %s", e)
        return []

async def deezer_expand(query):
    m = DEEZER_LINK_RE.search(query or "")
    if not m:
        return None
    kind, did = m.group(1), m.group(2)
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{DEEZER_API}/{kind}/{did}", timeout=timeout) as r:
                data = await r.json(content_type=None)
        if kind == "track":
            items = [data] if data and data.get("title") else []
        else:
            items = (((data or {}).get("tracks") or {}).get("data") or []) if isinstance(data, dict) else []
        songs = [_dz_track_to_song(t) for t in items if isinstance(t, dict)]
        return songs
    except Exception as e:
        logger.error("Deezer expand error: %s", e)
        return []

# ==================== SOUNDCLOUD ====================
def soundcloud_search(query, limit=2):
    try:
        opts = {
            "format": "bestaudio/best", "quiet": True, "no_warnings": True,
            "noplaylist": True, "skip_download": True, "extract_flat": True,
            "socket_timeout": 15, "extractor_retries": 2,
        }
        if os.path.exists(COOKIES_FILE):
            opts["cookiefile"] = COOKIES_FILE
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"scsearch{max(int(limit), 2)}:{query}", download=False)
        entries = (info or {}).get("entries") or []
        songs = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            songs.append({
                "title": e.get("title") or "Unknown",
                "duration": e.get("duration") or 0,
                "uploader": e.get("uploader") or e.get("channel") or "SoundCloud",
                "webpage_url": e.get("url") or e.get("webpage_url") or "",
                "thumbnail": e.get("thumbnail") or "",
                "source": "soundcloud",
            })
        return songs[:limit]
    except Exception as e:
        logger.error("SoundCloud search error: %s", e)
        return []

def _sync_play_loop(q):
    total = (1 if q.current is not None else 0) + len(q.songs)
    if total <= 1:
        q.repeat = True
        q.loop = False
    else:
        q.loop = True
        q.repeat = False
    q.silent_loop = True

async def _queue_songs(message, songs, natural=False, label=""):
    chat_id = message.chat.id
    q = get_queue(chat_id)
    if natural:
        for s in songs:
            s["normal"] = True
    q.songs.extend(songs)
    _sync_play_loop(q)
    if not q.current:
        q.current = q.songs.popleft()
        active_calls.add(chat_id)
        await play_song(chat_id, q.current)
    extra = f" +{len(songs) - 1} more" if len(songs) > 1 else ""
    await message.reply_text(
        f"✅ 『{BRAND}』 — {stylish('Added')}: {len(songs)} {stylish('tracks')}"
        + (f" · {label}" if label else "") + "\n"
        f"🎵 {songs[0]['title']}{extra}"
    )

async def _play_picked_songs(message, sid, indices, natural=False):
    results = search_cache.get(sid)
    if not results:
        await message.reply_text("❌ Search expired — dobara `.play <song>` karo.")
        return False, 0, None
    q = get_queue(message.chat.id)
    songs = []
    for i in indices:
        if 0 <= i < len(results):
            s = results[i]
            if natural:
                s["normal"] = True
            songs.append(s)
    if not songs:
        await message.reply_text(
            f"❌ {stylish('Invalid number')} — {stylish('Valid')}: `1`–`{len(results)}`"
        )
        return False, 0, None
    started = False
    q.songs.extend(songs)
    _sync_play_loop(q)
    if not q.current:
        q.current = q.songs.popleft()
        active_calls.add(message.chat.id)
        started = await play_song(message.chat.id, q.current)
        if not started:
            q.current = None
            return False, 0, None
    return started, len(songs), songs[0].get("title", "")

# ==================== FFMPEG / STREAM BUILDING ====================
def _atempo_chain(speed):
    speed = float(speed)
    if speed <= 0.1:
        return "atempo=1.0"
    parts = []
    remaining = speed
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    parts.append(f"atempo={remaining:.3f}")
    return ",".join(parts)

def _loudspeaker_filter(level):
    lvl = max(0, min(100, int(level or 0)))
    vol = round(0.8 + (lvl / 100.0) * 6.2, 2)
    g60 = round((lvl / 100.0) * 4, 1)
    g120 = round((lvl / 100.0) * 3.5, 1)
    g250 = round(1 + (lvl / 100.0) * 3, 1)
    g500 = round(2 + (lvl / 100.0) * 3, 1)
    g1k = round(3 + (lvl / 100.0) * 3, 1)
    g8k = round(2 + (lvl / 100.0) * 3, 1)
    g14k = round(1 + (lvl / 100.0) * 3, 1)
    return (
        "loudnorm=I=-7:TP=-0.3:LRA=11,"
        f"equalizer=f=60:t=q:w=1.5:g={g60},"
        f"equalizer=f=120:t=q:w=1.5:g={g120},"
        f"equalizer=f=250:t=q:w=1.5:g={g250},"
        f"equalizer=f=500:t=q:w=1.2:g={g500},"
        f"equalizer=f=1000:t=q:w=1:g={g1k},"
        f"equalizer=f=8000:t=q:w=1:g={g8k},"
        f"equalizer=f=14000:t=q:w=1:g={g14k},"
        "alimiter=limit=0.92:attack=0.1:release=1,"
        f"volume={vol}"
    )

def _ultra_boost_filter(level):
    lvl = max(0, min(100, int(level or 0)))
    vol = round(1.0 + (lvl / 100.0) * 8.0, 2)
    g60 = round((lvl / 100.0) * 6, 1)
    g120 = round((lvl / 100.0) * 5, 1)
    g250 = round(1 + (lvl / 100.0) * 4, 1)
    g500 = round(2 + (lvl / 100.0) * 5, 1)
    g1k = round(3 + (lvl / 100.0) * 5, 1)
    g8k = round(3 + (lvl / 100.0) * 4, 1)
    g14k = round(2 + (lvl / 100.0) * 3, 1)
    echo = round(0.1 + (lvl / 100.0) * 0.15, 2)
    return (
        "loudnorm=I=-5:TP=-0.2:LRA=11,"
        f"equalizer=f=60:t=q:w=1.5:g={g60},"
        f"equalizer=f=120:t=q:w=1.5:g={g120},"
        f"equalizer=f=250:t=q:w=1.5:g={g250},"
        f"equalizer=f=500:t=q:w=1.2:g={g500},"
        f"equalizer=f=1000:t=q:w=1:g={g1k},"
        f"equalizer=f=8000:t=q:w=1:g={g8k},"
        f"equalizer=f=14000:t=q:w=1:g={g14k},"
        f"aecho=0.6:0.7:40:{echo},"
        f"aecho=0.6:0.7:400:{round(echo * 0.7, 2)},"
        "alimiter=limit=0.88:attack=0.1:release=1,"
        f"volume={vol}"
    )

def _echo_filter(level):
    lvl = max(0, min(100, int(level or 0)))
    g1 = round((lvl / 100.0) * 0.5, 2)
    g2 = round(g1 * 0.8, 2)
    g3 = round(g1 * 0.6, 2)
    g4 = round(g1 * 0.45, 2)
    return (
        f"aecho=0.7:0.8:40:{g1},"
        f"aecho=0.6:0.7:200:{g2},"
        f"aecho=0.5:0.6:400:{g3},"
        f"aecho=0.4:0.5:800:{g4}"
    )

def _bass_filter(level):
    lvl = max(0, min(100, int(level or 0)))
    g = round((lvl / 100.0) * 20, 1)
    if g <= 0:
        return None
    return f"bass=g={g}:f=80:w=0.8"

def _treble_filter(level):
    lvl = max(0, min(100, int(level or 0)))
    g = round((lvl / 100.0) * 12, 1)
    if g <= 0:
        return None
    return f"equalizer=f=8000:t=q:w=1:g={g}"

def make_ffmpeg_params(boost_level=0, eco_level=0, loudspeaker_level=0, bass=0, treble=0,
                       loop=False, seek=None, speed=1.0, nightcore=False):
    filters = []
    if loudspeaker_level:
        filters.append(_loudspeaker_filter(loudspeaker_level))
    if boost_level:
        filters.append(_ultra_boost_filter(boost_level))
    if eco_level:
        filters.append(_echo_filter(eco_level))
    _b = _bass_filter(bass)
    if _b:
        filters.append(_b)
    _t = _treble_filter(treble)
    if _t:
        filters.append(_t)
    if nightcore:
        filters.append("asetrate=44100*1.35,aresample=44100,atempo=1.35")
    else:
        try:
            _sp = float(speed or 1.0)
        except Exception:
            _sp = 1.0
        if _sp != 1.0:
            filters.append(_atempo_chain(_sp))
    first = "-stream_loop -1" if loop else ""
    seek_pre = f"-ss {int(seek)} " if (seek and int(seek) > 0) else ""
    if filters:
        return f"{seek_pre}{first} -filter:a {','.join(filters)}"
    if loop:
        return f"{seek_pre}-stream_loop -1"
    if seek_pre:
        return seek_pre.strip()
    return None

def build_stream(source, boost_level=0, eco_level=0, loudspeaker_level=0, bass=0, treble=0,
                  loop=False, seek=None, speed=1.0, nightcore=False, headers=None):
    return MediaStream(
        source,
        audio_parameters=AudioQuality.HIGH,
        video_flags=MediaStream.Flags.IGNORE,
        ffmpeg_parameters=make_ffmpeg_params(boost_level, eco_level, loudspeaker_level, bass, treble,
                                             loop=loop, seek=seek, speed=speed, nightcore=nightcore),
        headers=headers,
    )

# ==================== DOWNLOAD / PLAYBACK ====================
YT_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/|/v/)([A-Za-z0-9_-]{11})")

def extract_youtube_id(query):
    m = YT_ID_RE.search(query or "")
    return m.group(1) if m else None

INVIDIOUS_INSTANCES = [
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://yewtu.be",
    "https://invidious.f5.si",
]

async def invidious_stream_url(video_id):
    if not video_id:
        return None
    for base in INVIDIOUS_INSTANCES:
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{base}/api/v1/videos/{video_id}", timeout=timeout) as r:
                    data = await r.json(content_type=None)
            fmts = (data or {}).get("adaptiveFormats") or (data or {}).get("formatStreams") or []
            best = None
            for f in fmts:
                if not isinstance(f, dict):
                    continue
                if "audio" in (f.get("type") or ""):
                    if best is None or int(f.get("bitrate") or 0) > int(best.get("bitrate") or 0):
                        best = f
            if best and best.get("url"):
                return best["url"]
        except Exception as e:
            logger.warning("Invidious %s fail: %s", base, e)
            continue
    return None

async def download_http_audio(url):
    path = os.path.join(TEMP_DIR, f"inv_{uuid.uuid4().hex}.m4a")
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=timeout) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            with open(path, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 256):
                    f.write(chunk)
    if not os.path.exists(path) or os.path.getsize(path) < 1024:
        raise RuntimeError("download empty")
    return path

async def _search_guard(coro, seconds=15):
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except Exception as e:
        logger.warning("search guarded (%ss): %s", seconds, e)
        return []

async def _fetch_youtube_audio(query, title, chat_id, max_wait=100):
    try:
        local = await asyncio.wait_for(
            asyncio.to_thread(download_youtube_file, query), timeout=max_wait)
        if local and os.path.exists(local) and os.path.getsize(local) > 1024:
            return local, "youtube"
    except Exception as e:
        logger.warning("yt-dlp download fail (%s) -> fallback chain", e)

    if not query.startswith("ytsearch"):
        vid = extract_youtube_id(query)
        if vid:
            try:
                url = await asyncio.wait_for(invidious_stream_url(vid), timeout=40)
                if url:
                    local = await asyncio.wait_for(download_http_audio(url), timeout=90)
                    if local:
                        return local, "invidious"
            except Exception as e:
                logger.warning("Invidious fallback fail: %s", e)

    if title:
        try:
            hits = await asyncio.wait_for(saavn_search(title, limit=1), timeout=25)
            if hits and hits[0].get("direct_url"):
                local = await asyncio.wait_for(
                    download_saavn_file(hits[0]["direct_url"]), timeout=90)
                if local:
                    return local, "saavn"
        except Exception as e:
            logger.warning("Saavn fallback fail: %s", e)
    raise RuntimeError("All download sources failed — .ytupdate karke retry karo")

def download_youtube_file(query):
    path = os.path.join(TEMP_DIR, f"yt_{uuid.uuid4().hex}.m4a")
    opts = {
        "format": "bestaudio/best", "outtmpl": path,
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "extractor_retries": 2, "retries": 2, "socket_timeout": 20,
        "nocheckcertificate": True,
    }
    if os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    else:
        opts["extractor_args"] = {
            "youtube": {"player_client": ["android", "android_vr", "tv_embedded", "web_embedded"]}
        }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(query, download=True)
    return path

async def download_saavn_file(url):
    path = os.path.join(TEMP_DIR, f"saavn_{uuid.uuid4().hex}.m4a")
    timeout = aiohttp.ClientTimeout(total=180)
    async with aiohttp.ClientSession(headers=SAAVN_HEADERS) as session:
        async with session.get(url, timeout=timeout, allow_redirects=True) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Saavn download HTTP {resp.status}")
            with open(path, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 256):
                    f.write(chunk)
    if not os.path.exists(path) or os.path.getsize(path) < 1024:
        raise RuntimeError("Saavn download empty / too small")
    return path

def _is_temp_song(song):
    return bool(song and song.get("temp"))

def _delete_temp_song_files(song):
    if not _is_temp_song(song):
        return
    for key in ("_file", "local_path"):
        p = song.get(key)
        if not p:
            continue
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass

async def _build_stream_for(song, q, seek=None):
    src = song.get("source", "youtube")
    normal = bool(song.get("normal"))
    do_loop = bool(q.repeat)
    speed = float(getattr(q, "speed", 1.0) or 1.0)
    nightcore = bool(getattr(q, "nightcore", False))
    bass = int(getattr(q, "bass", 0) or 0)
    treble = int(getattr(q, "treble", 0) or 0)
    loudspeaker_level = 0
    if not normal:
        if int(getattr(q, "loud_level", 0) or 0) > 0:
            loudspeaker_level = int(q.loud_level)
        elif int(getattr(q, "gain_level", 0) or 0) > 0:
            loudspeaker_level = int(q.gain_level)
        elif song.get("loudspeaker"):
            loudspeaker_level = 100
    boost_level = int(getattr(q, "boost_level", 0) or 0) if not normal else 0
    eco_level = int(getattr(q, "eco_level", 0) or 0) if not normal else 0

    existing = song.get("local_path") or song.get("_file")
    if existing and os.path.exists(existing):
        song["_file"] = existing
        song["local_path"] = existing
        return build_stream(existing, loop=do_loop, seek=seek, speed=speed, nightcore=nightcore,
                            bass=bass, treble=treble, loudspeaker_level=loudspeaker_level,
                            boost_level=boost_level, eco_level=eco_level)

    if src in ("spotify", "deezer"):
        rq = song.get("resolve_query") or f"{song.get('title', '')} {song.get('uploader', '')}"
        local, via = await _fetch_youtube_audio(
            f"ytsearch1:{rq}", song.get("title", ""), 0)
        song["temp"] = not normal
        song["_file"] = local
        song["local_path"] = local
        return build_stream(local, loop=do_loop, seek=seek, speed=speed, nightcore=nightcore,
                            bass=bass, treble=treble, loudspeaker_level=loudspeaker_level,
                            boost_level=boost_level, eco_level=eco_level)

    if src == "soundcloud":
        query = song.get("webpage_url") or song.get("direct_url")
        if not query:
            raise RuntimeError("SoundCloud URL missing")
        local = await asyncio.wait_for(
            asyncio.to_thread(download_youtube_file, query), timeout=120)
        song["temp"] = not normal
        song["_file"] = local
        song["local_path"] = local
        return build_stream(local, loop=do_loop, seek=seek, speed=speed, nightcore=nightcore,
                            bass=bass, treble=treble, loudspeaker_level=loudspeaker_level,
                            boost_level=boost_level, eco_level=eco_level)

    if src == "saavn":
        direct = song.get("direct_url")
        enc = song.get("enc_url")
        if enc:
            fresh = await saavn_auth_url(enc)
            if fresh:
                direct = fresh
                song["direct_url"] = fresh
        if not direct:
            raise RuntimeError("Saavn stream URL missing")
        local = await asyncio.wait_for(download_saavn_file(direct), timeout=120)
        song["temp"] = not normal
        song["_file"] = local
        song["local_path"] = local
        return build_stream(local, loop=do_loop, seek=seek, speed=speed, nightcore=nightcore,
                            bass=bass, treble=treble, loudspeaker_level=loudspeaker_level,
                            boost_level=boost_level, eco_level=eco_level)

    if src == "radio":
        url = song.get("direct_url")
        if not url:
            raise RuntimeError("Radio stream URL missing")
        return build_stream(url, loop=False, seek=seek, speed=speed, nightcore=nightcore,
                            bass=bass, treble=treble, loudspeaker_level=loudspeaker_level,
                            boost_level=boost_level, eco_level=eco_level)

    query = song.get("webpage_url") or song.get("direct_url")
    local, via = await _fetch_youtube_audio(query, song.get("title", ""), 0)
    song["temp"] = not normal
    song["_file"] = local
    song["local_path"] = local
    return build_stream(local, loop=do_loop, seek=seek, speed=speed, nightcore=nightcore,
                        bass=bass, treble=treble, loudspeaker_level=loudspeaker_level,
                        boost_level=boost_level, eco_level=eco_level)

def _is_join_error(e):
    s = str(e)
    return ("GROUPCALL" in s.upper()) or ("JOINGROUPCALL" in s.upper()) or ("NOT_IN_CALL" in s.upper())

async def play_song(chat_id, song, announce=True, silent=False, seek=None):
    q = get_queue(chat_id)
    if not vc_ready():
        active_calls.discard(chat_id)
        if not silent:
            await _engine_down_reply_play(chat_id)
        return False

    st = None
    if announce and not silent and not (song.get("local_path") and os.path.exists(song.get("local_path") or "")):
        try:
            st = await music.send_message(chat_id, f"⏳ 『{BRAND}』 — {stylish('Downloading audio')}…")
        except Exception:
            st = None

    try:
        song["chat_id"] = chat_id
        stream = await _build_stream_for(song, q, seek=seek)
        current_files[chat_id] = song.get("_file") or song.get("local_path")

        last_err = None
        for attempt in range(4):
            try:
                await vc.play(chat_id, stream)
                last_err = None
                break
            except NoActiveGroupCall:
                raise
            except Exception as e:
                last_err = e
                if _is_join_error(e):
                    logger.warning("Join error (attempt %d): %s", attempt + 1, e)
                    try:
                        await vc.leave_call(chat_id)
                    except Exception:
                        pass
                    active_calls.discard(chat_id)
                    await asyncio.sleep(3)
                    continue
                raise
        if last_err is not None:
            raise last_err

        _reset_timer(chat_id, seek)

        if _vol_auto.get(chat_id, True):
            q.volume = AUTO_MAX_VOLUME
        try:
            await vc.change_volume_call(chat_id, q.volume)
            _last_vol_applied[chat_id] = q.volume
        except Exception:
            pass
        END_GUARD.pop(chat_id, None)
        db.bump("total_played")
        err_notified.discard(chat_id)
        logger.info("Now playing: %s", song.get("title"))
        if announce and not silent:
            try:
                np = now_playing_text(song)
                kb = player_panel(q)
                if st is not None:
                    await st.edit_text(np, reply_markup=kb)
                else:
                    await music.send_message(chat_id, np, reply_markup=kb)
            except Exception:
                pass
        return True
    except NoActiveGroupCall:
        active_calls.discard(chat_id)
        current_files.pop(chat_id, None)
        _delete_temp_song_files(song)
        if st is not None:
            try:
                await st.delete()
            except Exception:
                pass
        if not silent:
            try:
                await music.send_message(
                    chat_id,
                    f"❌ 『{BRAND}』 — {stylish('No active voice chat')}\n\n"
                    "Start a **voice chat** in this group first.\n"
                    "Make sure this account is **admin** in the group.\n\n"
                    "👉 Then run `.play <song>` again.",
                )
            except Exception:
                pass
        return False
    except Exception as e:
        logger.error("Play error: %s", e)
        active_calls.discard(chat_id)
        current_files.pop(chat_id, None)
        _delete_temp_song_files(song)
        if st is not None:
            try:
                await st.delete()
            except Exception:
                pass
        if not silent:
            try:
                await music.send_message(
                    chat_id,
                    f"❌ 『{BRAND}』 — {stylish('Playback failed')}\n"
                    f"`{e}`\n\n"
                    f"👉 Run `.diag` to check the problem.\n"
                    f"💡 .ytupdate karke retry karo.\n",
                )
            except Exception:
                pass
        return False

async def reapply_current_effects(chat_id):
    q = get_queue(chat_id)
    if not q.current:
        return False
    pos = max(0, int(get_elapsed(chat_id)))
    dur = int(q.current.get("duration") or 0)
    if pos and dur and pos >= dur:
        if q.repeat or q.loop:
            pos = pos % dur
        else:
            pos = 0
    return await play_song(chat_id, q.current, announce=False, seek=pos)

async def _engine_down_reply_play(chat_id):
    try:
        await music.send_message(
            chat_id,
            f"❌ 『{BRAND}』 — {stylish('Voice engine not running')}\n\n"
            "Fix:\n"
            "`pip install \"pyrogram==2.0.106\" \"py-tgcalls[pyrogram]==2.2.5\"`\n"
            f"{stylish('then restart the bot.')}",
        )
    except Exception:
        pass

def now_playing_text(song):
    icon = "🎙️" if song.get("local_path") else "🎵"
    lines = [
        f"▶️ 『{BRAND}』 — {stylish('Now Playing')}",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"{icon} {song.get('title', 'Unknown')}",
        f"⏱️ {fmt_duration(song.get('duration', 0))}",
        f"👤 {song.get('uploader', 'DEV X CORE')}",
    ]
    if song.get("album"):
        line = f"💿 {song.get('album')}"
        if song.get("year"):
            line += f" · 📅 {song.get('year')}"
        lines.append(line)
    if song.get("quality"):
        lines.append(f"🔊 {song.get('quality')}")
    if song.get("normal"):
        lines.append(f"🎧 {stylish('natural sound — no effects')}")
    return "\n".join(lines)

async def on_stream_end(pytgcalls_client, update: StreamEnded):
    if not vc_ready():
        return
    chat_id = update.chat_id
    q = get_queue(chat_id)
    if not q.current:
        return
    if q.repeat:
        if chat_id in replay_lock:
            return
        path = q.current.get("local_path") or q.current.get("_file")
        if path and not os.path.exists(path):
            q.repeat = False
            q.current = None
            current_files.pop(chat_id, None)
            active_calls.discard(chat_id)
            PLAY_TIMERS.pop(chat_id, None)
            if chat_id not in err_notified:
                err_notified.add(chat_id)
                try:
                    await music.send_message(
                        chat_id,
                        f"❌ 『{BRAND}』 — {stylish('Loop stopped')}\n"
                        f"{stylish('File is missing. Use .play again.')}",
                    )
                except Exception:
                    pass
            return
        replay_lock.add(chat_id)
        try:
            _pos = max(0, int(get_elapsed(chat_id)))
            ok = await play_song(chat_id, q.current, announce=False, silent=True, seek=_pos)
            if not ok and chat_id not in err_notified:
                err_notified.add(chat_id)
                try:
                    await music.send_message(
                        chat_id,
                        f"❌ 『{BRAND}』 — {stylish('Loop stopped')}\n"
                        f"{stylish('Check voice chat, then .play again.')}",
                    )
                except Exception:
                    pass
                q.repeat = False
                active_calls.discard(chat_id)
                PLAY_TIMERS.pop(chat_id, None)
        finally:
            replay_lock.discard(chat_id)
        return
    _elapsed = get_elapsed(chat_id)
    _dur = int(q.current.get("duration") or 0)
    _natural = False
    if _dur and _elapsed >= _dur - 3:
        _natural = True
    elif not _dur:
        _now_t = time.time()
        _g = END_GUARD.get(chat_id)
        if _g and (_now_t - _g["ts"]) < 60:
            _g["count"] += 1
        else:
            _g = {"count": 1, "ts": _now_t}
        END_GUARD[chat_id] = _g
        _natural = _g["count"] > 3
    if not _natural:
        _pos = max(0, int(_elapsed))
        await play_song(chat_id, q.current, announce=False, silent=True, seek=_pos)
        return
    finished = q.current
    requeued = False
    if q.loop and q.current:
        q.songs.append(q.current)
        requeued = True
    current_files.pop(chat_id, None)
    q.current = None
    if q.songs:
        q.current = q.songs.popleft()
        await play_song(chat_id, q.current, announce=not q.silent_loop)
        if not requeued and finished is not None:
            _delete_temp_song_files(finished)
    else:
        if finished is not None:
            _delete_temp_song_files(finished)
        active_calls.discard(chat_id)
        PLAY_TIMERS.pop(chat_id, None)

async def auto_enforce_loop():
    while True:
        await asyncio.sleep(ENFORCE_INTERVAL)
        if not vc_ready():
            continue
        for chat_id in list(active_calls):
            q = get_queue(chat_id)
            if AUTO_UNMUTE and not _muted_state.get(chat_id):
                try:
                    await vc.unmute(chat_id)
                except Exception:
                    pass
            if _last_vol_applied.get(chat_id) != q.volume:
                try:
                    await vc.change_volume_call(chat_id, q.volume)
                    _last_vol_applied[chat_id] = q.volume
                except Exception:
                    pass

# ==================== CLIENTS ====================
music = Client(
    SESSION_NAME,
    api_id=API_ID,
    api_hash=API_HASH,
    in_memory=True,
)

bot = Client(
    "login_helper_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
)

vc = None
music_started = False
login_state = {}

def vc_ready():
    return vc is not None and music_started

async def _engine_down_reply(message):
    try:
        await message.reply_text(
            f"❌ 『{BRAND}』 — {stylish('Voice engine not running')}\n\n"
            f"🔧 Fix:\n"
            "`pip install \"pyrogram==2.0.106\" \"py-tgcalls[pyrogram]==2.2.5\"`\n"
            f"{stylish('then restart the bot.')}",
        )
    except Exception:
        pass

def needs_vc(func):
    @functools.wraps(func)
    async def wrapper(client, message, *args, **kwargs):
        if not vc_ready():
            await _engine_down_reply(message)
            return
        return await func(client, message, *args, **kwargs)
    return wrapper

# ==================== KEYBOARDS ====================
def player_panel(q=None):
    q = q or MusicQueue()
    loop_txt = "Loop ON" if q.loop else "Loop OFF"
    rep_txt = "Repeat ON" if q.repeat else "Repeat OFF"
    boost_txt = f"Boost {q.boost_level}%" if q.boost_level else "Boost OFF"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏸", callback_data="cb_pause"),
         InlineKeyboardButton("▶", callback_data="cb_resume"),
         InlineKeyboardButton("⏭", callback_data="cb_skip"),
         InlineKeyboardButton("⏹", callback_data="cb_stop")],
        [InlineKeyboardButton(loop_txt, callback_data="cb_loop"),
         InlineKeyboardButton(rep_txt, callback_data="cb_repeat"),
         InlineKeyboardButton(boost_txt, callback_data="cb_boost")],
        [InlineKeyboardButton("Vol −", callback_data="cb_voldown"),
         InlineKeyboardButton("Mute", callback_data="cb_mute"),
         InlineKeyboardButton("200%", callback_data="cb_vol200"),
         InlineKeyboardButton("Vol +", callback_data="cb_volup")],
        [InlineKeyboardButton("▶  Play", callback_data="cb_play"),
         InlineKeyboardButton("🎙  Voice", callback_data="cb_voice")],
        [InlineKeyboardButton("🔊 Sound", callback_data="cb_sound"),
         InlineKeyboardButton("Queue", callback_data="cb_queue"),
         InlineKeyboardButton("Now", callback_data="cb_now")],
        [InlineKeyboardButton("⌂  Hub", callback_data="cb_menu")],
    ])

def main_menu():
    return hub_keyboard()

def volume_panel(q):
    q = q or MusicQueue()
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Mute", callback_data="cb_mute"),
         InlineKeyboardButton("200%", callback_data="cb_vol200")],
        [InlineKeyboardButton("− 10", callback_data="cb_voldown"),
         InlineKeyboardButton("+ 10", callback_data="cb_volup")],
        [InlineKeyboardButton("10%", callback_data="cb_vol_10"),
         InlineKeyboardButton("50%", callback_data="cb_vol_50")],
        [InlineKeyboardButton("100%", callback_data="cb_vol_100"),
         InlineKeyboardButton("150%", callback_data="cb_vol_150")],
        [InlineKeyboardButton("←  Deck", callback_data="cb_panel")],
    ])

def owner_panel():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Stats", callback_data="cb_stats"),
         InlineKeyboardButton("Users", callback_data="cb_users")],
        [InlineKeyboardButton("Broadcast", callback_data="cb_broadcast")],
        [InlineKeyboardButton("👑 Add Admin", callback_data="cb_addadmin"),
         InlineKeyboardButton("🗑️ Del Admin", callback_data="cb_deladmin")],
        [InlineKeyboardButton("📋 Admin List", callback_data="cb_adminlist")],
        [InlineKeyboardButton("⌂  Hub", callback_data="cb_menu")],
    ])

def queue_panel(q, page=0, per_page=5):
    q = q or MusicQueue()
    songs = list(q.songs)
    total = len(songs)
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages - 1)
    start = page * per_page
    chunk = songs[start:start + per_page]

    body = ""
    if q.current:
        body += f"  ▸  now  **{q.current.get('title','?')}**\n\n"
    if not chunk:
        body += f"  {stylish('queue is empty.')}"
    for i, s in enumerate(chunk, start + 1):
        body += f"  `{i}.` {s.get('title','?')}  ·  {fmt_duration(s.get('duration',0))}\n"
    body += (
        f"\n  {stylish('page')} {page+1}/{pages}  ·  {total} {stylish('tracks')}\n"
        f"  loop {'on' if q.loop else 'off'}  ·  repeat {'on' if q.repeat else 'off'}  ·  {fmt_vol(q.volume)}"
    )
    text = core_card("queue", body)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"cb_qpage_{page-1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"cb_qpage_{page+1}"))
    rows = [nav] if nav else []
    rows.append([InlineKeyboardButton("🎛️ Panel", callback_data="cb_panel")])
    return text, InlineKeyboardMarkup(rows)

# ==================== MUSIC COMMAND HANDLERS ====================
@music.on_message(filters.command("join", prefixes=PREFIX))
@needs_vc
async def join_cmd(client, message):
    chat_id = message.chat.id
    if not await can_control(client, chat_id, message.from_user.id):
        await _deny_reply(message)
        return
    try:
        await vc.play(chat_id)
        active_calls.add(chat_id)
        await message.reply_text(f"✅ 『{BRAND}』 — {stylish('Joined voice chat!')}\n\nNow run `.play <song>`.")
    except NoActiveGroupCall:
        await message.reply_text(
            f"❌ {stylish('No voice chat found')}\n\n"
            "Start a **voice chat** in this group first, then run `.join` again."
        )
    except Exception as e:
        await message.reply_text(f"❌ Join failed: `{e}`\n\n👉 Make sure this account is **admin** in the group.")

@music.on_message(filters.command("diag", prefixes=PREFIX))
async def diag_cmd(client, message):
    import shutil
    chat_id = message.chat.id
    q = get_queue(chat_id)
    ffmpeg_ok = shutil.which("ffmpeg") is not None
    ffprobe_ok = shutil.which("ffprobe") is not None
    in_call = chat_id in active_calls
    cur = q.current
    admin_ok = await is_group_admin(client, chat_id, client.me.id if hasattr(client, 'me') else OWNER_ID)
    try:
        from importlib.metadata import version as _dist_version
        pt_version = _dist_version("py-tgcalls")
    except Exception:
        pt_version = "?"
    compat_ok, compat_msg = check_engine_compat()
    try:
        yt_version = yt_dlp.version.__version__
    except Exception:
        yt_version = "?"
    lines = [
        f"🩺 『{BRAND}』 — {stylish('Diagnostic')}",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"🔧 ffmpeg: {'✅ INSTALLED' if ffmpeg_ok else '❌ MISSING — run: sudo apt install -y ffmpeg'}",
        f"🔧 ffprobe: {'✅ INSTALLED' if ffprobe_ok else '❌ MISSING'}",
        f"🎵 Music bot: {'✅ RUNNING' if music_started else '❌ NOT STARTED'}",
        f"📞 In voice call: {'✅ YES' if in_call else '❌ NO — start a voice chat first!'}",
        f"🎶 Current song: {cur.get('title') if cur else '❌ NONE'}",
        f"👑 Account admin here: {'✅ YES' if admin_ok else '❌ NO — make bot admin to auto-join'}",
        f"🔗 pytgcalls: `{pt_version}` · {'✅ compatible' if compat_ok else '❌ INCOMPATIBLE'}",
        f"🎭 Mode: `{get_mode()}`",
        f"📦 yt-dlp: `{yt_version}`",
        "━━━━━━━━━━━━━━━━━━━━━",
    ]
    lines.append(f"💡 {stylish('yt-dlp purana ho to')}: `.ytupdate` ({stylish('owner')})")
    lines.append(f"💡 {stylish('Download fail? Bot khud fallback leta hai')}: yt-dlp → Invidious → Saavn")
    if not compat_ok:
        lines.append(f"💊 {stylish('Fix')}: `pip install \"pyrogram==2.0.106\" \"py-tgcalls[pyrogram]==2.2.5\"` then restart.")
    await message.reply_text("\n".join(lines))

@music.on_message(filters.command("start", prefixes=PREFIX) & filters.private)
async def start_cmd(client, message):
    u = message.from_user
    db.add_user(u.id, u.username, u.first_name, u.last_name or "")
    await message.reply_text(hub_home_text(f"  {stylish('welcome')}, **{u.first_name}**.\n  {stylish('add me to a group + start vc.')}"), reply_markup=hub_keyboard())

@music.on_message(filters.command("menu", prefixes=PREFIX))
async def menu_cmd(client, message):
    await message.reply_text(hub_home_text(), reply_markup=hub_keyboard())

@music.on_message(filters.command("panel", prefixes=PREFIX))
async def panel_cmd(client, message):
    await message.reply_text(
        core_card("player deck", stylish("transport  ·  loop  ·  boost  ·  volume")),
        reply_markup=player_panel(get_queue(message.chat.id)),
    )

@music.on_message(filters.command("queue", prefixes=PREFIX))
async def queue_cmd(client, message):
    q = get_queue(message.chat.id)
    if not q.songs and not q.current:
        await message.reply_text("📭 Queue is empty.", reply_markup=main_menu())
        return
    text, kb = queue_panel(q, page=0)
    await message.reply_text(text, reply_markup=kb)

@music.on_message(filters.command("now", prefixes=PREFIX))
async def now_cmd(client, message):
    q = get_queue(message.chat.id)
    if not q.current:
        await message.reply_text("❌ Nothing is playing.", reply_markup=main_menu())
        return
    await message.reply_text(now_playing_text(q.current), reply_markup=player_panel(q))

@music.on_message(filters.command("loop", prefixes=PREFIX))
async def loop_cmd(client, message):
    if not await can_control(client, message.chat.id, message.from_user.id):
        await _deny_reply(message)
        return
    q = get_queue(message.chat.id)
    q.loop = not q.loop
    if q.loop:
        q.repeat = False
        q.silent_loop = True
    else:
        q.silent_loop = False
    await message.reply_text(f"🔁 『{BRAND}』 — {stylish('Loop')}: {stylish('ON' if q.loop else 'OFF')}")

@music.on_message(filters.command("repeat", prefixes=PREFIX))
@needs_vc
async def repeat_cmd(client, message):
    if not await can_control(client, message.chat.id, message.from_user.id):
        await _deny_reply(message)
        return
    q = get_queue(message.chat.id)
    q.repeat = not q.repeat
    if q.repeat:
        q.loop = False
        q.silent_loop = True
    else:
        q.silent_loop = False
    if q.current:
        await reapply_current_effects(message.chat.id)
    await message.reply_text(f"🔂 『{BRAND}』 — {stylish('Repeat')}: {stylish('ON' if q.repeat else 'OFF')}")

@music.on_message(filters.command("boost", prefixes=PREFIX))
@needs_vc
async def boost_cmd(client, message):
    if not await can_control(client, message.chat.id, message.from_user.id):
        await _deny_reply(message)
        return
    q = get_queue(message.chat.id)
    arg = " ".join(message.command[1:]).lower() if len(message.command) > 1 else ""
    if arg in ("on", "max"):
        q.boost_level = 100
    elif arg in ("off", "0", "none", "reset"):
        q.boost_level = 0
    elif arg.replace(".", "", 1).isdigit():
        q.boost_level = max(0, min(100, int(float(arg))))
    else:
        q.boost_level = 0 if q.boost_level > 0 else 100
    if q.current:
        await reapply_current_effects(message.chat.id)
    await message.reply_text(
        f"🚀 『{BRAND}』 — {stylish('Ultra Booster')}: `{q.boost_level}%`\n"
        f"💡 `.boost 50` · `.boost 100` · `.boost off`"
    )

@music.on_message(filters.command("eco", prefixes=PREFIX))
@needs_vc
async def eco_cmd(client, message):
    if not await can_control(client, message.chat.id, message.from_user.id):
        await _deny_reply(message)
        return
    q = get_queue(message.chat.id)
    arg = " ".join(message.command[1:]).lower() if len(message.command) > 1 else ""
    if arg in ("on", "max"):
        q.eco_level = 100
    elif arg in ("off", "0", "none", "reset"):
        q.eco_level = 0
    elif arg.replace(".", "", 1).isdigit():
        q.eco_level = max(0, min(100, int(float(arg))))
    else:
        q.eco_level = 0 if q.eco_level > 0 else 100
    if q.current:
        await reapply_current_effects(message.chat.id)
    await message.reply_text(
        f"🎤 『{BRAND}』 — {stylish('Echo')}: `{q.eco_level}%`\n"
        f"💡 `.eco 30` · `.eco 70` · `.eco off`"
    )

@music.on_message(filters.command("gain", prefixes=PREFIX))
@needs_vc
async def gain_cmd(client, message):
    if not await can_control(client, message.chat.id, message.from_user.id):
        await _deny_reply(message)
        return
    q = get_queue(message.chat.id)
    arg = " ".join(message.command[1:]).lower() if len(message.command) > 1 else ""
    if arg in ("on", "max"):
        q.gain_level = 100
    elif arg in ("off", "0", "none", "reset"):
        q.gain_level = 0
    elif arg.replace(".", "", 1).isdigit():
        q.gain_level = max(0, min(100, int(float(arg))))
    else:
        q.gain_level = 0 if q.gain_level > 0 else 100
    if q.current:
        await reapply_current_effects(message.chat.id)
    await message.reply_text(
        f"📢 『{BRAND}』 — {stylish('Gain Mode')}: `{q.gain_level}%`\n"
        f"💡 `.gain 40` · `.gain 100` · `.gain off`"
    )

@music.on_message(filters.command("mix", prefixes=PREFIX))
@needs_vc
async def mix_cmd(client, message):
    if not await can_control(client, message.chat.id, message.from_user.id):
        await _deny_reply(message)
        return
    q = get_queue(message.chat.id)
    arg = " ".join(message.command[1:]).lower() if len(message.command) > 1 else ""
    if arg in ("off", "0", "none", "reset"):
        lvl = 0
    elif arg.replace(".", "", 1).isdigit():
        lvl = max(0, min(100, int(float(arg))))
    else:
        lvl = 0 if (q.boost_level > 0 or q.eco_level > 0 or q.gain_level > 0) else 100
    q.boost_level = q.eco_level = q.gain_level = lvl
    q.loud_level = 0
    if lvl == 0:
        q.treble = 0
    if q.current:
        await reapply_current_effects(message.chat.id)
    await message.reply_text(
        f"⚡ 『{BRAND}』 — {stylish('MIX MODE')}: `{lvl}%`\n"
        f"📢 {stylish('Gain')}: `{q.gain_level}%` · 🎤 {stylish('Echo')}: `{q.eco_level}%` · "
        f"🚀 {stylish('Boost')}: `{q.boost_level}%`\n"
        f"💡 `.mix 50` · `.mix off`"
    )

@music.on_message(filters.command("loud", prefixes=PREFIX))
@needs_vc
async def loud_cmd(client, message):
    if not await can_control(client, message.chat.id, message.from_user.id):
        await _deny_reply(message)
        return
    q = get_queue(message.chat.id)
    arg = " ".join(message.command[1:]).lower() if len(message.command) > 1 else ""
    if arg in ("on", "max"):
        q.loud_level = 100
    elif arg in ("off", "0", "none", "reset"):
        q.loud_level = 0
    elif arg.replace(".", "", 1).isdigit():
        q.loud_level = max(0, min(100, int(float(arg))))
    else:
        q.loud_level = 0 if q.loud_level > 0 else 100
    if q.current:
        q.current["loudspeaker"] = q.loud_level > 0
    await reapply_current_effects(message.chat.id)
    await message.reply_text(
        f"🔊 『{BRAND}』 — {stylish('Loudspeaker')}: `{q.loud_level}%`\n"
        f"💡 `.loud 20` · `.loud 50` · `.loud off`"
    )

@music.on_message(filters.command("volume", prefixes=PREFIX))
async def volume_cmd(client, message):
    q = get_queue(message.chat.id)
    if len(message.command) >= 2 and not await can_control(client, message.chat.id, message.from_user.id):
        await _deny_reply(message)
        return
    if len(message.command) < 2:
        await message.reply_text(
            f"🎚️ Volume: {fmt_vol(q.volume)}", reply_markup=volume_panel(q)
        )
        return
    try:
        v = int(message.command[1])
        if not (0 <= v <= 5000):
            raise ValueError
        q.volume = v
        _vol_auto[message.chat.id] = False
        if vc_ready():
            try:
                await vc.change_volume_call(message.chat.id, min(v, 200))
                _last_vol_applied[message.chat.id] = min(v, 200)
            except Exception:
                pass
        await message.reply_text(f"🔊 Volume set to {fmt_vol(v)}")
    except ValueError:
        await message.reply_text("❌ Volume must be 0–5000.")

@music.on_message(filters.command("stats", prefixes=PREFIX))
async def stats_cmd(client, message):
    try:
        real_users = len(db.get_users())
    except Exception:
        real_users = db.get_stat("total_users")
    body = (
        f"  👥 {stylish('users')}        `{real_users}`\n"
        f"  🎵 {stylish('played')}       `{db.get_stat('total_played')}`\n"
        f"  📞 {stylish('live vc')}      `{len(active_calls)}`\n"
        f"  ⏱️  {stylish('uptime')}       `{fmt_uptime()}`\n"
    )
    await message.reply_text(core_card("telemetry", body), reply_markup=hub_keyboard())

@music.on_message(filters.command("lockmusic", prefixes=PREFIX))
async def lockmusic_cmd(client, message):
    if not await is_group_admin(client, message.chat.id, message.from_user.id):
        await message.reply_text("❌ Only group admins can change this.")
        return
    db.set_config(f"lock:{message.chat.id}", "1")
    await message.reply_text("🔒🛡️ Per-chat HARD LOCK — ab is chat me SIRF owner + core admins hi chala sakte hain. 👑")

@music.on_message(filters.command("unlockmusic", prefixes=PREFIX))
async def unlockmusic_cmd(client, message):
    if not await is_group_admin(client, message.chat.id, message.from_user.id):
        await message.reply_text("❌ Only group admins can change this.")
        return
    db.set_config(f"lock:{message.chat.id}", "0")
    await message.reply_text("🔓🎉 Hard lock hata — ab bot mood (.public/.private) ke hisaab se control chalega.")

@music.on_message(filters.command("admin", prefixes=PREFIX))
async def admin_cmd(client, message):
    if not is_core_admin(message.from_user.id):
        await message.reply_text("❌ Owner / core admins only.")
        return
    if message.reply_to_message:
        if message.from_user.id != OWNER_ID:
            await message.reply_text("❌ Sirf **OWNER** hi admin add/remove kar sakta hai. 👑")
            return
        _r = message.reply_to_message
        _ru = getattr(_r, "from_user", None)
        if _ru is None or getattr(_ru, "is_bot", False):
            await message.reply_text("❌ Reply me koi user nahi hai (ya bot hai).")
            return
        _act = (message.command[1].lower() if len(message.command) >= 2 else "add")
        if _act not in ("add", "del", "remove", "rm"):
            _act = "add"
        db.add_user(_ru.id, _ru.username, _ru.first_name, _ru.last_name or "")
        _nm = f"@{_ru.username}" if getattr(_ru, "username", None) else (_ru.first_name or str(_ru.id))
        if _act in ("del", "remove", "rm"):
            if _ru.id == OWNER_ID:
                await message.reply_text("❌ Owner ko remove nahi kar sakte. 👑")
                return
            del_core_admin(_ru.id)
            await message.reply_text(
                f"🗑️ 👑 {_nm} (`{_ru.id}`) admin list se hata diya."
            )
        else:
            add_core_admin(_ru.id)
            await message.reply_text(
                f"✅ 👑 {_nm} (`{_ru.id}`) ab **core admin** hai — 🔐 private mood me bhi access!"
            )
        return
    args = message.command[1:]
    if args:
        action = args[0].lower()
        if action in ("add", "del", "remove"):
            if message.from_user.id != OWNER_ID:
                await message.reply_text("❌ Sirf **OWNER** hi admin add/remove kar sakta hai. 👑")
                return
            if len(args) < 2 or not args[1].lstrip("-").isdigit():
                await message.reply_text(
                    "❌ Usage:\n"
                    "`.admin add <user_id | @username>` — admin banao\n"
                    "`.admin del <user_id | @username>` — admin hatayo\n"
                    "`.admin` (reply) — replied user ko admin banao"
                )
                return
            target = args[1]
            uid = None
            if target.lstrip("-").isdigit():
                uid = int(target)
            elif target.startswith("@"):
                try:
                    _u = await client.get_users(target)
                    uid = _u.id
                except Exception:
                    await message.reply_text(f"❌ `{target}` user nahi mila.")
                    return
            else:
                await message.reply_text(
                    "❌ Usage: `.admin add <id | @username>` ya reply karke `.admin`"
                )
                return
            if action == "add":
                add_core_admin(uid)
                await message.reply_text(f"✅ 👑 `{uid}` ab **core admin** hai!")
            else:
                if uid == OWNER_ID:
                    await message.reply_text("❌ Owner ko remove nahi kar sakte. 👑")
                    return
                del_core_admin(uid)
                await message.reply_text(f"🗑️ 👑 `{uid}` admin list se hata diya.")
            return
        if action in ("list", "admins"):
            admins = sorted(get_core_admins())
            lines = [f"📋 『{BRAND}』 — {stylish('Core Admin List')}", "━━━━━━━━━━━━━━━━━━━━━"]
            for a in admins:
                tag = "👑 owner" if a == OWNER_ID else "🛡️ admin"
                lines.append(f"• `{a}` · {tag}")
            lines.append("━━━━━━━━━━━━━━━━━━━━━")
            await message.reply_text("\n".join(lines))
            return
    await message.reply_text(
        "👑 **𝐃𝐄𝐕 𝐗 𝐂𝐎𝐑𝐄 — Owner Panel**\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"{stylish('Admin manage')}:\n"
        "`.admin` (reply) — replied user ko admin banao 👑\n"
        "`.admin add <id|@user>` · `.admin del` (reply)",
        reply_markup=owner_panel(),
    )

@music.on_message(filters.command("rjoin", prefixes=PREFIX))
async def rjoin_cmd(client, message):
    """Removed file-based rjoin; kept simple auto-join info."""
    chat_id = message.chat.id
    if chat_id > 0:
        await message.reply_text("❌ Sirf group me chalta hai — private chat me nahi.")
        return
    if not await can_control(client, chat_id, message.from_user.id):
        await _deny_reply(message)
        return
    args = message.command[1:]
    if not args or args[0].strip().lower() in ("off", "stop", "0"):
        await message.reply_text(
            f"🔕 『{BRAND}』 — {stylish('Auto-join OFF')}.\n"
            f"{stylish('Wapas on')}: `.rjoin`"
        )
        return
    set_rjoin_spec = db.set_config
    spec = " ".join(args)
    set_rjoin_spec(f"rjoin:{chat_id}", spec)
    await message.reply_text(
        f"🔁 『{BRAND}』 — {stylish('Auto-Join ON')}\n"
        f"  ▸  {stylish('spec')}: `{spec}`\n"
        f"{stylish('Jab bhi call lagega, bot khud join karega.')}\n"
        f"{stylish('Band')}: `.rjoin off`"
    )

def get_rjoin_spec(chat_id):
    raw = (db.get_config(f"rjoin:{chat_id}") or "").strip()
    return raw or None

@music.on_message(filters.command(["public", "private"], prefixes=PREFIX))
async def mode_cmd(client, message):
    if message.from_user.id != OWNER_ID:
        await message.reply_text("❌ Sirf **OWNER** hi mood change kar sakta hai. 👑")
        return
    mode = "public" if (message.command[0] or "").replace(PREFIX, "", 1).lower() == "public" else "private"
    set_mode(mode)
    if mode == "public":
        body = (
            f"  🌍 {stylish('PUBLIC MODE ON')}\n\n"
            f"  ▸  {stylish('group owner + admins')} — sab chala sakte hain\n"
            f"  ▸  👑 {stylish('owner + core admins')} — hamesha\n"
        )
        footer = stylish("mood: public")
    else:
        body = (
            f"  🔐 {stylish('PRIVATE MODE ON')}\n\n"
            f"  ▸  {stylish('sirf OWNER + core admins')} 👑\n"
        )
        footer = stylish("mood: private")
    await message.reply_text(core_card("bot mood", body, footer))

@music.on_message(filters.command(["rm", "removeadmin"], prefixes=PREFIX))
async def rm_cmd(client, message):
    if message.from_user.id != OWNER_ID:
        await message.reply_text("❌ Sirf **OWNER** admin remove kar sakta hai. 👑")
        return
    arg = " ".join(message.command[1:]).strip().lower()
    if arg in ("all", "*"):
        removed = sorted(get_core_admins() - {OWNER_ID})
        for a in removed:
            del_core_admin(a)
        await message.reply_text(
            f"🗑️ {stylish('All core admins removed')}: {len(removed)}"
        )
        return
    uid = None
    if message.reply_to_message and message.reply_to_message.from_user:
        uid = message.reply_to_message.from_user.id
    elif arg.lstrip("-").isdigit():
        uid = int(arg)
    if not uid:
        await message.reply_text(
            "❌ Usage:\n"
            "`.rm <user_id>` — id se remove\n"
            "`.rm` (reply) — us user ko remove\n"
            "`.rm all` — sab core admins remove"
        )
        return
    if uid == OWNER_ID:
        await message.reply_text("❌ Owner ko remove nahi kar sakte. 👑")
        return
    if not is_core_admin(uid):
        await message.reply_text(f"❌ `{uid}` core admin list me hai hi nahi.")
        return
    del_core_admin(uid)
    await message.reply_text(f"🗑️ `{uid}` {stylish('core admin list se removed')}.")

@music.on_message(filters.command(["alist", "adminlist"], prefixes=PREFIX))
async def alist_cmd(client, message):
    if not is_core_admin(message.from_user.id):
        await message.reply_text("❌ Owner / core admin only.")
        return
    admins = sorted(get_core_admins())
    lines = [
        f"📋 『{BRAND}』 — {stylish('Core Admin List')}",
        "━━━━━━━━━━━━━━━━━━━━━",
    ]
    for a in admins:
        tag = "👑 owner" if a == OWNER_ID else "🛡️ admin"
        lines.append(f"• `{a}` · {tag}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"👉 `.rm <id>` · `.rm all` · `.admin add <id>`")
    await message.reply_text("\n".join(lines))

@music.on_message(filters.command("servers", prefixes=PREFIX))
async def servers_cmd(client, message):
    if not is_core_admin(message.from_user.id):
        await message.reply_text("❌ Owner / core admin only.")
        return
    groups = []
    async for d in client.get_dialogs():
        if d.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            groups.append(f"• {d.chat.title} (`{d.chat.id}`)")
    await message.reply_text("🌐 **Groups:**\n" + "\n".join(groups[:30] or ["No groups."]))

@music.on_message(filters.command("users", prefixes=PREFIX))
async def users_cmd(client, message):
    if not is_core_admin(message.from_user.id):
        await message.reply_text("❌ Owner / core admin only.")
        return
    users = db.get_users(50)
    text = "👥 **Users:**\n"
    for u in users:
        text += f"• `{u[0]}` @{u[1] or '—'}\n"
    await message.reply_text(text or "No users yet.")

@music.on_message(filters.command("broadcast", prefixes=PREFIX))
async def broadcast_cmd(client, message):
    if not is_core_admin(message.from_user.id):
        await message.reply_text("❌ Owner / core admin only.")
        return
    if len(message.command) < 2:
        await message.reply_text(f"❌ `{PREFIX}broadcast <text>`")
        return
    text = "📢 **𝐃𝐄𝐕 𝐗 𝐂𝐎𝐑𝐄 — Announcement**\n\n" + " ".join(message.command[1:])
    users = db.get_users()
    ok = fail = 0
    st = await message.reply_text("📢 Broadcasting…")
    for u in users:
        try:
            await client.send_message(u[0], text)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await st.edit_text(f"✅ Sent to {ok} users. Failed: {fail}")

@music.on_message(filters.command(["ban", "unban"], prefixes=PREFIX))
async def ban_cmd(client, message):
    if not is_core_admin(message.from_user.id):
        await message.reply_text("❌ Owner / core admin only.")
        return
    if len(message.command) < 2:
        await message.reply_text(f"❌ `{PREFIX}ban <user_id>` or `{PREFIX}unban <user_id>`")
        return
    try:
        uid = int(message.command[1])
    except ValueError:
        await message.reply_text("❌ Invalid id.")
        return
    if message.command[0] == "ban":
        db.ban_user(uid)
        await message.reply_text(f"🚫 Banned `{uid}`.")
    else:
        db.unban_user(uid)
        await message.reply_text(f"✅ Unbanned `{uid}`.")

GBAN_KEY = "gbans"

def get_gbans():
    raw = db.get_config(GBAN_KEY, "") or ""
    ids = {int(x) for x in raw.split(",") if x.strip().isdigit()}
    ids.discard(OWNER_ID)
    return ids

def gban_user(uid):
    ids = get_gbans()
    ids.add(int(uid))
    db.set_config(GBAN_KEY, ",".join(str(i) for i in sorted(ids)))

def ungban_user(uid):
    ids = get_gbans()
    ids.discard(int(uid))
    db.set_config(GBAN_KEY, ",".join(str(i) for i in sorted(ids)))

def uid_gbanned(uid):
    try:
        return int(uid) in get_gbans()
    except Exception:
        return False

@music.on_message(filters.command("gban", prefixes=PREFIX))
async def gban_cmd(client, message):
    if message.from_user.id != OWNER_ID:
        await message.reply_text(f"❌ {stylish('Sirf OWNER')} 👑")
        return
    uid = None
    if message.reply_to_message and message.reply_to_message.from_user:
        uid = message.reply_to_message.from_user.id
    elif " ".join(message.command[1:]).lstrip("-").isdigit():
        uid = int(" ".join(message.command[1:]))
    if not uid or uid == OWNER_ID:
        await message.reply_text(f"❌ {stylish('Reply karo ya user id do')} ({stylish('owner ko nahi')}).")
        return
    gban_user(uid)
    await message.reply_text(
        f"🚫 『{BRAND}』 — {stylish('Global banned')}: `{uid}`\n"
        f"{stylish('Ab wo bot use nahi kar sakta')}."
    )

@music.on_message(filters.command("ungban", prefixes=PREFIX))
async def ungban_cmd(client, message):
    if message.from_user.id != OWNER_ID:
        await message.reply_text(f"❌ {stylish('Sirf OWNER')} 👑")
        return
    uid = None
    if message.reply_to_message and message.reply_to_message.from_user:
        uid = message.reply_to_message.from_user.id
    elif " ".join(message.command[1:]).lstrip("-").isdigit():
        uid = int(" ".join(message.command[1:]))
    if not uid:
        await message.reply_text(f"❌ {stylish('Reply karo ya user id do')}.")
        return
    ungban_user(uid)
    await message.reply_text(f"✅ 『{BRAND}』 — {stylish('Unbanned')}: `{uid}`")

@music.on_message(filters.command("gbanlist", prefixes=PREFIX))
async def gbanlist_cmd(client, message):
    if not is_core_admin(message.from_user.id):
        await message.reply_text(f"❌ {stylish('Owner / core admin only')}.")
        return
    ids = sorted(get_gbans())
    if not ids:
        await message.reply_text(f"📭 {stylish('Koi gban nahi')}.")
        return
    await message.reply_text(
        "🚫 **Global Bans**\n━━━━━━━━━━━━━━━━━━━━━\n" +
        "\n".join(f"• `{i}`" for i in ids)
    )

@music.on_message(filters.command("restart", prefixes=PREFIX) & filters.user(OWNER_ID))
async def restart_cmd(client, message):
    try:
        await message.reply_text(
            f"♻️ 『{BRAND}』 — {stylish('Restarting')}…\n"
            f"⏳ {stylish('2-5 sec me wapas online')} — `.ping` {stylish('se check karo')}"
        )
    except Exception:
        pass
    await asyncio.sleep(1)
    try:
        await music.stop()
    except Exception:
        pass
    try:
        await bot.stop()
    except Exception:
        pass
    try:
        db.close()
    except Exception:
        pass
    try:
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception:
        os._exit(0)

@music.on_message(filters.command("backup", prefixes=PREFIX) & filters.user(OWNER_ID))
async def backup_cmd(client, message):
    import zipfile
    os.makedirs("backups", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zp = os.path.join("backups", f"devx_backup_{ts}.zip")
    try:
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
            for fp in ([DB_PATH, SESSION_FILE] if os.path.exists(SESSION_FILE) else [DB_PATH]):
                if os.path.exists(fp):
                    z.write(fp)
            log = os.path.join(LOGS_DIR, "bot.log")
            if os.path.exists(log):
                z.write(log)
        await message.reply_document(zp, caption=f"📦 Backup: `{os.path.basename(zp)}`")
    except Exception as e:
        await message.reply_text(f"❌ Backup fail: `{e}`")

@music.on_message(filters.command("clean", prefixes=PREFIX))
async def clean_cmd(client, message):
    if not is_core_admin(message.from_user.id):
        await message.reply_text(f"❌ {stylish('Owner / core admin only')}.")
        return
    playing = set(current_files.values())
    removed = 0
    try:
        for name in os.listdir(TEMP_DIR):
            fp = os.path.join(TEMP_DIR, name)
            if os.path.isfile(fp) and fp not in playing:
                try:
                    os.remove(fp)
                    removed += 1
                except Exception:
                    pass
    except Exception:
        pass
    await message.reply_text(
        f"🧹 『{BRAND}』 — {stylish('Temp cleaned')}: {removed} {stylish('files')}"
    )

@music.on_message(filters.command("ytupdate", prefixes=PREFIX) & filters.user(OWNER_ID))
async def ytupdate_cmd(client, message):
    st = await message.reply_text("⬇️ yt-dlp update ho raha hai…")
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "-U", "--no-cache-dir", "yt-dlp",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _out, err = await asyncio.wait_for(proc.communicate(), timeout=240)
        try:
            import yt_dlp as _ydl
            newver = _ydl.version.__version__
        except Exception:
            newver = "?"
        tail = (err.decode(errors="ignore") or "").strip().splitlines()
        note = tail[-1][:200] if tail else "Installed latest!"
        await st.edit_text(
            f"✅ 『{BRAND}』 — {stylish('yt-dlp updated')}\n"
            f"📦 `{newver}`\n\n`{note}`"
        )
    except Exception as e:
        await st.edit_text(f"❌ Update failed: `{e}`\nManual: `pip install -U yt-dlp`")

@music.on_message(filters.command("eval", prefixes=PREFIX) & filters.user(OWNER_ID))
async def eval_cmd(client, message):
    if len(message.command) < 2:
        await message.reply_text(f"❌ `{PREFIX}eval <code>`")
        return
    code = " ".join(message.command[1:])
    env = {"music": music, "client": client, "vc": vc, "db": db, "message": message, "queues": queues}
    try:
        result = eval(code, {"__builtins__": __builtins__}, env)
        await message.reply_text(f"```\n{result}\n```")
    except Exception as e:
        await message.reply_text(f"```\n{e}\n```")

@music.on_message(filters.new_chat_members)
@music.on_message(filters.service)
async def rjoin_service_handler(client, message):
    if not vc_ready():
        return
    svc = getattr(message, "service", None)
    if svc is None:
        return
    chat_id = message.chat.id
    if chat_id > 0:
        return
    CHAT_STARTED = {
        getattr(MessageServiceType, n) for n in ("VIDEO_CHAT_STARTED", "VOICE_CHAT_STARTED")
        if getattr(MessageServiceType, n, None) is not None
    }
    CHAT_ENDED = {
        getattr(MessageServiceType, n) for n in ("VIDEO_CHAT_ENDED", "VOICE_CHAT_ENDED")
        if getattr(MessageServiceType, n, None) is not None
    }
    if svc in CHAT_ENDED:
        try:
            if chat_id in active_calls:
                await vc.leave_call(chat_id)
        except Exception:
            pass
        active_calls.discard(chat_id)
        q = get_queue(chat_id)
        if q.current is not None and q.current.get("rjoin"):
            q.current = None
            q.songs.clear()
            q.repeat = False
            q.loop = False
            q.silent_loop = False
            current_files.pop(chat_id, None)
            PLAY_TIMERS.pop(chat_id, None)
            logger.info("rjoin: call ended in %s — cleaned", chat_id)
        return
    if svc not in CHAT_STARTED:
        return
    if get_rjoin_spec(chat_id) is None:
        return
    _rj_now = time.time()
    if hasattr(rjoin_on_call_started, "_last_call"):
        _rj_last = rjoin_on_call_started._last_call.get(chat_id, 0)
        if _rj_now - _rj_last < 15:
            return
    else:
        rjoin_on_call_started._last_call = {}
    rjoin_on_call_started._last_call[chat_id] = _rj_now
    result = await rjoin_on_call_started(chat_id)
    if result:
        try:
            await music.send_message(
                chat_id,
                f"🔁 『{BRAND}』 — {stylish('Auto-Joined!')}\n"
                f"{stylish('Call start hui — bot join ho gaya')}",
            )
        except Exception:
            pass

async def rjoin_on_call_started(chat_id):
    if not vc_ready():
        return None
    spec = get_rjoin_spec(chat_id)
    if spec is None:
        return None
    _q = get_queue(chat_id)
    if _q.current is not None or chat_id in active_calls:
        return None
    await asyncio.sleep(3)
    joined = False
    for attempt in range(6):
        try:
            await vc.play(chat_id)
            joined = True
            break
        except NoActiveGroupCall:
            await asyncio.sleep(3)
        except Exception:
            await asyncio.sleep(3)
    if not joined:
        return None
    active_calls.add(chat_id)
    if spec == "join":
        return "joined"
    return "joined"

@music.on_message(filters.command("play", prefixes=PREFIX))
@needs_vc
async def play_cmd(client, message):
    chat_id = message.chat.id
    q = get_queue(chat_id)

    if not await can_control(client, chat_id, message.from_user.id):
        await _deny_reply(message)
        return

    if message.reply_to_message:
        path = await download_reply_media(message)
        if path:
            song = {
                "title": os.path.basename(path),
                "duration": 0,
                "uploader": "DEV X MUSIC",
                "local_path": path,
                "source": "local",
                "temp": True,
            }
            db.bump("total_voice")
            q.songs.append(song)
            _sync_play_loop(q)
            if not q.current:
                q.current = q.songs.popleft()
                active_calls.add(chat_id)
                await play_song(chat_id, q.current)
            else:
                await message.reply_text(f"✅ Queued: **{song['title']}**")
            return

    if len(message.command) < 2:
        await message.reply_text(
            f"❌ Please provide a song name or URL.\nExample: `{PREFIX}play despacito`",
            reply_markup=main_menu(),
        )
        return

    query = " ".join(message.command[1:])
    st = await message.reply_text(f"🔎 『{BRAND}』 — {stylish('Searching')}…")

    if is_url(query):
        if "open.spotify.com" in query:
            try:
                sp = await spotify_expand(query)
            except RuntimeError:
                await st.delete()
                await message.reply_text(spotify_setup_help())
                return
            if not sp:
                await st.delete()
                await message.reply_text("❌ Spotify link se tracks nahi mile.")
                return
            await st.delete()
            await _queue_songs(message, sp, natural=False, label="🎵 playlist")
            return
        if "deezer.com" in query:
            dz = await deezer_expand(query)
            if not dz:
                await st.delete()
                await message.reply_text("❌ Deezer link se tracks nahi mile.")
                return
            await st.delete()
            await _queue_songs(message, dz, natural=False, label="🎵 playlist")
            return
        song = await _search_guard(asyncio.to_thread(resolve_song, query), 25)
        await st.delete()
        if not song:
            await message.reply_text("❌ Couldn't find that song.", reply_markup=main_menu())
            return
        if "soundcloud.com" in query:
            song["source"] = "soundcloud"
        q.songs.append(song)
        _sync_play_loop(q)
        if not q.current:
            q.current = q.songs.popleft()
            active_calls.add(chat_id)
            await play_song(chat_id, q.current)
        else:
            await message.reply_text(
                f"✅ 『{BRAND}』 — {stylish('Added')}:\n"
                f"🎵 {song['title']}\n⏱️ {fmt_duration(song['duration'])} — {stylish('Position')} {len(q.songs)}",
                reply_markup=player_panel(q),
            )
        return

    saavn_results, yt_results, sc_results, sp_results, dz_results = await asyncio.gather(
        _search_guard(saavn_search(query, limit=4), 15),
        _search_guard(asyncio.to_thread(resolve_songs, query, 4), 15),
        _search_guard(asyncio.to_thread(soundcloud_search, query, 2), 15),
        _search_guard(spotify_search(query, 3), 12),
        _search_guard(deezer_search(query, 3), 12),
    )
    await st.delete()
    results = (saavn_results or []) + (yt_results or []) + (sc_results or []) + (sp_results or []) + (dz_results or [])
    if not results:
        await message.reply_text("❌ No results found.", reply_markup=main_menu())
        return

    search_seq[0] += 1
    sid = search_seq[0]
    search_cache[sid] = results
    last_search[(chat_id, message.from_user.id)] = {"sid": sid, "natural": False, "ts": time.time()}

    text = search_results_text(results, query, natural=False)
    buttons = []
    for i, s in enumerate(results):
        tag = SOURCE_TAG.get(s.get("source"), "🎵")
        label = s['title'][:25]
        buttons.append([InlineKeyboardButton(
            f"{i+1}. {tag} {label}", callback_data=f"cb_pick_{sid}_{i}"
        )])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cb_cancel")])
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

@music.on_message(filters.command("nplay", prefixes=PREFIX))
@needs_vc
async def nplay_cmd(client, message):
    chat_id = message.chat.id
    q = get_queue(chat_id)

    if not await can_control(client, chat_id, message.from_user.id):
        await _deny_reply(message)
        return

    if message.reply_to_message:
        path = await download_reply_media(message)
        if path:
            song = {
                "title": os.path.basename(path),
                "duration": 0,
                "uploader": "DEV X CORE",
                "local_path": path,
                "source": "local",
                "normal": True,
            }
            db.bump("total_voice")
            q.songs.append(song)
            _sync_play_loop(q)
            if not q.current:
                q.current = q.songs.popleft()
                active_calls.add(chat_id)
                await play_song(chat_id, q.current)
            else:
                await message.reply_text(f"✅ Queued (natural 🎧): **{song['title']}**")
            return

    if len(message.command) < 2:
        await message.reply_text(
            f"❌ Song name ya URL do.\n"
            f"Example: `{PREFIX}nplay despacito`\n\n"
            f"🎧 {stylish('Natural sound')} — {stylish('no boost, no echo, no extra gain')}",
            reply_markup=main_menu(),
        )
        return

    query = " ".join(message.command[1:])
    st = await message.reply_text(f"🔎 『{BRAND}』 — {stylish('Searching natural')}…")

    if is_url(query):
        if "open.spotify.com" in query:
            try:
                sp = await spotify_expand(query)
            except RuntimeError:
                await st.delete()
                await message.reply_text(spotify_setup_help())
                return
            if not sp:
                await st.delete()
                await message.reply_text("❌ Spotify link se tracks nahi mile.")
                return
            await st.delete()
            await _queue_songs(message, sp, natural=True, label="🎧 playlist")
            return
        if "deezer.com" in query:
            dz = await deezer_expand(query)
            if not dz:
                await st.delete()
                await message.reply_text("❌ Deezer link se tracks nahi mile.")
                return
            await st.delete()
            await _queue_songs(message, dz, natural=True, label="🎧 playlist")
            return
        song = await _search_guard(asyncio.to_thread(resolve_song, query), 25)
        await st.delete()
        if not song:
            await message.reply_text("❌ Couldn't find that song.", reply_markup=main_menu())
            return
        if "soundcloud.com" in query:
            song["source"] = "soundcloud"
        song["normal"] = True
        q.songs.append(song)
        _sync_play_loop(q)
        if not q.current:
            q.current = q.songs.popleft()
            active_calls.add(chat_id)
            await play_song(chat_id, q.current)
        else:
            await message.reply_text(
                f"✅ 『{BRAND}』 — {stylish('Added (natural)')}:\n"
                f"🎵 {song['title']}\n⏱️ {fmt_duration(song['duration'])} — {stylish('Position')} {len(q.songs)}",
                reply_markup=player_panel(q),
            )
        return

    saavn_results, yt_results, sc_results, sp_results, dz_results = await asyncio.gather(
        _search_guard(saavn_search(query, limit=4), 15),
        _search_guard(asyncio.to_thread(resolve_songs, query, 4), 15),
        _search_guard(asyncio.to_thread(soundcloud_search, query, 2), 15),
        _search_guard(spotify_search(query, 3), 12),
        _search_guard(deezer_search(query, 3), 12),
    )
    await st.delete()
    results = (saavn_results or []) + (yt_results or []) + (sc_results or []) + (sp_results or []) + (dz_results or [])
    if not results:
        await message.reply_text("❌ No results found.", reply_markup=main_menu())
        return

    search_seq[0] += 1
    sid = search_seq[0]
    search_cache[sid] = results
    last_search[(chat_id, message.from_user.id)] = {"sid": sid, "natural": True, "ts": time.time()}

    text = search_results_text(results, query, natural=True)
    buttons = []
    for i, s in enumerate(results):
        tag = SOURCE_TAG.get(s.get("source"), "🎵")
        label = s['title'][:25]
        buttons.append([InlineKeyboardButton(
            f"{i+1}. {tag} {label}", callback_data=f"cb_npick_{sid}_{i}"
        )])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cb_cancel")])
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

@music.on_message(filters.command("skip", prefixes=PREFIX))
@needs_vc
async def skip_cmd(client, message):
    chat_id = message.chat.id
    q = get_queue(chat_id)
    if not await can_control(client, chat_id, message.from_user.id):
        await _deny_reply(message)
        return
    if q.songs:
        old = q.current
        q.current = q.songs.popleft()
        await play_song(chat_id, q.current)
        if old is not None:
            _delete_temp_song_files(old)
        await message.reply_text("⏭️ Skipped! 🎶")
    else:
        old = q.current
        q.current = None
        if old is not None:
            _delete_temp_song_files(old)
        try:
            await vc.leave_call(chat_id)
        except Exception:
            pass
        active_calls.discard(chat_id)
        await message.reply_text("⏭️ Skipped. Queue empty — stopped.")

@music.on_message(filters.command("pause", prefixes=PREFIX))
@needs_vc
async def pause_cmd(client, message):
    if not await can_control(client, message.chat.id, message.from_user.id):
        await _deny_reply(message)
        return
    try:
        await vc.pause(message.chat.id)
        _pause_timer(message.chat.id)
        await message.reply_text("⏸️ Paused.")
    except Exception:
        await message.reply_text("❌ Nothing is playing.")

@music.on_message(filters.command("resume", prefixes=PREFIX))
@needs_vc
async def resume_cmd(client, message):
    if not await can_control(client, message.chat.id, message.from_user.id):
        await _deny_reply(message)
        return
    try:
        await vc.resume(message.chat.id)
        _resume_timer(message.chat.id)
        await message.reply_text("▶️ Resumed.")
    except Exception:
        await message.reply_text("❌ Nothing is paused.")

@music.on_message(filters.command("stop", prefixes=PREFIX))
@needs_vc
async def stop_cmd(client, message):
    chat_id = message.chat.id
    q = get_queue(chat_id)
    if not await can_control(client, chat_id, message.from_user.id):
        await _deny_reply(message)
        return
    queued = list(q.songs)
    q.songs.clear()
    old = q.current
    q.current = None
    q.loop = False
    q.repeat = False
    q.silent_loop = False
    active_calls.discard(chat_id)
    current_files.pop(chat_id, None)
    err_notified.discard(chat_id)
    replay_lock.discard(chat_id)
    PLAY_TIMERS.pop(chat_id, None)
    if old is not None:
        _delete_temp_song_files(old)
    for _s in queued:
        _delete_temp_song_files(_s)
    try:
        await vc.leave_call(chat_id)
        await message.reply_text(
            "⏹️ Stopped & left the voice chat.\n"
        )
    except Exception:
        await message.reply_text("❌ Not in a voice chat.")

# ==================== HELP / CMD PAGES ====================
CMD_HELP = {
    "play": ".play — 🎵 gaana search karke play",
    "nplay": ".nplay — 🎧 natural sound (no effects)",
    "pause": ".pause — pause playback",
    "resume": ".resume — resume playback",
    "skip": ".skip — skip current track",
    "stop": ".stop — stop & leave voice chat",
    "now": ".now — show now playing",
    "queue": ".queue — show music queue",
    "loop": ".loop — loop whole queue (ON/OFF)",
    "repeat": ".repeat — repeat current track (ON/OFF)",
    "boost": ".boost <0-100> — 🚀 ultra booster level",
    "eco": ".eco <0-100> — 🎤 echo/reverb level",
    "gain": ".gain <0-100> — 📢 gain/loudspeaker level",
    "mix": ".mix <0-100> — ⚡ sab effects ek saath",
    "loud": ".loud <0-100> — 🔊 loudspeaker level current song",
    "treble": ".treble <0-100> — 🎼 treble/highs level",
    "bass": ".bass <0-100> — 🔊 bass boost level",
    "sound": ".sound — 🔊 saare sound levels ek saath dekho",
    "max": ".max — 💯 SAB effects 100 (Telegram max loud)",
    "volume": ".volume — volume panel / set volume",
    "speed": ".speed <0.5-2.5> — ⚡ playback speed",
    "nightcore": ".nightcore — ⚡ nightcore mode ON/OFF",
    "seek": ".seek +30 / -15 — ⏩ current track me aage/peeche",
    "menu": ".menu — main menu",
    "panel": ".panel — player panel",
    "stats": ".stats — bot stats",
    "diag": ".diag — diagnose playback issues",
    "admin": ".admin — reply karke us user ko core admin banao 👑",
    "rm": ".rm <id|reply> — core admin remove (owner)",
    "alist": ".alist — core admin list",
    "public": ".public — 🌍 mood: group ke owner/admins bot chala sakte hain",
    "private": ".private — 🔐 mood: sirf owner + core admins",
    "lockmusic": ".lockmusic — lock controls to admins",
    "unlockmusic": ".unlockmusic — unlock for everyone",
    "gban": ".gban <id|reply> — 🚫 global ban (owner)",
    "ungban": ".ungban <id|reply> — unban (owner)",
    "gbanlist": ".gbanlist — saare global bans",
    "broadcast": ".broadcast <text> — announcement to all users",
    "ytupdate": ".ytupdate — 🔧 player update (owner)",
    "clean": ".clean — 🧹 temp files clean (owner/core)",
    "backup": ".backup — 📦 db+session backup zip (owner)",
    "restart": ".restart — ♻️ bot restart (owner)",
    "rjoin": ".rjoin — call pe auto-join",
    "alive": ".alive — system card",
    "ping": ".ping — latency",
    "setname": ".setname — change account name",
    "setbio": ".setbio — change bio",
    "setpp": ".setpp — change profile photo",
    "getpp": ".getpp — get profile photo",
    "lyrics": ".lyrics <song> — 🎤 gaane ke lyrics",
    "radio": ".radio — 📻 live radio",
    "notesadd": ".notesadd title | body",
    "noteslist": ".noteslist",
    "notesdel": ".notesdel <id>",
}

def spotify_setup_help():
    return (
        f"🟢 『{BRAND}』 — {stylish('Spotify API Setup')}\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"{stylish('Free keys banao (2 min)')}:\n"
        "1️⃣ https://developer.spotify.com/dashboard\n"
        "2️⃣ Login → Create app (name: DEV X CORE)\n"
        "3️⃣ App settings me Client ID + Client Secret milega\n"
        "4️⃣ Server pe env set karke restart:\n"
        "`export SPOTIFY_CLIENT_ID=your_id`\n"
        "`export SPOTIFY_CLIENT_SECRET=your_secret`\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"{stylish('Bina keys ke bhi chalega')}: YouTube · Saavn · Deezer · SoundCloud"
    )

JOKES = [
    "I told my Wi-Fi we needed space. Now it keeps dropping me.",
    "A SQL query walks into a bar, walks up to two tables and asks: can I join you?",
    "I would tell you a UDP joke, but you might not get it.",
    "There are 10 kinds of people: those who get binary and those who don't.",
    "Why did the developer go broke? Because he used up all his cache.",
]
TRUTHS = [
    "What song is stuck in your head right now?",
    "What is a hill you will die on?",
    "Which group member has the best music taste?",
]
DARES = [
    "Send a voice note of your current song — badly.",
    "Change your last name to CORE for 10 minutes.",
    "Play the next track with .boost on.",
]

def _arg(message, default=""):
    if len(message.command) < 2:
        return default
    return " ".join(message.command[1:]).strip()

async def _target_user(client, message):
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user
    arg = _arg(message)
    if arg.lstrip("-").isdigit():
        try:
            return await client.get_users(int(arg))
        except Exception:
            return None
    if arg:
        try:
            return await client.get_users(arg)
        except Exception:
            return None
    return message.from_user

@music.on_message(filters.command(["alive", "core"], prefixes=PREFIX))
async def alive_cmd(client, message):
    t0 = time.time()
    me = None
    try:
        me = getattr(client, "me", None)
        if me is None:
            me = await client.get_me()
        _ = me.id
    except Exception:
        try:
            me = await client.get_me()
        except Exception:
            me = None
    ping = round((time.time() - t0) * 1000, 1)
    if me and getattr(me, "username", None):
        uname = f"@{me.username}"
    elif me and getattr(me, "first_name", None):
        uname = me.first_name
    else:
        uname = "core"
    body = (
        f"  💚 {stylish('status')}     **ONLINE**\n"
        f"  👤 {stylish('account')}    `{uname}`\n"
        f"  🚀 {stylish('ping')}       `{ping} ms`\n"
        f"  ⏱️  {stylish('uptime')}     `{fmt_uptime()}`\n"
        f"  📦 {stylish('version')}    `{CORE_VERSION}`\n"
        f"  🎛️  {stylish('engine')}     `{'on' if music_started else 'off'}`\n"
        f"  📞 {stylish('live vc')}    `{len(active_calls)}`\n"
    )
    await message.reply_text(core_card("alive", body, stylish("system nominal")), reply_markup=hub_keyboard())

@music.on_message(filters.command("ping", prefixes=PREFIX))
async def ping_cmd(client, message):
    t0 = time.time()
    try:
        msg = await message.reply_text("⌁")
        ms = round((time.time() - t0) * 1000, 1)
        await msg.edit_text(
            core_card("ping ⚡",
                      f"  🚀  `{ms} ms`\n"
                      f"  ⏱️  {stylish('uptime')}  `{fmt_uptime()}`\n"
                      f"  🎛️  {stylish('engine')}   `{'on' if music_started else 'off'}`")
        )
    except Exception:
        try:
            await message.reply_text(f"🏓 Pong!")
        except Exception:
            pass

@music.on_message(filters.command(["uptime", "status"], prefixes=PREFIX))
async def uptime_cmd(client, message):
    await stats_cmd(client, message)

@music.on_message(filters.command("sysinfo", prefixes=PREFIX))
async def sysinfo_cmd(client, message):
    import platform, shutil
    body = (
        f"  ▸  {stylish('python')}   `{platform.python_version()}`\n"
        f"  ▸  {stylish('system')}   `{platform.system()} {platform.release()}`\n"
        f"  ▸  {stylish('ffmpeg')}   `{'yes' if shutil.which('ffmpeg') else 'missing'}`\n"
        f"  ▸  {stylish('core')}     `{CORE_VERSION}`\n"
        f"  ▸  {stylish('uptime')}   `{fmt_uptime()}`\n"
    )
    await message.reply_text(core_card("sysinfo", body))

@music.on_message(filters.command("id", prefixes=PREFIX))
async def id_cmd(client, message):
    u = await _target_user(client, message)
    if not u:
        await message.reply_text("❌ User not found.")
        return
    body = (
        f"  ▸  {stylish('name')}   {u.first_name}\n"
        f"  ▸  {stylish('user')}   @{u.username or '—'}\n"
        f"  ▸  {stylish('id')}     `{u.id}`\n"
        f"  ▸  {stylish('chat')}   `{message.chat.id}`\n"
    )
    await message.reply_text(core_card("identity", body))

@music.on_message(filters.command("chatid", prefixes=PREFIX))
async def chatid_cmd(client, message):
    c = message.chat
    await message.reply_text(core_card("chat", f"  ▸  {c.title or 'private'}\n  ▸  `{c.id}`\n  ▸  {c.type}"))

@music.on_message(filters.command(["whois", "info"], prefixes=PREFIX))
async def whois_cmd(client, message):
    u = await _target_user(client, message)
    if not u:
        await message.reply_text("❌ User not found.")
        return
    body = (
        f"  ▸  {u.first_name} {u.last_name or ''}\n"
        f"  ▸  @{u.username or '—'}\n"
        f"  ▸  `{u.id}`\n"
        f"  ▸  bot: `{bool(u.is_bot)}`  dc: `{getattr(u, 'dc_id', '—')}`\n"
    )
    await message.reply_text(core_card("whois", body))

@music.on_message(filters.command("setname", prefixes=PREFIX) & filters.user(OWNER_ID))
async def setname_cmd(client, message):
    name = _arg(message)
    if not name:
        await message.reply_text("❌ `.setname Your Name`")
        return
    await client.update_profile(first_name=name[:64])
    await message.reply_text(f"✅ {stylish('name set')} → **{name[:64]}**")

@music.on_message(filters.command("setbio", prefixes=PREFIX) & filters.user(OWNER_ID))
async def setbio_ub_cmd(client, message):
    bio = _arg(message)
    if not bio:
        await message.reply_text("❌ `.setbio your bio`")
        return
    await client.update_profile(bio=bio[:70])
    await message.reply_text(f"✅ {stylish('bio set')}")

@music.on_message(filters.command("setpp", prefixes=PREFIX) & filters.user(OWNER_ID))
async def setpp_cmd(client, message):
    r = message.reply_to_message
    if not r or not (r.photo or r.document):
        await message.reply_text("❌ Reply to a photo with `.setpp`")
        return
    path = await r.download()
    try:
        await client.set_profile_photo(photo=path)
        await message.reply_text(f"✅ {stylish('profile photo updated')}")
    finally:
        try:
            os.remove(path)
        except Exception:
            pass

@music.on_message(filters.command("getpp", prefixes=PREFIX))
async def getpp_cmd(client, message):
    u = await _target_user(client, message)
    if not u:
        await message.reply_text("❌ User not found.")
        return
    photos = []
    try:
        async for ph in client.get_chat_photos(u.id, limit=1):
            photos.append(ph)
    except Exception as e:
        await message.reply_text(f"❌ {e}")
        return
    if not photos:
        await message.reply_text("📭 No profile photo.")
        return
    await message.reply_photo(photos[0].file_id, caption=f"{u.first_name} · `{u.id}`")

@music.on_message(filters.command(["time", "date"], prefixes=PREFIX))
async def time_cmd(client, message):
    now = datetime.now()
    await message.reply_text(core_card("clock 🕰️", f"  ⏰  `{now.strftime('%H:%M:%S')}`\n  📅  `{now.strftime('%A, %d %B %Y')}`"))

@music.on_message(filters.command("calc", prefixes=PREFIX))
async def calc_cmd(client, message):
    expr = _arg(message)
    if not expr:
        await message.reply_text("❌ `.calc 2+2*8`")
        return
    if not re.fullmatch(r"[0-9+\-*/().% ]+", expr):
        await message.reply_text("❌ Numbers and + - * / ( ) % only.")
        return
    try:
        val = eval(expr, {"__builtins__": {}}, {})
        await message.reply_text(core_card("calc", f"  `{expr}`\n  = **{val}**"))
    except Exception as e:
        await message.reply_text(f"❌ {e}")

@music.on_message(filters.command("weather", prefixes=PREFIX))
async def weather_cmd(client, message):
    city = _arg(message) or "Delhi"
    url = f"https://wttr.in/{city}?format=j1"
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=timeout, headers={"User-Agent": "devx-core"}) as r:
                data = await r.json(content_type=None)
        cur = (data.get("current_condition") or [{}])[0]
        area = ((data.get("nearest_area") or [{}])[0].get("areaName") or [{}])[0].get("value", city)
        desc = ((cur.get("weatherDesc") or [{}])[0]).get("value", "")
        body = (
            f"  ▸  {area}\n"
            f"  ▸  {desc}\n"
            f"  ▸  {cur.get('temp_C', '?')}°C  (feels {cur.get('FeelsLikeC', '?')}°C)\n"
            f"  ▸  humidity {cur.get('humidity', '?')}% · wind {cur.get('windspeedKmph', '?')} km/h\n"
        )
        await message.reply_text(core_card("weather", body))
    except Exception as e:
        await message.reply_text(f"❌ Weather failed: {e}")

@music.on_message(filters.command("qr", prefixes=PREFIX))
async def qr_cmd(client, message):
    data = _arg(message)
    if not data and message.reply_to_message:
        data = message.reply_to_message.text or ""
    if not data:
        await message.reply_text("❌ `.qr https://example.com`")
        return
    url = "https://api.qrserver.com/v1/create-qr-code/?size=400x400&data=" + aiohttp.helpers.quote(data[:800], safe="")
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        path = os.path.join(TEMP_DIR, f"qr_{uuid.uuid4().hex}.png")
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=timeout) as r:
                if r.status != 200:
                    raise RuntimeError(r.status)
                with open(path, "wb") as f:
                    f.write(await r.read())
        await message.reply_photo(path, caption=f"QR · `{data[:80]}`")
        try:
            os.remove(path)
        except Exception:
            pass
    except Exception as e:
        await message.reply_text(f"❌ QR failed: {e}")

@music.on_message(filters.command("tts", prefixes=PREFIX))
async def tts_cmd(client, message):
    data = _arg(message)
    if not data and message.reply_to_message:
        data = message.reply_to_message.text or ""
    if not data:
        await message.reply_text("❌ `.tts hello world`")
        return
    try:
        from gtts import gTTS
    except Exception:
        await message.reply_text("❌ gTTS not installed. `pip install gTTS`")
        return
    path = os.path.join(TEMP_DIR, f"tts_{uuid.uuid4().hex}.mp3")
    try:
        await asyncio.to_thread(lambda: gTTS(text=data[:400], lang="en").save(path))
        await message.reply_voice(path, caption=data[:80])
    except Exception as e:
        await message.reply_text(f"❌ TTS failed: {e}")
    finally:
        try:
            os.remove(path)
        except Exception:
            pass

@music.on_message(filters.command("fancy", prefixes=PREFIX))
async def fancy_cmd(client, message):
    data = _arg(message) or (message.reply_to_message.text if message.reply_to_message else "")
    if not data:
        await message.reply_text("❌ `.fancy hello`")
        return
    await message.reply_text(stylish(data)[:4000])

@music.on_message(filters.command(["upper", "lower", "reverse"], prefixes=PREFIX))
async def textcase_cmd(client, message):
    data = _arg(message) or (message.reply_to_message.text if message.reply_to_message else "")
    if not data:
        await message.reply_text("❌ Give text.")
        return
    cmd = message.command[0]
    out = data.upper() if cmd == "upper" else (data.lower() if cmd == "lower" else data[::-1])
    await message.reply_text(f"`{out[:4000]}`")

@music.on_message(filters.command("md5", prefixes=PREFIX))
async def md5_cmd(client, message):
    data = _arg(message)
    if not data:
        await message.reply_text("❌ `.md5 text`")
        return
    await message.reply_text(f"`{hashlib.md5(data.encode()).hexdigest()}`")

@music.on_message(filters.command("password", prefixes=PREFIX))
async def password_cmd(client, message):
    n = 16
    if _arg(message).isdigit():
        n = max(8, min(64, int(_arg(message))))
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%"
    pw = "".join(random.choice(alphabet) for _ in range(n))
    await message.reply_text(core_card("password 🔐", f"  `{pw}`"))

@music.on_message(filters.command("notesadd", prefixes=PREFIX))
async def notesadd_cmd(client, message):
    data = _arg(message)
    if not data:
        await message.reply_text("❌ `.notesadd title | body`")
        return
    if "|" in data:
        title, body = [x.strip() for x in data.split("|", 1)]
    else:
        title, body = data[:40], data
    nid = db.add_note(message.from_user.id, title, body)
    await message.reply_text(f"✅📝 note `{nid}` saved — **{title}**")

@music.on_message(filters.command("noteslist", prefixes=PREFIX))
async def noteslist_cmd(client, message):
    rows = db.get_notes(message.from_user.id)
    if not rows:
        await message.reply_text("📭 No notes. `.notesadd title | body`")
        return
    body = ""
    for nid, title, nb, _ts in rows:
        body += f"  `{nid}`  **{title}**\n      {nb[:80]}\n"
    await message.reply_text(core_card("notes", body, ".notesdel <id>"))

@music.on_message(filters.command(["notesdel", "notesdelete"], prefixes=PREFIX))
async def notesdel_cmd(client, message):
    arg = _arg(message)
    if not arg.isdigit():
        await message.reply_text("❌ `.notesdel 3`")
        return
    ok = db.delete_note(message.from_user.id, int(arg))
    await message.reply_text("✅ deleted" if ok else "❌ not found")

@music.on_message(filters.command("flip", prefixes=PREFIX))
async def flip_cmd(client, message):
    await message.reply_text("🪙  **" + random.choice(["HEADS", "TAILS"]) + "**")

@music.on_message(filters.command("dice", prefixes=PREFIX))
async def dice_cmd(client, message):
    await message.reply_text(f"🎲  **{random.randint(1, 6)}**")

@music.on_message(filters.command(["8ball", "eightball"], prefixes=PREFIX))
async def eightball_cmd(client, message):
    ans = random.choice([
        "Yes.", "No.", "Ask again later.", "Absolutely.", "Don't count on it.",
        "The core says maybe.", "Signs point to yes.", "Not today.",
    ])
    await message.reply_text(f"🎱  {ans}")

@music.on_message(filters.command("joke", prefixes=PREFIX))
async def joke_cmd(client, message):
    await message.reply_text("😂  " + random.choice(JOKES))

@music.on_message(filters.command("choose", prefixes=PREFIX))
async def choose_cmd(client, message):
    data = _arg(message)
    parts = [x.strip() for x in re.split(r"\||,", data) if x.strip()]
    if len(parts) < 2:
        await message.reply_text("❌ `.choose red | blue | green`")
        return
    await message.reply_text("👉  **" + random.choice(parts) + "**")

@music.on_message(filters.command("rps", prefixes=PREFIX))
async def rps_cmd(client, message):
    you = _arg(message).lower()
    if you not in ("rock", "paper", "scissors"):
        await message.reply_text("❌ `.rps rock|paper|scissors`")
        return
    botc = random.choice(["rock", "paper", "scissors"])
    win = {("rock", "scissors"), ("paper", "rock"), ("scissors", "paper")}
    if you == botc:
        res = "draw"
    elif (you, botc) in win:
        res = "you win"
    else:
        res = "core wins"
    await message.reply_text(f"you `{you}` · core `{botc}`\n**{res}**")

@music.on_message(filters.command("truth", prefixes=PREFIX))
async def truth_cmd(client, message):
    await message.reply_text("🗣  " + random.choice(TRUTHS))

@music.on_message(filters.command("dare", prefixes=PREFIX))
async def dare_cmd(client, message):
    await message.reply_text("⚡  " + random.choice(DARES))

@music.on_message(filters.command("purge", prefixes=PREFIX))
async def purge_cmd(client, message):
    if not await is_group_admin(client, message.chat.id, message.from_user.id):
        await message.reply_text("❌ Admins only.")
        return
    if not message.reply_to_message:
        await message.reply_text("❌ Reply to the first message to purge from.")
        return
    start = message.reply_to_message.id
    end = message.id
    deleted = 0
    try:
        await client.delete_messages(message.chat.id, list(range(start, end + 1)))
        deleted = end - start + 1
    except Exception as e:
        await message.reply_text(f"❌ {e}")
        return
    msg = await message.reply_text(f"🧹 purged ~{deleted}")
    await asyncio.sleep(2)
    try:
        await msg.delete()
    except Exception:
        pass

@music.on_message(filters.command("pin", prefixes=PREFIX))
async def pin_cmd(client, message):
    if not await is_group_admin(client, message.chat.id, message.from_user.id):
        await message.reply_text("❌ Admins only.")
        return
    if not message.reply_to_message:
        await message.reply_text("❌ Reply to a message with `.pin`")
        return
    await message.reply_to_message.pin()
    await message.reply_text("📌 pinned.")

@music.on_message(filters.command("unpin", prefixes=PREFIX))
async def unpin_cmd(client, message):
    if not await is_group_admin(client, message.chat.id, message.from_user.id):
        await message.reply_text("❌ Admins only.")
        return
    try:
        await client.unpin_chat_message(message.chat.id)
        await message.reply_text("📌 unpinned.")
    except Exception as e:
        await message.reply_text(f"❌ {e}")

@music.on_message(filters.command(["kick"], prefixes=PREFIX))
async def kick_cmd(client, message):
    if not await is_group_admin(client, message.chat.id, message.from_user.id):
        await message.reply_text("❌ Admins only.")
        return
    u = await _target_user(client, message)
    if not u or u.id == message.from_user.id:
        await message.reply_text("❌ Reply to a user or give an id.")
        return
    try:
        await client.ban_chat_member(message.chat.id, u.id)
        await client.unban_chat_member(message.chat.id, u.id)
        await message.reply_text(f"👢 kicked {u.first_name}")
    except Exception as e:
        await message.reply_text(f"❌ {e}")

@music.on_message(filters.command("promote", prefixes=PREFIX))
async def promote_cmd(client, message):
    if not await is_group_admin(client, message.chat.id, message.from_user.id):
        await message.reply_text("❌ Admins only.")
        return
    u = await _target_user(client, message)
    if not u:
        await message.reply_text("❌ Reply to a user.")
        return
    try:
        await client.promote_chat_member(message.chat.id, u.id, can_manage_chat=True, can_delete_messages=True, can_invite_users=True)
        await message.reply_text(f"⬆️ promoted {u.first_name}")
    except Exception as e:
        await message.reply_text(f"❌ {e}")

@music.on_message(filters.command("demote", prefixes=PREFIX))
async def demote_cmd(client, message):
    if not await is_group_admin(client, message.chat.id, message.from_user.id):
        await message.reply_text("❌ Admins only.")
        return
    u = await _target_user(client, message)
    if not u:
        await message.reply_text("❌ Reply to a user.")
        return
    try:
        await client.promote_chat_member(message.chat.id, u.id, can_manage_chat=False)
        await message.reply_text(f"⬇️ demoted {u.first_name}")
    except Exception as e:
        await message.reply_text(f"❌ {e}")

# ==================== RADIO ====================
RADIO_STATIONS = {
    "1": {"name": "BBC World Service", "url": "http://stream.live.vc.bbcmedia.co.uk/bbc_world_service"},
    "2": {"name": "Radio City Hindi", "url": "http://prclive1.listenon.in:9960/"},
    "3": {"name": "Radio Mirchi", "url": "http://node-28.zeno.fm/8f4wk1f77gruv"},
    "4": {"name": "SomaFM Groove Salad", "url": "https://ice1.somafm.com/groovesalad-256-mp3"},
    "5": {"name": "Radio Paradise", "url": "https://stream.radioparadise.com/mp3-192"},
    "6": {"name": "Lofi Radio", "url": "http://usa9.fastcast4u.com/proxy/jamz?mp=/1"},
}

@music.on_message(filters.command("radio", prefixes=PREFIX))
@needs_vc
async def radio_cmd(client, message):
    chat_id = message.chat.id
    if not await can_control(client, chat_id, message.from_user.id):
        await _deny_reply(message)
        return
    arg = _arg(message)
    if not arg:
        body = "\n".join(
            f"  `{n}.` 📻 {s['name']}" for n, s in RADIO_STATIONS.items())
        body += "\n\n  🎵 `.radio 1` — play karo\n  🚫 `.radio off` — band"
        await message.reply_text(core_card("live radio 📻", body, stylish("radio streams")))
        return
    if arg.lower() in ("off", "stop", "0"):
        q = get_queue(chat_id)
        q.songs.clear()
        old = q.current
        q.current = None
        if old is not None:
            _delete_temp_song_files(old)
        try:
            await vc.leave_call(chat_id)
        except Exception:
            pass
        active_calls.discard(chat_id)
        PLAY_TIMERS.pop(chat_id, None)
        await message.reply_text(f"📻 {stylish('Radio band')}.")
        return
    stn = RADIO_STATIONS.get(arg.split()[0])
    if not stn:
        await message.reply_text(f"❌ {stylish('Invalid station')}. `.radio` {stylish('se list dekho')}.")
        return
    song = {
        "title": f"📻 {stn['name']}",
        "duration": 0,
        "uploader": "Live Radio",
        "direct_url": stn["url"],
        "source": "radio",
        "normal": True,
    }
    q = get_queue(chat_id)
    q.songs.clear()
    q.current = song
    q.loop = False
    q.repeat = False
    active_calls.add(chat_id)
    err_notified.discard(chat_id)
    ok = await play_song(chat_id, song, announce=True)
    if not ok:
        q.current = None
        active_calls.discard(chat_id)

@music.on_message(filters.command("sound", prefixes=PREFIX))
async def sound_cmd(client, message):
    q = get_queue(message.chat.id)
    body = (
        f"  🔊 {stylish('loud')}     `{q.loud_level}%`    .loud <0-100>\n"
        f"  📢 {stylish('gain')}     `{q.gain_level}%`    .gain <0-100>\n"
        f"  🚀 {stylish('boost')}    `{q.boost_level}%`   .boost <0-100>\n"
        f"  🎤 {stylish('eco')}      `{q.eco_level}%`     .eco <0-100>\n"
        f"  🔊 {stylish('bass')}     `{q.bass}%`     .bass <0-100>\n"
        f"  🎼 {stylish('treble')}   `{q.treble}%`   .treble <0-100>\n"
        f"  🎚️  {stylish('volume')}   `{q.volume}%`   .volume <0-200>\n"
        f"  ⚡ {stylish('speed')}    `{q.speed}x`   .speed <0.5-2.5>\n"
        f"  ⚡ {stylish('nightcore')} `{'ON' if q.nightcore else 'OFF'}`   .nightcore\n"
        "\n"
        f"  💯 {stylish('MAX LOUD')}: `.max` = {stylish('sab 100')} (Telegram limit) 🚀\n"
        f"  {stylish('e.g.')} `.loud 100` `.bass 100` `.boost 100` `.mix 100`\n"
        f"  `.mix <lvl>` = {stylish('sab ek saath')} · `.mix off` = {stylish('sab 0')}"
    )
    await message.reply_text(core_card("sound control 🔊", body, stylish(".max · .sound · .loud 100")))

@music.on_message(filters.command("max", prefixes=PREFIX))
@needs_vc
async def max_cmd(client, message):
    if not await can_control(client, message.chat.id, message.from_user.id):
        await _deny_reply(message)
        return
    q = get_queue(message.chat.id)
    arg = _arg(message).lower()
    if arg in ("off", "0", "none", "reset"):
        q.loud_level = q.gain_level = q.boost_level = q.eco_level = 0
        q.bass = q.treble = 0
        q.volume = 100
        q.speed = 1.0
        q.nightcore = False
        label = "OFF (sab normal)"
    else:
        q.loud_level = q.gain_level = q.boost_level = q.eco_level = 100
        q.bass = q.treble = 100
        q.volume = 200
        _vol_auto[message.chat.id] = False
        label = "MAX LOUD 💯 (Telegram limit)"
    if q.current:
        await reapply_current_effects(message.chat.id)
    try:
        if vc_ready():
            await vc.change_volume_call(message.chat.id, q.volume)
    except Exception:
        pass
    await message.reply_text(
        f"💯 『{BRAND}』 — {stylish(label)}\n"
        f"🔊 {stylish('loud')} 100 · 📢 {stylish('gain')} 100 · 🚀 {stylish('boost')} 100 · "
        f"🎤 {stylish('eco')} 100\n"
        f"🔊 {stylish('bass')} 100 · 🎼 {stylish('treble')} 100 · 🎚️ {stylish('volume')} 200\n"
        f"💡 `.max off` — {stylish('sab normal')} · `.max`"
    )

@music.on_message(filters.command("speed", prefixes=PREFIX))
@needs_vc
async def speed_cmd(client, message):
    if not await can_control(client, message.chat.id, message.from_user.id):
        await _deny_reply(message)
        return
    q = get_queue(message.chat.id)
    arg = _arg(message)
    if not arg:
        await message.reply_text(
            f"🎚️ Speed: `{q.speed}x`\n"
            f"💡 `.speed 1.25` · `.speed 0.75` · `.speed 1.0` {stylish('(normal)')}"
        )
        return
    try:
        v = float(arg)
        if not (0.5 <= v <= 2.5):
            raise ValueError
    except ValueError:
        await message.reply_text(f"❌ {stylish('Speed')} 0.5–2.5 ({stylish('e.g.')} `.speed 1.25`).")
        return
    q.speed = v
    q.nightcore = False
    if q.current:
        await reapply_current_effects(message.chat.id)
    await message.reply_text(f"⚡ 『{BRAND}』 — Speed: `{v}x`")

@music.on_message(filters.command("nightcore", prefixes=PREFIX))
@needs_vc
async def nightcore_cmd(client, message):
    if not await can_control(client, message.chat.id, message.from_user.id):
        await _deny_reply(message)
        return
    q = get_queue(message.chat.id)
    q.nightcore = not q.nightcore
    if q.nightcore:
        q.speed = 1.0
    state = "ON ⚡" if q.nightcore else "OFF"
    if q.current:
        await reapply_current_effects(message.chat.id)
    await message.reply_text(f"⚡ 『{BRAND}』 — Nightcore: {state}")

@music.on_message(filters.command("bass", prefixes=PREFIX))
@needs_vc
async def bass_cmd(client, message):
    if not await can_control(client, message.chat.id, message.from_user.id):
        await _deny_reply(message)
        return
    q = get_queue(message.chat.id)
    arg = _arg(message).lower()
    if arg in ("off", "0", "none", "reset"):
        q.bass = 0
    elif arg.replace(".", "", 1).isdigit():
        q.bass = max(0, min(100, int(float(arg))))
    else:
        q.bass = 0 if q.bass > 0 else 50
    if q.current:
        await reapply_current_effects(message.chat.id)
    await message.reply_text(
        f"🔊 『{BRAND}』 — Bass boost: `{q.bass}%` (g={q.bass / 5:.1f})\n"
        f"💡 `.bass 20` · `.bass 60` · `.bass off`"
    )

@music.on_message(filters.command("treble", prefixes=PREFIX))
@needs_vc
async def treble_cmd(client, message):
    if not await can_control(client, message.chat.id, message.from_user.id):
        await _deny_reply(message)
        return
    q = get_queue(message.chat.id)
    arg = _arg(message).lower()
    if arg in ("off", "0", "none", "reset"):
        q.treble = 0
    elif arg.replace(".", "", 1).isdigit():
        q.treble = max(0, min(100, int(float(arg))))
    else:
        q.treble = 0 if q.treble > 0 else 50
    if q.current:
        await reapply_current_effects(message.chat.id)
    await message.reply_text(
        f"🎼 『{BRAND}』 — Treble boost: `{q.treble}%`\n"
        f"💡 `.treble 30` · `.treble 70` · `.treble off`"
    )

@music.on_message(filters.command("seek", prefixes=PREFIX))
@needs_vc
async def seek_cmd(client, message):
    if not await can_control(client, message.chat.id, message.from_user.id):
        await _deny_reply(message)
        return
    chat_id = message.chat.id
    q = get_queue(chat_id)
    if not q.current:
        await message.reply_text(f"❌ {stylish('Kuch play nahi ho raha')}.")
        return
    arg = _arg(message)
    try:
        delta = int(float(arg))
    except (ValueError, TypeError):
        await message.reply_text(f"❌ `.seek +30` · `.seek -15` · `.seek 60`")
        return
    cur = int(get_elapsed(chat_id))
    pos = max(0, cur + delta)
    dur = int(q.current.get("duration") or 0)
    if dur and pos >= dur:
        pos = max(0, dur - 2)
    ok = await play_song(chat_id, q.current, announce=False, seek=pos)
    await message.reply_text(
        f"⏩ 『{BRAND}』 — {stylish('Seeked')}: `{fmt_duration(pos)}`"
        if ok else f"❌ {stylish('Seek fail')}"
    )

# ==================== DOWNLOAD REPLY MEDIA ====================
async def download_reply_media(message: Message):
    r = message.reply_to_message
    if r is None:
        return None
    media = None
    ext = "bin"
    title = None
    if r.audio:
        media, ext = r.audio, (r.audio.file_name or "audio.mp3").rsplit(".", 1)[-1]
        title = getattr(r.audio, "title", None) or r.audio.file_name
    elif r.voice:
        media, ext = r.voice, "ogg"
        title = "voice_note.ogg"
    elif r.video:
        media, ext = r.video, (r.video.file_name or "video.mp4").rsplit(".", 1)[-1]
        title = r.video.file_name or "video.mp4"
    elif r.video_note:
        media, ext = r.video_note, "mp4"
        title = "video_note.mp4"
    elif r.document and (r.document.mime_type or "").startswith(("audio/", "video/")):
        media, ext = r.document, (r.document.file_name or "doc.bin").rsplit(".", 1)[-1]
        title = r.document.file_name
    else:
        return None
    path = os.path.join(TEMP_DIR, f"{message.chat.id}_{message.id}.{ext}")
    try:
        await r.download(file_name=path)
        return path
    except Exception as e:
        logger.error("Download error: %s", e)
        return None

# ==================== CALLBACK HANDLER ====================
@music.on_callback_query()
async def cb_handler(client, cb: CallbackQuery):
    data = cb.data
    chat_id = cb.message.chat.id
    q = get_queue(chat_id)
    await cb.answer()

    if data.startswith("cb_pick_"):
        _, _, sid, idx = data.split("_")
        results = search_cache.get(int(sid))
        if not results:
            await cb.answer("❌ Session expired. Search again.", show_alert=True)
            return
        try:
            song = results[int(idx)]
        except (IndexError, ValueError):
            await cb.answer("❌ Invalid selection. Search again.", show_alert=True)
            return
        if not await can_control(client, chat_id, cb.from_user.id):
            await _deny_answer(cb)
            return
        q.songs.append(song)
        _sync_play_loop(q)
        if not q.current:
            q.current = q.songs.popleft()
            active_calls.add(chat_id)
            ok = await play_song(chat_id, q.current)
            if not ok:
                q.current = None
                try:
                    await cb.message.edit_text(
                        f"❌ 『{BRAND}』 — {stylish('Play nahi ho paya')}\n"
                        f"{stylish('Voice chat check karo')} → `.diag`",
                        reply_markup=main_menu(),
                    )
                except Exception:
                    pass
        else:
            await cb.message.edit_text(
                f"✅ **𝐃𝐄𝐕 𝐗 𝐂𝐎𝐑𝐄** — Added:\n🎵 {song['title']}\n"
                f"⏱️ {fmt_duration(song['duration'])} — Position {len(q.songs)}",
                reply_markup=player_panel(q),
            )
        return

    if data.startswith("cb_npick_"):
        _, _, sid, idx = data.split("_")
        results = search_cache.get(int(sid))
        if not results:
            await cb.answer("❌ Session expired. Search again.", show_alert=True)
            return
        try:
            song = results[int(idx)]
        except (IndexError, ValueError):
            await cb.answer("❌ Invalid selection. Search again.", show_alert=True)
            return
        if not await can_control(client, chat_id, cb.from_user.id):
            await _deny_answer(cb)
            return
        song["normal"] = True
        q.songs.append(song)
        _sync_play_loop(q)
        if not q.current:
            q.current = q.songs.popleft()
            active_calls.add(chat_id)
            ok = await play_song(chat_id, q.current)
            if not ok:
                q.current = None
                try:
                    await cb.message.edit_text(
                        f"❌ 『{BRAND}』 — {stylish('Play nahi ho paya')}\n"
                        f"{stylish('Voice chat check karo')} → `.diag`",
                        reply_markup=main_menu(),
                    )
                except Exception:
                    pass
        else:
            await cb.message.edit_text(
                f"✅ **𝐃𝐄𝐕 𝐗 𝐂𝐎𝐑𝐄** — Added (natural 🎧):\n🎵 {song['title']}\n"
                f"⏱️ {fmt_duration(song['duration'])} — Position {len(q.songs)}",
                reply_markup=player_panel(q),
            )
        return

    if data == "cb_cancel":
        try:
            await cb.message.edit_text("❌ Cancelled.", reply_markup=main_menu())
        except Exception:
            pass
        return

    if data.startswith("cb_qpage_"):
        page = int(data.split("_")[-1])
        text, kb = queue_panel(q, page=page)
        try:
            await cb.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass
        return

    if data == "cb_files":
        await cb.answer("❌ Files system removed — use .play only", show_alert=True)
        return

    if data == "cb_delall":
        await cb.answer("❌ Files system removed", show_alert=True)
        return

    if data in CONTROL_CALLBACKS if 'CONTROL_CALLBACKS' in globals() else False:
        pass

    CONTROL_CALLBACKS_LOCAL = {
        "cb_pause", "cb_resume", "cb_skip", "cb_stop", "cb_loop",
        "cb_repeat", "cb_boost", "cb_volup", "cb_voldown", "cb_play", "cb_voice",
        "cb_mute", "cb_vol200", "cb_vol_10", "cb_vol_50", "cb_vol_100", "cb_vol_150",
    }
    if data in CONTROL_CALLBACKS_LOCAL and not await can_control(client, chat_id, cb.from_user.id):
        await _deny_answer(cb)
        return

    if data == "cb_play":
        awaiting[(chat_id, cb.from_user.id)] = "play"
        await cb.message.reply_text("▶️ **𝐃𝐄𝐕 𝐗 𝐂𝐎𝐑𝐄**\nSend me the song name or YouTube URL:")
        return
    if data == "cb_voice":
        awaiting[(chat_id, cb.from_user.id)] = "voice"
        await cb.message.reply_text(f"🎤 **{BOT_NAME}**\nReply to a voice/audio/video, then `{PREFIX}run` (loop) or `{PREFIX}play`.")
        return
    if data == "cb_panel":
        await cb.message.reply_text(
            core_card("player deck", stylish("transport  ·  loop  ·  boost  ·  volume")),
            reply_markup=player_panel(q),
        )
        return
    if data == "cb_menu":
        await cb.message.reply_text(hub_home_text(), reply_markup=hub_keyboard())
        return
    if data.startswith("hub_"):
        page = data[4:]
        if page == "music":
            await cb.message.reply_text(page_music_text(), reply_markup=hub_keyboard())
        elif page == "tools":
            await cb.message.reply_text(page_tools_text(), reply_markup=hub_keyboard())
        elif page == "fun":
            await cb.message.reply_text(page_fun_text(), reply_markup=hub_keyboard())
        elif page == "help":
            await help_cmd(client, cb.message)
        elif page == "alive":
            await alive_cmd(client, cb.message)
        elif page == "about":
            await cb.message.reply_text(
                core_card(
                    "about",
                    f"  {stylish('youtube + saavn + voice + video')}\n"
                    f"  {stylish('simple song play bot')}\n"
                    f"  {stylish('version')} `{CORE_VERSION}`",
                ),
                reply_markup=hub_keyboard(),
            )
        return
    if data == "cb_about":
        await cb.message.reply_text(
            core_card(
                "about",
                f"  {stylish('youtube + saavn + voice + video')}\n"
                f"  {stylish('simple song play bot')}\n"
                f"  {stylish('version')} `{CORE_VERSION}`",
            ),
            reply_markup=hub_keyboard(),
        )
        return
    if data == "cb_admin":
        if not is_core_admin(cb.from_user.id):
            await cb.answer("❌ Owner / core admin only", show_alert=True)
            return
        await cb.message.reply_text(page_admin_text(), reply_markup=owner_panel())
        return
    if data == "cb_addadmin":
        if cb.from_user.id != OWNER_ID:
            await cb.answer("❌ Sirf OWNER admin bana sakta hai 👑", show_alert=True)
            return
        awaiting[(chat_id, cb.from_user.id)] = "addadmin"
        await cb.message.reply_text("👑 **Admin Add**\nUser ID bhejo (digits):")
        return
    if data == "cb_deladmin":
        if cb.from_user.id != OWNER_ID:
            await cb.answer("❌ Sirf OWNER admin hata sakta hai 👑", show_alert=True)
            return
        awaiting[(chat_id, cb.from_user.id)] = "deladmin"
        await cb.message.reply_text("🗑️ **Admin Del**\nUser ID bhejo (digits):")
        return
    if data == "cb_adminlist":
        if not is_core_admin(cb.from_user.id):
            await cb.answer("❌ Owner / core admin only", show_alert=True)
            return
        await alist_cmd(client, cb.message)
        return
    if data == "cb_users":
        if not is_core_admin(cb.from_user.id):
            await cb.answer("❌ Owner / core admin only", show_alert=True)
            return
        await users_cmd(client, cb.message)
        return
    if data == "cb_servers":
        if not is_core_admin(cb.from_user.id):
            await cb.answer("❌ Owner / core admin only", show_alert=True)
            return
        await servers_cmd(client, cb.message)
        return
    if data == "cb_broadcast":
        if not is_core_admin(cb.from_user.id):
            await cb.answer("❌ Owner / core admin only", show_alert=True)
            return
        awaiting[(chat_id, cb.from_user.id)] = "broadcast"
        await cb.message.reply_text("📢 **𝐃𝐄𝐕 𝐗 𝐂𝐎𝐑𝐄**\nSend the broadcast message text:")
        return
    if data == "cb_volume":
        await cb.message.reply_text(f"🎚️ **𝐃𝐄𝐕 𝐗 𝐂𝐎𝐑𝐄 — Volume**\nCurrent: {fmt_vol(q.volume)}", reply_markup=volume_panel(q))
        return
    if data == "cb_lock":
        if not await is_group_admin(client, chat_id, cb.from_user.id):
            await cb.answer("❌ Group admins only", show_alert=True)
            return
        db.set_config(f"lock:{chat_id}", "1")
        await _deny_answer(cb)
        return
    if data == "cb_unlock":
        if not await is_group_admin(client, chat_id, cb.from_user.id):
            await cb.answer("❌ Group admins only", show_alert=True)
            return
        db.set_config(f"lock:{chat_id}", "0")
        await cb.answer("🔓 Unlocked", show_alert=True)
        return

    if data in CONTROL_CALLBACKS_LOCAL and not vc_ready():
        await cb.answer("❌ Voice engine not running — check /status", show_alert=True)
        return

    if data == "cb_pause":
        try:
            await vc.pause(chat_id); r = "⏸️ Paused"
            _pause_timer(chat_id)
        except Exception:
            r = "❌ Nothing playing"
    elif data == "cb_resume":
        try:
            await vc.resume(chat_id); r = "▶️ Resumed"
            _resume_timer(chat_id)
        except Exception:
            r = "❌ Nothing paused"
    elif data == "cb_skip":
        if q.songs:
            old = q.current
            q.current = q.songs.popleft()
            await play_song(chat_id, q.current)
            if old is not None:
                _delete_temp_song_files(old)
            r = "⏭️ Skipped"
        else:
            old = q.current
            q.current = None
            if old is not None:
                _delete_temp_song_files(old)
            try:
                await vc.leave_call(chat_id)
            except Exception:
                pass
            active_calls.discard(chat_id)
            PLAY_TIMERS.pop(chat_id, None)
            r = "⏭️ Skipped (empty)"
    elif data == "cb_stop":
        old = q.current
        queued = list(q.songs)
        q.songs.clear(); q.current = None; q.loop = False; q.repeat = False
        q.silent_loop = False
        if old is not None:
            _delete_temp_song_files(old)
        for _s in queued:
            _delete_temp_song_files(_s)
        active_calls.discard(chat_id)
        PLAY_TIMERS.pop(chat_id, None)
        try:
            await vc.leave_call(chat_id); r = "⏹️ Stopped"
        except Exception:
            r = "❌ Not in call"
    elif data == "cb_loop":
        q.loop = not q.loop
        if q.loop:
            q.repeat = False
            q.silent_loop = True
        else:
            q.silent_loop = False
        r = f"🔁 Loop {'ON' if q.loop else 'OFF'}"
    elif data == "cb_repeat":
        q.repeat = not q.repeat
        if q.repeat:
            q.loop = False
            q.silent_loop = True
        else:
            q.silent_loop = False
        if q.current:
            await reapply_current_effects(chat_id)
        r = f"🔂 Repeat {'ON' if q.repeat else 'OFF'}"
    elif data == "cb_boost":
        q.boost_level = 0 if q.boost_level > 0 else 100
        if q.current:
            await reapply_current_effects(chat_id)
        r = f"🚀 Boost {q.boost_level}%"
    elif data == "cb_volup":
        q.volume = min(200, q.volume + 10)
        _vol_auto[chat_id] = False
        try:
            await vc.change_volume_call(chat_id, q.volume)
            _last_vol_applied[chat_id] = q.volume
        except Exception:
            pass
        r = f"🔊 {fmt_vol(q.volume)}"
    elif data == "cb_voldown":
        q.volume = max(0, q.volume - 10)
        _vol_auto[chat_id] = False
        try:
            await vc.change_volume_call(chat_id, q.volume)
            _last_vol_applied[chat_id] = q.volume
        except Exception:
            pass
        r = f"🔉 {fmt_vol(q.volume)}"
    elif data == "cb_vol200":
        q.volume = 200
        _vol_auto[chat_id] = False
        try:
            await vc.change_volume_call(chat_id, 200)
            _last_vol_applied[chat_id] = 200
        except Exception:
            pass
        r = "🔊 200%"
    elif data == "cb_vol_10":
        q.volume = 10
        _vol_auto[chat_id] = False
        try:
            await vc.change_volume_call(chat_id, 10)
            _last_vol_applied[chat_id] = 10
        except Exception:
            pass
        r = "🎚️ 10%"
    elif data == "cb_vol_50":
        q.volume = 50
        _vol_auto[chat_id] = False
        try:
            await vc.change_volume_call(chat_id, 50)
            _last_vol_applied[chat_id] = 50
        except Exception:
            pass
        r = "🎚️ 50%"
    elif data == "cb_vol_100":
        q.volume = 100
        _vol_auto[chat_id] = False
        try:
            await vc.change_volume_call(chat_id, 100)
            _last_vol_applied[chat_id] = 100
        except Exception:
            pass
        r = "🎚️ 100%"
    elif data == "cb_vol_150":
        q.volume = 150
        _vol_auto[chat_id] = False
        try:
            await vc.change_volume_call(chat_id, 150)
            _last_vol_applied[chat_id] = 150
        except Exception:
            pass
        r = "🎚️ 150%"
    elif data == "cb_mute":
        if _muted_state.get(chat_id):
            try:
                await vc.unmute(chat_id); r = "🔊 Unmuted"
            except Exception:
                r = "❌ Not in call"
            _muted_state[chat_id] = False
        else:
            try:
                await vc.mute(chat_id); r = "🔇 Muted"
            except Exception:
                r = "❌ Not in call"
            _muted_state[chat_id] = True
    elif data == "cb_sound":
        await sound_cmd(client, cb.message)
        return
    elif data == "cb_queue":
        text, kb = queue_panel(q, page=0)
        await cb.message.reply_text(text, reply_markup=kb)
        return
    elif data == "cb_now":
        await now_cmd(client, cb.message)
        return
    elif data == "cb_stats":
        await stats_cmd(client, cb.message)
        return
    elif data == "cb_help":
        await help_cmd(client, cb.message)
        return
    else:
        return

    try:
        await cb.message.edit_text(
            "🎛️ **𝐃𝐄𝐕 𝐗 𝐂𝐎𝐑𝐄 — Player Panel**\n" + r, reply_markup=player_panel(q)
        )
    except Exception:
        pass

# ==================== HELP CMD ====================
@music.on_message(filters.command(["help", "cmds"], prefixes=PREFIX))
async def help_cmd(client, message):
    await _show_help_page(message)

async def _show_help_page(message, page_key="hpg_music"):
    text = page_music_text()
    await message.reply_text(text, reply_markup=hub_keyboard())

# ==================== AWAITING HANDLER ====================
@music.on_message(filters.text)
async def awaiting_handler(client, message):
    if (message.text or "").startswith(PREFIX):
        return
    key = (message.chat.id, message.from_user.id)

    ls = last_search.get(key)
    if ls and (time.time() - ls.get("ts", 0)) < 600:
        spec = (message.text or "").strip()
        if spec:
            results = search_cache.get(ls.get("sid"))
            if results:
                try:
                    if spec.lower() in ("all", "*", "etc"):
                        indices = list(range(len(results)))
                    else:
                        indices = [int(x) - 1 for x in spec.replace(" ", "").split(",") if x.strip().isdigit()]
                        indices = [i for i in indices if 0 <= i < len(results)]
                except Exception:
                    indices = []
                if indices:
                    last_search.pop(key, None)
                    natural = ls.get("natural", False)
                    started, queued, first = await _play_picked_songs(
                        message, ls["sid"], indices, natural=natural)
                    if started or queued:
                        tag = "🎧 natural" if natural else "🎵"
                        extra = f" +{queued - 1} more" if queued > 1 else ""
                        await message.reply_text(
                            f"✅ 『{BRAND}』 — {stylish('Selected')}: {queued} {tag}{extra}\n"
                            f"🎵 {first}"
                        )
                    return

    if key not in awaiting:
        return
    purpose = awaiting.pop(key)
    if purpose == "play":
        message.text = f"{PREFIX}play {message.text}"
        message.command = message.text.split()
        await play_cmd(client, message)
    elif purpose == "voice":
        await message.reply_text(f"🎤 Reply to a voice/audio/video and send `{PREFIX}play`.")
    elif purpose == "broadcast":
        if not is_core_admin(message.from_user.id):
            return
        text = "📢 **𝐃𝐄𝐕 𝐗 𝐂𝐎𝐑𝐄 — Announcement**\n\n" + message.text
        users = db.get_users()
        ok = fail = 0
        st = await message.reply_text("📢 Broadcasting…")
        for u in users:
            try:
                await client.send_message(u[0], text)
                ok += 1
            except Exception:
                fail += 1
            await asyncio.sleep(0.05)
        await st.edit_text(f"✅ Sent to {ok} users. Failed: {fail}", reply_markup=owner_panel())
    elif purpose == "addadmin":
        if message.from_user.id != OWNER_ID:
            return
        if not (message.text or "").strip().lstrip("-").isdigit():
            await message.reply_text("❌ Valid User ID bhejo (digits).")
            return
        uid = int(message.text.strip())
        add_core_admin(uid)
        await message.reply_text(f"✅ 👑 `{uid}` ab **core admin** hai!")
    elif purpose == "deladmin":
        if message.from_user.id != OWNER_ID:
            return
        if not (message.text or "").strip().lstrip("-").isdigit():
            await message.reply_text("❌ Valid User ID bhejo (digits).")
            return
        uid = int(message.text.strip())
        if uid == OWNER_ID:
            await message.reply_text("❌ Owner ko remove nahi kar sakte. 👑")
            return
        del_core_admin(uid)
        await message.reply_text(f"🗑️ 👑 `{uid}` admin list se hata diya.")

# ==================== TOKEN BOT (LOGIN HELPER) ====================
def clean_phone(text):
    return "".join(ch for ch in text if ch.isdigit() or ch == "+")

async def safe_cleanup(client):
    try:
        await client.disconnect()
    except Exception:
        pass

async def start_music():
    global vc, music_started
    if music_started:
        return
    if not SESSION_STRING:
        logger.error("start_music() called but no session string.")
        return
    compat_ok, compat_msg = check_engine_compat()
    if not compat_ok:
        logger.error(compat_msg)
        print("\n  ❌ " + compat_msg.replace("\n", "\n     ") + "\n")
    try:
        music.session_string = SESSION_STRING
        music.storage = MemoryStorage(SESSION_NAME, SESSION_STRING)
        await music.start()
        logger.info("Music userbot started")
        vc = PyTgCalls(music)
        if hasattr(vc, "add_handler"):
            vc.add_handler(on_stream_end)
        else:
            vc.on_stream_end(on_stream_end)
        await vc.start()
        logger.info("PyTgCalls started")
        asyncio.create_task(auto_enforce_loop())
        music_started = True
        logger.info("%s is running…", BOT_NAME)
    except Exception as e:
        import traceback as _tb
        logger.error("Music start error: %s", e)
        _tb.print_exc()
        vc = None
        music_started = False
        if isinstance(e, ImportError) and "GroupcallForbidden" in str(e):
            print("\n" + "=" * 55)
            print("  ❌ FOUND THE KNOWN VERSION BUG:")
            print("     py-tgcalls >= 2.2.6 needs 'GroupcallForbidden'")
            print("     which pyrogram 2.0.106 does not have.")
            print("  🔧 FIX: pip install \"pyrogram==2.0.106\" \"py-tgcalls[pyrogram]==2.2.5\"")
            print("=" * 55 + "\n")

@bot.on_message(filters.command("start"))
async def bot_start(client, message):
    me = await bot.get_me()
    await message.reply_text(
        f"🤖 **𝐃𝐄𝐕 𝐗 𝐂𝐎𝐑𝐄 — Login Bot** (@{me.username})\n\n"
        f"👤 Your User ID: `{message.from_user.id}`\n\n"
        "Commands:\n"
        "/login — log in your user account (for music)\n"
        "/me — see your ID\n"
        "/status — check login/music status\n\n"
        "⚠️ /login can only be used by the owner."
    )

@bot.on_message(filters.command("status"))
async def bot_status(client, message):
    if message.from_user.id != OWNER_ID:
        await message.reply_text("❌ Owner only.")
        return
    try:
        from importlib.metadata import version as _dist_version
        pt_version = _dist_version("py-tgcalls")
    except Exception:
        pt_version = "?"
    compat_ok, compat_msg = check_engine_compat()
    lines = [
        "📊 **𝐃𝐄𝐕 𝐗 𝐂𝐎𝐑𝐄 — Status**",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"🔑 Session string: {'✅ EXISTS' if SESSION_STRING else '❌ NOT SET'}",
        f"🎵 Music bot: {'✅ RUNNING' if music_started else '❌ NOT STARTED'}",
        f"💾 Session file: {'✅ FOUND' if os.path.exists(SESSION_FILE) else '❌ NOT FOUND'}",
        f"🔗 pytgcalls: `{pt_version}` · {'✅ compatible' if compat_ok else '❌ INCOMPATIBLE'}",
        f"🎭 Mode: `{get_mode()}`",
    ]
    if not compat_ok:
        lines.append("💊 Fix: `pip install \"pyrogram==2.0.106\" \"py-tgcalls[pyrogram]==2.2.5\"` then restart.")
    await message.reply_text("\n".join(lines))

@bot.on_message(filters.command("me"))
async def bot_me(client, message):
    uid = message.from_user.id
    owner = "✅ You ARE the owner!" if uid == OWNER_ID else "❌ You are NOT the owner."
    await message.reply_text(
        f"👤 **Your User ID:** `{uid}`\n"
        f"🔑 **Bot OWNER_ID:** `{OWNER_ID}`\n\n{owner}\n\n"
        "If you are the owner but /login is not working, send me your ID."
    )

@bot.on_message(filters.command("login"))
async def bot_login(client, message):
    if message.from_user.id != OWNER_ID:
        await message.reply_text(
            f"❌ You are not the owner.\nYour ID: `{message.from_user.id}`\nOwner ID: `{OWNER_ID}`\n\nCheck with /me."
        )
        return
    login_state[message.from_user.id] = {"step": "phone"}
    await message.reply_text(
        "📱 **Send your Telegram phone number**\n\n"
        "Format: `+91XXXXXXXXXX`\n\n"
        "This is the SAME account the music bot will use."
    )

@bot.on_message(filters.text & ~filters.command(["start", "login", "me", "status"]))
async def bot_text(client, message):
    global SESSION_STRING
    user_id = message.from_user.id
    st = login_state.get(user_id)
    if not st:
        return

    step = st["step"]

    if step == "phone":
        phone = clean_phone(message.text)
        if not phone.startswith("+") or len(phone) < 8:
            await message.reply_text("❌ Send a valid number. Format: `+91XXXXXXXXXX`")
            return
        user_client = Client(f"tmp_login_{user_id}", api_id=API_ID, api_hash=API_HASH, in_memory=True)
        try:
            await user_client.connect()
            sent = await user_client.send_code(phone)
            st.update({"step": "code", "phone": phone, "hash": sent.phone_code_hash, "user_client": user_client})
            await message.reply_text(
                "🔐 **OTP sent!**\n\n"
                "The code will arrive via Telegram app/SMS.\nNow send the **code here** (digits only)."
            )
        except FloodWait as e:
            await safe_cleanup(user_client); login_state.pop(user_id, None)
            await message.reply_text(f"⏳ FloodWait: wait {e.value} sec, then /login again.")
        except PhoneNumberInvalid:
            await safe_cleanup(user_client); login_state.pop(user_id, None)
            await message.reply_text(
                "❌ **Invalid phone number.**\n\n"
                "Send it like this:\n`+91XXXXXXXXXX`\n"
                "(country code + full number, with + sign)"
            )
        except PhoneNumberUnoccupied:
            await safe_cleanup(user_client); login_state.pop(user_id, None)
            await message.reply_text(
                "❌ **This number is NOT registered on Telegram.**\n\n"
                "Create a Telegram account with this number first "
                "(install Telegram, sign up), then try /login again."
            )
        except PhoneNumberBanned:
            await safe_cleanup(user_client); login_state.pop(user_id, None)
            await message.reply_text("❌ This number is banned by Telegram.")
        except Exception as e:
            logger.error("Login error (phone step): %s", e)
            await safe_cleanup(user_client); login_state.pop(user_id, None)
            await message.reply_text(f"❌ Error: {e}")
        return

    if step == "code":
        user_client = st["user_client"]
        try:
            await user_client.sign_in(st["phone"], st["hash"], message.text.strip())
            ss = await user_client.export_session_string()
            await safe_cleanup(user_client)

            with open(SESSION_FILE, "w") as f:
                f.write(ss)
            SESSION_STRING = ss
            login_state.pop(user_id, None)

            logger.info("Login SUCCESS for user %s", user_id)
            await message.reply_text("✅ **Login successful!**\n\nMusic bot is **starting now**…")
            await start_music()
            await message.reply_text(
                "🎉 **𝐃𝐄𝐕 𝐗 𝐂𝐎𝐑𝐄 active!**\n\n"
                "The music bot is now ready. Start a voice chat in your group and use `.play <song>`!"
            )
        except SessionPasswordNeeded:
            hint = ""
            try:
                hint = await user_client.get_password_hint()
            except Exception:
                pass
            st["step"] = "2fa"
            await message.reply_text(
                "🔒 **2FA password is enabled!**\n\n" + (f"Hint: {hint}\n" if hint else "") + "Send your password."
            )
        except PhoneCodeInvalid:
            await message.reply_text("❌ Invalid code. Send the correct code.")
        except PhoneCodeExpired:
            try:
                sent = await user_client.send_code(st["phone"])
                st["hash"] = sent.phone_code_hash
                await message.reply_text(
                    "⏳ Code expired. I sent a **NEW code** — check and send it quickly."
                )
            except Exception as e2:
                logger.error("Resend code error: %s", e2)
                login_state.pop(user_id, None)
                await message.reply_text(f"❌ Could not resend: {e2}. Use /login again.")
        except FloodWait as e:
            await message.reply_text(f"⏳ FloodWait: {e.value} sec.")
        except Exception as e:
            logger.error("Login error (code step): %s", e)
            login_state.pop(user_id, None)
            await message.reply_text(f"❌ Error: {e}")
        return

    if step == "2fa":
        user_client = st["user_client"]
        try:
            await user_client.check_password(message.text)
            ss = await user_client.export_session_string()
            await safe_cleanup(user_client)

            with open(SESSION_FILE, "w") as f:
                f.write(ss)
            SESSION_STRING = ss
            login_state.pop(user_id, None)

            logger.info("Login SUCCESS (2fa) for user %s", user_id)
            await message.reply_text("✅ **Login successful!**\n\nMusic bot is **starting now**…")
            await start_music()
            await message.reply_text("🎉 **𝐃𝐄𝐕 𝐗 𝐂𝐎𝐑𝐄 active!** The music bot is ready!")
        except PasswordHashInvalid:
            await message.reply_text("❌ Invalid password. Send it again.")
        except FloodWait as e:
            await message.reply_text(f"⏳ FloodWait: {e.value} sec.")
        except Exception as e:
            login_state.pop(user_id, None)
            await message.reply_text(f"❌ Error: {e}")
        return

# ==================== MAIN ====================
async def main():
    global SESSION_STRING
    import shutil

    print("=" * 55)
    print(" DEV X CORE  v4  —  SIMPLE MUSIC USERBOT")
    print("=" * 55)

    if shutil.which("ffmpeg") is None:
        print("  ❌ WARNING: ffmpeg NOT FOUND!")
        print("     Music will NOT play without ffmpeg.")
        print("     Install: sudo apt install -y ffmpeg")
        print("=" * 55)
    else:
        print(f"  ✅ ffmpeg: {shutil.which('ffmpeg')}")

    compat_ok, compat_msg = check_engine_compat()
    if compat_ok:
        print("  ✅ py-tgcalls + pyrogram versions: compatible")
    else:
        print("  ❌ " + compat_msg.replace("\n", "\n     "))
        print("=" * 55)

    try:
        await bot.start()
        me = await bot.get_me()
        print(f"  ✅ Login bot started: @{me.username}")
        print(f"  ✅ Owner ID: {OWNER_ID}")
    except Exception as e:
        print(f"  ❌ Login bot start FAILED: {e}")
        await asyncio.sleep(2)

    if SESSION_STRING:
        print("  🔑 Session string found — starting music bot…")
        await start_music()
    else:
        print()
        print("  👉 Now send /login to your bot on Telegram")
        print("     (enter number + OTP directly in Telegram)")
        print("     The music bot will start automatically after login!")

    print("=" * 55)

    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        LOOP.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user.")
    finally:
        try:
            db.close()
        except Exception:
            pass
