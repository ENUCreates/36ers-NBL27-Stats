"""Build updater/overseas.json - the one-off, curated career data that the official NBL and ESPN feeds don't have:
overseas leagues, G League, NBL seasons before 2015-16, and US college / NBA seasons ESPN couldn't link.

It is built once from page extractions saved in overseas/raw/ (October 2026) and is never refreshed by the daily
rebuild: bios.py only merges overseas.json into data/bios.json. Run it again only after adding or fixing raw files:

    python3 build.py <repo_root>        -> writes updater/overseas.json and prints a check report

Raw files (overseas/raw/<ESPN athlete id>.<kind>):
  bbr.jsonl / bbr2.jsonl  Basketball-Reference international page: regular-season Totals (All view), one JSON object per
                          row; bbr2 includes the career row. Two reads of the same page are compared row by row.
  rgm.jsonl               RealGM player Summary page: NBA / G League / NCAA / International season lines (per game).
  rgm2.txt                a second, independent read of the same RealGM page (pipe-separated, with career rows).
  wiki.jsonl              Wikipedia career history (clubs only), used through curated.json.
curated.json: hand-mapped journey stops (club, league, country, season) with the source they were read from.

What is used, and the checks it must pass:
  * Basketball-Reference rows (stats): leagues other than the NBL, and NBL seasons before the official data (2015-16).
    Every row must add up (FG = 2P + 3P, FGA = 2PA + 3PA, TRB = ORB + DRB, PTS = 2*FG + 3P + FT; a row the source itself
    gets wrong is reported, not changed) and all rows must sum to the page's career row. NBL rows from 2015-16 on are only
    compared with the official numbers (reported).
  * RealGM G League rows, and NBA / NCAA rows for seasons ESPN doesn't have (stats, per game): both reads must agree and
    the per-season lines must add up to the career line (games exactly, averages within rounding).
  * RealGM International lines: RealGM folds every competition of a season into one line (preseason and playoffs too).
    A line that is a single league Basketball-Reference doesn't cover is kept as a stats row marked allGames; every
    other club becomes a journey stop without stats.
  * Identity: the date of birth on each page must match ours (a day's difference allowed and reported).
"""
import json, os, re, sys, glob, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
OFFICIAL_FROM = "2015-16"          # first season of the official NBL box scores (historyNBL)

# ---------- leagues ----------
# Basketball-Reference league codes -> (label, country or None when it depends on the club)
BBR_LEAGUE = {"NBL": ("NBL", "AU"), "Liga ACB": ("ACB", "ES"), "ACB": ("ACB", "ES"), "LNB Pro A": ("LNB", "FR"),
              "LBA": ("LBA", "IT"), "GBL": ("GBL", "GR"), "CBA": ("CBA", "CN"), "ABA": ("ABA", None),
              "EuroLeague": ("EuroLeague", None), "EuroCup": ("EuroCup", None), "BSL": ("BSL", "TR"),
              "IPL": ("IBSL", "IL"), "Israeli BSL": ("IBSL", "IL"), "VTB": ("VTB", None)}
BBR_TOURNAMENT = {"FIBA", "WC", "OLY", "Olympics", "World Cup"}
# seasons Basketball-Reference covers, by label (from its international index)
BBR_FROM = {"ACB": "1983-84", "LBA": "1998-99", "LNB": "2002-03", "GBL": "2001-02", "EuroLeague": "2000-01",
            "EuroCup": "2002-03", "CBA": "2011-12", "NBL": "2011-12", "ABA": "2020-21", "BSL": "2020-21",
            "IBSL": "2020-21", "VTB": "2020-21"}
# RealGM league names (League cell) -> label, country (None = by club), calendar-year league?
RGM_LEAGUE = {"AUS NBL": ("NBL", "AU", False), "NBL Blitz": (None, None, False), "NZ NBL": ("NZNBL", "NZ", True),
              "KLS": ("KLS", "RS", False), "HEBA A1": ("GBL", "GR", False), "I-BSL": ("IBSL", "IL", False),
              "CBA": ("CBA", "CN", False), "Lega A": ("LBA", "IT", False), "BAL": ("BAL", None, True),
              "Liga ABA": ("ABA", None, False), "PBA CC": ("PBA", "PH", False), "LEB DivA": ("Lebanon D1", "LB", False),
              "BSN": ("BSN", "PR", True), "G Pro A": ("ProA", "DE", False), "G-BBL": ("BBL", "DE", False),
              "Euroleague": ("EuroLeague", None, False), "Eurocup": ("EuroCup", None, False)}
