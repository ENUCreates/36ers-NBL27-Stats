"""Build updater/coaches.json - the Head coaches tab: each NBL27 head coach's NBL seasons as head coach since 2015-16,
the regular-season games he coached in each, his team's advanced stats in those games and where they rank among that
season's NBL teams.

    python3 build.py <repo_root> [page.html ...]

One-off, like overseas.json: built from coaches/curated.json (who coached which club in which season, with the date he
took over when it was mid-season, and the sources) and the official NBL box scores (the nblR release files that
official.py downloads; set NBL_OFFICIAL_CACHE to reuse a download). It is not refreshed by the daily rebuild: run it
again when a club changes coach. The page carries the result as a constant, between the /*COACHES*/ markers, so
pass the page file(s) to update them too (the Claude page source and the GitHub index.html).

The numbers use the page's own formulas (index.html, compute()): possessions = FGA - OREB + TOV + 0.44 FTA averaged
over both teams in each game, pace per 40 minutes, ratings per 100 possessions, the four factors for and against.
Official team totals include team rebounds and team turnovers, as the page's NBL27 data does. Ranks: 1 = best (pace:
1 = fastest) among that season's teams; a coach who took over mid-season is ranked on his games against the other
clubs' full seasons. Checks printed at the end: games per team and season, and each coach's games and W-L (finals
included) against his career record where his whole NBL career falls inside the official data.

Overseas seasons: a career row with 'stats' (the club's name in coaches/overseas_teams.json, which holds every team's
season totals for that league-season: overseas_espn.py writes G League regular seasons from ESPN box scores,
overseas_realgm.py the others from RealGM totals - playoffs included for the international leagues, the regular season
for the G League / D-League) also goes in the drop-down - his whole season as sole head coach, with the same formulas,
ranked among that league's teams; the data must agree with the curated record (the club's W-L, or else its number of
games: see overseas_seasons). Pace stays per 40 minutes in every league.

curated.json also carries each coach's whole head-coaching career ('career': every club job in every league, season by
season, with the pages each row was read from) and his national-team jobs ('national'). They are passed to the page as
they are, newest season first, with the source pages numbered once per coach ('csrc'); every NBL row from 2015-16 on
must agree with the official results for the games he coached (W-L and the number of teams), or nothing is written.
"""
import json, os, sys, re, tempfile, datetime
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import official  # noqa: E402  (the repo's own downloader for the official NBL files)

FIRST, LAST = "2015-16", "2025-26"          # seasons with official box scores, before NBL27
STATS = (("pts", "points"), ("fgm", "field_goals_made"), ("fga", "field_goals_attempted"), ("tpm", "three_pointers_made"),
         ("tpa", "three_pointers_attempted"), ("ftm", "free_throws_made"), ("fta", "free_throws_attempted"),
         ("oreb", "rebounds_offensive"), ("dreb", "rebounds_defensive"), ("ast", "assists"), ("stl", "steals"),
         ("blk", "blocks"), ("tov", "turnovers"), ("pf", "fouls_personal"))
# metric -> 1 higher is better, -1 lower is better, 0 neither (pace: rank 1 = fastest), as the page's COLS
BETTER = {"pace": 0, "ortg": 1, "drtg": -1, "net": 1, "efg": 1, "tov": -1, "orb": 1, "ftr": 1,
          "oefg": -1, "otov": 1, "drb": 1, "oftr": -1}


def num(v):
    try:
        x = float(v)
        return 0.0 if x != x else x
    except Exception:
        return 0.0


def ftx(s):
    """Free throws that end a possession: 0.44 a free throw, or the count kept as 'ftx' where a league shoots one free
    throw per trip (overseas_teams.json)."""
    return s["ftx"] if "ftx" in s else 0.44 * s["fta"]


def poss(s):
    return s["fga"] - s["oreb"] + s["tov"] + ftx(s)


def blank():
    # games / w / l: every game (official match list scores); gp: the games with box scores, which the numbers use
    return {"games": 0, "w": 0, "l": 0, "gp": 0, "poss": 0.0, "mins": 0, "own": defaultdict(float), "opp": defaultdict(float)}


def add(t, g, team):
    """One game for `team`: the result always, the box-score numbers when the official file has both lines."""
    opp = g["away"] if g["home"] == team else g["home"]
    t["games"] += 1
    if g["score"][team] > g["score"][opp]:
        t["w"] += 1
    else:
        t["l"] += 1
    if not g["box"]:
        return
    s, o = g["box"][team], g["box"][opp]
    t["gp"] += 1
    t["poss"] += (poss(s) + poss(o)) / 2
    t["mins"] += g["mins"]
    for k, _ in STATS:
        t["own"][k] += s[k]
        t["opp"][k] += o[k]


