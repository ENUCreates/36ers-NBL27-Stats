"""Team season totals from RealGM for the overseas seasons the NBL27 head coaches coached, into coaches/overseas_teams.json
(read by build.py for the Head coaches drop-down).

    python3 overseas_realgm.py

RealGM can't be downloaded from a script, so its pages are copied from a browser (select all, copy) into text files in
coaches/realgm/ - one file per league-season, holding both 'Totals - Team Totals' and 'Totals - Opponent Totals' for
https://basketball.realgm.com/international/league/<id>/<league>/team-stats/<end year>/Totals/Team_Totals (and
.../Opponent_Totals). Every file there is read again on each run, so the numbers always come straight from the copies.

RealGM's season totals cover every league game that season, playoffs included (it has no regular-season-only team
totals for these leagues); each entry is marked 'allGames'. Checks, per league-season: the same teams in both tables,
games equal for each team and its opponents, field-goal/three/free-throw percentages match their makes and attempts,
and - every game being between two of the league's teams - the league's totals equal its opponents' totals (RealGM's
own box-score quirks leave a few points or rebounds over a season; anything larger stops the run). Points are RealGM's
(the final scores); minutes are the game minutes, the mean of the team's and its opponents' figures.
"""
import json, os, re, sys, datetime
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "realgm")
OUT = os.path.join(HERE, "overseas_teams.json")
# RealGM league name -> (league as coaches/curated.json names it, short label, RealGM id and slug)
LEAGUES = {
    "German BBL": ("Basketball Bundesliga", "Bundesliga", 15, "German-BBL"),
    "French Jeep Elite": ("LNB Pro A", "Pro A", 12, "French-Jeep-Elite"),
    "French Betclic Elite": ("LNB Pro A", "Pro A", 12, "French-Jeep-Elite"),
    "Greek HEBA A1": ("Greek A1", "Greek A1", 8, "Greek-HEBA-A1"),
    "Chinese CBA": ("CBA (China)", "CBA", 40, "Chinese-CBA"),
    "Japanese BLeague": ("B.League B1", "B.League", 105, "Japanese-BLeague"),
    "Japanese B.League": ("B.League B1", "B.League", 105, "Japanese-BLeague"),
    "PBA: Commissioners Cup": ("PBA Commissioner's Cup", "PBA", 131, "PBA--Commissioners-Cup"),
}
COLS = ["GP", "MIN", "PTS", "FGM", "FGA", "FG%", "3PM", "3PA", "3P%", "FTM", "FTA", "FT%", "ORB", "DRB", "REB", "AST", "STL", "BLK", "TOV", "PF"]
KEY = {"PTS": "pts", "FGM": "fgm", "FGA": "fga", "3PM": "tpm", "3PA": "tpa", "FTM": "ftm", "FTA": "fta", "ORB": "oreb", "DRB": "dreb",
       "AST": "ast", "STL": "stl", "BLK": "blk", "TOV": "tov", "PF": "pf"}
HEAD = re.compile(r"^(\d{4})-(\d{4}) (.+?) Totals - (Team|Opponent) Totals\s*$")


def num(x):
    return float(x.replace(",", ""))


def parse(path, blocks):
    cur = None
    for raw in open(path, encoding="utf-8", errors="replace"):
        line = raw.rstrip("\n").replace(" ", " ")
        m = HEAD.match(line.strip())
        if m:
            cur = (m.group(3).strip(), f"{m.group(1)}-{m.group(2)[2:]}", "own" if m.group(4) == "Team" else "opp")
            if cur in blocks:
                sys.exit(f"{path}: {cur} appears twice")
            blocks[cur] = {}
            continue
        if cur and re.match(r"^\d+\.\t", line):
            parts = line.split("\t")
            if len(parts) != 2 + len(COLS):
                sys.exit(f"{path}: a row with {len(parts)} fields: {line!r}")
            team = parts[1].strip()
            if team in blocks[cur]:
                sys.exit(f"{path}: {team} twice in {cur}")
            blocks[cur][team] = dict(zip(COLS, [num(v) for v in parts[2:]]))
        elif cur and line.startswith("Showing "):
            n = int(re.search(r"of (\d+) rows", line).group(1))
            if n != len(blocks[cur]):
                sys.exit(f"{path}: the page says {n} rows, {len(blocks[cur])} read for {cur}")
            cur = None


