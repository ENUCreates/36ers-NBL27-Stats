"""Team stats by period (each quarter and overtime) for the round-by-round tips' played games, from the NBL's own
feed (its team_match_statistics rows: period 0 is the whole game, 1-4 the quarters, overtime its own rows).

Written into each game of games.json as
    periods: {"per": ["Q1", "Q2", "Q3", "Q4", "OT"], "home": [[...], ...], "away": [[...], ...], "fouls": "team",
              "src": "nbl"}
one row of FIELDS per period. A game's periods are kept only where, for both teams and every field, they add up to the
feed's full-game row and, for every field but fouls, to this site's box score, so a half or the full game summed
from them is the same box score shown everywhere else. Fouls are the feed's team fouls ("fouls": every foul charged to
the team, technicals included) where the feed fills them in for the game, otherwise its personal fouls; "fouls" in the
doc says which ("team" or "personal"). Either can differ a little from the fouls in this site's (ESPN's) box score. Otherwise
last run's are kept when this run's feed can't be read or doesn't add up, as long as they still add up to this site's
box score; else none are written (the page then shows only the full game)."""
import collections

FIELDS = ["pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf"]
CHECK = [f for f in FIELDS if f != "pf"]


def build(m, home, away):
    """-> periods doc, or (None, why)"""
    rows = m.get("periods") or []
    by = collections.defaultdict(dict)
    for r in rows:
        if r.get("team") not in (home, away):
            return None, f"a team row for {r.get('team')}"
        per, pt = str(r["per"]), r["pt"]
        # the feed numbers an overtime's team row 11, 12, ... and sometimes calls it 'regular'
        if per.isdigit() and int(per) > 4:
            per, pt = str(int(per) - 10 if int(per) > 10 else int(per) - 4), "overtime"
        by[r["team"]][(per, pt)] = dict(r)
    if set(by) != {home, away}:
        return None, "rows for one team only"
    full0 = {t: [v for k, v in by[t].items() if k[0] == "0"] for t in (home, away)}
    kind = "team" if all(len(full0[t]) == 1 and (full0[t][0].get("fouls") or 0) > 0 for t in (home, away)) else "personal"
    if kind == "team":
        for t in (home, away):
            for r in by[t].values():
                r["pf"] = r.get("fouls")
    keys = None
    for t in (home, away):
        ks = sorted((k for k in by[t] if k[0] != "0"), key=lambda k: (k[1] == "overtime", int(k[0]) if k[0].isdigit() else 99))
        if keys is None:
            keys = ks
        elif ks != keys:
            return None, "the two teams' periods differ"
        full = [v for k, v in by[t].items() if k[0] == "0"]
        if len(full) != 1:
            return None, "no full-game row"
        for f in FIELDS:
            vals = [by[t][k].get(f) for k in ks]
            if any(v is None for v in vals) or full[0].get(f) is None or sum(vals) != full[0][f]:
                return None, f"{t} {f} by period doesn't add up to the full game"
    if [k[0] for k in keys if k[1] != "overtime"] != ["1", "2", "3", "4"]:
        return None, "not four quarters"
    n_ot = sum(k[1] == "overtime" for k in keys)
    ot = iter(range(1, n_ot + 1))
    return {"per": [("OT" if n_ot == 1 else f"OT{next(ot)}") if k[1] == "overtime" else f"Q{k[0]}" for k in keys],
            "home": [[by[home][k][f] for f in FIELDS] for k in keys],
            "away": [[by[away][k][f] for f in FIELDS] for k in keys], "fouls": kind, "src": "nbl"}, None


def adds_up(doc, g):
    """every field but fouls, summed over the periods, equals this site's box score for both teams"""
    return all(sum(r[FIELDS.index(f)] for r in doc[side]) == T.get(f)
               for side, T in (("home", g["home"]), ("away", g["away"])) for f in CHECK)


