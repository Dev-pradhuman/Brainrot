"""Free end-to-end Shorts pipeline.

Groq (free tier) writes a script  ->  edge-tts (free) voices it with per-word
timings  ->  ffmpeg burns word-by-word captions over a looping background and
muxes the audio.  No paid services.

Run standalone:  python generate.py horror
"""
import asyncio
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import time

import requests

import video as video_mod

HERE = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------- #
# Config + secrets
# --------------------------------------------------------------------------- #
def _load_env(path=os.path.join(HERE, ".env")):
    """Tiny .env loader (no python-dotenv dependency)."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key, val = key.strip(), val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _resolve_env(obj):
    """Recursively replace ${VAR} placeholders in strings with env values."""
    if isinstance(obj, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), obj)
    if isinstance(obj, dict):
        return {k: _resolve_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env(v) for v in obj]
    return obj


def load_config():
    _load_env()
    with open(os.path.join(HERE, "config.json"), "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return _resolve_env(cfg)


def _cat_cfg(cfg, category):
    """Merge top-level config with the per-category overrides (category wins)."""
    merged = {k: v for k, v in cfg.items() if k != "categories"}
    cat = (cfg.get("categories") or {}).get(category, {})
    merged.update(cat)
    return merged


# --------------------------------------------------------------------------- #
# 1. Script generation (Groq, OpenAI-compatible)
# --------------------------------------------------------------------------- #
THEME_PROMPTS = {
    "reddit": (
        "a highly specific, bizarre first-person confession story (e.g. involving weird double-lives, "
        "bizarre inheritances, or secret corporate sabotage) with a rapid setup and an organic, "
        "mind-bending twist. Avoid generic cheating or simple family secrets."
    ),
    "relationship": (
        "a raw, emotionally gripping first-person relationship conflict. It must feel like an authentic, "
        "heartbreaking text thread or conversation about a bizarre dealbreaker or hidden truth that "
        "destroys the connection instantly, ending on a chilling realizations."
    ),
    "cold": (
        "a brutal, raw psychological truth about success, human nature, or survival. Write in second-person "
        "(you), sounding like a cold, experienced mentor sharing truths that people are too soft to admit. "
        "High-density wisdom, zero fluff."
    ),
    "horror": (
        "a chilling first-person horror story about an ordinary occurrence that turns deeply wrong "
        "(e.g., a smart home assistant responding to someone else, a photo backup showing unknown pictures, "
        "or a GPS leading into a nonexistent road). End on a highly unsettling, unresolved cliffhanger."
    ),
    "simpsons": (
        "a fast-paced, funny Springfield theory or dark parody story told from a fan's perspective, "
        "exploring crazy what-if scenarios (e.g., Homer's coma, Bart's future), filled with fast-paced comedic irony."
    ),
    "anime": (
        "a highly controversial, mind-blowing anime theory or hidden detail (e.g., in Attack on Titan, "
        "Death Note, or One Piece) that immediately triggers debate in the comments. Frame it as 'the secret "
        "no one noticed' and explain the evidence with high conviction."
    ),
    "betrayal": (
        "a shocking, high-stakes first-person betrayal story. Focus on a massive breach of trust by a best friend, "
        "business partner, or close family member where the narrator uncovers a long-running, elaborate secret. "
        "End on the exact moment of confrontation."
    ),
    "funny": (
        "a chaotic, hilarious real-life embarrassment story that escalates out of control (e.g., job interview "
        "mixups, dating disasters, or texting the wrong group chat). High speed comedy with punchy visual details."
    ),
    "games": (
        "a high-octane commentary on a legendary, highly clutch gaming moment or speedrun scandal. Make the viewer "
        "feel the intensity of what happened and why it broke the internet."
    ),
    "space": (
        "a terrifying, awe-inspiring cosmic mystery or space anomaly (e.g., the Great Attractor, the Fermi Paradox, "
        "rogue planets, or strange signals from deep space). Make the viewer feel the absolute horror of the "
        "universe's scale and silence."
    ),
}

FALLBACK_STORY = {
    "title": "The Night Everything Changed",
    "hook": "I never told anyone what really happened that night.",
    "script": (
        "I never told anyone what really happened that night. It started like any "
        "other evening, quiet and ordinary, until I heard a knock at the door. "
        "Nobody should have been there. When I looked through the window, there was "
        "no one. But the knocking kept going, slow and patient, like whoever it was "
        "had all the time in the world. I called out, and the knocking stopped. "
        "Then my phone buzzed. A message from an unknown number. It said: open the "
        "door. I never did. And to this day, I still hear that knock."
    ),
}


def generate_script(cfg, category, progress=None):
    """Return {'title','hook','script'} using Groq, with a safe fallback."""
    llm = cfg.get("llm", {})
    theme = THEME_PROMPTS.get(category, THEME_PROMPTS["reddit"])
    target = cfg.get("target_words", 300)

    if not llm.get("enabled") or not llm.get("api_key"):
        if progress:
            progress("script", "LLM disabled/unconfigured — using fallback story.")
        return dict(FALLBACK_STORY)

    if progress:
        progress("script", f"Writing a {category} script with {llm.get('model')}...")

    sys_prompt = (
        "You are a master scriptwriter specialized in 1M+ views YouTube Shorts and TikToks. "
        "You understand that the first 3 seconds are everything. You write spoken-word voiceovers "
        "that start immediately without introduction. You use raw, high-tension conversational style, "
        "short punchy sentences, and open strong curiosity loops. "
        "CRITICAL RULES: \n"
        "- NEVER start with introductory phrases like 'I always thought', 'It started like any other day', 'I never told anyone', 'so basically', or 'let me explain'.\n"
        "- NEVER use cliches, emojis, hashtags, markdown, stage directions, or narration guides.\n"
        "- Ensure each story has a unique, specific, and creative plot setup to prevent scripts from sounding similar.\n"
        "- Output ONLY valid JSON."
    )
    user_prompt = (
        f"Write a viral short-form script about {theme}.\n\n"
        "Requirements:\n"
        "- HOOK (first 3-5 seconds, 1 sentence): Start directly with a shocking, highly specific statement or "
        "action that disrupts the scroll. The viewer must feel an immediate urge to find out what happens next.\n"
        f"- SCRIPT: starts with the exact hook, followed by ~{target} words of continuous, spoken-word voiceover. "
        "Write in highly conversational, short, active sentences. Build tension, keep the pacing fast, and "
        "finish with a punchy twist, ironic payoff, or unsettling cliffhanger.\n"
        "- TITLE: high-CTR, highly engaging title under 70 characters. Do not use all caps or emojis.\n\n"
        'Respond as JSON: {"title": "...", "hook": "...", "script": "..."}'
    )

    try:
        resp = requests.post(
            f"{llm['base_url']}/chat/completions",
            headers={"Authorization": f"Bearer {llm['api_key']}"},
            json={
                "model": llm["model"],
                "temperature": llm.get("temperature", 1.0),
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        data = json.loads(_strip_fences(content))
        script = (data.get("script") or "").strip()
        if not script:
            raise ValueError("empty script")
        return {
            "title": (data.get("title") or f"A {category} story").strip()[:95],
            "hook": (data.get("hook") or script.split(".")[0]).strip(),
            "script": script,
        }
    except Exception as e:  # noqa: BLE001
        if progress:
            progress("script", f"LLM failed ({e}) — using fallback story.")
        return dict(FALLBACK_STORY)


def _strip_fences(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def generate_games_script(cfg, category, progress=None):
    """Fetch a trending gaming video and write a reaction script about it.

    Falls back to a generic games story if the fetch or LLM call fails.
    """
    import games

    try:
        info = games.fetch_trending_game_video(cfg, progress)
    except Exception as e:  # noqa: BLE001
        if progress:
            progress("script", f"Games fetch failed ({e}) — using generic games story.")
        return generate_script(cfg, category, progress)

    llm = cfg.get("llm", {})
    target = cfg.get("target_words", 300)
    if not llm.get("enabled") or not llm.get("api_key"):
        # No LLM: build a serviceable reaction script from the metadata directly.
        script = (
            f"You won't believe what {info['channel']} just did. Their new video, "
            f"{info['title']}, is already blowing up with over {info['views']:,} views. "
            "Gamers everywhere are losing their minds over this one. If you're into "
            "gaming, you have to see how this plays out. Follow for more insane gaming "
            "moments every single day."
        )
        return {
            "title": f"{info['channel']} just did the UNTHINKABLE",
            "hook": f"You won't believe what {info['channel']} just did.",
            "script": script,
            "source_url": info["url"],
        }

    if progress:
        progress("script", f"Writing a games reaction with {llm.get('model')}...")

    sys_prompt = (
        "You are a top-tier viral gaming Shorts scriptwriter. You hype up trending "
        "gaming clips so viewers NEED to watch. The first 5 seconds decide everything. "
        "Never invent specific facts that aren't supported by the video info given. "
        "No emojis, hashtags, or markdown. Output ONLY valid JSON."
    )
    user_prompt = (
        "Write a hyped reaction/commentary voiceover for a YouTube Short about this "
        "trending gaming video:\n\n"
        f"Title: {info['title']}\n"
        f"Channel: {info['channel']}\n"
        f"Views: {info['views']:,}\n"
        f"Tags: {', '.join(info['tags'][:10])}\n"
        f"Description: {info['description'][:600]}\n\n"
        "Requirements:\n"
        "- HOOK (first ~5 seconds): a scroll-stopping 'you won't believe what this "
        "gamer just did' style line that teases the craziest part without spoiling it.\n"
        f"- SCRIPT: starts with the hook, ~{target} words, hyped and conversational, "
        "builds excitement, and ends by telling viewers to follow for more gaming "
        "clips. Base everything on the title/description above; don't fabricate "
        "specific events.\n"
        "- TITLE: high-CTR YouTube title under 70 chars referencing the gamer/game.\n\n"
        'Respond as JSON: {"title": "...", "hook": "...", "script": "..."}'
    )

    try:
        resp = requests.post(
            f"{llm['base_url']}/chat/completions",
            headers={"Authorization": f"Bearer {llm['api_key']}"},
            json={
                "model": llm["model"],
                "temperature": llm.get("temperature", 1.0),
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = json.loads(_strip_fences(resp.json()["choices"][0]["message"]["content"]))
        script = (data.get("script") or "").strip()
        if not script:
            raise ValueError("empty script")
        return {
            "title": (data.get("title") or info["title"]).strip()[:95],
            "hook": (data.get("hook") or script.split(".")[0]).strip(),
            "script": script,
            "source_url": info["url"],
        }
    except Exception as e:  # noqa: BLE001
        if progress:
            progress("script", f"LLM failed ({e}) — using metadata-based script.")
        return {
            "title": f"{info['channel']} just did the UNTHINKABLE",
            "hook": f"You won't believe what {info['channel']} just did.",
            "script": (
                f"You won't believe what {info['channel']} just did in {info['title']}. "
                f"It's already at {info['views']:,} views and blowing up. Follow for "
                "more insane gaming moments every day."
            ),
            "source_url": info["url"],
        }


# --------------------------------------------------------------------------- #
# 2. Voiceover (edge-tts, free) with per-word timings
# --------------------------------------------------------------------------- #
async def _edge_tts(text, voice, rate, pitch, out_path):
    import edge_tts

    kwargs = {"voice": voice}
    if rate:
        kwargs["rate"] = rate
    if pitch:
        kwargs["pitch"] = pitch
    communicate = edge_tts.Communicate(text, **kwargs)

    words, sentences = [], []
    with open(out_path, "wb") as f:
        async for chunk in communicate.stream():
            kind = chunk["type"]
            if kind == "audio":
                f.write(chunk["data"])
            elif kind in ("WordBoundary", "SentenceBoundary"):
                item = {
                    "text": chunk["text"],
                    "start": chunk["offset"] / 1e7,            # 100ns ticks -> s
                    "end": (chunk["offset"] + chunk["duration"]) / 1e7,
                }
                (words if kind == "WordBoundary" else sentences).append(item)
    # Newer edge-tts/Microsoft voices emit only SentenceBoundary. Synthesize
    # per-word timings by splitting each sentence and spreading its duration
    # across words, weighted by word length (longer words take more time).
    if not words and sentences:
        words = _words_from_sentences(sentences)
    return words


def _words_from_sentences(sentences):
    words = []
    for sent in sentences:
        toks = re.findall(r"\S+", sent["text"])
        if not toks:
            continue
        weights = [len(t) + 1 for t in toks]
        total = sum(weights)
        span = max(sent["end"] - sent["start"], 0.01)
        t = sent["start"]
        for tok, wgt in zip(toks, weights):
            dur = span * (wgt / total)
            words.append({"text": tok, "start": t, "end": t + dur})
            t += dur
    return words


def synthesize_voice(cfg, text, out_path, progress=None):
    voice = cfg.get("edge_voice", "en-US-AvaMultilingualNeural")
    rate = cfg.get("edge_rate", "+0%")
    pitch = cfg.get("edge_pitch", "+0Hz")
    if progress:
        progress("tts", f"Voicing with edge-tts ({voice})...")
    words = asyncio.run(_edge_tts(text, voice, rate, pitch, out_path))
    return words


# --------------------------------------------------------------------------- #
# 3. Captions (ASS subtitle file, word-by-word)
# --------------------------------------------------------------------------- #
def _ass_time(t):
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _chunk_words(words, max_words, max_ms):
    """Group word timings into caption chunks of <= max_words / max_ms."""
    chunks, cur = [], []
    for w in words:
        if not w["text"].strip():
            continue
        if cur:
            span_ms = (w["end"] - cur[0]["start"]) * 1000
            if len(cur) >= max_words or span_ms > max_ms:
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    # Extend each chunk's end to the next chunk's start so captions never blink out.
    for i, ch in enumerate(chunks):
        start = ch[0]["start"]
        end = chunks[i + 1][0]["start"] if i + 1 < len(chunks) else ch[-1]["end"] + 0.4
        ch_text = " ".join(w["text"] for w in ch)
        ch[:] = [{"start": start, "end": end, "text": ch_text}]
    return [c[0] for c in chunks]


def write_ass(cfg, words, ass_path):
    cap = cfg.get("caption", {})
    w, h = cfg.get("resolution", [1080, 1920])
    chunks = _chunk_words(
        words,
        cap.get("max_words", 3),
        cap.get("max_caption_ms", 1200),
    )

    style = ",".join(str(x) for x in [
        "Default",
        cap.get("font", "Anton"),
        cap.get("font_size", 96),
        cap.get("primary_color", "&H00FFFFFF"),
        "&H000000FF",                              # secondary (unused)
        cap.get("outline_color", "&H00000000"),
        "&H64000000",                              # back / shadow colour
        -1 if cap.get("bold") else 0,
        0, 0, 0,                                   # italic, underline, strikeout
        100, 100, 0, 0,                            # scaleX, scaleY, spacing, angle
        1,                                         # border style (outline+shadow)
        cap.get("outline", 5),
        cap.get("shadow", 1),
        cap.get("alignment", 5),
        40, 40,                                    # marginL, marginR
        cap.get("margin_v", 0),
        1,                                         # encoding
    ])

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {w}",
        f"PlayResY: {h}",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
         "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
         "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
         "MarginL, MarginR, MarginV, Encoding"),
        f"Style: {style}",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    upper = cap.get("uppercase", True)
    for c in chunks:
        text = c["text"].upper() if upper else c["text"]
        text = text.replace("\n", " ").strip()
        lines.append(
            f"Dialogue: 0,{_ass_time(c['start'])},{_ass_time(c['end'])},"
            f"Default,,0,0,0,,{text}"
        )
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# --------------------------------------------------------------------------- #
# 4. Reddit title card (optional, PIL)
# --------------------------------------------------------------------------- #
def make_reddit_card(cfg, text, out_path):
    from PIL import Image, ImageDraw, ImageFont

    rc = cfg.get("reddit_card", {})
    width = rc.get("width", 960)
    subreddit = rc.get("subreddit", "r/AskReddit")
    pad = 48

    def _font(size, bold=False):
        for name in (("arialbd.ttf", "arial.ttf") if bold else ("arial.ttf",)):
            p = os.path.join(r"C:\Windows\Fonts", name)
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
        return ImageFont.load_default()

    body_font = _font(46, bold=True)
    meta_font = _font(34)

    # word-wrap the question to the card width
    dummy = Image.new("RGB", (10, 10))
    dd = ImageDraw.Draw(dummy)
    max_w = width - pad * 2
    words, line, wrapped = text.split(), "", []
    for word in words:
        trial = (line + " " + word).strip()
        if dd.textlength(trial, font=body_font) <= max_w:
            line = trial
        else:
            wrapped.append(line)
            line = word
    if line:
        wrapped.append(line)

    line_h = body_font.getbbox("Ay")[3] + 14
    height = pad * 2 + 64 + len(wrapped) * line_h
    img = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, width - 1, height - 1], radius=28,
                        fill=(255, 255, 255, 255))

    # avatar dot + subreddit name
    d.ellipse([pad, pad, pad + 44, pad + 44], fill=(255, 69, 0, 255))
    d.text((pad + 60, pad + 6), subreddit, font=meta_font, fill=(70, 70, 70, 255))

    y = pad + 64
    for ln in wrapped:
        d.text((pad, y), ln, font=body_font, fill=(20, 20, 20, 255))
        y += line_h

    img.save(out_path)
    return out_path


# --------------------------------------------------------------------------- #
# 5. Compose with ffmpeg
# --------------------------------------------------------------------------- #
VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm")


def _pick_background(bg_dir):
    if bg_dir and os.path.isdir(bg_dir):
        vids = [os.path.join(bg_dir, f) for f in os.listdir(bg_dir)
                if f.lower().endswith(VIDEO_EXTS)]
        if vids:
            return random.choice(vids)
    return None


def compose(cfg, bg_path, audio_path, ass_name, duration, out_path,
            work_dir, card_path=None, progress=None):
    w, h = cfg.get("resolution", [1080, 1920])
    fps = cfg.get("fps", 30)
    ff = video_mod.ffmpeg_exe()

    cmd = [ff, "-y"]
    if bg_path:
        cmd += ["-stream_loop", "-1", "-i", bg_path]
    else:
        cmd += ["-f", "lavfi", "-i", f"color=c=0x111118:s={w}x{h}:r={fps}"]
    cmd += ["-i", audio_path]

    card_idx = None
    if card_path:
        cmd += ["-i", card_path]
        card_idx = 2

    # Build the filter graph. ass_name is referenced relative to work_dir (cwd).
    chain = [f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase",
             f"crop={w}:{h}", "setsar=1", f"fps={fps}"]
    vlabel = "[bg]"
    fc = "%s%s" % (",".join(chain), vlabel)

    if card_idx is not None:
        ry = cfg.get("reddit_card", {}).get("y_ratio", 0.30)
        fc += (f";{vlabel}[{card_idx}:v]overlay=(W-w)/2:H*{ry}:"
               f"enable='lt(t,4)'[cv]")
        vlabel = "[cv]"

    fc += f";{vlabel}subtitles={ass_name}:fontsdir=.[v]"

    cmd += [
        "-filter_complex", fc,
        "-map", "[v]", "-map", "1:a",
        "-t", f"{duration:.2f}",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        out_path,
    ]

    if progress:
        progress("render", "Compositing background + captions + audio (ffmpeg)...")
    proc = subprocess.run(cmd, cwd=work_dir, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise RuntimeError(f"ffmpeg failed:\n{tail}")
    return out_path


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _slug(text, maxlen=60):
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:maxlen] or "short"


def generate_video(cfg, category="reddit", progress=None):
    cat = _cat_cfg(cfg, category)

    out_dir = cfg.get("output_dir", "../output")
    if not os.path.isabs(out_dir):
        out_dir = os.path.normpath(os.path.join(HERE, out_dir))
    os.makedirs(out_dir, exist_ok=True)

    if progress:
        progress("start", f"Starting generation for '{category}'")

    # 1. script
    if category == "games":
        story = generate_games_script(cat, category, progress)
    else:
        story = generate_script(cat, category, progress)

    work_dir = tempfile.mkdtemp(prefix="short_", dir=HERE)
    try:
        audio_path = os.path.join(work_dir, "voice.mp3")
        ass_name = "subs.ass"
        ass_path = os.path.join(work_dir, ass_name)

        # 2. voiceover + word timings
        words = synthesize_voice(cat, story["script"], audio_path, progress)
        duration = video_mod.get_duration(audio_path)
        if duration <= 0 and words:
            duration = words[-1]["end"] + 0.4
        duration = min(duration, cfg.get("max_seconds", 180))

        # 3. captions
        if progress:
            progress("captions", f"Building captions ({len(words)} words)...")
        write_ass(cfg, words, ass_path)

        # make the caption font discoverable by ffmpeg's subtitles filter
        font_src = os.path.join(HERE, cfg.get("caption", {}).get("fonts_dir", "fonts"),
                                "Anton-Regular.ttf")
        if os.path.exists(font_src):
            shutil.copy(font_src, work_dir)

        # 4. optional reddit card
        card_path = None
        if cat.get("reddit_card", {}).get("enabled"):
            card_path = os.path.join(work_dir, "card.png")
            make_reddit_card(cat, story.get("hook") or story["title"], card_path)

        # 5. background + compose
        bg_dir = cat.get("background_dir", cfg.get("background_dir"))
        if bg_dir and not os.path.isabs(bg_dir):
            bg_dir = os.path.normpath(os.path.join(HERE, bg_dir))
        bg_path = _pick_background(bg_dir)
        if progress:
            progress("background",
                     f"Background: {os.path.basename(bg_path) if bg_path else 'solid color (no clips found)'}")

        out_name = f"{category}_{_slug(story['title'])}_{int(time.time())}.mp4"
        out_path = os.path.join(out_dir, out_name)
        compose(cfg, bg_path, audio_path, ass_name, duration, out_path,
                work_dir, card_path=card_path, progress=progress)

        if progress:
            progress("done", f"Done: {out_name} ({duration:.1f}s)")

        return {
            "path": out_path,
            "title": story["title"],
            "duration": duration,
            "category": category,
            "script": story["script"],
            "hook": story.get("hook", ""),
            "source_url": story.get("source_url"),
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    import sys
    cat = sys.argv[1] if len(sys.argv) > 1 else "reddit"
    res = generate_video(load_config(), category=cat,
                         progress=lambda s, m: print(f"[{s}] {m}"))
    print("\nSaved:", res["path"])
