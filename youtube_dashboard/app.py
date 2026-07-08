"""Standalone YouTube Analytics Dashboard - Decoupled from Brainrot.

Renders aggregate and channel-specific statistics (views, subscribers, 
likes, estimated watch time, top/least watched videos) and charts.
"""
import os
import sys
import uuid
import json
import glob
import shutil
import traceback
from datetime import datetime
from flask import Flask, render_template, request, redirect, session, jsonify
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

HERE = os.path.dirname(os.path.abspath(__file__))
# Check and copy client_secret.json from Brainrot project if it exists
CLIENT_SECRETS_FILE = os.path.join(HERE, "client_secret.json")
SOURCE_SECRET = os.path.normpath(os.path.join(HERE, "..", "Brainrot", "shorts_generator", "client_secret.json"))

if not os.path.exists(CLIENT_SECRETS_FILE) and os.path.exists(SOURCE_SECRET):
    try:
        shutil.copy(SOURCE_SECRET, CLIENT_SECRETS_FILE)
        print("[Auth] Successfully copied client_secret.json from Brainrot workspace.")
    except Exception as copy_err:
        print(f"[Warning] Failed to auto-copy client_secret.json: {copy_err}")

app = Flask(__name__)
app.secret_key = uuid.uuid4().hex

# We only need read-only access to view stats
SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly"
]

def parse_duration(duration_str):
    """Parse ISO 8601 duration string (e.g. PT1M30S) to minutes. Defaults to 1 min."""
    import re
    if not duration_str:
        return 1.0
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
    if not match:
        return 1.0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 60 + minutes + (seconds / 60)

