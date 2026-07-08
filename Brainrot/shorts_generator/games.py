"""Fetch a trending gaming video's METADATA (free, no download) for the games niche.

Uses the YouTube Data API v3 with a simple API key (read-only public data, no
OAuth needed). Strategy: pull the latest uploads from a configured list of big
gaming channels first, then fall back to YouTube's trending Gaming chart for the
configured regions (e.g. India + US). A small local file remembers recently used
videos so we don't repeat the same clip.
"""
import json
import os

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://www.googleapis.com/youtube/v3"
SEEN_FILE = os.path.join(HERE, "games_seen.json")
GAMING_CATEGORY_ID = "20"


def _seen():
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def _mark_seen(video_id):
    s = _seen()
    s.add(video_id)
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(list(s)[-300:], f)
    except Exception:
        pass


def _get(path, params, key):
    params = dict(params)
    params["key"] = key
    r = requests.get(f"{API}/{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _uploads_playlist(handle, key):
    h = handle.lstrip("@")
    data = _get("channels", {"part": "contentDetails", "forHandle": h}, key)
    items = data.get("items", [])
    if items:
        return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    return None


def _channel_latest(handle, key, n=2):
    pl = _uploads_playlist(handle, key)
    if not pl:
        return []
    data = _get("playlistItems",
                {"part": "snippet", "playlistId": pl, "maxResults": n}, key)
    return [it["snippet"]["resourceId"]["videoId"] for it in data.get("items", [])]


def _trending(region, key, n=15):
    data = _get("videos", {
        "part": "id",
        "chart": "mostPopular",
        "videoCategoryId": GAMING_CATEGORY_ID,
        "regionCode": region,
        "maxResults": n,
    }, key)
    return [it["id"] for it in data.get("items", [])]


def _video_details(video_id, key):
    data = _get("videos", {"part": "snippet,statistics", "id": video_id}, key)
    items = data.get("items", [])
    if not items:
        return None
    it = items[0]
    sn = it.get("snippet", {})
    st = it.get("statistics", {})
    return {
        "video_id": video_id,
        "title": sn.get("title", ""),
        "channel": sn.get("channelTitle", ""),
        "description": (sn.get("description", "") or "")[:1500],
        "tags": (sn.get("tags") or [])[:15],
        "views": int(st.get("viewCount", 0) or 0),
        "url": f"https://youtu.be/{video_id}",
    }


def fetch_trending_game_video(cfg, progress=None):
    """Return details of a fresh trending gaming video. Raises on failure."""
    g = cfg.get("games", {})
    key = g.get("api_key")
    if not key:
        raise RuntimeError("YOUTUBE_API_KEY is not set (needed for the games niche).")

    seen = _seen()
    candidates = []

    # 1) latest uploads from the configured channel list
    for handle in g.get("channels", []):
        try:
            for vid in _channel_latest(handle, key, g.get("per_channel", 2)):
                if vid not in seen and vid not in candidates:
                    candidates.append(vid)
        except Exception as e:  # noqa: BLE001
            if progress:
                progress("games", f"channel {handle} failed: {e}")

    # 2) trending Gaming chart fallback / supplement
    if not candidates:
        for region in g.get("regions", ["IN", "US"]):
            try:
                for vid in _trending(region, key, g.get("max_results", 15)):
                    if vid not in seen and vid not in candidates:
                        candidates.append(vid)
            except Exception as e:  # noqa: BLE001
                if progress:
                    progress("games", f"trending {region} failed: {e}")

    if not candidates:
        raise RuntimeError("No new trending gaming videos found.")

    # pick the most-viewed among the first few candidates
    best = None
    for vid in candidates[:8]:
        try:
            d = _video_details(vid, key)
        except Exception:
            continue
        if d and (best is None or d["views"] > best["views"]):
            best = d
    if not best:
        raise RuntimeError("Could not load video details.")

    _mark_seen(best["video_id"])
    if progress:
        progress("games", f"Reacting to: {best['title']} "
                          f"({best['channel']}, {best['views']:,} views)")
    return best
