"""NBL27 events (rotations, clutch, possessions, assists) from the NBL's own play-by-play, in the same shape as
events.py builds them from ESPN's: who is on the floor comes straight from the feed's substitutions (as in
nbl_lineups.py), points from the made shots and free throws the official box score counts (nbl_shots.clean), the
passer of each assisted basket, the stealer of each turnover and the blocker of each block from the feed's own
assist / steal / block rows next to them.

The rules are events.py's, unchanged:
  - rotations: each player's stretches on the floor, in game seconds;
  - clutch: the last 5:00 of the 4th quarter and all of overtime while the margin is 5 or fewer - seconds, the team
    box score for and against, and each player's line (+/- included);
  - possessions: each one by how it started (steal, dead-ball turnover, defensive rebound, opponent's make, other)
    with its points; transition = a possession after a steal or defensive rebound whose first shot, free throw or
    turnover comes within 7 seconds; second chance = points after an offensive rebound in the same possession;
  - fg: every made field goal as (scorer, passer or '', zone, value) for the assist network.

A team-game's NBL events are used only when its lineups reproduce the official box score (nbl_lineups.check: five on
the floor throughout, every player's minutes and plus-minus) and its points and made field goals equal this site's
box score; otherwise ESPN's events (events.py) are kept for that team. Each document says where each team's events
come from: 'src' {team: 'nbl' | 'espn'}.
"""
import collections

import nbl_lineups as NL
import nbl_shots as NS

CLUTCH_SEC, CLUTCH_MARGIN, TRANS_SEC = 300, 5, 7
SRC = NS.SRC


def poff(per):
    return 600 * (per - 1) if per <= 4 else 2400 + 300 * (per - 5)


def links(plays, keep):
    """-> ({made shot index: assist pid}, {turnover index: steal pid}, {missed shot index: block pid}), each row
    linked to the play it belongs to: same period, same clock, nearest first"""
    ty = lambda a: (a.get("type") or "").lower()
    ast, stl, blk = {}, {}, {}
    for j, a in enumerate(plays):
        t = ty(a)
        if t not in ("assist", "steal", "block") or not a.get("pid"):
            continue
        near = sorted((k for k in range(max(0, j - 15), min(len(plays), j + 15)) if k != j), key=lambda k: (k > j, abs(k - j)))
        for k in near:
            b = plays[k]
            if b.get("per") != a.get("per") or b.get("pt") != a.get("pt") or b.get("clock") != a.get("clock"):
                continue
            tb = ty(b)
            if t == "assist" and tb in ("2pt", "3pt") and b.get("ok") and k in keep and b.get("team") == a.get("team") \
                    and k not in ast:
                ast[k] = a["pid"]; break
            if t == "steal" and tb == "turnover" and b.get("team") != a.get("team") and k not in stl:
                stl[k] = a["pid"]; break
            if t == "block" and tb in ("2pt", "3pt") and not b.get("ok") and b.get("team") != a.get("team") and k not in blk:
                blk[k] = a["pid"]; break
    return ast, stl, blk


def ft_of(a):
    """'1Of2' -> (1, 2)"""
    s = (a.get("sub") or "").lower().replace(" ", "")
    if "of" in s:
        x, y = s.split("of", 1)
        if x.isdigit() and y.isdigit():
            return int(x), int(y)
    return None


