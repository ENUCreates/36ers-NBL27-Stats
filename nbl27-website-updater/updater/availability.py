#!/usr/bin/env python3
"""Who's available, and what it's worth: the injury adjustment in the projected ladder and the game tips.

A team's strength (ladder.py) is its average over the games it has played, with whoever played them. For a game to
come, each player who has played for the team this season moves that strength by

    x = (chance he plays - share of the team's games he has played) * his minutes a game / 40
    adjustment = x * value,    value (points a game per 40 minutes) = B_GS * w * (Game Score per 40 - league average)
                                                                      + B_REPL,   w = minutes / (minutes + K)

so only the CHANGE from what the strength already contains counts: a regular who is newly out costs the most; a player
who has missed most games already (and is still out) costs little, because the strength already reflects playing
without him; a player back from a long absence adds to it.

B_GS, B_REPL and K were fitted on 1,458 NBL regular-season games, 2015-16 to 2025-26 (the official box scores
republished by nblR, which show exactly who played each game), using each team's other games only. Tested season by
season on seasons left out of the fit, the adjustment made the predicted chances more accurate (Brier score 0.2247 ->
0.2225) and the expected margins closer (mean squared error 1.7% lower). Game Score (Hollinger): PTS + 0.4 FGM
- 0.7 FGA - 0.4 (FTA - FTM) + 0.7 OREB + 0.3 DREB + STL + 0.7 AST + 0.7 BLK - 0.4 PF - TOV.

Chance he plays, from the injury list (data/injuries.json) for each game to come:
  out for the season / retired          0
  out, back "Round N"                   0 before round N, then 1
  out, back by a date ("Early December", "Mid-December", "8 November")   0 before it, then 1 ("possibly": 1/2 that week)
  out, "TBC" or anything else           0 for his team's next 2 games, 1/2 for the next 2, then 1
  day-to-day                            1/2 for his team's next game, then 1
  not listed, but missed his team's last 3+ games    0 for its next 3 games, then as below (reason unknown)
  otherwise                             the share of his team's last 5 games he played
"""
import collections, datetime, re

B_GS, B_REPL, K = 0.616, 1.654, 200.0
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def game_score(x):
    return (x["pts"] + 0.4 * x["fgm"] - 0.7 * x["fga"] - 0.4 * (x["fta"] - x["ftm"]) + 0.7 * x["oreb"] + 0.3 * x["dreb"]
            + x["stl"] + 0.7 * x["ast"] + 0.7 * x["blk"] - 0.4 * (x.get("pf") or 0) - x["tov"])


def values(games, players):
    """Per team: its games (dates) and, per player who has played for it, games, minutes a game, value per 40,
    and how many of the team's games in a row he has missed up to now."""
    tg = collections.defaultdict(list)
    for g in games:
        for s in ("home", "away"):
            tg[g[s]["team"]].append(g["date"])
    P = collections.defaultdict(dict)
    gs_all = min_all = 0.0
    for d in players:
        for t, L in d["teams"].items():
            for x in L:
                m = x.get("min") or 0
                if m <= 0:
                    continue
                p = P[t].setdefault(x["id"], {"id": x["id"], "name": x["name"], "gp": 0, "min": 0.0, "gs": 0.0, "dates": []})
                gs = game_score(x)
                p["gp"] += 1; p["min"] += m; p["gs"] += gs; p["dates"].append(d["date"])
                gs_all += gs; min_all += m
    lg40 = 40 * gs_all / min_all if min_all else 0.0
    out = {}
    for t, dates in tg.items():
        dates = sorted(dates)
        G = len(dates)
        for p in P.get(t, {}).values():
            last = max(p["dates"])
            p["G"], p["share"] = G, p["gp"] / G if G else 0.0
            p["mpg"] = p["min"] / p["gp"]
            p["gs40"] = 40 * p["gs"] / p["min"]
            w = p["min"] / (p["min"] + K)
            p["value"] = B_GS * w * (p["gs40"] - lg40) + B_REPL
            p["missedRun"] = sum(1 for d in dates if d > last)
            recent = dates[-5:]                 # how often he has played lately (rest, coach's decisions, niggles)
            p["recent"] = sum(1 for d in recent if d in set(p["dates"])) / len(recent) if recent else 1.0
        out[t] = {"G": G, "players": P.get(t, {})}
    return out, lg40


