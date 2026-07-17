"""Import a long-form video and extract ranked short-form clip candidates.

A parallel path to generate.py, not a replacement.  generate.py invents a story
and speaks it; this module takes footage that already contains real speech and
finds the parts worth clipping:

    ffmpeg audio extract -> transcribe (Deepgram or local whisper)
      -> Groq ranks "viral moments" -> ffmpeg center-crop to 9:16
      -> existing ASS captions / metadata / YouTube upload

The script-writing, edge-tts and Reddit-card steps are skipped entirely.

Pipeline logic ported from JayWebtech/autoshorts (Tauri + Rust):
  - audio extraction + 9:16 crop expression   <- its media.rs
  - Deepgram normalisation + word->segment merge <- its transcription.rs
  - viral-moment prompt + candidate ranking   <- its llm.rs

Run standalone:  python import_clip.py path/to/long_video.mp4
"""
import json
import os
import re
import shutil
import subprocess
import time

import requests

import generate as gen
import video as video_mod

HERE = os.path.dirname(os.path.abspath(__file__))


def _icfg(cfg):
    return cfg.get("import", {}) or {}


# --------------------------------------------------------------------------- #
# 1. Audio extraction  (autoshorts media.rs::extract_audio)
# --------------------------------------------------------------------------- #
def extract_audio(src_path, work_dir, progress=None):
    """Extract a mono 16kHz wav — the format both Deepgram and whisper expect."""
    out_path = os.path.join(work_dir, "transcription_audio.wav")
    if progress:
        progress("audio", "Extracting audio track (ffmpeg)...")
    cmd = [video_mod.ffmpeg_exe(), "-y", "-i", src_path,
           "-vn", "-ac", "1", "-ar", "16000", out_path]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise RuntimeError(f"ffmpeg audio extraction failed:\n{tail}")
    return out_path


# --------------------------------------------------------------------------- #
# 2. Transcription
# --------------------------------------------------------------------------- #
DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"


def build_segments(words):
    """Merge word timings into speech segments.

    Breaks on a pause longer than 0.9s, a speaker change, or a sentence-ending
    punctuation mark, so the transcript handed to the LLM is chunked at natural
    thought boundaries (autoshorts transcription.rs::build_segments).
    """
    segments, cur = [], None
    for w in words:
        if cur is not None:
            pause = w["start"] - cur["end"]
            speaker_changed = cur.get("speaker") != w.get("speaker")
            sentence_end = cur["text"].endswith((".", "!", "?"))
            if pause > 0.9 or speaker_changed or sentence_end:
                segments.append(cur)
                cur = None
        if cur is None:
            cur = {"start": w["start"], "end": w["end"],
                   "speaker": w.get("speaker"), "text": w["text"]}
        else:
            cur["end"] = w["end"]
            cur["text"] += " " + w["text"]
    if cur:
        segments.append(cur)
    return segments


def _normalize_deepgram(data):
    """Deepgram JSON -> the shared transcript shape."""
    channels = (data.get("results") or {}).get("channels") or []
    alts = (channels[0] if channels else {}).get("alternatives") or []
    alt = alts[0] if alts else {}
    meta = data.get("metadata") or {}

    raw_words = alt.get("words")
    if not raw_words:
        raise RuntimeError("Deepgram response did not include word timestamps.")

    words, speakers = [], set()
    for w in raw_words:
        # punctuated_word carries smart_format's capitalisation/punctuation,
        # which build_segments needs to detect sentence ends.
        text = (w.get("punctuated_word") or w.get("word") or "").strip()
        if not text:
            continue
        speaker = None
        if w.get("speaker") is not None:
            speaker = f"S{int(w['speaker']) + 1}"
            speakers.add(speaker)
        words.append({
            "text": text,
            "start": float(w.get("start") or 0.0),
            "end": float(w.get("end") or 0.0),
            "speaker": speaker,
        })

    return {
        "language": meta.get("language", "en"),
        "duration": float(meta.get("duration") or 0.0),
        "speakers": sorted(speakers),
        "words": words,
        "segments": build_segments(words),
    }