def apply(games, off, index, prev_games=None):
    """games: games.json rows, edited in place. -> report lines"""
    pbp = (off or {}).get("pbp") or {}
    prev = {g["id"]: g.get("periods") for g in prev_games or [] if g.get("periods")}
    rep, n = [], collections.Counter()
    for g in games:
        home, away = g["home"]["team"], g["away"]["team"]
        tag = f"{g['date']} {away} at {home}"
        m = pbp.get(index.get((g["date"], frozenset((home, away)))))
        doc, why = (build(m, home, away) if m is not None else (None, None))
        if doc:
            for side, T in (("home", g["home"]), ("away", g["away"])):
                for f in CHECK:
                    if sum(r[FIELDS.index(f)] for r in doc[side]) != T.get(f):
                        doc, why = None, f"{T['team']} {f} differs from this site's box score"
                        break
                if not doc:
                    break
        if doc:
            g["periods"] = doc
            n["by period"] += 1
            continue
        g.pop("periods", None)
        pv = prev.get(g["id"])
        if pv and (m is None or adds_up(pv, g)):
            # this run's feed can't be read, or doesn't add up (the NBL revises a game's numbers now and then):
            # last run's are kept while they still add up to this site's box score
            g["periods"] = pv
            n["last run's kept"] += 1
            if why:
                rep.append(f"{tag}: this run's team stats by period don't add up ({why}) - last run's kept")
        elif why:
            rep.append(f"{tag}: no team stats by period ({why})")
            n["left out"] += 1
        else:
            n["no NBL feed"] += 1
    rep.insert(0, "team stats by period: " + ", ".join(f"{v} {k}" for k, v in sorted(n.items())))
    return rep


def keep_previous(games, prev_games):
    prev = {g["id"]: g.get("periods") for g in prev_games or [] if g.get("periods")}
    for g in games:
        if prev.get(g["id"]):
            g["periods"] = prev[g["id"]]


# ---- players by period: the NBL's play-by-play, each player's line per quarter (and overtime)
# Written into each game's players.json doc as
#     periods: {"per": ["Q1", ...], "f": PFIELDS, "p": {player id: [[...] per period]}, "bad": {player id: [fields]}}
# min = seconds on the floor (from the substitutions), pm = the score while he was on the floor (both left out for
# everyone in a game where the substitutions ever leave a team without five on the floor, or where anyone's minutes
# don't add up). A player's field is
# listed in "bad" (and the page shows "–" for it by quarter) wherever his periods don't add up to his line in this
# site's box score (minutes within 5 seconds); everything else adds up to it exactly.
import nbl_lineups as NL  # noqa: E402
import nbl_shots as NS  # noqa: E402

