#!/usr/bin/env python3
# KVR OSC scorecard vote extractor v0.14
# Goal: inspect every scorecard, assign parser-template version, extract normalized results,
# validate extraction quality, and produce enough dumps to verify parser logic.
# v0.7: also supports OSC001-OSC015 TXT scorecards; HTML generators should consume these CSV/JSON outputs only.

import argparse, csv, json, re, zipfile, hashlib, sys
import xml.etree.ElementTree as ET
from pathlib import Path
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

version = "0.16"

NS={'a':'http://schemas.openxmlformats.org/spreadsheetml/2006/main','r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships','rel':'http://schemas.openxmlformats.org/package/2006/relationships'}
CELL_RE=re.compile(r'^([A-Z]+)([0-9]+)$')
FILE_RE=re.compile(r'^OSC(\d{3})_(.+?)_(\d+)\.(xlsx|txt)$', re.I)

VARIANT_LABELS={
 'V01':'early single-sheet result table',
 'V02':'classic sparse 10-point: Voting Results + TOTAL SCORES',
 'V03':'early full-score 1-5: TOTAL SCORES + Voting Results + Analysis/Raw Values',
 'V04':'ranked ballot / stacking: Final Stacking + TOTAL SCORES',
 'V05':'Results! + OSCxx matrix',
 'V06':'Results + Data',
 'V07':'Results + Data + Prizes Picked',
 'V08':'modern Results + Data + Prizes',
 'V09':'custom transitional sheets around OSC063-066',
 'T01':'early forum poll TXT',
 'T02':'early final table TXT',
 'V99':'unknown'}
VARIANT_ORDER={v:i for i,v in enumerate(['T01','T02','V01','V02','V03','V04','V05','V06','V07','V08','V09'],1)}

ARCHIVE_SEARCH_URL = 'https://archive.org/advancedsearch.php'
ARCHIVE_METADATA_URL = 'https://archive.org/metadata/{}'
ARCHIVE_SEARCH_QUERY = 'creator:"One Synth Challenge"'
ARCHIVE_SEARCH_FIELDS = ['identifier', 'title', 'creator', 'date', 'mediatype']
ARCHIVE_AUDIO_HINTS = ('mp3', 'flac', 'ogg', 'wav', 'm4a', 'aac')

_ARCHIVE_INDEX_CACHE = None
_ARCHIVE_META_CACHE = {}
_ARCHIVE_TRACKS_CACHE = {}
_ARCHIVE_URL_CHECK_CACHE = {}
_ARCHIVE_CACHE_FILE = None
_ARCHIVE_REFRESH_CACHE = False
_ARCHIVE_PREFETCH_WORKERS = 8
_ARCHIVE_VERIFY_LINKS = False

# Optional manual aliases. Keep tiny and conservative; generated candidate report does the rest.
MANUAL_ARTIST_ALIASES={
    'jasinski':'jasinski', 'Jasinski':'jasinski',
    'SIL3NC3_SWX':'SIL3NC3_SWX', 'SiL3NC3_SWX':'SIL3NC3_SWX', 'Sil3nc3_Swx':'SIL3NC3_SWX',
}

def col_to_num(s):
    n=0
    for ch in s.upper(): n=n*26+ord(ch)-64
    return n

def num_to_col(n):
    s=''
    while n:
        n,r=divmod(n-1,26); s=chr(65+r)+s
    return s

def cell_key(ref):
    m=CELL_RE.match(ref.replace('$',''))
    return (int(m.group(2)), col_to_num(m.group(1))) if m else None

def clean(x):
    if x is None: return ''
    if isinstance(x,float) and x.is_integer(): return str(int(x))
    return str(x).strip()

def clean_ws(x):
    return re.sub(r'\s+', ' ', clean(x))


def safe_int(v, default=0):
    try:
        if v is None or v == '': return default
        return int(float(str(v).strip()))
    except Exception:
        return default

def to_num(x):
    if x in (None,''): return None
    if isinstance(x,(int,float)): return float(x)
    try:
        return float(str(x).strip().replace(',','.'))
    except Exception:
        return None

def http_get_json(url, params=None, timeout=30):
    if params:
        query = urlencode(params, doseq=True)
        url = f"{url}?{query}"
    req = Request(url, headers={'User-Agent': f'kvrosc-scorecard-extractor/{version}'})
    with urlopen(req, timeout=timeout) as resp:
        payload = resp.read().decode('utf-8', errors='replace')
    return json.loads(payload)

def norm_text_key(s):
    return re.sub(r'[^a-z0-9]+', '', clean_ws(s).casefold())

def archive_parse_osc(text):
    if not text:
        return None
    s = clean_ws(text)
    patterns = [
        r'#\s*(\d{1,3})\b',
        r'\b(?:kvr[\s_\-]*)?osc[\s_\-]*0*(\d{1,3})\b',
        r'\bone[\s_\-]*synth[\s_\-]*challenge[\s_\-]*0*(\d{1,3})\b',
        r'challenge[^\d]{0,6}(\d{1,3})\b',
    ]
    for pat in patterns:
        m = re.search(pat, s, re.I)
        if m:
            return int(m.group(1))
    return None

def archive_index_row(doc):
    osc = archive_parse_osc(doc.get('title')) or archive_parse_osc(doc.get('identifier'))
    if osc is None:
        return None
    return {
        'osc': osc,
        'identifier': doc.get('identifier', ''),
        'title': doc.get('title', ''),
        'creator': doc.get('creator', ''),
        'date': doc.get('date', ''),
        'mediatype': doc.get('mediatype', ''),
    }

def archive_cache_load(cache_path):
    global _ARCHIVE_INDEX_CACHE, _ARCHIVE_TRACKS_CACHE, _ARCHIVE_URL_CHECK_CACHE
    if not cache_path:
        return False
    path = Path(cache_path)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        rows = data.get('rows', []) or []
        index = {}
        for row in rows:
            try:
                osc = int(row.get('osc'))
            except Exception:
                continue
            index.setdefault(osc, row)
        items = data.get('items', {}) or {}
        _ARCHIVE_INDEX_CACHE = {'rows': rows, 'index': index}
        _ARCHIVE_TRACKS_CACHE = {}
        for ident, item in items.items():
            if not isinstance(item, dict):
                continue
            tracks = item.get('tracks')
            if tracks:
                _ARCHIVE_TRACKS_CACHE[ident] = tracks
        link_checks = data.get('link_checks', {}) or {}
        _ARCHIVE_URL_CHECK_CACHE = {
            str(url): value
            for url, value in link_checks.items()
        }
        return True
    except Exception:
        return False

def archive_cache_save(cache_path):
    if not cache_path:
        return
    path = Path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    items = {}
    for ident, tracks in _ARCHIVE_TRACKS_CACHE.items():
        items.setdefault(ident, {})['tracks'] = tracks
    payload = {
        'rows': _ARCHIVE_INDEX_CACHE.get('rows', []) if _ARCHIVE_INDEX_CACHE else [],
        'items': items,
        'link_checks': _ARCHIVE_URL_CHECK_CACHE,
    }
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass

def archive_fetch_index():
    global _ARCHIVE_INDEX_CACHE
    if _ARCHIVE_INDEX_CACHE is not None:
        return _ARCHIVE_INDEX_CACHE
    data = http_get_json(ARCHIVE_SEARCH_URL, {
        'q': ARCHIVE_SEARCH_QUERY,
        'fl[]': ARCHIVE_SEARCH_FIELDS,
        'rows': 1000,
        'page': 1,
        'output': 'json',
        'sort[]': ['date desc'],
    })
    docs = data.get('response', {}).get('docs', []) or []
    index = {}
    rows = []
    for doc in docs:
        row = archive_index_row(doc)
        if not row:
            continue
        rows.append(row)
        index.setdefault(row['osc'], row)
    _ARCHIVE_INDEX_CACHE = {'rows': rows, 'index': index}
    return _ARCHIVE_INDEX_CACHE

def archive_fetch_metadata(identifier):
    if not identifier:
        return {}
    if identifier in _ARCHIVE_META_CACHE:
        return _ARCHIVE_META_CACHE[identifier]
    data = http_get_json(ARCHIVE_METADATA_URL.format(identifier), timeout=45)
    _ARCHIVE_META_CACHE[identifier] = data
    return data

def archive_get_tracks(identifier):
    if not identifier:
        return []
    if identifier in _ARCHIVE_TRACKS_CACHE:
        return _ARCHIVE_TRACKS_CACHE[identifier]
    meta = archive_fetch_metadata(identifier)
    tracks = archive_extract_tracks(meta)
    _ARCHIVE_TRACKS_CACHE[identifier] = tracks
    return tracks

def archive_prefetch_tracks(identifiers, workers=8):
    missing = [ident for ident in identifiers if ident and ident not in _ARCHIVE_TRACKS_CACHE]
    if not missing:
        return 0
    workers = max(1, min(int(workers or 1), len(missing), 16))
    def fetch_one(ident):
        try:
            tracks = archive_get_tracks(ident)
            return ident, len(tracks)
        except Exception:
            return ident, 0
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch_one, ident) for ident in missing]
        for fut in as_completed(futures):
            ident, count = fut.result()
            if count >= 0:
                done += 1
                if done == 1 or done % 10 == 0 or done == len(missing):
                    print(f"Archive validation cache warmup: {done}/{len(missing)}", flush=True)
                    if _ARCHIVE_CACHE_FILE:
                        archive_cache_save(_ARCHIVE_CACHE_FILE)
    return done

def archive_clean_title(text):
    t = clean_ws(text)
    t = re.sub(r'^\d+\s*p\s+', '', t, flags=re.I)
    t = re.sub(r'^\(?\s*\d+\s*[\.\)]\s*', '', t)
    t = re.sub(r'\s*-\s*OSC\d{1,3}\s*$', '', t, flags=re.I)
    t = re.sub(r'\s+OSC\d{1,3}\s*$', '', t, flags=re.I)
    t = re.sub(r'\s*\((?=[^()]*?(?:OSC|KVR|One Synth Challenge))[^()]*\)\s*$', '', t, flags=re.I)
    return clean_ws(t)

def archive_extract_tracks(meta):
    files = meta.get('files', []) if isinstance(meta, dict) else []
    tracks = []
    seen = set()
    for f in files:
        if clean_ws(f.get('source')) not in ('original', 'source'):
            continue
        fmt = clean_ws(f.get('format')).lower()
        if fmt and not any(h in fmt for h in ARCHIVE_AUDIO_HINTS):
            continue
        artist = clean_ws(f.get('artist') or f.get('creator') or '')
        title = archive_clean_title(f.get('title') or '')
        if title and artist:
            artist_pat = re.escape(artist).replace(r'\ ', r'[\s_]+')
            title = clean_ws(re.sub(rf'^{artist_pat}\s*-\s*', '', title, flags=re.I))
        if not title and clean_ws(f.get('name')):
            name = clean_ws(f.get('name'))
            base = re.sub(r'\.[^.]+$', '', name)
            m = re.match(r'^\s*\d+\s+(.+?)\s*-\s*(.+?)\s*(?:OSC\d{1,3})?\s*$', base, re.I)
            if m:
                artist = artist or clean_ws(m.group(1))
                title = title or archive_clean_title(m.group(2))
        if not artist or not title:
            continue
        key = (norm_text_key(artist), norm_text_key(title))
        pair_key = norm_entry_key(f'{artist} - {title}')
        if key in seen:
            continue
        seen.add(key)
        tracks.append({
            'file_name': clean_ws(f.get('name') or ''),
            'artist': artist,
            'track': title,
            'artist_key': key[0],
            'track_key': key[1],
            'pair_key': pair_key,
            'sequence': len(tracks),
        })
    return tracks