def _transcribe_deepgram(audio_path, cfg, progress=None):
    dg = _icfg(cfg).get("deepgram", {})
    key = (dg.get("api_key") or "").strip()
    if not key:
        raise RuntimeError(
            "DEEPGRAM_API_KEY is not set. Add it to .env, or set "
            "import.transcription_engine to 'whisper' in config.json."
        )
    if progress:
        progress("transcribe", f"Transcribing with Deepgram ({dg.get('model', 'nova-2')})...")

    with open(audio_path, "rb") as f:
        audio = f.read()

    resp = requests.post(
        DEEPGRAM_URL,
        params={
            "model": dg.get("model", "nova-2"),
            "smart_format": "true",
            "diarize": "true",
            "punctuate": "true",
            "filler_words": "true",
        },
        headers={"Authorization": f"Token {key}", "Content-Type": "audio/wav"},
        data=audio,
        timeout=600,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Deepgram request failed ({resp.status_code}): {resp.text[:300]}")
    return _normalize_deepgram(resp.json())


def _transcribe_whisper(audio_path, cfg, progress=None):
    """Local, free fallback. Downloads the model on first use only."""
    wc = _icfg(cfg).get("whisper", {})
    size = wc.get("model_size", "base")
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError(
            "faster-whisper is not installed. Run 'pip install faster-whisper', "
            "or set import.transcription_engine to 'deepgram' in config.json."
        ) from e

    if progress:
        progress("transcribe", f"Loading local whisper '{size}' "
                               f"(first run downloads the model)...")
    model = WhisperModel(size, device=wc.get("device", "cpu"),
                         compute_type=wc.get("compute_type", "int8"))

    if progress:
        progress("transcribe", "Transcribing locally — this is CPU-bound and slow on long videos...")
    seg_iter, info = model.transcribe(audio_path, word_timestamps=True)

    words, segments = [], []
    for s in seg_iter:                       # generator — consuming it does the work
        text = (s.text or "").strip()
        if text:
            segments.append({"start": s.start, "end": s.end,
                             "speaker": "S1", "text": text})
        for w in (s.words or []):
            wt = (w.word or "").strip()
            if wt:
                words.append({"text": wt, "start": w.start,
                              "end": w.end, "speaker": "S1"})
        if progress and len(segments) % 25 == 0 and segments:
            progress("transcribe", f"Transcribed up to {s.end:.0f}s...")

    if not words:
        raise RuntimeError("Local whisper returned no word timestamps.")

    return {
        "language": getattr(info, "language", "en") or "en",
        "duration": float(getattr(info, "duration", 0.0) or words[-1]["end"]),
        "speakers": ["S1"],
        "words": words,
        # whisper has no diarisation, so rebuild segments on pause/punctuation
        # only — same rule Deepgram output goes through.
        "segments": build_segments(words),
    }


def transcribe(cfg, audio_path, progress=None):
    """Dispatch to the configured engine, falling back to whisper if available."""
    engine = (_icfg(cfg).get("transcription_engine") or "deepgram").lower()
    if engine == "whisper":
        return _transcribe_whisper(audio_path, cfg, progress)
    try:
        return _transcribe_deepgram(audio_path, cfg, progress)
    except Exception as e:  # noqa: BLE001
        if progress:
            progress("transcribe", f"Deepgram failed ({e}) — trying local whisper...")
        return _transcribe_whisper(audio_path, cfg, progress)


# --------------------------------------------------------------------------- #
# 3. Viral-moment detection  (autoshorts llm.rs)
# --------------------------------------------------------------------------- #
def _compact_segments(segments):
    """Format the transcript for the prompt: [12.34-56.78] S1: text"""
    return "\n".join(
        f"[{s['start']:.2f}-{s['end']:.2f}] {s.get('speaker') or 'Speaker'}: {s['text']}"
        for s in segments
    )


# The prompt autoshorts sends is identical across all six of its providers; the
# hardened wording below is its local-LLM variant, which adds the explicit
# duration/count/timestamp guards.  Only the provider plumbing differs, so this
# points at Groq — already configured here, and free.
VIRAL_MOMENT_SYSTEM = (
    "You are identifying the most viral moments and strongest short-form clip "
    "candidates from a long-form transcript. For each candidate, the clip must be "
    "self-contained, starting with an extremely engaging hook within the first 3 "
    "seconds (to capture immediate attention on social feeds), {lo}-{hi} seconds long, "
    "and cut at clean sentence/thought boundaries. "
    "CRITICAL: each candidate MUST have a duration between {lo} and {hi} seconds "
    "(i.e. 'end' minus 'start' must be between {lo}.0 and {hi}.0). Do NOT return clips "
    "shorter than {lo} seconds. Combine multiple adjacent sentences to build a "
    "meaningful segment. "
    "Favor highly shareable content: concrete stories, strong opinions, emotional "
    "turns, surprising or counter-intuitive claims, clear payoffs, and "
    "high-energy/dramatic peaks. Avoid rambling setup, context-dependent "
    "references, and pure filler. "
    "You MUST identify and return at least 3-5 candidates (up to {n}). Do not return "
    "an empty candidates list. Ensure 'start' and 'end' correspond to actual "
    "timestamps in the transcript. Do not output 0.0 for start and end times. "
    "'score' is 0.0-1.0. Output ONLY valid JSON matching this schema: "
    '{{"candidates":[{{"start":0.0,"end":0.0,"score":0.0,"hook":"...","rationale":"..."}}]}}'
)

# Keys different models wrap the array in.
_CANDIDATE_KEYS = ("candidates", "Candidates", "moments", "clips", "segments", "results")


def _coerce_float(val, default=0.0):
    if isinstance(val, bool):
        return default
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val.strip())
        except ValueError:
            return default
    return default


