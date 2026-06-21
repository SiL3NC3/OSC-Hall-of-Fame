#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import hashlib
from collections import Counter, defaultdict
from pathlib import Path
from datetime import datetime

import pandas as pd
import yaml

version = '0.23'

def esc(x) -> str:
    if pd.isna(x):
        return ""
    return html.escape(str(x))


def as_int(x, default=0):
    try:
        if pd.isna(x):
            return default
        return int(float(x))
    except Exception:
        return default


def as_float(x, default=0.0):
    try:
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def safe_text(x):
    if pd.isna(x):
        return ""
    s = str(x).strip()
    return "" if s.lower() == "nan" else s


def format_duration_months(months: int) -> str:
    months = int(months or 0)
    if months <= 0:
        return "–"
    years, rem = divmod(months, 12)
    parts = []
    if years:
        parts.append(f"{years} year" + ("s" if years != 1 else ""))
    if rem:
        parts.append(f"{rem} month" + ("s" if rem != 1 else ""))
    if not parts:
        parts.append("1 month")
    return ", ".join(parts)


def rank_sort_value(x):
    t = str(x).strip().lower()
    if t in {"", "nan", "none", "dq"}:
        return 999999
    try:
        return int(float(t))
    except Exception:
        return 999999


def pct(x):
    return f"{as_float(x)*100:.1f}%"


def split_placements(s: str):
    if not isinstance(s, str) or not s.strip():
        return []
    return [p.strip() for p in s.split(" | ") if p.strip()]


def medal_badges(row):
    parts=[]
    for key, cls, label in [("gold","gold","🥇"),("silver","silver","🥈"),("bronze","bronze","🥉"),("fourth","fourth","4"),("fifth","fifth","5")]:
        v=as_int(row.get(key,0))
        if v:
            parts.append(f'<span class="badge {cls}">{label} {v}</span>')
    return " ".join(parts) or '<span class="muted">–</span>'


def osc_from_filename(path: Path) -> int:
    m = re.search(r"OSC\s*0*(\d+)", path.name, re.I)
    return int(m.group(1)) if m else 0


def synth_from_filename(path: Path) -> str:
    m = re.search(r"OSC\s*0*\d+_([^_]+(?:-[^_]+)*)", path.stem, re.I)
    return (m.group(1).replace("-", " ") if m else "").strip()


def normalize_artist_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_") or "unknown"


def split_title_artist(label: str):
    label = re.sub(r"\s+", " ", (label or "").strip(" -\t"))
    m = re.match(r"^(.*?)\s*\(([^()]+)\)\s*$", label)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    if " - " in label:
        a, b = label.rsplit(" - ", 1)
        return a.strip(), b.strip()
    if "-" in label and label.count("-") == 1:
        a, b = label.rsplit("-", 1)
        return a.strip(), b.strip()
    return label, ""