def metrics(t):
    s, o, P = t["own"], t["opp"], t["poss"]
    d = lambda a, b: a / b if b else None
    return {"pace": d(40 * P, t["mins"]), "ortg": d(100 * s["pts"], P), "drtg": d(100 * o["pts"], P), "net": d(100 * (s["pts"] - o["pts"]), P),
            "efg": d(s["fgm"] + 0.5 * s["tpm"], s["fga"]), "tov": d(s["tov"], s["fga"] + ftx(s) + s["tov"]),
            "orb": d(s["oreb"], s["oreb"] + o["dreb"]), "ftr": d(s["fta"], s["fga"]),
            "oefg": d(o["fgm"] + 0.5 * o["tpm"], o["fga"]), "otov": d(o["tov"], o["fga"] + ftx(o) + o["tov"]),
            "drb": d(s["dreb"], s["dreb"] + o["oreb"]), "oftr": d(o["fta"], o["fga"])}


def rnd(m):
    return {k: (None if v is None else round(v, 2 if k in ("pace", "ortg", "drtg", "net") else 4)) for k, v in m.items()}


def rank(k, v, others):
    """1 + the number of other teams strictly better (pace: faster)."""
    b = BETTER[k]
    if v is None:
        return None
    return 1 + sum(1 for x in others if x is not None and ((x < v) if b == -1 else (x > v)))


def isnum(v):
    try:
        x = float(v)
        return x == x
    except Exception:
        return False


def games_by_season(dest):
    """Regular-season games from the official match list (scores, date, overtime), each with both teams' box-score lines
    when the official file has them; finals (match list, plus 2025-26 finals the list lacks) for the record check."""
    import pyreadr
    files = official.download(dest)
    bt = pyreadr.read_r(files["team"])[None]
    rw = pyreadr.read_r(files["res"])[None]
    tn = lambda n: official.TEAMNAME.get(n, n)
    lines = defaultdict(dict)
    for r in bt.to_dict("records"):
        season = official.season_label(r["season"])
        if not (FIRST <= season <= LAST):
            continue
        s = {k: num(r[f]) for k, f in STATS}
        if not isnum(r["points"]):               # 2025-26 lines carry the score, not 'points'
            s["pts"] = num(r["score"])
        ok = isnum(r["field_goals_attempted"]) and s["fga"] > 0 and isnum(r["score"])
        lines[r["match_id"]].setdefault(tn(r["name"]), (season, s if ok else None))   # a repeated line is the same game
    out, finals, seen = defaultdict(list), defaultdict(list), set()
    for r in rw.to_dict("records"):
        season = official.season_label(r["season"])
        if not (FIRST <= season <= LAST) or r["match_type"] not in ("REGULAR", "FINALS"):
            continue
        seen.add(r["match_id"])
        h, a = tn(r["home_team_name"]), tn(r["away_team_name"])
        sc = {h: num(r["home_score_string"]), a: num(r["away_score_string"])}
        L = lines.get(r["match_id"]) or {}
        box = {n: (L.get(n) or (None, None))[1] for n in (h, a)}
        g = {"id": r["match_id"], "home": h, "away": a, "score": sc,
             "box": box if all(box.values()) and set(L) == {h, a} else None}
        if r["match_type"] == "FINALS":
            finals[season].append(g); continue
        tip = official.tip_syd(r["match_time_utc"])
        g.update(mins=40 + 5 * int(num(r["extra_periods_used"])), date=tip.date().isoformat() if tip else None)
        out[season].append(g)
    for mid, L in lines.items():                 # finals games the match list doesn't have (2025-26)
        if mid in seen or len(L) != 2:
            continue
        (season, sa), (_, sb) = L.values()
        (h, a) = L.keys()
        if sa and sb:
            finals[season].append({"id": mid, "home": h, "away": a, "score": {h: sa["pts"], a: sb["pts"]}, "box": None})
    return out, finals, []


CAREER_KEYS = {"season", "disp", "club", "cc", "lg", "w", "l", "team", "pos", "of", "grp", "res", "part", "note", "seq", "side", "src", "stats"}