def get_channel_analytics(tf):
    """Retrieve full analytics for a given token file."""
    filename = os.path.basename(tf)
    with open(tf, "r", encoding="utf-8") as f:
        content = json.load(f)
    if "token" not in content and "refresh_token" not in content:
        raise ValueError("Invalid OAuth token format")
        
    creds = Credentials.from_authorized_user_file(tf)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(tf, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
            
    youtube = build("youtube", "v3", credentials=creds)
    
    # 1. Fetch channel info & uploads playlist
    chan_resp = youtube.channels().list(
        part="snippet,statistics,contentDetails", mine=True
    ).execute()
    
    items = chan_resp.get("items", [])
    if not items:
        raise ValueError("No channel details found in Google account.")
        
    item = items[0]
    snippet = item["snippet"]
    stats = item["statistics"]
    uploads_playlist_id = item["contentDetails"]["relatedPlaylists"]["uploads"]
    
    chan_info = {
        "title": snippet["title"],
        "custom_url": snippet.get("customUrl", ""),
        "thumbnail": snippet["thumbnails"]["high"]["url"] if "high" in snippet["thumbnails"] else snippet["thumbnails"]["default"]["url"],
        "subscribers": int(stats.get("subscriberCount", 0)),
        "views": int(stats.get("viewCount", 0)),
        "video_count": int(stats.get("videoCount", 0)),
        "id": item["id"],
        "token_file": filename,
        "videos": [],
        "estimated_watch_time_mins": 0,
        "likes": 0,
        "comments": 0,
        "top_video": None,
        "least_video": None
    }
    
    # 2. Fetch last 15 videos
    if uploads_playlist_id:
        pl_resp = youtube.playlistItems().list(
            part="snippet",
            playlistId=uploads_playlist_id,
            maxResults=15
        ).execute()
        
        pl_items = pl_resp.get("items", [])
        video_ids = [pi["snippet"]["resourceId"]["videoId"] for pi in pl_items]
        
        if video_ids:
            vid_resp = youtube.videos().list(
                part="snippet,statistics,contentDetails",
                id=",".join(video_ids)
            ).execute()
            
            v_items = vid_resp.get("items", [])
            for v in v_items:
                v_snippet = v["snippet"]
                v_stats = v["statistics"]
                v_content = v["contentDetails"]
                v_views = int(v_stats.get("viewCount", 0))
                v_likes = int(v_stats.get("likeCount", 0))
                v_comments = int(v_stats.get("commentCount", 0))
                dur_mins = parse_duration(v_content.get("duration", ""))
                
                video_obj = {
                    "id": v["id"],
                    "title": v_snippet["title"],
                    "thumbnail": v_snippet["thumbnails"]["medium"]["url"] if "medium" in v_snippet["thumbnails"] else v_snippet["thumbnails"]["default"]["url"],
                    "views": v_views,
                    "likes": v_likes,
                    "comments": v_comments,
                    "duration_mins": dur_mins,
                    "published_at": v_snippet["publishedAt"][:10]  # YYYY-MM-DD
                }
                chan_info["videos"].append(video_obj)
                
                # Accumulate metrics
                chan_info["likes"] += v_likes
                chan_info["comments"] += v_comments
                # Estimate watch time: duration * views * average view duration ratio (approx 50% for standard video)
                chan_info["estimated_watch_time_mins"] += (dur_mins * v_views * 0.5)

            if chan_info["videos"]:
                # Sort to find top and least watched
                sorted_vids = sorted(chan_info["videos"], key=lambda k: k["views"])
                chan_info["least_video"] = sorted_vids[0]
                chan_info["top_video"] = sorted_vids[-1]
                
    return chan_info

def load_all_dashboard_data():
    """Scan all token files and compile aggregate + individual statistics."""
    # Look for tokens in this standalone directory first
    token_files = glob.glob(os.path.join(HERE, "token*.json"))
    
    # Fallback to copy tokens from Brainrot directory if local directory has none
    SOURCE_TOKENS_DIR = os.path.normpath(os.path.join(HERE, "..", "Brainrot", "shorts_generator"))
    if not token_files and os.path.exists(SOURCE_TOKENS_DIR):
        source_tokens = glob.glob(os.path.join(SOURCE_TOKENS_DIR, "token*.json"))
        for st in source_tokens:
            try:
                shutil.copy(st, os.path.join(HERE, os.path.basename(st)))
            except Exception:
                pass
        token_files = glob.glob(os.path.join(HERE, "token*.json"))

    channels = []
    aggregate = {
        "total_subscribers": 0,
        "total_views": 0,
        "total_videos": 0,
        "total_likes": 0,
        "total_comments": 0,
        "total_watch_time_hours": 0,
        "channel_shares": [] # For pie charts: {name, value}
    }
    
    for tf in token_files:
        filename = os.path.basename(tf)
        try:
            chan_data = get_channel_analytics(tf)
            channels.append(chan_data)
            
            # Add to aggregates
            aggregate["total_subscribers"] += chan_data["subscribers"]
            aggregate["total_views"] += chan_data["views"]
            aggregate["total_videos"] += chan_data["video_count"]
            aggregate["total_likes"] += chan_data["likes"]
            aggregate["total_comments"] += chan_data["comments"]
            aggregate["total_watch_time_hours"] += (chan_data["estimated_watch_time_mins"] / 60)
            
            aggregate["channel_shares"].append({
                "name": chan_data["title"],
                "subscribers": chan_data["subscribers"],
                "views": chan_data["views"]
            })
        except Exception as e:
            channels.append({
                "token_file": filename,
                "error": str(e)
            })
            
    aggregate["total_watch_time_hours"] = round(aggregate["total_watch_time_hours"], 1)
    return channels, aggregate

@app.route('/')
def index():
    has_secret = os.path.exists(CLIENT_SECRETS_FILE)
    channels, aggregate = load_all_dashboard_data()
    return render_template(
        "index.html",
        channels=channels,
        aggregate=aggregate,
        has_secret=has_secret
    )

@app.route('/authorize')
def authorize():
    if not os.path.exists(CLIENT_SECRETS_FILE):
        return "Error: client_secret.json not found in youtube_dashboard folder.", 400
        
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, scopes=SCOPES,
        redirect_uri='http://127.0.0.1:8080/oauth2callback'
    )
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true'
    )
    session['state'] = state
    return redirect(authorization_url)

@app.route('/oauth2callback')
def oauth2callback():
    state = session.get('state')
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, scopes=SCOPES, state=state,
        redirect_uri='http://127.0.0.1:8080/oauth2callback'
    )
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    
    # Fetch channel name to save file uniquely
    youtube = build("youtube", "v3", credentials=creds)
    chan_resp = youtube.channels().list(part="snippet", mine=True).execute()
    items = chan_resp.get("items", [])
    if items:
        title = items[0]["snippet"]["title"]
        safe_title = "".join([c if c.isalnum() else "_" for c in title.lower()])
        token_name = f"token_{safe_title}.json"
    else:
        token_name = f"token_{uuid.uuid4().hex[:8]}.json"
        
    with open(os.path.join(HERE, token_name), "w", encoding="utf-8") as f:
        f.write(creds.to_json())
        
    return redirect('/')

@app.route('/api/delete-channel/<token_file>', methods=["POST"])
def delete_channel(token_file):
    """Delete a channel token file to unlink it from the dashboard."""
    try:
        token_path = os.path.join(HERE, token_file)
        if os.path.exists(token_path) and token_file.startswith("token") and token_file.endswith(".json"):
            os.remove(token_path)
            return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"error": "Invalid token file."}), 400

if __name__ == "__main__":
    print("Standalone YouTube Dashboard running on http://127.0.0.1:8080")
    app.run(host="127.0.0.1", port=8080, debug=False, threaded=True)
