import base64
import csv
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

RACINE = Path(__file__).resolve().parents[1]
FICHIER_TRACKS = RACINE / "tracks.json"
FICHIER_INDEX = RACINE / "index.html"
FICHIER_MEMOIRE = RACINE / "data" / "seen_tracker_keys.json"
URL_CSV = "https://docs.google.com/spreadsheets/d/1FUzAZyTCgFTVxQ--qbCAS2bUk4dsAw6ASxwjURPHbyI/export?format=csv&gid=1241081326"
SOURCE = "Yeat Trackër — Unreleased"


def texte(valeur):
    if valeur is None:
        return ""
    return str(valeur).replace("\r\n", "\n").replace("\r", "\n").strip()


def propre(valeur):
    return re.sub(r"\s+", " ", texte(valeur).replace("\n", " ")).strip()


def cle_header(nom):
    return re.sub(r"[^a-z0-9]+", "", texte(nom).lower())


def valeur(ligne, noms):
    if isinstance(noms, str):
        noms = [noms]
    index = {cle_header(k): v for k, v in ligne.items()}
    for nom in noms:
        val = index.get(cle_header(nom))
        if val is not None:
            return texte(val)
    return ""


def normalise(valeur):
    valeur = propre(valeur).lower()
    valeur = valeur.replace("ë", "e").replace("é", "e").replace("è", "e").replace("ê", "e")
    valeur = valeur.replace("ö", "o").replace("ï", "i").replace("à", "a")
    valeur = re.sub(r"[^a-z0-9]+", " ", valeur)
    return re.sub(r"\s+", " ", valeur).strip()


def slug(valeur):
    valeur = normalise(valeur)
    valeur = re.sub(r"\s+", "-", valeur)
    return valeur[:80].strip("-") or "track"


