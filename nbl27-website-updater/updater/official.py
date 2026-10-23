"""Official NBL numbers, from the league's own box scores.

The nblR project (github.com/JaseZiv/nblr_data, by Jason Zivkovic) republishes the NBL's official match data as
GitHub release files, refreshed about once a week:
  box_player/box_player.rds       every player's official box-score line, every game since 2015-16 (NBL27 included)
  box_team/box_team.rds           official team totals (team rebounds and team turnovers included)
  match_results/results_wide.rds  match list: season, REGULAR / FINALS, status, tip-off time (UTC)
  csvs/nbl_player_bios.rds        official player profiles up to 2024-25: date of birth, height (cm), weight (kg), nationality
This module downloads them and provides:
  pair()       our ESPN player ids <-> the official players (same NBL27 game and team; same name, or a close spelling
               with an identical scoring line)
  compare()    our ESPN-built NBL27 box scores vs the official ones -> corrections for a few counting fields
  history()    every player's earlier NBL regular seasons (per-game averages from the official game lines)
  bios()       official profile fields
Needs pyreadr (pip install pyreadr).

Usage (report only, changes nothing): python3 official.py <repo_root>
"""
import os, sys, json, re, subprocess, unicodedata, datetime, difflib, time, tempfile
from zoneinfo import ZoneInfo

SYD = ZoneInfo("Australia/Sydney")
REL = "https://github.com/JaseZiv/nblr_data/releases/download/"
FILES = {"box": "box_player/box_player.rds", "team": "box_team/box_team.rds",
         "res": "match_results/results_wide.rds", "bios": "csvs/nbl_player_bios.rds"}
TEAMNAME = {"NZ Breakers": "New Zealand Breakers", "The Hawks": "Illawarra Hawks"}   # official name -> this site's name
CUR = "2026-2027"
STAT = (("pts", "points"), ("fgm", "field_goals_made"), ("fga", "field_goals_attempted"), ("tpm", "three_pointers_made"),
        ("tpa", "three_pointers_attempted"), ("ftm", "free_throws_made"), ("fta", "free_throws_attempted"),
        ("oreb", "rebounds_offensive"), ("dreb", "rebounds_defensive"), ("ast", "assists"), ("stl", "steals"),
        ("blk", "blocks"), ("tov", "turnovers"), ("pf", "fouls_personal"))
SCORING = ("pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta")
# Fields set to the official value when it differs. Scoring, minutes and +/- already match the official box scores
# exactly (any difference there is only reported). Fouls are left alone: the official feed's personal-foul field
# leaves out offensive fouls, while ESPN's PF counts every foul, as a FIBA box score does.
FIX = ("ast", "tov", "oreb", "dreb", "stl", "blk")
NICK = {"mitch": "mitchell", "matt": "matthew", "nick": "nicholas", "will": "william", "sam": "samuel", "tom": "thomas",
        "tommy": "thomas", "josh": "joshua", "chris": "christopher", "dan": "daniel", "danny": "daniel", "mike": "michael",
        "alex": "alexander", "ben": "benjamin", "jake": "jacob", "tim": "timothy", "zac": "zachary", "zach": "zachary",
        "nate": "nathan", "jon": "jonathan", "tony": "anthony", "andy": "andrew", "rob": "robert", "jim": "james",
        "jimmy": "james", "joe": "joseph", "dave": "david", "steve": "steven", "pat": "patrick", "lachie": "lachlan",
        "cam": "cameron", "fred": "frederick", "freddie": "frederick", "harry": "harrison", "jono": "jonathan"}
