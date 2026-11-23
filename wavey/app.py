import os
import re
import sqlite3
from flask import Flask, jsonify, render_template, request, send_from_directory
import yt_dlp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")
DB_PATH = os.path.join(BASE_DIR, "music.db")

os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "templates"), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "static"), exist_ok=True)

app = Flask(__name__)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS favorites (
            id TEXT PRIMARY KEY,
            title TEXT,
            artist TEXT,
            duration TEXT,
            thumbnail TEXT,
            webpage_url TEXT,
            source TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def fmt_duration(sec):
    if not sec:
        return ""
    try:
        total = int(float(sec))
        return f"{total // 60}:{total % 60:02d}"
    except Exception:
        return ""


def safe_filename(name):
    cleaned = re.sub(r'[\\/*?:"<>|]+', "_", name).strip()
    return cleaned[:100] or "track"


def ydl_search_entries(search_expr, limit=10):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "default_search": "ytsearch",
        "noplaylist": True,
        "playlistend": limit,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(search_expr, download=False)
    if not info:
        return []
    entries = info.get("entries")
    if entries is None:
        return [info]
    return [e for e in entries if e]


def extract_best_audio_url(target):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "skip_download": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(target, download=False)
        if info and "entries" in info and info["entries"]:
            info = info["entries"][0]
        if info and info.get("url"):
            return info["url"]
    return None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/downloads/<path:filename>")
def serve_download(filename):
    return send_from_directory(DOWNLOADS_DIR, filename, as_attachment=False)


@app.route("/api/search")
def api_search():
    q = (request.args.get("q") or "").strip()
    source = (request.args.get("source") or "yt").strip().lower()
    if not q:
        return jsonify({"tracks": []})

    source_queries = {
        "star": [f"ytsearch10:{q}"],
        "spotify": [f"ytsearch10:{q} official audio"],
        "yt": [f"ytsearch10:{q}"],
        "apple": [f"ytsearch10:{q} topic"],
        "wave_green": [f"scsearch10:{q}", f"ytsearch10:{q}"],
    }
    candidates = source_queries.get(source, [f"ytsearch10:{q}"])

    tracks = []
    seen_ids = set()

    for expr in candidates:
        try:
            raw_items = ydl_search_entries(expr, limit=10)
            for idx, item in enumerate(raw_items):
                if item.get("_type") == "playlist":
                    continue
                title = (item.get("title") or "").strip()
                if not title:
                    continue
                artist = (
                    item.get("artist")
                    or item.get("uploader")
                    or item.get("channel")
                    or ""
                ).strip()
                webpage_url = item.get("webpage_url") or item.get("url") or ""
                if webpage_url and not webpage_url.startswith("http"):
                    if source == "wave_green":
                        webpage_url = f"https://soundcloud.com/{webpage_url.lstrip('/')}"
                    else:
                        webpage_url = f"https://www.youtube.com/watch?v={webpage_url}"
                thumb = item.get("thumbnail") or ""
                if not thumb and isinstance(item.get("thumbnails"), list) and item["thumbnails"]:
                    thumb = item["thumbnails"][-1].get("url") or ""
                item_id = str(item.get("id") or f"{source}_{idx}_{abs(hash(title + artist))}")
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                tracks.append(
                    {
                        "id": item_id,
                        "title": title,
                        "artist": artist,
                        "duration": fmt_duration(item.get("duration")),
                        "thumbnail": thumb or "/static/icon.png",
                        "webpage_url": webpage_url,
                        "source": source,
                    }
                )
            if tracks:
                break
        except Exception:
            continue

    return jsonify({"tracks": tracks})


@app.route("/api/stream")
def api_stream():
    webpage_url = (request.args.get("url") or "").strip()
    title = (request.args.get("title") or "").strip()
    artist = (request.args.get("artist") or "").strip()

    candidates = []
    if webpage_url:
        candidates.append(webpage_url)
    if title:
        candidates.append(f"ytsearch1:{artist} {title}".strip())
        candidates.append(f"ytsearch1:{title}".strip())

    for target in candidates:
        try:
            stream_url = extract_best_audio_url(target)
            if stream_url:
                return jsonify({"stream_url": stream_url})
        except Exception:
            continue

    return jsonify({"error": "Не удалось получить аудиопоток"}), 404


@app.route("/api/download", methods=["POST"])
def api_download():
    data = request.get_json(silent=True) or {}
    title = data.get("title") or "track"
    artist = data.get("artist") or ""
    webpage_url = data.get("webpage_url") or ""
    target = webpage_url or f"ytsearch1:{artist} {title}".strip()
    base_name = safe_filename(f"{artist} - {title}" if artist else title)
    out_template = os.path.join(DOWNLOADS_DIR, f"{base_name}.%(ext)s")

    opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([target])
        mp3_name = f"{base_name}.mp3"
        if os.path.exists(os.path.join(DOWNLOADS_DIR, mp3_name)):
            return jsonify({"file": mp3_name, "url": f"/downloads/{mp3_name}"})
        for fname in os.listdir(DOWNLOADS_DIR):
            if fname.startswith(base_name):
                return jsonify({"file": fname, "url": f"/downloads/{fname}"})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"error": "Файл не создан"}), 500


@app.route("/api/favorites")
def api_favorites():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM favorites").fetchall()
    conn.close()
    return jsonify({"favorites": [dict(r) for r in rows]})


@app.route("/api/favorites/toggle", methods=["POST"])
def api_favorites_toggle():
    track = request.get_json(silent=True) or {}
    track_id = str(track.get("id") or "")
    if not track_id:
        return jsonify({"liked": False})

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    exists = cur.execute("SELECT 1 FROM favorites WHERE id = ?", (track_id,)).fetchone()
    if exists:
        cur.execute("DELETE FROM favorites WHERE id = ?", (track_id,))
        liked = False
    else:
        cur.execute(
            """
            INSERT OR REPLACE INTO favorites (id, title, artist, duration, thumbnail, webpage_url, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                track_id,
                track.get("title") or "",
                track.get("artist") or "",
                track.get("duration") or "",
                track.get("thumbnail") or "/static/icon.png",
                track.get("webpage_url") or "",
                track.get("source") or "",
            ),
        )
        liked = True
    conn.commit()
    conn.close()
    return jsonify({"liked": liked})


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)