def build(m, key):
    """one match -> {team: events team entry (events.py's shape)}, last period. key: NBL player id -> site id"""
    home, away = m["home"], m["away"]
    clubs, opp = [home, away], {home: away, away: home}
    plays = [a for a in m["plays"] if a.get("clock")]
    keep_idx, _ = NS.clean({**m, "plays": plays})
    keep = set(keep_idx)
    E = NS.ends({**m, "plays": plays})
    ast, stl, blk = links(plays, keep)
    on = NL.starters(m, plays)
    T = {c: {"rot": collections.defaultdict(list),
             "clutch": {"sec": 0, "f": collections.Counter(), "a": collections.Counter(), "p": collections.defaultdict(collections.Counter)},
             "poss": {k: [0, 0] for k in ("steal", "dto", "dreb", "make", "other")},
             "trans": [0, 0], "sc": [0, 0], "fg": collections.Counter(), "pts": 0} for c in clubs}
    pers = sorted({NL.per_no(a) for a in plays} | {1, 2, 3, 4})
    pos = None
    state = {"pos": None}

    def close(p):
        if not p:
            return
        X = T[p["team"]]
        X["poss"][p["type"]][0] += 1; X["poss"][p["type"]][1] += p["pts"]
        if p["trans"]:
            X["trans"][0] += 1; X["trans"][1] += p["tr_pts"]
        if p["oreb"]:
            X["sc"][0] += 1; X["sc"][1] += p["sc_pts"]

    def start(team, typ, t0):
        close(state["pos"])
        state["pos"] = {"team": team, "type": typ, "t0": t0, "first": None, "pts": 0, "oreb": False, "sc_pts": 0,
                        "tr_pts": 0, "trans": False}

    def act(team, g, last_term):
        p = state["pos"]
        if p is None or p["team"] != team:
            lt = last_term[opp[team]]
            if lt and lt[0] in ("steal", "dto", "make"):
                start(team, lt[0], lt[1])
            else:
                start(team, "other", lt[1] if lt else g)
        p = state["pos"]
        if p["first"] is None:
            p["first"] = g
            p["trans"] = p["type"] in ("steal", "dreb") and g - p["t0"] <= TRANS_SEC

    for per in pers:
        L = NL.plen(per)
        close(state["pos"]); state["pos"] = None
        last_term = {c: None for c in clubs}
        prev_ft_nonfinal = False
        last = 0.0

        def advance(tnow):
            dt_all = tnow - last
            if dt_all <= 0:
                return
            g0, g1 = poff(per) + last, poff(per) + tnow
            for c in clubs:
                for pid in on[c]:
                    st = T[c]["rot"][key(pid)]
                    if st and abs(st[-1][1] - g0) < 1e-6:
                        st[-1][1] = g1
                    else:
                        st.append([g0, g1])
            if per >= 4 and abs(T[home]["pts"] - T[away]["pts"]) <= CLUTCH_MARGIN:
                c0 = max(last, L - CLUTCH_SEC) if per == 4 else last
                dt = tnow - c0
                if dt > 0:
                    for c in clubs:
                        T[c]["clutch"]["sec"] += dt
                        for pid in on[c]:
                            T[c]["clutch"]["p"][key(pid)]["sec"] += dt
        for i, a in ((i, a) for i, a in enumerate(plays) if NL.per_no(a) == per):
            tnow = L - NL.csec(a["clock"])
            if tnow > last:
                advance(tnow)
                last = tnow
            t, st, tm = (a.get("type") or "").lower(), (a.get("sub") or "").lower(), a.get("team")
            if t == "substitution":
                if tm in on and a.get("pid") and st in ("in", "out"):
                    (on[tm].add if st == "in" else on[tm].discard)(a["pid"])
                continue
            if tm not in T:
                continue
            g = poff(per) + tnow
            kind = None
            if t in ("2pt", "3pt"):
                if i not in keep:
                    continue           # a shot the official box score doesn't count
                kind = "fg"
            elif t == "freethrow":
                kind = "ft"
            elif t == "rebound" and st in ("offensive", "defensive"):
                kind = "oreb" if st == "offensive" else "dreb"
            elif t == "turnover":
                kind = "tov"
            if not kind:
                continue
            made = bool(a.get("ok")) if kind in ("fg", "ft") else False
            sh = a.get("pid")
            clutch = per >= 4 and (per > 4 or L - tnow <= CLUTCH_SEC) and abs(T[home]["pts"] - T[away]["pts"]) <= CLUTCH_MARGIN
            # possessions
            p = state["pos"]
            if kind == "dreb":
                if p is None or p["team"] != tm:
                    start(tm, "dreb", g)
            elif kind == "oreb":
                if not (prev_ft_nonfinal and not sh):        # a dead-ball team rebound between free throws: no change
                    if p is None or p["team"] != tm:
                        act(tm, g, last_term)
                    state["pos"]["oreb"] = True
            else:
                act(tm, g, last_term)
                if kind == "tov":
                    last_term[tm] = ("steal" if i in stl else "dto", g)
                elif kind == "fg":
                    last_term[tm] = ("make", g) if made else ("miss", g)
                elif kind == "ft" and made:
                    f = ft_of(a)
                    if f and f[0] == f[1]:
                        last_term[tm] = ("make", g)
            # points
            pv = 0
            if made:
                pv = 1 if kind == "ft" else (3 if t == "3pt" else 2)
            if pv:
                T[tm]["pts"] += pv
                p = state["pos"]
                if p and p["team"] == tm:
                    p["pts"] += pv
                    if p["oreb"]:
                        p["sc_pts"] += pv
                    elif p["trans"]:
                        p["tr_pts"] += pv
            # clutch box
            if clutch:
                if pv:
                    T[tm]["clutch"]["f"]["pts"] += pv; T[opp[tm]]["clutch"]["a"]["pts"] += pv
                    for pid in on[tm]:
                        T[tm]["clutch"]["p"][key(pid)]["pm"] += pv
                    for pid in on[opp[tm]]:
                        T[opp[tm]]["clutch"]["p"][key(pid)]["pm"] -= pv
                F, A, P = T[tm]["clutch"]["f"], T[opp[tm]]["clutch"]["a"], T[tm]["clutch"]["p"]
                delta = collections.Counter()
                if kind == "ft":
                    delta["fta"] += 1; delta["ftm"] += int(made)
                    if sh:
                        P[key(sh)]["fta"] += 1; P[key(sh)]["ftm"] += int(made); P[key(sh)]["pts"] += int(made)
                elif kind == "fg":
                    three = t == "3pt"
                    delta["fga"] += 1; delta["tpa"] += three; delta["fgm"] += made; delta["tpm"] += made and three
                    if sh:
                        P[key(sh)]["fga"] += 1; P[key(sh)]["tpa"] += three; P[key(sh)]["fgm"] += made; P[key(sh)]["tpm"] += made and three
                        P[key(sh)]["pts"] += (3 if three else 2) * made
                    if made and i in ast:
                        P[key(ast[i])]["ast"] += 1; delta["ast"] += 1
                    if not made and i in blk:
                        T[opp[tm]]["clutch"]["p"][key(blk[i])]["blk"] += 1
                elif kind in ("oreb", "dreb"):
                    delta[kind] += 1
                    if sh:
                        P[key(sh)][kind] += 1
                elif kind == "tov":
                    delta["tov"] += 1
                    if sh:
                        P[key(sh)]["tov"] += 1
                    if i in stl:
                        T[opp[tm]]["clutch"]["p"][key(stl[i])]["stl"] += 1
                for k, v in delta.items():
                    F[k] += v; A[k] += v
            if kind == "ft":
                f = ft_of(a)
                prev_ft_nonfinal = bool(f and f[0] != f[1])
            else:
                prev_ft_nonfinal = False
            # made field goals for the assist network
            if kind == "fg" and made and sh:
                X, Y = NS.frame(a.get("x"), a.get("y"), E.get((tm, NS.half(a)), "lo"))
                z = NS.zone(t == "3pt", X, Y)
                T[tm]["fg"][(key(sh), key(ast[i]) if i in ast else "", NS.ZC[z], 3 if t == "3pt" else 2)] += 1
        if L > last:
            advance(L)
            last = L
    close(state["pos"])
    out = {}
    for c in clubs:
        X = T[c]
        out[c] = {
            "rot": {a: [[round(s), round(e)] for s, e in v if e - s >= 1] for a, v in X["rot"].items()},
            "clutch": {"sec": round(X["clutch"]["sec"]), "f": dict(X["clutch"]["f"]), "a": dict(X["clutch"]["a"]),
                       "p": {a: {k: int(v) for k, v in s.items() if v} for a, s in X["clutch"]["p"].items()}},
            "poss": X["poss"], "trans": X["trans"], "sc": X["sc"], "pts": X["pts"],
            "fg": [[s, a, z, v, n] for (s, a, z, v), n in sorted(X["fg"].items())],
        }
    return out, max(pers)


