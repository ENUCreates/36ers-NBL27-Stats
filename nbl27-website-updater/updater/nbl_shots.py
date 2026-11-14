"""NBL27 shots from the NBL's own play-by-play: every field-goal attempt with its court position, the shooter and
whether it was a two or a three, straight from the league's live stats feed (official.py reads it from the NBL's API,
prod.rosetta.nbl.com.au, match/<id>/live/all, with the official box scores). This feed reproduces the official box
score shot for shot, so the shot charts (pshots.json) and the shot-zone totals behind the shot-quality numbers
(shots.json) are built from it; ESPN's play-by-play (pshots.py, shotlib.py) stays only for a team-game whose NBL
data doesn't add up.

Position frame: the one every zone and the page's court already use (ESPN's): x 0-50 ft across the court with the
basket at x = 25, y = feet from the basket toward halfway. The NBL gives each shot as a percentage of the court's
length (x%) and width (y%). ESPN's positions are the same points on a 94 x 50 ft frame: matched shot by shot on
7,400 shots that both feeds list (52 games, 4 Nov 2026), y = 0.9375 * (x% from the baseline the team attacks) - 4.90
and x = 50 - y%/2 at the x% = 0 end (y%/2 at the other end), median difference 0.24 ft, which is ESPN's own rounding.
The end a team attacks comes from where most of its shots are in that half, so a heave from the far side of
halfway stays the long shot it was.

Zones as in shotlib.classify, with the official two/three call instead of the words in ESPN's play text: a three is
a corner three when it's within 4.64 ft of the basket's line along the court (the straight part of the arc), above
the break otherwise; a two is in the restricted area within 4.10 ft of the basket, in the paint inside the key
(8.04 ft either side, 13.86 ft out), mid-range otherwise.

A team-game's NBL shots are used when they reproduce this site's box score (FGA within 2, FGM within 1 of the team's
totals: every NBL27 team-game checked so far matches exactly or misses one attempt); otherwise ESPN's are kept if
those pass the same check. Each document says where each team's shots come from: 'src' {team: 'nbl' | 'espn'}.
"""
import collections, math

BX, BY = 25.0, 0.0
RA, KEY_HALF, KEY_DEPTH, C3_DEPTH = 4.10, 8.04, 13.86, 4.64
ZC = {"ra": 0, "paint": 1, "mid": 2, "c3": 3, "ab3": 4, "unk": 5}
ALONG, BASE = 0.9375, 4.90        # ESPN feet per percent of the court's length; basket offset from the baseline
ACROSS = 0.5                      # ESPN feet per percent of the court's width
SRC = "https://nbl.com.au/ (the NBL's play-by-play)"


def per_no(a):
    p = int(a["per"])
    return 4 + p if a.get("pt") == "overtime" else p


def half(a):
    return 1 if per_no(a) <= 2 else 2


def ends(m):
    """{(team, half): 'lo' | 'hi'} - the end of the court each team shoots at, from where most of its shots are"""
    c = collections.defaultdict(collections.Counter)
    for a in m["plays"]:
        if (a.get("type") or "").lower() in ("2pt", "3pt") and a.get("x") is not None:
            c[(a.get("team"), half(a))]["lo" if a["x"] < 50 else "hi"] += 1
    out = {k: v.most_common(1)[0][0] for k, v in c.items()}
    # a half with no shots for one team: the other end from its other half
    for t in {k[0] for k in out}:
        for h in (1, 2):
            if (t, h) not in out and (t, 3 - h) in out:
                out[(t, h)] = "hi" if out[(t, 3 - h)] == "lo" else "lo"
    return out


def frame(x, y, end):
    """NBL percentages -> ESPN feet (x across, basket at 25; y from the basket toward halfway)"""
    if x is None or y is None:
        return None, None
    if end == "lo":
        return round(50 - ACROSS * y, 1), round(ALONG * x - BASE, 1)
    return round(ACROSS * y, 1), round(ALONG * (100 - x) - BASE, 1)


