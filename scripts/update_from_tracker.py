import base64
import csv
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
TRACKS_FILE = ROOT / "tracks.json"
INDEX_FILE = ROOT / "index.html"
MEMORY_FILE = ROOT / "data" / "seen_tracker_keys.json"
CSV_URL = "https://docs.google.com/spreadsheets/d/1FUzAZyTCgFTVxQ--qbCAS2bUk4dsAw6ASxwjURPHbyI/export?format=csv&gid=1241081326"
SOURCE_NAME = "Yeat Trackër — Unreleased"
MAX_NEW_TRACKS_PER_RUN = 50
MIN_TRACK_ROWS = 50

COLUMN_GROUPS = {
    "title": {
        "required": True,
        "aliases": ["Name", "Title", "Track", "Track Name", "Song", "Song Name"],
    },
    "era": {
        "required": True,
        "aliases": ["Era", "Album", "Intended Album", "Era / Album", "Project"],
    },
    "links": {
        "required": True,
        "aliases": ["Link(s)", "Links", "Link", "URL", "URLs", "Pillow", "Pillows", "Pillow Link", "Pillow Links", "Pillows Link", "Pillows Links"],
    },
    "type": {
        "required": True,
        "aliases": ["Type", "Version Type", "File Type", "Track Type"],
    },
    "quality": {
        "required": True,
        "aliases": ["Quality", "File Quality", "Audio Quality"],
    },
    "track_length": {
        "required": True,
        "aliases": ["Track Length", "Length", "Duration", "Song Length"],
    },
    "notes": {
        "required": True,
        "aliases": ["Notes", "Note", "Comments", "Description", "Info", "Notes (Join the Yeat Hub Discord!)"],
    },
    "file_date": {
        "required": False,
        "aliases": ["File Date", "File", "File Date(s)"],
    },
    "first_preview": {
        "required": False,
        "aliases": ["First Preview", "First Preview Date", "Preview Date"],
    },
    "leak_date": {
        "required": False,
        "aliases": ["Leak Date", "OG File Leak Date", "Leaked", "Leak"],
    },
    "available_length": {
        "required": False,
        "aliases": ["Available Length", "Available", "Available Time"],
    },
}

FIELD_COLUMNS = {}


class BotError(Exception):
    pass


SUMMARY_LINES = []


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def add_summary(line=""):
    SUMMARY_LINES.append(line)


def log(message):
    print(message, flush=True)
    add_summary(message)


def write_summary(status, message):
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    title = "✅ Bot run completed" if status == "success" else "❌ Bot run stopped"
    body = [f"# {title}", "", f"**Time:** {now_utc()}", "", f"**Result:** {message}", ""]
    if SUMMARY_LINES:
        body.extend(["## Details", ""])
        body.extend(f"- {line}" for line in SUMMARY_LINES if line.strip())
    Path(summary_path).write_text("\n".join(body) + "\n", encoding="utf-8")


def stop(message):
    raise BotError(message)


def text(value):
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def clean(value):
    return re.sub(r"\s+", " ", text(value).replace("\n", " ")).strip()


def header_key(name):
    return re.sub(r"[^a-z0-9]+", "", text(name).lower())


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


def aliases(field_name):
    group = COLUMN_GROUPS.get(field_name)
    if not group:
        return [field_name]
    resolved = FIELD_COLUMNS.get(field_name)
    if resolved:
        return [resolved] + [name for name in group["aliases"] if name != resolved]
    return group["aliases"]


def get_value(row, names):
    if isinstance(names, str):
        names = aliases(names)
    lookup = {header_key(key): value for key, value in row.items()}
    for name in names:
        value = lookup.get(header_key(name))
        if value is not None:
            return text(value)
    return ""


def header_snapshot(fieldnames):
    snapshot = []
    for name in fieldnames or []:
        cleaned = clean(name)
        if cleaned:
            snapshot.append({"name": cleaned, "key": header_key(cleaned)})
    return snapshot


def snapshot_keys(snapshot):
    return {item.get("key", "") for item in snapshot if item.get("key")}


def names_for_keys(snapshot, keys):
    lookup = {item.get("key", ""): item.get("name", "") for item in snapshot if item.get("key")}
    return [lookup.get(key, key) for key in keys]