# RealGM league URL segments (team links) -> RealGM league name above
RGM_SLUG = {"Australian-NBL": "AUS NBL", "NBL-Blitz": "NBL Blitz", "New-Zealand-NBL": "NZ NBL", "Chinese-CBA": "CBA",
            "Puerto-Rican-BSN": "BSN", "Lebanese-Division-A": "LEB DivA", "PBA--Commissioners-Cup": "PBA CC",
            "German-Pro-A": "G Pro A", "German-BBL": "G-BBL"}
# club -> country, for leagues that span countries
CLUB_CC = {"Mornar": "ME", "KK Mornar": "ME", "Mornar Bar": "ME", "Bayern": "DE", "Bayern Munich": "DE",
           "Anadolu Efes": "TR", "Al Ahly": "EG", "Lokomotiv Kuban": "RU"}
# RealGM club names (International table) -> (league, display name) when the first read has no team links
RGM_CLUB = {"Wellington Saints": "NZ NBL", "Super City Rangers": "NZ NBL", "Franklin Bulls": "NZ NBL",
            "Canterbury Rams": "NZ NBL", "Aukland Tuatara": "NZ NBL", "Auckland Tuatara": "NZ NBL",
            "Manawatu Jets": "NZ NBL", "KK Mega Bemax": "KLS", "ASA Koroivos Amaliadas": "HEBA A1",
            "Ironi Nahariya": "I-BSL", "Zhejiang Guangsha": "CBA", "Virtus Roma": "Lega A", "Al Ahly": "BAL",
            "KK Mornar": "Liga ABA", "Zhejiang Chouzhou": "CBA", "Magnolia Hotshots": "PBA CC",
            "Jilin Northeast": "CBA", "Sagesse Al Hekmeh Beirut": "LEB DivA", "Ningbo Rockets": "CBA",
            "Santeros de Aguada": "BSN", "Capitanes de Arecibo": "BSN", "Guangdong Dongguan": "CBA",
            "Taranaki Mountain Airs": "NZ NBL", "Hawke's Bay Hawks": "NZ NBL", "Art Giants Dusseldorf": "G Pro A"}
NBL_CLUBS = {"New Zealand", "Adelaide", "Perth", "Brisbane", "Cairns", "Illawarra", "Melbourne", "Sydney", "Tasmania",
             "South East Melbourne", "Wollongong", "Townsville", "Gold Coast"}
RGM_NAME = {"Aukland Tuatara": "Auckland Tuatara", "Art Giants Dusseldorf": "ART Giants Düsseldorf",
            "Sagesse Al Hekmeh Beirut": "Sagesse (Al Hekmeh)", "KK Mega Bemax": "Mega Bemax",
            "ASA Koroivos Amaliadas": "Koroivos Amaliadas", "KK Mornar": "Mornar Bar"}

# Basketball-Reference team link slug -> club name shown on the cards
BBR_CLUB = {"xinjiang": "Xinjiang Flying Tigers", "anadolu-efes": "Anadolu Efes", "brescia": "Germani Brescia",
            "miraflores": "San Pablo Burgos", "jiangsu-dragons": "Jiangsu Dragons", "bilbao": "Bilbao Basket",
            "gravelines": "BCM Gravelines-Dunkerque", "basquet-girona": "Bàsquet Girona", "bayern-muenchen": "Bayern Munich",
            "koroivos": "Koroivos Amaliadas", "roma": "Virtus Roma", "zhejiang-hangzhou": "Zhejiang Guangsha Lions",
            "mornar": "Mornar Bar", "zhejiang-yiwu": "Zhejiang Golden Bulls", "jilin": "Jilin Northeast Tigers",
            "ningbo": "Ningbo Rockets", "guangdong": "Guangdong Southern Tigers"}