# earlier official names of players whose name changed in the official box scores: current name key -> earlier keys.
# Each one checked by hand against the player's other records before it was added.
ALIAS = {"izayahleafa": {"izayahmauriohooholeafa"}}   # 2020-21 South East Melbourne box scores: "Izayah Mauriohooho-Le Afa"
COUNTRY = {"AUSTRALIA": ("Australia", "AU"), "NEW ZEALAND": ("New Zealand", "NZ"), "UNITED STATES": ("United States", "US"),
           "UNITED STATES OF AMERICA": ("United States", "US"), "CANADA": ("Canada", "CA"), "SUDAN": ("Sudan", "SD"),
           "SOUTH SUDAN": ("South Sudan", "SS"), "FRANCE": ("France", "FR"), "CHINA": ("China", "CN"), "GERMANY": ("Germany", "DE"),
           "JAPAN": ("Japan", "JP"), "BRAZIL": ("Brazil", "BR"), "PHILIPPINES": ("Philippines", "PH"), "MEXICO": ("Mexico", "MX"),
           "SENEGAL": ("Senegal", "SN"), "SPAIN": ("Spain", "ES"), "TRINIDAD AND TOBAGO": ("Trinidad and Tobago", "TT"),
           "SWEDEN": ("Sweden", "SE"), "LITHUANIA": ("Lithuania", "LT"), "NIGERIA": ("Nigeria", "NG"), "INDIA": ("India", "IN"),
           "RUSSIAN FEDERATION": ("Russia", "RU"), "RUSSIA": ("Russia", "RU"), "SERBIA": ("Serbia", "RS"), "CROATIA": ("Croatia", "HR"),
           "GREECE": ("Greece", "GR"), "ITALY": ("Italy", "IT"), "TURKEY": ("Turkey", "TR"), "ISRAEL": ("Israel", "IL"),
           "LATVIA": ("Latvia", "LV"), "SLOVENIA": ("Slovenia", "SI"), "MONTENEGRO": ("Montenegro", "ME"), "CAMEROON": ("Cameroon", "CM"),
           "GHANA": ("Ghana", "GH"), "KENYA": ("Kenya", "KE"), "FIJI": ("Fiji", "FJ"), "SAMOA": ("Samoa", "WS"), "TONGA": ("Tonga", "TO"),
           "PAPUA NEW GUINEA": ("Papua New Guinea", "PG"), "UNITED KINGDOM": ("United Kingdom", "GB"), "GREAT BRITAIN": ("Great Britain", "GB"),
           "ENGLAND": ("England", "GB"), "IRELAND": ("Ireland", "IE"), "NETHERLANDS": ("Netherlands", "NL"), "BELGIUM": ("Belgium", "BE"),
           "PUERTO RICO": ("Puerto Rico", "PR"), "DOMINICAN REPUBLIC": ("Dominican Republic", "DO"), "JAMAICA": ("Jamaica", "JM"),
           "BAHAMAS": ("Bahamas", "BS"), "ARGENTINA": ("Argentina", "AR"), "VENEZUELA": ("Venezuela", "VE"), "SOUTH KOREA": ("South Korea", "KR"),
           "KOREA": ("South Korea", "KR"), "TAIWAN": ("Taiwan", "TW"), "CHINESE TAIPEI": ("Chinese Taipei", "TW"), "LEBANON": ("Lebanon", "LB"),
           "IRAN": ("Iran", "IR"), "JORDAN": ("Jordan", "JO"), "EGYPT": ("Egypt", "EG"), "SOUTH AFRICA": ("South Africa", "ZA"),
           "ETHIOPIA": ("Ethiopia", "ET"), "UGANDA": ("Uganda", "UG"), "CONGO": ("Congo", "CG"), "DR CONGO": ("DR Congo", "CD"),
           "MALI": ("Mali", "ML"), "ANGOLA": ("Angola", "AO"), "CZECH REPUBLIC": ("Czech Republic", "CZ"), "POLAND": ("Poland", "PL"),
           "UKRAINE": ("Ukraine", "UA"), "FINLAND": ("Finland", "FI"), "DENMARK": ("Denmark", "DK"), "NORWAY": ("Norway", "NO"),
           "SWITZERLAND": ("Switzerland", "CH"), "AUSTRIA": ("Austria", "AT"), "PORTUGAL": ("Portugal", "PT"), "BOSNIA AND HERZEGOVINA":
           ("Bosnia and Herzegovina", "BA"), "NORTH MACEDONIA": ("North Macedonia", "MK"), "GEORGIA": ("Georgia", "GE"),
           "HUNGARY": ("Hungary", "HU"), "ROMANIA": ("Romania", "RO"), "BULGARIA": ("Bulgaria", "BG"), "ESTONIA": ("Estonia", "EE")}


