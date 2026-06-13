import base64
import csv
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
TRACKS_FILE = ROOT / "tracks.json"
INDEX_FILE = ROOT / "index.html"
MEMORY_FILE = ROOT / "data" / "seen_tracker_keys.json"
CSV_URL = "https://docs.google.com/spreadsheets/d/1FUzAZyTCgFTVxQ--qbCAS2bUk4dsAw6ASxwjURPHbyI/export?format=csv&gid=1241081326"
SOURCE_NAME = "Yeat Trackër — Unreleased"


def text(value):
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def clean(value):
    return re.sub(r"\s+", " ", text(value).replace("\n", " ")).strip()


def header_key(name):
    return re.sub(r"[^a-z0-9]+", "", text(name).lower())


def get_value(row, names):
    if isinstance(names, str):
        names = [names]
    lookup = {header_key(key): value for key, value in row.items()}
    for name in names:
        value = lookup.get(header_key(name))
        if value is not None:
            return text(value)
    return ""


def normalize(value):
    value = clean(value).lower()
    value = value.replace("ë", "e").replace("é", "e").replace("è", "e").replace("ê", "e")
    value = value.replace("ö", "o").replace("ï", "i").replace("à", "a")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def slug(value):
    value = normalize(value)
    value = re.sub(r"\s+", "-", value)
    return value[:80].strip("-") or "track"


def read_csv_rows():
    request = Request(CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=60) as response:
        content = response.read().decode("utf-8-sig")
    return list(csv.DictReader(content.splitlines()))


def is_track_row(row):
    era = get_value(row, "Era")
    title = get_value(row, "Name")
    if not era or not title:
        return False
    normalized_era = normalize(era)
    if "total" in normalized_era and ("full" in normalized_era or "snippet" in normalized_era or "unavailable" in normalized_era):
        return False
    useful_fields = [
        "Track Length",
        "File Date",
        "First Preview",
        "Leak Date",
        "OG File Leak Date",
        "Type",
        "Available Length",
        "Quality",
        "Link(s)",
    ]
    return any(get_value(row, field) for field in useful_fields)


def pillow_links(raw_value):
    links = []
    for link in re.findall(r"https?://(?:www\.)?pillows\.su/f/[^\s,;\])}\"']+", raw_value):
        link = link.rstrip(". )]")
        if link not in links:
            links.append(link)
    return links


def parse_date(raw_value):
    raw_value = clean(raw_value)
    if not raw_value or normalize(raw_value) in {"na", "n a", "leaked", "unknown"}:
        return "--"
    raw_value = re.sub(r"\s+", " ", raw_value)
    formats = [
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%m/%d/%y",
        "%d/%m/%y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
    ]
    for date_format in formats:
        try:
            return datetime.strptime(raw_value, date_format).strftime("%Y-%m-%d")
        except ValueError:
            pass
    match = re.search(r"(20\d{2}|19\d{2})", raw_value)
    if match:
        return match.group(1)
    return raw_value


def year_from_dates(*dates):
    for date in dates:
        match = re.search(r"(20\d{2}|19\d{2})", date or "")
        if match:
            return int(match.group(1))
    return datetime.utcnow().year


def extract_notes(raw_notes):
    raw_notes = text(raw_notes)
    og_filename = "--"
    notes = raw_notes
    lines = [line.strip() for line in raw_notes.split("\n") if line.strip()]
    if lines and lines[0].lower().startswith("og filename:"):
        og_filename = clean(lines[0].split(":", 1)[1]) or "--"
        notes = "\n".join(lines[1:])
    return og_filename, clean(notes)


