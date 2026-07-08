"""Flask website: pick a category, generate a short, and upload it to YouTube."""
import os
import threading
import traceback
import uuid

from flask import Flask, jsonify, render_template, request, send_from_directory

import bulk as bulk_mod
import generate as gen
import metadata as meta_mod
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
            try:
                up = yt.upload_video(
                    result["path"], md["title"], md["description"], md["tags"],
                    privacy=privacy, progress=progress, token_file=token_file,
                    secret_file=secret_file,
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
    icons = {"reddit":"👽","relationship":"💔","cold":"🧊","horror":"👻","simpsons":"🍩","anime":"🌸","betrayal":"🔪","funny":"😂","games":"🎮"}
    bgs   = {"reddit":"#1a0505","relationship":"#1a0510","cold":"#050d1a","horror":"#0d0505","simpsons":"#1a1200","anime":"#0d0518","betrayal":"#16060a","funny":"#161200","games":"#05140d"}
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
