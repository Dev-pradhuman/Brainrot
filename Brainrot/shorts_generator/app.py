"""Flask website: pick a category, generate a short, and upload it to YouTube."""
import os
import threading
import traceback
import uuid

from flask import Flask, jsonify, render_template, request, send_from_directory

import bulk as bulk_mod
import generate as gen
import import_clip as imp
import metadata as meta_mod
import schedule as sched
import video as video_mod
import youtube_upload as yt
from yt_dlp.utils import download_range_func
import static_ffmpeg

try:
    static_ffmpeg.add_paths()
except Exception as e:
    print(f"Warning: Failed to initialize static-ffmpeg paths in app.py: {e}")

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.normpath(os.path.join(HERE, gen.load_config()["output_dir"]))

app = Flask(__name__)

# In-memory job store: job_id -> {status, stage, log[], result, error}
JOBS = {}

CATEGORIES = [
    {"id": "reddit",       "name": "Reddit Storytelling", "icon": "👽", "ready": True,
     "desc": "Engaging stories from Reddit threads.",
     "bg": "linear-gradient(135deg,#1a0505,#2d0a0a)", "overlay": "linear-gradient(135deg,rgba(255,69,0,.3),rgba(180,30,0,.2))"},
    {"id": "simpsons",     "name": "Simpsons",            "icon": "🍩", "ready": True,
     "desc": "Daily doses of Springfield chaos.",
     "bg": "linear-gradient(135deg,#1a1200,#2d2000)", "overlay": "linear-gradient(135deg,rgba(255,200,0,.25),rgba(200,140,0,.15))"},
    {"id": "cold",         "name": "Cold Story",          "icon": "🧊", "ready": True,
     "desc": "Real, deep and thought-provoking.",
     "bg": "linear-gradient(135deg,#050d1a,#0a1828)", "overlay": "linear-gradient(135deg,rgba(0,120,255,.2),rgba(0,60,180,.15))"},
    {"id": "relationship", "name": "Relationship Advice", "icon": "💔", "ready": True,
     "desc": "Advice that hits different.",
     "bg": "linear-gradient(135deg,#1a0510,#2d0a1a)", "overlay": "linear-gradient(135deg,rgba(255,50,120,.25),rgba(180,20,80,.15))"},
    {"id": "horror",       "name": "Horror Stories",      "icon": "👻", "ready": True,
     "desc": "Terrifying tales that keep them up at night.",
     "bg": "linear-gradient(135deg,#0d0505,#1a0808)", "overlay": "linear-gradient(135deg,rgba(180,0,0,.3),rgba(80,0,0,.2))"},
    {"id": "anime",        "name": "Anime",               "icon": "🌸", "ready": True,
     "desc": "Anime moments, facts and theories.",
     "bg": "linear-gradient(135deg,#0d0518,#1a0a2d)", "overlay": "linear-gradient(135deg,rgba(168,85,247,.25),rgba(100,40,180,.15))"},
    {"id": "betrayal",     "name": "Betrayal",            "icon": "🔪", "ready": True,
     "desc": "Betrayed by a best friend, bf or gf.",
     "bg": "linear-gradient(135deg,#16060a,#26090f)", "overlay": "linear-gradient(135deg,rgba(220,38,38,.28),rgba(120,20,20,.18))"},
    {"id": "funny",        "name": "Funny Stories",       "icon": "😂", "ready": True,
     "desc": "Relatable, chaotic, laugh-out-loud stories.",
     "bg": "linear-gradient(135deg,#161200,#262000)", "overlay": "linear-gradient(135deg,rgba(250,204,21,.28),rgba(180,140,0,.16))"},
    {"id": "games",        "name": "Gaming Reactions",    "icon": "🎮", "ready": True,
     "desc": "Reacts to the latest trending gaming videos.",
     "bg": "linear-gradient(135deg,#05140d,#082617)", "overlay": "linear-gradient(135deg,rgba(34,197,94,.26),rgba(16,120,70,.16))"},
    {"id": "space",        "name": "Space Mysteries",     "icon": "🚀", "ready": True,
     "desc": "Terrifying cosmic facts and space anomalies.",
     "bg": "linear-gradient(135deg,#050a18,#0a122d)", "overlay": "linear-gradient(135deg,rgba(56,189,248,.25),rgba(30,58,138,.15))"},
    {"id": "fifa",         "name": "FIFA / EA FC",        "icon": "⚽", "ready": True,
     "desc": "Ultimate Team drama, meta tips and pack luck.",
     "bg": "linear-gradient(135deg,#03140a,#062615)", "overlay": "linear-gradient(135deg,rgba(16,185,129,.28),rgba(6,95,70,.16))"},
    {"id": "gaming",       "name": "Gaming Stories",      "icon": "🕹️", "ready": True,
     "desc": "Gaming drama, dev secrets and legends.",
     "bg": "linear-gradient(135deg,#0a0518,#140a2d)", "overlay": "linear-gradient(135deg,rgba(139,92,246,.26),rgba(76,29,149,.16))"},
    {"id": "aitools",      "name": "AI Dev Tools",        "icon": "🤖", "ready": True,
     "desc": "Claude Code, MCP, Codex & local models.",
     "bg": "linear-gradient(135deg,#04121a,#07202d)", "overlay": "linear-gradient(135deg,rgba(6,182,212,.26),rgba(14,116,144,.16))"},
    {"id": "trending",     "name": "Trending Now",        "icon": "🔥", "ready": True,
     "desc": "Reacts to whatever is blowing up right now.",
     "bg": "linear-gradient(135deg,#1a0a03,#2d1406)", "overlay": "linear-gradient(135deg,rgba(249,115,22,.28),rgba(154,52,18,.16))"},
    {"id": "colourgame",   "name": "Colour Game",         "icon": "🎨", "ready": True,
     "desc": "Viral colour game bets and psychology.",
     "bg": "linear-gradient(135deg,#1a1a03,#2d2d06)", "overlay": "linear-gradient(135deg,rgba(255,255,0,.25),rgba(200,200,0,.15))"},
    {"id": "squidgame",    "name": "Squid Game",          "icon": "🦑", "ready": True,
     "desc": "Squid Game theories and hidden details.",
     "bg": "linear-gradient(135deg,#1a030a,#2d0614)", "overlay": "linear-gradient(135deg,rgba(255,50,150,.25),rgba(200,30,120,.15))"},
    {"id": "movies",       "name": "New Movies",          "icon": "🍿", "ready": True,
     "desc": "Trending movie Easter eggs and theories.",
     "bg": "linear-gradient(135deg,#030a1a,#06142d)", "overlay": "linear-gradient(135deg,rgba(50,150,255,.25),rgba(30,120,200,.15))"},
    {"id": "gta6",         "name": "GTA 6",               "icon": "🏎️", "ready": True,
     "desc": "GTA 6 leaks, hype, and mechanics.",
     "bg": "linear-gradient(135deg,#1a0518,#2d0a2d)", "overlay": "linear-gradient(135deg,rgba(255,50,255,.25),rgba(200,30,200,.15))"},
]