def overseas_seasons(c, overseas, rep, bad):
    """His full seasons in other leagues that overseas_teams.json has every team's totals for (career rows with 'stats' =
    the club's name there): the same numbers as an NBL season - his team's ratings and four factors, the rank among that
    league-season's teams and the league average. Only a whole season as the sole head coach (w/l given, no 'part').
    His W-L is the regular season, and the data must fit it, or nothing is written: the club's W-L where the data has
    one; otherwise its games - all of the regular season and no more for regular-season totals, at least that for totals
    with the playoffs in ('allGames', where some club must have played exactly that many: one that missed the playoffs).
    Every other club too: a club that left the league during the season ('partial') is in the league average but not
    ranked, and the source's totals may lack a few games (other clubs short of the regular season, at most 1% of the
    league's games, never his club's) - named in the notes."""
    rows = []
    for r in c.get("career") or []:
        if not r.get("stats"):
            continue
        tag = f"{c['name']} {r['season']} {r['club']} ({r['lg']})"
        if r.get("part") or r.get("w") is None:
            bad.append(f"{tag}: 'stats' on a part season or a season without his own W-L"); continue
        L = [x for x in overseas if x["league"] == r["lg"] and x["season"] == r["season"]]
        if len(L) != 1 or r["stats"] not in L[0]["teams"]:
            bad.append(f"{tag}: no team '{r['stats']}' in overseas_teams.json for that league-season"); continue
        L, T = L[0], L[0]["teams"]
        t, rs = T[r["stats"]], r["w"] + r["l"]
        full = {n: x for n, x in T.items() if not x.get("partial")}
        short = {n: x["games"] for n, x in full.items() if x["games"] < rs}
        if "w" in t:
            if (t["w"], t["l"]) != (r["w"], r["l"]):
                bad.append(f"{tag}: curated {r['w']}-{r['l']}, the league data {t['w']}-{t['l']}"); continue
        elif L.get("allGames"):
            if t["games"] < rs or not any(x["games"] == rs for x in full.values()):
                bad.append(f"{tag}: curated {r['w']}-{r['l']}, but no club in the league data played exactly {rs} games, or the club "
                           f"played fewer ({t['games']})"); continue
        elif t["games"] != rs:
            bad.append(f"{tag}: curated {r['w']}-{r['l']}, the league data {t['games']} regular-season games for the club"); continue
        if r["stats"] in short or sum(rs - g for g in short.values()) > 0.01 * rs * len(full):
            bad.append(f"{tag}: the league data lacks games: " + ", ".join(f"{n} {g}" for n, g in sorted(short.items()))
                       + f" of a {rs}-game regular season"); continue
        met = {n: metrics({**x, "poss": (poss(x["own"]) + poss(x["opp"])) / 2}) for n, x in T.items()}
        allt = {"mins": sum(x["mins"] for x in T.values()), "poss": sum((poss(x["own"]) + poss(x["opp"])) / 2 for x in T.values()),
                "own": defaultdict(float), "opp": defaultdict(float)}
        for x in T.values():
            for side in ("own", "opp"):
                for k, v in x[side].items():
                    allt[side][k] += v
        m = met[r["stats"]]
        notes = list(L.get("notes") or [])
        for g in sorted(set(short.values())):
            names = sorted(n for n, x in short.items() if x == g)
            notes.append(f"{L['source']}: {g} games for {', '.join(names[:-1]) + ' and ' if len(names) > 1 else ''}{names[-1]} "
                         f"(a {rs}-game regular season)")
        for n in sorted(set(T) - set(full)):
            notes.append(f"{n} left the league after {T[n]['games']} games: its games stay in the totals and the league average, "
                         f"it isn't ranked")
        row = {"season": r["season"], "team": r["club"], "league": L["short"], "games": t["games"], "w": r["w"], "l": r["l"],
               "gp": t["gp"], "n": len(full), "m": rnd(m),
               "rk": {k: rank(k, m[k], [met[n][k] for n in full if n != r["stats"]]) for k in BETTER},
               "lg": rnd(metrics(allt)), "src": L["source"], "notes": notes}
        if L.get("allGames"):
            row["allGames"], row["rsGames"] = 1, rs
        rows.append(row)
        rep.append(f"{tag}: {t['games']} g" + (f" (all games; regular season {r['w']}-{r['l']})" if L.get("allGames") else f" {r['w']}-{r['l']}")
                   + (f" (box scores {t['gp']})" if t["gp"] != t["games"] else "")
                   + f", {len(full)} teams ranked, {L['source']} - ORtg {row['m']['ortg']} ({row['rk']['ortg']}), DRtg {row['m']['drtg']} ({row['rk']['drtg']})"
                   + "".join(f"; {x}" for x in notes[len(L.get('notes') or []):]))
    return rows