def validate_and_resolve_headers(fieldnames):
    global FIELD_COLUMNS
    if not fieldnames:
        stop("The CSV has no header row. The Google Sheets export may be private, empty, or broken.")

    available = {header_key(name): clean(name) for name in fieldnames if clean(name)}
    resolved = {}
    missing = []
    optional_missing = []

    for label, config in COLUMN_GROUPS.items():
        found = ""
        for alias in config["aliases"]:
            key = header_key(alias)
            if key in available:
                found = available[key]
                break
        if found:
            resolved[label] = found
        elif config["required"]:
            missing.append(f"{label}: expected one of {config['aliases']}")
        else:
            optional_missing.append(label)

    if missing:
        columns = ", ".join(clean(name) for name in fieldnames if clean(name))
        stop("Missing required Google Sheets columns: " + "; ".join(missing) + f". Current columns: {columns}")

    FIELD_COLUMNS = resolved

    alias_messages = []
    for label, actual in resolved.items():
        primary = COLUMN_GROUPS[label]["aliases"][0]
        if header_key(actual) != header_key(primary):
            alias_messages.append(f"{label}: using '{actual}' instead of '{primary}'")
    if alias_messages:
        log("Column alias mapping: " + "; ".join(alias_messages))

    if optional_missing:
        log("Warning: optional columns missing: " + ", ".join(optional_missing))

    log("Column mapping OK: " + ", ".join(f"{key}='{value}'" for key, value in sorted(resolved.items())))
    return resolved


def compare_headers(previous_snapshot, current_snapshot):
    if not previous_snapshot:
        log("Column watch initialized. Future column changes will be reported in the Actions logs.")
        return True

    old_keys = snapshot_keys(previous_snapshot)
    new_keys = snapshot_keys(current_snapshot)
    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)

    if not added and not removed:
        return False

    log("Warning: Google Sheets columns changed since the previous successful run.")
    if added:
        log("New column(s) detected and ignored unless mapped: " + ", ".join(names_for_keys(current_snapshot, added)))
    if removed:
        log("Removed/renamed column(s) detected: " + ", ".join(names_for_keys(previous_snapshot, removed)))
    log("If a required column was renamed to an unknown name, this run stops before changing the site.")
    return True


