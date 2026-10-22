"""Player bios and career history for the Player cards (data/bios.json).

For every player who has appeared in an NBL27 game (data/players.json):
  ESPN (site.api.espn.com):
  - bio: position, jersey, height, weight, date of birth, birthplace, college   (nbl athlete endpoint)
  - NBA regular-season averages by season and team                             (nba athlete stats)
  - NCAA averages by season and school                                         (mens-college-basketball athlete stats)
  Official NBL data (official.py: the league's own box scores and profiles, republished by nblR on GitHub):
  - the official spelling of the player's name and his official player id
  - every earlier NBL regular season (2015-16 on), averaged from the official game lines   -> historyNBL
  - date of birth, height (cm), weight (kg), nationality from the official profile (players up to 2024-25)
  - official NBL27 headshot, stored as a small embedded image in data/photos.json (loaded by the page only when
    the Player cards tab is opened), re-downloaded only when the NBL changes the picture
Official fields take precedence over ESPN's (ESPN's dates of birth are sometimes a day out). A curated
updater/bios_extra.json (keyed by ESPN athlete id) can add or override fields last; each field keeps its source.
Fields nobody has are left empty (never guessed).

Usage: python3 bios.py <repo_root> [max_age_days=7] [budget=25]
New players are always fetched from ESPN; entries older than max_age_days are refreshed, oldest first, at most
`budget` per run. The official layer is rebuilt on every run (one download of a few MB); if it can't be loaded,
the previous official fields are kept as they were.
"""
import json, os, sys, re, time, datetime, subprocess, io, base64

API = "https://site.api.espn.com/apis/common/v3/sports/basketball"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
OFF_BIO = ("dob", "heightCm", "heightIn", "weightKg", "weightLb", "nationality", "nationalityCode", "jersey", "pos", "posName")
OFF_KEEP = ("historyNBL", "nblId", "nblName", "photoUrl")


def get(url):
    """Return (status, json) with retries; 404 -> (404, None)."""
    for k in range(4):
        r = subprocess.run(["curl", "-sS", "--compressed", "-m", "30", "-w", "\n%{http_code}", url], capture_output=True, text=True)
        body, _, code = r.stdout.rpartition("\n")
        if code == "404":
            return 404, None
        if code == "200":
            try:
                return 200, json.loads(body)
            except Exception:
                pass
        time.sleep(2 + k * 2)
    return 0, None


def height_in(s):
    m = re.match(r"\s*(\d+)'\s*(\d+)", s or "")
    return int(m.group(1)) * 12 + int(m.group(2)) if m else None


def weight_lb(s):
    m = re.match(r"\s*(\d+)", s or "")
    return int(m.group(1)) if m else None


def dob_iso(s):
    """ESPN's NBL athlete endpoint gives displayDOB as day/month/year (checked against its roster dateOfBirth)."""
    try:
        return datetime.datetime.strptime(s, "%d/%m/%Y").strftime("%Y-%m-%d")
    except Exception:
        return None


def season_rows(d, league):
    """ESPN stats payload -> list of per-season averages rows (team rows only, no 'Totals' lines)."""
    out = []
    if not d:
        return out
    cats = {c.get("name"): c for c in d.get("categories", [])}
    av = cats.get("averages")
    if not av:
        return out
    labels = av.get("labels", [])
    teams = d.get("teams") or {}
    for st in av.get("statistics", []):
        slug = st.get("teamSlug") or ""
        if st.get("teamId") is None or slug.endswith("Totals"):
            continue
        v = dict(zip(labels, st.get("stats", [])))
        num = lambda k: (float(v[k]) if v.get(k) not in (None, "", "-") else None)

        def pair(k):
            try:
                a, b = v[k].split("-")
                return float(a), float(b)
            except Exception:
                return None, None
        fgm, fga = pair("FG"); tpm, tpa = pair("3PT"); ftm, fta = pair("FT")
        team = (teams.get(slug) or {}).get("displayName") or slug.replace("-", " ").title()
        cc = "CA" if team.startswith("Toronto") else "US"   # NBA / NCAA: every team is in the USA except Toronto
        frac = lambda k: (round(num(k) / 100, 4) if num(k) is not None else None)
        out.append({"season": st.get("season", {}).get("displayName"), "team": team, "league": league, "country": cc,
                    "gp": int(num("GP") or 0), "gs": int(num("GS") or 0) if "GS" in v else None, "mpg": num("MIN"),
                    "pts": num("PTS"), "reb": num("REB"), "oreb": num("OR"), "dreb": num("DR"), "ast": num("AST"),
                    "stl": num("STL"), "blk": num("BLK"), "tov": num("TO"),
                    "fgm": fgm, "fga": fga, "fgp": frac("FG%"), "tpm": tpm, "tpa": tpa, "tpp": frac("3P%"),
                    "ftm": ftm, "fta": fta, "ftp": frac("FT%"), "src": "ESPN"})
    return out