def return_date(text, season_start_year=2026):
    """'Early December' -> 7 Dec, 'Mid-December' -> 15 Dec, 'Late Nov' -> 25 Nov, '8 November' -> 8 Nov, 'December' -> 15 Dec."""
    t = (text or "").lower()
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*", t) or \
        re.search(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?\b", t)
    day = mon = None
    if m:
        a, b = m.groups()
        day, mon = (int(a), b) if a.isdigit() else (int(b), a)
    else:
        m = re.search(r"(early|mid|late)?[\s-]*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*", t)
        if not m:
            return None
        day, mon = {"early": 7, "mid": 15, "late": 25, None: 15}[m.group(1)], m.group(2)
    mo = MONTHS[mon[:3]]
    yr = season_start_year if mo >= 7 else season_start_year + 1
    try:
        return datetime.date(yr, mo, day)
    except ValueError:
        return None


def chance(p, row, game, k):
    """Chance player p plays this game (k = how many of his team's games come before it from now)."""
    if row is not None:
        cls, ret = row.get("cls"), (row.get("ret") or "")
        if cls in ("season", "retired"):
            return 0.0
        if cls == "dtd":
            return 0.5 if k == 0 else 1.0
        m = re.search(r"round\s*(\d+)", ret, re.I)
        if m and game.get("round") is not None:
            return 0.0 if game["round"] < int(m.group(1)) else 1.0
        d = return_date(ret)
        if d:
            gd = datetime.date.fromisoformat(game["date"])
            if "possibl" in ret.lower() or "maybe" in ret.lower():
                return 0.0 if gd < d else 0.5 if (gd - d).days <= 6 else 1.0
            return 0.0 if gd < d else 1.0
        return 0.0 if k < 2 else 0.5 if k < 4 else 1.0
    if p["missedRun"] >= 3:
        return 0.0 if k < 3 else p["recent"]
    return p["recent"]


def adjust(V, injuries, sched):
    """For each game to come (sorted by tip-off): each side's adjustment in points and the players behind it."""
    inj = {r["id"]: r for r in (injuries or {}).get("players", []) if r.get("id")}
    k = collections.Counter()
    out = []
    for g in sched:
        side = {}
        for s, t in (("h", g["home"]), ("a", g["away"])):
            tot, det = 0.0, []
            for p in V.get(t, {}).get("players", {}).values():
                row = inj.get(p["id"])
                if row is not None and row.get("team") not in (t, None):
                    row = None                      # listed for another club now: leave him to that club
                c = chance(p, row, g, k[t])
                x = (c - p["share"]) * p["mpg"] / 40
                v = x * p["value"]
                tot += v
                if abs(v) >= 0.25:
                    nrec = min(5, p["G"])
                    why = ("injury list: " + (row.get("status") or "out") + (f", {row['injury']}" if row.get("injury") else "")
                           + (f", back {row['ret']}" if row.get("ret") and row["ret"].upper() != "TBC" else ", return TBC")) if row is not None \
                        else ("not on the injury list but hasn't played the last %d games" % p["missedRun"] if c < 1 and p["missedRun"] >= 3 else
                              "played %d of the last %d games, %d of %d this season" % (round(p["recent"] * nrec), nrec, p["gp"], p["G"]))
                    det.append({"id": p["id"], "name": p["name"], "pts": round(v, 2), "chance": c, "why": why})
            side[s] = (round(tot, 2), sorted(det, key=lambda z: z["pts"]))
            k[t] += 1
        out.append(side)
    return out