CC3 = {"AUS": ("Australia", "AU"), "NZL": ("New Zealand", "NZ"), "USA": ("United States", "US"), "CAN": ("Canada", "CA"),
       "BRA": ("Brazil", "BR"), "SUD": ("Sudan", "SD"), "SDN": ("Sudan", "SD"), "SSD": ("South Sudan", "SS"), "DEU": ("Germany", "DE"),
       "GER": ("Germany", "DE"), "SRB": ("Serbia", "RS"), "FRA": ("France", "FR"), "TRI": ("Trinidad and Tobago", "TT"),
       "TTO": ("Trinidad and Tobago", "TT"), "LTU": ("Lithuania", "LT"), "GBR": ("Great Britain", "GB"), "ENG": ("England", "GB"),
       "IRL": ("Ireland", "IE"), "ITA": ("Italy", "IT"), "ESP": ("Spain", "ES"), "GRE": ("Greece", "GR"), "GRC": ("Greece", "GR"),
       "TUR": ("Turkey", "TR"), "ISR": ("Israel", "IL"), "LAT": ("Latvia", "LV"), "LVA": ("Latvia", "LV"), "SLO": ("Slovenia", "SI"),
       "SVN": ("Slovenia", "SI"), "CRO": ("Croatia", "HR"), "HRV": ("Croatia", "HR"), "MNE": ("Montenegro", "ME"),
       "BIH": ("Bosnia and Herzegovina", "BA"), "MKD": ("North Macedonia", "MK"), "CZE": ("Czechia", "CZ"), "POL": ("Poland", "PL"),
       "UKR": ("Ukraine", "UA"), "RUS": ("Russia", "RU"), "FIN": ("Finland", "FI"), "SWE": ("Sweden", "SE"), "NOR": ("Norway", "NO"),
       "DEN": ("Denmark", "DK"), "DNK": ("Denmark", "DK"), "NED": ("Netherlands", "NL"), "NLD": ("Netherlands", "NL"),
       "BEL": ("Belgium", "BE"), "SUI": ("Switzerland", "CH"), "CHE": ("Switzerland", "CH"), "AUT": ("Austria", "AT"),
       "POR": ("Portugal", "PT"), "PRT": ("Portugal", "PT"), "ARG": ("Argentina", "AR"), "PUR": ("Puerto Rico", "PR"),
       "PRI": ("Puerto Rico", "PR"), "DOM": ("Dominican Republic", "DO"), "JAM": ("Jamaica", "JM"), "BAH": ("Bahamas", "BS"),
       "BHS": ("Bahamas", "BS"), "MEX": ("Mexico", "MX"), "VEN": ("Venezuela", "VE"), "CHN": ("China", "CN"), "JPN": ("Japan", "JP"),
       "KOR": ("South Korea", "KR"), "TPE": ("Chinese Taipei", "TW"), "TWN": ("Taiwan", "TW"), "PHI": ("Philippines", "PH"),
       "PHL": ("Philippines", "PH"), "LBN": ("Lebanon", "LB"), "LIB": ("Lebanon", "LB"), "IRI": ("Iran", "IR"), "IRN": ("Iran", "IR"),
       "JOR": ("Jordan", "JO"), "EGY": ("Egypt", "EG"), "NGR": ("Nigeria", "NG"), "NGA": ("Nigeria", "NG"), "SEN": ("Senegal", "SN"),
       "CMR": ("Cameroon", "CM"), "GHA": ("Ghana", "GH"), "KEN": ("Kenya", "KE"), "MLI": ("Mali", "ML"), "ANG": ("Angola", "AO"),
       "AGO": ("Angola", "AO"), "COD": ("DR Congo", "CD"), "CGO": ("Congo", "CG"), "COG": ("Congo", "CG"), "RSA": ("South Africa", "ZA"),
       "ZAF": ("South Africa", "ZA"), "ETH": ("Ethiopia", "ET"), "UGA": ("Uganda", "UG"), "FIJ": ("Fiji", "FJ"), "FJI": ("Fiji", "FJ"),
       "SAM": ("Samoa", "WS"), "WSM": ("Samoa", "WS"), "TGA": ("Tonga", "TO"), "TON": ("Tonga", "TO"), "PNG": ("Papua New Guinea", "PG"),
       "IND": ("India", "IN"), "CUB": ("Cuba", "CU"), "HAI": ("Haiti", "HT"), "HTI": ("Haiti", "HT"), "GEO": ("Georgia", "GE"),
       "HUN": ("Hungary", "HU"), "ROU": ("Romania", "RO"), "BUL": ("Bulgaria", "BG"), "BGR": ("Bulgaria", "BG"), "EST": ("Estonia", "EE"),
       "ISL": ("Iceland", "IS"), "SVK": ("Slovakia", "SK"), "BLR": ("Belarus", "BY"), "KAZ": ("Kazakhstan", "KZ"), "QAT": ("Qatar", "QA")}


POS = {"G": ("G", "Guard"), "F": ("F", "Forward"), "C": ("C", "Centre"), "GF": ("G-F", "Guard-Forward"), "G-F": ("G-F", "Guard-Forward"),
       "FC": ("F-C", "Forward-Centre"), "F-C": ("F-C", "Forward-Centre"), "PG": ("G", "Guard"), "SG": ("G", "Guard"),
       "SF": ("F", "Forward"), "PF": ("F", "Forward")}


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", s)
    return re.sub(r"[^a-z]", "", s)


def nkey(first, family):
    return norm(f"{first} {family}")


def variants(first, family):
    """Name keys for the same person under a common short/long first name (Mitch <-> Mitchell)."""
    f = norm(first)
    out = set()
    for a, b in NICK.items():
        if f == a:
            out.add(norm(f"{b} {family}"))
        if f == b:
            out.add(norm(f"{a} {family}"))
    return out


def iv(v):
    try:
        return 0 if v is None or v != v else int(float(v))
    except Exception:
        return 0


def sv(v):
    return "" if v is None or (isinstance(v, float) and v != v) else str(v)


def secs(v):
    s = sv(v)
    if ":" in s:
        try:
            m, x = s.split(":")[:2]
            return int(m) * 60 + int(float(x))
        except Exception:
            return 0
    try:
        return int(round(float(s) * 60))
    except Exception:
        return 0