def archive_details_url(identifier):
    return f'https://archive.org/details/{quote(identifier)}' if identifier else ''

def archive_download_url(identifier, file_name):
    return f'https://archive.org/download/{quote(identifier)}/{quote(file_name)}' if identifier and file_name else ''

def archive_track_fields(track):
    artist = clean_ws(track.get('artist') or track.get('archive_artist') or '')
    title = archive_clean_title(track.get('track') or track.get('archive_track') or '')
    if title and artist:
        artist_pat = re.escape(artist).replace(r'\ ', r'[\s_]+').replace(r'_', r'[\s_]+')
        title = clean_ws(re.sub(rf'^{artist_pat}\s*-\s*', '', title, flags=re.I))
        parts = re.split(r'\s*-\s*', title, maxsplit=1)
        if len(parts) == 2 and norm_text_key(parts[0]) == norm_text_key(artist):
            title = clean_ws(parts[1])
    file_name = str(track.get('file_name') or '')
    if file_name and (not artist or not title):
        stem = re.sub(r'\.[^.]+$', '', file_name)
        stem = re.sub(r'^\s*\d+\s*', '', stem)
        if ' - ' in stem:
            artist2, title2 = stem.split(' - ', 1)
            artist = artist or clean_ws(artist2.replace('_', ' '))
            title = title or clean_ws(title2.replace('_', ' '))
    return artist, title

def archive_track_key(track):
    artist, title = archive_track_fields(track)
    return (norm_text_key(artist), norm_text_key(title))

def archive_verify_download_url(url, timeout=15):
    if not url:
        return None
    if url in _ARCHIVE_URL_CHECK_CACHE:
        return _ARCHIVE_URL_CHECK_CACHE[url]

    def probe(req):
        with urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, 'status', None) or resp.getcode()
            return 200 <= int(code) < 400

    req = Request(url, headers={'User-Agent': f'kvrosc-scorecard-extractor/{version}', 'Accept': '*/*'}, method='HEAD')
    try:
        ok = probe(req)
        _ARCHIVE_URL_CHECK_CACHE[url] = ok
        return ok
    except HTTPError as e:
        if e.code == 404:
            _ARCHIVE_URL_CHECK_CACHE[url] = False
            return False
    except URLError:
        pass

    req = Request(url, headers={'User-Agent': f'kvrosc-scorecard-extractor/{version}', 'Accept': '*/*', 'Range': 'bytes=0-0'})
    try:
        ok = probe(req)
        _ARCHIVE_URL_CHECK_CACHE[url] = ok
        return ok
    except HTTPError as e:
        if e.code == 404:
            _ARCHIVE_URL_CHECK_CACHE[url] = False
            return False
    except URLError:
        pass

    _ARCHIVE_URL_CHECK_CACHE[url] = None
    return None

def repair_result_rows_with_archive(result_rows, archive_tracks):
    if not result_rows or not archive_tracks:
        return result_rows
    archive_pairs = {archive_track_key(t) for t in archive_tracks}
    archive_pairs = {pair for pair in archive_pairs if pair and pair[0] and pair[1]}
    if not archive_pairs:
        return result_rows
    repaired = []
    for row in result_rows:
        entry = clean_ws(row.get('entry') or '')
        current_key = (norm_text_key(row.get('artist') or ''), norm_text_key(row.get('track') or ''))
        candidates = []
        for mode in ('artist_first', 'track_first'):
            artist, track = split_entry(entry, mode)
            artist = clean_ws(artist)
            track = clean_ws(track)
            if not artist or not track:
                continue
            key = (norm_text_key(artist), norm_text_key(track))
            score = 10 if key in archive_pairs else 0
            if key == current_key:
                score += 1
            candidates.append((score, artist, track, key))
        best = max(candidates, default=None, key=lambda item: item[0])
        if best and best[0] >= 10 and best[3] != current_key:
            row = {
                **row,
                'artist': canonical_artist(best[1]),
                'artist_key': artist_key(best[1]),
                'artist_canonical': canonical_artist(best[1]),
                'track': clean_ws(best[2]),
            }
        repaired.append(row)
    return repaired

def archive_enrich_result_rows(result_rows, archive_sum, archive_detail, archive_tracks):
    archive_ident = str(archive_sum.get('archive_identifier') or '')
    matched_files = {
        str(d.get('archive_file') or '')
        for d in archive_detail
        if d.get('archive_file')
    }
    remaining_tracks = [t for t in (archive_tracks or []) if str(t.get('file_name') or '') not in matched_files]
    enriched = []
    for idx, row in enumerate(result_rows):
        d = archive_detail[idx] if idx < len(archive_detail) else {}
        archive_file = str(d.get('archive_file') or '')
        archive_title = str(d.get('archive_track') or d.get('archive_title') or '')
        archive_match_kind = str(d.get('match_kind') or '')
        expected_key = (
            norm_text_key(row.get('artist') or ''),
            norm_text_key(row.get('track') or ''),
        )
        if not archive_file and remaining_tracks:
            exact_idx = next((i for i, t in enumerate(remaining_tracks) if archive_track_key(t) == expected_key), None)
            fallback = remaining_tracks.pop(exact_idx) if exact_idx is not None else None
            if fallback:
                archive_file = str(fallback.get('file_name') or '')
                archive_title = str(fallback.get('track') or archive_title or '')
                if archive_match_kind in ('', 'MISSING'):
                    archive_match_kind = 'TRACK_MATCH'
        archive_url = archive_download_url(archive_ident, archive_file)
        live_state = archive_verify_download_url(archive_url) if (_ARCHIVE_VERIFY_LINKS and archive_url) else None
        if live_state is True:
            archive_link_state = 'verified'
        elif live_state is False:
            archive_link_state = 'missing'
        else:
            archive_link_state = 'verified' if archive_file else 'missing'
        enriched.append({
            **row,
            'url': archive_details_url(archive_ident),
            'archive_url': archive_url,
            'archive_title': archive_title,
            'archive_identifier': archive_ident,
            'archive_file': archive_file,
            'archive_match_kind': archive_match_kind,
            'archive_link_state': archive_link_state,
        })
    return enriched

def validate_against_archive(osc, synth, filename, result_rows, archive_index):
    doc = archive_index.get(osc)
    if not doc:
        return (
            {
                'osc': osc,
                'synth': synth,
                'file': filename,
                'archive_identifier': '',
                'archive_title': '',
                'archive_tracks': 0,
                'result_rows': len(result_rows),
                'exact_matches': 0,
                'swapped_matches': 0,
                'missing_rows': len(result_rows),
                'unmatched_archive_tracks': '',
                'status': 'SKIP',
                'checks': 'no archive item found',
            },
            [],
        )
    tracks = archive_get_tracks(doc['identifier'])
    archive_map = {}
    archive_entry_map = {}
    archive_pairs = []
    for t in tracks:
        artist_name, track_title = archive_track_fields(t)
        key = archive_track_key(t)
        archive_pairs.append(key)
        archive_map.setdefault(key, {**t, 'artist': artist_name or t.get('artist', ''), 'track': track_title or t.get('track', '')})
        archive_entry_map.setdefault(norm_entry_key(f'{artist_name} - {track_title}'), {**t, 'artist': artist_name or t.get('artist', ''), 'track': track_title or t.get('track', '')})
    archive_set = set(archive_pairs)
    matched_archive = set()
    exact = swapped = missing = 0
    detail = []
    for r in result_rows:
        ra = clean_ws(r.get('artist') or '')
        rt = clean_ws(r.get('track') or '')
        reentry = clean_ws(r.get('entry') or f'{ra} - {rt}')
        exact_key = (norm_text_key(ra), norm_text_key(rt))
        swapped_key = (norm_text_key(rt), norm_text_key(ra))
        entry_key = norm_entry_key(reentry)
        match_kind = 'MISSING'
        match = None
        if exact_key in archive_set:
            match_kind = 'EXACT'
            match = archive_map.get(exact_key)
            matched_archive.add(exact_key)
            exact += 1
        elif entry_key in archive_entry_map:
            match_kind = 'ENTRY'
            match = archive_entry_map.get(entry_key)
            matched_archive.add((match.get('artist_key', ''), match.get('track_key', '')))
            exact += 1
        elif swapped_key in archive_set:
            match_kind = 'SWAPPED'
            match = archive_map.get(swapped_key)
            matched_archive.add(swapped_key)
            swapped += 1
        else:
            missing += 1
        detail.append({
            'osc': osc,
            'synth': synth,
            'file': filename,
            'archive_identifier': doc.get('identifier', ''),
            'archive_title': doc.get('title', ''),
            'result_rank': r.get('rank', ''),
            'result_artist': ra,
            'result_track': rt,
            'result_entry': reentry,
            'archive_artist': match.get('artist', '') if match else '',
            'archive_track': match.get('track', '') if match else '',
            'archive_file': match.get('file_name', '') if match else '',
            'match_kind': match_kind,
        })
    unmatched = len(archive_set - matched_archive)
    checks = []
    status = 'OK'
    if len(result_rows) != len(archive_set):
        status = 'CHECK'
        checks.append(f'count mismatch results={len(result_rows)} archive={len(archive_set)}')
    if swapped:
        status = 'CHECK'
        checks.append(f'{swapped} swapped matches')
    if missing:
        status = 'CHECK'
        checks.append(f'{missing} missing rows')
    if unmatched:
        status = 'CHECK'
        checks.append(f'{unmatched} unmatched archive tracks')
    if not checks:
        checks.append('OK')
    return (
        {
            'osc': osc,
            'synth': synth,
            'file': filename,
            'archive_identifier': doc.get('identifier', ''),
            'archive_title': doc.get('title', ''),
            'archive_tracks': len(archive_set),
            'result_rows': len(result_rows),
            'exact_matches': exact,
            'swapped_matches': swapped,
            'missing_rows': missing,
            'unmatched_archive_tracks': unmatched,
            'status': status,
            'checks': '; '.join(checks),
        },
        detail,
    )

def parse_rank(x):
    t=clean(x).lower().replace('(dq)','dq').replace('d/q','dq')
    if t=='dq': return 'dq'
    m=re.match(r'(\d+)', t)
    return m.group(1) if m else ''

def split_entry(entry, mode='artist_first'):
    e=clean_ws(entry)
    e=re.sub(r'\s*-\s*_\s*-\s*', '-', e)
    e=re.sub(r'\s*[–—]\s*', '-', e)
    if ' - ' in e:
        if mode=='artist_first':
            a,t=e.split(' - ',1); return a.strip(),t.strip()
        t,a=e.rsplit(' - ',1); return a.strip(),t.strip()
    if '-' in e:
        a,t=e.split('-',1)
        a=a.strip()
        t=t.strip()
        if len(a) >= 3 and len(t) >= 2:
            return a,t
    parts = e.split()
    if len(parts) == 2 and len(parts[0]) >= 4 and len(parts[1]) >= 3:
        return parts[0].strip(), parts[1].strip()
    return '', e

def artist_key(name):
    n=MANUAL_ARTIST_ALIASES.get(clean(name), clean(name))
    k=n.lower()
    k=re.sub(r'\s+', '', k)
    k=re.sub(r'[^a-z0-9]+', '', k)
    return k or '(unknown)'

def canonical_artist(name):
    if clean(name) in MANUAL_ARTIST_ALIASES: return MANUAL_ARTIST_ALIASES[clean(name)]
    return clean_ws(name) or '(unknown)'