def bio(pid):
    code, d = get(f"{API}/nbl/athletes/{pid}")
    a = (d or {}).get("athlete") or {}
    hi, wl = height_in(a.get("displayHeight")), weight_lb(a.get("displayWeight"))
    b = {"id": pid, "name": a.get("displayName"), "pos": (a.get("position") or {}).get("abbreviation"),
         "posName": (a.get("position") or {}).get("displayName"), "jersey": a.get("displayJersey") or a.get("jersey"),
         "heightIn": hi, "heightCm": round(hi * 2.54) if hi else None, "weightLb": wl,
         "weightKg": round(wl * 0.45359237) if wl else None, "dob": dob_iso(a.get("displayDOB")),
         "birthPlace": a.get("displayBirthPlace"), "college": (a.get("college") or {}).get("name")}
    src = {k: "ESPN" for k, v in b.items() if v not in (None, "") and k not in ("id",)}
    hist = []
    for lg, name in (("nba", "NBA"), ("mens-college-basketball", "NCAA")):
        c, s = get(f"{API}/{lg}/athletes/{pid}/stats")
        if c == 200:
            hist += season_rows(s, name)
    return b, src, hist, code


def photo_uri(url, box=(160, 200)):
    """Download an official headshot and return a small embedded WebP (transparency kept), or None."""
    r = subprocess.run(["curl", "-sSL", "--fail", "-m", "30", url], capture_output=True)
    if r.returncode != 0 or len(r.stdout) < 500:
        return None
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(r.stdout))
        im = im.convert("RGBA")
        bb = im.getchannel("A").getbbox()            # trim empty transparent margins
        if bb:
            im = im.crop(bb)
        w, h = im.size
        tw, th = box                                   # portrait 4:5, keep the head: crop from the top
        if w / h > tw / th:
            nw = int(h * tw / th); im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
        else:
            nh = int(w * th / tw); im = im.crop((0, 0, w, nh))
        im = im.resize(box, Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=72, method=4)
        return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception as ex:
        print("photo conversion failed:", url, ex)
        return None


