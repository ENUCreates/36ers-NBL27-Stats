"""Team season totals for the G League seasons the NBL27 head coaches coached, from ESPN's box scores, into
coaches/overseas_teams.json (read by build.py for the Head coaches drop-down).

    python3 overseas_espn.py [season ...]        (default: every season in SEASONS)

One-off, like build.py: run it again only to add a season. Every regular-season game of the season (ESPN season type 2,
so the G League's Tip-Off Tournament and the playoffs are left out) is read from ESPN's scoreboard, day by day, then
each game's box score (the 'summary'). Set OVERSEAS_CACHE to a folder to keep the downloads.

Per game and team: field goals, threes, free throws, offensive and defensive rebounds (players), turnovers (players +
team), assists, steals, blocks, fouls; points = the final score. A game whose box score doesn't add up to the final score
(or, under the single-free-throw rule, whose play-by-play free throws don't match the box score) keeps its result but is
left out of the numbers for both teams, as build.py does with missing official NBL box scores.

Minutes: 48 per game plus overtime - a fixed length per overtime period, or ('clock') the overtime elapsed on the
play-by-play clock, for the untimed overtime of recent seasons. Single free throws: when every free-throw trip is one shot
worth 2 or 3 ('1 for 2', '1 for 3'), each such shot ends a possession, so it counts 1 (not 0.44) in the possession
estimate; the count is read from the play-by-play and kept as 'ftx' (= singles + 0.44 x the other free throws).
"""
import json, os, re, sys, time, datetime, tempfile, concurrent.futures as cf
from collections import defaultdict
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "overseas_teams.json")
BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba-development"
K = ("pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "oreb", "dreb", "tov", "ast", "stl", "blk", "pf")
# ESPN shows each franchise under its current name; 'names' gives the name it had that season
SEASONS = {
    "2018-19": {"from": "2018-10-25", "to": "2019-04-05", "ot": 2, "single": False,
                "names": {"Cleveland Charge": "Canton Charge", "Osceola Magic": "Lakeland Magic", "San Diego Clippers": "Agua Caliente Clippers",
                          "Noblesville Boom": "Fort Wayne Mad Ants", "Coachella Valley Lakers": "South Bay Lakers",
                          "Motor City Cruise": "Northern Arizona Suns", "Laketown Squadron": "Erie BayHawks",
                          "Grand Rapids Gold": "Grand Rapids Drive", "Maine Celtics": "Maine Red Claws"},
                "notes": ["G League 2018-19: ESPN box scores of every regular-season game (50 a team)",
                          "pace per 40 minutes (G League games are 48; overtime periods 2 minutes)"]},
    "2025-26": {"from": "2025-11-01", "to": "2026-04-10", "ot": "clock", "single": True,
                "names": {"Laketown Squadron": "Birmingham Squadron", "Coachella Valley Lakers": "South Bay Lakers"},
                "notes": ["G League 2025-26: ESPN box scores of every regular-season game (36 a team; the Tip-Off Tournament is left out)",
                          "pace per 40 minutes (G League games are 48; overtime is untimed, first to 7 points, its length read from the game clock)",
                          "one free throw per trip, worth 2 or 3: each counts as a possession (other free throws 0.44, as usual)"]},
}
S = requests.Session()


def get(url, fp):
    if os.path.exists(fp):
        return json.load(open(fp))
    for i in range(4):
        try:
            r = S.get(url, timeout=30)
            if r.status_code == 200:
                d = r.json()
                json.dump(d, open(fp, "w"))
                return d
        except Exception:
            pass
        time.sleep(1 + 2 * i)
    sys.exit(f"could not download {url}")


def clk(v):  # "1:36:15" (h:mm:ss) or "4:12" (m:ss) -> seconds
    p = [float(x) for x in v.split(":")]
    return sum(x * 60 ** (len(p) - 1 - i) for i, x in enumerate(p))


def line(t):
    s = {x["name"]: x["displayValue"] for x in t["statistics"]}
    mk = lambda k: [int(v) for v in s[k].split("-")]
    fgm, fga = mk("fieldGoalsMade-fieldGoalsAttempted")
    tpm, tpa = mk("threePointFieldGoalsMade-threePointFieldGoalsAttempted")
    ftm, fta = mk("freeThrowsMade-freeThrowsAttempted")
    return {"fgm": fgm, "fga": fga, "tpm": tpm, "tpa": tpa, "ftm": ftm, "fta": fta, "oreb": int(s["offensiveRebounds"]),
            "dreb": int(s["defensiveRebounds"]), "tov": int(s["totalTurnovers"]), "ast": int(s["assists"]),
            "stl": int(s["steals"]), "blk": int(s["blocks"]), "pf": int(s["fouls"])}


def season(label, cfg, cache):
    day, end, days = datetime.date.fromisoformat(cfg["from"]), datetime.date.fromisoformat(cfg["to"]), []
    while day <= end:
        days.append(day.strftime("%Y%m%d")); day += datetime.timedelta(days=1)
    events = {}
    with cf.ThreadPoolExecutor(8) as ex:
        for dd, d in ex.map(lambda dd: (dd, get(f"{BASE}/scoreboard?dates={dd}&limit=200", os.path.join(cache, f"sb_{dd}.json"))), days):
            for e in d.get("events", []):
                if e.get("season", {}).get("type") == 2:     # regular season
                    events[e["id"]] = {"date": dd, "name": e["name"], "status": e["competitions"][0]["status"]["type"]["name"]}
    with cf.ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda i: get(f"{BASE}/summary?event={i}", os.path.join(cache, f"sum_{i}.json")), sorted(events)))
    T = defaultdict(lambda: {"games": 0, "w": 0, "l": 0, "gp": 0, "mins": 0.0, "own": defaultdict(float), "opp": defaultdict(float)})
    rep, games, left = [], 0, 0
    for eid, e in sorted(events.items()):
        if e["status"] != "STATUS_FINAL":
            rep.append(f"not played or not final ({e['status']}): {e['date']} {e['name']} [{eid}]"); continue
        d = json.load(open(os.path.join(cache, f"sum_{eid}.json")))
        cs = {c["team"]["id"]: c for c in d["header"]["competitions"][0]["competitors"]}
        bx = {t["team"]["id"]: t for t in d["boxscore"]["teams"]}
        if set(cs) != set(bx) or len(cs) != 2:
            sys.exit(f"{eid}: the box score's teams don't match the result")
        nper = max(len(c.get("linescores", [])) for c in cs.values())
        if nper < 4:
            sys.exit(f"{eid}: {nper} periods")
        ids = list(cs)
        L = {i: line(bx[i]) for i in ids}
        box, why = True, []
        ftp = {i: L[i]["ftm"] for i in ids}
        if cfg["single"]:
            pl = d.get("plays") or []
            if not pl:
                box = False; why.append("no play-by-play")
            else:
                P = {i: {"fta": 0, "pts": 0, "single": 0} for i in ids}
                for p in pl:
                    t = p.get("text", "")
                    if "free throw" not in t.lower():
                        continue
                    tid = (p.get("team") or {}).get("id")
                    if tid not in P:
                        sys.exit(f"{eid}: a free throw without a team: {t}")
                    P[tid]["fta"] += 1
                    P[tid]["pts"] += p.get("scoreValue") or 0
                    P[tid]["single"] += 1 if re.search(r"free throw 1 for [23]\b", t) else 0
                for i in ids:
                    if P[i]["fta"] != L[i]["fta"]:
                        box = False; why.append(f"{cs[i]['team']['displayName']}: {P[i]['fta']} free throws in the play-by-play, {L[i]['fta']} in the box score")
                    ftp[i] = P[i]["pts"]
                    L[i]["ftx"] = P[i]["single"] + 0.44 * (L[i]["fta"] - P[i]["single"])
        for i in ids:
            L[i]["pts"] = int(cs[i]["score"])
            bp = 2 * L[i]["fgm"] + L[i]["tpm"] + ftp[i]
            if box and bp != L[i]["pts"]:
                box = False; why.append(f"{cs[i]['team']['displayName']}: the box score adds up to {bp}, final score {L[i]['pts']}")
        mins = 48.0
        if box and nper > 4:
            if cfg["ot"] == "clock":
                for k in range(5, nper + 1):
                    pl = [p for p in d.get("plays", []) if p.get("period", {}).get("number") == k]
                    if not pl:
                        sys.exit(f"{eid}: no plays in overtime {k - 4}")
                    endp = [p for p in pl if re.match(r"End of", p.get("text", ""))] or pl[-1:]
                    mins += (clk(pl[0]["clock"]["displayValue"]) - clk(endp[-1]["clock"]["displayValue"])) / 60
            else:
                mins += cfg["ot"] * (nper - 4)
        games += 1
        if not box:
            left += 1
            rep.append(f"box score left out ({'; '.join(why)}): {e['date']} {e['name']} [{eid}]")
        for i in ids:
            j = ids[1] if i == ids[0] else ids[0]
            t = T[cfg["names"].get(cs[i]["team"]["displayName"], cs[i]["team"]["displayName"])]
            t["games"] += 1
            t["w" if L[i]["pts"] > L[j]["pts"] else "l"] += 1
            if box:
                t["gp"] += 1; t["mins"] += mins
                for k in K + (("ftx",) if cfg["single"] else ()):
                    t["own"][k] += L[i][k]; t["opp"][k] += L[j][k]
    teams = {n: {"games": t["games"], "w": t["w"], "l": t["l"], "gp": t["gp"], "mins": round(t["mins"], 2),
                 "own": {k: round(v, 2) for k, v in t["own"].items()}, "opp": {k: round(v, 2) for k, v in t["opp"].items()}}
             for n, t in sorted(T.items())}
    per = sorted({t["games"] for t in teams.values()})
    rep.insert(0, f"G League {label}: {games} regular-season games, {len(teams)} teams, games per team {per}, "
                  f"{left} box score(s) left out")
    entry = {"league": "NBA G League", "short": "G League", "season": label, "source": "ESPN box scores",
             "sourceUrl": "https://www.espn.com/nba-g-league/", "built": datetime.date.today().isoformat(),
             "games": games, "boxLeftOut": left, "notes": cfg["notes"], "teams": teams}
    return entry, rep


def main():
    want = sys.argv[1:] or list(SEASONS)
    cache = os.environ.get("OVERSEAS_CACHE") or tempfile.mkdtemp()
    data = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {"leagues": []}
    data["_about"] = ("Every team's season totals (own and opponents') for the overseas league-seasons the NBL27 head coaches "
                      "coached in full, for the Head coaches drop-down (build.py computes ratings, four factors, ranks and "
                      "league averages from them with the page's formulas). G League: overseas_espn.py, ESPN box scores, "
                      "regular season. Other leagues: overseas_realgm.py, RealGM season totals copied into coaches/realgm/, "
                      "every league game (playoffs included, 'allGames'). Each entry names its source.")
    for label in want:
        entry, rep = season(label, SEASONS[label], cache)
        data["leagues"] = [x for x in data["leagues"] if not (x["league"] == entry["league"] and x["season"] == label)] + [entry]
        print("\n".join(rep))
    data["leagues"].sort(key=lambda x: (x["league"], x["season"]))
    json.dump(data, open(OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    open(OUT, "a", encoding="utf-8").write("\n")
    print(f"-> {os.path.normpath(OUT)}")


if __name__ == "__main__":
    main()
