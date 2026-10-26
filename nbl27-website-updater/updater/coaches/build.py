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


def poss(s):
    return s["fga"] - s["oreb"] + s["tov"] + 0.44 * s["fta"]


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
            "efg": d(s["fgm"] + 0.5 * s["tpm"], s["fga"]), "tov": d(s["tov"], s["fga"] + 0.44 * s["fta"] + s["tov"]),
            "orb": d(s["oreb"], s["oreb"] + o["dreb"]), "ftr": d(s["fta"], s["fga"]),
            "oefg": d(o["fgm"] + 0.5 * o["tpm"], o["fga"]), "otov": d(o["tov"], o["fga"] + 0.44 * o["fta"] + o["tov"]),
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


def main():
    root = sys.argv[1]
    pages = sys.argv[2:]
    cur = json.load(open(os.path.join(HERE, "curated.json")))
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
    coaches = []
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
        coaches.append(out)
        rec = c.get("record")
        line = f"{c['name']} ({c['team']}): " + (", ".join(f"{r['season']} {r['team']} {r['games']} g {r['w']}-{r['l']}"
                                                          + (f" (box scores {r['gp']})" if r["gp"] != r["games"] else "")
                                                          + (f" (from {r['from']}, of {r['teamGames']})" if r.get("from") else "") for r in out["seasons"])
                                                or "no NBL head-coaching season in the official data")
        if rec and rows:
            line += f" | official games since 2015-16 incl. finals: {check_g} g {check_w}-{check_g - check_w}; career record (Wikipedia): {rec['games']} g {rec['w']}-{rec['l']}"
            if rec["games"] == check_g and rec["w"] == check_w:
                line += " - MATCHES"
        rep.append(line)
    res = {"_about": ("Head coaches tab data, built once by updater/coaches/build.py from coaches/curated.json and the official NBL "
                      "box scores (nblR files): each NBL27 head coach's NBL head-coaching seasons since 2015-16 - regular-season "
                      "games he coached, his team's ratings and four factors in those games, their rank among that season's teams "
                      "(1 = best; pace: 1 = fastest) and the league average. Built " + datetime.date.today().isoformat() + "."),
           "recordSource": cur.get("recordSource"), "coaches": coaches}
    fp = os.path.join(HERE, "..", "coaches.json")
    json.dump(res, open(fp, "w"), indent=1, ensure_ascii=False)
    print(f"{len(coaches)} coaches, {sum(len(c['seasons']) for c in coaches)} coach-seasons -> {os.path.normpath(fp)}")
    print("\n".join(rep))
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