# NBL clubs before 2015-16 (Basketball-Reference slug -> official name)
BBR_NBL = {"nbl-new-zealand": "New Zealand Breakers", "perth": "Perth Wildcats", "adelaide": "Adelaide 36ers",
           "cairns": "Cairns Taipans", "sydney": "Sydney Kings", "townsville": "Townsville Crocodiles",
           "wollongong": "Wollongong Hawks", "illawarra": "Illawarra Hawks", "gold-coast": "Gold Coast Blaze"}
BBR_NBL_MELB = lambda season: "Melbourne Tigers" if season < "2014-15" else "Melbourne United"

NBA_ABBR = {"UTH": "Utah Jazz", "UTA": "Utah Jazz", "MEM": "Memphis Grizzlies", "PHX": "Phoenix Suns",
            "NOP": "New Orleans Pelicans", "ATL": "Atlanta Hawks", "DAL": "Dallas Mavericks", "NYK": "New York Knicks",
            "WAS": "Washington Wizards"}
G_ABBR = {"AUS": "Austin Spurs", "ERI": "Erie BayHawks", "IWA": "Iowa Wolves", "BIR": "Birmingham Squadron",
          "SLC": "Salt Lake City Stars", "SBL": "South Bay Lakers", "GOG": "Capital City Go-Go"}
JUCO = {"South Georgia Technical College"}
G_FIX = {"Capital-City-Go-Go": "Capital City Go-Go"}


# ---------- helpers ----------
def season_lbl(s):
    s = re.sub(r"[^0-9-]", "", str(s))
    return s


def bbr_season(s, league):
    """Basketball-Reference labels the NBL's 2020-21 season '2021'."""
    s = season_lbl(s)
    if re.fullmatch(r"\d{4}", s) and league == "NBL":
        y = int(s); return f"{y - 1}-{str(y)[2:]}"
    return s


def cal_year(s):
    """RealGM files a calendar-year league season Y (e.g. NZNBL 2024) under '(Y-1)-Y'."""
    m = re.fullmatch(r"(\d{4})-(\d{2})", s)
    return str(int(m.group(1)) + 1) if m else s


def num(x):
    if x in (None, "", "-", "—"):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    x = str(x).strip().rstrip("%")
    try:
        v = float(x)
    except ValueError:
        return None
    return v


def pct(x):
    """'.420' / 0.42 / '42.0%' -> 0.42"""
    if x in (None, "", "-"):
        return None
    if isinstance(x, str) and x.strip().endswith("%"):
        return round(float(x.strip()[:-1]) / 100, 4)
    v = num(x)
    return None if v is None else round(v, 4)


def parse_date(s):
    if not s:
        return None
    s = re.split(r"\s+in\s+", str(s))[0].strip()
    for f in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.datetime.strptime(s, f).date()
        except ValueError:
            pass
    return None


def jsonl(fp):
    out = []
    for ln in open(fp, encoding="utf-8"):
        ln = ln.strip()
        if ln:
            out.append(json.loads(ln))
    return out


def load_bbr(fp):
    meta, head, rows, career, trunc = {}, {}, [], None, None
    for d in jsonl(fp):
        if "_meta" in d:
            meta = d["_meta"]
        elif "truncated" in d and len(d) <= 2:
            trunc = d["truncated"]
        elif d.get("career") is True or re.search(r"seasons?$", str(d.get("season", "")), re.I):
            career = d
        elif "season" in d:
            rows.append(d)
        else:
            head.update(d)
    return {"meta": meta, "head": head, "rows": rows, "career": career, "truncated": trunc}


def load_rgm(fp):
    meta, head, rows = {}, {}, []
    for d in jsonl(fp):
        if "_meta" in d:
            meta = d["_meta"]
        elif "tbl" in d:
            rows.append(d)
        elif "truncated" in d:
            pass
        else:
            head.update(d)
    return {"meta": meta, "head": head, "rows": [r for r in rows if not r.get("cut")]}


TBL = {"NBA Regular Season Stats": "NBA", "G League Regular Season Stats": "G", "NCAA Season Stats": "NCAA",
       "International Season Stats": "INT"}
COLS = ["season", "team", "league", "gp", "min", "pts", "reb", "ast", "stl", "blk", "tov", "fgp", "tpp", "ftp"]


