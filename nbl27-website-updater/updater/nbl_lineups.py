"""NBL27 lineup stints from the NBL's own play-by-play: the league's live stats feed, which official.py reads from the
NBL's API (prod.rosetta.nbl.com.au, match/<id>/live/all) along with the official box scores.

The feed records every substitution and lists the plays in game order (late corrections by the scorers sit at their
place in the game, unlike their action numbers), so the five on the floor are read straight off it: no solving. Time
moves on with each row's game clock, the clock's own start/stop rows included: a substitution keyed while the clock
was still running counts from the moment it stopped, as the official minutes do. A
team-game is used only when it reproduces the official box score exactly:
  - five players on the floor at every play, five starters;
  - floor time equal to the game's length (40 minutes plus 5 per overtime);
  - points, field goals, threes, free throws, rebounds and turnovers equal to the official team totals;
  - every player's minutes within MIN_TOL seconds of his official minutes, and his plus-minus equal to the official one;
and it must also pass the page's own check against this site's box score (lineupTeamOk in index.html: points equal,
FGA and FTA within 3, every player's minutes within 2). Otherwise the ESPN-based lineups (oncourt.py, solved from
ESPN's play-by-play) are kept for that team-game.

Checked on the first 47 NBL27 games (31 Oct 2026): 93 of 94 team-games reproduce the official box exactly (every
player's minutes within 2 seconds, every plus-minus exact), against 73% of player-minutes within 2 seconds for the
ESPN-based solve. The other team-game (Illawarra v Adelaide, 2 Oct 2026) has a scorer's correction that takes the
wrong players off, leaving four on the floor; it keeps the ESPN-based lineups.

rebuild.py calls apply() after reading the official data; each lineup document then says where each team's
lineups come from: 'src' {team: 'nbl' | 'espn'}.
"""
import collections

