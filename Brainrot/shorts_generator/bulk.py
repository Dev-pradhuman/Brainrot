"""Bulk generation with anti-spam scheduling.

Generates a custom number of shorts back to back, waiting a randomized,
gradually-increasing interval between each upload so YouTube doesn't flag the
channel for spammy bursts. Hard-capped (default 10 videos/day, 5-15 min apart).

CLI:
    python bulk.py reddit 5 --upload
    python bulk.py games 3 --upload --private
"""
import random
import time

import generate as gen
import metadata as meta_mod
import youtube_upload as yt


import os

HERE = os.path.dirname(os.path.abspath(__file__))


def _interval(i, n, lo, hi):
    """Interval before the (i+1)-th wait: ramps lo->hi across the run, + jitter."""
    if n <= 1:
        return lo
    frac = i / (n - 1)                       # 0.0 .. 1.0
    base = lo + (hi - lo) * frac             # gradual increase
    jitter = random.uniform(-0.25, 0.25) * (hi - lo)
    return max(lo, min(hi, base + jitter))   # clamp to [lo, hi]


def bulk_generate(category, count, do_upload=False, privacy="public",
                  cfg=None, progress=None, on_result=None, sleep_fn=None):
    """Generate `count` shorts, spacing uploads with random anti-spam waits.

    `category` may be a single niche (str) or a list of niches. With a list, the
    run cycles through them round-robin (e.g. ["reddit","horror"] with count=4 ->
    reddit, horror, reddit, horror) so one batch can mix niches.

    progress(stage, msg)  -> live log callback
    on_result(result)     -> called after each finished video
    sleep_fn(seconds)     -> override for testing (defaults to time.sleep)
    Returns the list of result dicts.
    """
    cfg = cfg or gen.load_config()
    bcfg = cfg.get("bulk", {})
    cap = int(bcfg.get("daily_cap", 10))
    lo = int(bcfg.get("min_interval_sec", 300))
    hi = int(bcfg.get("max_interval_sec", 900))
    sleep_fn = sleep_fn or time.sleep

    cats = list(category) if isinstance(category, (list, tuple)) else [category]
    cats = [c for c in cats if c] or ["reddit"]

    count = max(1, min(int(count), cap))

    def log(stage, msg):
        if progress:
            progress(stage, msg)

    # Pre-validation check: Ensure tokens exist for all chosen niches BEFORE we start generating
    if do_upload:
        missing_tokens = []
        used_cats = set()
        for i in range(count):
            used_cats.add(cats[i % len(cats)])
            
        for cat in used_cats:
            cat_cfg = (cfg.get("categories") or {}).get(cat, {})
            token = cat_cfg.get("youtube_token") or "token.json"
            token_path = token if os.path.isabs(token) else os.path.join(HERE, token)
            if not os.path.exists(token_path):
                missing_tokens.append(f"{cat} (needs: {token})")
                
        if missing_tokens:
            err_msg = f"Authorization required. Missing tokens for: {', '.join(missing_tokens)}. Run 'python youtube_upload.py <token_file>' in terminal first."
            log("bulk", f"ERROR: {err_msg}")
            raise FileNotFoundError(err_msg)

    if count >= cap:
        log("bulk", f"Capped at {cap} videos/day (anti-spam).")
    if len(cats) > 1:
        log("bulk", f"Mixing niches round-robin: {', '.join(cats)}")

    results = []
    for i in range(count):
        cat = cats[i % len(cats)]
        log("bulk", f"=== Video {i + 1}/{count}  [{cat}] ===")
        result = gen.generate_video(cfg, category=cat, progress=progress)
        md = meta_mod.build_metadata(result)
        result["meta"] = md

        if do_upload:
            cat_cfg = (cfg.get("categories") or {}).get(cat, {})
            token = cat_cfg.get("youtube_token")
            secret = cat_cfg.get("youtube_client_secret")
            log("upload", f"Uploading {i + 1}/{count} -> {token or 'token.json'} ...")
            try:
                up = yt.upload_video(
                    result["path"], md["title"], md["description"], md["tags"],
                    privacy=privacy, progress=progress,
                    token_file=token, secret_file=secret,
                )
                result["youtube"] = up
                log("upload", f"Uploaded: {up['url']}")
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                result["upload_error"] = msg
                log("upload", f"UPLOAD ERROR: {msg}")
                if "uploadLimitExceeded" in msg or "exceeded the number" in msg:
                    log("bulk", "YouTube daily upload limit hit — stopping bulk run.")
                    results.append(result)
                    if on_result:
                        on_result(result)
                    break

        results.append(result)
        if on_result:
            on_result(result)

        if i < count - 1:
            wait = _interval(i, count, lo, hi)
            m, s = divmod(int(wait), 60)
            log("wait", f"Waiting {m}m{s:02d}s before next video (anti-spam)...")
            sleep_fn(wait)

    log("done", f"Bulk run finished: {len(results)} video(s) generated"
                f"{' & uploaded' if do_upload else ''}.")
    return results


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    # first positional may be a single niche or comma-separated list: reddit,horror
    category = [c.strip() for c in args[0].split(",")] if args else "reddit"
    count = int(args[1]) if len(args) > 1 else 1
    do_upload = "--upload" in args
    privacy = "private" if "--private" in args else (
        "unlisted" if "--unlisted" in args else "public")

    bulk_generate(
        category, count, do_upload=do_upload, privacy=privacy,
        progress=lambda stage, msg: print(f"[{stage}] {msg}"),
    )
