# 🎵 DEV X CORE — Telegram Music Userbot (Single File)

All-in-one Telegram music userbot built with **Pyrogram** + **PyTgCalls** +
**yt-dlp**. Includes a login helper bot, saved-file vault, sound effects
(loud/boost/echo/bass/treble), sequential loops, GIF banners, and more.

## Features

| Feature | Description |
|---------|-------------|
| `/login` (helper bot) | Log in your user account via phone + OTP — music bot starts automatically |
| `.play <song>` | Search YouTube/Saavn/SoundCloud/Spotify/Deezer and play |
| `.nplay <song>` | Natural sound — no boost/echo/gain effects |
| `.run` / `.run 1,2,3` / `.run all` | Loop saved files sequentially (silent, no spam) |
| `.vrun` | Loop video files |
| `.rjoin` | Auto-join voice chat when it starts |
| `.boost` / `.eco` / `.gain` / `.loud` / `.bass` / `.treble` / `.max` | Sound effects 0–100 |
| `.volume <0-200>` | Volume control |
| `.pause` / `.resume` / `.skip` / `.stop` | Playback control |
| `.queue` / `.now` / `.speed` / `.nightcore` / `.seek` | Queue and playback tools |
| `.files` / `.del` / `.delall` | Saved-file vault |
| `.set` | GIF banner attached to menu/help |
| `.lyrics` / `.radio` / `.playlist` / `.gban` / `.backup` | Extra utilities |

## Quick Start

```bash
# 1. Clone / extract
cd devx-music-bot

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy env and fill in values
cp .env.example .env
# Edit .env with your API_ID, API_HASH, BOT_TOKEN, OWNER_ID

# 4. Install ffmpeg (REQUIRED for audio playback)
sudo apt-get update && sudo apt-get install -y ffmpeg

# 5. Run
python music.py
```

Then send **`/login`** to your helper bot on Telegram, enter your phone number + OTP,
and the music userbot starts automatically.

## Deploy on Render

1. Create a new **Web Service** on Render, connect your GitHub repo
2. Build command: `pip install -r requirements.txt`
3. Start command: `python music.py`
4. Add env vars: `API_ID`, `API_HASH`, `BOT_TOKEN`, `OWNER_ID`, `SESSION_STRING` (optional)
5. **Important:** Render's free tier may not support long-running voice bots reliably.
   A VPS with `systemd` or `screen`/`tmux` is recommended for 24/7 operation.

## Version Fix (important)

If you see `'NoneType' object has no attribute 'play'` on `.play`, your
py-tgcalls version is incompatible:

```bash
pip install "pyrogram==2.0.106" "py-tgcalls[pyrogram]==2.2.5"
```

## Files

| File | Purpose |
|------|---------|
| `music.py` | Main bot — everything in one file |
| `requirements.txt` | Python dependencies (pinned versions) |
| `.env.example` | Environment variables template |
| `README.md` | This file |
| `session_string.txt` | Auto-created after first login |
| `devx_music.db` | Auto-created SQLite database |

## Support

👤 `@DEV_AI_REMASTERED`