def season_label(s):
    m = re.fullmatch(r"(\d{4})-(\d{4})", sv(s))
    return f"{m.group(1)}-{m.group(2)[2:]}" if m else sv(s)


def download(dest):
    os.makedirs(dest, exist_ok=True)
    out = {}
    for k, path in FILES.items():
        fp = os.path.join(dest, os.path.basename(path))
        if os.path.exists(fp) and os.path.getsize(fp) > 1000:      # already fetched during this run
            out[k] = fp
            continue
        err = ""
        for i in range(3):
            r = subprocess.run(["curl", "-sSL", "--fail", "-m", "180", "-o", fp, REL + path], capture_output=True, text=True)
            if r.returncode == 0 and os.path.exists(fp) and os.path.getsize(fp) > 1000:
                break
            err = (r.stderr or "").strip()[:200]
            time.sleep(3 + 3 * i)
        else:
            raise RuntimeError(f"official data download failed ({path}): {err}")
        out[k] = fp
    return out


def load(dest=None):
    """All official data -> {'box','team','res','bios','roster','live','notes'} as lists of plain dicts.
    Earlier seasons and the 2015-25 profiles come from the nblR release files; the current season comes straight
    from the NBL's own API (same day) when it answers, otherwise from the nblR files (about a week behind)."""
    dest = dest or os.environ.get("NBL_OFFICIAL_CACHE") or tempfile.mkdtemp()
    out = {"box": [], "team": [], "res": [], "bios": [], "roster": [], "live": False, "notes": []}
    try:
        import pyreadr
        files = download(dest)
        for k, fp in files.items():
            out[k] = pyreadr.read_r(fp)[None].to_dict("records")
    except Exception as ex:
        out["notes"].append(f"nblR files unavailable: {ex}")
    try:
        res, box, team, roster = rosetta_current(os.path.join(dest, "rosetta"))
        out["res"] = [r for r in out["res"] if r["season"] != CUR] + res
        out["box"] = [r for r in out["box"] if r["season"] != CUR] + box
        out["team"] = [r for r in out["team"] if r["season"] != CUR] + team
        out["roster"], out["live"] = roster, True
    except Exception as ex:
        out["notes"].append(f"NBL API unavailable, current season from the nblR files: {ex}")
    if not out["box"]:
        raise RuntimeError("; ".join(out["notes"]) or "no official data")
    return out


ROSETTA = "https://prod.rosetta.nbl.com.au/get/"
RHEAD = ["-H", "accept: */*", "-H", "origin: https://nbl.com.au", "-H", "referer: https://nbl.com.au/", "-A",
         "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"]
ROSETTA_YEAR = "2026"          # the NBL API names the 2026-27 season "2026"


def rget(path, dest=None, fresh=False):
    """GET a JSON document from the NBL's own API (prod.rosetta.nbl.com.au), with retries; cached in dest."""
    fp = os.path.join(dest, re.sub(r"[^A-Za-z0-9_.-]", "_", path) + ".json") if dest else None
    if fp and not fresh and os.path.exists(fp):
        return json.load(open(fp))
    err = ""
    for i in range(3):
        os.makedirs(dest, exist_ok=True) if dest else None
        r = subprocess.run(["curl", "-sS", "--compressed", "--fail", "-m", "60"] + RHEAD + [ROSETTA + path], capture_output=True, text=True)
        if r.returncode == 0:
            try:
                d = json.loads(r.stdout)
                if fp:
                    json.dump(d, open(fp, "w"))
                return d
            except Exception as ex:
                err = f"bad JSON: {ex}"
        else:
            err = (r.stderr or "").strip()[:200]
        time.sleep(2 + 3 * i)
    raise RuntimeError(f"NBL API {path}: {err}")