def _log(job, stage, msg):
    job["stage"] = stage
    job["log"].append(msg.strip())


def run_job(job_id, category, do_upload, privacy):
    job = JOBS[job_id]
    try:
        cfg = gen.load_config()
        job["status"] = "generating"

        def progress(stage, msg):
            _log(job, stage, msg)

        result = gen.generate_video(cfg, category=category, progress=progress)
        job["result"] = {
            "file": os.path.basename(result["path"]),
            "title": result["title"],
            "duration": round(result["duration"], 1),
            "category": category,
        }

        md = meta_mod.build_metadata(result)
        job["result"]["meta"] = md

        if do_upload:
            cat_cfg = (cfg.get("categories") or {}).get(category, {})
            token_file = cat_cfg.get("youtube_token")  # None -> default token.json
            secret_file = cat_cfg.get("youtube_client_secret")  # None -> client_secret.json
            job["status"] = "uploading"
            _log(job, "upload", f"Uploading to YouTube ({token_file or 'token.json'})...")
            slots = sched.slots_for(cfg, 1)          # [] when scheduling is disabled
            try:
                up = yt.upload_video(
                    result["path"], md["title"], md["description"], md["tags"],
                    privacy=privacy, progress=progress, token_file=token_file,
                    secret_file=secret_file,
                    publish_at=slots[0] if slots else None,
                )
                job["result"]["youtube"] = up
                _log(job, "upload", f"Uploaded: {up['url']}")
            except Exception as upload_err:
                err_msg = str(upload_err)
                if "uploadLimitExceeded" in err_msg or "exceeded the number of videos" in err_msg:
                    friendly_err = "YouTube Upload Limit Exceeded: This channel has hit its daily video upload limit. Please try again tomorrow, or configure a different YouTube channel in config.json."
                else:
                    friendly_err = f"YouTube Upload Failed: {err_msg}"
                job["result"]["upload_error"] = friendly_err
                _log(job, "upload", f"UPLOAD ERROR: {friendly_err}")
                traceback.print_exc()

        job["status"] = "done"
        _log(job, "done", "Finished.")
    except Exception as e:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = str(e)
        _log(job, "error", f"ERROR: {e}")
        traceback.print_exc()