def load_rgm2(fp):
    """Pipe-separated second read -> {tbl: {"rows": [...], "career": [...]}}; a table followed by CUT is incomplete."""
    meta, tbls, cur = {}, {}, None
    for ln in open(fp, encoding="utf-8"):
        ln = ln.rstrip("\n")
        if ln.startswith("#"):
            k, _, v = ln[1:].partition(" "); meta[k] = v; continue
        if not ln.strip():
            continue
        if ln.strip() in TBL:
            cur = TBL[ln.strip()]; tbls[cur] = {"rows": [], "career": [], "cut": False}; continue
        if ln.strip() == "CUT":
            if cur:
                tbls[cur]["cut"] = True
            continue
        cells = [c.strip() for c in ln.split("|")]
        if cur is None or len(cells) != len(COLS):
            continue
        d = dict(zip(COLS, cells))
        (tbls[cur]["career"] if d["season"].upper().startswith("CAREER") else tbls[cur]["rows"]).append(d)
    return meta, tbls


# ---------- main ----------
def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    bios = {b["id"]: b for b in json.load(open(os.path.join(root, "data", "bios.json")))}
    curated = {k: v for k, v in json.load(open(os.path.join(HERE, "curated.json"))).items() if not k.startswith("_")}
    pids = sorted({os.path.basename(f).split(".")[0] for f in glob.glob(os.path.join(RAW, "*"))} | set(curated))
    out, report = {}, []
    for pid in pids:
        b = bios.get(pid)
        if not b:
            report.append(f"{pid}: not an NBL27 player in data/bios.json - skipped"); continue
        name, dob = b.get("name"), b.get("dob")
        rep = lambda m: report.append(f"{name}: {m}")
        f = lambda kind: os.path.join(RAW, f"{pid}.{kind}")
        rows, stints, college, sources = [], [], None, {}
        ok_ident = True
        # ----- identity -----
        for kind in ("bbr2.jsonl", "bbr.jsonl", "rgm.jsonl", "wiki.jsonl"):
            if not os.path.exists(f(kind)):
                continue
            head = {}
            for d in jsonl(f(kind)):
                if "born" in d:
                    head = d; break
            bd = parse_date(head.get("born"))
            if bd and dob:
                gap = abs((bd - datetime.date.fromisoformat(dob)).days)
                if gap > 1:
                    rep(f"{kind}: born {bd} but ours is {dob} - this page is not used"); ok_ident = False
                elif gap == 1:
                    rep(f"{kind}: born {bd}, ours {dob} (a day apart) - same player")
        if not ok_ident:
            continue
        espn = {(h.get("league"), h.get("season")) for h in b.get("history") or []}
        official = {h.get("season") for h in b.get("historyNBL") or []}

        # ----- Basketball-Reference -----
        reads = [load_bbr(f(k)) for k in ("bbr2.jsonl", "bbr.jsonl") if os.path.exists(f(k))]
        bbr_keys = set()
        if reads:
            main_ = reads[0]
            url = main_["meta"].get("url"); sources["bbr"] = url
            R = main_["rows"]
            car = main_["career"]
            usable = [r for r in R if r.get("league") not in BBR_TOURNAMENT and not (r.get("league") == "NBL" and bbr_season(r["season"], "NBL") >= OFFICIAL_FROM)]
            fields = ["g", "mp", "fg", "fga", "tp", "tpa", "twop", "twopa", "ft", "fta", "orb", "drb", "trb", "ast", "stl", "blk", "tov", "pf", "pts"]
            good = True
            if car:
                bad = [k for k in fields if car.get(k) is not None and sum(int(r.get(k) or 0) for r in R) != int(car[k])]
                if bad:
                    rep(f"Basketball-Reference rows don't sum to the career line ({', '.join(bad)}) - not used"); good = False
            elif usable:
                rep("Basketball-Reference read has no career line - rows checked only against the second read")
            if len(reads) > 1:
                other = {(r["season"], r["team"], r["league"]): r for r in reads[1]["rows"]}
                for r in R:
                    o = other.get((r["season"], r["team"], r["league"]))
                    if o is None:
                        if main_["career"] and reads[1]["meta"].get("view") == "league" and r["league"] in BBR_TOURNAMENT:
                            continue
                        rep(f"BBR {r['season']} {r['team']} {r['league']}: only in one read" + (" (career line confirms it)" if car and good else ""))
                        continue
                    diff = [k for k in fields if o.get(k) is not None and r.get(k) != o.get(k)]
                    if diff:
                        rep(f"BBR {r['season']} {r['team']}: the two reads differ on {diff} - not used"); good = False
            elif not car:
                good = False
                if usable:
                    rep("Basketball-Reference: one read and no career line - not used")
            if good:
                for r in R:
                    lg = r.get("league")
                    if lg in BBR_TOURNAMENT:
                        continue
                    if lg not in BBR_LEAGUE:
                        rep(f"BBR league {lg!r} not mapped - row skipped"); continue
                    label, cc = BBR_LEAGUE[lg]
                    season = bbr_season(r["season"], lg)
                    slug = (re.search(r"/teams/([^/]+)/", r.get("team_href") or "") or [None, None])[1]
                    arith = []
                    if r["fg"] != r["tp"] + r["twop"]: arith.append("FG")
                    if r["fga"] != r["tpa"] + r["twopa"]: arith.append("FGA")
                    if r["trb"] != r["orb"] + r["drb"]: arith.append("TRB")
                    if r["pts"] != 2 * r["fg"] + r["tp"] + r["ft"]: arith.append(f"PTS ({r['pts']} vs {2 * r['fg'] + r['tp'] + r['ft']})")
                    if label == "NBL":
                        team = BBR_NBL.get(slug) or (BBR_NBL_MELB(season) if slug == "melbourne" else None)
                        if season >= OFFICIAL_FROM:
                            continue            # official NBL data covers it (compared separately)
                        if not team:
                            rep(f"BBR NBL club {slug!r} not mapped - row skipped"); continue
                        cc = "NZ" if team == "New Zealand Breakers" else "AU"
                    else:
                        team = BBR_CLUB.get(slug) or r["team"]
                        if slug not in BBR_CLUB:
                            rep(f"BBR club slug {slug!r} not in BBR_CLUB - showing {r['team']!r}")
                        cc = cc or CLUB_CC.get(team) or CLUB_CC.get(r["team"])
                        if not cc:
                            rep(f"BBR {team}: no country - row skipped"); continue
                    if arith:
                        rep(f"BBR {season} {team}: the source's own totals don't add up ({', '.join(arith)}) - shown as published")
                    g = r["g"]
                    if not g:
                        continue
                    p = lambda a, b_: round(a / b_, 4) if b_ else None
                    rows.append({"season": season, "team": team, "league": label, "country": cc, "gp": g, "gs": None,
                                 "mpg": round(r["mp"] / g, 1), "pts": round(r["pts"] / g, 1), "reb": round(r["trb"] / g, 1),
                                 "oreb": round(r["orb"] / g, 1), "dreb": round(r["drb"] / g, 1), "ast": round(r["ast"] / g, 1),
                                 "stl": round(r["stl"] / g, 1), "blk": round(r["blk"] / g, 1), "tov": round(r["tov"] / g, 1),
                                 "fgm": round(r["fg"] / g, 1), "fga": round(r["fga"] / g, 1), "fgp": p(r["fg"], r["fga"]),
                                 "tpm": round(r["tp"] / g, 1), "tpa": round(r["tpa"] / g, 1), "tpp": p(r["tp"], r["tpa"]),
                                 "ftm": round(r["ft"] / g, 1), "fta": round(r["fta"] / g, 1), "ftp": p(r["ft"], r["fta"]),
                                 "src": "BBR", "url": url,
                                 "tot": {"sec": r["mp"] * 60, "pts": r["pts"], "fgm": r["fg"], "fga": r["fga"], "tpm": r["tp"],
                                         "tpa": r["tpa"], "ftm": r["ft"], "fta": r["fta"], "oreb": r["orb"], "dreb": r["drb"],
                                         "ast": r["ast"], "stl": r["stl"], "blk": r["blk"], "tov": r["tov"]}})
                    bbr_keys.add((season, label))
            # official comparison (report only)
            offrows = {}
            for h in b.get("historyNBL") or []:
                offrows.setdefault(h["season"], []).append(h)
            for r in R:
                if r.get("league") != "NBL":
                    continue
                s = bbr_season(r["season"], "NBL")
                if s < OFFICIAL_FROM or s not in offrows:
                    continue
                t = {k: sum((h.get("tot") or {}).get(k, 0) for h in offrows[s]) for k in ("pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "ast")}
                gp = sum(h["gp"] for h in offrows[s])
                d = {k: (r[bk], t[k]) for k, bk in (("pts", "pts"), ("fgm", "fg"), ("fga", "fga"), ("tpm", "tp"), ("tpa", "tpa"), ("ftm", "ft"), ("fta", "fta"), ("ast", "ast")) if r[bk] != t[k]}
                if r["g"] != gp:
                    d["gp"] = (r["g"], gp)
                if d:
                    rep(f"note: BBR NBL {s} vs official (BBR, official): {d}")

        # ----- RealGM -----
        A = load_rgm(f("rgm.jsonl")) if os.path.exists(f("rgm.jsonl")) else None
        B_meta, B = load_rgm2(f("rgm2.txt")) if os.path.exists(f("rgm2.txt")) else ({}, {})
        if A:
            sources["realgm"] = A["meta"].get("url")
            if A["head"].get("school"):
                college = A["head"]["school"]
            # per-game tables: NBA (only seasons ESPN lacks), G League, NCAA (only seasons ESPN lacks)
            for tbl, league in (("NBA", "NBA"), ("G", "G League"), ("NCAA", "NCAA")):
                ra = [r for r in A["rows"] if r["tbl"] == tbl and num(r.get("gp"))
                      and not (league in ("NBA", "NCAA") and (league, season_lbl(r["season"])) in espn)]
                if not ra:
                    continue
                tb = B.get(tbl)
                if not tb:
                    rep(f"RealGM {tbl}: no second read - not used"); continue
                rb = {season_lbl(r["season"]): r for r in tb["rows"]}
                okt = True
                for r in ra:
                    o = rb.get(season_lbl(r["season"]))
                    if not o:
                        rep(f"RealGM {tbl} {r['season']}: missing from the second read - table not used"); okt = False; continue
                    for k in ("gp", "min", "pts", "reb", "ast", "stl", "blk", "tov"):
                        if num(r.get(k)) != num(o.get(k)):
                            rep(f"RealGM {tbl} {r['season']}: reads differ on {k} ({r.get(k)} vs {o.get(k)}) - table not used"); okt = False
                    for k in ("fgp", "tpp", "ftp"):
                        if pct(r.get(k)) != pct(o.get(k)):
                            rep(f"RealGM {tbl} {r['season']}: reads differ on {k} - table not used"); okt = False
                # career lines: games exact, averages within rounding (per level for college)
                lines = [r for r in tb["rows"] if num(r["gp"])]
                if tb["career"] and len(tb["career"]) > 1:
                    rep(f"RealGM {tbl}: career lines by level {[c['team'] for c in tb['career']]} (sum {sum(num(c['gp']) or 0 for c in tb['career']):g} games vs seasons {sum(num(r['gp']) for r in lines):g})")
                if tb["career"]:
                    tot_gp = sum(num(r["gp"]) for r in lines)
                    car_gp = sum(num(c["gp"]) or 0 for c in tb["career"])
                    if len(tb["career"]) == 1 and car_gp != tot_gp:
                        rep(f"RealGM {tbl}: seasons add up to {tot_gp:g} games, career line says {car_gp:g} - table not used"); okt = False
                    if len(tb["career"]) == 1 and car_gp == tot_gp:
                        c = tb["career"][0]
                        for k in ("min", "pts", "reb", "ast"):
                            w = sum(num(r["gp"]) * num(r[k]) for r in lines) / tot_gp
                            if abs(w - num(c[k])) > 0.1:
                                rep(f"RealGM {tbl}: {k} seasons average {w:.2f}, career line {c[k]} - table not used"); okt = False
                elif not tb["cut"]:
                    rep(f"RealGM {tbl}: no career line in the second read")
                if not okt:
                    continue
                for r in ra:
                    s = season_lbl(r["season"])
                    teams = r.get("teams") or []
                    names = []
                    lvl = league
                    for t in teams:
                        if isinstance(t, dict):
                            nm, href = t.get("name"), t.get("href") or ""
                        else:
                            nm, href = t, ""
                        if tbl == "NBA":
                            m = re.search(r"/nba/teams/([^/]+)/", href)
                            nm = m.group(1).replace("-", " ") if m and m.group(1) != "N-A" else ", ".join(NBA_ABBR.get(x.strip(), x.strip()) for x in nm.split(","))
                        elif tbl == "G":
                            m = re.search(r"/gleague/teams/([^/]+)/", href)
                            nm = G_FIX.get(m.group(1), m.group(1).replace("-", " ")) if m else ", ".join(G_ABBR.get(x.strip(), x.strip()) for x in nm.split(","))
                        else:
                            if "/ncaa-dii/" in href:
                                lvl = "NCAA D2"
                            elif nm in JUCO:
                                lvl = "NJCAA"
                        names.append(nm)
                    if any(n in (None, "") for n in names) or not names:
                        rep(f"RealGM {tbl} {s}: no team name - row skipped"); continue
                    team = " / ".join(n.replace(", ", " / ") for n in names)
                    gp = int(num(r["gp"]))
                    rows.append({"season": s, "team": team, "league": lvl, "country": "US", "gp": gp, "gs": None,
                                 "mpg": num(r.get("min")), "pts": num(r.get("pts")), "reb": num(r.get("reb")), "oreb": None,
                                 "dreb": None, "ast": num(r.get("ast")), "stl": num(r.get("stl")), "blk": num(r.get("blk")),
                                 "tov": num(r.get("tov")), "fgm": None, "fga": None, "fgp": pct(r.get("fgp")), "tpm": None,
                                 "tpa": None, "tpp": pct(r.get("tpp")), "ftm": None, "fta": None, "ftp": pct(r.get("ftp")),
                                 "src": "RealGM", "url": A["meta"].get("url"), **({"school": team} if lvl.startswith("N") and tbl == "NCAA" else {})})
            # International lines
            ints = [r for r in A["rows"] if r["tbl"] == "INT"]
            tb = B.get("INT") or {"rows": []}
            bints = {season_lbl(r["season"]): r for r in tb["rows"]}
            for r in ints:
                s = season_lbl(r["season"])
                leagues = [x.strip() for x in (r.get("leagues") or [])]
                unknown = [x for x in leagues if x not in RGM_LEAGUE]
                if unknown:
                    rep(f"RealGM INT {s}: league(s) {unknown} not mapped - line skipped"); continue
                real = [x for x in leagues if RGM_LEAGUE[x][0] not in (None, "NBL")]
                if not real:
                    continue
                # club -> RealGM league name
                clubs = []
                for i, t in enumerate(r.get("teams") or []):
                    if isinstance(t, dict):
                        nm, href = t.get("name"), t.get("href") or ""
                        m = re.search(r"/international/league/\d+/([^/]+)/team/", href)
                        lg = RGM_SLUG.get(m.group(1)) if m else None
                        if m and not lg:
                            rep(f"RealGM league slug {m.group(1)!r} not mapped")
                    else:
                        nm = t
                        tl = (r.get("team_leagues") or [None] * 99)
                        lg = RGM_SLUG.get(tl[i]) if i < len(tl) and tl[i] else None
                    if nm in NBL_CLUBS:
                        continue
                    if lg in (None, "NBL Blitz", "AUS NBL"):
                        lg = RGM_CLUB.get(nm)
                    if not lg:
                        rep(f"RealGM INT {s}: club {nm!r} has no league - left out"); continue
                    clubs.append((nm, lg))
                single = len(leagues) == 1 and len(real) == 1 and len(clubs) == 1
                for nm, lg in clubs:
                    label, cc, cal = RGM_LEAGUE[lg]
                    disp = RGM_NAME.get(nm, nm)
                    cc = cc or CLUB_CC.get(nm) or CLUB_CC.get(disp)
                    if not cc:
                        rep(f"RealGM {disp}: no country - left out"); continue
                    ss = cal_year(s) if cal else s
                    covered = label in BBR_FROM and ss >= BBR_FROM[label]
                    if (ss, label) in bbr_keys:
                        continue                                  # Basketball-Reference has the stats
                    if single and not covered:
                        o = bints.get(s)
                        same = o and all(num(o.get(k)) == num(r.get(k)) for k in ("gp", "min", "pts", "reb", "ast", "stl", "blk", "tov"))
                        if same:
                            rows.append({"season": ss, "team": disp, "league": label, "country": cc, "gp": int(num(r["gp"])), "gs": None,
                                         "mpg": num(r.get("min")), "pts": num(r.get("pts")), "reb": num(r.get("reb")), "oreb": None, "dreb": None,
                                         "ast": num(r.get("ast")), "stl": num(r.get("stl")), "blk": num(r.get("blk")), "tov": num(r.get("tov")),
                                         "fgm": None, "fga": None, "fgp": pct(r.get("fgp")), "tpm": None, "tpa": None, "tpp": pct(r.get("tpp")),
                                         "ftm": None, "fta": None, "ftp": pct(r.get("ftp")), "src": "RealGM", "allGames": True,
                                         "url": A["meta"].get("url")})
                            continue
                        rep(f"RealGM INT {s} {disp}: " + ("no second read" if not o else "the two reads differ") + " - journey only")
                    elif single and covered:
                        rep(f"RealGM INT {s} {disp} ({label}): Basketball-Reference covers this league but has no row - journey only")
                    stints.append({"season": ss, "team": disp, "league": label, "country": cc, "src": "RealGM", "url": A["meta"].get("url")})
        # ----- curated -----
        for key, nm in ((curated.get(pid) or {}).get("rename") or {}).items():
            ks, kl = key.split("|")
            hit = [r for r in rows if r["season"] == ks and r["league"] == kl]
            if len(hit) != 1:
                rep(f"curated rename {key}: {len(hit)} rows match - not applied"); continue
            hit[0]["team"] = nm
        for st in (curated.get(pid) or {}).get("stints") or []:
            if (st["season"], st["league"]) in bbr_keys or any(r["season"] == st["season"] and r["league"] == st["league"] for r in rows):
                rep(f"curated stint {st['season']} {st['team']} already has a stats row - skipped"); continue
            stints.append(dict(st))
        # de-duplicate stints (same season, club, league) and drop those a stats row already covers
        seen, st2 = set(), []
        for st in stints:
            k = (st["season"], st["team"], st["league"])
            if k in seen or any(r["season"] == st["season"] and r["league"] == st["league"] and r["team"] == st["team"] for r in rows):
                continue
            seen.add(k); st2.append(st)
        fetched = []
        for fp_ in glob.glob(os.path.join(RAW, pid + ".*")):
            for ln in open(fp_, encoding="utf-8"):
                m = re.search(r'"fetched":\s*"([0-9-]+)"|^#fetched\s+([0-9-]+)', ln)
                if m:
                    fetched.append(m.group(1) or m.group(2))
        entry = {"checked": max(fetched)} if fetched else {}
        if rows:
            entry["history"] = sorted(rows, key=lambda r: r["season"])
        if st2:
            entry["career"] = sorted(st2, key=lambda r: r["season"])
        if college and not b.get("college"):
            entry["college"] = college
            entry["src"] = {"college": "RealGM"}
        if sources:
            entry["sources"] = sources
        if entry:
            out[pid] = entry
        if not rows and not st2:
            rep("checked - nothing outside the NBL / ESPN data")
    about = ("One-off curated career data (overseas leagues, G League, NBL before 2015-16, college/NBA seasons ESPN "
             "couldn't link), built by overseas/build.py from page reads saved in overseas/raw/ on 2026-10-22. "
             "Sources: Basketball-Reference (season totals, every row checked), RealGM (season lines read twice), "
             "Wikipedia (clubs only). Not refreshed by the daily rebuild.")
    res = {"_about": about, **{k: out[k] for k in sorted(out)}}
    fp = os.path.join(os.path.dirname(HERE), "overseas.json")
    json.dump(res, open(fp, "w"), indent=1, ensure_ascii=False, sort_keys=False)
    print(f"{len(out)} players -> {fp}")
    print(f"{sum(len(v.get('history', [])) for v in out.values())} season rows, {sum(len(v.get('career', [])) for v in out.values())} journey-only stops")
    print("\n".join(report))


if __name__ == "__main__":
    main()