STATS = ["pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "oreb", "dreb", "tov"]
MIN_TOL = 5          # seconds
NBL_SRC = "https://nbl.com.au/ (the NBL's play-by-play)"


def per_no(a):
    """period number: 1-4, overtimes 5, 6, ... (the feed numbers overtimes 1, 2, ... with period_type 'overtime')"""
    p = int(a["per"])
    return 4 + p if a.get("pt") == "overtime" else p


def plen(p):
    return 600 if p <= 4 else 300


def csec(c):
    m, s = str(c).split(":")
    return int(m) * 60 + float(s)


def dur(v):
    """'37:26', 'PT37M26S' or minutes as a number -> seconds"""
    if v in (None, ""):
        return 0.0
    v = str(v)
    if v.startswith("PT"):
        import re
        m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?$", v)
        if not m:
            return 0.0
        h, mi, se = (float(g) if g else 0.0 for g in m.groups())
        return h * 3600 + mi * 60 + se
    if ":" in v:
        return csec(v)
    return float(v) * 60


def stints(m):
    """m: one match as official.py keeps it ({'home', 'away', 'players', 'teams', 'plays'}).
    -> ([(team, frozenset of player ids, seconds, own totals, opponents' totals)], [problems])"""
    home, away = m["home"], m["away"]
    opp = {home: away, away: home}
    on = {t: {p["pid"] for p in m["players"] if p["team"] == t and p.get("starter")} for t in opp}
    probs = [f"{t}: {len(on[t])} starters" for t in opp if len(on[t]) != 5]
    out = []
    plays = [a for a in m["plays"] if a.get("clock")]
    pers = sorted({per_no(a) for a in plays} | {1, 2, 3, 4})
    for per in pers:
        L = plen(per)
        cur = {t: None for t in opp}
        last = 0.0

        def flush(t):
            s = cur[t]
            if s is not None and (s[1] or any(s[2].values()) or any(s[3].values())):
                out.append((t, s[0], s[1], s[2], s[3]))
            cur[t] = None

        def lineup(t):
            s = frozenset(on[t])
            if len(s) != 5:
                probs.append(f"{t}: {len(s)} on the floor in period {per} with {L - last:.0f}s left")
            if cur[t] is None or cur[t][0] != s:
                flush(t)
                cur[t] = [s, 0.0, {k: 0 for k in STATS}, {k: 0 for k in STATS}]
            return cur[t]
        for a in (a for a in plays if per_no(a) == per):
            tnow = L - csec(a["clock"])
            if tnow > last:
                for t in opp:
                    lineup(t)[1] += tnow - last
                last = tnow
            # the feed's casing varies between games ('In' / 'in', 'Offensive' / 'offensive')
            ty, st, team = (a.get("type") or "").lower(), (a.get("sub") or "").lower(), a.get("team")
            if ty == "substitution":
                if team in on and a.get("pid") and st in ("in", "out"):
                    (on[team].add if st == "in" else on[team].discard)(a["pid"])
                continue
            if team not in opp:
                continue
            d = {}
            if ty in ("2pt", "3pt"):
                d["fga"] = 1
                if ty == "3pt":
                    d["tpa"] = 1
                if a.get("ok"):
                    d["fgm"], d["pts"] = 1, (3 if ty == "3pt" else 2)
                    if ty == "3pt":
                        d["tpm"] = 1
            elif ty == "freethrow":
                d["fta"] = 1
                if a.get("ok"):
                    d["ftm"], d["pts"] = 1, 1
            elif ty == "rebound" and st in ("offensive", "defensive"):
                d["oreb" if st == "offensive" else "dreb"] = 1
            elif ty == "turnover":
                d["tov"] = 1
            if d:
                f, ag = lineup(team)[2], lineup(opp[team])[3]
                for k, v in d.items():
                    f[k] += v
                    ag[k] += v
        if L > last:
            for t in opp:
                lineup(t)[1] += L - last
        for t in opp:
            flush(t)
    return out, probs, max(pers)


def check(m, S, probs, nper):
    """-> {team: [reasons]} against the official box score (empty list = reproduces it)"""
    expect = 2400 + 300 * max(0, nper - 4)
    tbox = {x["team"]: x for x in m["teams"]}
    res = {}
    for t in (m["home"], m["away"]):
        why = [p for p in probs if p.startswith(t + ":")]
        mine = [s for s in S if s[0] == t]
        sec = sum(s[2] for s in mine)
        if abs(sec - expect) > 1:
            why.append(f"floor time {sec:.0f}s, game {expect}s")
        tb = tbox.get(t)
        if not tb:
            why.append("no official team totals")
        else:
            for k in STATS:
                v = sum(s[3][k] for s in mine)
                if tb.get(k) is not None and v != tb[k]:
                    why.append(f"{k} {v}, official {tb[k]}")
        psec, ppm = collections.Counter(), collections.Counter()
        for _, ps, s, f, a in mine:
            for p in ps:
                psec[p] += s
                ppm[p] += f["pts"] - a["pts"]
        for p in m["players"]:
            if p["team"] != t:
                continue
            bs = dur(p.get("min"))
            if abs(psec.get(p["pid"], 0) - bs) > MIN_TOL:
                why.append(f"{p['name']} {psec.get(p['pid'], 0):.0f}s on the floor, official {bs:.0f}s")
            if p.get("pm") not in (None, "") and (bs or psec.get(p["pid"])) and int(p["pm"]) != ppm.get(p["pid"], 0):
                why.append(f"{p['name']} {ppm.get(p['pid'], 0):+d}, official {int(p['pm']):+d}")
        res[t] = why
    return res


def site_ok(units, team, g, pdoc):
    """The page's own check (lineupTeamOk in index.html) against this site's box score -> reason or ''"""
    b = g["home"] if g["home"]["team"] == team else g["away"]
    if not units or any(len(u["p"]) != 5 for u in units):
        return "a lineup without five players"
    sec = sum(u["sec"] for u in units)
    if abs(sec - (2400 + ((b.get("min") or 200) - 200) * 12)) > 30:
        return f"floor time {sec}s"
    s = lambda k: sum(u["f"][k] for u in units)
    if s("pts") != b["pts"] or abs(s("fga") - b["fga"]) > 3 or abs(s("fta") - b["fta"]) > 3:
        return f"pts/fga/fta {s('pts')}/{s('fga')}/{s('fta')}, this site's box {b['pts']}/{b['fga']}/{b['fta']}"
    on = collections.Counter()
    for u in units:
        for p in u["p"]:
            on[p] += u["sec"]
    for p in (pdoc or {}).get("teams", {}).get(team, []):
        if abs(on.get(p["id"], 0) / 60 - (p.get("min") or 0)) > 2:
            return f"{p.get('name')} {on.get(p['id'], 0) / 60:.1f} min on the floor, box {p.get('min')}"
    return ""


def units_of(S, team, espn_of):
    """stints of one team -> lineup units in data/lineups.json's shape (ESPN player ids; 'nbl-<id>' for a player the
    official data couldn't pair with ESPN's box score)"""
    agg = {}
    for t, ps, sec, f, a in S:
        if t != team:
            continue
        k = tuple(sorted(espn_of.get(p) or f"nbl-{p}" for p in ps))
        g = agg.setdefault(k, {"sec": 0.0, "f": {x: 0 for x in STATS}, "a": {x: 0 for x in STATS}})
        g["sec"] += sec
        for x in STATS:
            g["f"][x] += f[x]
            g["a"][x] += a[x]
    return [{"p": list(k), "sec": int(round(v["sec"])), "f": v["f"], "a": v["a"]}
            for k, v in sorted(agg.items(), key=lambda kv: -kv[1]["sec"])]


def apply(lineups, games, players, off, pairs, index, prev=None):
    """Replace each team's ESPN-based lineups (lineups: {espn id: doc}, edited in place) with the NBL's play-by-play
    lineups wherever those reproduce the official box score and pass the page's check. games / players: this
    run's games.json / players.json lists; off: official.load(); pairs: official.compare()['pairs'];
    index: official.nbl27_index(off); prev: last run's lineups {espn id: doc} (reused for a team-game whose
    official play-by-play can't be read this run). -> report lines."""
    espn_of = {v["pid"]: k for k, v in pairs.items() if v.get("pid")}
    P = {p["espnId"]: p for p in players}
    pbp = off.get("pbp") or {}
    rep, n = [], collections.Counter()
    for g in games:
        eid = g["id"].replace("espn-", "")
        teams = (g["home"]["team"], g["away"]["team"])
        doc = lineups.get(eid)
        if doc is None:
            doc = {"espnId": eid, "date": g["date"], "home": teams[0], "away": teams[1], "names": {}, "teams": {}, "fit": {},
                   "source": f"https://www.espn.com/nbl/game/_/gameId/{eid}"}
        src = doc.setdefault("src", {})
        for t in teams:
            if doc["teams"].get(t):
                src[t] = "espn"
        m = pbp.get(index.get((g["date"], frozenset(teams))))
        tag = f"{g['date']} {g['away']['team']} at {g['home']['team']}"
        if m is None:
            # no official play-by-play this run: keep last run's NBL-based lineups, if any
            pv = (prev or {}).get(eid) or {}
            for t in teams:
                if (pv.get("src") or {}).get(t) == "nbl":
                    doc["teams"][t] = pv["teams"][t]
                    doc["fit"][t] = (pv.get("fit") or {}).get(t)
                    src[t] = "nbl"
                    doc["names"].update({k: v for k, v in (pv.get("names") or {}).items() if k not in doc["names"]})
            n["no official play-by-play"] += 1
            if doc["teams"]:
                lineups[eid] = doc
            continue
        S, probs, nper = stints(m)
        why = check(m, S, probs, nper)
        names = {p["pid"]: p["name"] for p in m["players"]}
        for t in teams:
            units = units_of(S, t, espn_of)
            bad = why.get(t) or []
            if not bad:
                s = site_ok(units, t, g, P.get(eid))
                if s:
                    bad = [f"page check: {s}"]
            if bad:
                n["espn" if doc["teams"].get(t) else "left out"] += 1
                rep.append(f"{tag} {t}: NBL play-by-play not used ({'; '.join(bad[:3])})"
                           + ("" if doc["teams"].get(t) else " - no lineups for this team-game"))
                continue
            doc["teams"][t] = units
            src[t] = "nbl"
            err = max([0.0] + [abs(sum(s[2] for s in S if s[0] == t and p["pid"] in s[1]) - dur(p.get("min")))
                               for p in m["players"] if p["team"] == t])
            doc["fit"][t] = {"source": "nbl", "maxMinErr": int(round(err)), "maxPmErr": 0, "offcourt": 0, "unrecorded": 0,
                             "ignored": 0}
            for u in units:
                for i in u["p"]:
                    if i not in doc["names"]:
                        pid = i[4:] if i.startswith("nbl-") else next((k for k, v in espn_of.items() if v == i), None)
                        doc["names"][i] = next((x["name"] for x in (P.get(eid) or {}).get("teams", {}).get(t, []) if x["id"] == i),
                                               None) or names.get(pid) or i
            n["nbl"] += 1
        if all(src.get(t) == "nbl" for t in teams):
            doc.pop("invalid", None)
        if doc["teams"]:
            lineups[eid] = doc
    rep.insert(0, "lineups by team-game: " + ", ".join(f"{k} {v}" for k, v in sorted(n.items())))
    return rep, dict(n)


def keep_previous(lineups, prev):
    """The official data couldn't be read this run: keep last run's NBL-based lineups team by team."""
    for eid, pv in (prev or {}).items():
        doc = lineups.get(eid)
        if doc is None:
            continue
        for t, s in (pv.get("src") or {}).items():
            if s == "nbl" and pv["teams"].get(t):
                doc["teams"][t] = pv["teams"][t]
                doc.setdefault("fit", {})[t] = (pv.get("fit") or {}).get(t)
                doc.setdefault("src", {})[t] = "nbl"
                doc["names"].update({k: v for k, v in (pv.get("names") or {}).items() if k not in doc["names"]})
        if all((doc.get("src") or {}).get(t) == "nbl" for t in (doc["home"], doc["away"])):
            doc.pop("invalid", None)