TXT_RESULT_LINE_RE = re.compile(
    r"^\s*(?P<rank>\d+\s*=?)(?:\s*\([^)]*\))?\s*[\.)]?\s*(?P<body>.+?)\s*(?:(?:\.\.\.| - )\s*(?P<votes>[-+0-9,\s]+)?\s*(?:=\s*(?P<points>[-+]?\d+(?:[\.,]\d+)?))?\s*)?$"
)
TXT_RESULT_LINE_RE2 = re.compile(
    r"^\s*(?P<rank>\d+\s*=?)(?:\s*\([^)]*\))?\s*[\.)]?\s*(?P<body>.+)$"
)

def parse_txt_rank(raw):
    t=clean(raw).replace(' ','')
    m=re.match(r'(\d+)', t)
    return m.group(1) if m else ''

def strip_rank_prefix(line):
    return re.sub(r'^\s*\d+\s*=?\s*(?:\([^)]*\))?\s*[\.)]?\s*', '', line).strip()

def split_txt_entry(body, mode='artist_first'):
    b=clean_ws(body)
    # remove trailing score fragments if still present
    b=re.sub(r'\s*(?:\.\.\.| - )\s*[-+0-9,\s]+\s*(?:=\s*[-+]?\d+(?:[\.,]\d+)?)?\s*$', '', b).strip()
    # "Title (Artist)" is common in OSC008+.
    m=re.match(r'^(.*?)\s*\(([^()]*)\)\s*$', b)
    if m and clean_ws(m.group(2)):
        return (clean_ws(m.group(1)), clean_ws(m.group(2))) if mode == 'artist_first' else (clean_ws(m.group(2)), clean_ws(m.group(1)))
    # Prefer long ellipsis separator removed above, then last hyphen as artist separator.
    # Also split hyphens without spaces: Artist-Track.
    b=re.sub(r'\s*-\s*_\s*-\s*', '-', b)
    b=re.sub(r'\s*[–—]\s*', '-', b)
    if ' - ' in b:
        left,right=b.rsplit(' - ',1)
        return (clean_ws(left), clean_ws(right)) if mode == 'artist_first' else (clean_ws(right), clean_ws(left))
    # Also split compact hyphens without spaces: Artist-Track.
    if '-' in b:
        left,right=b.split('-',1)
        left=clean_ws(left); right=clean_ws(right)
        if len(left) >= 3 and len(right) >= 2:
            return left, right
    parts=b.split()
    if len(parts) == 2 and len(parts[0]) >= 4 and len(parts[1]) >= 3:
        return clean_ws(parts[0]), clean_ws(parts[1])
    # Fall back: unknown artist, whole text as track.
    return '', b

def txt_final_table_like(text):
    for line in text.splitlines():
        s=clean_ws(line)
        if re.match(r'^\s*\d+\s*(?:\(\d+\)|=|\.)\s*\S', s):
            return True
    return bool(re.search(r'(?i)\bfinal positions\b|\bfinal table\b', text))

def txt_points_from_votes(votes_text, explicit_points):
    ep=to_num(explicit_points)
    if ep is not None:
        return ep
    nums=[to_num(x) for x in re.findall(r'[-+]?\d+(?:[\.,]\d+)?', clean(votes_text))]
    nums=[x for x in nums if x is not None]
    return float(sum(nums)) if nums else 0.0

def strip_txt_track_marker(text):
    t=clean_ws(text)
    t=re.sub(r'^\(\s*\d+\s*\.\s*\)\s*', '', t)
    t=re.sub(r'^\(\s*\d+\s*\)\s*', '', t)
    return clean_ws(t)

def classify_txt(path, text):
    name=Path(path).name
    if txt_final_table_like(text):
        return 'T02'
    return 'T01'

def extract_txt_results(path, text, variant):
    rows=[]
    lines=text.splitlines()
    if variant=='T01':
        mfile=FILE_RE.match(Path(path).name)
        osc_num=int(mfile.group(1)) if mfile else 0
        poll_split_mode='artist_first' if osc_num == 1 else 'track_first'
        i=0
        while i < len(lines):
            entry=clean_ws(lines[i])
            if not entry or entry.lower().startswith(('poll ended','who/what','best ','choose ','do you think')) or entry.lower().startswith('total votes'):
                i+=1; continue
            if i+1 < len(lines) and to_num(lines[i+1]) is not None:
                pts=to_num(lines[i+1]) or 0.0
                artist,track=split_entry(entry, poll_split_mode)
                if not artist and poll_split_mode != 'track_first':
                    a2,t2=split_entry(entry, 'track_first')
                    artist,track=a2,t2
                if not artist:
                    artist='(unknown)'
                rows.append({'rank':'','artist':canonical_artist(artist),'artist_key':artist_key(artist),'artist_canonical':canonical_artist(artist),'track':clean_ws(track),'entry':entry,'points':pts,'sheet':'TXT Poll','row':i+1})
                i+=3; continue
            i+=1
        # de-duplicate exact duplicate poll options, keeping the first and warning via validation duplicate ranks if needed.
        dedup=[]; seen=set()
        for r in rows:
            k=norm_entry_key(r['entry'])
            if k in seen: continue
            seen.add(k); dedup.append(r)
        dedup.sort(key=lambda r:(-float(r.get('points') or 0), clean_ws(r.get('entry'))))
        prev=None; rank=0
        for idx,r in enumerate(dedup, start=1):
            pts=float(r.get('points') or 0)
            if prev is None or pts != prev:
                rank=idx; prev=pts
            r['rank']=str(rank)
        return dedup
    # T02 final table lines. Join wrapped lines only when a row starts with rank.
    current=''
    for line in lines:
        raw=line.rstrip()
        if not raw.strip():
            if current:
                _txt_add_ranked_row(rows,current)
                current=''
            continue
        if re.match(r'^\s*\d+\s*=?\s*(?:\([^)]*\))?\s*[\.)]?', raw):
            if current:
                _txt_add_ranked_row(rows,current)
            current=raw
        elif current and ('=' not in current) and re.search(r'^[\s\d,]+=', raw):
            current += ' ' + raw.strip()
        elif current and re.search(r',\s*$', current):
            current += ' ' + raw.strip()
        else:
            # narrative; close current if present
            if current:
                _txt_add_ranked_row(rows,current)
                current=''
    if current:
        _txt_add_ranked_row(rows,current)
    # Final cleanup: keep numeric rank rows only, sort by rank but preserve ties.
    out=[]
    for r in rows:
        if parse_rank(r.get('rank')):
            out.append(r)
    out.sort(key=lambda r:int(parse_rank(r.get('rank')) or 9999))
    return out

def _txt_add_ranked_row(rows, line):
    s=clean_ws(line)
    if not re.match(r'^\d', s): return
    rank=parse_txt_rank(s)
    body=strip_rank_prefix(s)
    # Split points from final = N first.
    explicit=''
    m_eq=re.search(r'=\s*([-+]?\d+(?:[\.,]\d+)?)\s*$', body)
    if m_eq:
        explicit=m_eq.group(1)
        before=body[:m_eq.start()].rstrip()
    else:
        before=body
    # Split vote list from entry.
    votes=''
    entry_text=before
    if '...' in before:
        entry_text, votes = before.split('...',1)
    elif re.search(r'\s-\s*[-+0-9,\s]+$', before):
        entry_text, votes = re.split(r'\s-\s*', before, maxsplit=1)
    pts=txt_points_from_votes(votes, explicit)
    artist,track=split_txt_entry(entry_text, 'track_first')
    if not artist:
        artist='(unknown)'
    track=strip_txt_track_marker(track)
    rows.append({'rank':rank,'artist':canonical_artist(artist),'artist_key':artist_key(artist),'artist_canonical':canonical_artist(artist),'track':track,'entry':f'{canonical_artist(artist)} - {track}' if artist and track else clean_ws(entry_text),'points':pts,'sheet':'TXT Final Table','row':len(rows)+1})

def v01_layout(book, sheet):
    h1=clean(book.value(sheet,1,1)).lower()
    h2=clean(book.value(sheet,1,2)).lower()
    if 'entry' in h1 or ('entry' in h1 and ('score' in h2 or 'points' in h2)):
        return 'combined_entry'
    return 'track_artist_score'

def extract_txt_votes(path, text, variant, result_rows):
    votes=[]
    if variant=='T01':
        return votes, {'vote_sheet':'TXT Poll','vote_targets':len(result_rows),'vote_voters':'','vote_rows':0,'vote_cells':0,'vote_min':'','vote_max':'','vote_mode':'forum_poll_totals_only','vote_validation':'poll totals only; individual voters unavailable'}
    vals=[]
    for r in result_rows:
        # Raw voter identity is unavailable in the forum table, but the per-entry vote values are present.
        # Re-read them from source line would be more exact; use vote_count unknown and total points as aggregate proxy.
        pass
    return votes, {'vote_sheet':'TXT Final Table','vote_targets':len(result_rows),'vote_voters':'','vote_rows':0,'vote_cells':0,'vote_min':'','vote_max':'','vote_mode':'forum_final_table_totals_only','vote_validation':'forum final table; individual voter names unavailable'}

class XlsxBook:
    def __init__(self,path):
        self.path=Path(path); self.z=zipfile.ZipFile(path); self.shared=[]; self.sheets=[]; self.data={}
        self._load()
    def _load(self):
        z=self.z
        if 'xl/sharedStrings.xml' in z.namelist():
            root=ET.fromstring(z.read('xl/sharedStrings.xml'))
            for si in root.findall('a:si',NS):
                self.shared.append(''.join(t.text or '' for t in si.findall('.//a:t',NS)))
        wb=ET.fromstring(z.read('xl/workbook.xml'))
        rels=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        rid_to_target={r.attrib['Id']:r.attrib['Target'] for r in rels.findall('rel:Relationship',NS)}
        for s in wb.findall('.//a:sheet',NS):
            name=s.attrib['name']; rid=s.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
            target=rid_to_target[rid]
            target = target.lstrip('/') if target.startswith('/') else 'xl/'+target
            self.sheets.append(name)
            self.data[name]=self._read_sheet(target)
    def _read_sheet(self,target):
        root=ET.fromstring(self.z.read(target))
        cells={}; maxr=0; maxc=0
        for c in root.findall('.//a:sheetData/a:row/a:c',NS):
            ref=c.attrib.get('r',''); key=cell_key(ref)
            if not key: continue
            r,cc=key; maxr=max(maxr,r); maxc=max(maxc,cc)
            t=c.attrib.get('t'); v=c.find('a:v',NS); f=c.find('a:f',NS)
            val=None; formula=None
            if f is not None and f.text: formula='='+f.text
            if v is not None and v.text is not None:
                raw=v.text
                if t=='s':
                    try: val=self.shared[int(raw)]
                    except Exception: val=raw
                else:
                    try: val=float(raw)
                    except Exception: val=raw
            elif t=='inlineStr':
                val=''.join(tt.text or '' for tt in c.findall('.//a:t',NS))
            cells[(r,cc)]={'v':val,'f':formula}
        return {'cells':cells,'max_row':maxr,'max_col':maxc}
    def value(self,sheet,r,c,resolve=True,depth=0):
        sh=self.data.get(sheet)
        if not sh: return None
        cell=sh['cells'].get((r,c))
        if not cell: return None
        if cell.get('v') not in (None,''): return cell.get('v')
        if resolve and cell.get('f') and depth<4: return self.resolve_formula(cell['f'], sheet, depth+1)
        return None
    def formula(self,sheet,r,c):
        cell=self.data.get(sheet,{}).get('cells',{}).get((r,c))
        return cell.get('f') if cell else None
    def resolve_formula(self,f,current_sheet,depth=0):
        if not f: return None
        s=f[1:] if f.startswith('=') else f
        m=re.fullmatch(r"'([^']+)'!\$?([A-Z]+)\$?(\d+)",s)
        if not m: m=re.fullmatch(r"([^'!]+)!\$?([A-Z]+)\$?(\d+)",s)
        if m: return self.value(m.group(1), int(m.group(3)), col_to_num(m.group(2)), True, depth)
        m=re.fullmatch(r"\$?([A-Z]+)\$?(\d+)",s)
        if m: return self.value(current_sheet, int(m.group(2)), col_to_num(m.group(1)), True, depth)
        return None
    def row_values(self,sheet,r,maxc=None):
        sh=self.data[sheet]; maxc=maxc or sh['max_col']
        return [self.value(sheet,r,c) for c in range(1,maxc+1)]
    def row_formulas(self,sheet,r,maxc=None):
        sh=self.data[sheet]; maxc=maxc or sh['max_col']
        return [self.formula(sheet,r,c) or '' for c in range(1,maxc+1)]