def rosetta_current(dest):
    """Current-season official data straight from the NBL's API, in the same row shapes as the nblR files:
    -> (res rows, box rows, team rows, roster profiles). Same-day, unlike the weekly nblR files."""
    lst = rget(f"nbl/matches/in/season/{ROSETTA_YEAR}?limit=400", dest, fresh=True)["data"]
    res, box, team = [], [], []
    for m in sorted(lst, key=lambda m: (m.get("start_time") or "", m["id"])):
        st, ty = sv(m.get("match_status")).lower(), sv(m.get("match_type")).lower()
        res.append({"season": CUR, "match_id": m["id"], "match_status": "COMPLETE" if st == "complete" else st.upper(),
                    "match_type": "REGULAR" if ty == "regular" else ty.upper(), "round_number": sv(m.get("round")),
                    "home_team_name": m["home_team"]["name"], "away_team_name": m["away_team"]["name"],
                    "match_time_utc": sv(m.get("start_time"))})
        if st != "complete" or ty != "regular":
            continue
        time.sleep(0.3)
        d = rget(f"match/{m['id']}/live/all", dest)["data"][0]
        if sv(d.get("match_status")).lower() != "complete":
            continue
        tn = {}
        for side in ("home_team", "away_team"):
            for t in (d.get(side) or {}, m.get(side) or {}):
                for k in ("id", "external_id"):
                    if t.get(k):
                        tn[t[k]] = t.get("name") or tn.get(t[k])
        tname = lambda t: (t.get("name") or tn.get(t.get("id")) or tn.get(t.get("external_id"))) if isinstance(t, dict) else tn.get(t)
        for x in d.get("player_match_statistics") or []:
            if sv(x.get("period")) != "0":
                continue
            p = x.get("player") or {}
            box.append({"season": CUR, "match_id": m["id"], "team_name": tname(x.get("team")),
                        "first_name": p.get("first_name"), "family_name": p.get("last_name"), "player_id": p.get("id"),
                        "minutes": x.get("minutes"), "points": x.get("points"),
                        "field_goals_made": x.get("field_goals_made"), "field_goals_attempted": x.get("field_goals_attempted"),
                        "three_pointers_made": x.get("three_points_made"), "three_pointers_attempted": x.get("three_points_attempted"),
                        "free_throws_made": x.get("free_throws_made"), "free_throws_attempted": x.get("free_throws_attempted"),
                        "rebounds_offensive": x.get("offensive_rebounds"), "rebounds_defensive": x.get("defensive_rebounds"),
                        "rebounds_total": x.get("rebounds"), "assists": x.get("assists"), "steals": x.get("steals"),
                        "blocks": x.get("blocks"), "turnovers": x.get("turnovers"), "fouls_personal": x.get("personal_fouls"),
                        "plus_minus": x.get("plus_minus"), "starter": 1 if x.get("starter") else 0,
                        "photo_t": p.get("external_player_image") or p.get("image")})
        for x in d.get("team_match_statistics") or []:
            if sv(x.get("period")) != "0":
                continue
            team.append({"season": CUR, "match_id": m["id"], "name": tname(x.get("team")), "score": x.get("points"),
                         "points": x.get("points"), "field_goals_made": x.get("field_goals_made"),
                         "field_goals_attempted": x.get("field_goals_attempted"), "three_pointers_made": x.get("three_points_made"),
                         "three_pointers_attempted": x.get("three_points_attempted"), "free_throws_made": x.get("free_throws_made"),
                         "free_throws_attempted": x.get("free_throws_attempted"), "rebounds_offensive": x.get("offensive_rebounds"),
                         "rebounds_defensive": x.get("defensive_rebounds"), "rebounds_total": x.get("rebounds"),
                         "assists": x.get("assists"), "steals": x.get("steals"), "blocks": x.get("blocks"),
                         "turnovers": x.get("turnovers"), "fouls_total": x.get("fouls")})
    roster = rget(f"nbl/players/in/season/{ROSETTA_YEAR}", dest, fresh=True)["data"]
    return res, box, team, roster


def tip_syd(utc):
    s = sv(utc)[:19]
    try:
        return datetime.datetime.fromisoformat(s).replace(tzinfo=datetime.timezone.utc).astimezone(SYD)
    except Exception:
        return None


def nbl27_index(off):
    """(sydney date, frozenset(teams)) -> official match id, for completed NBL27 regular-season games with box scores."""
    have = {r["match_id"] for r in off["box"] if r["season"] == CUR}
    idx = {}
    for r in off["res"]:
        if r["season"] != CUR or sv(r["match_status"]) != "COMPLETE" or sv(r["match_type"]) != "REGULAR":
            continue
        if r["match_id"] not in have:
            continue
        t = tip_syd(r["match_time_utc"])
        if not t:
            continue
        teams = frozenset(TEAMNAME.get(x, x) for x in (sv(r["home_team_name"]), sv(r["away_team_name"])))
        idx[(t.strftime("%Y-%m-%d"), teams)] = r["match_id"]
    return idx


def _lines(off):
    by = {}
    for r in off["box"]:
        if r["season"] == CUR:
            by.setdefault(r["match_id"], {}).setdefault(TEAMNAME.get(sv(r["team_name"]), sv(r["team_name"])), []).append(r)
    return by


def _team_rows(off):
    by = {}
    for r in off["team"]:
        if r["season"] == CUR:
            by[(r["match_id"], TEAMNAME.get(sv(r["name"]), sv(r["name"])))] = r
    return by


