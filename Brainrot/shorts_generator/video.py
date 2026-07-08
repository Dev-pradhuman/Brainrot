"""Small ffmpeg helpers shared by the generator and the Flask app."""
import re
import subprocess

import imageio_ffmpeg


def ffmpeg_exe():
    """Path to the ffmpeg binary bundled with imageio-ffmpeg (no system install needed)."""
    return imageio_ffmpeg.get_ffmpeg_exe()


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
