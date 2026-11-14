#!/usr/bin/env python3
"""The fixed half of the Impact tab's data: every NBL regular-season game from 2015-16 to 2025-26, as impact.py
needs it, in updater/impact_hist.json.gz. One-off, like coaches/lineups_hist.py: the daily update (impact.py) adds
NBL27 to it every morning. Run it again only to rebuild the history (for example when nblR republishes a season).

    python3 impact_hist.py            (set NBL_OFFICIAL_CACHE to reuse a download of the nblR files)

Reads the official NBL files republished by nblR (official.py's box scores and match list, and the play-by-play file
pbp/pbp.rds, read with coaches/rdsread.py; NBL_PBP_PKL may name a pickle of that same table to skip the slow read).

1. Stints. For every game both teams' fives are followed through the play-by-play together, with the rules of
   coaches/lineups_hist.py (the official starters, then every substitution; all of a team's substitutions at one
   clock reading taken together when the clock next moves; team rebounds and turnovers the newer feed logs without a
   team go to the side the play says). A stint is a stretch of game clock with the same ten players on the floor; each
   side's points, field-goal attempts, offensive rebounds, turnovers and free-throw attempts in it (for possessions),
   and its threes and free throws made with the makes expected from the shooters who took them (each shooter's NBL
   3P% and FT% over every season in the files, pulled toward the league rate by 140 and 30 attempts, as ladder.py
   does). A game is used only when it reconciles with the official box score, as in lineups_hist.py: five on the floor
   for each team whenever the clock runs, the whole game covered (within 30 seconds), both teams' points exact,
   field-goal and free-throw attempts within 3, and every player's floor time within 2 minutes of his official
   minutes (plus whatever the official minutes fall short of five players' full game).
2. Box scores. Every player's line in every regular-season game with an official box score (used or not above), with
   his share of the game's possessions (the game's possessions, FGA - OREB + TOV + 0.44 FTA averaged over both teams,
   times his minutes over the game's length), and every game's date (for impactcheck.py's tests).

Players are keyed by name (letters only, lower case, accents dropped), the key the NBL27 data uses too.
"""
import gzip, json, os, pickle, sys, tempfile, datetime
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "coaches"))
import official            # noqa: E402
import lineups_hist as LH  # noqa: E402  (the play-by-play reading rules, shared)
import build as B          # noqa: E402  (games_by_season: the official regular-season games)
import rdsread             # noqa: E402

OUT = os.path.join(HERE, "impact_hist.json.gz")
SIDE = ["pts", "fga", "oreb", "tov", "fta", "tpm", "x3", "ftm", "xft"]
BOX = [("pts", "points"), ("fgm", "field_goals_made"), ("fga", "field_goals_attempted"), ("tpm", "three_pointers_made"),
       ("tpa", "three_pointers_attempted"), ("ftm", "free_throws_made"), ("fta", "free_throws_attempted"),
       ("oreb", "rebounds_offensive"), ("dreb", "rebounds_defensive"), ("ast", "assists"), ("stl", "steals"),
       ("blk", "blocks"), ("tov", "turnovers"), ("pf", "fouls_personal")]
K3, KFT = 140, 30                     # league-average pseudo-attempts in each shooter's estimate (as ladder.py)
tn = lambda n: official.TEAMNAME.get(n, n)
poss_of = lambda s: s["fga"] - s["oreb"] + s["tov"] + 0.44 * s["fta"]


def num(v):
    try:
        x = float(v)
        return None if x != x else x
    except Exception:
        return None