@app.route("/")
def index():
    # Build recent videos list from output folder
    recent = []
    # Derived from CATEGORIES so new niches can never drift out of sync here.
    icons = {c["id"]: c["icon"] for c in CATEGORIES}
    bgs = {"reddit":"#1a0505","relationship":"#1a0510","cold":"#050d1a","horror":"#0d0505",
           "simpsons":"#1a1200","anime":"#0d0518","betrayal":"#16060a","funny":"#161200",
           "games":"#05140d","space":"#050a18","fifa":"#03140a","gaming":"#0a0518",
           "aitools":"#04121a","trending":"#1a0a03","colourgame":"#1a1a03","squidgame":"#1a030a",
           "movies":"#030a1a","gta6":"#1a0518"}
    try:
        mp4s = sorted(
            [f for f in os.listdir(OUTPUT_DIR) if f.endswith(".mp4")],
            key=lambda f: os.path.getmtime(os.path.join(OUTPUT_DIR, f)),
            reverse=True
        )[:6]
        for f in mp4s:
            cat = next((c["id"] for c in CATEGORIES if c["id"] in f), "reddit")
            dur_s = video_mod.get_duration(os.path.join(OUTPUT_DIR, f))
            m, s = int(dur_s // 60), int(dur_s % 60)
            recent.append({
                "name": f.replace(".mp4","").replace("_"," ")[:28],
                "file": f, "icon": icons.get(cat,"🎬"),
                "bg": bgs.get(cat,"#111118"),
                "dur": f"{m}:{s:02d}",
                "status": "Uploaded", "status_cls": "status-done",
            })
    except Exception:
        pass
    return render_template(
        "index.html",
        categories=CATEGORIES,
        yt_ready=yt.has_client_secret(),
        yt_authorized=yt.is_authorized(),
        recent_videos=recent,
        recent_count=len(recent),
    )


@app.route("/api/generate", methods=["POST"])
def api_generate():
    data = request.get_json(force=True)
    category = data.get("category", "reddit")
    do_upload = bool(data.get("upload", False))
    privacy = data.get("privacy", "public")

    if not any(c["id"] == category and c["ready"] for c in CATEGORIES):
        return jsonify({"error": "That category isn't available yet."}), 400
    if do_upload:
        if not yt.has_client_secret():
            return jsonify({"error": "YouTube not configured. Add client_secret.json."}), 400
        
        # Check if the token file exists for the specific category
        cfg = gen.load_config()
        cat_cfg = (cfg.get("categories") or {}).get(category, {})
        token = cat_cfg.get("youtube_token") or "token.json"
        token_path = token if os.path.isabs(token) else os.path.join(HERE, token)
        if not os.path.exists(token_path):
            return jsonify({"error": f"Authorization required for '{category}' (needs: {token}). Please run 'python youtube_upload.py {token}' in your terminal first."}), 400

    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"status": "queued", "stage": "queued", "log": [], "result": None, "error": None}
    threading.Thread(target=run_job, args=(job_id, category, do_upload, privacy), daemon=True).start()
    return jsonify({"job_id": job_id})


def run_bulk_job(job_id, categories, count, do_upload, privacy):
    job = JOBS[job_id]
    try:
        job["status"] = "running"
        cfg = gen.load_config()

        def progress(stage, msg):
            _log(job, stage, msg)

        def on_result(r):
            job["completed"] = job.get("completed", 0) + 1
            job.setdefault("results", []).append({
                "file": os.path.basename(r["path"]),
                "title": r["title"],
                "category": r.get("category"),
                "duration": round(r.get("duration", 0), 1),
                "youtube": r.get("youtube"),
                "upload_error": r.get("upload_error"),
            })

        bulk_mod.bulk_generate(
            categories, count, do_upload=do_upload, privacy=privacy,
            cfg=cfg, progress=progress, on_result=on_result,
        )
        job["status"] = "done"
        _log(job, "done", "Bulk run complete.")
    except Exception as e:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = str(e)
        _log(job, "error", f"ERROR: {e}")
        traceback.print_exc()


