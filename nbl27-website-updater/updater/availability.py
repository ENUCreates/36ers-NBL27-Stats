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
  taken off the list since he last played ("cleared" in injuries.json)   back: as below, with the games he has just
                                        missed not counted - until he plays, or his team plays 2 games without him
  not listed, but missed his team's last 3+ games    0 for its next 3 games, then as below (reason unknown)
  otherwise                             the share of his team's games he has played since his first one for it, not
                                        counting any absence of 3+ games in a row that is over (he has played since)

That last share replaced "the share of his team's last 5 games" on 21 Nov 2026. Tested on every NBL regular season
2015-16 to 2025-26 (181,525 cases of a rotation player - 10+ minutes a game over 3+ games - who played his team's
latest game, each scored on whether he played each of its next 10 games): Brier score 0.1059 with the last 5 games,
0.0990 with this share (0.1015 if every such player were assumed to play); for players back from an absence of 3+
games in the last 5, 0.2925 -> 0.1633, because the last 5 games kept counting the absence against them all season.

Who is on each club's list now, from the NBL's own squads (data/roster.json, saved by the daily update):
  no longer on the club's list, and missed its latest game     0 (released, or moved clubs)
  moved clubs this season (now on the new club's list)          for the new club: the share above, counted from his
      first game for it, and his value on all his games this season (both clubs) - not just the few he has played
      for it
A player who played his club's latest game always counts as with it, whatever the list says.
"""
import collections, datetime, re, unicodedata

B_GS, B_REPL, K = 0.616, 1.654, 200.0
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def game_score(x):
    return (x["pts"] + 0.4 * x["fgm"] - 0.7 * x["fga"] - 0.4 * (x["fta"] - x["ftm"]) + 0.7 * x["oreb"] + 0.3 * x["dreb"]
            + x["stl"] + 0.7 * x["ast"] + 0.7 * x["blk"] - 0.4 * (x.get("pf") or 0) - x["tov"])


def norm(name):
    """a name to match the NBL's squad lists with this site's (accents, punctuation and Jr / III etc. dropped)"""
    s = unicodedata.normalize("NFKD", re.sub(r"\(.*?\)", " ", name or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(t for t in re.sub(r"[^a-z ]", " ", s.replace("-", " ").replace("'", "")).split()
                    if t not in ("jr", "sr", "ii", "iii", "iv"))


def fit_share(seq):
    """seq: did he play each of his team's games, from his first for it. -> (share of games played, not counting
    absences of 3+ in a row that are over; the same with the absence he is in now also counted as over; games
    played; games counted; games left out; how many absences they make)"""
    out = [False] * len(seq)
    runs = 0
    i = 0
    while i < len(seq):
        if seq[i]:
            i += 1
            continue
        j = i
        while j < len(seq) and not seq[j]:
            j += 1
        if j - i >= 3 and j < len(seq):          # 3+ in a row, and he has played since
            out[i:j] = [True] * (j - i)
            runs += 1
        i = j
    n = sum(1 for o in out if not o)
    gp = sum(seq)
    cur = 0
    while cur < len(seq) and not seq[len(seq) - 1 - cur]:
        cur += 1
    fit = gp / n if n else 1.0
    back = gp / (n - cur) if n - cur > 0 else 1.0
    return fit, back, gp, n, sum(out), runs


def values(games, players, roster=None):
    """Per team: its games (dates) and, per player who has played for it, games, minutes a game, value per 40,
    how many of the team's games in a row he has missed up to now, and how often he plays (fit_share). roster:
    data/roster.json (the NBL's current squads), or None to leave squads out."""
    tg = collections.defaultdict(list)
    for g in games:
        for s in ("home", "away"):
            tg[g[s]["team"]].append(g["date"])
    P = collections.defaultdict(dict)
    A = collections.defaultdict(lambda: {"min": 0.0, "gs": 0.0, "gp": 0, "teams": set()})   # per player, every club
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
                a = A[x["id"]]; a["min"] += m; a["gs"] += gs; a["gp"] += 1; a["teams"].add(t)
                gs_all += gs; min_all += m
    lg40 = 40 * gs_all / min_all if min_all else 0.0
    listed = {t: {norm(r.get("name")) for r in L} for t, L in ((roster or {}).get("teams") or {}).items() if L}
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
            mine = set(p["dates"])
            seq = [d in mine for d in dates if d >= min(p["dates"])]      # his team's games from his first for it
            # how often he plays (rest, coach's decisions, niggles), absences of 3+ games that are over left out
            p["fit"], p["fitBack"], p["fitGp"], p["fitN"], p["fitOut"], p["fitRuns"] = fit_share(seq)
            if t in listed:
                on = norm(p["name"]) in listed[t]
                if not on and p["missedRun"] >= 1:
                    p["left"] = True            # not on the club's list now, and missed its latest game
                a = A[p["id"]]
                if on and len(a["teams"]) > 1:
                    # moved clubs this season: how often he has played for this club (counted from his first game for
                    # it, above), and his value on all his games this season
                    p["joined"] = {"from": sorted(a["teams"] - {t}), "since": len(seq), "played": sum(seq)}
                    p["gs40"] = 40 * a["gs"] / a["min"]
                    p["value"] = B_GS * a["min"] / (a["min"] + K) * (p["gs40"] - lg40) + B_REPL
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


def is_cleared(p, c, team_dates):
    """c: his entry in injuries.json "cleared" (taken off the league's list since he last played): still counts while
    he hasn't played since and his team has played fewer than 2 games since he came off it."""
    if not c or p["missedRun"] == 0 or max(p["dates"]) >= c.get("off", ""):
        return False
    return sum(1 for d in team_dates if d >= c.get("off", "")) < 2


def chance(p, row, game, k, cleared=False):
    """Chance player p plays this game (k = how many of his team's games come before it from now; cleared: he has
    been taken off the injury list since he last played, is_cleared)."""
    if p.get("left"):
        return 0.0
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
    if cleared:
        return p["fitBack"]
    if p["missedRun"] >= 3:
        return 0.0 if k < 3 else p["fit"]
    return p["fit"]


def day(iso):
    d = datetime.date.fromisoformat(iso[:10])
    return f"{d.day} {d:%b}"


def adjust(V, injuries, sched, team_dates=None):
    """For each game to come (sorted by tip-off): each side's adjustment in points and the players behind it.
    team_dates: {team: dates of its games so far} (for players taken off the injury list, is_cleared)."""
    inj = {r["id"]: r for r in (injuries or {}).get("players", []) if r.get("id")}
    clr = {(c.get("team"), c["id"]): c for c in (injuries or {}).get("cleared", []) if c.get("id")}
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
                cc = clr.get((t, p["id"]))
                cl = row is None and is_cleared(p, cc, (team_dates or {}).get(t, []))
                c = chance(p, row, g, k[t], cl)
                x = (c - p["share"]) * p["mpg"] / 40
                v = x * p["value"]
                tot += v
                if abs(v) >= 0.25:
                    J = p.get("joined")
                    pre = p["G"] - (p["fitN"] + p["fitOut"])          # the team's games before his first for it
                    skip = [f"the {pre} before his first" if pre else "",
                            "" if not p["fitOut"] else f"{p['fitOut']} missed in a row" if p["fitRuns"] == 1
                            else f"{p['fitOut']} missed in {p['fitRuns']} runs of 3+"]
                    skip = " and ".join(x for x in skip if x)
                    left_out = f", {p['fitGp']} of {p['fitN']} leaving out {skip}" if skip else ""
                    why = ("no longer in the club's NBL squad" if p.get("left") else
                           ("injury list: " + (row.get("status") or "out") + (f", {row['injury']}" if row.get("injury") else "")
                           + (f", back {row['ret']}" if row.get("ret") and row["ret"].upper() != "TBC" else ", return TBC")) if row is not None
                        else "back: taken off the injury list on %s, after missing %d games" % (day(cc["off"]), p["missedRun"]) if cl
                        else "joined from %s: played %d of %d games since" % (" and ".join(J["from"]), J["played"], J["since"]) if J
                        else ("not on the injury list but hasn't played the last %d games" % p["missedRun"] if c < 1 and p["missedRun"] >= 3 else
                              "played %d of %d games this season%s" % (p["gp"], p["G"], left_out)))
                    det.append({"id": p["id"], "name": p["name"], "pts": round(v, 2), "chance": round(c, 3), "why": why})
            side[s] = (round(tot, 2), sorted(det, key=lambda z: z["pts"]))
            k[t] += 1
        out.append(side)
    return out