def read_csv_rows():
    log("Downloading Google Sheets CSV...")
    request = Request(CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(request, timeout=60) as response:
            content_type = response.headers.get("content-type", "")
            raw_content = response.read()
    except HTTPError as exc:
        stop(f"Google Sheets download failed with HTTP {exc.code}. The CSV link may be private or unavailable.")
    except URLError as exc:
        stop(f"Google Sheets download failed: {exc.reason}")
    except TimeoutError:
        stop("Google Sheets download timed out after 60 seconds.")

    content = raw_content.decode("utf-8-sig", errors="replace")
    preview = content[:300].lower()
    if "text/html" in content_type.lower() or "<html" in preview or "<!doctype html" in preview:
        stop("Google returned an HTML page instead of CSV. The sheet is probably private, moved, or not published correctly.")
    if not content.strip():
        stop("Google Sheets CSV is empty.")

    reader = csv.DictReader(content.splitlines())
    current_snapshot = header_snapshot(reader.fieldnames)
    validate_and_resolve_headers(reader.fieldnames)
    rows = list(reader)
    log(f"CSV downloaded: {len(rows)} raw row(s).")
    return rows, current_snapshot


def is_track_row(row):
    era = get_value(row, "era")
    title = get_value(row, "title")
    if not era or not title:
        return False
    normalized_era = normalize(era)
    if "total" in normalized_era and ("full" in normalized_era or "snippet" in normalized_era or "unavailable" in normalized_era):
        return False
    useful_fields = [
        "track_length",
        "file_date",
        "first_preview",
        "leak_date",
        "type",
        "available_length",
        "quality",
        "links",
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
    era = get_value(row, "era")
    title = get_value(row, "title")
    raw_notes = get_value(row, "notes")
    og_filename, notes_without_og = extract_notes(raw_notes)
    parts = [
        era,
        title,
        og_filename,
        get_value(row, "track_length"),
        get_value(row, "file_date"),
        get_value(row, "first_preview"),
        get_value(row, "leak_date"),
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
    era = clean(get_value(row, "era"))
    title = clean(get_value(row, "title")) or "Untitled"
    raw_notes = get_value(row, "notes")
    length = clean(get_value(row, "track_length")) or "--:--"
    file_date = parse_date(get_value(row, "file_date"))
    first_preview = parse_date(get_value(row, "first_preview"))
    leak_date = parse_date(get_value(row, "leak_date"))
    track_type = clean(get_value(row, "type")) or "Unknown"
    available_length = clean(get_value(row, "available_length")) or "Unknown"
    quality = clean(get_value(row, "quality")) or "Unknown"
    links = pillow_links(get_value(row, "links"))
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


def read_json_strict(path):
    if not path.exists():
        stop(f"Missing required file: {path.relative_to(ROOT)}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        stop(f"Invalid JSON in {path.relative_to(ROOT)} at line {exc.lineno}, column {exc.colno}: {exc.msg}")


def read_memory():
    if not MEMORY_FILE.exists():
        return None
    try:
        memory = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        stop(f"Invalid JSON in {MEMORY_FILE.relative_to(ROOT)} at line {exc.lineno}, column {exc.colno}: {exc.msg}")
    if not isinstance(memory, dict):
        stop("Memory file is invalid: expected a JSON object.")
    keys = memory.get("keys")
    if keys is None:
        keys = []
    if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
        stop("Memory file is invalid: expected 'keys' to be a list of strings.")
    columns = memory.get("columns")
    if columns is not None and not isinstance(columns, list):
        stop("Memory file is invalid: expected 'columns' to be a list.")
    return {
        "initialized": bool(memory.get("initialized")),
        "keys": keys,
        "columns": columns or [],
    }


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sync_index(tracks):
    if not INDEX_FILE.exists():
        stop("index.html is missing, so embedded tracks cannot be synced.")
    compact = json.dumps(tracks, ensure_ascii=False, separators=(",", ":"))
    encoded = base64.b64encode(compact.encode("utf-8")).decode("ascii")
    html = INDEX_FILE.read_text(encoding="utf-8")
    pattern = r'(<script type="text/plain" id="embeddedTracksDataB64">).*?(</script>)'
    if not re.search(pattern, html, flags=re.S):
        stop("index.html does not contain embeddedTracksDataB64. Sync stopped to avoid corrupting the page.")
    updated_html = re.sub(pattern, lambda match: match.group(1) + encoded + match.group(2), html, flags=re.S)
    if updated_html != html:
        INDEX_FILE.write_text(updated_html, encoding="utf-8")


def write_memory(keys, columns):
    write_json(MEMORY_FILE, {"initialized": True, "keys": sorted(set(keys)), "columns": columns})


def run_bot():
    log(f"Bot started at {now_utc()}.")
    if not TRACKS_FILE.exists():
        stop("tracks.json is missing.")
    if not INDEX_FILE.exists():
        stop("index.html is missing.")

    csv_rows, current_columns = read_csv_rows()
    rows = [row for row in csv_rows if is_track_row(row)]
    log(f"Valid tracker rows detected: {len(rows)}.")
    if len(rows) < MIN_TRACK_ROWS:
        stop(f"Only {len(rows)} valid track rows were found. This is suspicious, so no files were changed.")

    tracks = read_json_strict(TRACKS_FILE)
    if not isinstance(tracks, list):
        stop("tracks.json is invalid: expected a JSON array.")
    log(f"Current site tracks: {len(tracks)}.")

    csv_keys = [row_key(row) for row in rows]
    memory = read_memory()

    if memory is None or not memory.get("initialized"):
        write_memory(csv_keys, current_columns)
        log(f"Memory initialized with {len(set(csv_keys))} tracker row(s). No old tracks were added.")
        return "Memory initialized. No old tracks were added."

    memory_changed = compare_headers(memory.get("columns") or [], current_columns)
    seen_keys = set(memory.get("keys") or [])
    existing_track_keys = {track_key(track) for track in tracks if isinstance(track, dict)}

    pending_rows = []
    for number, row in enumerate(rows, start=1):
        key = row_key(row)
        if key not in seen_keys:
            pending_rows.append((number, row, key))

    log(f"New tracker row candidates: {len(pending_rows)}.")
    if len(pending_rows) > MAX_NEW_TRACKS_PER_RUN:
        examples = []
        for _, row, _ in pending_rows[:10]:
            examples.append(f"{clean(get_value(row, 'title')) or 'Untitled'} / {clean(get_value(row, 'era')) or 'Unknown era'}")
        stop(
            f"Too many new rows detected at once ({len(pending_rows)}). "
            f"Limit is {MAX_NEW_TRACKS_PER_RUN}. This may mean the sheet structure changed or the memory file was reset. "
            f"Examples: {', '.join(examples)}"
        )

    new_tracks = []
    for number, row, key in pending_rows:
        seen_keys.add(key)
        if key in existing_track_keys:
            continue
        track = convert_row_to_track(row, number)
        new_tracks.append(track)
        tracks.append(track)
        existing_track_keys.add(key)

    if new_tracks:
        write_json(TRACKS_FILE, tracks)
        sync_index(tracks)
        write_memory(seen_keys, current_columns)
        log(f"Added {len(new_tracks)} new track(s).")
        for track in new_tracks[:20]:
            log(f"Added: {track['title']} / {track['era']}")
        if len(new_tracks) > 20:
            log(f"And {len(new_tracks) - 20} more track(s).")
        return f"Added {len(new_tracks)} new track(s)."

    if memory_changed:
        write_memory(seen_keys, current_columns)
        log("Column memory updated. No tracks were changed.")
        return "Column memory updated. No tracks were changed."

    log("No new tracks found.")
    return "No new tracks found."


def main():
    try:
        result = run_bot()
        write_summary("success", result)
    except BotError as exc:
        message = str(exc)
        print(f"ERROR: {message}", file=sys.stderr, flush=True)
        add_summary(f"ERROR: {message}")
        write_summary("error", message)
        sys.exit(1)
    except Exception as exc:
        message = f"Unexpected error: {type(exc).__name__}: {exc}"
        print(f"ERROR: {message}", file=sys.stderr, flush=True)
        add_summary(f"ERROR: {message}")
        write_summary("error", message)
        sys.exit(1)


if __name__ == "__main__":
    main()
