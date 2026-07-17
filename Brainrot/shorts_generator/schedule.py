"""Pick publish times that land in the target country's Shorts prime time.

The problem this solves: peak Shorts engagement is the target audience's evening.
Targeting the US from IST means the good slots (4-9pm ET) fall at 1:30-6:30am IST,
which nobody is awake to upload into. So we upload whenever, and hand YouTube a
`publishAt` so it goes live at the right local moment for the viewer.

Slot data is from Buffer's March 2026 analysis of 1.8M Shorts:
  - Shorts peak in the EVENING (6-9pm), the opposite of long-form (8-11am).
  - Best days: Friday > Saturday > Thursday. Worst: Tuesday, Monday.
  - The top three slots of the entire week are all Friday: 4pm, 6pm, 7pm.
  - Weakest window is 12-5pm, except Friday afternoon which is the peak.
  https://buffer.com/resources/best-time-to-post-on-youtube/

CLI:  python schedule.py            # preview the next 10 slots
      python schedule.py 5 Asia/Kolkata
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Best publish hours per weekday, strongest first. Python weekday(): Mon=0..Sun=6.
BEST_HOURS = {
    0: [20, 17, 18],   # Monday
    1: [20, 21, 19],   # Tuesday
    2: [19, 20, 21],   # Wednesday
    3: [19, 20, 21],   # Thursday
    4: [16, 18, 19],   # Friday   <- the three best slots of the week
    5: [19, 11, 18],   # Saturday <- only day where a morning slot ranks
    6: [19, 20, 17],   # Sunday
}

# Weekdays ordered best -> worst for Shorts engagement.
DAY_RANK = [4, 5, 3, 6, 2, 0, 1]   # Fri, Sat, Thu, Sun, Wed, Mon, Tue

DEFAULT_TZ = "America/New_York"


def _scfg(cfg):
    return cfg.get("schedule", {}) or {}


def upcoming_slots(count=1, tz_name=DEFAULT_TZ, now=None, days_ahead=30,
                   min_lead_min=20, per_day=1, best_days_only=False):
    """Return `count` upcoming publish slots, chronologically.

    Walks forward one day at a time taking that day's best `per_day` hour(s),
    rather than globally ranking every slot. Ranking globally looks smarter but
    stacks a whole batch onto Fridays and leaves week-long gaps — and steady daily
    posting into each day's own peak beats a Friday pile-up, both because the
    algorithm rewards consistency and because your own Shorts stop competing with
    each other in the same feed window.

    `best_days_only` restricts to Fri/Sat/Thu for low-volume, quality-first runs.
    `min_lead_min` keeps a buffer so a slot can't fall into the past mid-upload.
    """
    tz = ZoneInfo(tz_name)
    now = now.astimezone(tz) if now else datetime.now(tz)
    earliest = now + timedelta(minutes=min_lead_min)
    strong_days = {4, 5, 3}   # Fri, Sat, Thu

    out = []
    for d in range(days_ahead + 1):
        if len(out) >= count:
            break
        day = now + timedelta(days=d)
        wd = day.weekday()
        if best_days_only and wd not in strong_days:
            continue
        taken = 0
        for hour in BEST_HOURS[wd]:
            if taken >= per_day or len(out) >= count:
                break
            dt = day.replace(hour=hour, minute=0, second=0, microsecond=0)
            if dt <= earliest:
                continue
            out.append(dt)
            taken += 1
    # BEST_HOURS is rank-ordered, so a per_day>1 day can emit 8pm before 5pm.
    # Callers map slots[i] to video i, so hand them back in real time order.
    out.sort()
    return out


def next_slot(tz_name=DEFAULT_TZ, now=None):
    """The single best upcoming publish slot."""
    return upcoming_slots(1, tz_name, now)[0]


def slots_for(cfg, count=1, now=None):
    """Config-driven slots. Returns [] when scheduling is off, so callers can
    treat 'no slots' as 'publish immediately'."""
    s = _scfg(cfg)
    if not s.get("enabled"):
        return []
    return upcoming_slots(
        count,
        s.get("timezone", DEFAULT_TZ),
        now=now,
        per_day=int(s.get("per_day", 1)),
        best_days_only=bool(s.get("best_days_only", False)),
    )


def describe(dt, viewer_tz=None):
    """Human-readable slot, optionally alongside the uploader's own clock."""
    out = dt.strftime("%a %d %b, %I:%M %p %Z")
    if viewer_tz:
        local = dt.astimezone(ZoneInfo(viewer_tz))
        out += f"  ({local.strftime('%I:%M %p %Z, %a')} your time)"
    return out


def to_iso8601(dt):
    """YouTube wants publishAt as ISO 8601 UTC."""
    if dt.tzinfo is None:
        raise ValueError("publish_at must be timezone-aware")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    import sys

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    mine = sys.argv[2] if len(sys.argv) > 2 else "Asia/Kolkata"

    print(f"Next {n} best US Shorts publish slots (Buffer 1.8M-Short data):\n")
    for dt in upcoming_slots(n):
        print("  " + describe(dt, mine))
    print("\nAll times are the US viewer's local evening — that is the point.")
