"""Time-range dedup between Strava and Fitbit workout logs.

When a user has both Strava and Fitbit connected, an activity captured
by one often gets auto-detected by the other - the same run shows up
twice in `workout_logs` with different source values. Summing
`calories_burned` across both then double-counts the session in every
downstream calc (eat-back adjustment, dashboard "cal burned" total,
activity-trend chart).

Both rows are kept in the DB so disconnecting Strava later doesn't
orphan history; dedup happens at read time. Strava wins because it
carries richer data (GPS, power, segments).
"""

from datetime import datetime, timedelta


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def dedup_fitbit_vs_strava(workouts: list[dict]) -> list[dict]:
    """Drop Fitbit entries whose time range overlaps a Strava entry.

    Overlap = `[start, start+duration_sec]` intervals intersect.
    Only Fitbit rows are candidates for removal; manual and Strava
    entries always pass through.
    """
    strava_ranges: list[tuple[datetime, datetime]] = []
    for w in workouts:
        if w.get("source") != "strava":
            continue
        start = _parse_iso(w.get("started_at") or "")
        if not start:
            continue
        end = start + timedelta(seconds=int(w.get("duration_sec") or 0))
        strava_ranges.append((start, end))

    if not strava_ranges:
        return workouts

    def overlaps_strava(w: dict) -> bool:
        if w.get("source") != "fitbit":
            return False
        start = _parse_iso(w.get("started_at") or "")
        if not start:
            return False
        end = start + timedelta(seconds=int(w.get("duration_sec") or 0))
        for s_start, s_end in strava_ranges:
            if start < s_end and end > s_start:
                return True
        return False

    return [w for w in workouts if not overlaps_strava(w)]