def _normalize_score(score):
    """Fold the scales models actually return (0-10, 0-100) into 0-1."""
    if 1.0 < score <= 10.0:
        return score / 10.0
    if 10.0 < score <= 100.0:
        return score / 100.0
    if score > 100.0:
        return 1.0
    if score < 0.0:
        return 0.0
    return score


def _extract_candidate_array(val):
    """Dig the candidate list out of whatever shape the model returned."""
    if isinstance(val, list):
        return val
    if not isinstance(val, dict):
        return None
    for key in _CANDIDATE_KEYS:
        arr = val.get(key)
        if isinstance(arr, list):
            return arr
    # Any single list value will do.
    for v in val.values():
        if isinstance(v, list):
            return v
    # A bare single candidate object.
    if "start" in val and "end" in val:
        return [val]
    return None


def _parse_candidates(text, min_duration, limit=10):
    """Parse + rank the model's JSON into clip candidates.

    Ported from autoshorts llm.rs::parse_candidate_json — score normalisation,
    the relaxed second pass when nothing clears min_duration, sort by score
    descending, truncate.
    """
    data = json.loads(gen._strip_fences(text))
    arr = _extract_candidate_array(data)
    if arr is None:
        raise ValueError(f"no candidates array in model output: {str(data)[:200]}")

    drafts = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        drafts.append({
            "start": _coerce_float(item.get("start"), 0.0),
            "end": _coerce_float(item.get("end"), 0.0),
            "score": _normalize_score(_coerce_float(item.get("score"), 0.8)),
            "hook": (item.get("hook") or "").strip(),
            "rationale": (item.get("rationale") or "").strip(),
        })

    def _keep(c, floor):
        return (c["end"] - c["start"]) >= floor and c["hook"]

    cands = [c for c in drafts if _keep(c, min_duration)]
    if not cands:
        # Relaxed retry rather than returning nothing — a too-strict model is
        # still more useful than an empty list.
        cands = [c for c in drafts if _keep(c, 5.0)]

    cands.sort(key=lambda c: c["score"], reverse=True)
    return cands[:limit]


def _fallback_candidates(transcript, lo, hi, limit):
    """Fixed-window fallback when the LLM is unavailable or returns garbage.

    Packs consecutive segments into <=hi-second windows on segment boundaries and
    ranks by words-per-second, so denser stretches float up.  Crude, but it keeps
    the import path usable without an LLM (mirrors generate.py's FALLBACK_STORY).
    """
    segs = transcript.get("segments") or []
    windows, cur = [], None
    for s in segs:
        if cur is None:
            cur = {"start": s["start"], "end": s["end"], "text": s["text"]}
            continue
        if (s["end"] - cur["start"]) <= hi:
            cur["end"] = s["end"]
            cur["text"] += " " + s["text"]
        else:
            windows.append(cur)
            cur = {"start": s["start"], "end": s["end"], "text": s["text"]}
    if cur:
        windows.append(cur)

    out = []
    for w in windows:
        dur = w["end"] - w["start"]
        if dur < min(lo, 5.0):
            continue
        density = len(w["text"].split()) / max(dur, 0.01)
        out.append({
            "start": w["start"],
            "end": min(w["end"], w["start"] + hi),
            # ~3 words/sec is brisk speech; normalise around that into 0-1.
            "score": max(0.0, min(density / 3.0, 1.0)),
            "hook": " ".join(w["text"].split()[:12]),
            "rationale": "Fallback: selected by speech density (no LLM ranking).",
        })
    out.sort(key=lambda c: c["score"], reverse=True)
    return out[:limit]