def lire_csv():
    requete = Request(URL_CSV, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(requete, timeout=60) as reponse:
        contenu = reponse.read().decode("utf-8-sig")
    return list(csv.DictReader(contenu.splitlines()))


def est_ligne_son(ligne):
    era = valeur(ligne, "Era")
    nom = valeur(ligne, "Name")
    if not era or not nom:
        return False
    era_n = normalise(era)
    if "total" in era_n and ("full" in era_n or "snippet" in era_n or "unavailable" in era_n):
        return False
    champs = ["Track Length", "File Date", "First Preview", "Leak Date", "OG File Leak Date", "Type", "Available Length", "Quality", "Link(s)"]
    return any(valeur(ligne, champ) for champ in champs)


def liens_pillows(valeur_brute):
    liens = []
    for lien in re.findall(r"https?://(?:www\.)?pillows\.su/f/[^\s,;\])}\"']+", valeur_brute):
        lien = lien.rstrip(". )]")
        if lien not in liens:
            liens.append(lien)
    return liens


def date_iso(valeur_brute):
    valeur_brute = propre(valeur_brute)
    if not valeur_brute or normalise(valeur_brute) in {"na", "n a", "leaked", "unknown"}:
        return "--"
    valeur_brute = re.sub(r"\s+", " ", valeur_brute)
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
    for format_date in formats:
        try:
            return datetime.strptime(valeur_brute, format_date).strftime("%Y-%m-%d")
        except ValueError:
            pass
    trouve = re.search(r"(20\d{2}|19\d{2})", valeur_brute)
    if trouve:
        return trouve.group(1)
    return valeur_brute


def annee_depuis_dates(*dates):
    for date in dates:
        trouve = re.search(r"(20\d{2}|19\d{2})", date or "")
        if trouve:
            return int(trouve.group(1))
    return datetime.utcnow().year


def extrait_notes(notes_brutes):
    notes_brutes = texte(notes_brutes)
    og = "--"
    notes = notes_brutes
    lignes = [ligne.strip() for ligne in notes_brutes.split("\n") if ligne.strip()]
    if lignes and lignes[0].lower().startswith("og filename:"):
        og = propre(lignes[0].split(":", 1)[1]) or "--"
        notes = "\n".join(lignes[1:])
    return og, propre(notes)


def cle_ligne(ligne):
    era = valeur(ligne, "Era")
    nom = valeur(ligne, "Name")
    notes = valeur(ligne, ["Notes", "Notes (Join the Yeat Hub Discord!)"])
    og, notes_sans_og = extrait_notes(notes)
    morceaux = [
        era,
        nom,
        og,
        valeur(ligne, "Track Length"),
        valeur(ligne, "File Date"),
        valeur(ligne, "First Preview"),
        valeur(ligne, "Leak Date"),
        notes_sans_og[:160],
    ]
    base = "|".join(normalise(m) for m in morceaux if propre(m))
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def cle_track(track):
    info = track.get("info") if isinstance(track.get("info"), dict) else {}
    dates = info.get("dates") if isinstance(info.get("dates"), dict) else {}
    morceaux = [
        track.get("era", ""),
        track.get("title", ""),
        info.get("ogFilename", ""),
        info.get("length", ""),
        dates.get("fileDate", ""),
        dates.get("firstPreview", ""),
        dates.get("leakDate", ""),
        info.get("notes", "")[:160],
    ]
    base = "|".join(normalise(m) for m in morceaux if propre(m))
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def convertir(ligne, numero):
    era = propre(valeur(ligne, "Era"))
    titre = propre(valeur(ligne, "Name")) or "Untitled"
    notes_brutes = valeur(ligne, ["Notes", "Notes (Join the Yeat Hub Discord!)"])
    longueur = propre(valeur(ligne, "Track Length")) or "--:--"
    file_date = date_iso(valeur(ligne, "File Date"))
    first_preview = date_iso(valeur(ligne, "First Preview"))
    leak_date = date_iso(valeur(ligne, "Leak Date"))
    type_track = propre(valeur(ligne, "Type")) or "Unknown"
    available = propre(valeur(ligne, "Available Length")) or "Unknown"
    quality = propre(valeur(ligne, "Quality")) or "Unknown"
    liens = liens_pillows(valeur(ligne, "Link(s)"))
    og, notes = extrait_notes(notes_brutes)
    description = notes
    if og != "--":
        description = f"OG Filename: {og} {description}".strip()
    if not description:
        description = "No description."
    identifiant = f"unreleased-{slug(era)}-{slug(titre)}-{numero}-{cle_ligne(ligne)[:8]}"
    track = {
        "id": identifiant,
        "title": titre,
        "era": era or "Unknown era",
        "year": annee_depuis_dates(file_date, first_preview, leak_date),
        "intendedAlbum": era or "Unsorted",
        "status": f"{type_track} • {available}",
        "quality": quality,
        "file": "",
        "privateLink": liens[0] if liens else "",
        "description": description,
        "info": {
            "source": SOURCE,
            "length": longueur,
            "version": type_track,
            "notes": notes,
            "dates": {
                "fileDate": file_date,
                "firstPreview": first_preview,
                "leakDate": leak_date,
            },
            "noteLinksBlock": [],
            "ogFilename": og,
        },
    }
    if len(liens) > 1:
        track["privateLinks"] = liens
    return track


def lire_json(chemin, defaut):
    if not chemin.exists():
        return defaut
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except Exception:
        return defaut


def ecrire_json(chemin, donnees):
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(donnees, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sync_index(tracks):
    if not FICHIER_INDEX.exists():
        return
    compact = json.dumps(tracks, ensure_ascii=False, separators=(",", ":"))
    encoded = base64.b64encode(compact.encode("utf-8")).decode("ascii")
    html = FICHIER_INDEX.read_text(encoding="utf-8")
    nouveau = re.sub(
        r'(<script type="text/plain" id="embeddedTracksDataB64">).*?(</script>)',
        lambda match: match.group(1) + encoded + match.group(2),
        html,
        flags=re.S,
    )
    if nouveau != html:
        FICHIER_INDEX.write_text(nouveau, encoding="utf-8")


def main():
    lignes = [ligne for ligne in lire_csv() if est_ligne_son(ligne)]
    tracks = lire_json(FICHIER_TRACKS, [])
    memoire = lire_json(FICHIER_MEMOIRE, {"initialized": False, "keys": []})
    if not isinstance(memoire, dict):
        memoire = {"initialized": False, "keys": []}
    deja_vus = set(memoire.get("keys") or [])
    cles_csv = [cle_ligne(ligne) for ligne in lignes]
    if not memoire.get("initialized"):
        ecrire_json(FICHIER_MEMOIRE, {"initialized": True, "keys": sorted(set(cles_csv))})
        print(f"Memoire creee avec {len(set(cles_csv))} lignes. Aucun ancien son ajoute.")
        return
    cles_tracks = {cle_track(track) for track in tracks if isinstance(track, dict)}
    nouveaux = []
    for numero, ligne in enumerate(lignes, start=1):
        cle = cle_ligne(ligne)
        if cle in deja_vus:
            continue
        deja_vus.add(cle)
        if cle in cles_tracks:
            continue
        track = convertir(ligne, numero)
        nouveaux.append(track)
        tracks.append(track)
        cles_tracks.add(cle)
    ecrire_json(FICHIER_MEMOIRE, {"initialized": True, "keys": sorted(deja_vus)})
    if nouveaux:
        ecrire_json(FICHIER_TRACKS, tracks)
        sync_index(tracks)
        print(f"{len(nouveaux)} nouveau(x) son(s) ajoute(s).")
        for track in nouveaux[:20]:
            print(f"- {track['title']} / {track['era']}")
    else:
        print("Aucun nouveau son.")


if __name__ == "__main__":
    main()