def apply(events, games, players, off, pairs, index, prev=None):
    """Replace each team's ESPN-based events (events: {espn id: doc}, edited in place) with the NBL's wherever its
    lineups reproduce the official box score and its points and made field goals equal this site's box score.
    -> report lines"""
    espn_of = {v["pid"]: k for k, v in pairs.items() if v.get("pid")}
    key = lambda pid: (espn_of.get(pid) or f"nbl-{pid}") if pid else "0"
    P = {p["espnId"]: p for p in players}
    pbp = off.get("pbp") or {}
    rep, n = [], collections.Counter()
    for g in games:
        eid = g["id"].replace("espn-", "")
        teams = (g["home"]["team"], g["away"]["team"])
        tag = f"{g['date']} {g['away']['team']} at {g['home']['team']}"
        doc = events.get(eid) or {"espnId": eid, "date": g["date"], "home": teams[0], "away": teams[1], "periods": 4,
                                  "names": {}, "teams": {}, "source": f"https://www.espn.com/nbl/game/_/gameId/{eid}"}
        src = doc.setdefault("src", {})
        for t in teams:
            if t in doc["teams"]:
                src[t] = "espn"
        m = pbp.get(index.get((g["date"], frozenset(teams))))
        if m is None or not m.get("plays"):
            pv = (prev or {}).get(eid) or {}
            for t in teams:
                if (pv.get("src") or {}).get(t) == "nbl":
                    doc["teams"][t] = pv["teams"][t]
                    src[t] = "nbl"
                    doc["names"].update({k: v for k, v in (pv.get("names") or {}).items() if k not in doc["names"]})
            if m is not None:
                rep.append(f"{tag}: the NBL's play-by-play came back empty this run - last run's events kept")
            n["no NBL play-by-play"] += 1
            if doc["teams"]:
                events[eid] = doc
            continue
        S, probs, nper = NL.stints(m)
        why = NL.check(m, S, probs, nper, {g["home"]["team"]: g["home"], g["away"]["team"]: g["away"]})
        pv = (prev or {}).get(eid) or {}
        built, last_per = build(m, key)
        box = (P.get(eid) or {}).get("teams", {})
        names = {p["pid"]: p["name"] for p in m["players"]}
        used = False
        for t in teams:
            b = g["home"] if g["home"]["team"] == t else g["away"]
            X = built[t]
            fgm = sum(r[4] for r in X["fg"])
            bad = list(why.get(t) or [])
            if X["pts"] != b["pts"]:
                bad.append(f"points {X['pts']}, box {b['pts']}")
            if fgm != b["fgm"]:
                bad.append(f"made field goals {fgm}, box {b['fgm']}")
            px = (pv.get("teams") or {}).get(t)
            if bad and (pv.get("src") or {}).get(t) == "nbl" and px and px.get("ok") and px.get("pts") == b["pts"] \
                    and sum(r[4] for r in px.get("fg") or []) == b["fgm"]:
                # this run's NBL data doesn't add up but last run's did: keep last run's
                doc["teams"][t] = px
                src[t] = "nbl"
                doc["names"].update({k: v for k, v in (pv.get("names") or {}).items() if k not in doc["names"]})
                doc["periods"] = max(doc.get("periods") or 4, pv.get("periods") or 4)
                n["nbl (last run's)"] += 1
                rep.append(f"{tag} {t}: this run's NBL play-by-play doesn't add up ({'; '.join(bad[:2])}) - last run's NBL events kept")
                continue
            if bad:
                n["espn" if t in doc["teams"] else "nbl, not adding up"] += 1
                rep.append(f"{tag} {t}: NBL events not used ({'; '.join(bad[:3])})" + ("" if t in doc["teams"] else " - none"))
                if t in doc["teams"]:
                    continue
            X["ok"] = not bad
            doc["teams"][t] = X
            src[t] = "nbl"
            used = True
            n["nbl"] += 0 if bad else 1
            for pid, nm in names.items():
                k = key(pid)
                if k not in doc["names"]:
                    doc["names"][k] = next((x["name"] for x in box.get(t, []) if x["id"] == k), None) or nm
        if used:
            doc["periods"] = max(doc.get("periods") or 4, last_per)
            if all(src.get(t) == "nbl" for t in teams):
                doc["source"] = SRC
        events[eid] = doc
    rep.insert(0, "events by team-game: " + ", ".join(f"{k} {v}" for k, v in sorted(n.items())))
    return rep, dict(n)


def keep_previous(events, prev):
    """The official data couldn't be read this run: keep last run's NBL-based events team by team."""
    for eid, pv in (prev or {}).items():
        doc = events.get(eid)
        if doc is None:
            continue
        for t, s in (pv.get("src") or {}).items():
            if s == "nbl" and t in (pv.get("teams") or {}):
                doc["teams"][t] = pv["teams"][t]
                doc.setdefault("src", {})[t] = "nbl"
                doc.setdefault("names", {}).update({k: v for k, v in (pv.get("names") or {}).items() if k not in doc["names"]})