def sheet_by_lower(book, lname):
    lname=lname.lower()
    for s in book.sheets:
        if s.lower()==lname: return s
    return None

def classify(book):
    names=book.sheets; nset=set(n.lower() for n in names)
    if len(names)==1 and names[0].lower()=='sheet1': return 'V01'
    if 'final stacking' in nset: return 'V04'
    if 'results!' in nset: return 'V05'
    if 'results' in nset and 'data' in nset and 'prizes picked' in nset: return 'V07'
    if 'results' in nset and 'data' in nset and 'prizes' in nset: return 'V08'
    if 'results' in nset and 'data' in nset: return 'V06'
    if 'total scores' in nset and 'voting results' in nset and ('analysis' in nset or 'raw values' in nset): return 'V03'
    if 'total scores' in nset and 'voting results' in nset: return 'V02'
    if ('scores' in nset and 'results' in nset) or ('super star results' in nset): return 'V09'
    return 'V99'

def add_result(rows, rank, entry, points, sheet, row, mode='artist_first'):
    pts=to_num(points)
    if pts is None:
        m=re.search(r'[-+]?\d+(?:[\.,]\d+)?', clean(points))
        if m: pts=float(m.group(0).replace(',','.'))
    ent=clean_ws(entry)
    if not ent or pts is None: return
    artist,track=split_entry(ent, mode)
    rows.append({'rank': parse_rank(rank) or str(len(rows)+1), 'artist': artist, 'artist_key':artist_key(artist), 'artist_canonical':canonical_artist(artist), 'track': track, 'entry': ent, 'points': pts, 'sheet': sheet, 'row': row})

def result_header_row(book, sheet):
    for r in range(1, min(book.data[sheet]['max_row'], 6) + 1):
        vals = [clean(book.value(sheet, r, c)).lower() for c in (1, 2, 3)]
        joined = ' | '.join(vals)
        if any(tok in joined for tok in ('name', 'entry', 'points', 'score')):
            return r
    return 1

def extract_results(book, variant):
    rows=[]
    if variant=='V01':
        sh=book.sheets[0]
        layout=v01_layout(book, sh)
        if layout=='combined_entry':
            for r in range(2, book.data[sh]['max_row']+1):
                entry=book.value(sh,r,1); pts=book.value(sh,r,2)
                if clean(entry) and to_num(pts) is not None:
                    artist,track=split_entry(clean_ws(entry), 'track_first')
                    if not artist:
                        artist='(unknown)'
                    rows.append({'rank':str(len(rows)+1),'artist':clean_ws(artist),'artist_key':artist_key(artist),'artist_canonical':canonical_artist(artist),'track':clean_ws(track),'entry':clean_ws(entry),'points':to_num(pts),'sheet':sh,'row':r})
        else:
            for r in range(2, book.data[sh]['max_row']+1):
                track=book.value(sh,r,1); artist=book.value(sh,r,2); pts=book.value(sh,r,3)
                if clean(track) and clean(artist) and to_num(pts) is not None:
                    rows.append({'rank':str(len(rows)+1),'artist':clean_ws(artist),'artist_key':artist_key(artist),'artist_canonical':canonical_artist(artist),'track':clean_ws(track),'entry':f'{clean_ws(artist)} - {clean_ws(track)}','points':to_num(pts),'sheet':sh,'row':r})
    elif variant in ('V02','V03','V04'):
        sh=sheet_by_lower(book,'total scores') or 'TOTAL SCORES'
        maxr=book.data[sh]['max_row']
        header_row = result_header_row(book, sh)
        row1 = [clean(book.value(sh, 1, c)).lower() for c in (1, 2, 3)]
        data_starts_at_one = (
            parse_rank(row1[0]) != ''
            or (row1[0] and to_num(row1[1]) is not None and row1[0] not in {'entry', 'rank', 'pos', 'place', 'final results', 'final results:'})
        )
        start_row = 1 if data_starts_at_one else header_row + 1
        for r in range(start_row,maxr+1):
            c1 = clean(book.value(sh,r,1))
            c2 = clean(book.value(sh,r,2))
            c3 = clean(book.value(sh,r,3))
            if not c1 and not c2 and not c3:
                continue
            if c1.lower().startswith('final results'):
                continue
            if parse_rank(c1) or c1.lower() in ('dq','(dq)'):
                if c2 and c3:
                    add_result(rows, c1, c2, c3, sh, r, mode='artist_first')
                    continue
                if c2 and to_num(c2) is not None:
                    # Rare malformed rows where the rank column is present but the entry is not.
                    add_result(rows, c1, c2, '', sh, r, mode='artist_first')
                    continue
            if c1 and to_num(c2) is not None and not c3:
                # entry | score
                add_result(rows, str(len(rows)+1), c1, c2, sh, r, mode='artist_first')
                continue
            if c2 and to_num(c3) is not None:
                # Some transitional sheets store a leading label in col1.
                add_result(rows, str(len(rows)+1), c2, c3, sh, r, mode='artist_first')
                continue
        for i,row in enumerate(rows,1):
            if not parse_rank(row.get('rank')):
                row['rank']=str(i)
    elif variant=='V09':
        sh=sheet_by_lower(book,'super star results') or sheet_by_lower(book,'results')
        if sh:
            maxr=book.data[sh]['max_row']
            for r in range(2,maxr+1):
                a,bv,cv=book.value(sh,r,1),book.value(sh,r,2),book.value(sh,r,3)
                if to_num(cv) is not None or re.search(r'\d', clean(cv)):
                    add_result(rows,a,bv,cv,sh,r,mode='artist_first')
                else:
                    add_result(rows,'',a,bv,sh,r,mode='artist_first')
    elif variant in ('V05','V06','V07','V08'):
        sh=sheet_by_lower(book,'results!') if variant=='V05' else sheet_by_lower(book,'results')
        maxr=book.data[sh]['max_row']
        for r in range(2,maxr+1):
            rank=book.value(sh,r,1); entry=book.value(sh,r,2); pts=book.value(sh,r,3)
            if to_num(pts) is None and not re.search(r'\d', clean(pts)):
                pts=book.value(sh,r,10)
            if parse_rank(rank) or clean(rank).lower() in ('dq','(dq)'):
                add_result(rows, rank, entry, pts, sh, r, mode='artist_first')
    return rows

def vote_profile(book, variant):
    sheet=None
    if variant in ('V02','V03','V04'): sheet=sheet_by_lower(book,'voting results')
    if variant=='V03':
        sheet=sheet_by_lower(book,'raw values') or sheet_by_lower(book,'voting results')
    elif variant=='V05': sheet=next((s for s in book.sheets if re.fullmatch(r'OSC\d+',s,re.I)), None)
    elif variant in ('V06','V07','V08'): sheet=sheet_by_lower(book,'data')
    elif variant=='V09': sheet=sheet_by_lower(book,'scores') or sheet_by_lower(book,'nerdy, math stuff') or next((s for s in book.sheets if re.fullmatch(r'OSC\d+',s,re.I)), None)
    elif variant=='V01': sheet=book.sheets[0]
    if not sheet or sheet not in book.data: return {'vote_sheet':'','vote_min':'','vote_max':'','vote_cells':0,'filled_ratio':'','rule_guess':''}
    sh=book.data[sheet]; vals=[]; filled=0; total=0
    start_col=4 if variant=='V01' and v01_layout(book, sheet)=='track_artist_score' else (3 if variant=='V01' else 2)
    for r in range(2, min(sh['max_row'],220)+1):
        for c in range(start_col, min(sh['max_col'],220)+1):
            v=to_num(book.value(sheet,r,c))
            if v is not None:
                vals.append(v); filled+=1
            total+=1
    if not vals: return {'vote_sheet':sheet,'vote_min':'','vote_max':'','vote_cells':0,'filled_ratio':'','rule_guess':''}
    vmin,vmax=min(vals),max(vals)
    ratio=round(filled/total,3) if total else ''
    if vmax<=5 and ratio>0.25: rg='likely 1-5 full/mostly-full voting'
    elif vmax>=10 and ratio<0.15: rg='likely sparse 1-10 voting'
    elif vmax>=10: rg='likely 1-10 or ranked/stacking voting'
    else: rg='unknown/low-density 1-5'
    return {'vote_sheet':sheet,'vote_min':vmin,'vote_max':vmax,'vote_cells':len(vals),'filled_ratio':ratio,'rule_guess':rg}

def validate_file(osc, rows):
    numeric=[]; ranks=[]; warnings=[]
    for r in rows:
        pr=parse_rank(r.get('rank'))
        if pr and pr!='dq':
            ranks.append(int(pr))
            numeric.append(r)
    if not rows: warnings.append('no results')
    if ranks:
        if min(ranks)!=1: warnings.append('rank does not start at 1')
        if len(ranks)!=len(set(ranks)): warnings.append('duplicate ranks')
        # gaps are OK for ties only if duplicate ranks; otherwise warn
        if len(ranks)==len(set(ranks)) and sorted(ranks)!=list(range(min(ranks),max(ranks)+1)):
            warnings.append('rank gaps')
    else: warnings.append('no numeric ranks')
    if numeric and any(not r['artist'] for r in numeric[:10]): warnings.append('artist split suspicious in top rows')
    return '; '.join(warnings)

def safe_cell(v):
    s=clean_ws(v)
    if len(s)>80: s=s[:77]+'...'
    return s.replace('|','/')

def dump_sample(book, variant, outdir, filename):
    d=outdir/'variant_samples'; d.mkdir(parents=True,exist_ok=True)
    stem=f"{variant}_{Path(filename).stem}.md"
    with open(d/stem,'w',encoding='utf-8') as f:
        f.write(f'# {filename} / {variant}\n\nSheets: '+', '.join(book.sheets)+'\n\n')
        for sh in book.sheets[:5]:
            maxr=min(book.data[sh]['max_row'],16); maxc=min(book.data[sh]['max_col'],10)
            f.write(f'## Sheet: {sh}\n\n')
            f.write('| row | '+' | '.join(num_to_col(c) for c in range(1,maxc+1))+' |\n')
            f.write('|---|'+'|'.join(['---']*maxc)+'|\n')
            for r in range(1,maxr+1):
                vals=[safe_cell(book.value(sh,r,c)) for c in range(1,maxc+1)]
                if any(vals): f.write(f'| {r} | '+' | '.join(vals)+' |\n')
            # formula hints
            formulas=[]
            for r in range(1,min(book.data[sh]['max_row'],40)+1):
                for c in range(1,min(book.data[sh]['max_col'],12)+1):
                    fo=book.formula(sh,r,c)
                    if fo: formulas.append(f'{num_to_col(c)}{r}={fo}')
                    if len(formulas)>=8: break
                if len(formulas)>=8: break
            if formulas:
                f.write('\nFormula examples:\n\n')
                for fo in formulas: f.write(f'- `{fo}`\n')
            f.write('\n')


