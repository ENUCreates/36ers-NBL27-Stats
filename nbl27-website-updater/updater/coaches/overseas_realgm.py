"""Team season totals from RealGM for the overseas seasons the NBL27 head coaches coached, into coaches/overseas_teams.json
(read by build.py for the Head coaches drop-down).

    python3 overseas_realgm.py

RealGM can't be downloaded from a script, so its pages are copied from a browser (select all, copy) into text files in
coaches/realgm/ - one file per league-season, holding both 'Totals - Team Totals' and 'Totals - Opponent Totals' for
https://basketball.realgm.com/international/league/<id>/<league>/team-stats/<end year>/Totals/Team_Totals (and
.../Opponent_Totals), or for the G League (the NBA D-League until 2017)
https://basketball.realgm.com/gleague/team-stats/<end year>/Totals/Team_Totals/Regular_Season (and
.../Opponent_Totals/Regular_Season). Every file there is read again on each run, so the numbers always come straight
from the copies.

RealGM's season totals for the international leagues cover every league game that season, playoffs included (it has
no regular-season-only team totals for them): those entries are marked 'allGames'. Its G League pages give the regular
season alone. A club with under half the league's usual number of games left the league during the season: marked
'partial' (build.py keeps it in the league average - its games are in its opponents' totals too - but doesn't rank
it). Checks, per league-season: the same teams in both tables,
games equal for each team and its opponents, field-goal/three/free-throw percentages match their makes and attempts,
and - every game being between two of the league's teams - the league's totals equal its opponents' totals (a few
points or rebounds apart over a season at most). Each table copied from the page carries the page's own checksum,
and the copy must reproduce it, so the numbers are RealGM's exactly; RealGM's own box-score quirks (a team's points
and what its made shots add up to a little apart) are noted on the page beyond 0.5% of its points and stop the run
beyond 3%. Points are RealGM's
(the final scores); minutes are the game minutes, the mean of the team's and its opponents' figures.
"""
import json, os, re, sys, datetime, hashlib
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
    "PBA - Commissioners Cup": ("PBA Commissioner's Cup", "PBA", 131, "PBA--Commissioners-Cup"),
    # RealGM's G League section (no league id): regular-season totals; the NBA D-League before 2017-18
    "G League": ("NBA G League", "G League", None, None),
}
COLS = ["GP", "MIN", "PTS", "FGM", "FGA", "FG%", "3PM", "3PA", "3P%", "FTM", "FTA", "FT%", "ORB", "DRB", "REB", "AST", "STL", "BLK", "TOV", "PF"]
KEY = {"PTS": "pts", "FGM": "fgm", "FGA": "fga", "3PM": "tpm", "3PA": "tpa", "FTM": "ftm", "FTA": "fta", "ORB": "oreb", "DRB": "dreb",
       "AST": "ast", "STL": "stl", "BLK": "blk", "TOV": "tov", "PF": "pf"}
HEAD = re.compile(r"^(\d{4})-(\d{4}) (.+?) Totals - (Team|Opponent) Totals\s*$")
ROW = re.compile(r"^(\d+)\.\s+(.+?)\s+((?:\S+\s+){19}\S+)$")


def num(x):
    return float(x.replace(",", ""))