def detect_candidates(cfg, transcript, progress=None):
    """Rank clip candidates with Groq, falling back to density windows."""
    ic = _icfg(cfg)
    lo = int(ic.get("min_clip_sec", 30))
    hi = int(ic.get("max_clip_sec", 90))
    limit = int(ic.get("max_candidates", 10))
    llm = cfg.get("llm", {})

    # autoshorts' rule: a short source can't yield 30s clips, so relax the floor.
    duration = float(transcript.get("duration") or 0.0)
    min_duration = max(duration * 0.5, 5.0) if duration < 60.0 else float(lo)

    if not llm.get("enabled") or not llm.get("api_key"):
        if progress:
            progress("rank", "LLM disabled/unconfigured — ranking by speech density.")
        return _fallback_candidates(transcript, lo, hi, limit)

    if progress:
        progress("rank", f"Finding viral moments with {llm.get('model')}...")

    try:
        resp = requests.post(
            f"{llm['base_url']}/chat/completions",
            headers={"Authorization": f"Bearer {llm['api_key']}"},
            json={
                "model": llm["model"],
                # Ranking wants determinism, unlike the creative default in generate.py.
                "temperature": 0.2,
                "messages": [
                    {"role": "system",
                     "content": VIRAL_MOMENT_SYSTEM.format(lo=lo, hi=hi, n=limit)},
                    {"role": "user",
                     "content": "Transcript:\n" + _compact_segments(transcript["segments"])},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout=120,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        cands = _parse_candidates(content, min_duration, limit)
        if not cands:
            raise ValueError("model returned no usable candidates")
    except Exception as e:  # noqa: BLE001
        if progress:
            progress("rank", f"LLM ranking failed ({e}) — ranking by speech density.")
        return _fallback_candidates(transcript, lo, hi, limit)

    # Clamp to the real media bounds; models do hallucinate timestamps.
    for c in cands:
        c["start"] = max(0.0, c["start"])
        if duration:
            c["end"] = min(c["end"], duration)
    cands = [c for c in cands if c["end"] - c["start"] >= 1.0]

    if progress:
        progress("rank", f"Found {len(cands)} clip candidate(s).")
    return cands


# --------------------------------------------------------------------------- #
# 4. Render  (autoshorts media.rs::render_flat_clip, adapted)
# --------------------------------------------------------------------------- #
def _clip_words(transcript, start, end):
    """Words overlapping [start,end), rebased so the clip starts at t=0."""
    out = []
    for w in transcript.get("words", []):
        if w["end"] > start and w["start"] < end:
            out.append({
                "text": w["text"],
                "start": max(0.0, w["start"] - start),
                "end": max(0.0, w["end"] - start),
            })
    return out


def render_clip(cfg, src_path, candidate, transcript, out_path, work_dir,
                burn_captions=True, progress=None):
    """Cut a 9:16 clip out of the source, optionally with burned-in captions."""
    w, h = cfg.get("resolution", [1080, 1920])
    fps = cfg.get("fps", 30)
    start = float(candidate["start"])
    dur = max(float(candidate["end"]) - start, 0.5)

    # autoshorts crops to the largest native-resolution 9:16 region and stops
    # there, so its output size follows the source.  Use compose()'s convention
    # instead so imported clips come out at exactly the configured resolution,
    # identical to generated ones.  The framing is the same centre crop.
    chain = [f"scale={w}:{h}:force_original_aspect_ratio=increase",
             f"crop={w}:{h}", "setsar=1", f"fps={fps}"]

    if burn_captions:
        words = _clip_words(transcript, start, float(candidate["end"]))
        if words:
            ass_name = "subs.ass"
            gen.write_ass(cfg, words, os.path.join(work_dir, ass_name))
            font_src = os.path.join(
                HERE, cfg.get("caption", {}).get("fonts_dir", "fonts"),
                "Anton-Regular.ttf")
            if os.path.exists(font_src):
                shutil.copy(font_src, work_dir)
            chain.append(f"subtitles={ass_name}:fontsdir=.")
        elif progress:
            progress("render", "No words in range — rendering without captions.")

    # -ss/-t BEFORE -i is input seeking: it skips straight to the cut instead of
    # decoding everything before it, which matters a lot on hour-long sources.
    # (autoshorts seeks after -i, which is accurate but decodes from 0.)  Input
    # seeking rebases PTS to 0, which is why _clip_words() shifts the timings.
    cmd = [video_mod.ffmpeg_exe(), "-y",
           "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", src_path,
           "-vf", ",".join(chain),
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart",
           out_path]

    if progress:
        progress("render", f"Cutting {start:.1f}s-{candidate['end']:.1f}s "
                           f"({dur:.1f}s) and cropping to {w}x{h}...")
    proc = subprocess.run(cmd, cwd=work_dir, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise RuntimeError(f"ffmpeg clip render failed:\n{tail}")
    return out_path


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def analyze_video(cfg, src_path, progress=None):
    """Step 1: probe -> extract audio -> transcribe -> rank candidates.

    Returns {'probe','transcript','candidates'}.  Rendering is a separate step so
    the UI can show the ranked list before any render time is spent.
    """
    if not os.path.exists(src_path):
        raise FileNotFoundError(f"Source video not found: {src_path}")

    probe = video_mod.probe_media(src_path)
    if not probe["has_video"]:
        raise RuntimeError("That file has no video stream.")
    if progress:
        mins = probe["duration"] / 60
        progress("probe", f"Source: {probe['width']}x{probe['height']}, "
                          f"{mins:.1f} min, {probe['video_codec']}")

    work_dir = os.path.join(HERE, f"_import_{int(time.time())}")
    os.makedirs(work_dir, exist_ok=True)
    try:
        audio_path = extract_audio(src_path, work_dir, progress)
        transcript = transcribe(cfg, audio_path, progress)
        if progress:
            progress("transcribe", f"Transcribed {len(transcript['words'])} words, "
                                   f"{len(transcript['segments'])} segments, "
                                   f"speakers: {', '.join(transcript['speakers']) or 'n/a'}")
        candidates = detect_candidates(cfg, transcript, progress)
        return {"probe": probe, "transcript": transcript, "candidates": candidates}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def render_candidate(cfg, src_path, transcript, candidate, category=None,
                     burn_captions=None, progress=None):
    """Step 2: render one candidate into output_dir, shaped for metadata.py.

    The returned dict matches generate_video()'s contract, so build_metadata()
    and upload_video() consume it unchanged.
    """
    ic = _icfg(cfg)
    category = category or ic.get("metadata_category", "reddit")
    if burn_captions is None:
        burn_captions = bool(ic.get("burn_captions", True))

    out_dir = cfg.get("output_dir", "../output")
    if not os.path.isabs(out_dir):
        out_dir = os.path.normpath(os.path.join(HERE, out_dir))
    os.makedirs(out_dir, exist_ok=True)

    hook = candidate.get("hook") or "Imported clip"
    title = hook if len(hook) <= 95 else hook[:92].rstrip() + "..."
    out_name = f"{category}_{gen._slug(title)}_{int(time.time())}.mp4"
    out_path = os.path.join(out_dir, out_name)

    work_dir = os.path.join(HERE, f"_clip_{int(time.time())}")
    os.makedirs(work_dir, exist_ok=True)
    try:
        render_clip(cfg, src_path, candidate, transcript, out_path, work_dir,
                    burn_captions=burn_captions, progress=progress)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    duration = video_mod.get_duration(out_path) or (candidate["end"] - candidate["start"])
    if progress:
        progress("done", f"Done: {out_name} ({duration:.1f}s)")

    words = _clip_words(transcript, float(candidate["start"]), float(candidate["end"]))
    return {
        "path": out_path,
        "title": title,
        "duration": duration,
        "category": category,
        "script": " ".join(w["text"] for w in words),
        "hook": hook,
        "source_url": None,
        "score": candidate.get("score"),
        "rationale": candidate.get("rationale"),
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python import_clip.py <video_path> [category] [--render-top N]")
        raise SystemExit(1)

    src = sys.argv[1]
    cat = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else None
    top = 0
    if "--render-top" in sys.argv:
        top = int(sys.argv[sys.argv.index("--render-top") + 1])

    _cfg = gen.load_config()
    _log = lambda s, m: print(f"[{s}] {m}")  # noqa: E731
    res = analyze_video(_cfg, src, progress=_log)

    print(f"\n{len(res['candidates'])} candidate(s):\n")
    for i, c in enumerate(res["candidates"], 1):
        print(f"{i:2d}. [{c['score']:.2f}] {c['start']:7.1f}s -> {c['end']:7.1f}s "
              f"({c['end'] - c['start']:5.1f}s)  {c['hook']}")
        if c.get("rationale"):
            print(f"     {c['rationale']}")

    for c in res["candidates"][:top]:
        out = render_candidate(_cfg, src, res["transcript"], c, category=cat,
                               progress=_log)
        print("Saved:", out["path"])