def parse_forum_poll_scorecard(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = [ln.rstrip() for ln in text.splitlines()]
    rows = []
    total_votes = 0
    poll_ended = ""
    m = re.search(r"Poll ended at\s+(.+)", text, re.I)
    if m:
        poll_ended = m.group(1).strip()
    m = re.search(r"Total votes\s*:\s*(\d+)", text, re.I)
    if m:
        total_votes = int(m.group(1))
    i = 0
    while i < len(lines) - 2:
        label = lines[i].strip()
        if not label or label.lower().startswith(("poll ended", "who/what", "best ", "choose ", "do you")):
            i += 1; continue
        if re.fullmatch(r"\d+", lines[i+1].strip()):
            votes = int(lines[i+1].strip())
            pct_txt = lines[i+2].strip()
            pct_val = None if "no votes" in pct_txt.lower() else as_float(pct_txt.replace("%", ""), None)
            track, artist = split_title_artist(label)
            rows.append({"label": label, "track": track, "artist": artist, "votes": votes, "percent": pct_val})
            i += 3
        else:
            i += 1
    rows.sort(key=lambda r: (-r["votes"], r["label"].lower()))
    for n, r in enumerate(rows, 1):
        r["rank"] = n
    return {"osc": osc_from_filename(path), "synth": synth_from_filename(path), "variant": "forum_poll", "total_voters": total_votes, "total_points": total_votes, "poll_ended": poll_ended, "entries": len(rows), "rows": rows}


def parse_table_scorecard(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    # Join wrapped score continuations, e.g. OSC015 has a second points line before the total.
    raw_lines = [ln.rstrip() for ln in text.splitlines()]
    joined = []
    for ln in raw_lines:
        if joined and not re.match(r"^\s*\d+\s*[=.()= ]", ln) and re.match(r"^\s*[\d, ]+\s*(?:=\s*\d+)?\s*$", ln):
            joined[-1] += " " + ln.strip()
        else:
            joined.append(ln)
    rows = []
    rank_pat = re.compile(r"^\s*(\d+)\s*[=.]*\s*\([^)]*\)\s*(.*?)\s*$")
    for ln in joined:
        m = rank_pat.match(ln)
        if not m:
            continue
        rank = int(m.group(1))
        tail = m.group(2).strip()
        pts = None
        score_blob = ""
        rest = tail
        if "..." in tail:
            rest, score_blob = re.split(r"\s*\.{2,}\s*", tail, maxsplit=1)
        elif " - " in tail and re.search(r"\d", tail.rsplit(" - ", 1)[-1]):
            rest, score_blob = tail.rsplit(" - ", 1)
        mpts = re.search(r"=\s*(\d+)\s*$", score_blob)
        if mpts:
            pts = int(mpts.group(1))
        track, artist = split_title_artist(rest)
        if pts is None:
            nums = [int(x) for x in re.findall(r"\b\d+\b", score_blob)]
            pts = sum(nums) if nums else 0
        rows.append({"rank": rank, "label": rest.strip(), "track": track, "artist": artist, "votes": pts, "points": pts, "scores": score_blob.strip()})
    total_voters = 0; total_points = 0
    m = re.search(r"(?:total\s+)?voters?\s*[:=,. ]+\s*(\d+)|(\d+)\s+voters", text, re.I)
    if not m:
        m = re.search(r"total\s+votes\D+(\d+)", text, re.I)  # OSC008 wording means voter count.
    if m:
        total_voters = int(next(x for x in m.groups() if x))
    m = re.search(r"total\s+points\D+(\d+)|total\s+votes\s*[:=]\s*(\d+)|(\d+)\s+votes\s*\)|votes\D+(\d+)\s*\)", text, re.I)
    if m:
        total_points = int(next(x for x in m.groups() if x))
    return {"osc": osc_from_filename(path), "synth": synth_from_filename(path), "variant": "score_table", "total_voters": total_voters, "total_points": total_points, "poll_ended": "", "entries": len(rows), "rows": rows}


def parse_scorecard_file(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    if re.search(r"Total votes\s*:", text, re.I) and not re.search(r"^\s*1\s*[=.]*\s*\(", text, re.M):
        return parse_forum_poll_scorecard(path)
    return parse_table_scorecard(path)


def normalize_results_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize an arbitrary all-results CSV to the columns used by the HTML."""
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    # common aliases from previous audit/export scripts
    aliases = {
        "place": "rank", "placement": "rank", "position": "rank", "pos": "rank",
        "score": "points", "votes": "points", "total": "points",
        "song": "track", "title": "track", "track_title": "track",
        "artist_name": "artist", "entrant": "artist", "user": "artist",
        "challenge": "osc", "osc_number": "osc", "number": "osc",
        "version": "template_version", "template": "template_version",
    }
    lower_map = {c.lower().strip(): c for c in out.columns}
    for src, dst in aliases.items():
        if dst not in out.columns and src in lower_map:
            out = out.rename(columns={lower_map[src]: dst})
    required = ["osc", "rank", "artist", "track"]
    if not all(c in out.columns for c in required):
        return pd.DataFrame()
    out["rank"] = out["rank"].apply(lambda x: str(x).strip())
    if "synth" not in out.columns:
        out["synth"] = ""
    if "year" not in out.columns:
        out["year"] = ""
    if "points" not in out.columns:
        out["points"] = 0
    if "template_version" not in out.columns:
        out["template_version"] = ""
    if "url" not in out.columns:
        out["url"] = ""
    if "archive_url" not in out.columns:
        out["archive_url"] = ""
    if "archive_title" not in out.columns:
        out["archive_title"] = ""
    if "archive_identifier" not in out.columns:
        out["archive_identifier"] = ""
    if "archive_file" not in out.columns:
        out["archive_file"] = ""
    if "archive_match_kind" not in out.columns:
        out["archive_match_kind"] = ""
    if "archive_link_state" not in out.columns:
        out["archive_link_state"] = ""
    if "artist_key" not in out.columns:
        out["artist_key"] = out["artist"].apply(normalize_artist_key)
    for col in ["url", "archive_url", "archive_title", "archive_identifier", "archive_file", "archive_match_kind", "archive_link_state"]:
        out[col] = out[col].fillna("").astype(str).map(safe_text)
    out["osc"] = out["osc"].apply(as_int)
    out["rank_sort"] = out["rank"].apply(rank_sort_value)
    out["points"] = out["points"].apply(as_float)
    return out[["osc","year","synth","rank","rank_sort","artist","artist_key","track","points","template_version","url","archive_url","archive_title","archive_identifier","archive_file","archive_match_kind","archive_link_state"]].copy()


def load_all_results_csv(input_dir: Path) -> tuple[pd.DataFrame, str]:
    """Load the single source of truth for the HOF build."""
    path = input_dir / "normalized_results.csv"
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}. Run the extractor first.")
    df = normalize_results_frame(pd.read_csv(path))
    if df.empty:
        raise RuntimeError(f"{path} did not contain any normalized result rows.")
    return df, path.name


def load_scorecard_inventory(input_dir: Path) -> tuple[int, int]:
    """Return the number of scorecard files and distinct OSCs present in the extractor inventory."""
    path = input_dir / "scorecard_file_inventory.csv"
    if not path.exists():
        return 0, 0
    try:
        df = pd.read_csv(path)
    except Exception:
        return 0, 0
    if df.empty or "osc" not in df.columns:
        return 0, 0
    df = df.copy()
    df["osc"] = df["osc"].apply(as_int)
    return int(len(df)), int(df["osc"].nunique())


def load_challenge_metadata(challenges: Path | None) -> pd.DataFrame:
    """Load OSC metadata used to enrich the normalized results."""
    if not challenges:
        return pd.DataFrame()
    path = Path(challenges)
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if df.empty or "osc" not in df.columns:
        return pd.DataFrame()
    df = df.copy()
    df["osc"] = df["osc"].apply(as_int)
    if "year" not in df.columns:
        df["year"] = ""
    if "synth" not in df.columns:
        df["synth"] = ""
    if "url" not in df.columns:
        df["url"] = ""
    df["year"] = df["year"].fillna("").astype(str).str.strip()
    df["synth"] = df["synth"].fillna("").astype(str).str.strip()
    df["url"] = df["url"].fillna("").astype(str).str.strip()
    df = df[df["osc"] > 0].copy()
    return df[["osc", "year", "synth", "url"]].drop_duplicates("osc")


def build_results_dataset(input_dir: Path, challenges: pd.DataFrame|None=None):
    """Build the table data for ALL OSC Results.

    The HOF uses a single source of truth: normalized_results.csv.
    """
    full, source = load_all_results_csv(input_dir)
    scorecard_count, inventory_osc_count = load_scorecard_inventory(input_dir)
    full = normalize_results_frame(full)
    full["osc"] = full["osc"].apply(as_int)
    full["rank_sort"] = full["rank"].apply(rank_sort_value)
    challenge_meta = load_challenge_metadata(challenges)
    if not challenge_meta.empty:
        full = full.merge(challenge_meta, on="osc", how="left", suffixes=("", "_challenge"))
        if "year_challenge" in full.columns:
            full["year"] = full["year"].fillna("").astype(str).str.strip()
            full["year_challenge"] = full["year_challenge"].fillna("").astype(str).str.strip()
            full["year"] = full["year"].where(full["year"].str.fullmatch(r"\d+"), full["year_challenge"])
            full = full.drop(columns=["year_challenge"], errors="ignore")
        if "synth_challenge" in full.columns:
            full["synth"] = full["synth"].fillna("").astype(str).str.strip()
            full["synth_challenge"] = full["synth_challenge"].fillna("").astype(str).str.strip()
            full["synth"] = full["synth"].where(full["synth"].astype(str).str.strip() != "", full["synth_challenge"])
            full = full.drop(columns=["synth_challenge"], errors="ignore")
        if "url_challenge" in full.columns:
            full["url"] = full["url"].fillna("").astype(str).str.strip()
            full["url_challenge"] = full["url_challenge"].fillna("").astype(str).str.strip()
            full["url"] = full["url"].where(full["url"].astype(str).str.strip() != "", full["url_challenge"])
            full = full.drop(columns=["url_challenge"], errors="ignore")
    full = full.sort_values(["osc", "rank_sort", "points"], ascending=[False, True, False]).reset_index(drop=True)
    return full, source, True, scorecard_count, inventory_osc_count

def load_inputs(input_dir: Path, challenges: Path|None):
    result_details, result_source, has_full_results, scorecard_count, inventory_osc_count = build_results_dataset(input_dir, challenges)
    return result_details, result_source, has_full_results, scorecard_count, inventory_osc_count


def summarize_results(result_details: pd.DataFrame):
    """Derive HOF overview rows and top-5 details from the single results source."""
    if result_details is None or result_details.empty:
        return pd.DataFrame(), pd.DataFrame()

    df = result_details.copy()
    if "artist_key" not in df.columns:
        df["artist_key"] = df["artist"].apply(normalize_artist_key)
    df["artist_key"] = df["artist_key"].fillna("").astype(str).str.strip().replace("", "(unknown)")
    df["artist"] = df["artist"].fillna("").astype(str).map(lambda x: x.strip())
    df["osc"] = df["osc"].apply(as_int)
    df["rank"] = df["rank"].apply(as_int)
    df["rank_sort"] = df["rank"].apply(rank_sort_value)
    df["points"] = df["points"].apply(as_float)
    if "year" not in df.columns:
        df["year"] = ""
    if "url" not in df.columns:
        df["url"] = ""
    if "synth" not in df.columns:
        df["synth"] = ""
    if "template_version" not in df.columns:
        df["template_version"] = ""

    top5 = df[(df["rank_sort"] >= 1) & (df["rank_sort"] <= 5)].copy()
    top5 = top5.sort_values(["osc", "rank_sort", "points"], ascending=[False, True, False]).reset_index(drop=True)

    overall_rows = []
    for key, grp in top5.groupby("artist_key", dropna=False):
        grp = grp.copy()
        all_grp = df[df["artist_key"] == key].copy()
        valid = grp
        top5_grp = valid
        podium_grp = valid[valid["rank_sort"] <= 3]
        gold = int((valid["rank_sort"] == 1).sum())
        silver = int((valid["rank_sort"] == 2).sum())
        bronze = int((valid["rank_sort"] == 3).sum())
        fourth = int((valid["rank_sort"] == 4).sum())
        fifth = int((valid["rank_sort"] == 5).sum())
        participations = int(all_grp["osc"].nunique())
        top5_osc_count = int(top5_grp["osc"].nunique())
        top5_rate = round(top5_osc_count / participations, 4) if participations else 0
        avg_top5_rank = round(float(top5_grp["rank_sort"].mean()), 3) if len(top5_grp) else ""
        first_osc = int(all_grp["osc"].min()) if len(all_grp) else 0
        last_osc = int(all_grp["osc"].max()) if len(all_grp) else 0
        total_points_top5 = round(float(top5_grp["points"].sum()), 3)
        artist_name_candidates = [a for a in all_grp["artist"].astype(str).tolist() if a and a.lower() not in {"(unknown)", "nan"}]
        artist_name = Counter(artist_name_candidates).most_common(1)[0][0] if artist_name_candidates else str(key)
        placements = []
        for _, r in top5_grp.iterrows():
            osc = as_int(r.get("osc"))
            rk = as_int(r.get("rank"))
            synth = str(r.get("synth", "")).strip()
            track = str(r.get("track", "")).strip()
            pts = r.get("points")
            placements.append(f"OSC{osc} #{rk} {synth}: {track}" + (f" ({pts})" if pts != "" else ""))
        overall_rows.append({
            "artist_key": key,
            "artist": artist_name,
            "gold": gold,
            "silver": silver,
            "bronze": bronze,
            "fourth": fourth,
            "fifth": fifth,
            "top5_total": int(len(top5_grp)),
            "podium_total": int(len(podium_grp)),
            "participations": participations,
            "top5_osc_count": top5_osc_count,
            "top5_rate": top5_rate,
            "avg_top5_rank": avg_top5_rank,
            "first_osc": first_osc,
            "last_osc": last_osc,
            "range_first_osc": first_osc,
            "range_last_osc": last_osc,
            "total_points_top5": total_points_top5,
            "placements": " | ".join(placements),
        })

    overall = pd.DataFrame(overall_rows)
    if not overall.empty:
        overall = overall.sort_values(
            ["gold", "silver", "bronze", "fourth", "fifth", "last_osc", "artist"],
            ascending=[False, False, False, False, False, False, True],
        ).reset_index(drop=True)
        overall["hof_rank"] = range(1, len(overall) + 1)
    return overall, top5


def js_str(x):
    return json.dumps(str(x or ""), ensure_ascii=False)


def load_hof_config(path: Path) -> dict:
    if not path or not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Failed to read HOF config {path}: {exc}") from exc
    return data or {}


def write_protection_files(output_dir: Path, protection_cfg: dict) -> None:
    if not protection_cfg or not protection_cfg.get("activated"):
        return
    username = str(protection_cfg.get("username", "")).strip()
    password = str(protection_cfg.get("password", "")).strip()
    if not username or not password:
        raise RuntimeError("protection.activated is true but username/password are missing in the config.")

    output_dir.mkdir(parents=True, exist_ok=True)
    passwd_path = output_dir / ".passwd"
    htaccess_path = output_dir / ".htaccess"
    password_hash = base64.b64encode(hashlib.sha1(password.encode("utf-8")).digest()).decode("ascii")
    passwd_path.write_text(f"{username}:{{SHA}}{password_hash}\n", encoding="utf-8")
    htaccess_path.write_text(
        "\n".join([
            "AuthType Basic",
            'AuthName "KVR OSC Hall of Fame"',
            f"AuthUserFile {passwd_path.resolve()}",
            "Require valid-user",
            "",
        ]),
        encoding="utf-8",
    )


def build_html(result_details, result_source="", has_full_results=False, scorecard_count=0, inventory_osc_count=0, title="KVR OSC Hall of Fame", debug_template_columns=False):
    """v0.13: fixed-frame UI, no sidebar overlap, two working tables side by side."""
    result_details = result_details.copy()
    if "artist_key" not in result_details.columns:
        result_details["artist_key"] = result_details.get("artist", "").apply(normalize_artist_key)
    if "year" not in result_details.columns:
        result_details["year"] = ""
    if "url" not in result_details.columns:
        result_details["url"] = ""
    if "synth" not in result_details.columns:
        result_details["synth"] = ""
    if "template_version" not in result_details.columns:
        result_details["template_version"] = ""
    result_details["artist_key"] = result_details["artist_key"].fillna("").astype(str).str.strip().replace("", "(unknown)")
    result_details["artist"] = result_details["artist"].fillna("").astype(str).map(lambda x: x.strip())
    result_details["osc"] = result_details["osc"].apply(as_int)
    result_details["rank_sort"] = result_details["rank"].apply(rank_sort_value)
    result_details["points"] = result_details["points"].apply(as_float)
    result_details = result_details.sort_values(["osc", "rank_sort", "points"], ascending=[False, True, False]).copy()
    overall, details = summarize_results(result_details)

    result_oscs = result_details["osc"].nunique()
    osc_min, osc_max = as_int(result_details["osc"].min()), as_int(result_details["osc"].max())
    year_series = result_details["year"].dropna().astype(str).str.strip()
    year_numeric = year_series[year_series.str.fullmatch(r"\d+")]
    year_min = as_int(year_numeric.min()) if len(year_numeric) else ""
    year_max = as_int(year_numeric.max()) if len(year_numeric) else ""
    total_tracks = len(result_details)
    top5_count = len(details)
    contributor_keys = result_details["artist_key"].fillna("").astype(str).str.strip()
    contributor_keys = contributor_keys[~contributor_keys.isin(["", "(unknown)", "nan", "None"])]
    total_contributors = int(contributor_keys.nunique())
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    url_by_osc = {}
    if "url" in result_details.columns:
        for _, rr in result_details[["osc", "url"]].dropna().drop_duplicates("osc").iterrows():
            u = str(rr.get("url", "")).strip()
            if u:
                url_by_osc[as_int(rr.get("osc"))] = u

    def osc_link(osc):
        osc = as_int(osc)
        label = f"OSC{osc:03d}"
        url = url_by_osc.get(osc)
        if url:
            return f'<a href="{esc(url)}" target="_blank" rel="noopener">{label}</a>'
        return label

    artist_rows = defaultdict(list)
    artist_years = defaultdict(set)
    for _, r in result_details.iterrows():
        key = str(r.get("artist_key", "")).strip() or normalize_artist_key(r.get("artist", ""))
        raw_rank = str(r.get("rank", "")).strip()
        rank_sort = rank_sort_value(raw_rank)
        is_dq = raw_rank.lower() == "dq"
        osc = as_int(r.get("osc"))
        year_raw = str(r.get("year", "")).strip()
        year_val = as_int(year_raw, 0)
        if year_val:
            artist_years[key].add(year_val)
        artist_rows[key].append({
            "osc": osc,
            "year": year_val if year_val else "",
            "synth": str(r.get("synth", "")).strip(),
            "rank_raw": raw_rank,
            "rank_sort": rank_sort,
            "is_dq": is_dq,
            "artist": str(r.get("artist", "")).strip() or key,
            "track": str(r.get("track", "")).strip(),
            "points": as_float(r.get("points")),
            "template_version": str(r.get("template_version", "")).strip(),
            "source_file": str(r.get("source_file", "")).strip(),
            "url": str(r.get("url", "")).strip(),
        })

    artist_meta = {}
    for _, r in overall.iterrows():
        key = str(r.get("artist_key", "")).strip()
        rows = sorted(artist_rows.get(key, []), key=lambda x: (x["osc"], x["rank_sort"], x["track"].casefold()))
        valid_rows = [row for row in rows if not row["is_dq"]]
        years = sorted(artist_years.get(key, set()))
        first_year = years[0] if years else ""
        last_year = years[-1] if years else ""
        year_span = f"{first_year}–{last_year}" if first_year and last_year else (str(first_year) if first_year else "–")
        active_years = len(years)
        duration_months = max(0, as_int(r.get("range_last_osc")) - as_int(r.get("range_first_osc")) + 1)
        total_participations = as_int(r.get("participations"))
        top3_rows = sum(1 for row in valid_rows if row["rank_sort"] <= 3)
        top5_rows = sum(1 for row in valid_rows if row["rank_sort"] <= 5)
        podium_rows = sum(1 for row in valid_rows if row["rank_sort"] <= 3)
        wins = sum(1 for row in valid_rows if row["rank_sort"] == 1)
        artist_name = str(r.get("artist", "")).strip() or key
        osc_range = f"OSC{as_int(r.get('range_first_osc')):03d}–OSC{as_int(r.get('range_last_osc')):03d}"
        bio_parts = [
            f"Active since {osc_range}",
            f"{total_participations} entries",
            f"{top5_rows} Top5",
            f"{podium_rows} podiums",
            f"{wins} wins",
        ]
        if year_span != "–":
            bio_parts.append(f"Years {year_span}")
        bio_parts.append(f"Span {format_duration_months(duration_months)}")
        bio_short = " · ".join([
            osc_range,
            f"{total_participations} entries",
            f"{top5_rows} Top5",
            f"Span {format_duration_months(duration_months)}",
        ])
        artist_meta[key] = {
            "artist": artist_name,
            "key": key,
            "osc_first": as_int(r.get("range_first_osc")),
            "osc_last": as_int(r.get("range_last_osc")),
            "osc_range": osc_range,
            "participations": total_participations,
            "top3": top3_rows,
            "top5": top5_rows,
            "podium": podium_rows,
            "wins": wins,
            "first_year": first_year,
            "last_year": last_year,
            "year_span": year_span,
            "active_years": active_years,
            "duration_text": format_duration_months(duration_months),
            "avg_top5_rank": r.get("avg_top5_rank", ""),
            "top5_rate": r.get("top5_rate", ""),
            "bio": " · ".join(bio_parts),
            "bio_short": bio_short,
        }
    artist_meta_json = json.dumps(artist_meta, ensure_ascii=False).replace("</", "<\\/")

    def artist_button(key, label, extra_class="artist-link"):
        key = str(key or "").strip()
        return f'<button type="button" class="{extra_class}" onclick="openArtistDetails(\'{esc(key)}\')">{esc(label)}</button>'

    leader_items = []
    for i, (_, r) in enumerate(overall.head(3).iterrows(), start=1):
        key = str(r.get("artist_key", ""))
        trophy = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"#{i}")
        medal_breakdown = " · ".join([
            f"🥇 {as_int(r.get('gold'))}",
            f"🥈 {as_int(r.get('silver'))}",
            f"🥉 {as_int(r.get('bronze'))}",
        ])
        leader_items.append(f'''
        <button type="button" class="leader leader-{i}" onclick="openArtistDetails('{esc(key)}')" title="{as_int(r.get('top5_total'))} Top5 · {as_int(r.get('podium_total'))} podiums">
          <span class="leader-rank">{trophy} {i}</span>
          <span class="leader-name">{esc(r.get('artist'))}</span>
          <span class="leader-count">{esc(medal_breakdown)}</span>
        </button>''')

    stat_items = [
        (f"{osc_min:03d}–{osc_max:03d}", "OSC range"),
        (f"{year_min}–{year_max}" if year_min and year_max else "–", "Years"),
        (str(scorecard_count or inventory_osc_count or result_oscs), "Scorecards"),
        (str(result_oscs), "OSCs with results"),
        (str(total_contributors), "Total contributors"),
        (str(total_tracks), "Tracks"),
    ]
    stats_html = "".join(f'<div class="stat"><b>{esc(a)}</b><small>{esc(b)}</small></div>' for a, b in stat_items)

    overall_rows = []
    for idx, (_, r) in enumerate(overall.iterrows()):
        key = str(r.get("artist_key", ""))
        placements = split_placements(str(r.get("placements", "")))
        overall_rows.append(f'''
        <tr data-orig="{idx}" data-artist="{esc(key)} {esc(r.get('artist', '')).lower()}">
          <td class="rank">{as_int(r.get('hof_rank'))}</td>
          <td class="artist-name">{artist_button(key, r.get('artist'))}</td>
          <td class="num gold-t">{as_int(r.get('gold'))}</td>
          <td class="num silver-t">{as_int(r.get('silver'))}</td>
          <td class="num bronze-t">{as_int(r.get('bronze'))}</td>
          <td class="num">{as_int(r.get('fourth'))}</td>
          <td class="num">{as_int(r.get('fifth'))}</td>
          <td class="num strong">{as_int(r.get('top5_total'))}</td>
          <td class="num">{as_int(r.get('podium_total'))}</td>
          <td class="num">{as_int(r.get('participations'))}</td>
          <td class="num">{pct(r.get('top5_rate'))}</td>
          <td class="num">{as_int(r.get('range_first_osc'))}–{as_int(r.get('range_last_osc'))}</td>
          <td class="details-cell"><button class="details-button" onclick="openArtistDetails('{esc(key)}')">▸ {len(placements)}</button></td>
        </tr>''')

    result_rows = []
    for idx, (_, r) in enumerate(result_details.iterrows()):
        raw_rank = str(r.get("rank", "")).strip()
        is_dq = raw_rank.lower() == "dq"
        rank = as_int(raw_rank, 999999)
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, "") if not is_dq else ""
        place_text = "DQ" if is_dq else (f"{medal} {rank}" if medal else str(rank))
        osc = as_int(r.get("osc"))
        year = as_int(r.get("year"), "") if "year" in r else ""
        key = str(r.get("artist_key", "")).strip() or "(unknown)"
        artist_name = str(r.get("artist", "")).strip() or "(unknown)"
        track = str(r.get("track", "")).strip()
        synth = str(r.get("synth", "")).strip()
        template = str(r.get("template_version", "")).strip()
        url = safe_text(r.get("url", ""))
        archive_url = safe_text(r.get("archive_url", ""))
        archive_title = safe_text(r.get("archive_title", ""))
        archive_identifier = safe_text(r.get("archive_identifier", ""))
        archive_file = safe_text(r.get("archive_file", ""))
        archive_match_kind = safe_text(r.get("archive_match_kind", ""))
        archive_link_state = safe_text(r.get("archive_link_state", ""))
        result_rows.append(f'''
        <tr data-orig="{idx}" data-artist-key="{esc(key)}" data-artist="{esc(key)} {esc(artist_name).lower()}" data-artist-name="{esc(artist_name)}" data-osc="{osc}" data-year="{esc(year)}" data-rank="{rank if not is_dq else 999999}" data-rank-raw="{esc(raw_rank)}" data-dq="{1 if is_dq else 0}" data-synth="{esc(synth)}" data-track="{esc(track)}" data-points="{as_float(r.get('points')):.0f}" data-template="{esc(template)}" data-url="{esc(url)}" data-archive-url="{esc(archive_url)}" data-archive-title="{esc(archive_title)}" data-archive-file="{esc(archive_file)}" data-archive-identifier="{esc(archive_identifier)}" data-archive-match-kind="{esc(archive_match_kind)}" data-archive-link-state="{esc(archive_link_state)}">
          <td class="num">{osc_link(osc)}</td>
          <td class="num">{year}</td>
          <td>{esc(r.get('synth'))}</td>
          <td class="num rankmark r{rank}">{place_text}</td>
          <td>{artist_button(key, artist_name)}</td>
        <td>{f'<a href="{esc(url)}" target="_blank" rel="noopener">{esc(track)}</a>' if url else esc(track)}</td>
          <td class="num">{as_float(r.get('points')):.0f}</td>
          {f'<td>{esc(r.get("template_version"))}</td>' if debug_template_columns else ''}
        </tr>''')

    osc_options = ['<option value="">All OSCs</option>']
    for osc in sorted(result_details["osc"].dropna().apply(as_int).unique(), reverse=True):
        sub = result_details[result_details["osc"].apply(as_int) == osc]
        synth = str(sub.iloc[0].get("synth", "")).strip() if not sub.empty else ""
        label = f"OSC{osc:03d} - {synth}" if synth else f"OSC{osc:03d}"
        osc_options.append(f'<option value="{osc}">{esc(label)}</option>')

    if debug_template_columns:
        template_versions = [str(v).strip() for v in result_details["template_version"].fillna("").astype(str).unique() if str(v).strip()]
        template_versions = sorted(set(template_versions), key=lambda v: (0 if re.match(r'^[TV](\d+)$', v, re.I) else 1, int(re.match(r'^[TV](\d+)$', v, re.I).group(1)) if re.match(r'^[TV](\d+)$', v, re.I) else v))
        template_options = ['<option value="">All</option>'] + [f'<option value="{esc(v)}">{esc(v)}</option>' for v in template_versions]
        template_filter_html = f'<select id="templateFilter">{"".join(template_options)}</select>'
        template_head_html = '<th>Template</th>'
    else:
        template_filter_html = ''
        template_head_html = ''

    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<style>
:root{{--bg:#0b1020;--panel:#121a2d;--panel2:#17223a;--line:#263553;--text:#e8eefc;--muted:#94a3bd;--cyan:#72e7ff;--gold:#ffd166;--silver:#d9e2ef;--bronze:#d58b52;--head:66px;--top:78px;--foot:34px}}
*{{box-sizing:border-box}} html,body{{height:100%;margin:0;overflow:hidden}} body{{font-family:Inter,system-ui,-apple-system,Segoe UI,Arial,sans-serif;background:radial-gradient(circle at top left,#18284a,#0b1020 46%,#090d18);color:var(--text)}}
a{{color:var(--cyan)}}
.hero{{height:var(--head);display:flex;flex-direction:column;align-items:center;justify-content:center;background:linear-gradient(180deg,rgba(11,16,32,.99),rgba(11,16,32,.92));border-bottom:1px solid rgba(255,255,255,.08)}}
.hero h1{{font-size:clamp(24px,2.2vw,34px);margin:0;letter-spacing:.02em}} .subtitle{{color:var(--muted);font-size:13px;margin-top:2px}}
.frame{{height:calc(100vh - var(--head) - var(--foot));display:grid;grid-template-rows:var(--top) minmax(0,1fr);gap:10px;padding:10px 14px;overflow:hidden}}
.topbar{{display:grid;grid-template-columns:minmax(560px,1.45fr) minmax(360px,1fr);gap:10px;min-height:0}}
.panel{{background:rgba(18,26,45,.92);border:1px solid var(--line);border-radius:18px;box-shadow:0 18px 50px rgba(0,0,0,.24);min-width:0;overflow:hidden}}
.statusbar{{padding:10px 12px;display:grid;grid-template-columns:repeat(5,minmax(90px,1fr));gap:8px;align-content:center}}
.stat{{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:12px;padding:8px 9px;min-width:0}} .stat b{{font-size:18px;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}} .stat small{{color:var(--muted);font-size:11px}}
.leaders{{padding:8px 10px;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;align-content:center}}
.leader{{border:1px solid var(--line);background:linear-gradient(160deg,#1a2741,#10182b);color:var(--text);border-radius:12px;padding:7px 8px;min-width:0;cursor:pointer;text-align:left;height:62px;display:grid;grid-template-columns:auto 1fr;grid-template-rows:1fr 1fr;column-gap:7px;align-items:center}}
.leader:hover{{border-color:var(--cyan)}} .leader-rank{{grid-row:1/3;font-size:18px;font-weight:900;color:var(--muted);line-height:1}} .leader-name{{font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}} .leader-count{{font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.15}} .leader-1,.leader-2,.leader-3{{background:linear-gradient(160deg,#203152,#10182b)}}
.tables{{display:grid;grid-template-columns:1fr 1fr;gap:10px;min-height:0;overflow:hidden}}
.table-panel{{display:flex;flex-direction:column;padding:11px;min-height:0}}
.panel-head{{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px}} .panel-head h2{{margin:0;font-size:20px}}
.toolbar{{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-bottom:8px}} input,select,button{{background:#09111f;color:var(--text);border:1px solid var(--line);border-radius:999px;padding:8px 11px;font:inherit;font-size:13px}} input{{min-width:190px;flex:1}} button{{cursor:pointer}} button:hover{{border-color:var(--cyan)}} .mode-group{{display:flex;gap:5px}} .mode-btn.active{{border-color:var(--cyan);background:#0b1d34;color:var(--cyan)}} .table-count{{margin-left:auto;color:var(--muted);font-size:12px;font-weight:700;white-space:nowrap;padding-right:2px}}
.table-wrap{{flex:1;min-height:0;overflow:auto;border:1px solid var(--line);border-radius:13px}}
table{{width:100%;border-collapse:separate;border-spacing:0;font-size:13px}} th,td{{padding:8px 9px;border-bottom:1px solid rgba(255,255,255,.08);vertical-align:top}} th{{position:sticky;top:0;z-index:5;background:#111a2d;color:#cbd5e1;text-align:left;user-select:none;box-shadow:0 1px 0 rgba(255,255,255,.08)}} th.sortable{{cursor:pointer}} th.sortable:hover{{background:#1b2944;color:#fff}} th.sortable::after{{content:"↕";font-size:10px;color:var(--muted);margin-left:5px}} th.sort-asc::after{{content:"▲";color:var(--cyan)}} th.sort-desc::after{{content:"▼";color:var(--cyan)}} tr:hover td{{background:rgba(114,231,255,.04)}} .num{{text-align:right;white-space:nowrap}} .rank{{font-weight:800;color:var(--cyan)}} .artist-name{{font-weight:700}} .gold-t{{color:var(--gold);font-weight:800}} .silver-t{{color:var(--silver);font-weight:800}} .bronze-t{{color:var(--bronze);font-weight:800}} .strong{{font-weight:800}} .rankmark{{font-weight:900}} .r1{{color:var(--gold)}} .r2{{color:var(--silver)}} .r3{{color:var(--bronze)}} td.details-cell,th.details-cell{{white-space:nowrap}} .details-button{{white-space:nowrap;border-radius:999px;padding:5px 9px;background:#071425;color:var(--cyan)}}
.artist-link{{border:0;background:none;color:var(--cyan);padding:0;font:inherit;font-weight:800;cursor:pointer;text-align:left}} .artist-link:hover{{text-decoration:underline}}
.fullscreen{{position:fixed!important;inset:8px 8px calc(var(--foot) + 8px) 8px!important;z-index:80!important;background:rgba(18,26,45,.98)}} .fs-close{{display:none}} .fullscreen .fs-close{{display:inline-block}}
.modal-backdrop{{position:fixed;inset:0;background:rgba(0,0,0,.66);display:none;align-items:center;justify-content:center;z-index:100;padding:24px}} .modal-backdrop.open{{display:flex}} .modal{{width:min(1100px,96vw);max-height:90vh;overflow:auto;background:#10182b;border:1px solid var(--line);border-radius:22px;box-shadow:0 30px 90px rgba(0,0,0,.55)}} .modal-head{{position:sticky;top:0;background:#10182b;border-bottom:1px solid var(--line);padding:16px 20px;display:flex;justify-content:space-between;gap:12px;align-items:flex-start}} .modal-head>div{{min-width:0;flex:1}} .modal-head h2{{margin:0;font-size:22px}} .artist-bio{{display:block;width:100%;min-width:0;max-width:100%;color:var(--muted);font-size:13px;line-height:1.35;margin-top:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}} .modal-body{{padding:16px 20px 22px}} .artist-player-wrap{{display:flex;flex-direction:column;gap:10px;margin-bottom:14px;padding:16px;border:1px solid rgba(114,231,255,.22);border-radius:18px;background:linear-gradient(180deg,#15233e,#0b1220);box-shadow:0 18px 40px rgba(0,0,0,.28)}} .artist-player-top{{display:grid;grid-template-columns:auto 1fr;gap:14px;align-items:center}} .artist-player-meta{{min-width:0;flex:1}} .artist-player-meta b{{display:block;font-size:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}} .artist-player-meta small{{display:block;color:var(--muted);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}} .artist-player-actions{{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}} .player-chip{{display:inline-flex;align-items:center;gap:6px;padding:6px 10px;border-radius:999px;border:1px solid var(--line);background:#09111f;color:var(--text);font-size:12px;font-weight:700;white-space:nowrap}} .player-chip:hover{{border-color:var(--cyan)}} .player-chip.is-verified{{border-color:rgba(114,231,255,.4);color:var(--cyan)}} .player-chip.is-fallback{{border-color:rgba(217,226,239,.35);color:var(--silver)}} .player-chip.is-missing{{border-color:rgba(255,145,145,.4);color:#ff9a9a}} .player-chip.is-unknown{{border-color:rgba(251,191,36,.4);color:#fbbf24}} .artist-player{{width:100%;min-width:0}} .play-button{{min-width:42px;border-radius:999px;padding:6px 10px;background:#071425;color:var(--cyan);font-weight:900}} .play-button:hover{{border-color:var(--cyan)}} .play-button.hero-play{{min-width:60px;min-height:60px;padding:0 16px;font-size:20px;border-radius:18px;background:linear-gradient(180deg,#112744,#071425);box-shadow:0 10px 24px rgba(0,0,0,.35)}} .artist-stats{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-bottom:12px}} .artist-toolbar{{display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap;margin:8px 0 12px}} .artist-mode-note{{color:var(--muted);font-size:12px}} .artist-detail-wrap{{border:1px solid var(--line);border-radius:14px;overflow:auto;max-height:52vh}} .artist-detail-table{{width:100%;border-collapse:separate;border-spacing:0;font-size:13px}} .artist-detail-table th{{top:0;background:#111a2d}} .artist-detail-table td{{background:transparent}} .artist-row.dq td{{opacity:.78}} .artist-row.dq .rankmark{{color:#ff8f8f}} .dq{{color:#ff8f8f;font-weight:900}} .rank-panel{{display:grid;gap:6px}} .rank-panel-head{{display:flex;justify-content:flex-start;align-items:baseline;gap:8px;flex-wrap:wrap}} .rank-panel-title{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}} .rank-badges{{display:flex;gap:4px;flex-wrap:wrap}} .rank-pill{{display:inline-flex;align-items:center;gap:6px;padding:4px 9px;border-radius:999px;border:1px solid var(--line);background:#0a1221;font-size:12px;font-weight:800;white-space:nowrap}} .rank-pill b{{font-size:13px}} .rank-pill.gold{{color:var(--gold)}} .rank-pill.silver{{color:var(--silver)}} .rank-pill.bronze{{color:var(--bronze)}} .rank-pill.fourth{{color:#a7bddc}} .rank-pill.fifth{{color:#9db0c8}}
.loading-overlay{{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(6,10,18,.28);backdrop-filter:blur(2px);opacity:0;pointer-events:none;transition:opacity .15s ease;z-index:120}} .loading-overlay.open{{opacity:1;pointer-events:auto}} .loading-card{{display:flex;align-items:center;gap:12px;padding:14px 16px;border-radius:16px;border:1px solid rgba(114,231,255,.35);background:rgba(16,24,43,.94);box-shadow:0 18px 50px rgba(0,0,0,.35);color:var(--text)}} .loading-spinner{{width:18px;height:18px;border-radius:999px;border:2px solid rgba(114,231,255,.22);border-top-color:var(--cyan);animation:spin .8s linear infinite}} .loading-text b{{display:block;font-size:14px}} .loading-text small{{display:block;color:var(--muted);font-size:12px;margin-top:2px}} body.is-loading .topbar,body.is-loading .tables{{opacity:.7;filter:saturate(.92)}} @keyframes spin{{to{{transform:rotate(360deg)}}}}
.footer{{position:fixed;left:0;right:0;bottom:0;height:var(--foot);display:flex;align-items:center;justify-content:center;gap:14px;color:var(--muted);background:rgba(8,13,24,.96);border-top:1px solid rgba(255,255,255,.08);z-index:90;font-size:12px}}
@media(max-width:1400px){{:root{{--top:150px}} .topbar{{grid-template-columns:1fr;grid-template-rows:68px 72px}} .leaders{{grid-template-columns:repeat(3,1fr)}} .leader{{height:28px;grid-template-rows:1fr;grid-template-columns:auto 1fr auto}} .leader-rank{{grid-row:auto;font-size:14px}} .leader-count{{font-size:10px}} .tables{{grid-template-columns:1fr 1fr}}}}
@media(max-width:900px){{html,body{{overflow:auto}} .frame{{height:auto;display:block}} .topbar,.tables{{display:block}} .panel{{margin-bottom:10px}} .statusbar{{grid-template-columns:repeat(2,1fr)}} .leaders{{grid-template-columns:repeat(2,1fr)}} .table-wrap{{height:420px}}}}
</style>
</head>
<body>
<header class="hero">
  <h1>🏆 KVR OSC Hall of Fame</h1>
  <div class="subtitle">OSC{osc_min:03d}–OSC{osc_max:03d}{f' · {year_min}–{year_max}' if year_min and year_max else ''} · Hall of Fame + OSC Results · generated {esc(now)}</div>
</header>
<div class="frame">
  <section class="topbar">
    <div class="panel statusbar" id="statusbar">{stats_html}</div>
    <div class="panel leaders" id="leaders">{''.join(leader_items)}</div>
  </section>
  <main class="tables">
    <section class="panel table-panel" id="overall-section">
      <div class="panel-head"><h2>Hall of Fame Overall</h2><div><button onclick="toggleFullscreen('overall-section')">Fullscreen</button><button class="fs-close" onclick="toggleFullscreen('overall-section')">Close</button></div></div>
      <div class="toolbar"><input id="artistSearch" placeholder="Search artist, track, or OSC..."><span id="overallCount" class="table-count"></span><button onclick="resetOverall()">Reset</button></div>
      <div class="table-wrap"><table id="overallTable"><thead><tr><th>#</th><th>Artist</th><th>🥇</th><th>🥈</th><th>🥉</th><th>4th</th><th>5th</th><th>Top5</th><th>Podium</th><th>Entries</th><th>Rate</th><th>OSC range</th><th class="details-cell">Details</th></tr></thead><tbody>{''.join(overall_rows)}</tbody></table></div>
    </section>
    <section class="panel table-panel" id="results-section">
      <div class="panel-head"><h2>All OSC Results</h2><div><button onclick="toggleFullscreen('results-section')">Fullscreen</button><button class="fs-close" onclick="toggleFullscreen('results-section')">Close</button></div></div>
      <div class="toolbar"><input id="top5Search" placeholder="Search OSC, year, synth, artist, or track..."><select id="oscFilter">{''.join(osc_options)}</select>{template_filter_html}<div class="mode-group"><button class="mode-btn active" data-mode="all" onclick="setResultMode('all')">ALL</button><button class="mode-btn" data-mode="top3" onclick="setResultMode('top3')">TOP3</button><button class="mode-btn" data-mode="top5" onclick="setResultMode('top5')">TOP5</button><button class="mode-btn" data-mode="top10" onclick="setResultMode('top10')">TOP10</button><button class="mode-btn" data-mode="last10" onclick="setResultMode('last10')">LAST10</button></div><span id="resultsCount" class="table-count"></span><button onclick="resetResults()">Reset</button></div>
      <div class="table-wrap"><table id="resultsTable"><thead><tr><th>OSC</th><th>Year</th><th>Synth</th><th>Place</th><th>Artist</th><th>Track</th><th>Points</th>{template_head_html}</tr></thead><tbody>{''.join(result_rows)}</tbody></table></div>
    </section>
  </main>
</div>
<div id="artistModal" class="modal-backdrop" onclick="closeArtistDetails(event)">
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="artistModalTitle" onclick="event.stopPropagation()">
    <div class="modal-head">
      <div>
        <h2 id="artistModalTitle">Details</h2>
        <div id="artistModalBio" class="artist-bio"></div>
      </div>
      <button onclick="closeArtistDetails()">Close</button>
    </div>
    <div class="modal-body" id="artistModalBody">
      <div id="artistPlayerWrap" class="artist-player-wrap" style="display:none">
        <div class="artist-player-top">
          <button type="button" id="artistPlayerToggle" class="play-button hero-play" title="Play / Pause">▶</button>
          <div class="artist-player-meta">
            <b id="artistPlayerTitle">Archive Player</b>
            <small id="artistPlayerSubtitle"></small>
            <div class="artist-player-actions">
              <span id="artistPlayerStatus" class="player-chip is-verified">Archive checked</span>
              <a id="artistPlayerArchiveLink" class="player-chip" href="#" target="_blank" rel="noopener">Archive Details</a>
              <a id="artistPlayerFileLink" class="player-chip" href="#" target="_blank" rel="noopener">Track File</a>
            </div>
          </div>
        </div>
        <audio id="artistPlayer" class="artist-player" controls preload="none"></audio>
      </div>
      <div id="artistModalStats" class="artist-stats"></div>
      <div class="artist-toolbar">
        <div class="mode-group">
          <button class="mode-btn active" id="artistModeAll" onclick="setArtistMode('all')">ALL</button>
          <button class="mode-btn" id="artistModeTop3" onclick="setArtistMode('top3')">TOP3</button>
          <button class="mode-btn" id="artistModeTop5" onclick="setArtistMode('top5')">TOP5</button>
        </div>
        <div id="artistModeHint" class="artist-mode-note"></div>
      </div>
      <div class="artist-detail-wrap">
        <table class="artist-detail-table">
          <thead><tr><th>OSC</th><th>Year</th><th>Synth</th><th data-nosort="1">Play</th><th>Place</th><th>Artist</th><th>Track</th><th>Points</th>{template_head_html}</tr></thead>
          <tbody id="artistDetailRows"></tbody>
        </table>
      </div>
    </div>
  </div>
</div>
<div id="loadingOverlay" class="loading-overlay" aria-hidden="true">
  <div class="loading-card" role="status" aria-live="polite" aria-busy="true">
    <span class="loading-spinner"></span>
    <div class="loading-text">
      <b id="loadingTitle">Loading data...</b>
      <small>Please wait a moment.</small>
    </div>
  </div>
</div>
<footer class="footer"><span>Script {version}</span><span>·</span><a href="https://www.kvraudio.com/forum/viewforum.php?f=1" target="_blank" rel="noopener">KVR One Synth Challenge</a><span>·</span><span>Hall of Fame + OSC Results</span></footer>
<script id="artist-meta" type="application/json">{artist_meta_json}</script>
<script>
const ARTIST_META = JSON.parse(document.getElementById('artist-meta').textContent);
let activeArtistKey = '';
let activeArtistMode = 'all';
let resultMode = 'all';
let loadingSeq = 0;
const STORAGE_PREFIX = 'hof.';
function norm(s){{ return String(s || '').toLowerCase(); }}
function escapeHtml(s){{ return String(s||'').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c])); }}
function storageGet(key, fallback='') {{
  try {{
    const value = localStorage.getItem(STORAGE_PREFIX + key);
    return value === null ? fallback : value;
  }} catch (e) {{
    return fallback;
  }}
}}
function storageSet(key, value) {{
  try {{ localStorage.setItem(STORAGE_PREFIX + key, String(value ?? '')); }} catch (e) {{}}
}}
function storageRemove(key) {{
  try {{ localStorage.removeItem(STORAGE_PREFIX + key); }} catch (e) {{}}
}}
function setLoading(active, label='Loading data...') {{
  const overlay = document.getElementById('loadingOverlay');
  const title = document.getElementById('loadingTitle');
  if(!overlay || !title) return;
  if(active) {{
    title.textContent = label;
    overlay.classList.add('open');
    document.body.classList.add('is-loading');
    overlay.setAttribute('aria-hidden', 'false');
  }} else {{
    overlay.classList.remove('open');
    document.body.classList.remove('is-loading');
    overlay.setAttribute('aria-hidden', 'true');
  }}
}}
function runWithLoading(label, action) {{
  const token = ++loadingSeq;
  setLoading(true, label);
  requestAnimationFrame(() => {{
    requestAnimationFrame(() => {{
      try {{
        action();
      }} finally {{
        requestAnimationFrame(() => {{
          if(token === loadingSeq) setLoading(false);
        }});
      }}
    }});
  }});
}}
function openArtistDetails(key, mode='all'){{
  console.log("openArtistDetails()...")
  activeArtistKey = key;
  activeArtistMode = mode || 'all';
  renderArtistDetails();
  document.getElementById('artistModal').classList.add('open');
}}
function closeArtistDetails(ev){{
  if(ev && ev.target && ev.target.id !== 'artistModal') return;
  const audio = document.getElementById('artistPlayer');
  if(audio) {{
    try {{ audio.pause(); }} catch (e) {{}}
  }}
  document.getElementById('artistModal').classList.remove('open');
}}
document.addEventListener('keydown', ev => {{ if(ev.key === 'Escape') {{ closeArtistDetails(); document.querySelectorAll('.fullscreen').forEach(x => x.classList.remove('fullscreen')); }} }});
function artistRows(key) {{
  return Array.from(document.querySelectorAll('#resultsTable tbody tr')).filter(tr => tr.dataset.artistKey === key);
}}
function visibleArtistRows(mode) {{
  return artistRows(activeArtistKey).filter(tr => {{
    const rank = Number(tr.dataset.rank);
    const isDQ = tr.dataset.dq === '1';
    if(mode === 'top3') return !isDQ && rank <= 3;
    if(mode === 'top5') return !isDQ && rank <= 5;
    return true;
  }});
}}
function artistPlacementSortValue(tr) {{
  const isDQ = tr.dataset.dq === '1';
  const rank = Number(tr.dataset.rank);
  return isDQ ? 999999 : (Number.isFinite(rank) ? rank : 999999);
}}
function artistStatCard(value, label) {{
  return '<div class="stat"><b>' + escapeHtml(String(value)) + '</b><small>' + escapeHtml(label) + '</small></div>';
}}
function artistRankPanel(title, pills) {{
  return '<div class="stat rank-panel"><div class="rank-panel-head"><b>' + escapeHtml(title) + '</b><span class="rank-panel-title">Place distribution</span></div><div class="rank-badges">' + pills.join('') + '</div></div>';
}}
function rankPill(label, value, cls) {{
  return '<span class="rank-pill ' + cls + '"><span>' + escapeHtml(label) + '</span><b>' + escapeHtml(String(value)) + '</b></span>';
}}
function countPill(value, cls) {{
  return '<span class="rank-pill ' + cls + '"><b>' + escapeHtml(String(value)) + '</b></span>';
}}
function archiveStatusLabel(state, fallbackKind) {{
  const s = String(state || '').toLowerCase();
  const k = String(fallbackKind || '').toUpperCase();
  if(s === 'verified') return ['Archive online', 'is-verified'];
  if(s === 'missing') return ['Not found', 'is-missing'];
  if(s === 'unknown') return ['Not checked', 'is-unknown'];
  if(k === 'ORDER_FALLBACK') return ['Verified via archive', 'is-fallback'];
  if(k === 'EXACT' || k === 'ENTRY' || k === 'SWAPPED') return ['Verified via archive', 'is-verified'];
  return ['Archive checked', 'is-verified'];
}}
function setArchivePlayer(url, title, subtitle, state, detailsUrl, fileUrl, matchKind) {{
  const wrap = document.getElementById('artistPlayerWrap');
  const audio = document.getElementById('artistPlayer');
  const titleEl = document.getElementById('artistPlayerTitle');
  const subEl = document.getElementById('artistPlayerSubtitle');
  const statusEl = document.getElementById('artistPlayerStatus');
  const detailsEl = document.getElementById('artistPlayerArchiveLink');
  const fileEl = document.getElementById('artistPlayerFileLink');
  const toggleEl = document.getElementById('artistPlayerToggle');
  if(!wrap || !audio || !titleEl || !subEl || !statusEl || !detailsEl || !fileEl || !toggleEl) return;
  if(!url) {{
    wrap.style.display = 'none';
    audio.removeAttribute('src');
    audio.load();
    titleEl.textContent = 'Archive Player';
    subEl.textContent = '';
    statusEl.textContent = 'No archive file';
    statusEl.className = 'player-chip is-missing';
    detailsEl.removeAttribute('href');
    fileEl.removeAttribute('href');
    return;
  }}
  wrap.style.display = '';
  audio.src = url;
  audio.load();
  titleEl.textContent = title || 'Archive Stream';
  subEl.textContent = subtitle || '';
  const label = archiveStatusLabel(state, matchKind);
  statusEl.textContent = label[0];
  statusEl.className = 'player-chip ' + label[1];
  if(detailsUrl) {{
    detailsEl.href = detailsUrl;
    detailsEl.style.display = '';
  }} else {{
    detailsEl.removeAttribute('href');
    detailsEl.style.display = 'none';
  }}
  if(fileUrl) {{
    fileEl.href = fileUrl;
    fileEl.style.display = '';
  }} else {{
    fileEl.removeAttribute('href');
    fileEl.style.display = 'none';
  }}
  toggleEl.textContent = '▶';
}}
function playArchiveTrack(btn) {{
  const url = btn && btn.dataset ? btn.dataset.archiveUrl : '';
  const state = btn && btn.dataset ? btn.dataset.archiveLinkState : '';
  const detailsUrl = btn && btn.dataset ? btn.dataset.archiveDetailsUrl : '';
  const matchKind = btn && btn.dataset ? btn.dataset.archiveMatchKind : '';
  const row = btn && typeof btn.closest === 'function' ? btn.closest('tr') : null;
  const artistName = row && row.dataset ? row.dataset.artistName : '';
  const track = row && row.dataset ? row.dataset.track : '';
  const osc = row && row.dataset ? row.dataset.osc : '';
  const archiveFile = row && row.dataset ? row.dataset.archiveFile : '';
  const title = (String(artistName || '').trim() + ' - ' + String(track || '').trim()).replace(/^\\s*-\\s*|\\s*-\\s*$/g, '').trim() || (btn && btn.dataset ? btn.dataset.archiveTitle : '');
  const subtitle = ('OSC' + String(osc || '').padStart(3, '0') + ' · ' + String(archiveFile || track || '').trim()).trim();
  if(!url) return;
  setArchivePlayer(url, title, subtitle, state, detailsUrl, url, matchKind);
  const audio = document.getElementById('artistPlayer');
  if(audio) {{
    const playPromise = audio.play();
    if(playPromise && typeof playPromise.catch === 'function') playPromise.catch(() => {{}});
  }}
}}
function syncArchiveToggle() {{
  const audio = document.getElementById('artistPlayer');
  const toggle = document.getElementById('artistPlayerToggle');
  if(!audio || !toggle) return;
  toggle.textContent = (audio.paused || audio.ended) ? '▶' : '⏸';
}}
function renderArtistDetails(){{
  const meta = ARTIST_META[activeArtistKey] || {{artist: activeArtistKey, bio: 'No metadata found.', bio_short: 'No metadata found.', placements: []}};
  const showTemplateColumns = {str(debug_template_columns).lower()};
  const rows = visibleArtistRows(activeArtistMode).sort((a,b) => {{
    const ar = artistPlacementSortValue(a), br = artistPlacementSortValue(b);
    if(ar !== br) return ar - br;
    const ao = Number(a.dataset.osc), bo = Number(b.dataset.osc);
    if(ao !== bo) return bo - ao;
    return String(a.dataset.track || '').localeCompare(String(b.dataset.track || ''), undefined, {{numeric:true, sensitivity:'base'}});
  }});
  const total = artistRows(activeArtistKey).length;
  const gold = artistRows(activeArtistKey).filter(tr => tr.dataset.dq !== '1' && Number(tr.dataset.rank) === 1).length;
  const silver = artistRows(activeArtistKey).filter(tr => tr.dataset.dq !== '1' && Number(tr.dataset.rank) === 2).length;
  const bronze = artistRows(activeArtistKey).filter(tr => tr.dataset.dq !== '1' && Number(tr.dataset.rank) === 3).length;
  const fourth = artistRows(activeArtistKey).filter(tr => tr.dataset.dq !== '1' && Number(tr.dataset.rank) === 4).length;
  const fifth = artistRows(activeArtistKey).filter(tr => tr.dataset.dq !== '1' && Number(tr.dataset.rank) === 5).length;
  const top3 = artistRows(activeArtistKey).filter(tr => tr.dataset.dq !== '1' && Number(tr.dataset.rank) <= 3).length;
  const top5 = artistRows(activeArtistKey).filter(tr => tr.dataset.dq !== '1' && Number(tr.dataset.rank) <= 5).length;
  const years = artistRows(activeArtistKey).map(tr => Number(tr.dataset.year)).filter(y => y > 0);
  const firstYear = years.length ? Math.min(...new Set(years)) : null;
  const lastYear = years.length ? Math.max(...new Set(years)) : null;
  const activeYears = years.length ? new Set(years).size : 0;
  const durationMonths = Math.max(0, Number(meta.osc_last || 0) - Number(meta.osc_first || 0) + 1);
  const yearSpan = firstYear && lastYear ? (firstYear === lastYear ? String(firstYear) : firstYear + '–' + lastYear) : '–';
  const durationText = meta.duration_text || '–';
  const top5Rate = isFinite(Number(meta.top5_rate)) ? (Number(meta.top5_rate) * 100).toFixed(1) + '%' : '–';
  const modeCount = rows.length;
  document.getElementById('artistModalTitle').textContent = meta.artist || activeArtistKey;
  document.getElementById('artistModalBio').textContent = meta.bio_short || meta.bio || '';
  const bioEl = document.getElementById('artistModalBio');
  bioEl.style.whiteSpace = 'nowrap';
  bioEl.style.overflow = 'hidden';
  bioEl.style.textOverflow = 'ellipsis';
  bioEl.style.width = '100%';
  const playable = rows.find(tr => tr.dataset.archiveUrl);
  if(playable) {{
    const oscTitle = ((playable.dataset.artistName || '') + ' - ' + (playable.dataset.track || '')).trim().replace(/^\\s*-\\s*|\\s*-\\s*$/g, '').trim();
    const oscSubtitle = ('OSC' + String(playable.dataset.osc || '').padStart(3, '0') + ' · ' + (playable.dataset.archiveFile || playable.dataset.track || '')).trim();
    setArchivePlayer(
      playable.dataset.archiveUrl || '',
      oscTitle,
      oscSubtitle,
      playable.dataset.archiveLinkState || '',
      playable.dataset.url || '',
      playable.dataset.archiveUrl || '',
      playable.dataset.archiveMatchKind || ''
    );
  }} else {{
    setArchivePlayer('', '', '', '', '', '', '');
  }}
  document.getElementById('artistModalStats').innerHTML = [
    artistStatCard(meta.osc_range || '–', 'OSC range'),
    artistStatCard(total, 'Entries'),
    artistRankPanel('Top 3', [
      rankPill('🥇', gold, 'gold'),
      rankPill('🥈', silver, 'silver'),
      rankPill('🥉', bronze, 'bronze')
    ]),
    artistRankPanel('Place 4 / 5', [
      countPill(fourth, 'fourth'),
      countPill(fifth, 'fifth')
    ]),
    artistStatCard(yearSpan, 'Years'),
    artistStatCard(durationText, 'Duration'),
    artistStatCard(top5Rate, 'Top5 rate')
  ].join('');
  document.getElementById('artistModeHint').textContent = modeCount + ' visible entries';
  bioEl.title = meta.bio || meta.bio_short || '';
  const tbody = document.getElementById('artistDetailRows');
  const items = rows.map(tr => {{
    const rankRaw = tr.dataset.rankRaw || tr.dataset.rank || '';
    const isDQ = tr.dataset.dq === '1';
    const place = isDQ ? 'DQ' : rankRaw;
    const rankClass = isDQ ? 'dq' : 'r' + Number(tr.dataset.rank);
    const osc = tr.dataset.osc || '';
    const year = tr.dataset.year || '';
    const synth = tr.dataset.synth || '';
    const artist = tr.dataset.artistName || '';
    const track = tr.dataset.track || '';
    const points = tr.dataset.points || '';
    const template = tr.dataset.template || '';
    const url = tr.dataset.url || '';
    const archiveUrl = tr.dataset.archiveUrl || '';
    const archiveDetailsUrl = tr.dataset.url || '';
    const archiveState = tr.dataset.archiveLinkState || '';
    const archiveMatchKind = tr.dataset.archiveMatchKind || '';
    const oscCell = url ? '<a href=\"' + escapeHtml(url) + '\" target=\"_blank\" rel=\"noopener\">OSC' + String(osc).padStart(3, '0') + '</a>' : 'OSC' + String(osc).padStart(3, '0');
    const playCell = archiveUrl ? '<button type=\"button\" class=\"play-button\" data-archive-url=\"' + escapeHtml(archiveUrl) + '\" data-archive-details-url=\"' + escapeHtml(archiveDetailsUrl) + '\" data-archive-title=\"' + escapeHtml(tr.dataset.archiveTitle || '') + '\" data-archive-link-state=\"' + escapeHtml(archiveState) + '\" data-archive-match-kind=\"' + escapeHtml(archiveMatchKind) + '\" onclick=\"playArchiveTrack(this)\">▶</button>' : '<span class=\"muted\">–</span>';
    const templateCell = showTemplateColumns ? '<td>' + escapeHtml(template) + '</td>' : '';
    return '<tr class=\"artist-row ' + rankClass + '\">' + 
      '<td class=\"num\">' + oscCell + '</td>' +
      '<td class=\"num\">' + escapeHtml(String(year || '')) + '</td>' +
      '<td>' + escapeHtml(synth) + '</td>' +
      '<td class=\"num\">' + playCell + '</td>' +
      '<td class=\"num rankmark ' + rankClass + '\">' + escapeHtml(String(place)) + '</td>' +
      '<td>' + escapeHtml(artist) + '</td>' +
      '<td>' + (url ? '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener">' + escapeHtml(track) + '</a>' : escapeHtml(track)) + '</td>' +
      '<td class=\"num\">' + escapeHtml(String(points)) + '</td>' +
      templateCell +
    '</tr>';
  }}).join('');
  tbody.innerHTML = items || '<tr><td colspan=\"' + (showTemplateColumns ? '9' : '8') + '\">No details found.</td></tr>';
  document.getElementById('artistModeAll').classList.toggle('active', activeArtistMode === 'all');
  document.getElementById('artistModeTop3').classList.toggle('active', activeArtistMode === 'top3');
  document.getElementById('artistModeTop5').classList.toggle('active', activeArtistMode === 'top5');
}}
function setArtistMode(mode){{ activeArtistMode = mode; renderArtistDetails(); }}
function cellSortValue(td){{
  if(!td) return '';
  const txt = (td.innerText || '').trim();
  if(/^dq$/i.test(txt)) return 999999;
  const osc = txt.match(/^OSC\\s*0*(\\d+)/i); if(osc) return Number(osc[1]);
  const range = txt.match(/^(\\d+)\\s*[–-]\\s*(\\d+)$/); if(range) return Number(range[1]);
  const pct = txt.match(/^(-?\\d+(?:[.,]\\d+)?)%$/); if(pct) return Number(pct[1].replace(',','.'));
  const num = txt.replace(/^[🥇🥈🥉]\\s*/, '').replace(/,/g,'.').match(/^-?\\d+(?:\\.\\d+)?$/); if(num) return Number(num[0]);
  return txt.toLowerCase();
}}
function compareValues(a,b,asc){{
  const an = typeof a === 'number', bn = typeof b === 'number';
  const cmp = (an && bn) ? a - b : String(a).localeCompare(String(b), undefined, {{numeric:true, sensitivity:'base'}});
  return asc ? cmp : -cmp;
}}
function sortTable(table, col){{
  const tbody = table.tBodies[0]; if(!tbody) return;
  const asc = table.dataset.sortCol !== String(col) || table.dataset.sortDir !== 'asc';
  Array.from(tbody.rows).sort((ra, rb) => compareValues(cellSortValue(ra.cells[col]), cellSortValue(rb.cells[col]), asc)).forEach(r => tbody.appendChild(r));
  table.dataset.sortCol = String(col); table.dataset.sortDir = asc ? 'asc' : 'desc';
  table.querySelectorAll('th').forEach(th => th.classList.remove('sort-asc','sort-desc'));
  const th = table.tHead && table.tHead.rows[0] && table.tHead.rows[0].cells[col]; if(th) th.classList.add(asc ? 'sort-asc' : 'sort-desc');
}}
function makeTablesSortable(){{
  document.querySelectorAll('table').forEach(table => {{
    const head = table.tHead && table.tHead.rows[0]; if(!head) return;
    Array.from(head.cells).forEach((th, i) => {{ if(th.dataset && th.dataset.nosort === '1') return; th.classList.add('sortable'); th.title = 'Sortieren'; th.addEventListener('click', () => sortTable(table, i)); }});
  }});
}}
function filterTable(inputId, tableId){{
  const q = norm(document.getElementById(inputId).value);
  const rows = Array.from(document.querySelectorAll('#' + tableId + ' tbody tr'));
  rows.forEach(tr => {{ tr.style.display = norm(tr.innerText).includes(q) ? '' : 'none'; }});
  updateOverallCount();
}}
function latestOscs(n){{ return Array.from(new Set(Array.from(document.querySelectorAll('#resultsTable tbody tr')).map(tr => Number(tr.dataset.osc)))).sort((a,b) => b-a).slice(0,n); }}
function updateModeButtons(){{ document.querySelectorAll('.mode-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === resultMode)); }}
function setResultMode(mode){{ resultMode = mode; storageSet('resultMode', resultMode); updateModeButtons(); filterResults(); }}
function applySavedFilters() {{
  const artistSearch = document.getElementById('artistSearch');
  const top5Search = document.getElementById('top5Search');
  const oscFilter = document.getElementById('oscFilter');
  const templateFilterEl = document.getElementById('templateFilter');
  if(artistSearch) artistSearch.value = storageGet('artistSearch', artistSearch.value || '');
  if(top5Search) top5Search.value = storageGet('top5Search', top5Search.value || '');
  if(oscFilter) oscFilter.value = storageGet('oscFilter', oscFilter.value || '');
  if(templateFilterEl) templateFilterEl.value = storageGet('templateFilter', templateFilterEl.value || '');
  resultMode = storageGet('resultMode', resultMode || 'all') || 'all';
  updateModeButtons();
  filterTable('artistSearch','overallTable');
  filterResults();
}}
function debounce(fn, wait) {{
  let timer = null;
  return (...args) => {{
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  }};
}}
function filterResults(){{
  const q = norm(document.getElementById('top5Search').value);
  const osc = document.getElementById('oscFilter').value;
  const templateEl = document.getElementById('templateFilter');
  const template = templateEl ? templateEl.value : '';
  const latest = resultMode === 'last10' ? latestOscs(10) : null;
  const rankLimit = resultMode === 'top3' ? 3 : (resultMode === 'top5' ? 5 : (resultMode === 'top10' ? 10 : null));
  const rows = Array.from(document.querySelectorAll('#resultsTable tbody tr'));
  rows.forEach(tr => {{
    const rank = Number(tr.dataset.rank), rowOsc = Number(tr.dataset.osc);
    const isDQ = tr.dataset.dq === '1';
    const rowTemplate = String(tr.dataset.template || '');
    const okq = norm(tr.innerText).includes(q);
    const okOsc = osc ? String(rowOsc) === osc : true;
    const okTemplate = templateEl ? (template ? rowTemplate === template : true) : true;
    const okRank = rankLimit ? (!isDQ && rank <= rankLimit) : true;
    const okLast = latest ? (latest.includes(rowOsc) && !isDQ) : true;
    tr.style.display = (okq && okOsc && okTemplate && okRank && okLast) ? '' : 'none';
  }});
  updateResultsCount();
}}
function resetOrder(tableId){{
  const table = document.getElementById(tableId), tbody = table && table.tBodies[0]; if(!tbody) return;
  Array.from(tbody.rows).sort((a,b) => Number(a.dataset.orig || 0) - Number(b.dataset.orig || 0)).forEach(r => tbody.appendChild(r));
  delete table.dataset.sortCol; delete table.dataset.sortDir; table.querySelectorAll('th').forEach(th => th.classList.remove('sort-asc','sort-desc'));
}}
const debouncedOverallFilter = debounce(() => filterTable('artistSearch','overallTable'), 180);
const debouncedResultFilter = debounce(filterResults, 180);
function updateTableCount(tableId, countId, label) {{
  const table = document.getElementById(tableId);
  const counter = document.getElementById(countId);
  if(!table || !counter) return;
  const rows = Array.from(table.tBodies[0].rows);
  const visible = rows.filter(tr => tr.style.display !== 'none').length;
  counter.textContent = visible + ' / ' + rows.length + ' ' + label;
}}
function updateOverallCount() {{ updateTableCount('overallTable', 'overallCount', 'Artists'); }}
function updateResultsCount() {{ updateTableCount('resultsTable', 'resultsCount', 'Rows'); }}
function resetOverall(){{
  runWithLoading('Resetting overall filters...', () => {{
    document.getElementById('artistSearch').value='';
    storageRemove('artistSearch');
    resetOrder('overallTable');
    filterTable('artistSearch','overallTable');
  }});
}}
function resetResults(){{
  runWithLoading('Resetting result filters...', () => {{
    document.getElementById('top5Search').value='';
    document.getElementById('oscFilter').value='';
    const templateEl = document.getElementById('templateFilter');
    if(templateEl) templateEl.value='';
    resultMode='all';
    storageRemove('top5Search');
    storageRemove('oscFilter');
    storageRemove('templateFilter');
    storageRemove('resultMode');
    updateModeButtons();
    resetOrder('resultsTable');
    filterResults();
  }});
}}
function toggleFullscreen(id){{ document.getElementById(id).classList.toggle('fullscreen'); }}
document.getElementById('artistSearch').addEventListener('input', ev => {{ storageSet('artistSearch', ev.target.value); debouncedOverallFilter(); }});
document.getElementById('top5Search').addEventListener('input', ev => {{ storageSet('top5Search', ev.target.value); debouncedResultFilter(); }});
document.getElementById('oscFilter').addEventListener('change', ev => {{ storageSet('oscFilter', ev.target.value); filterResults(); }});
const templateFilterEl = document.getElementById('templateFilter');
if(templateFilterEl) templateFilterEl.addEventListener('change', ev => {{ storageSet('templateFilter', ev.target.value); filterResults(); }});
const artistPlayerEl = document.getElementById('artistPlayer');
const artistPlayerToggleEl = document.getElementById('artistPlayerToggle');
if(artistPlayerEl) {{
  ['play','pause','ended','loadedmetadata','emptied'].forEach(evt => artistPlayerEl.addEventListener(evt, syncArchiveToggle));
}}
if(artistPlayerToggleEl) {{
  artistPlayerToggleEl.addEventListener('click', ev => {{
    ev.preventDefault();
    const audio = document.getElementById('artistPlayer');
    if(!audio || !audio.src) return;
    if(audio.paused || audio.ended) audio.play().catch(() => {{}});
    else audio.pause();
    syncArchiveToggle();
  }});
}}
makeTablesSortable();
applySavedFilters();
window.addEventListener('pageshow', () => {{
  applySavedFilters();
  syncArchiveToggle();
}});
</script>
</body>
</html>'''


def validate_output(overall, details, html_text: str, result_details=None, has_full_results=False):
    """Sanity checks before writing the generated Hall-of-Fame HTML."""
    issues = []
    osc_values = set(details["osc"].dropna().apply(as_int).tolist()) if "osc" in details.columns else set()
    osc_min = min(osc_values) if osc_values else 0
    osc_max = max(osc_values) if osc_values else 0
    if osc_max <= 15:
        issues.append(f"FATAL: dataset only reaches OSC{osc_max:03d}; refusing to write a shrunken 001-015 HTML.")
    for section_id in ["statusbar", "leaders", "overall-section", "results-section"]:
        if f'id="{section_id}"' not in html_text:
            issues.append(f"FATAL: missing HTML section id #{section_id}.")
    forbidden = ["winners-section", "content-nav", "id=\"timeline\"", "scorecardTable", "Scorecard Stimmen"]
    for token in forbidden:
        if token in html_text:
            issues.append(f"FATAL: obsolete block still present: {token}")
    result_rows = html_text.count('data-rank="')
    expected_rows = len(result_details) if result_details is not None else len(details)
    if result_rows != expected_rows:
        issues.append(f"WARN: HTML result row count {result_rows} differs from result dataframe rows {expected_rows}.")
    required_js = ["function norm", "function sortTable", "function filterResults", "function resetResults", "makeTablesSortable();"]
    for token in required_js:
        if token not in html_text:
            issues.append(f"FATAL: missing JS function/call: {token}")
    fatal = [x for x in issues if x.startswith("FATAL:")]
    if fatal:
        raise RuntimeError("HTML validation failed:\n" + "\n".join(issues))
    winner_count = int((details["rank"] == 1).sum()) if "rank" in details.columns else 0
    print(f"Checks OK: OSC{osc_min:03d}-OSC{osc_max:03d}, {len(overall)} artists, {len(details)} result rows, {winner_count} winners.")
    for issue in issues:
        print(issue)


def main():
    ap = argparse.ArgumentParser(description=f"Generate a standalone KVR OSC Hall of Fame HTML page from audit CSVs. {version}: reads extractor normalized_results.csv for All OSC Results, compact leaders, fixed result filters.")
    ap.add_argument("--input", default="dist/hof", help="Folder with hall_of_fame_*.csv files")
    ap.add_argument("--challenges", default="kvrosc_challenges.csv", help="kvrosc_challenges.csv for year/url metadata")
    ap.add_argument("--config", default=str(Path(__file__).with_name("kvrosc_hall_of_fame_config.yaml")), help="YAML config for debug mode and optional protection.")
    ap.add_argument("--out", default="dist/hof/index.html", help="Output HTML path")
    ap.add_argument("--debug-template-columns", action="store_true", help="Show template columns and template filter for smoke-test debugging.")
    args = ap.parse_args()
    input_dir = Path(args.input)
    challenges = Path(args.challenges) if args.challenges else None
    config = load_hof_config(Path(args.config))
    protection_cfg = dict(config.get("protection") or {})
    debug_template_columns = bool(
        args.debug_template_columns
        or bool(config.get("debug-mode"))
        or os.environ.get("HOF_DEBUG_TEMPLATE_COLUMNS", "").strip() in {"1", "true", "TRUE", "yes", "on"}
    )
    result_details, result_source, has_full_results, scorecard_count, inventory_osc_count = load_inputs(input_dir, challenges)
    overall, details = summarize_results(result_details)
    html_text = build_html(
        result_details,
        result_source=result_source,
        has_full_results=has_full_results,
        scorecard_count=scorecard_count,
        inventory_osc_count=inventory_osc_count,
        debug_template_columns=debug_template_columns,
    )
    validate_output(overall, details, html_text, result_details=result_details, has_full_results=has_full_results)
    out = Path(args.out)
    out.write_text(html_text, encoding="utf-8")
    write_protection_files(out.parent, protection_cfg)
    print(f"v{version} wrote {out} ({len(overall)} artists, {len(details)} top5 rows, {len(result_details)} result rows; source: {result_source})")


if __name__ == "__main__":
    main()
