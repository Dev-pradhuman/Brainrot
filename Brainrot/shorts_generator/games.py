"""Fetch a trending video's METADATA (free, no download) for API-driven niches.

Uses the YouTube Data API v3 with a simple API key (read-only public data, no
OAuth needed). Strategy: pull the latest uploads from a configured list of big
channels first, then fall back to YouTube's trending chart for the configured
regions. A small local file per niche remembers recently used videos so we don't
repeat the same clip.

Drives two niches, each with its own config block:
  "games"    -> category_id "20" (Gaming), seeded with big gaming channels
  "trending" -> category_id null (all categories), pure trending chart
"""
import json
import os

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://www.googleapis.com/youtube/v3"
GAMING_CATEGORY_ID = "20"


def _seen_file(block):
    return os.path.join(HERE, f"{block}_seen.json")


def _seen(block="games"):
    try:
        with open(_seen_file(block), "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def _mark_seen(video_id, block="games"):
    s = _seen(block)
    s.add(video_id)
    try:
        with open(_seen_file(block), "w", encoding="utf-8") as f:
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


def _trending(region, key, n=15, category_id=GAMING_CATEGORY_ID):
    params = {
        "part": "id",
        "chart": "mostPopular",
        "regionCode": region,
        "maxResults": n,
    }
    # Omitting videoCategoryId gives the overall trending chart, not just Gaming.
    if category_id:
        params["videoCategoryId"] = category_id
    data = _get("videos", params, key)
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


def fetch_trending_video(cfg, progress=None, block="games"):
    """Return details of a fresh trending video for `block`. Raises on failure.

    `block` selects the config section ("games" or "trending"), which supplies the
    API key, optional seed channels, target regions, and videoCategoryId.
    """
    g = cfg.get(block, {})
    key = g.get("api_key")
    if not key:
        raise RuntimeError(f"YOUTUBE_API_KEY is not set (needed for the {block} niche).")

    category_id = g.get("category_id", GAMING_CATEGORY_ID if block == "games" else None)
    seen = _seen(block)
    candidates = []

    # 1) latest uploads from the configured channel list (may be empty)
    for handle in g.get("channels", []) or []:
        try:
            for vid in _channel_latest(handle, key, g.get("per_channel", 2)):
                if vid not in seen and vid not in candidates:
                    candidates.append(vid)
        except Exception as e:  # noqa: BLE001
            if progress:
                progress(block, f"channel {handle} failed: {e}")

    # 2) trending chart fallback / supplement
    if not candidates:
        for region in g.get("regions", ["US"]):
            try:
                for vid in _trending(region, key, g.get("max_results", 15), category_id):
                    if vid not in seen and vid not in candidates:
                        candidates.append(vid)
            except Exception as e:  # noqa: BLE001
                if progress:
                    progress(block, f"trending {region} failed: {e}")

    if not candidates:
        raise RuntimeError(f"No new trending videos found for the {block} niche.")

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

    _mark_seen(best["video_id"], block)
    if progress:
        progress(block, f"Reacting to: {best['title']} "
                        f"({best['channel']}, {best['views']:,} views)")
    return best


def fetch_trending_game_video(cfg, progress=None):
    """Back-compat alias for the games niche."""
    return fetch_trending_video(cfg, progress, block="games")