def _match_team(L, rows):
    """Pair our player lines (L) with official rows of the same team-game. Returns [(ours, official, how)], unpaired."""
    left = list(L)
    out, rest = [], []
    for r in rows:
        k = nkey(r["first_name"], r["family_name"])
        hit = [x for x in left if norm(x["name"]) == k]
        if len(hit) == 1:
            out.append((hit[0], r, "name")); left.remove(hit[0])
        else:
            rest.append(r)
    unpaired = []
    for r in rest:
        k = nkey(r["first_name"], r["family_name"])
        best, sc = None, 0
        for x in left:
            s = difflib.SequenceMatcher(None, norm(x["name"]), k).ratio()
            if s > sc:
                best, sc = x, s
        if best is not None and sc >= 0.8 and all(best.get(f, 0) == iv(r[c]) for f, c in STAT if f in SCORING) and secs(r["minutes"]) > 0:
            out.append((best, r, "spelling")); left.remove(best)
        else:
            unpaired.append(r)
    return out, unpaired, left


def compare(games, players, off):
    """-> dict with pairs (espn id -> official name/id), per-game corrections and notes.
    games/players: this site's games.json / players.json lists."""
    idx, lines, trows = nbl27_index(off), _lines(off), _team_rows(off)
    P = {p["espnId"]: p for p in players}
    res = {"pairs": {}, "fix_players": [], "fix_teams": [], "notes": [], "checked": [], "missing": []}
    for g in games:
        k = (g["date"], frozenset((g["home"]["team"], g["away"]["team"])))
        mid = idx.get(k)
        eid = g["id"].replace("espn-", "")
        if not mid:
            res["missing"].append(eid); continue
        p = P.get(eid)
        if not p:
            continue
        res["checked"].append(eid)
        for side in ("home", "away"):
            T = g[side]; team = T["team"]
            rows = lines.get(mid, {}).get(team, [])
            mm, unpaired, left = _match_team(p["teams"].get(team, []), rows)
            for x, r, how in mm:
                nm = f"{sv(r['first_name'])} {sv(r['family_name'])}".strip()
                res["pairs"][x["id"]] = {"name": nm, "first": sv(r["first_name"]).strip(), "family": sv(r["family_name"]).strip(),
                                         "pid": sv(r["player_id"])}
                bad = [f for f, c in STAT if f in SCORING and x.get(f, 0) != iv(r[c])]
                if abs(x.get("min", 0) * 60 - secs(r["minutes"])) > 60:
                    bad.append("min")
                if iv(r.get("plus_minus")) != x.get("pm", 0) and sv(r.get("plus_minus")) != "":
                    bad.append("+/-")
                if bad:
                    res["notes"].append(f"{g['date']} {team} {x['name']}: official differs in {', '.join(bad)} (not changed)")
                    continue
                for f, c in STAT:
                    if f in FIX and x.get(f, 0) != iv(r[c]):
                        res["fix_players"].append({"eid": eid, "team": team, "id": x["id"], "name": x["name"], "field": f,
                                                   "ours": x.get(f, 0), "official": iv(r[c])})
            for r in unpaired:
                if secs(r["minutes"]) > 0:
                    res["notes"].append(f"{g['date']} {team}: official box has {sv(r['first_name'])} {sv(r['family_name'])} "
                                        f"({sv(r['minutes'])}, {iv(r['points'])} pts), ESPN does not")
            for x in left:
                if x.get("min", 0) > 0:
                    res["notes"].append(f"{g['date']} {team}: ESPN has {x['name']} ({x.get('min')} min), the official box does not")
            # team totals: only from an internally consistent official team row with the same final score
            tr = trows.get((mid, team))
            if tr is None:
                continue
            opts = {f: sum(iv(r[c]) for r in rows) for f, c in STAT}
            tcol = lambda f, c: "score" if f == "pts" else c       # the team row's points field is empty; its score is the total
            ok = iv(tr["score"]) == T["pts"] and all(iv(tr[tcol(f, c)]) == opts[f] for f, c in STAT if f in SCORING)
            if not ok:
                res["notes"].append(f"{g['date']} {team}: official team row inconsistent or different score (team totals not changed)")
                continue
            off_t = {"oreb": iv(tr["rebounds_offensive"]) + iv(tr.get("rebounds_team_offensive")),
                     "dreb": iv(tr["rebounds_defensive"]) + iv(tr.get("rebounds_team_defensive")),
                     "ast": iv(tr["assists"]), "stl": iv(tr["steals"]), "blk": iv(tr["blocks"]),
                     "tov": iv(tr["turnovers"]) + iv(tr.get("turnovers_team"))}
            if iv(tr.get("rebounds_total")) and off_t["oreb"] + off_t["dreb"] != iv(tr["rebounds_total"]):
                res["notes"].append(f"{g['date']} {team}: official rebound split doesn't add up (team totals not changed)")
                continue
            for f in FIX:
                if T.get(f) != off_t[f]:
                    res["fix_teams"].append({"eid": eid, "team": team, "field": f, "ours": T.get(f), "official": off_t[f]})
    return res


