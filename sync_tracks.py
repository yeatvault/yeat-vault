import base64
import json
import re
from pathlib import Path

root = Path(__file__).resolve().parent
tracks_path = root / "tracks.json"
index_path = root / "index.html"

data = json.loads(tracks_path.read_text(encoding="utf-8"))
compact = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
encoded = base64.b64encode(compact.encode("utf-8")).decode("ascii")
html = index_path.read_text(encoding="utf-8")
html = re.sub(
    r'(<script type="text/plain" id="embeddedTracksDataB64">).*?(</script>)',
    lambda match: match.group(1) + encoded + match.group(2),
    html,
    flags=re.S,
)
index_path.write_text(html, encoding="utf-8")
print("tracks.json synced into index.html")
