"""Small ffmpeg helpers shared by the generator and the Flask app."""
import re
import subprocess

import imageio_ffmpeg


def ffmpeg_exe():
    """Path to the ffmpeg binary bundled with imageio-ffmpeg (no system install needed)."""
    return imageio_ffmpeg.get_ffmpeg_exe()


def probe_media(path):
    """Return {duration, has_video, width, height, video_codec, audio_codec}.

    imageio-ffmpeg bundles ffmpeg but not ffprobe, so this parses ffmpeg's own
    stderr banner instead of shelling out to ffprobe.  Unknown fields come back
    as None rather than raising, so callers can fall back to defaults.
    """
    info = {"duration": 0.0, "has_video": False, "width": None, "height": None,
            "video_codec": None, "audio_codec": None}
    try:
        proc = subprocess.run(
            [ffmpeg_exe(), "-i", path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception:
        return info

    err = proc.stderr
    info["duration"] = get_duration(path)

    vm = re.search(r"Stream #\d+:\d+.*?: Video: (\w+).*?, (\d+)x(\d+)", err)
    if vm:
        info["has_video"] = True
        info["video_codec"] = vm.group(1)
        info["width"] = int(vm.group(2))
        info["height"] = int(vm.group(3))

    am = re.search(r"Stream #\d+:\d+.*?: Audio: (\w+)", err)
    if am:
        info["audio_codec"] = am.group(1)

    return info


def get_duration(path):
    """Return media duration in seconds by parsing ffmpeg's stderr. 0.0 if unknown."""
    try:
        proc = subprocess.run(
            [ffmpeg_exe(), "-i", path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", proc.stderr)
        if m:
            h, mnt, s = m.groups()
            return int(h) * 3600 + int(mnt) * 60 + float(s)
    except Exception:
        pass
    return 0.0