def walk(rows, home, away, starters, p3, pft, lg3, lgft):
    """Both teams' fives through one game -> ({(home five, away five): [seconds, home Counter, away Counter]},
    floor seconds per (team, player), problems)"""
    st, why = {}, []
    on = {home: set(starters[home]), away: set(starters[away])}
    outs = {home: [], away: []}
    ins = {home: [], away: []}
    floor = defaultdict(float)
    last, at_lab = 0.0, None
    other = lambda x: away if x == home else home
    lab = lambda r: f"{'OT' if str(r['period_type']).upper().startswith('OVER') else 'Q'}{int(r['period'])} {r['gt']}"

    def cur():
        k = (frozenset(on[home]), frozenset(on[away]))
        if k not in st:
            st[k] = [0.0, Counter(), Counter()]
        return st[k]

    def apply(T):
        both = set(outs[T]) & set(ins[T])
        for p in outs[T]:
            if p in both:
                continue
            if p not in on[T]:
                why.append(f"{T}: {p} went off while not on the floor ({at_lab})")
                return False
            on[T].discard(p)
        for p in ins[T]:
            if p not in both:
                on[T].add(p)
        outs[T].clear(); ins[T].clear()
        return True

    def run(t):
        nonlocal last
        if t <= last + 1e-9:
            return True
        for T in (home, away):
            if (outs[T] or ins[T]) and not apply(T):
                return False
            if len(on[T]) != 5:
                why.append(f"{T}: {len(on[T])} on the floor at {at_lab or 'the start'}")
                return False
        u = cur(); u[0] += t - last
        for T in (home, away):
            for p in on[T]:
                floor[(T, p)] += t - last
        last = t
        return True
    pend = None
    miss = possn = None
    for r in rows:
        if r["period"] != r["period"]:
            continue
        t = LH.abs_time(r["period_type"], r["period"], r["gt"])
        if t is not None:
            ot = str(r["period_type"]).upper().startswith("OVER")
            end = 2400 + int(r["period"]) * 300 if ot else int(r["period"]) * 600
            if pend is not None and end > pend and not run(pend):
                return None, None, why
            pend = max(pend or 0, end)
            if not run(t):
                return None, None, why
        at, side = str(r["action_type"]).lower(), LH.side_of(r)
        if at == "substitution":
            if side in on and r["first_name"]:
                (ins if str(r["sub_type"]).lower() == "in" else outs)[side].append(LH.nkey(r["first_name"], r["family_name"]))
                at_lab = lab(r)
            continue
        stp = str(r["sub_type"]).lower().replace(" ", "")
        if side is None and at in ("rebound", "turnover"):
            side = (miss if stp.startswith("off") else other(miss) if miss else None) if at == "rebound" else possn
        if at in ("2pt", "3pt", "freethrow") and side:
            if r["success"] == 1:
                possn, miss = other(side), None
            else:
                miss = side
        elif at == "rebound" and side and "deadball" not in stp:
            possn = side
        elif at in ("steal", "jumpball") and side and (at == "steal" or stp == "won"):
            possn = side
        elif at == "turnover" and side:
            possn = other(side)
        if side not in on or at not in ("2pt", "3pt", "freethrow", "rebound", "turnover"):
            continue
        if at == "rebound" and "deadball" in stp:
            continue                          # a dead-ball team rebound (after a missed first free throw): not in the box score
        u = cur()
        d = u[1] if side == home else u[2]
        made = r["success"] == 1
        k = LH.nkey(r["first_name"], r["family_name"]) if r["first_name"] else None
        if at in ("2pt", "3pt"):
            d["fga"] += 1
            if at == "3pt":
                d["x3"] += p3.get(k, lg3)
                if made:
                    d["tpm"] += 1
            if made:
                d["pts"] += 3 if at == "3pt" else 2
        elif at == "freethrow":
            d["fta"] += 1; d["xft"] += pft.get(k, lgft)
            if made:
                d["ftm"] += 1; d["pts"] += 1
        elif at == "rebound":
            if stp.startswith("off"):
                d["oreb"] += 1
        elif at == "turnover":
            d["tov"] += 1
    if pend is not None and not run(pend):
        return None, None, why
    for T in (home, away):
        if (outs[T] or ins[T]) and not apply(T):
            return None, None, why
    return st, floor, why