@app.route("/api/bulk", methods=["POST"])
def api_bulk():
    data = request.get_json(force=True)
    # accept either a list of niches ("categories") or a single "category"
    categories = data.get("categories") or [data.get("category", "reddit")]
    categories = [c for c in categories if c]
    count = int(data.get("count", 1))
    do_upload = bool(data.get("upload", False))
    privacy = data.get("privacy", "public")

    ready_ids = {c["id"] for c in CATEGORIES if c["ready"]}
    bad = [c for c in categories if c not in ready_ids]
    if not categories or bad:
        return jsonify({"error": f"Unavailable niche(s): {', '.join(bad) or 'none selected'}"}), 400
    if do_upload and not yt.has_client_secret():
        return jsonify({"error": "YouTube not configured. Add client_secret.json."}), 400

    cap = int(gen.load_config().get("bulk", {}).get("daily_cap", 10))
    count = max(1, min(count, cap))

    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"status": "queued", "stage": "queued", "log": [],
                    "result": None, "error": None, "kind": "bulk",
                    "niches": categories, "total": count, "completed": 0,
                    "results": []}
    threading.Thread(target=run_bulk_job,
                     args=(job_id, categories, count, do_upload, privacy),
                     daemon=True).start()
    return jsonify({"job_id": job_id, "total": count})


# --------------------------------------------------------------------------- #
# Import a long video -> ranked clip candidates -> render -> existing upload
# --------------------------------------------------------------------------- #
IMPORT_JOBS = {}
IMPORT_DIR = os.path.join(HERE, "_imports")
VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")


def import_status(cfg=None):
    """Which import backends are usable — drives the key warnings in the UI."""
    cfg = cfg or gen.load_config()
    ic = cfg.get("import", {}) or {}
    llm = cfg.get("llm", {}) or {}
    try:
        import faster_whisper  # noqa: F401
        has_whisper = True
    except ImportError:
        has_whisper = False
    return {
        "engine": ic.get("transcription_engine", "deepgram"),
        "has_deepgram": bool(((ic.get("deepgram") or {}).get("api_key") or "").strip()),
        "has_whisper": has_whisper,
        "has_groq": bool((llm.get("api_key") or "").strip()) and bool(llm.get("enabled")),
        "min_clip_sec": ic.get("min_clip_sec", 30),
        "max_clip_sec": ic.get("max_clip_sec", 90),
        "burn_captions": bool(ic.get("burn_captions", True)),
        "metadata_category": ic.get("metadata_category", "reddit"),
    }


def run_import_job(job_id, src_path):
    job = IMPORT_JOBS[job_id]
    try:
        job["status"] = "analyzing"
        cfg = gen.load_config()

        def progress(stage, msg):
            _log(job, stage, msg)

        res = imp.analyze_video(cfg, src_path, progress=progress)
        # The transcript stays server-side: the render step needs it for caption
        # timings, but it's far too large to ship to the browser on every poll.
        job["transcript"] = res["transcript"]
        job["probe"] = res["probe"]
        job["candidates"] = res["candidates"]
        job["status"] = "done"
        _log(job, "done", f"Analysis complete — {len(res['candidates'])} candidate(s).")
    except Exception as e:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = str(e)
        _log(job, "error", f"ERROR: {e}")
        traceback.print_exc()


@app.route("/import")
def import_page():
    return render_template("import.html", categories=CATEGORIES,
                           yt_ready=yt.has_client_secret(),
                           imp_status=import_status())