def zone(three, X, Y):
    if X is None or Y is None:
        return "unk"
    if three:
        return "c3" if Y - BY <= C3_DEPTH else "ab3"
    if math.hypot(X - BX, Y - BY) <= RA:
        return "ra"
    if abs(X - BX) <= KEY_HALF and Y - BY <= KEY_DEPTH:
        return "paint"
    return "mid"


def voided(plays, types=("2pt", "3pt")):
    """indices of made shots (or, with types=("freethrow",), made free throws) whose rise in the feed's running score
    is undone at the next change of score - the scorers taking a basket back (waived after a review, or entered in
    error). Only a hint: the running score on edited rows isn't always reliable, so clean() and ft_drop() use it just
    to pick which one to drop when the official box score shows a player (or the team) with one too many."""
    out, prev = set(), None
    for i, a in enumerate(plays):
        cur = (a.get("s1"), a.get("s2"))
        if None in cur:
            continue
        if prev is not None and a.get("ok") and (a.get("type") or "").lower() in types:
            d1, d2 = cur[0] - prev[0], cur[1] - prev[1]
            if d1 > 0 or d2 > 0:
                for b in plays[i + 1:]:
                    nxt = (b.get("s1"), b.get("s2"))
                    if None in nxt or nxt == cur:
                        continue
                    if (d1 > 0 and nxt[0] < cur[0]) or (d2 > 0 and nxt[1] < cur[1]):
                        out.add(i)
                    break
        prev = cur
    return out


def _cat(a):
    return ((a.get("type") or "").lower() == "3pt", bool(a.get("ok")))


def _target(x):
    """official line -> {(three, made): count}"""
    return {(True, True): x["tpm"], (True, False): x["tpa"] - x["tpm"], (False, True): x["fgm"] - x["tpm"],
            (False, False): (x["fga"] - x["tpa"]) - (x["fgm"] - x["tpm"])}


def clean(m):
    """-> (indices of the shots to keep, notes). A shot the official box score doesn't count is dropped: where a player
    (or, for shots with no shooter recorded, the team) has more made or missed twos or threes in the feed than in
    the official box score, the extra ones go - the ones the running score marks as taken back, or all of them when
    that's exactly how many are extra. Anything the box score can't settle is kept as it is."""
    plays = m["plays"]
    flagged = voided(plays)
    shots = [i for i, a in enumerate(plays) if (a.get("type") or "").lower() in ("2pt", "3pt")]
    by = collections.defaultdict(lambda: collections.defaultdict(list))
    for i in shots:
        by[(plays[i].get("team"), plays[i].get("pid"))][_cat(plays[i])].append(i)
    offp = {p["pid"]: p for p in m["players"] if p.get("fga") is not None}
    drop, notes = set(), []

    def trim(idx, extra, who):
        fl = [i for i in idx if i in flagged]
        pick = fl[:extra] if len(fl) >= extra else (list(idx) if len(idx) == extra else [])
        drop.update(pick)
        if pick:
            notes.append(f"{who}: {len(pick)} shot(s) the official box score doesn't count left out")
    for (team, pid), cats in by.items():
        p = offp.get(pid) if pid else None
        if p is None:
            continue
        tg = _target(p)
        for c, idx in cats.items():
            if len(idx) > tg[c]:
                trim(idx, len(idx) - tg[c], p.get("name") or pid)
    # shots with no shooter: kept when the team's official totals need them, dropped when they're extra
    for x in m["teams"]:
        team, cats = x["team"], by.get((x["team"], None))
        if not cats or x.get("fga") is None:
            continue
        tg = _target(x)
        for c, idx in cats.items():
            have = sum(1 for (t, _), cs in by.items() if t == team for i in cs.get(c, []) if i not in drop)
            if have > tg[c]:
                trim(idx, min(len(idx), have - tg[c]), f"{team} (no shooter recorded)")
    return [i for i in shots if i not in drop], notes