def main():
    import pyreadr
    dest = os.environ.get("NBL_OFFICIAL_CACHE") or tempfile.mkdtemp()
    seasons, _, _ = B.games_by_season(dest)
    files = official.download(dest)
    bx = pyreadr.read_r(files["box"])[None]
    # every shooter's NBL 3P% and FT% (all seasons in the file), pulled toward the league rate
    c = defaultdict(Counter)
    for r in bx[["first_name", "family_name", "three_pointers_made", "three_pointers_attempted", "free_throws_made",
                 "free_throws_attempted"]].itertuples(index=False):
        k = LH.nkey(r[0], r[1])
        for f, v in zip(("tpm", "tpa", "ftm", "fta"), r[2:]):
            c[k][f] += num(v) or 0
    lg3 = sum(x["tpm"] for x in c.values()) / sum(x["tpa"] for x in c.values())
    lgft = sum(x["ftm"] for x in c.values()) / sum(x["fta"] for x in c.values())
    p3 = {k: (x["tpm"] + K3 * lg3) / (x["tpa"] + K3) for k, x in c.items()}
    pft = {k: (x["ftm"] + KFT * lgft) / (x["fta"] + KFT) for k, x in c.items()}
    # the regular-season games, with each game's possessions and length
    gmeta = {}
    for season, G in seasons.items():
        for g in G:
            if g["box"]:
                gmeta[LH.sid(g["id"])] = (season, g, 0.5 * (poss_of(g["box"][g["home"]]) + poss_of(g["box"][g["away"]])))
    # box-score lines
    boxrows, starters_of, mins_of, names = [], defaultdict(lambda: defaultdict(dict)), {}, defaultdict(Counter)
    for r in bx.to_dict("records"):
        mid = LH.sid(r["match_id"])
        if mid not in gmeta:
            continue
        season, g, gp = gmeta[mid]
        team, k = tn(r["team_name"]), LH.nkey(r["first_name"], r["family_name"])
        mn = LH.mmss(r["minutes"])
        st = int(r["starter"]) if r["starter"] == r["starter"] and r["starter"] is not None else 0
        starters_of[mid][team][k] = (mn, st, f"{r['first_name']} {r['family_name']}".strip())
        if not mn or mn <= 0:
            continue
        names[k][f"{r['first_name']} {r['family_name']}".strip()] += 1
        vals = [num(r[f]) for _, f in BOX]
        boxrows.append([k, season, mid, team, st, round(mn, 4), round(gp * mn / g["mins"], 4), vals])
    # stints
    if os.environ.get("NBL_PBP_PKL"):
        pbp = pickle.load(open(os.environ["NBL_PBP_PKL"], "rb"))
    else:
        pbp = rdsread.read_df(LH.download_pbp(dest))
    cols = ["match_id", "team_name", "period_type", "period", "gt", "action_type", "sub_type", "success", "first_name",
            "family_name", "action_number"]
    pbp = pbp[cols]
    pbp = pbp[pbp["match_id"].map(LH.sid).isin(gmeta)]
    games, bad, used = [], Counter(), Counter()
    for mid, grp in pbp.groupby(pbp["match_id"].map(LH.sid), sort=False):
        season, g, _ = gmeta[mid]
        home, away = g["home"], g["away"]
        rows = grp.astype(object).where(grp.notna(), None).to_dict("records")
        for rec in rows:
            for k in ("period", "success", "action_number"):
                if rec[k] is None:
                    rec[k] = float("nan")
        Bx = starters_of.get(mid, {})
        if home not in Bx or away not in Bx:
            bad["no official box score"] += 1; continue
        starters = {T: [p for p, v in Bx[T].items() if v[1] == 1] for T in (home, away)}
        if any(len(v) != 5 for v in starters.values()):
            bad["not five starters"] += 1; continue
        stt, floor, why = walk(LH.ordered(rows), home, away, starters, p3, pft, lg3, lgft)
        if stt is None:
            bad["substitutions don't add up"] += 1; continue
        sec = sum(v[0] for v in stt.values())
        exp = 60 * g["mins"]
        H, A = Counter(), Counter()
        for v in stt.values():
            H.update(v[1]); A.update(v[2])
        o, oo = g["box"][home], g["box"][away]
        why = []
        if abs(sec - exp) > 30:
            why.append("game length")
        if H["pts"] != o["pts"] or A["pts"] != oo["pts"]:
            why.append("points")
        if abs(H["fga"] - o["fga"]) > 3 or abs(H["fta"] - o["fta"]) > 3 or abs(A["fga"] - oo["fga"]) > 3 or abs(A["fta"] - oo["fta"]) > 3:
            why.append("attempts")
        for T in (home, away):
            Bt = Bx[T]
            short = max(0.0, 5 * exp / 60 - sum(v[0] or 0 for v in Bt.values()))
            if any(Bt[p][0] is not None and abs(floor.get((T, p), 0) / 60 - Bt[p][0]) > 2 + short for p in Bt):
                why.append("minutes")
                break
        if why:
            bad["off the box score: " + ", ".join(why)] += 1; continue
        rnd = lambda C: [round(C.get(f, 0), 4) for f in SIDE]
        games.append([mid, season, g["date"], home, away,
                      [[sorted(k[0]), sorted(k[1]), round(v[0], 2), rnd(v[1]), rnd(v[2])] for k, v in stt.items()
                       if v[0] > 0 or v[1] or v[2]]])
        used[season] += 1
    keys = sorted({k for g in games for s in g[5] for k in s[0] + s[1]} | {r[0] for r in boxrows})
    ki = {k: i for i, k in enumerate(keys)}
    for g in games:
        for s in g[5]:
            s[0] = [ki[k] for k in s[0]]; s[1] = [ki[k] for k in s[1]]
    for r in boxrows:
        r[0] = ki[r[0]]
    tot = Counter(s for s, _, _ in gmeta.values())
    doc = {"built": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "source": "Official NBL box scores, match list and play-by-play as republished by nblR (github.com/JaseZiv/nblr_data)",
           "side": SIDE, "box": [f for f, _ in BOX], "keys": keys,
           "talent": {k: [round(p3[k], 5), round(pft[k], 5)] for k in keys if k in p3},
           "league": {"lg3": lg3, "lgft": lgft},
           "seasons": {s: {"games": tot[s], "used": used[s]} for s in sorted(tot)},
           "dates": {mid: g["date"] for mid, (_, g, _) in gmeta.items()},
           "names": {k: dict(names[k]) for k in keys},
           "games": games, "boxrows": boxrows}
    with gzip.open(OUT, "wt") as f:
        json.dump(doc, f, separators=(",", ":"))
    print(f"{len(games)} of {len(gmeta)} games reconcile ({dict(bad)}); {sum(len(g[5]) for g in games)} stints; "
          f"{len(boxrows)} box-score lines; {len(keys)} players -> {OUT} ({os.path.getsize(OUT) // 1024} KB)")


if __name__ == "__main__":
    main()