def career_rows(c, out, rep, bad):
    """The curated career (every head-coaching job, any league) for the page: each row's source pages become numbers
    into one list per coach ('csrc'), newest season first. NBL rows from 2015-16 on must match the official results
    for the games he coached (W-L, and the number of teams); a mismatch stops the build."""
    car, nat = c.get("career") or [], c.get("national") or []
    if not car and not nat:
        return
    urls = []

    def ix(lst):
        if not lst:
            bad.append(f"{c['name']}: a row without sources")
        for u in lst:
            if u not in urls:
                urls.append(u)
        return [urls.index(u) + 1 for u in lst]
    official_rows = {(r["season"], r["team"]): r for r in out["seasons"]}
    rows, checked = [], 0
    for r in car:
        if set(r) - CAREER_KEYS or not {"season", "club", "cc", "lg"} <= set(r):
            bad.append(f"{c['name']} {r.get('season')} {r.get('club')}: unexpected or missing keys")
        x = {k: v for k, v in r.items() if k != "src"}
        x["s"] = ix(r.get("src") or [])
        if r["lg"] == "NBL" and FIRST <= r["season"] <= LAST:
            o = official_rows.get((r["season"], r["club"]))
            if not o:
                bad.append(f"{c['name']} {r['season']} {r['club']}: no official coach-season (add the stint)")
            else:
                if (r.get("w"), r.get("l")) != (o["w"], o["l"]):
                    bad.append(f"{c['name']} {r['season']} {r['club']}: curated {r.get('w')}-{r.get('l')}, official {o['w']}-{o['l']}")
                if r.get("of") not in (None, o["n"]):
                    bad.append(f"{c['name']} {r['season']} {r['club']}: curated {r['of']} teams, official {o['n']}")
                x["of"], x["off"] = o["n"], 1
                checked += 1
        rows.append(x)
    seasons_of = {s["season"] for s in out["seasons"]}
    missing = sorted(seasons_of - {r["season"] for r in car if r["lg"] == "NBL"})
    if missing:
        bad.append(f"{c['name']}: official NBL coach-seasons missing from the career list: {', '.join(missing)}")
    out["career"] = sorted(rows, key=lambda r: (r["season"], r.get("seq", 0)), reverse=True)
    out["national"] = [{**{k: v for k, v in n.items() if k != "src"}, "s": ix(n.get("src") or [])} for n in nat]
    out["csrc"] = urls
    if c.get("careerChecked"):
        out["careerChecked"] = c["careerChecked"]
    rep.append(f"{c['name']}: career {len(rows)} rows ({checked} NBL seasons checked against the official results), "
               f"{len(out['national'])} national-team jobs, {len(urls)} sources")