@app.route("/api/import/start", methods=["POST"])
def api_import_start():
    """Accept either an uploaded file or a local path to a long-form video."""
    src_path = None

    if "file" in request.files and request.files["file"].filename:
        f = request.files["file"]
        if not f.filename.lower().endswith(VIDEO_EXTS):
            return jsonify({"error": f"Unsupported file type. Allowed: {', '.join(VIDEO_EXTS)}"}), 400
        os.makedirs(IMPORT_DIR, exist_ok=True)
        # Flatten any directory component the browser may send.
        safe = os.path.basename(f.filename.replace("\\", "/"))
        dest = os.path.join(IMPORT_DIR, safe)
        base, ext = os.path.splitext(dest)
        n = 1
        while os.path.exists(dest):
            dest = f"{base}_{n}{ext}"
            n += 1
        f.save(dest)
        src_path = dest
    else:
        data = request.get_json(silent=True) or request.form or {}
        raw = (data.get("path") or "").strip().strip('"')
        if not raw:
            return jsonify({"error": "Provide a video file or a local path."}), 400
        if not os.path.isabs(raw):
            raw = os.path.normpath(os.path.join(HERE, raw))
        if not os.path.exists(raw):
            return jsonify({"error": f"File not found: {raw}"}), 400
        if not raw.lower().endswith(VIDEO_EXTS):
            return jsonify({"error": f"Unsupported file type. Allowed: {', '.join(VIDEO_EXTS)}"}), 400
        src_path = raw

    st = import_status()
    if st["engine"] == "deepgram" and not st["has_deepgram"] and not st["has_whisper"]:
        return jsonify({"error": "No transcription backend available. Add DEEPGRAM_API_KEY "
                                 "to .env, or pip install faster-whisper and set "
                                 "import.transcription_engine to 'whisper'."}), 400
    if st["engine"] == "whisper" and not st["has_whisper"]:
        return jsonify({"error": "faster-whisper is not installed. Run "
                                 "'pip install faster-whisper' or switch "
                                 "import.transcription_engine to 'deepgram'."}), 400

    job_id = uuid.uuid4().hex[:12]
    IMPORT_JOBS[job_id] = {"status": "queued", "stage": "queued", "log": [],
                           "error": None, "kind": "import", "source": src_path,
                           "candidates": [], "probe": None}
    threading.Thread(target=run_import_job, args=(job_id, src_path),
                     daemon=True).start()
    return jsonify({"job_id": job_id, "source": os.path.basename(src_path)})


@app.route("/api/import/status/<job_id>")
def api_import_status(job_id):
    job = IMPORT_JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown import job"}), 404
    return jsonify({k: v for k, v in job.items() if k != "transcript"})


def run_import_render_job(job_id, analyze_id, indices, category, burn_captions,
                          do_upload, privacy):
    job = IMPORT_JOBS[job_id]
    src = IMPORT_JOBS[analyze_id]
    try:
        job["status"] = "running"
        cfg = gen.load_config()
        transcript = src["transcript"]
        src_path = src["source"]

        def progress(stage, msg):
            _log(job, stage, msg)

        for n, idx in enumerate(indices, 1):
            cand = src["candidates"][idx]
            _log(job, "render", f"=== Clip {n}/{len(indices)} "
                                f"[{cand['start']:.1f}s-{cand['end']:.1f}s] ===")
            result = imp.render_candidate(
                cfg, src_path, transcript, cand, category=category,
                burn_captions=burn_captions, progress=progress,
            )
            md = meta_mod.build_metadata(result)
            result["meta"] = md

            entry = {
                "file": os.path.basename(result["path"]),
                "title": result["title"],
                "category": result["category"],
                "duration": round(result["duration"], 1),
                "score": cand.get("score"),
            }

            if do_upload:
                cat_cfg = (cfg.get("categories") or {}).get(category, {})
                token = cat_cfg.get("youtube_token")
                secret = cat_cfg.get("youtube_client_secret")
                _log(job, "upload", f"Uploading to YouTube ({token or 'token.json'})...")
                try:
                    up = yt.upload_video(
                        result["path"], md["title"], md["description"], md["tags"],
                        privacy=privacy, progress=progress,
                        token_file=token, secret_file=secret,
                    )
                    entry["youtube"] = up
                    _log(job, "upload", f"Uploaded: {up['url']}")
                except Exception as e:  # noqa: BLE001
                    msg = str(e)
                    entry["upload_error"] = msg
                    _log(job, "upload", f"UPLOAD ERROR: {msg}")
                    if "uploadLimitExceeded" in msg or "exceeded the number" in msg:
                        _log(job, "render", "YouTube daily upload limit hit — stopping.")
                        job.setdefault("results", []).append(entry)
                        job["completed"] = job.get("completed", 0) + 1
                        break

            job.setdefault("results", []).append(entry)
            job["completed"] = job.get("completed", 0) + 1

        job["status"] = "done"
        _log(job, "done", f"Rendered {job.get('completed', 0)} clip(s).")
    except Exception as e:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = str(e)
        _log(job, "error", f"ERROR: {e}")
        traceback.print_exc()