def apply(games, players, cmp):
    """Apply compare() corrections in place. Returns a list of human-readable change lines."""
    G = {g["id"].replace("espn-", ""): g for g in games}
    P = {p["espnId"]: p for p in players}
    out = []
    for c in cmp["fix_players"]:
        for x in P[c["eid"]]["teams"][c["team"]]:
            if x["id"] == c["id"]:
                x[c["field"]] = c["official"]
        out.append(f"{G[c['eid']]['date']} {c['name']} ({c['team']}) {c['field']} {c['ours']}->{c['official']}")
    for c in cmp["fix_teams"]:
        g = G[c["eid"]]
        T = g["home"] if g["home"]["team"] == c["team"] else g["away"]
        T[c["field"]] = c["official"]
        out.append(f"{g['date']} {c['team']} team {c['field']} {c['ours']}->{c['official']}")
    return out


def rename(cols, names):
    """Official spellings (names: espn id -> name) in every collection that stores player names."""
    n = 0
    for p in cols.get("players", []):
        for L in p.get("teams", {}).values():
            for x in L:
                if x.get("id") in names and x.get("name") != names[x["id"]]:
                    x["name"] = names[x["id"]]; n += 1
    for c in ("lineups", "events"):
        for d in cols.get(c, []):
            nm = d.get("names") or {}
            for i in list(nm):
                if i in names and nm[i] != names[i]:
                    nm[i] = names[i]; n += 1
    for d in cols.get("pshots", []):
        for t in (d.get("teams") or {}).values():
            for i, x in (t.get("p") or {}).items():
                if i in names and isinstance(x, dict) and x.get("n") != names[i]:
                    x["n"] = names[i]; n += 1
    return n


def _regular(off):
    reg = {}
    for r in off["res"]:
        if sv(r["match_type"]) == "REGULAR" and sv(r["match_status"]) == "COMPLETE":
            reg[r["match_id"]] = tip_syd(r["match_time_utc"])
    return reg


