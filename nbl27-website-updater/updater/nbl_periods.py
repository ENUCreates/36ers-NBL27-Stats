"""Team stats by period (each quarter and overtime) for the round-by-round tips' played games, from the NBL's own
feed (its team_match_statistics rows: period 0 is the whole game, 1-4 the quarters, overtime its own rows).

Written into each game of games.json as
    periods: {"per": ["Q1", "Q2", "Q3", "Q4", "OT"], "home": [[...], ...], "away": [[...], ...], "fouls": "team",
              "src": "nbl"}
one row of FIELDS per period. A game's periods are kept only where, for both teams and every field, they add up to the
feed's full-game row and, for every field but fouls, to this site's box score, so a half or the full game summed
from them is the same box score shown everywhere else. Fouls are the feed's team fouls ("fouls": every foul charged to
the team, technicals included) where the feed fills them in for the game, otherwise its personal fouls; "fouls" in the
doc says which ("team" or "personal"). Either can differ a little from the fouls in this site's (ESPN's) box score. Otherwise none are written (the page
then shows only the full game), or last run's are kept when this run's feed can't be read."""
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
        by[r["team"]][(r["per"], r["pt"])] = dict(r)
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
        if m is None and prev.get(g["id"]):
            g["periods"] = prev[g["id"]]
            n["last run's kept"] += 1
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