def target_columns(book, sheet):
    """Return candidate target/track columns from a matrix sheet.
    Uses row 1 headers and requires numeric vote data below.
    """
    sh=book.data[sheet]
    cols=[]
    stop_blank_run=0
    for c in range(2, sh['max_col']+1):
        h=clean_ws(book.value(sheet,1,c))
        if not h:
            stop_blank_run += 1
            if stop_blank_run >= 5 and c>20:
                break
            continue
        stop_blank_run=0
        hl=h.lower()
        if hl in ('score','points','pts','average','avg','sum','total') or 'comment' in hl or 'timestamp' in hl or 'generosity' in hl or 'count' in hl or 'score' in hl or 'percent' in hl or re.fullmatch(r"[1-5]'?s", hl) or hl.endswith('score*'):
            continue
        # require at least one numeric value in plausible voter rows
        nums=0
        for r in range(2, min(sh['max_row'],260)+1):
            if to_num(book.value(sheet,r,c)) is not None:
                nums += 1
                if nums>=1: break
        if nums:
            cols.append((c,h))
    return cols

def voter_rows(book, sheet, cols, min_numeric=1):
    """Return plausible voter row numbers: col A non-empty and has numeric votes."""
    sh=book.data[sheet]
    rows=[]
    colnums=[c for c,h in cols]
    for r in range(2, sh['max_row']+1):
        voter=clean_ws(book.value(sheet,r,1))
        if not voter:
            continue
        vl=voter.lower()
        if (vl in ('sum','total','average','avg','score','points','pts') or 'count' in vl or 'average' in vl or 'score' in vl or 'point' in vl or vl.startswith('#') or vl in ('5','4','3','2','1') or re.fullmatch(r"[1-5]'?s", vl)):
            # once sheet summary rows start, later rows are not voters
            if r > 3:
                break
            continue
        nums=[]
        formulas=0
        for c in colnums:
            if book.formula(sheet,r,c): formulas+=1
            v=to_num(book.value(sheet,r,c))
            if v is not None: nums.append(v)
        if len(nums)>=min_numeric and formulas < max(2, len(colnums)//2):
            rows.append(r)
    return rows

def vote_sheet_for_variant(book, variant):
    if variant in ('V02','V03','V04'):
        if variant=='V03':
            return sheet_by_lower(book,'raw values') or sheet_by_lower(book,'voting results')
        return sheet_by_lower(book,'voting results')
    if variant=='V05':
        return next((s for s in book.sheets if re.fullmatch(r'OSC\d+',s,re.I)), None) or sheet_by_lower(book,'data')
    if variant in ('V06','V07','V08'):
        return sheet_by_lower(book,'data')
    if variant=='V09':
        return sheet_by_lower(book,'scores') or sheet_by_lower(book,'nerdy, math stuff') or next((s for s in book.sheets if re.fullmatch(r'OSC\d+',s,re.I)), None)
    if variant=='V01':
        return book.sheets[0]
    return None

def extract_votes(book, variant, result_rows):
    """Extract normalized raw votes. Returns (votes, stats)."""
    votes=[]
    sheet=vote_sheet_for_variant(book, variant)
    if not sheet or sheet not in book.data:
        return votes, {'vote_sheet':'','vote_targets':0,'vote_voters':0,'vote_rows':0,'vote_cells':0,'vote_min':'','vote_max':'','vote_mode':'none','vote_validation':'no vote sheet'}
    sh=book.data[sheet]
    if variant=='V01':
        layout=v01_layout(book, sheet)
        # V01 appears in two layouts:
        # - track/artist/score + anonymous voters from D onward
        # - combined entry/score + anonymous voters from C onward
        vals=[]; targets=0; anon_cols=[]
        vote_start_col=4 if layout=='track_artist_score' else 3
        for c in range(vote_start_col, sh['max_col']+1):
            if any(to_num(book.value(sheet,r,c)) is not None for r in range(2,sh['max_row']+1)):
                anon_cols.append(c)
        for r in range(2, sh['max_row']+1):
            if layout=='track_artist_score':
                track=clean_ws(book.value(sheet,r,1)); artist=clean_ws(book.value(sheet,r,2))
                if not track or not artist: continue
                entry=f'{artist} - {track}'
                target_artist=artist
                target_track=track
            else:
                entry=clean_ws(book.value(sheet,r,1))
                score=book.value(sheet,r,2)
                if not entry or to_num(score) is None: continue
                target_artist, target_track = split_entry(entry, 'track_first')
                if not target_artist:
                    target_artist='(unknown)'
                if not target_track:
                    target_track=entry
            targets+=1
            for c in anon_cols:
                v=to_num(book.value(sheet,r,c))
                if v is None: continue
                vals.append(v)
                votes.append({'vote_sheet':sheet,'voter':f'anonymous_{num_to_col(c)}','target_entry':entry,'target_artist':target_artist,'target_track':target_track,'vote_value':v,'vote_points':v,'vote_rank':'','source_row':r,'source_col':num_to_col(c)})
        return votes, vote_stats_from_values(sheet, targets, len(anon_cols), vals, 'row_anonymous_points')

    cols=target_columns(book, sheet)
    min_numeric = 1 if variant=='V02' else max(1, min(3, len(cols)//10))
    rows=voter_rows(book, sheet, cols, min_numeric=min_numeric)
    vals=[]
    vote_mode='ranked_ballot' if variant=='V04' else 'points_matrix'
    n_targets=len(cols)
    for r in rows:
        voter=clean_ws(book.value(sheet,r,1))
        for c,entry in cols:
            v=to_num(book.value(sheet,r,c))
            if v is None: continue
            vals.append(v)
            artist,track=split_entry(entry, 'artist_first')
            vrank=''; vpoints=v
            if variant=='V04':
                vrank=v
                # ranked ballot conversion used by the sheet family: high score for low rank.
                vpoints=max(0, (n_targets + 1) - v)
            votes.append({'vote_sheet':sheet,'voter':voter,'target_entry':entry,'target_artist':artist,'target_track':track,'vote_value':v,'vote_points':vpoints,'vote_rank':vrank,'source_row':r,'source_col':num_to_col(c)})
    stats=vote_stats_from_values(sheet, len(cols), len(rows), vals, vote_mode)
    stats['vote_validation']=validate_vote_totals(book, variant, sheet, cols, rows, result_rows)
    return votes, stats

def vote_stats_from_values(sheet, targets, voters, vals, mode):
    if vals:
        return {'vote_sheet':sheet,'vote_targets':targets,'vote_voters':voters,'vote_rows':voters,'vote_cells':len(vals),'vote_min':min(vals),'vote_max':max(vals),'vote_mode':mode,'vote_validation':''}
    return {'vote_sheet':sheet,'vote_targets':targets,'vote_voters':voters,'vote_rows':voters,'vote_cells':0,'vote_min':'','vote_max':'','vote_mode':mode,'vote_validation':'no numeric vote cells'}

def norm_entry_key(s):
    return re.sub(r'[^a-z0-9]+','',clean_ws(s).lower())

def validate_vote_totals(book, variant, sheet, cols, rows, result_rows):
    """Compare vote column sums with extracted result points where possible."""
    if not cols or not rows or not result_rows:
        return 'not enough data for validation'
    # Build result point lookup by entry key, allow fuzzy containment later.
    result_by_key={norm_entry_key(r.get('entry','')): to_num(r.get('points')) for r in result_rows if to_num(r.get('points')) is not None}
    mismatches=0; checked=0; examples=[]
    n_targets=len(cols)
    for c,entry in cols:
        total=0.0
        for r in rows:
            v=to_num(book.value(sheet,r,c))
            if v is None: continue
            if variant=='V04': total += max(0, (n_targets+1)-v)
            else: total += v
        key=norm_entry_key(entry)
        expected=result_by_key.get(key)
        if expected is None:
            # fallback: try target key contained in result key or vice versa
            for rk,rv in result_by_key.items():
                if key and rk and (key in rk or rk in key):
                    expected=rv; break
        if expected is None:
            continue
        checked += 1
        if abs(total-expected) > 0.01:
            mismatches += 1
            if len(examples)<3: examples.append(f'{entry}: votes={total:g} result={expected:g}')
    if checked==0: return 'no matching result entries for vote-total validation'
    if mismatches==0: return f'OK {checked}/{len(cols)} totals matched'
    return f'{mismatches}/{checked} total mismatches; ' + '; '.join(examples)


def write_csv(path, rows, fields=None):
    path.parent.mkdir(parents=True,exist_ok=True)
    if fields is None:
        fields=list(rows[0].keys()) if rows else []
    with open(path,'w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fields); w.writeheader(); w.writerows(rows)


def infer_rule_epoch(row):
    """Summarize the observed voting system from extracted numeric votes."""
    try:
        vmax = float(row.get('vote_max')) if row.get('vote_max') not in ('', None) else None
        vmin = float(row.get('vote_min')) if row.get('vote_min') not in ('', None) else None
        targets = int(float(row.get('vote_targets') or 0))
        voters = int(float(row.get('vote_voters') or 0))
        cells = int(float(row.get('vote_cells') or 0))
    except Exception:
        return 'unknown'
    mode = row.get('vote_mode','')
    if not cells:
        return 'no raw votes extracted'
    if mode == 'ranked_ballot':
        return 'ranked ballot / stacking'
    density = (cells / (targets * voters)) if targets and voters else 0
    if vmax is not None and vmax <= 5 and density > 0.70:
        return '1-5 full voting, probably all/mostly all tracks required'
    if vmax is not None and vmax <= 5:
        return '1-5 partial/low-density voting'
    if vmax is not None and vmax >= 10 and density < 0.45:
        return 'sparse 1-10 voting, not every track required'
    if vmax is not None and vmax >= 10:
        return '1-10 voting / transitional density'
    return 'unknown'


def result_rows_numeric(rows):
    out=[]
    for r in rows:
        pr=parse_rank(r.get('rank'))
        if pr and pr!='dq' and to_num(r.get('points')) is not None:
            out.append(r)
    return out

def build_best_key_map(result_rows, vote_target_keys):
    """Map vote target keys to result row keys. Exact first, then conservative containment."""
    result_keys=[norm_entry_key(r.get('entry','')) for r in result_rows]
    result_key_set=set(result_keys)
    mapping={}
    used=set()
    for vk in vote_target_keys:
        if vk in result_key_set and vk not in used:
            mapping[vk]=vk; used.add(vk)
    for vk in vote_target_keys:
        if vk in mapping or not vk:
            continue
        candidates=[]
        for rk in result_keys:
            if not rk or rk in used:
                continue
            # Conservative: at least 8 chars, one contains the other, or strong overlap.
            if (len(vk)>=8 and len(rk)>=8 and (vk in rk or rk in vk)):
                candidates.append(rk)
        if len(candidates)==1:
            mapping[vk]=candidates[0]; used.add(candidates[0])
    return mapping

def reconcile_file(osc, synth, filename, variant, result_rows, vote_rows, vote_stats):
    """Hard reconciliation between final ranking and extracted votes.
    Returns (summary_row, detail_rows).
    """
    rnum=result_rows_numeric(result_rows)
    result_by_key={norm_entry_key(r.get('entry','')): r for r in rnum if norm_entry_key(r.get('entry',''))}
    vote_sum=defaultdict(float); vote_count=Counter(); vote_label={}
    for v in vote_rows:
        k=norm_entry_key(v.get('target_entry',''))
        if not k:
            continue
        vote_sum[k]+=float(to_num(v.get('vote_points')) or 0)
        vote_count[k]+=1
        vote_label.setdefault(k, clean_ws(v.get('target_entry','')))
    result_count=len(rnum)
    vote_targets=len(vote_sum)
    if variant in ('T01', 'T02') and vote_targets == 0:
        result_total=sum(float(to_num(r.get('points')) or 0) for r in rnum)
        summary={'osc':osc,'synth':synth,'file':filename,'template_version':variant,'result_rows':result_count,'vote_targets':0,'vote_rows':len(vote_rows),'checked_targets':0,'missing_targets':0,'point_mismatches':0,'orphan_vote_targets':0,'result_points_total':round(result_total,6),'matched_vote_points_total':0.0,'max_abs_delta':0.0,'status':'CHECK','checks':'TXT scorecard has no raw vote matrix; totals-only validation'}
        return summary, []
    mapping=build_best_key_map(rnum, vote_sum.keys())
    # Inverse mapping from result key to all vote keys.
    result_to_vote=defaultdict(list)
    for vk,rk in mapping.items():
        result_to_vote[rk].append(vk)
    detail=[]; mismatches=0; missing=0; max_delta=0.0; checked=0
    result_total=0.0; vote_total_matched=0.0
    for rk,r in result_by_key.items():
        expected=float(to_num(r.get('points')) or 0)
        result_total += expected
        vks=result_to_vote.get(rk, [])
        observed=sum(vote_sum[vk] for vk in vks)
        delta=observed-expected
        if not vks:
            missing += 1
            status='MISSING_VOTE_TARGET'
        elif abs(delta) <= 0.01:
            checked += 1
            status='OK'
        else:
            checked += 1; mismatches += 1; max_delta=max(max_delta, abs(delta)); status='POINT_MISMATCH'
        if vks:
            vote_total_matched += observed
        detail.append({'osc':osc,'synth':synth,'file':filename,'template_version':variant,'result_rank':r.get('rank'),'result_entry':r.get('entry'),'result_points':expected,'vote_target_entry':' | '.join(vote_label.get(vk,vk) for vk in vks),'vote_points_sum':observed if vks else '','vote_count':sum(vote_count[vk] for vk in vks),'delta':round(delta,6) if vks else '', 'status':status})
    orphan_keys=[vk for vk in vote_sum.keys() if vk not in mapping]
    for vk in orphan_keys[:1000]:
        detail.append({'osc':osc,'synth':synth,'file':filename,'template_version':variant,'result_rank':'','result_entry':'','result_points':'','vote_target_entry':vote_label.get(vk,vk),'vote_points_sum':round(vote_sum[vk],6),'vote_count':vote_count[vk],'delta':'','status':'ORPHAN_VOTE_TARGET'})
    status='OK'
    checks=[]
    if not result_count:
        status='FAIL'; checks.append('no numeric result rows')
    if not vote_targets:
        if variant in ('T01', 'T02'):
            status='CHECK' if status == 'OK' else status
            checks.append('TXT scorecard has no raw vote matrix; totals-only validation')
        else:
            status='FAIL'; checks.append('no vote targets')
    if missing:
        status='FAIL'; checks.append(f'{missing} result rows missing vote target')
    if mismatches:
        status='FAIL'; checks.append(f'{mismatches} point mismatches')
    if orphan_keys:
        # Orphans are less severe for old sparse templates with helper columns, but still a check.
        status='CHECK' if status=='OK' else status
        checks.append(f'{len(orphan_keys)} orphan vote targets')
    # hard count relation: vote targets should be close to result rows; tolerate DQ/non-voted extras.
    if result_count and vote_targets and abs(vote_targets-result_count) > max(2, int(result_count*0.15)):
        status='FAIL' if status=='OK' else status
        checks.append(f'target count mismatch results={result_count} vote_targets={vote_targets}')
    summary={'osc':osc,'synth':synth,'file':filename,'template_version':variant,'result_rows':result_count,'vote_targets':vote_targets,'vote_rows':len(vote_rows),'checked_targets':checked,'missing_targets':missing,'point_mismatches':mismatches,'orphan_vote_targets':len(orphan_keys),'result_points_total':round(result_total,6),'matched_vote_points_total':round(vote_total_matched,6),'max_abs_delta':round(max_delta,6),'status':status,'checks':'; '.join(checks) or 'OK'}
    return summary, detail


def write_hall_of_fame_outputs_from_results(results, out):
    """Write the canonical Hall-of-Fame CSV set from normalized full result rows.
    The full source of truth remains normalized_results.csv; these files are derived views.
    """
    parsed=[]
    for r in results:
        pr=parse_rank(r.get('rank'))
        if not pr or pr == 'dq':
            continue
        p=dict(r)
        p['osc']=int(p.get('osc') or 0)
        p['rank']=int(float(pr))
        p['points_float']=to_num(p.get('points'))
        if not p.get('artist_key'):
            p['artist_key']=artist_key(p.get('artist'))
        if not p.get('artist'):
            p['artist']=canonical_artist(p.get('artist_canonical') or p.get('artist_key'))
        parsed.append(p)
    top5=[r for r in parsed if 1 <= int(r['rank']) <= 5]
    podium=[r for r in top5 if int(r['rank']) <= 3]
    winners=[r for r in top5 if int(r['rank']) == 1]
    by_artist={}
    detail_by_artist=defaultdict(list)
    ranks_by_artist=defaultdict(list)
    oscs_by_artist=defaultdict(set)
    top5_oscs_by_artist=defaultdict(set)
    for r in parsed:
        oscs_by_artist[r['artist_key']].add(int(r['osc']))
    for r in top5:
        key=r['artist_key']
        rec=by_artist.setdefault(key, {'artist_key':key,'artist':r.get('artist') or key,'gold':0,'silver':0,'bronze':0,'fourth':0,'fifth':0,'top5_total':0,'podium_total':0,'first_osc':999999,'last_osc':0,'total_points_top5':0.0})
        rk=int(r['rank']); osc=int(r['osc'])
        rec['top5_total']+=1
        if rk==1: rec['gold']+=1
        elif rk==2: rec['silver']+=1
        elif rk==3: rec['bronze']+=1
        elif rk==4: rec['fourth']+=1
        elif rk==5: rec['fifth']+=1
        if rk<=3: rec['podium_total']+=1
        rec['first_osc']=min(rec['first_osc'],osc); rec['last_osc']=max(rec['last_osc'],osc)
        if r.get('points_float') is not None: rec['total_points_top5']+=float(r['points_float'])
        ranks_by_artist[key].append(rk); top5_oscs_by_artist[key].add(osc)
        track=clean_ws(r.get('track') or r.get('entry') or '')
        synth=clean_ws(r.get('synth') or '')
        pts=r.get('points') or ''
        detail_by_artist[key].append(f"OSC{osc} #{rk} {synth}: {track}" + (f" ({pts})" if pts!='' else ''))
    overall=[]
    for key, rec in by_artist.items():
        part=len(oscs_by_artist.get(key,set()))
        top5_count=len(top5_oscs_by_artist.get(key,set()))
        avg=sum(ranks_by_artist[key])/len(ranks_by_artist[key]) if ranks_by_artist[key] else ''
        rec['participations']=part
        rec['top5_osc_count']=top5_count
        rec['top5_rate']=round(top5_count/part,4) if part else ''
        rec['avg_top5_rank']=round(avg,3) if avg!='' else ''
        rec['total_points_top5']=round(rec['total_points_top5'],3)
        rec['placements']=' | '.join(detail_by_artist[key])
        overall.append(rec)
    overall.sort(key=lambda r:(safe_int(r.get('gold')),safe_int(r.get('silver')),safe_int(r.get('bronze')),safe_int(r.get('fourth')),safe_int(r.get('fifth')),safe_int(r.get('last_osc')),str(r.get('artist') or '').casefold()), reverse=True)
    for i,r in enumerate(overall,1): r['hof_rank']=i
    top5_details=sorted(top5, key=lambda r:(int(r['osc']), int(r['rank'])))
    def result_view(rows):
        return [{'osc':r.get('osc'),'synth':r.get('synth'),'rank':r.get('rank'),'artist':r.get('artist'),'track':r.get('track'),'points':r.get('points'),'template_version':r.get('template_version'),'source_file':r.get('source_file'),'artist_key':r.get('artist_key')} for r in rows]
    top5_out=result_view(top5_details)
    winners_out=[r for r in top5_out if safe_int(r.get('rank'))==1]
    podium_out=[r for r in top5_out if 1 <= safe_int(r.get('rank')) <= 3]
    part_rows=[]
    for key in sorted(oscs_by_artist.keys()):
        names=[r.get('artist') for r in parsed if r.get('artist_key')==key and r.get('artist')]
        name=Counter(names).most_common(1)[0][0] if names else key
        oscs=sorted(oscs_by_artist[key])
        part_rows.append({'artist_key':key,'artist':name,'participations':len(oscs),'first_osc':min(oscs) if oscs else '', 'last_osc':max(oscs) if oscs else '', 'osc_list':','.join(str(x) for x in oscs)})
    part_rows.sort(key=lambda r:(safe_int(r['participations']), safe_int(r['last_osc'])), reverse=True)
    streak_rows=[]
    for key, oscs_set in oscs_by_artist.items():
        oscs=sorted(oscs_set)
        best_len=cur_len=0; best_start=best_end=cur_start=cur_end=None; prev=None
        for osc in oscs:
            if prev is None or osc != prev+1: cur_start=cur_end=osc; cur_len=1
            else: cur_end=osc; cur_len+=1
            if cur_len>best_len: best_len,best_start,best_end=cur_len,cur_start,cur_end
            prev=osc
        t5=sorted(top5_oscs_by_artist.get(key,set()))
        best_t5=cur_t5=0; t5_start=t5_end=cur_s=cur_e=None; prev=None
        for osc in t5:
            if prev is None or osc != prev+1: cur_s=cur_e=osc; cur_t5=1
            else: cur_e=osc; cur_t5+=1
            if cur_t5>best_t5: best_t5,t5_start,t5_end=cur_t5,cur_s,cur_e
            prev=osc
        name=next((r.get('artist') for r in parsed if r.get('artist_key')==key), key)
        streak_rows.append({'artist_key':key,'artist':name,'best_participation_streak':best_len,'participation_streak_from':best_start or '', 'participation_streak_to':best_end or '', 'best_top5_streak':best_t5,'top5_streak_from':t5_start or '', 'top5_streak_to':t5_end or ''})
    streak_rows.sort(key=lambda r:(safe_int(r['best_top5_streak']), safe_int(r['best_participation_streak'])), reverse=True)
    fields_overall=['hof_rank','artist','gold','silver','bronze','fourth','fifth','top5_total','podium_total','participations','top5_osc_count','top5_rate','avg_top5_rank','first_osc','last_osc','total_points_top5','placements','artist_key']
    write_csv(out/'hall_of_fame_overall.csv', overall, fields_overall)
    write_csv(out/'hall_of_fame_top5_details.csv', top5_out, ['osc','synth','rank','artist','track','points','template_version','source_file','artist_key'])
    write_csv(out/'hall_of_fame_winners.csv', winners_out, ['osc','synth','rank','artist','track','points','template_version','source_file','artist_key'])
    write_csv(out/'hall_of_fame_podiums.csv', podium_out, ['osc','synth','rank','artist','track','points','template_version','source_file','artist_key'])
    write_csv(out/'hall_of_fame_participations.csv', part_rows, ['artist','participations','first_osc','last_osc','osc_list','artist_key'])
    write_csv(out/'hall_of_fame_streaks.csv', streak_rows, ['artist','best_participation_streak','participation_streak_from','participation_streak_to','best_top5_streak','top5_streak_from','top5_streak_to','artist_key'])
    summary={'source':'normalized_results.csv','result_rows':len(parsed),'top5_rows':len(top5),'podium_rows':len(podium),'winner_rows':len(winners),'artists_with_top5':len(overall),'artists_total':len(part_rows),'osc_min':min([r['osc'] for r in parsed], default=None),'osc_max':max([r['osc'] for r in parsed], default=None),'top20':overall[:20]}
    write_json(out/'hall_of_fame_summary.json', summary)

def make_cockpit_payload(results, votes, inv, summary, winners, top5, medal_rows_canon, alias, diags):
    """Return compact JSON data that the Cockpit can consume without parsing XLSX."""
    by_osc = defaultdict(lambda: {'results': [], 'top5': [], 'winner': None, 'vote_stats': None})
    for r in results:
        item = {
            'rank': r.get('rank'), 'artist': r.get('artist'), 'artist_key': r.get('artist_key'),
            'artist_canonical': r.get('artist_canonical'), 'track': r.get('track'),
            'entry': r.get('entry'), 'points': r.get('points'), 'source_file': r.get('source_file'),
            'template_version': r.get('template_version'), 'template_number': r.get('template_number')
        }
        by_osc[int(r['osc'])]['results'].append(item)
    for r in top5:
        by_osc[int(r['osc'])]['top5'].append({k:r.get(k) for k in ['rank','artist','artist_canonical','track','entry','points','source_file','template_version','template_number']})
    for r in winners:
        by_osc[int(r['osc'])]['winner'] = {k:r.get(k) for k in ['rank','artist','artist_canonical','track','entry','points','source_file','template_version','template_number']}
    for r in inv:
        try: osc=int(r.get('osc'))
        except Exception: continue
        by_osc[osc]['synth']=r.get('synth')
        by_osc[osc]['file']=r.get('file')
        by_osc[osc]['template_version']=r.get('template_version')
        by_osc[osc]['template_number']=r.get('template_number')
        by_osc[osc]['vote_stats']={k:r.get(k,'') for k in ['vote_sheet','vote_targets','vote_voters','vote_cells','vote_min','vote_max','vote_mode','vote_validation']}
        by_osc[osc]['vote_rule_guess']=infer_rule_epoch(r)
    oscs=[]
    for osc in sorted(by_osc):
        d=by_osc[osc]
        d['osc']=osc
        d['results'].sort(key=lambda x: int(float(x['rank'])) if str(x.get('rank','')).replace('.0','').isdigit() else 9999)
        oscs.append(d)
    return {
        'schema':'kvrosc_scorecards_cockpit_v1',
        'generated_by':f'scorecard_vote_extractor.py v{version}',
        'summary':{'osc_count':len(oscs),'result_rows':len(results),'vote_rows':len(votes),'diagnostics':len(diags),'variants':summary},
        'oscs':oscs,
        'hall_of_fame':medal_rows_canon,
        'artist_alias_candidates':alias[:250]
    }

def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path,'w',encoding='utf-8') as f:
        json.dump(data,f,ensure_ascii=False,indent=2)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--scorecards',default='scorecards')
    ap.add_argument('--out',default='dist/tmp/hof')
    ap.add_argument('--dump-samples',action='store_true',default=False)
    ap.add_argument('--ignore-identical-duplicates',action='store_true',default=True)
    ap.add_argument('--no-archive-validate',action='store_true',help='Skip archive.org validation')
    ap.add_argument('--archive-cache',default='dist/archive-cache/archive_validation_cache.json',help='Local cache for archive.org index and track metadata')
    ap.add_argument('--refresh-archive-cache',action='store_true',help='Ignore any cached archive.org metadata and rebuild it from the network')
    ap.add_argument('--archive-workers',type=int,default=16,help='Parallel archive.org metadata fetch workers')
    ap.add_argument('--archive-prefetch-missing',action='store_true',help='Prefetch any archive metadata missing from the local cache before extraction starts')
    ap.add_argument('--verify-archive-links',action='store_true',help='Live-check archive download URLs (slow, but precise)')
    ap.add_argument('--strict', action='store_true', help='Exit with code 2 when hard validation failures remain')
    args=ap.parse_args()
    sc=Path(args.scorecards); out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    results=[]; votes=[]; inv=[]; diags=[]; hard_validation=[]; reconciliation_rows=[]; archive_validation=[]; archive_validation_details=[]; variant_examples=defaultdict(list); per_osc=defaultdict(list)
    archive_index={}
    global _ARCHIVE_CACHE_FILE, _ARCHIVE_REFRESH_CACHE, _ARCHIVE_PREFETCH_WORKERS, _ARCHIVE_VERIFY_LINKS
    _ARCHIVE_CACHE_FILE = args.archive_cache
    _ARCHIVE_REFRESH_CACHE = bool(args.refresh_archive_cache)
    _ARCHIVE_PREFETCH_WORKERS = max(1, int(args.archive_workers or 1))
    _ARCHIVE_VERIFY_LINKS = bool(args.verify_archive_links)
    if not args.no_archive_validate:
        try:
            if not _ARCHIVE_REFRESH_CACHE and archive_cache_load(_ARCHIVE_CACHE_FILE):
                archive_payload = _ARCHIVE_INDEX_CACHE
                print(f"Archive validation cache loaded: {len(archive_payload.get('rows', []))} items", flush=True)
            else:
                archive_payload = archive_fetch_index()
                print(f"Archive validation index loaded: {len(archive_payload.get('rows', []))} items", flush=True)
            archive_index = archive_payload.get('index', {})
            prefetch_ids = [row.get('identifier', '') for row in archive_payload.get('rows', [])]
            if args.archive_prefetch_missing and prefetch_ids:
                done = archive_prefetch_tracks(prefetch_ids, workers=_ARCHIVE_PREFETCH_WORKERS)
                print(f"Archive validation tracks cached: {done}/{len(prefetch_ids)}", flush=True)
            elif prefetch_ids:
                cached = sum(1 for ident in prefetch_ids if ident in _ARCHIVE_TRACKS_CACHE)
                print(f"Archive validation tracks cached locally: {cached}/{len(prefetch_ids)}", flush=True)
            archive_cache_save(_ARCHIVE_CACHE_FILE)
        except Exception as e:
            diags.append({'file':'archive.org','level':'WARN','kind':'archive','message':repr(e)})
            print(f"Archive validation disabled: {e}", flush=True)
            archive_index = {}
    # group by OSC, ignore exact duplicate bug files (keep lexicographically first)
    candidates=sorted(list(sc.glob('OSC*.xlsx')) + list(sc.glob('OSC*.txt')))
    byosc=defaultdict(list)
    for p in candidates:
        m=FILE_RE.match(p.name); byosc[int(m.group(1)) if m else -1].append(p)
    selected=[]; dup_rows=[]
    for osc,files in sorted(byosc.items()):
        if len(files)>1:
            def suffix_num(path):
                mm=FILE_RE.match(path.name)
                return int(mm.group(3)) if mm else 999
            files_sorted=sorted(files, key=suffix_num)
            hashes=[]
            for p in files_sorted:
                h=hashlib.sha256(p.read_bytes()).hexdigest(); hashes.append((p,h))
            unique=len(set(h for p,h in hashes))
            dup_rows.append({'osc':osc,'files':' | '.join(p.name for p,h in hashes),'count':len(files),'unique_hashes':unique,'action':'kept lowest suffix, ignored rest'})
            selected.append(files_sorted[0]); continue
        selected.extend(files)
    total_files=len(selected)
    print(f"Extractor v{version}: {total_files} scorecards")
    for path in selected:
        m=FILE_RE.match(path.name)
        osc=int(m.group(1)) if m else None; synth=m.group(2) if m else ''; old_suffix=m.group(3) if m else ''
        try:
            if path.suffix.lower() == '.txt':
                text=path.read_text(encoding='utf-8', errors='replace')
                var=classify_txt(path, text)
                rrows=extract_txt_results(path, text, var)
                vrows,vstats=extract_txt_votes(path, text, var, rrows)
                b=None
            else:
                b=XlsxBook(path); var=classify(b); rrows=extract_results(b,var); vrows,vstats=extract_votes(b,var,rrows)
            archive_tracks = []
            if archive_index:
                archive_doc = archive_index.get(osc)
                archive_tracks = archive_get_tracks(archive_doc.get('identifier', '')) if archive_doc else []
                if archive_tracks:
                    rrows = repair_result_rows_with_archive(rrows, archive_tracks)
            hard_sum, hard_detail = reconcile_file(osc, synth, path.name, var, rrows, vrows, vstats)
            hard_validation.append(hard_sum); reconciliation_rows.extend(hard_detail)
            if archive_index:
                archive_sum, archive_detail = validate_against_archive(osc, synth, path.name, rrows, archive_index)
            else:
                archive_sum, archive_detail = (
                    {
                        'osc': osc,
                        'synth': synth,
                        'file': path.name,
                        'archive_identifier': '',
                        'archive_title': '',
                        'archive_tracks': '',
                        'result_rows': len(rrows),
                        'exact_matches': '',
                        'swapped_matches': '',
                        'missing_rows': '',
                        'unmatched_archive_tracks': '',
                        'status': 'SKIP',
                        'checks': 'archive validation disabled',
                    },
                    [],
                )
            archive_validation.append(archive_sum); archive_validation_details.extend(archive_detail)
            if len(variant_examples[var])<4:
                variant_examples[var].append(path.name)
                if args.dump_samples and b is not None: dump_sample(b,var,out,path.name)
            vnum=VARIANT_ORDER.get(var,'')
            invrow={'file':path.name,'osc':osc,'synth':synth,'old_suffix':old_suffix,'template_version':var,'template_number':vnum,'variant_label':VARIANT_LABELS.get(var,var),'sheets':('TXT' if b is None else ' | '.join(b.sheets)),'result_rows':len(rrows),'vote_rows_extracted':len(vrows),'validation_warning':validate_file(osc,rrows),'hard_validation_status':hard_sum.get('status'),'hard_validation_checks':hard_sum.get('checks'),'archive_validation_status':archive_sum.get('status'),'archive_validation_checks':archive_sum.get('checks'),'archive_identifier':archive_sum.get('archive_identifier'),'archive_title':archive_sum.get('archive_title'),'archive_tracks':archive_sum.get('archive_tracks'),'archive_exact_matches':archive_sum.get('exact_matches'),'archive_swapped_matches':archive_sum.get('swapped_matches'),'archive_missing_rows':archive_sum.get('missing_rows'),'archive_unmatched_tracks':archive_sum.get('unmatched_archive_tracks')}
            invrow.update(vstats); inv.append(invrow); per_osc[osc].append(path.name)
            if invrow['validation_warning']: diags.append({'file':path.name,'level':'WARN','kind':'results','message':invrow['validation_warning']})
            if vstats.get('vote_validation') and not str(vstats.get('vote_validation')).startswith('OK'):
                diags.append({'file':path.name,'level':'WARN','kind':'votes','message':vstats.get('vote_validation')})
            if hard_sum.get('status') != 'OK':
                diags.append({'file':path.name,'level':'WARN' if hard_sum.get('status')=='CHECK' else 'ERROR','kind':'hard_validation','message':hard_sum.get('checks')})
            if archive_sum.get('status') not in ('OK', 'SKIP'):
                diags.append({'file':path.name,'level':'WARN' if archive_sum.get('status')=='CHECK' else 'ERROR','kind':'archive_validation','message':archive_sum.get('checks')})
            archive_tracks = archive_tracks or (archive_get_tracks(archive_sum.get('archive_identifier', '')) if archive_index and archive_sum.get('archive_identifier') else [])
            enriched_rrows = archive_enrich_result_rows(rrows, archive_sum, archive_detail, archive_tracks)
            for rr in enriched_rrows:
                results.append({'osc':osc,'synth':synth,'template_version':var,'template_number':vnum,'source_file':path.name, **rr})
            for vv in vrows:
                votes.append({'osc':osc,'synth':synth,'template_version':var,'template_number':vnum,'source_file':path.name, **vv})
            print(f"[{len(inv):03d}/{total_files:03d}] OSC{osc:03d} {synth} {var} | rows={len(rrows)} votes={len(vrows)} | {hard_sum.get('status')} | archive={archive_sum.get('status')}", flush=True)
        except Exception as e:
            diags.append({'file':path.name,'level':'ERROR','kind':'file','message':repr(e)})
            inv.append({'file':path.name,'osc':osc,'synth':synth,'old_suffix':old_suffix,'template_version':'ERROR','template_number':'','variant_label':'error','sheets':'','result_rows':0,'vote_rows_extracted':0,'validation_warning':repr(e)})
            print(f"[{len(inv):03d}/{total_files:03d}] OSC{osc:03d} {synth} ERROR | {e}", flush=True)
    write_csv(out/'scorecard_file_inventory.csv', inv)
    write_csv(out/'normalized_results.csv', results)
    write_hall_of_fame_outputs_from_results(results, out)
    write_csv(out/'normalized_votes.csv', votes)
    write_csv(out/'parser_diagnostics.csv', diags, ['file','level','kind','message'])
    write_csv(out/'hard_reconciliation_by_osc.csv', hard_validation)
    write_csv(out/'hard_reconciliation_details.csv', reconciliation_rows)
    write_csv(out/'archive_validation_by_osc.csv', archive_validation)
    write_csv(out/'archive_validation_details.csv', archive_validation_details)
    write_csv(out/'duplicate_osc_files.csv', dup_rows, ['osc','count','unique_hashes','action','files'])
    # variants summary
    by=defaultdict(list)
    for r in inv: by[r['template_version']].append(r)
    summary=[]
    for var,items in sorted(by.items(), key=lambda kv: VARIANT_ORDER.get(kv[0],99)):
        oscs=[i['osc'] for i in items if isinstance(i.get('osc'),int)]
        summary.append({'template_version':var,'template_number':VARIANT_ORDER.get(var,''),'label':VARIANT_LABELS.get(var,var),'files':len(items),'osc_min':min(oscs) if oscs else '', 'osc_max':max(oscs) if oscs else '', 'result_rows':sum(int(i.get('result_rows') or 0) for i in items), 'vote_rows':sum(int(i.get('vote_rows_extracted') or 0) for i in items), 'vote_min':min([float(i['vote_min']) for i in items if i.get('vote_min')!=''], default=''), 'vote_max':max([float(i['vote_max']) for i in items if i.get('vote_max')!=''], default=''), 'examples':'; '.join(variant_examples.get(var,[])[:4])})
    write_csv(out/'scorecard_variant_summary.csv', summary)
    # per OSC vote stats
    vst=[]
    for r in inv:
        vst.append({k:r.get(k,'') for k in ['osc','synth','file','template_version','template_number','result_rows','vote_sheet','vote_targets','vote_voters','vote_cells','vote_min','vote_max','vote_mode','vote_validation']})
    write_csv(out/'vote_stats_by_osc.csv', vst)
    # rename plan
    plan=[]
    for r in inv:
        tv=r.get('template_number')
        if isinstance(tv,int):
            ext = Path(str(r.get('file',''))).suffix or '.xlsx'
            new=f"OSC{int(r['osc']):03d}_{r['synth']}_{tv}{ext}"
            plan.append({'old_file':r['file'],'new_file':new,'template_version':r['template_version'],'template_number':tv,'changed':r['file']!=new})
    write_csv(out/'rename_plan_to_template_versions.csv', plan)
    # Hall of Fame based on result rows
    top5=[]; winners=[]; medals_raw=defaultdict(lambda:Counter()); medals_key=defaultdict(lambda:Counter()); key_display={}
    for osc in sorted({r['osc'] for r in results if r.get('osc') is not None}):
        rr=[r for r in results if r['osc']==osc and str(r['rank']).replace('.0','').isdigit()]
        rr.sort(key=lambda x:int(float(x['rank'])))
        for r in rr[:5]:
            top5.append(r)
            rk=int(float(r['rank']))
            a=r['artist'] or '(unknown)'; k=r['artist_key']; key_display.setdefault(k,r['artist_canonical'] or a)
            for target in (medals_raw[a], medals_key[k]):
                if rk==1: target['gold']+=1
                elif rk==2: target['silver']+=1
                elif rk==3: target['bronze']+=1
                target['top5']+=1
        if rr: winners.append(rr[0])
    write_csv(out/'winners_by_osc.csv', winners)
    write_csv(out/'top5_all_osc.csv', top5)
    def medal_rows(medals, canon=False):
        rows=[]
        for a,c in medals.items():
            rows.append({'artist_key':a if canon else artist_key(a),'artist':key_display.get(a,a) if canon else a,'gold':c['gold'],'silver':c['silver'],'bronze':c['bronze'],'top5':c['top5']})
        rows.sort(key=lambda x:(-x['gold'],-x['silver'],-x['bronze'],-x['top5'],x['artist'].lower()))
        return rows
    write_csv(out/'artist_medals_top5_raw.csv', medal_rows(medals_raw), ['artist_key','artist','gold','silver','bronze','top5'])
    write_csv(out/'artist_medals_top5_canonical.csv', medal_rows(medals_key, True), ['artist_key','artist','gold','silver','bronze','top5'])
    # alias candidates
    forms=defaultdict(Counter)
    for r in results:
        if r['artist']: forms[r['artist_key']][r['artist']]+=1
    alias=[]
    for k,c in forms.items():
        if len(c)>1:
            alias.append({'artist_key':k,'forms':' | '.join(f'{name} ({cnt})' for name,cnt in c.most_common()),'count':sum(c.values())})
    alias.sort(key=lambda x:-x['count'])
    write_csv(out/'artist_alias_candidates.csv', alias, ['artist_key','forms','count'])
    # validation summary
    validation=[]
    for r in inv:
        ok = (r.get('hard_validation_status') == 'OK') and (not r.get('validation_warning')) and (r.get('archive_validation_status') in ('OK', 'SKIP'))
        validation.append({'osc':r.get('osc'),'file':r.get('file'),'template_version':r.get('template_version'),'results_rows':r.get('result_rows'),'votes_rows':r.get('vote_rows_extracted'),'vote_validation':r.get('vote_validation'),'hard_validation_status':r.get('hard_validation_status'),'hard_validation_checks':r.get('hard_validation_checks'),'archive_validation_status':r.get('archive_validation_status'),'archive_validation_checks':r.get('archive_validation_checks'),'status':'OK' if ok else 'CHECK'})
    write_csv(out/'validation_matrix.csv', validation)
    # cockpit-ready / review outputs
    failures=[r for r in validation if r.get('status')!='OK']
    write_csv(out/'validation_failures.csv', failures, ['osc','file','template_version','results_rows','votes_rows','vote_validation','hard_validation_status','hard_validation_checks','archive_validation_status','archive_validation_checks','status'])
    rule_rows=[]
    for r in inv:
        rule_rows.append({
            'osc':r.get('osc'), 'synth':r.get('synth'), 'file':r.get('file'),
            'template_version':r.get('template_version'), 'template_number':r.get('template_number'),
            'vote_mode':r.get('vote_mode'), 'vote_min':r.get('vote_min'), 'vote_max':r.get('vote_max'),
            'vote_targets':r.get('vote_targets'), 'vote_voters':r.get('vote_voters'), 'vote_cells':r.get('vote_cells'),
            'rule_guess':infer_rule_epoch(r), 'vote_validation':r.get('vote_validation')
        })
    write_csv(out/'voting_rule_history_by_osc.csv', rule_rows)
    # reuse canonical medal stats as Cockpit Hall of Fame CSV
    cockpit_hof=medal_rows(medals_key, True)
    write_csv(out/'cockpit_hall_of_fame.csv', cockpit_hof, ['artist_key','artist','gold','silver','bronze','top5'])
    payload=make_cockpit_payload(results, votes, inv, summary, winners, top5, cockpit_hof, alias, diags)
    write_json(out/'cockpit_scorecards_data.json', payload)
    # docs
    with open(out/'parser_plan.md','w',encoding='utf-8') as f:
        f.write(f'# Parser and vote extractor plan v{version}\n\n')
        f.write('Goal: extract final placements and raw vote matrices for every scorecard family. `_N` is treated as template/parser version, not file revision.\n\n')
        for s in summary:
            f.write(f"## {s['template_version']} / suffix _{s['template_number']}\n\n")
            f.write(f"{s['label']}\n\nFiles: {s['files']} / OSC{s['osc_min']}-OSC{s['osc_max']} / result rows {s['result_rows']} / vote rows {s['vote_rows']}\n\n")
            f.write(f"Vote range seen: {s['vote_min']}..{s['vote_max']}\n\nExamples: {s['examples']}\n\n")
            if s['template_version']=='V01': f.write('Votes: anonymous vote columns in the single result sheet; result score validates by row sum.\n\n')
            elif s['template_version']=='V04': f.write('Votes: ranked ballot values in `Voting Results`; `vote_rank` stores raw rank, `vote_points` stores inverted Borda-style points.\n\n')
            elif s['template_version'] in ('V02','V03'): f.write('Votes: `Voting Results` point matrix. V02 is sparse 1-10 style; V03 is mostly-full 1-5 style.\n\n')
            elif s['template_version']=='V05': f.write('Votes: sheet matching `OSCxx` point matrix.\n\n')
            elif s['template_version'] in ('V06','V07','V08'): f.write('Votes: `Data` point matrix; Prizes ignored for core stats.\n\n')
            elif s['template_version']=='V09': f.write('Votes: transitional `Scores`/custom sheet point matrix.\n\n')
    with open(out/'README_probe.md','w',encoding='utf-8') as f:
        f.write(f'# Scorecard vote extractor v{version}\n\n')
        f.write(f'Files processed: {len(inv)}\n\nResult rows extracted: {len(results)}\n\nVote rows extracted: {len(votes)}\n\nDiagnostics: {len(diags)}\n\n')
        f.write('## Key files\n\n- `normalized_results.csv`\n- `normalized_votes.csv`\n- `vote_stats_by_osc.csv`\n- `validation_matrix.csv`\n- `parser_diagnostics.csv`\n- `scorecard_variant_summary.csv`\n- `parser_plan.md`\n\n')
        f.write('- `archive_validation_by_osc.csv`\n- `archive_validation_details.csv`\n\n')
        if dup_rows:
            f.write('## Duplicate files\n\n')
            for d in dup_rows: f.write(f"- OSC{int(d['osc']):03d}: {d['files']} / action={d['action']}\n")
            f.write('\n')
    with open(out/'run_summary.json','w',encoding='utf-8') as f:
        json.dump({'files':len(inv),'result_rows':len(results),'vote_rows':len(votes),'diagnostics':len(diags),'validation_failures':len(failures),'hard_validation_not_ok':sum(1 for x in hard_validation if x.get('status')!='OK'),'archive_validation_checks':sum(1 for x in archive_validation if x.get('status')=='CHECK'),'archive_validation_skipped':sum(1 for x in archive_validation if x.get('status')=='SKIP'),'duplicates':dup_rows,'variants':summary},f,indent=2)
    print(f"Extractor v{version} done: files={len(inv)} results={len(results)} votes={len(votes)} diagnostics={len(diags)} failures={len(failures)} hard_failures={sum(1 for x in hard_validation if x.get('status')!='OK')} archive_checks={sum(1 for x in archive_validation if x.get('status')=='CHECK')} archive_skipped={sum(1 for x in archive_validation if x.get('status')=='SKIP')}")

if __name__=='__main__': main()