def ft_drop(m):
    """-> indices (in m['plays']) of the free throws the official box score doesn't count, as clean() does for shots:
    where a player has more made or missed free throws in the feed than in the official box score, the extra ones go -
    the made ones the running score marks as taken back, or all of them when that's exactly how many are extra.
    (13 Nov 2026, Brisbane at New Zealand: a free throw entered with 11 seconds left in the second quarter and taken
    straight back.) Anything the box score can't settle is kept as it is."""
    plays = m["plays"]
    flagged = voided(plays, ("freethrow",))
    by = collections.defaultdict(lambda: collections.defaultdict(list))
    for i, a in enumerate(plays):
        if (a.get("type") or "").lower() == "freethrow" and a.get("pid"):
            by[a["pid"]][bool(a.get("ok"))].append(i)
    offp = {p["pid"]: p for p in m["players"] if p.get("fta") is not None and p.get("ftm") is not None}
    drop = set()
    for pid, cats in by.items():
        p = offp.get(pid)
        if p is None:
            continue
        tg = {True: p["ftm"], False: p["fta"] - p["ftm"]}
        for made, idx in cats.items():
            extra = len(idx) - tg[made]
            if extra > 0:
                fl = [i for i in idx if i in flagged]
                drop.update(fl[:extra] if len(fl) >= extra else (idx if len(idx) == extra else []))
    return drop


def plays_of(m):
    """the match's plays that have a game clock, less the free throws the official box score doesn't count
    (ft_drop) - the list every step that reads the NBL's play-by-play works from"""
    drop = ft_drop(m)
    return [a for i, a in enumerate(m["plays"]) if a.get("clock") and i not in drop]


def shots_of(m):
    """-> [(team, nbl player id, X, Y, made, zone, three)] in game order, {team: [ftm, fta]}, notes"""
    E = ends(m)
    keep, notes = clean(m)
    out, ft = [], collections.defaultdict(lambda: [0, 0])
    for i in keep:
        a = m["plays"][i]
        three = (a.get("type") or "").lower() == "3pt"
        X, Y = frame(a.get("x"), a.get("y"), E.get((a.get("team"), half(a)), "lo"))
        out.append((a.get("team"), a.get("pid"), X, Y, bool(a.get("ok")), zone(three, X, Y), three))
    dft = ft_drop(m)
    for i, a in enumerate(m["plays"]):
        if (a.get("type") or "").lower() == "freethrow" and i not in dft:
            ft[a.get("team")][1] += 1
            ft[a.get("team")][0] += bool(a.get("ok"))
    if dft:
        notes.append(f"{len(dft)} free throw(s) the official box score doesn't count left out")
    return out, ft, notes


def tally(sh, team):
    z = {k: {"m": 0, "a": 0} for k in ("ra", "paint", "mid", "c3", "ab3", "ft", "unk")}
    for t, pid, X, Y, made, zn, three in sh:
        if t == team:
            z[zn]["a"] += 1
            z[zn]["m"] += int(made)
    return z