def official_layer(root, seen):
    """-> ({pid: {...}}, notes) from the official NBL data, or (None, [reason]) if it can't be loaded."""
    try:
        import official as O
        off = O.load()
    except Exception as ex:
        return None, [f"official NBL data unavailable this run ({ex}); previous official fields kept"]
    games = json.load(open(os.path.join(root, "data", "games.json")))
    players = json.load(open(os.path.join(root, "data", "players.json")))
    cmp = O.compare(games, players, off)
    want = {}
    for pid, (name, team) in seen.items():
        pr = cmp["pairs"].get(pid)
        if pr and pr["family"]:
            want[pid] = (pr["first"], pr["family"])
        else:
            f, _, l = name.partition(" ")
            want[pid] = (f, l)
    hist, notes = O.history(off, want)
    ob, dobs = O.bios(off, want, {pid: pr["pid"] for pid, pr in cmp["pairs"].items()})
    pics = O.photos(off)
    notes = list(off.get("notes") or []) + notes
    out = {}
    for pid in seen:
        pr = cmp["pairs"].get(pid) or {}
        out[pid] = {"name": pr.get("name"), "nblId": pr.get("pid"), "history": hist.get(pid, []), "bio": ob.get(pid, {}),
                    "dobs": dobs.get(pid, {}), "photoUrl": pics.get(pr.get("pid")) if pr.get("pid") else None}
    return out, notes


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    max_age = float(sys.argv[2]) if len(sys.argv) > 2 else 7
    budget = int(sys.argv[3]) if len(sys.argv) > 3 else 25
    players = json.load(open(os.path.join(root, "data", "players.json")))
    fp = os.path.join(root, "data", "bios.json")
    old = {b["id"]: b for b in (json.load(open(fp)) if os.path.exists(fp) else [])}
    extra_fp = os.path.join(HERE, "bios_extra.json")
    extra = {k: v for k, v in (json.load(open(extra_fp)) if os.path.exists(extra_fp) else {}).items() if not k.startswith("_")}
    seen = {}
    for d in sorted(players, key=lambda d: d["date"]):
        for team, L in d["teams"].items():
            for p in L:
                seen[p["id"]] = (p["name"], team)   # latest team wins (sorted by date)
    today = datetime.date.today()
    age = lambda o: (today - datetime.date.fromisoformat(o["checked"])).days if o and o.get("checked") else 10 ** 6
    stale = sorted((pid for pid in seen if pid in old and age(old[pid]) >= max_age), key=lambda p: (-age(old[p]), p))
    refresh = set(stale[:budget])
    offl, notes = official_layer(root, seen)
    ph_fp = os.path.join(root, "data", "photos.json")
    photos = {d["id"]: d for d in (json.load(open(ph_fp)) if os.path.exists(ph_fp) else [])}
    out, fetched, pics_new, pics_failed = [], 0, 0, 0
    for pid, (name, team) in sorted(seen.items()):
        o = old.get(pid)
        fresh = o is not None and pid not in refresh
        if fresh:
            b = dict(o)
        else:
            bb, src, hist, code = bio(pid)
            fetched += 1
            if code != 200 and o:      # ESPN unavailable: keep what we had
                b = dict(o)
            else:
                b = {**bb, "src": src, "history": hist, "checked": today.isoformat()}
                for k in OFF_KEEP:      # official fields survive an ESPN refresh until the official layer redoes them
                    if o and k in o:
                        b[k] = o[k]
                if o:
                    for k in OFF_BIO:
                        if (o.get("src") or {}).get(k) == "NBL":
                            b[k] = o[k]; b["src"][k] = "NBL"
        b.setdefault("src", {})
        # ESPN-only values of the official fields, kept so the official layer can always re-check them
        espn = b.get("espnBio") or {k: b.get(k) for k in OFF_BIO if b["src"].get(k) == "ESPN"}
        if not fresh and code == 200:
            espn = {k: bb.get(k) for k in OFF_BIO if bb.get(k) not in (None, "")}
        b["espnBio"] = espn
        if offl is not None:
            L = offl[pid]
            if L["name"]:
                b["nblName"] = L["name"]; b["nblId"] = L["nblId"]
            ob = L["bio"]
            ey, oy = (espn.get("dob") or "")[:4], (ob.get("dob") or "")[:4]
            if ey and oy and ey != oy:
                # a different birth year means the official profile under this name may be another player: don't use it
                notes.append(f"{name}: official profile born {oy}, ESPN {ey} - official profile and history not used")
                ob = {}
                ref = ey
            else:
                ref = oy or ey
            hist = []
            for r in L["history"]:
                s4 = r["season"][:4]
                raw = f"{s4}-{int(s4) + 1}" if re.fullmatch(r"\d{4}", s4) else r["season"]
                d = L["dobs"].get((raw, r["team"]), "")
                if ref and d and d[:4] != ref:
                    notes.append(f"{name}: {r['season']} {r['team']} belongs to another player of the same name (born {d[:4]}), left out")
                    continue
                if ref and re.fullmatch(r"\d{4}", s4) and not 15 <= int(s4) - int(ref) <= 45:
                    notes.append(f"{name}: {r['season']} {r['team']} doesn't fit his age, left out")
                    continue
                hist.append(r)
            b["historyNBL"] = hist
            # official profile replaces ESPN's values; fields it lacks fall back to ESPN's
            for k in OFF_BIO:
                if (b["src"].get(k) == "NBL") and k not in ob and not (k == "heightIn" and "heightCm" in ob) and not (k == "weightLb" and "weightKg" in ob):
                    if k in espn:
                        b[k] = espn[k]; b["src"][k] = "ESPN"
                    else:
                        b.pop(k, None); b["src"].pop(k, None)
            for k, v in ob.items():
                if k == "season":
                    continue
                b[k] = v; b["src"][k] = "NBL"
            if "heightCm" in ob:
                b["heightIn"] = round(ob["heightCm"] / 2.54); b["src"]["heightIn"] = "NBL"
            if "weightKg" in ob:
                b["weightLb"] = round(ob["weightKg"] / 0.45359237); b["src"]["weightLb"] = "NBL"
            url = L["photoUrl"]
            if url:
                b["photoUrl"] = url
                if (photos.get(pid) or {}).get("from") != url:
                    uri = photo_uri(url)
                    if uri:
                        photos[pid] = {"id": pid, "photo": uri, "from": url}; pics_new += 1
                    else:
                        pics_failed += 1
        b.pop("photo", None); b.pop("photoFrom", None)
        if pid in photos:
            b["src"]["photo"] = "NBL"
        else:
            b["src"].pop("photo", None)
        b["name"] = b.get("nblName") or b.get("name") or name
        b["team"] = team
        # merge curated extras (e.g. RealGM), field by field, last
        for k, v in (extra.get(pid) or {}).items():
            if k in ("history", "career"):
                b[k + "Extra"] = v
            elif k == "src":
                continue
            else:
                b[k] = v
                b["src"][k] = (extra[pid].get("src") or {}).get(k, "extra")
        out.append(b)
    out.sort(key=lambda b: b["id"])
    json.dump(out, open(fp, "w"), separators=(",", ":"), sort_keys=True)
    json.dump([photos[k] for k in sorted(photos) if k in seen], open(ph_fp, "w"), separators=(",", ":"), sort_keys=True)
    have = lambda k: sum(1 for b in out if b.get(k))
    print(json.dumps({"players": len(out), "fetched": fetched, "official": offl is not None,
                      "with_height": have("heightCm"), "with_weight": have("weightKg"), "with_dob": have("dob"),
                      "with_nationality": have("nationality"), "with_college": have("college"),
                      "with_nba_or_ncaa": have("history"), "with_nbl_history": have("historyNBL"),
                      "with_photo": sum(1 for k in photos if k in seen), "photos_new": pics_new, "photos_unreachable": pics_failed,
                      "notes": notes}, indent=1))


if __name__ == "__main__":
    main()