def parse(path, blocks):
    """A copy of RealGM tables -> blocks[(league, season, own|opp)] = {team: {column: value}}. A 'sha256 <hex>' line
    after a table is the checksum the page itself gave for that table (its heading, its rows with the cells joined by
    tabs, and its 'Showing' line): the copy must reproduce it exactly."""
    cur, head, rows, done = None, None, [], None
    for raw in open(path, encoding="utf-8", errors="replace"):
        line = raw.rstrip("\n").replace("\u00a0", " ")
        m = HEAD.match(line.strip())
        if m:
            cur = (m.group(3).strip(), f"{m.group(1)}-{m.group(2)[2:]}", "own" if m.group(4) == "Team" else "opp")
            if cur in blocks:
                sys.exit(f"{path}: {cur} appears twice")
            blocks[cur], head, rows, done = {}, line.strip(), [], None
            continue
        if cur and re.match(r"^\d+\.\s", line):
            parts = line.split("\t")
            if len(parts) != 2 + len(COLS):          # the page's text (space-separated): the team, then 20 numbers
                m = ROW.match(line.strip())
                if not m:
                    sys.exit(f"{path}: a row with {len(parts)} fields: {line!r}")
                parts = [m.group(1) + ".", m.group(2)] + m.group(3).split()
            parts = [p.strip() for p in parts]
            rows.append("\t".join(parts))
            team = parts[1]
            if team in blocks[cur]:
                sys.exit(f"{path}: {team} twice in {cur}")
            blocks[cur][team] = dict(zip(COLS, [num(v) for v in parts[2:]]))
        elif cur and line.startswith("Showing "):
            n = int(re.search(r"of (\d+) rows", line).group(1))
            if n != len(blocks[cur]):
                sys.exit(f"{path}: the page says {n} rows, {len(blocks[cur])} read for {cur}")
            text = f"{head}\n" + "\n".join(rows) + f"\nShowing 1 to {n} of {n} rows\n"
            done, cur = (head, hashlib.sha256(text.encode("utf-8")).hexdigest()), None
        elif done and line.startswith("sha256 "):
            if line.split()[1] != done[1]:
                sys.exit(f"{path}: '{done[0]}' doesn't match the page's checksum - the copy differs from RealGM's table")
            done = None


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
        teams, notes, off = {}, [], []
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
                # RealGM's own box-score quirks (the copies match the pages' checksums): shown on the page beyond
                # 0.5% of the points; beyond 3% the season isn't usable
                if abs(d) > max(6, 0.03 * v["PTS"]):
                    sys.exit(f"{lg} {season} {side}{t}: points and makes {d:+.0f} apart")
                if abs(d) > max(6, 0.005 * v["PTS"]):
                    off.append(f"{side}{t} {d:+.0f}")
            teams[t] = {"games": int(a["GP"]), "gp": int(a["GP"]), "mins": round((a["MIN"] + b["MIN"]) / 2, 2),
                        "own": {KEY[k]: a[k] for k in KEY}, "opp": {KEY[k]: b[k] for k in KEY}}
        diffs = {k: sum(x["own"][k] for x in teams.values()) - sum(x["opp"][k] for x in teams.values()) for k in KEY.values()}
        for k, v in diffs.items():
            tot = sum(x["own"][k] for x in teams.values())
            if abs(v) > max(6, 0.002 * tot):
                sys.exit(f"{lg} {season}: league {k} {tot:.0f}, its opponents {tot - v:.0f} - a table is incomplete or misread")
        name, short, rid, slug = LEAGUES[lg]
        end = "20" + season[-2:] if int(season[-2:]) < 50 else "19" + season[-2:]
        if rid is None:                             # the G League pages: regular season only
            if season < "2017-18":
                name, short = "NBA D-League", "D-League"
            url = f"https://basketball.realgm.com/gleague/team-stats/{end}/Totals/Team_Totals/Regular_Season"
        else:
            url = f"https://basketball.realgm.com/international/league/{rid}/{slug}/team-stats/{end}/Totals/Team_Totals"
        games = sorted(t["games"] for t in teams.values())
        left = sorted(n for n, t in teams.items() if t["games"] < games[len(games) // 2] / 2)
        for n in left:
            teams[n]["partial"] = True
        # minutes a game: 48 in the G League and the PBA, 40 elsewhere (overtime on top)
        per = sorted(t["mins"] / t["games"] for t in teams.values())[len(teams) // 2]
        glen = 48 if per > 46 else 40
        gps = sorted(set(games))
        gone = ", ".join(f"{n} ({teams[n]['games']} games)" for n in left)
        rep.append(f"{name} {season}: {len(teams)} teams, games per team {gps}; {glen}-minute games; league minus opponents: "
                   + (", ".join(f"{k} {v:+.0f}" for k, v in diffs.items() if v) or "none")
                   + (f"; left during the season: {gone}" if left else "")
                   + "".join(f"\n   {n}" for n in notes))
        out.append({"league": name, "short": short, "season": season,
                    "source": "RealGM season totals" if rid else "RealGM regular-season totals", "sourceUrl": url,
                    "built": datetime.date.today().isoformat(), **({"allGames": True} if rid else {}),
                    "notes": [f"{short} {season}: RealGM " + ("season totals" if rid else "regular-season totals"),
                              "pace per 40 minutes" + (f" ({short} games are 48)" if glen == 48 else "")]
                    + ([f"RealGM's own points and made shots disagree by more than 0.5% for {len(off)} of its totals (largest: {max(off, key=lambda x: abs(int(x.split()[-1])))} points over the season)"] if off else []),
                    "teams": teams})
    return out, rep


def main():
    blocks = {}
    for f in sorted(os.listdir(SRC)):
        if f.endswith(".txt"):
            parse(os.path.join(SRC, f), blocks)
    new, rep = entries(blocks)
    data = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {"leagues": []}
    keep = [x for x in data["leagues"] if not x.get("source", "").startswith("RealGM")]
    data["leagues"] = sorted(keep + new, key=lambda x: (x["league"], x["season"]))
    data["_about"] = ("Every team's season totals (own and opponents') for the overseas league-seasons the NBL27 head coaches "
                      "coached in full, for the Head coaches drop-down (build.py computes ratings, four factors, ranks and "
                      "league averages from them with the page's formulas). G League 2018-19 and 2025-26: overseas_espn.py, ESPN box "
                      "scores, regular season. Other league-seasons: overseas_realgm.py, RealGM totals copied into "
                      "coaches/realgm/ - every league game, playoffs included ('allGames'), for the international leagues; "
                      "the regular season for the G League / D-League. Each entry names its source.")
    json.dump(data, open(OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    open(OUT, "a", encoding="utf-8").write("\n")
    print("\n".join(rep))
    print(f"{len(new)} RealGM league-season(s) -> {os.path.normpath(OUT)}")


if __name__ == "__main__":
    main()