def main():
    root = sys.argv[1]
    pages = sys.argv[2:]
    cur = json.load(open(os.path.join(HERE, "curated.json")))
    ovp = os.path.join(HERE, "overseas_teams.json")
    overseas = json.load(open(ovp, encoding="utf-8"))["leagues"] if os.path.exists(ovp) else []
    dest = os.environ.get("NBL_OFFICIAL_CACHE") or tempfile.mkdtemp()
    seasons, finals, _ = games_by_season(dest)
    rep = []
    # every team's full regular season
    full = {}
    for season, G in seasons.items():
        T = defaultdict(blank)
        for g in G:
            for n in (g["home"], g["away"]):
                add(T[n], g, n)
        full[season] = T
        games = sorted({t["games"] for t in T.values()})
        nobox = [g for g in G if not g["box"]]
        rep.append(f"{season}: {len(T)} teams, {len(G)} regular-season games, games per team {games}"
                   + (f"; no official box score for {len(nobox)}: " + ", ".join(f"{g['date']} {g['home']} v {g['away']}" for g in nobox) if nobox else ""))
    league = {}
    for season, T in full.items():
        allt = blank()
        for t in T.values():
            allt["gp"] += t["gp"]; allt["poss"] += t["poss"]; allt["mins"] += t["mins"]
            for k, _ in STATS:
                allt["own"][k] += t["own"][k]; allt["opp"][k] += t["opp"][k]
        league[season] = rnd(metrics(allt))
    coaches, bad = [], []
    for c in cur["coaches"]:
        out = {k: c[k] for k in ("id", "name", "team") if k in c}
        for k in ("record", "titles", "note"):
            if c.get(k) not in (None, [], ""):
                out[k] = c[k]
        rows, check_g, check_w = [], 0, 0
        for st in c.get("stints") or []:
            season, team, frm = st["season"], st["team"], st.get("from")
            T = full.get(season) or {}
            if team not in T:
                rep.append(f"{c['name']}: {season} {team} not in the official data - left out"); continue
            if frm:
                t = blank()
                for g in seasons[season]:
                    if team in (g["home"], g["away"]) and g["date"] and g["date"] >= frm:
                        add(t, g, team)
            else:
                t = T[team]
            m = metrics(t)
            others = {k: [metrics(x)[k] for n, x in T.items() if n != team] for k in BETTER}
            row = {"season": season, "team": team, "games": t["games"], "w": t["w"], "l": t["l"], "gp": t["gp"], "n": len(T),
                   "m": rnd(m), "rk": {k: rank(k, m[k], others[k]) for k in BETTER}, "lg": league[season]}
            if frm:
                row["from"] = frm
                row["teamGames"] = T[team]["games"]
            rows.append(row)
            check_g += t["games"]; check_w += t["w"]
            # finals he coached that season (for the career-record check only)
            for g in finals.get(season, []):
                if team in (g["home"], g["away"]):
                    opp = g["away"] if g["home"] == team else g["home"]
                    check_g += 1; check_w += 1 if g["score"][team] > g["score"][opp] else 0
        out["seasons"] = sorted(rows, key=lambda r: r["season"], reverse=True)
        out["sources"] = c.get("sources") or []
        career_rows(c, out, rep, bad)
        # then his full overseas seasons: one list, newest first (an NBL season first inside one season)
        out["seasons"] = sorted(rows + overseas_seasons(c, overseas, rep, bad), key=lambda r: (r["season"], "league" not in r), reverse=True)
        coaches.append(out)
        rec = c.get("record")
        line = f"{c['name']} ({c['team']}): " + (", ".join(f"{r['season']} {r['team']} {r['games']} g {r['w']}-{r['l']}"
                                                          + (f" (box scores {r['gp']})" if r["gp"] != r["games"] else "")
                                                          + (f" (from {r['from']}, of {r['teamGames']})" if r.get("from") else "") for r in out["seasons"] if "league" not in r)
                                                or "no NBL head-coaching season in the official data")
        if rec and rows:
            line += f" | official games since 2015-16 incl. finals: {check_g} g {check_w}-{check_g - check_w}; career record (Wikipedia): {rec['games']} g {rec['w']}-{rec['l']}"
            if rec["games"] == check_g and rec["w"] == check_w:
                line += " - MATCHES"
        rep.append(line)
    res = {"_about": ("Head coaches tab data, built once by updater/coaches/build.py from coaches/curated.json and the official NBL "
                      "box scores (nblR files): each NBL27 head coach's NBL head-coaching seasons since 2015-16 - regular-season "
                      "games he coached, his team's ratings and four factors in those games, their rank among that season's teams "
                      "(1 = best; pace: 1 = fastest) and the league average - and his whole head-coaching career in every league "
                      "('career', newest first, with the pages read in 'csrc'; NBL rows from 2015-16 checked against the official "
                      "results) and national-team jobs; 'seasons' also carries his full seasons in other leagues ('league'), "
                      "from every team's totals in coaches/overseas_teams.json. Built " + datetime.date.today().isoformat() + "."),
           "recordSource": cur.get("recordSource"), "coaches": coaches}
    print("\n".join(rep))
    if bad:
        sys.exit("career rows that disagree with the official NBL data or are malformed (nothing written):\n" + "\n".join(bad))
    fp = os.path.join(HERE, "..", "coaches.json")
    json.dump(res, open(fp, "w"), indent=1, ensure_ascii=False)
    print(f"{len(coaches)} coaches, {sum(1 for c in coaches for s in c['seasons'] if 'league' not in s)} NBL coach-seasons, "
          f"{sum(1 for c in coaches for s in c['seasons'] if 'league' in s)} overseas, "
          f"{sum(len(c.get('career') or []) for c in coaches)} career rows -> {os.path.normpath(fp)}")
    # the page's copy
    blob = "/*COACHES*/const COACHES=" + json.dumps({"recordSource": res["recordSource"], "coaches": coaches}, ensure_ascii=False, separators=(",", ":")) + ";/*COACHES*/"
    for pg in pages:
        s = open(pg, encoding="utf-8").read()
        new, n = re.subn(r"/\*COACHES\*/.*?/\*COACHES\*/", lambda _: blob, s, flags=re.S)
        if n != 1:
            sys.exit(f"{pg}: expected one /*COACHES*/ block, found {n}")
        open(pg, "w", encoding="utf-8").write(new)
        print(f"updated {pg}")


if __name__ == "__main__":
    main()