PFIELDS = ["min", "pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "pm"]
# fouls a player is charged with (a coach's or the bench's technicals go to no player; "drawn" is the foul drawn)
PLAYER_FOULS = {"personal", "offensive", "flagrant", "disruptive", "category one technical", "category two technical"}


def player_lines(m):
    """m: one match as official.py keeps it -> ({NBL player id: {period no.: Counter}}, [period nos.],
    whether its substitutions always leave five on the floor for each team)"""
    home, away = m["home"], m["away"]
    opp = {home: away, away: home}
    plays = NS.plays_of(m)               # less any free throw the official box score doesn't count
    on = NL.starters(m, plays)
    keep = set(NS.clean({**m, "plays": plays})[0])
    R = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    pers = sorted({NL.per_no(a) for a in plays} | {1, 2, 3, 4})
    floor_ok = True
    for per in pers:
        L, last = NL.plen(per), 0.0
        for i, a in ((i, a) for i, a in enumerate(plays) if NL.per_no(a) == per):
            tnow = L - NL.csec(a["clock"])
            if tnow > last:
                for t in opp:
                    floor_ok &= len(on[t]) == 5
                    for p in on[t]:
                        R[p][per]["min"] += tnow - last
                last = tnow
            ty, st, team, pid = (a.get("type") or "").lower(), (a.get("sub") or "").lower(), a.get("team"), a.get("pid")
            if ty == "substitution":
                if team in on and pid and st in ("in", "out"):
                    (on[team].add if st == "in" else on[team].discard)(pid)
                continue
            d, pts = {}, 0
            if ty in ("2pt", "3pt"):
                if i not in keep:
                    continue
                d["fga"] = 1
                if ty == "3pt":
                    d["tpa"] = 1
                if a.get("ok"):
                    d["fgm"], pts = 1, (3 if ty == "3pt" else 2)
                    if ty == "3pt":
                        d["tpm"] = 1
            elif ty == "freethrow":
                d["fta"] = 1
                if a.get("ok"):
                    d["ftm"], pts = 1, 1
            elif ty == "rebound" and st in ("offensive", "defensive"):
                d["oreb" if st == "offensive" else "dreb"] = 1
            elif ty in ("turnover", "assist", "steal", "block"):
                d[{"turnover": "tov", "assist": "ast", "steal": "stl", "block": "blk"}[ty]] = 1
            elif ty == "foul" and st in PLAYER_FOULS:
                d["pf"] = 1
            if pts:
                d["pts"] = pts
            if pid:
                for k, v in d.items():
                    R[pid][per][k] += v
            if pts and team in opp:
                for p in on[team]:
                    R[p][per]["pm"] += pts
                for p in on[opp[team]]:
                    R[p][per]["pm"] -= pts
        if L > last:
            for t in opp:
                floor_ok &= len(on[t]) == 5
                for p in on[t]:
                    R[p][per]["min"] += L - last
    return R, pers, floor_ok


def apply_players(pdocs, games, off, pairs, index, prev=None):
    """pdocs: {espn id: players.json doc}, edited in place. -> report lines"""
    pbp = (off or {}).get("pbp") or {}
    nbl_of = {k: v["pid"] for k, v in pairs.items() if v.get("pid")}
    rep, n, nbad = [], collections.Counter(), collections.Counter()
    for g in games:
        eid = g["id"].replace("espn-", "")
        doc = pdocs.get(eid)
        if not doc:
            continue
        doc.pop("periods", None)
        m = pbp.get(index.get((g["date"], frozenset((g["home"]["team"], g["away"]["team"])))))
        if m is None or not m.get("plays"):
            pv = ((prev or {}).get(eid) or {}).get("periods")
            if pv:
                doc["periods"] = pv
                n["last run's kept"] += 1
            else:
                n["no NBL play-by-play"] += 1
            continue
        R, pers, floor_ok = player_lines(m)
        labels = [f"Q{p}" if p <= 4 else ("OT" if len(pers) == 5 else f"OT{p - 4}") for p in pers]
        out, bad = {}, {}
        for t, L in (doc.get("teams") or {}).items():
            for r in L:
                if not (r.get("min") or 0) > 0:
                    continue
                pid = nbl_of.get(r["id"]) or (r["id"][4:] if str(r["id"]).startswith("nbl-") else None)
                q = R.get(pid) or {}
                rows = [[q.get(p, {}).get(f, 0) for f in PFIELDS] for p in pers]
                b = []
                for j, f in enumerate(PFIELDS):
                    v = sum(x[j] for x in rows)
                    if (abs(v - r["min"] * 60) > 5) if f == "min" else (v != (r.get(f) or 0)):
                        b.append(f)
                        nbad[f] += 1
                for x in rows:
                    x[0] = round(x[0])
                out[r["id"]] = rows
                if b:
                    bad[r["id"]] = b
                    rep.append(f"{g['date']} {r['name']}: by quarter doesn't add up to the box score for {', '.join(b)}")
        # one player's minutes off means the substitutions are wrong somewhere: then no one's minutes or +/-
        # by quarter can be trusted
        if not floor_ok or any("min" in b for b in bad.values()):
            for pid in out:
                bad[pid] = sorted(set(bad.get(pid, [])) | {"min", "pm"}, key=PFIELDS.index)
            rep.append(f"{g['date']} {g['away']['team']} at {g['home']['team']}: the substitutions don't add up, "
                       "so minutes and +/- aren't split by quarter")
        doc["periods"] = {"per": labels, "f": PFIELDS, "p": out, "bad": bad, "src": "nbl"}
        n["every player adds up" if not bad else "some fields left out"] += 1
    rep.insert(0, "players by period: " + ", ".join(f"{v} {k}" for k, v in sorted(n.items()))
               + (f"; fields left out: {dict(nbad)}" if nbad else ""))
    return rep


def keep_previous_players(pdocs, prev):
    for eid, doc in pdocs.items():
        pv = ((prev or {}).get(eid) or {}).get("periods")
        if pv:
            doc["periods"] = pv