def apply(pshots, shots, games, players, off, pairs, index, prev_pshots=None, prev_shots=None):
    """Replace each team's ESPN-based shots in pshots ({espn id: doc}) and shots ({espn id: doc}) - both edited in
    place - with the NBL's wherever those reproduce this site's box score. games / players: this run's lists;
    off: official.load(); pairs: official.compare()['pairs']; index: official.nbl27_index(off); prev_*: last run's
    documents, reused for a team-game whose NBL play-by-play can't be read this run. -> report lines."""
    espn_of = {v["pid"]: k for k, v in pairs.items() if v.get("pid")}
    P = {p["espnId"]: p for p in players}
    pbp = off.get("pbp") or {}
    rep, n = [], collections.Counter()
    for g in games:
        eid = g["id"].replace("espn-", "")
        teams = (g["home"]["team"], g["away"]["team"])
        tag = f"{g['date']} {g['away']['team']} at {g['home']['team']}"
        pd = pshots.get(eid) or {"espnId": eid, "date": g["date"], "teams": {}}
        sd = shots.get(eid) or {"espnId": eid, "date": g["date"], "home": teams[0], "away": teams[1], "teams": {},
                                 "source": f"https://www.espn.com/nbl/game/_/gameId/{eid}"}
        psrc, ssrc = pd.setdefault("src", {}), sd.setdefault("src", {})
        for t in teams:
            if t in pd["teams"]:
                psrc[t] = "espn"
            if t in sd["teams"]:
                ssrc[t] = "espn"
        m = pbp.get(index.get((g["date"], frozenset(teams))))
        if m is None or not m.get("plays"):
            # no NBL play-by-play this run: keep last run's NBL-based shots, if any
            for prev, doc, src in ((prev_pshots, pd, psrc), (prev_shots, sd, ssrc)):
                pv = (prev or {}).get(eid) or {}
                for t in teams:
                    if (pv.get("src") or {}).get(t) == "nbl":
                        doc["teams"][t] = pv["teams"][t]
                        src[t] = "nbl"
            if m is not None:
                rep.append(f"{tag}: the NBL's play-by-play came back empty this run - last run's shots kept")
            n["no NBL play-by-play"] += 1
            pshots[eid], shots[eid] = pd, sd
            continue
        sh, ft, cnotes = shots_of(m)
        rep += [f"{tag}: {x}" for x in cnotes]
        names = {p["pid"]: p["name"] for p in m["players"]}
        box = (P.get(eid) or {}).get("teams", {})
        for t in teams:
            b = g["home"] if g["home"]["team"] == t else g["away"]
            mine = [s for s in sh if s[0] == t]
            fga, fgm = len(mine), sum(s[4] for s in mine)
            tpa, tpm = sum(s[6] for s in mine), sum(s[4] and s[6] for s in mine)
            ok = abs(fga - b["fga"]) <= 2 and abs(fgm - b["fgm"]) <= 1
            exact = (fga, fgm, tpa, tpm, ft[t][1], ft[t][0]) == (b["fga"], b["fgm"], b["tpa"], b["tpm"], b["fta"], b["ftm"])
            # player by player against this site's box score (ESPN ids)
            per = collections.defaultdict(lambda: [0, 0, 0, 0])
            key = lambda pid: (espn_of.get(pid) or f"nbl-{pid}") if pid else "0"
            for s in mine:
                r = per[key(s[1])]
                r[0] += s[4]; r[1] += 1; r[2] += s[4] and s[6]; r[3] += s[6]
            off_players = [f"{x['name']} {per.get(x['id'], [0] * 4)[:2]} vs box {[x['fgm'], x['fga']]}"
                           for x in box.get(t, []) if per.get(x["id"], [0] * 4) != [x["fgm"], x["fga"], x["tpm"], x["tpa"]]]
            off_players += [f"{names.get(k[4:], k)} (not paired with ESPN's box score) {v[:2]}" for k, v in per.items()
                            if k.startswith("nbl-") and v[1]]
            off_players += [f"no shooter recorded {v[:2]}" for k, v in per.items() if k == "0" and v[1]]
            espn_ok = (pd["teams"].get(t) or {}).get("ok") is not False and t in pd["teams"]
            pp, ps_ = ((prev_pshots or {}).get(eid) or {}), ((prev_shots or {}).get(eid) or {})
            if not ok and (pp.get("src") or {}).get(t) == "nbl" and (ps_.get("src") or {}).get(t) == "nbl":
                # this run's NBL data doesn't add up but last run's did: keep last run's if it still matches the box
                pz = ps_["teams"].get(t) or {}
                fz = ("ra", "paint", "mid", "c3", "ab3", "unk")
                if sum(pz.get(k, {}).get("a", 0) for k in fz) == b["fga"] and sum(pz.get(k, {}).get("m", 0) for k in fz) == b["fgm"]:
                    pd["teams"][t], sd["teams"][t] = pp["teams"][t], pz
                    psrc[t], ssrc[t] = "nbl", "nbl"
                    n["nbl (last run's)"] += 1
                    rep.append(f"{tag} {t}: this run's NBL shots don't add up (FGM-FGA {fgm}-{fga}) - last run's kept")
                    continue
            if not ok and espn_ok:
                n["espn"] += 1
                rep.append(f"{tag} {t}: NBL shots not used (FGM-FGA {fgm}-{fga}, box {b['fgm']}-{b['fga']}) - ESPN's kept")
                continue
            players_ = {}
            for s in mine:
                k = key(s[1])
                nm = next((x["name"] for x in box.get(t, []) if x["id"] == k), None) or names.get(s[1]) or "Unknown"
                players_.setdefault(k, {"n": nm, "s": []})["s"].append([s[2], s[3], int(s[4]), ZC[s[5]]])
            pd["teams"][t] = {"ok": ok, "p": players_}
            psrc[t] = "nbl"
            z = tally(sh, t)
            z["ft"] = {"m": ft[t][0], "a": ft[t][1]}
            sd["teams"][t] = z
            ssrc[t] = "nbl"
            n["nbl" if ok else "nbl, not adding up"] += 1
            if not exact or off_players:
                rep.append(f"{tag} {t}: NBL shots FGM-FGA {fgm}-{fga} 3P {tpm}-{tpa} FT {ft[t][0]}-{ft[t][1]}, box "
                           f"{b['fgm']}-{b['fga']} 3P {b['tpm']}-{b['tpa']} FT {b['ftm']}-{b['fta']}"
                           + (f"; players: {'; '.join(off_players[:4])}" if off_players else "")
                           + ("" if ok else " - left out"))
        # shots.json's mismatch flag: the zone totals don't reproduce the box score exactly
        probs = []
        for t in teams:
            b = g["home"] if g["home"]["team"] == t else g["away"]
            z = sd["teams"].get(t) or {}
            fz = [k for k in ("ra", "paint", "mid", "c3", "ab3", "unk")]
            m_, a_ = sum(z.get(k, {}).get("m", 0) for k in fz), sum(z.get(k, {}).get("a", 0) for k in fz)
            m3, a3 = sum(z.get(k, {}).get("m", 0) for k in ("c3", "ab3")), sum(z.get(k, {}).get("a", 0) for k in ("c3", "ab3"))
            if (m_, a_) != (b["fgm"], b["fga"]):
                probs.append(f"{t} FG {m_}-{a_} vs box {b['fgm']}-{b['fga']}")
            if (m3, a3) != (b["tpm"], b["tpa"]):
                probs.append(f"{t} 3P {m3}-{a3} vs box {b['tpm']}-{b['tpa']}")
        if probs:
            sd["mismatch"] = "; ".join(probs)
        else:
            sd.pop("mismatch", None)
        if ssrc and all(v == "nbl" for v in ssrc.values()):
            sd["source"] = SRC
        pshots[eid], shots[eid] = pd, sd
    rep.insert(0, "shots by team-game: " + ", ".join(f"{k} {v}" for k, v in sorted(n.items())))
    return rep, dict(n)


def keep_previous(pshots, shots, prev_pshots, prev_shots):
    """The official data couldn't be read this run: keep last run's NBL-based shots team by team."""
    for cur, prev in ((pshots, prev_pshots), (shots, prev_shots)):
        for eid, pv in (prev or {}).items():
            doc = cur.get(eid)
            if doc is None:
                continue
            for t, s in (pv.get("src") or {}).items():
                if s == "nbl" and t in (pv.get("teams") or {}):
                    doc["teams"][t] = pv["teams"][t]
                    doc.setdefault("src", {})[t] = "nbl"