@app.route("/api/import/render", methods=["POST"])
def api_import_render():
    data = request.get_json(force=True)
    analyze_id = data.get("job_id")
    src = IMPORT_JOBS.get(analyze_id)
    if not src or not src.get("transcript"):
        return jsonify({"error": "Unknown or unfinished analysis job. Re-run the analysis."}), 400

    total = len(src.get("candidates") or [])
    indices = data.get("indices")
    if not isinstance(indices, list) or not indices:
        return jsonify({"error": "Select at least one clip."}), 400
    try:
        indices = [int(i) for i in indices]
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid clip selection."}), 400
    bad = [i for i in indices if i < 0 or i >= total]
    if bad:
        return jsonify({"error": f"Clip selection out of range: {bad}"}), 400

    category = data.get("category") or import_status().get("metadata_category")
    if not any(c["id"] == category and c["ready"] for c in CATEGORIES):
        return jsonify({"error": f"Unknown category: {category}"}), 400

    do_upload = bool(data.get("upload", False))
    privacy = data.get("privacy", "public")
    burn_captions = bool(data.get("burn_captions", True))

    if do_upload:
        if not yt.has_client_secret():
            return jsonify({"error": "YouTube not configured. Add client_secret.json."}), 400
        cfg = gen.load_config()
        cat_cfg = (cfg.get("categories") or {}).get(category, {})
        token = cat_cfg.get("youtube_token") or "token.json"
        token_path = token if os.path.isabs(token) else os.path.join(HERE, token)
        if not os.path.exists(token_path):
            return jsonify({"error": f"Authorization required for '{category}' (needs: {token}). "
                                     f"Run 'python youtube_upload.py {token}' first."}), 400

    job_id = uuid.uuid4().hex[:12]
    IMPORT_JOBS[job_id] = {"status": "queued", "stage": "queued", "log": [],
                           "error": None, "kind": "import_render",
                           "total": len(indices), "completed": 0, "results": []}
    threading.Thread(
        target=run_import_render_job,
        args=(job_id, analyze_id, indices, category, burn_captions, do_upload, privacy),
        daemon=True,
    ).start()
    return jsonify({"job_id": job_id, "total": len(indices)})


DOWNLOAD_JOBS = {}


def search_youtube(query, api_key=None):
    # Try Official API search if key exists, otherwise fallback to HTML scraping search
    if api_key and api_key != "${YOUTUBE_API_KEY}" and api_key.strip():
        try:
            import requests
            url = "https://www.googleapis.com/youtube/v3/search"
            params = {"part": "snippet", "q": query, "type": "video", "maxResults": 1, "key": api_key}
            r = requests.get(url, params=params, timeout=10)
            items = r.json().get("items", [])
            if items:
                video_id = items[0]['id']['videoId']
                return f"https://www.youtube.com/watch?v={video_id}"
        except Exception:
            pass
    # Free scrape-search fallback (reliable and retry-enabled)
    import urllib.request, urllib.parse, re, time
    for attempt in range(1, 4):
        try:
            url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
            req = urllib.request.Request(
                url, 
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'}
            )
            html = urllib.request.urlopen(req, timeout=20).read().decode('utf-8', errors='ignore')
            
            # Method 1: find videoId in JSON or raw HTML
            vid_ids = re.findall(r'"videoId"\s*:\s*"([a-zA-Z0-9_-]{11})"', html)
            if not vid_ids:
                # Method 2: find watch?v=
                vid_ids = re.findall(r"watch\?v=([a-zA-Z0-9_-]{11})", html)
                
            if vid_ids:
                return f"https://www.youtube.com/watch?v={vid_ids[0]}"
        except Exception as e:
            print(f"Search attempt {attempt}/3 failed: {e}")
            if attempt < 3:
                time.sleep(2)
    return None


