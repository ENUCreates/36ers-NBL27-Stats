"""Player bios and career history for the Player cards (data/bios.json).

For every player who has appeared in an NBL27 game (data/players.json), read from ESPN (site.api.espn.com only):
  - bio: position, jersey, height, weight, date of birth, birthplace, college   (nbl athlete endpoint)
  - NBA regular-season averages by season and team                             (nba athlete stats)
  - NCAA averages by season and school                                         (mens-college-basketball athlete stats)
Fields ESPN does not have are left empty (never guessed). Extra sources can be merged later from
updater/bios_extra.json, keyed by ESPN athlete id, field by field (each field keeps its source).

Usage: python3 bios.py <repo_root> [max_age_days=7] [budget=25]
New players are always fetched. Entries older than max_age_days are refreshed, oldest first, at most `budget`
per run, so the daily load stays small and spread out; everything else is kept exactly as it was.
"""
import json, os, sys, re, time, datetime, subprocess

API = "https://site.api.espn.com/apis/common/v3/sports/basketball"
HERE = os.path.dirname(os.path.abspath(__file__))


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
    out, fetched = [], 0
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
        b["name"] = b.get("name") or name
        b["team"] = team
        # merge curated extras (e.g. RealGM / official NBL), field by field
        for k, v in (extra.get(pid) or {}).items():
            if k in ("history", "career"):
                b[k + "Extra"] = v
            elif k == "src":
                continue
            else:
                b[k] = v
                b.setdefault("src", {})[k] = (extra[pid].get("src") or {}).get(k, "extra")
        out.append(b)
    out.sort(key=lambda b: b["id"])
    json.dump(out, open(fp, "w"), separators=(",", ":"), sort_keys=True)
    have = lambda k: sum(1 for b in out if b.get(k))
    print(json.dumps({"players": len(out), "fetched": fetched, "with_height": have("heightCm"), "with_weight": have("weightKg"),
                      "with_dob": have("dob"), "with_college": have("college"), "with_nba_or_ncaa": sum(1 for b in out if b.get("history"))}))


if __name__ == "__main__":
    main()