def entries(blocks):
    out, rep = [], []
    for lg, season in sorted({(k[0], k[1]) for k in blocks}):
        if lg not in LEAGUES:
            sys.exit(f"{lg} {season}: add the league to LEAGUES")
        own, opp = blocks.get((lg, season, "own")), blocks.get((lg, season, "opp"))
        if not own or not opp:
            sys.exit(f"{lg} {season}: the team or the opponent totals are missing")
        if set(own) != set(opp):
            sys.exit(f"{lg} {season}: different teams in the two tables: {sorted(set(own) ^ set(opp))}")
        teams, notes = {}, []
        for t in sorted(own):
            a, b = own[t], opp[t]
            if a["GP"] != b["GP"]:
                sys.exit(f"{lg} {season} {t}: {a['GP']:.0f} games, its opponents {b['GP']:.0f}")
            for side, v in (("", a), ("opponents of ", b)):
                for pc, mk, at in (("FG%", "FGM", "FGA"), ("3P%", "3PM", "3PA"), ("FT%", "FTM", "FTA")):
                    if v[at] and abs(round(v[mk] / v[at], 3) - v[pc]) > 0.0015:
                        sys.exit(f"{lg} {season} {side}{t}: {pc} {v[pc]} but {v[mk]:.0f}/{v[at]:.0f}")
                d = v["PTS"] - (2 * v["FGM"] + v["3PM"] + v["FTM"])
                if d:
                    notes.append(f"{side}{t}: points {v['PTS']:.0f}, makes add up to {v['PTS'] - d:.0f}")
                if abs(d) > 6:
                    sys.exit(f"{lg} {season} {side}{t}: points and makes {d:+.0f} apart")
            teams[t] = {"games": int(a["GP"]), "gp": int(a["GP"]), "mins": round((a["MIN"] + b["MIN"]) / 2, 2),
                        "own": {KEY[k]: a[k] for k in KEY}, "opp": {KEY[k]: b[k] for k in KEY}}
        diffs = {k: sum(x["own"][k] for x in teams.values()) - sum(x["opp"][k] for x in teams.values()) for k in KEY.values()}
        for k, v in diffs.items():
            tot = sum(x["own"][k] for x in teams.values())
            if abs(v) > max(6, 0.002 * tot):
                sys.exit(f"{lg} {season}: league {k} {tot:.0f}, its opponents {tot - v:.0f} - a table is incomplete or misread")
        name, short, rid, slug = LEAGUES[lg]
        end = "20" + season[-2:] if int(season[-2:]) < 50 else "19" + season[-2:]
        url = f"https://basketball.realgm.com/international/league/{rid}/{slug}/team-stats/{end}/Totals/Team_Totals"
        gps = sorted({t["games"] for t in teams.values()})
        rep.append(f"{name} {season}: {len(teams)} teams, games per team {gps}; league minus opponents: "
                   + (", ".join(f"{k} {v:+.0f}" for k, v in diffs.items() if v) or "none")
                   + "".join(f"\n   {n}" for n in notes))
        out.append({"league": name, "short": short, "season": season, "source": "RealGM season totals", "sourceUrl": url,
                    "built": datetime.date.today().isoformat(), "allGames": True,
                    "notes": [f"{short} {season}: RealGM season totals", "pace per 40 minutes"],
                    "teams": teams})
    return out, rep


def main():
    blocks = {}
    for f in sorted(os.listdir(SRC)):
        if f.endswith(".txt"):
            parse(os.path.join(SRC, f), blocks)
    new, rep = entries(blocks)
    data = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {"leagues": []}
    keep = [x for x in data["leagues"] if x.get("source") != "RealGM season totals"]
    data["leagues"] = sorted(keep + new, key=lambda x: (x["league"], x["season"]))
    data["_about"] = ("Every team's season totals (own and opponents') for the overseas league-seasons the NBL27 head coaches "
                      "coached in full, for the Head coaches drop-down (build.py computes ratings, four factors, ranks and "
                      "league averages from them with the page's formulas). G League: overseas_espn.py, ESPN box scores, "
                      "regular season. Other leagues: overseas_realgm.py, RealGM season totals copied into coaches/realgm/, "
                      "every league game (playoffs included, 'allGames'). Each entry names its source.")
    json.dump(data, open(OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    open(OUT, "a", encoding="utf-8").write("\n")
    print("\n".join(rep))
    print(f"{len(new)} RealGM league-season(s) -> {os.path.normpath(OUT)}")


if __name__ == "__main__":
    main()