def run_download_batch(job_id, batch_jobs, browser, mode="search"):
    job = DOWNLOAD_JOBS[job_id]
    try:
        job["status"] = "running"
        
        # Check for cookies.txt
        cookie_file = None
        local_cookies = os.path.join(HERE, "cookies.txt")
        if os.path.exists(local_cookies):
            job["log"].append("[Cookies] Found local 'cookies.txt'. Using it automatically.")
            cookie_file = local_cookies
            
        # Copy FFmpeg locally
        import shutil
        import imageio_ffmpeg
        bundled_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        local_ffmpeg = os.path.join(HERE, "ffmpeg.exe")
        if not os.path.exists(local_ffmpeg):
            job["log"].append("[FFmpeg] Copying bundled FFmpeg locally as 'ffmpeg.exe'...")
            try:
                shutil.copy(bundled_ffmpeg, local_ffmpeg)
            except Exception as e:
                job["log"].append(f"[Warning] Failed to copy FFmpeg locally: {e}")

        import yt_dlp
        total_urls = sum(len(urls) for urls in batch_jobs.values())
        processed_count = 0
        failed_count = 0
        
        for folder, urls in batch_jobs.items():
            folder_name = "Bg_games" if folder == "Bg_games_reactions" else folder
            dest_dir = os.path.join(r"G:\My Drive\Brainrot-videos", folder_name)
            os.makedirs(dest_dir, exist_ok=True)
            
            for url in urls:
                processed_count += 1
                
                target_url = url
                if mode == "search" and not (url.startswith("http://") or url.startswith("https://")):
                    job["log"].append(f"[Search] Searching YouTube for query: '{url}'...")
                    api_key = os.environ.get("YOUTUBE_API_KEY") or ""
                    resolved = search_youtube(url, api_key)
                    if resolved:
                        job["log"].append(f"[Search] Resolved to: {resolved}")
                        target_url = resolved
                    else:
                        job["log"].append(f"❌ [Search] Failed to find video for query: '{url}'")
                        failed_count += 1
                        continue

                job["log"].append(f"[Download {processed_count}/{total_urls}] Starting download: {target_url} -> {folder_name}...")
                
                class MyLogger:
                    def debug(self, msg):
                        if "[download]" in msg and "%" in msg:
                            # Only log significant progress jumps to prevent web console spam
                            if "100%" in msg or "0.0%" in msg or "50." in msg:
                                job["log"].append(msg)
                        else:
                            job["log"].append(msg)
                    def info(self, msg):
                        job["log"].append(msg)
                    def warning(self, msg):
                        job["log"].append(f"WARNING: {msg}")
                    def error(self, msg):
                        job["log"].append(f"ERROR: {msg}")
                
                ydl_opts = {
                    "format": "bestvideo[height<=1080]+bestaudio/best",
                    "merge_output_format": "mp4",
                    "outtmpl": os.path.join(dest_dir, "%(title)s_1080p.%(ext)s"),
                    "concurrent_fragment_downloads": 5,
                    "js_runtimes": {"node": {}},
                    "download_ranges": download_range_func(None, [(0, 300)]),
                    "force_keyframes_at_cuts": True,
                    "socket_timeout": 15,
                    "postprocessors": [{
                        "key": "FFmpegVideoConvertor",
                        "preferedformat": "mp4",
                    }],
                    "logger": MyLogger(),
                    "ignoreerrors": True,
                }
                
                if cookie_file:
                    ydl_opts["cookiefile"] = cookie_file
                elif browser and browser != "none":
                    ydl_opts["cookiesfrombrowser"] = (browser, None, None, None)
                    
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ret_code = ydl.download([target_url])
                        if ret_code != 0:
                            failed_count += 1
                            job["log"].append(f"❌ Download failed for: {target_url}")
                        else:
                            job["log"].append(f"✅ Downloaded successfully: {target_url}")
                except Exception as e:
                    failed_count += 1
                    job["log"].append(f"❌ Exception during download: {e}")
                    
        job["status"] = "done"
        job["log"].append("=== BATCH RUN COMPLETE ===")
        job["log"].append(f"Successfully processed: {total_urls - failed_count}/{total_urls} videos.")
        
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        job["log"].append(f"ERROR: {e}")
        traceback.print_exc()