def row_key(row):
    era = get_value(row, "Era")
    title = get_value(row, "Name")
    raw_notes = get_value(row, ["Notes", "Notes (Join the Yeat Hub Discord!)"])
    og_filename, notes_without_og = extract_notes(raw_notes)
    parts = [
        era,
        title,
        og_filename,
        get_value(row, "Track Length"),
        get_value(row, "File Date"),
        get_value(row, "First Preview"),
        get_value(row, "Leak Date"),
        notes_without_og[:160],
    ]
    base = "|".join(normalize(part) for part in parts if clean(part))
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def track_key(track):
    info = track.get("info") if isinstance(track.get("info"), dict) else {}
    dates = info.get("dates") if isinstance(info.get("dates"), dict) else {}
    parts = [
        track.get("era", ""),
        track.get("title", ""),
        info.get("ogFilename", ""),
        info.get("length", ""),
        dates.get("fileDate", ""),
        dates.get("firstPreview", ""),
        dates.get("leakDate", ""),
        info.get("notes", "")[:160],
    ]
    base = "|".join(normalize(part) for part in parts if clean(part))
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def convert_row_to_track(row, number):
    era = clean(get_value(row, "Era"))
    title = clean(get_value(row, "Name")) or "Untitled"
    raw_notes = get_value(row, ["Notes", "Notes (Join the Yeat Hub Discord!)"])
    length = clean(get_value(row, "Track Length")) or "--:--"
    file_date = parse_date(get_value(row, "File Date"))
    first_preview = parse_date(get_value(row, "First Preview"))
    leak_date = parse_date(get_value(row, "Leak Date"))
    track_type = clean(get_value(row, "Type")) or "Unknown"
    available_length = clean(get_value(row, "Available Length")) or "Unknown"
    quality = clean(get_value(row, "Quality")) or "Unknown"
    links = pillow_links(get_value(row, "Link(s)"))
    og_filename, notes = extract_notes(raw_notes)
    description = notes
    if og_filename != "--":
        description = f"OG Filename: {og_filename} {description}".strip()
    if not description:
        description = "No description."
    track_id = f"unreleased-{slug(era)}-{slug(title)}-{number}-{row_key(row)[:8]}"
    track = {
        "id": track_id,
        "title": title,
        "era": era or "Unknown era",
        "year": year_from_dates(file_date, first_preview, leak_date),
        "intendedAlbum": era or "Unsorted",
        "status": f"{track_type} • {available_length}",
        "quality": quality,
        "file": "",
        "privateLink": links[0] if links else "",
        "description": description,
        "info": {
            "source": SOURCE_NAME,
            "length": length,
            "version": track_type,
            "notes": notes,
            "dates": {
                "fileDate": file_date,
                "firstPreview": first_preview,
                "leakDate": leak_date,
            },
            "noteLinksBlock": [],
            "ogFilename": og_filename,
        },
    }
    if len(links) > 1:
        track["privateLinks"] = links
    return track


def read_json(path, default_value):
    if not path.exists():
        return default_value
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default_value


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sync_index(tracks):
    if not INDEX_FILE.exists():
        return
    compact = json.dumps(tracks, ensure_ascii=False, separators=(",", ":"))
    encoded = base64.b64encode(compact.encode("utf-8")).decode("ascii")
    html = INDEX_FILE.read_text(encoding="utf-8")
    updated_html = re.sub(
        r'(<script type="text/plain" id="embeddedTracksDataB64">).*?(</script>)',
        lambda match: match.group(1) + encoded + match.group(2),
        html,
        flags=re.S,
    )
    if updated_html != html:
        INDEX_FILE.write_text(updated_html, encoding="utf-8")


def main():
    rows = [row for row in read_csv_rows() if is_track_row(row)]
    tracks = read_json(TRACKS_FILE, [])
    memory = read_json(MEMORY_FILE, {"initialized": False, "keys": []})
    if not isinstance(memory, dict):
        memory = {"initialized": False, "keys": []}
    seen_keys = set(memory.get("keys") or [])
    csv_keys = [row_key(row) for row in rows]
    if not memory.get("initialized"):
        write_json(MEMORY_FILE, {"initialized": True, "keys": sorted(set(csv_keys))})
        print(f"Memory initialized with {len(set(csv_keys))} tracker rows. No old tracks were added.")
        return
    existing_track_keys = {track_key(track) for track in tracks if isinstance(track, dict)}
    new_tracks = []
    for number, row in enumerate(rows, start=1):
        key = row_key(row)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if key in existing_track_keys:
            continue
        track = convert_row_to_track(row, number)
        new_tracks.append(track)
        tracks.append(track)
        existing_track_keys.add(key)
    write_json(MEMORY_FILE, {"initialized": True, "keys": sorted(seen_keys)})
    if new_tracks:
        write_json(TRACKS_FILE, tracks)
        sync_index(tracks)
        print(f"Added {len(new_tracks)} new track(s).")
        for track in new_tracks[:20]:
            print(f"- {track['title']} / {track['era']}")
    else:
        print("No new tracks found.")


if __name__ == "__main__":
    main()