def history(off, want, before=CUR):
    """Earlier NBL regular seasons for the players in `want` ({espn id: (first, family)}).
    -> {espn id: [season rows]}, review notes. Per-game averages are computed from the official game lines."""
    reg = _regular(off)
    keys = {}
    for pid, (first, fam) in want.items():
        keys.setdefault(nkey(first, fam), set()).add(pid)
    alt = {}
    for pid, (first, fam) in want.items():
        for v in variants(first, fam) | ALIAS.get(nkey(first, fam), set()):
            alt.setdefault(v, set()).add(pid)
    agg = {}
    for r in off["box"]:
        s = sv(r["season"])
        if s >= before or r["match_id"] not in reg:
            continue
        k = nkey(r["first_name"], r["family_name"])
        if k not in keys and k not in alt:
            continue
        t = secs(r["minutes"])
        if t <= 0:
            continue
        team = TEAMNAME.get(sv(r["team_name"]), sv(r["team_name"]))
        a = agg.setdefault((k, s, team), {"gp": 0, "gs": 0, "sec": 0, "first": None, "name": f"{sv(r['first_name'])} {sv(r['family_name'])}",
                                          **{f: 0 for f, _ in STAT}, "reb": 0})
        a["gp"] += 1; a["gs"] += 1 if iv(r.get("starter")) == 1 else 0; a["sec"] += t
        for f, c in STAT:
            a[f] += iv(r[c])
        a["reb"] += iv(r.get("rebounds_total"))
        tip = reg[r["match_id"]]
        if tip and (a["first"] is None or tip < a["first"]):
            a["first"] = tip
    out, notes = {}, []
    exact_seasons = {}
    for (k, s, team) in agg:
        exact_seasons.setdefault(k, set()).add(s)
    for (k, s, team), a in sorted(agg.items(), key=lambda kv: (kv[0][1], kv[1]["first"].isoformat() if kv[1]["first"] else "", kv[0][2], kv[0][0])):
        if k in keys:
            pids = keys[k]
        else:
            pids = {p for p in alt[k] if s not in exact_seasons.get(nkey(*want[p]), set())}
            for p in pids:
                notes.append(f"{want[p][0]} {want[p][1]}: {season_label(s)} {team} linked under the official name '{a['name']}'")
        gp = a["gp"]
        pg = lambda v: round(v / gp, 2)
        frac = lambda m, n: round(m / n, 4) if n else None
        if a["reb"] and a["reb"] != a["oreb"] + a["dreb"]:
            notes.append(f"{a['name']} {season_label(s)} {team}: rebounds {a['reb']} != {a['oreb']}+{a['dreb']} in the official lines")
        row = {"season": season_label(s), "team": team, "league": "NBL", "country": "NZ" if team == "New Zealand Breakers" else "AU",
               "gp": gp, "gs": a["gs"], "mpg": round(a["sec"] / 60 / gp, 2), "pts": pg(a["pts"]), "reb": pg(a["oreb"] + a["dreb"]),
               "oreb": pg(a["oreb"]), "dreb": pg(a["dreb"]), "ast": pg(a["ast"]), "stl": pg(a["stl"]), "blk": pg(a["blk"]),
               "tov": pg(a["tov"]), "fgm": pg(a["fgm"]), "fga": pg(a["fga"]), "fgp": frac(a["fgm"], a["fga"]),
               "tpm": pg(a["tpm"]), "tpa": pg(a["tpa"]), "tpp": frac(a["tpm"], a["tpa"]), "ftm": pg(a["ftm"]), "fta": pg(a["fta"]),
               "ftp": frac(a["ftm"], a["fta"]), "src": "NBL",
               "tot": {"sec": a["sec"], **{f: a[f] for f in ("pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "oreb", "dreb", "ast", "stl", "blk", "tov")}}}
        for p in sorted(pids):
            out.setdefault(p, []).append(row)
    return out, notes


def photos(off):
    """Official NBL27 headshot URL per official player id: the NBL27 roster record, else his latest NBL27 game."""
    tip = {r["match_id"]: sv(r["match_time_utc"]) for r in off["res"] if r["season"] == CUR}
    out = {}
    for r in sorted((r for r in off["box"] if r["season"] == CUR), key=lambda r: (tip.get(r["match_id"], ""), sv(r["match_id"]))):
        u = sv(r.get("photo_t")) or sv(r.get("photo_s"))
        if u.startswith("https://"):
            out[sv(r["player_id"])] = u
    for x in off.get("roster") or []:
        p = x.get("player") or {}
        u = sv(p.get("external_player_image")) or sv(p.get("image"))
        if p.get("external_id") and u.startswith("https://"):
            out[p["external_id"]] = u
    return out


def bios(off, want, pids=None):
    """Official profile for each wanted player (want: {espn id: (first, family)}; pids: {espn id: official id}):
    the NBL27 roster record (matched by official id) first, gaps filled from his latest 2015-25 official profile
    (matched by name). -> {espn id: {...}}, {espn id: {(season, team): date of birth}} for homonym checks."""
    roster = {}
    for x in off.get("roster") or []:
        p = x.get("player") or {}
        if p.get("external_id"):
            roster[p["external_id"]] = (x, p)
    by = {}
    for r in off.get("bios") or []:
        by.setdefault(nkey(r["firstName"], r["familyName"]), []).append(r)
    out, dobs = {}, {}
    for pid, (first, fam) in want.items():
        d = {}
        rr = roster.get((pids or {}).get(pid))
        if rr:
            x, p = rr
            d["season"] = season_label(CUR)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", sv(p.get("date_of_birth"))[:10]):
                d["dob"] = sv(p.get("date_of_birth"))[:10]
            if 150 <= iv(p.get("height")) <= 240:
                d["heightCm"] = iv(p.get("height"))
            if 50 <= iv(p.get("weight")) <= 180:
                d["weightKg"] = iv(p.get("weight"))
            cc = sv(p.get("nationality_code")).strip().upper()
            if cc:
                d["nationality"], d["nationalityCode"] = CC3.get(cc, (cc, ""))
            j = sv(x.get("jersey_number")) or sv(p.get("latest_jersey_number"))
            if j:
                d["jersey"] = j
            pos = (sv(x.get("playing_position")) or sv(p.get("latest_playing_position"))).upper()
            if pos in POS:
                d["pos"], d["posName"] = POS[pos]
        rows = by.get(nkey(first, fam), [])
        if rows:
            dobs[pid] = {(sv(r["season"]), TEAMNAME.get(sv(r["teamName"]), sv(r["teamName"]))): sv(r["dob"])[:10] for r in rows}
            r = max(rows, key=lambda r: sv(r["season"]))
            d.setdefault("season", season_label(r["season"]))
            if "dob" not in d and re.fullmatch(r"\d{4}-\d{2}-\d{2}", sv(r["dob"])[:10]):
                d["dob"] = sv(r["dob"])[:10]
            if "heightCm" not in d and 150 <= iv(r["height"]) <= 240:
                d["heightCm"] = iv(r["height"])
            # (weight is taken only from the current roster: an old profile's weight can be years out of date)
            nat = sv(r["nationality"]).strip().upper()
            if "nationality" not in d and nat:
                d["nationality"], d["nationalityCode"] = COUNTRY.get(nat, (nat.title(), ""))
        if len(d) > 1:
            out[pid] = d
    return out, dobs


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    off = load()
    games = json.load(open(os.path.join(root, "data", "games.json")))
    players = json.load(open(os.path.join(root, "data", "players.json")))
    cmp = compare(games, players, off)
    print(json.dumps({"checked": len(cmp["checked"]), "missing": cmp["missing"], "paired": len(cmp["pairs"]),
                      "fix_players": cmp["fix_players"], "fix_teams": cmp["fix_teams"], "notes": cmp["notes"]}, indent=1))


if __name__ == "__main__":
    main()