def get_all_channels_stats():
    import glob
    import json
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    
    token_files = glob.glob(os.path.join(HERE, "token*.json"))
    channels_data = []
    
    for tf in token_files:
        filename = os.path.basename(tf)
        try:
            with open(tf, "r", encoding="utf-8") as f:
                content = json.load(f)
            if "token" not in content and "refresh_token" not in content:
                continue # Skip non-token JSON files
                
            creds = Credentials.from_authorized_user_file(tf)
            
            # Refresh if expired
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(tf, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
                    
            youtube = build("youtube", "v3", credentials=creds)
            
            # 1. Get channel details & upload playlist ID
            chan_resp = youtube.channels().list(
                part="snippet,statistics,contentDetails", mine=True
            ).execute()
            
            items = chan_resp.get("items", [])
            if not items:
                continue
                
            item = items[0]
            snippet = item["snippet"]
            stats = item["statistics"]
            uploads_playlist_id = item["contentDetails"]["relatedPlaylists"]["uploads"]
            
            chan_info = {
                "title": snippet["title"],
                "custom_url": snippet.get("customUrl", ""),
                "thumbnail": snippet["thumbnails"]["default"]["url"],
                "subscribers": int(stats.get("subscriberCount", 0)),
                "views": int(stats.get("viewCount", 0)),
                "video_count": int(stats.get("videoCount", 0)),
                "id": item["id"],
                "token_file": filename,
                "recent_videos": []
            }
            
            # 2. Get last 3 uploaded videos
            if uploads_playlist_id:
                try:
                    pl_resp = youtube.playlistItems().list(
                        part="snippet",
                        playlistId=uploads_playlist_id,
                        maxResults=3
                    ).execute()
                    
                    pl_items = pl_resp.get("items", [])
                    video_ids = [pi["snippet"]["resourceId"]["videoId"] for pi in pl_items]
                    
                    if video_ids:
                        vid_resp = youtube.videos().list(
                            part="snippet,statistics",
                            id=",".join(video_ids)
                        ).execute()
                        
                        for v in vid_resp.get("items", []):
                            v_snippet = v["snippet"]
                            v_stats = v["statistics"]
                            chan_info["recent_videos"].append({
                                "title": v_snippet["title"],
                                "thumbnail": v_snippet["thumbnails"]["medium"]["url"] if "medium" in v_snippet["thumbnails"] else v_snippet["thumbnails"]["default"]["url"],
                                "views": int(v_stats.get("viewCount", 0)),
                                "likes": int(v_stats.get("likeCount", 0)),
                                "comments": int(v_stats.get("commentCount", 0)),
                                "id": v["id"]
                            })
                except Exception as pl_err:
                    print(f"Warning: Failed to fetch videos for {filename}: {pl_err}")
            
            channels_data.append(chan_info)
            
        except Exception as e:
            channels_data.append({
                "token_file": filename,
                "error": str(e)
            })
            
    return channels_data


@app.route("/channels")
def channels_dashboard():
    channels = get_all_channels_stats()
    return render_template("channels.html", channels=channels, yt_ready=yt.has_client_secret())


@app.route("/downloader")
def downloader_page():
    return render_template("downloader.html", yt_ready=yt.has_client_secret())


@app.route("/api/downloader/cookies-check")
def api_cookies_check():
    local_cookies = os.path.join(HERE, "cookies.txt")
    return jsonify({"exists": os.path.exists(local_cookies)})


@app.route("/api/downloader/start", methods=["POST"])
def api_downloader_start():
    data = request.get_json(force=True)
    batch_jobs = data.get("jobs", {})
    browser = data.get("browser", "none")
    mode = data.get("mode", "search")
    
    if not batch_jobs:
        return jsonify({"error": "No download jobs provided."}), 400
        
    job_id = uuid.uuid4().hex[:12]
    DOWNLOAD_JOBS[job_id] = {
        "status": "queued",
        "log": [],
        "error": None
    }
    
    threading.Thread(
        target=run_download_batch,
        args=(job_id, batch_jobs, browser, mode),
        daemon=True
    ).start()
    
    return jsonify({"job_id": job_id})


@app.route("/api/downloader/status/<job_id>")
def api_downloader_status(job_id):
    job = DOWNLOAD_JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown downloader job"}), 404
    return jsonify(job)


@app.route("/api/status/<job_id>")
def api_status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    return jsonify(job)


@app.route("/api/upload", methods=["POST"])
def api_upload():
    if "file" not in request.files:
        return jsonify({"error": "No file part in request"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400
    if file and file.filename.lower().endswith((".mp4", ".mov", ".mkv")):
        filename = file.filename
        save_path = os.path.join(OUTPUT_DIR, filename)
        base_name, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(save_path):
            save_path = os.path.join(OUTPUT_DIR, f"{base_name}_{counter}{ext}")
            counter += 1
        file.save(save_path)
        return jsonify({"success": True, "file": os.path.basename(save_path)})
    return jsonify({"error": "Invalid file type. Only MP4, MOV, and MKV allowed."}), 400


@app.route("/output/<path:filename>")
def output_file(filename):
    return send_from_directory(OUTPUT_DIR, filename)


if __name__ == "__main__":
    print("Open http://127.0.0.1:5000 in your browser.")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
