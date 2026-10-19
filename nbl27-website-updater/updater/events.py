#!/usr/bin/env python3
"""NBL27 events (clutch / rotations / possessions / assists) from ESPN play-by-play (site.api.espn.com only).
Usage: python3 -I events.py <db_dir> <espn_dir> <out_dir> [ids...]   (same inputs as lineups.py)
Who is on the floor at each play comes from oncourt.py, the same reconstruction lineups.py uses. When
NBL_ONCOURT_CACHE is set (rebuild.py runs lineups.py first), only games already solved there are written.
"""
import json, os, sys, re, glob, urllib.request, datetime, collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import oncourt  # noqa: E402
from oncourt import CLUBS, KEYS, TOV_TYPES, STATS, club, secs, plen, is_tov, sub_kind, order_plays  # noqa: E402,F401

API = "https://site.api.espn.com/apis/site/v2/sports/basketball/nbl/summary?event={}"
HOOK, CTX = [], {}


def fetch(eid, espn_dir):
    fp = os.path.join(espn_dir, f"{eid}.json")
    if not os.path.exists(fp):
        with urllib.request.urlopen(API.format(eid), timeout=60) as r:
            open(fp, "wb").write(r.read())
    return json.load(open(fp))


def check_sub_order(d, pdoc):
    names = {p["id"]: p["name"] for pls in pdoc["teams"].values() for p in pls}
    good = bad = 0
    for pl in d["plays"]:
        if pl["type"]["text"] == "Substitution" and len(pl.get("participants", [])) == 2 and "in" in pl["text"]:
            nm = pl["text"].split(" substitution")[0]
            e, l = [x["athlete"]["id"] for x in pl["participants"]]
            if names.get(e) == nm: good += 1
            elif names.get(l) == nm: bad += 1
    return good, bad



# ======================= NBL27 events layer (clutch, rotations, possessions, assists) =======================
# oncourt.process() logs every play with the five-man units on the floor (HOOK).
import math

BX, BY = 25.0, 0.0
RA, KEY_HALF, KEY_DEPTH, C3_DEPTH = 4.10, 8.04, 13.86, 4.64
ZI = {"ra": 0, "paint": 1, "mid": 2, "c3": 3, "ab3": 4, "unk": 5}
CLUTCH_SEC, CLUTCH_MARGIN, TRANS_SEC = 300, 5, 7
PSTATS = ["sec", "pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "ast", "tov", "stl", "blk", "oreb", "dreb", "pm"]


def zone(p):
    t = (p.get("text") or "").lower()
    c = p.get("coordinate") or {}
    x, y = c.get("x"), c.get("y")
    ok = x is not None and y is not None and -100 < x < 150 and -100 < y < 150
    if ok and y > 47:
        x, y = 50 - x, 94 - y
    if "three point" in t:
        if not ok: return "ab3"
        return "c3" if y - BY <= C3_DEPTH else "ab3"
    if not ok:
        return "ra" if any(k in t for k in ("dunk", "tip", "layup", "lay up", "alley")) else "unk"
    if math.hypot(x - BX, y - BY) <= RA: return "ra"
    if abs(x - BX) <= KEY_HALF and y - BY <= KEY_DEPTH: return "paint"
    return "mid"


def poff(per):
    return 600 * (per - 1) if per <= 4 else 2400 + 300 * (per - 5)


def events_doc(eid, d, pdoc, swap):
    HOOK.clear(); CTX.clear()
    res = oncourt.process(eid, d, pdoc, swap, hook=HOOK.append, ctx=CTX, allow_solve=oncourt.cache_path() is None)
    if res is None:
        return None
    doc, reasons, issues, _, ok = res
    if not ok:
        return None
    pteam, home, away = CTX["pteam"], CTX["home"], CTX["away"]
    clubs = [home, away]; opp = {home: away, away: home}
    T = {c: {"rot": collections.defaultdict(list),
             "clutch": {"sec": 0, "f": collections.Counter(), "a": collections.Counter(), "p": collections.defaultdict(collections.Counter)},
             "poss": {k: [0, 0] for k in ("steal", "dto", "dreb", "make", "other")},
             "trans": [0, 0], "sc": [0, 0], "fg": collections.Counter(), "pts": 0} for c in clubs}
    last_per = max(r[1] for r in HOOK) if HOOK else 4
    # ---- time-based: rotations + clutch seconds ----
    score = {home: 0, away: 0}
    for r in HOOK:
        if r[0] == "s":   # score snapshot after an event
            score[home], score[away] = r[2], r[3]; continue
        if r[0] != "t": continue
        _, per, t0, t1, sets = r
        if t1 <= t0: continue
        L = plen(per); g0, g1 = poff(per) + t0, poff(per) + t1
        for c in clubs:
            for a in (sets.get(c) or ()):
                st = T[c]["rot"][a]
                if st and abs(st[-1][1] - g0) < 1e-6: st[-1][1] = g1
                else: st.append([g0, g1])
        if per >= 4 and abs(score[home] - score[away]) <= CLUTCH_MARGIN:
            c0 = max(t0, L - CLUTCH_SEC) if per == 4 else t0
            if per > 4: c0 = t0
            dt = t1 - c0
            if dt > 0:
                for c in clubs:
                    T[c]["clutch"]["sec"] += dt
                    for a in (sets.get(c) or ()):
                        T[c]["clutch"]["p"][a]["sec"] += dt
    # ---- event-based ----
    ph = pa = 0
    pos = None
    last_term = {c: None for c in clubs}
    cur_per = None
    prev_ft_nonfinal = False
    plays = [r for r in HOOK if r[0] == "e"]
    # phantom scoring rows (score rise reverted at the next change) are skipped for FG logs, as in the shot script
    seq = [(int(r[4].get("homeScore") or 0), int(r[4].get("awayScore") or 0)) for r in plays]

    def voided(i):
        p = plays[i][4]
        if not (int(p.get("scoreValue") or 0) > 0) or i == 0: return False
        dh, da = seq[i][0] - seq[i - 1][0], seq[i][1] - seq[i - 1][1]
        for k in range(i + 1, len(plays)):
            if seq[k] != seq[i]:
                return (dh > 0 and seq[k][0] < seq[i][0]) or (da > 0 and seq[k][1] < seq[i][1])
        return False

    def close(p):
        if not p: return
        X = T[p["team"]]
        X["poss"][p["type"]][0] += 1; X["poss"][p["type"]][1] += p["pts"]
        if p["trans"]: X["trans"][0] += 1; X["trans"][1] += p["tr_pts"]
        if p["oreb"]: X["sc"][0] += 1; X["sc"][1] += p["sc_pts"]

    def start(team, typ, t0, g):
        nonlocal pos
        close(pos)
        pos = {"team": team, "type": typ, "t0": t0, "first": None, "pts": 0, "oreb": False, "sc_pts": 0,
               "tr_pts": 0, "trans": False}

    def act(team, g):
        """offensive action by team at game second g"""
        if pos is None or pos["team"] != team:
            lt = last_term[opp[team]]
            if lt and lt[0] in ("steal", "dto", "make"): start(team, lt[0], lt[1], g)
            else: start(team, "other", lt[1] if lt else g, g)
        if pos["first"] is None:
            pos["first"] = g
            pos["trans"] = pos["type"] in ("steal", "dreb") and g - pos["t0"] <= TRANS_SEC
    for i, r in enumerate(plays):
        _, per, tnow, idx, pl, sets = r
        if per != cur_per:
            close(pos); pos = None; last_term = {c: None for c in clubs}; cur_per = per
        L = plen(per); g = poff(per) + tnow
        t = pl["type"]["text"]; txt = (pl.get("text") or "").lower()
        parts = [x["athlete"]["id"] for x in pl.get("participants", []) if x.get("athlete", {}).get("id")]
        tm = pteam.get(parts[0]) if parts else None
        tm = tm or club_of(pl)
        clutch = per >= 4 and (per > 4 or L - tnow <= CLUTCH_SEC) and abs(T[home]["pts"] - T[away]["pts"]) <= CLUTCH_MARGIN
        is_ft = t.lower().startswith("free throw")
        made = bool(pl.get("scoringPlay")) or int(pl.get("scoreValue") or 0) > 0
        kind = None
        if t == "Substitution": kind = None
        elif is_ft: kind = "ft"
        elif pl.get("shootingPlay"): kind = "fg"
        elif t == "Offensive Rebound": kind = "oreb"
        elif t == "Defensive Rebound": kind = "dreb"
        elif is_tov(t) and t != "Offensive Foul": kind = "tov"
        # possession tracking
        if tm in T and kind:
            if kind == "dreb":
                if pos is None or pos["team"] != tm:
                    start(tm, "dreb", g, g)
            elif kind == "oreb":
                if prev_ft_nonfinal and not parts:
                    pass   # dead-ball team rebound logged between free throws
                else:
                    act(tm, g) if pos is None or pos["team"] != tm else None
                    pos["oreb"] = True
            else:
                act(tm, g)
                if kind == "tov":
                    last_term[tm] = ("steal" if len(parts) > 1 else "dto", g)
                elif kind == "fg":
                    last_term[tm] = ("make", g) if made else ("miss", g)
                elif kind == "ft" and made and re.search(r"(\d) of (\d)", txt) and \
                        re.search(r"(\d) of (\d)", txt).group(1) == re.search(r"(\d) of (\d)", txt).group(2):
                    last_term[tm] = ("make", g)
        # points come from the scoring plays themselves (ESPN's running score has phantom blips)
        pv = 0
        if tm in T and made and kind in ("fg", "ft") and not voided(i):
            pv = 1 if kind == "ft" else (3 if "three point" in txt else 2)
        dh, da = (pv, 0) if tm == home else (0, pv)
        for c, dv in ((home, dh), (away, da)):
            if not dv: continue
            T[c]["pts"] += dv
            if pos and pos["team"] == c:
                pos["pts"] += dv
                if pos["oreb"]: pos["sc_pts"] += dv
                elif pos["trans"]: pos["tr_pts"] += dv
        # clutch box
        if clutch:
            for c, dv in ((home, dh), (away, da)):
                if not dv: continue
                T[c]["clutch"]["f"]["pts"] += dv; T[opp[c]]["clutch"]["a"]["pts"] += dv
                for a in (sets.get(c) or ()): T[c]["clutch"]["p"][a]["pm"] += dv
                for a in (sets.get(opp[c]) or ()): T[opp[c]]["clutch"]["p"][a]["pm"] -= dv
            if tm in T and kind:
                F, A = T[tm]["clutch"]["f"], T[opp[tm]]["clutch"]["a"]
                P = T[tm]["clutch"]["p"]
                sh = parts[0] if parts else None
                delta = collections.Counter()
                if kind == "ft":
                    delta["fta"] += 1; delta["ftm"] += int(made)
                    if sh: P[sh]["fta"] += 1; P[sh]["ftm"] += int(made); P[sh]["pts"] += int(made)
                elif kind == "fg":
                    three = "three point" in txt
                    delta["fga"] += 1; delta["tpa"] += three; delta["fgm"] += made; delta["tpm"] += made and three
                    if sh:
                        P[sh]["fga"] += 1; P[sh]["tpa"] += three; P[sh]["fgm"] += made; P[sh]["tpm"] += made and three
                        P[sh]["pts"] += (3 if three else 2) * made
                        if made and "assist" in txt and len(parts) > 1: P[parts[1]]["ast"] += 1; delta["ast"] += 1
                        if not made and "blocks" in txt and len(parts) > 1:
                            T[opp[tm]]["clutch"]["p"][parts[1]]["blk"] += 1
                elif kind in ("oreb", "dreb"):
                    delta[kind] += 1
                    if sh: P[sh][kind] += 1
                elif kind == "tov":
                    delta["tov"] += 1
                    if sh: P[sh]["tov"] += 1
                    if len(parts) > 1: T[opp[tm]]["clutch"]["p"][parts[1]]["stl"] += 1
                for k, v in delta.items():
                    F[k] += v; A[k] += v
        if kind == "ft":
            mm = re.search(r"(\d) of (\d)", txt)
            prev_ft_nonfinal = bool(mm and mm.group(1) != mm.group(2))
        elif kind:
            prev_ft_nonfinal = False
        # made-FG log for assist network
        if kind == "fg" and made and tm in T and parts and not voided(i):
            z = zone(pl)
            ast = parts[1] if ("assist" in txt and len(parts) > 1) else ""
            T[tm]["fg"][(parts[0], ast, ZI[z], 3 if "three point" in txt else 2)] += 1
    close(pos)
    out = {}
    for c in clubs:
        X = T[c]
        bp = pdoc["teams"].get(c, [])
        bpts = sum(p.get("pts", 0) for p in bp); bfgm = sum(p.get("fgm", 0) for p in bp)
        lfgm = sum(X["fg"].values())
        okt = abs(X["pts"] - bpts) <= 3 and abs(lfgm - bfgm) <= 1
        out[c] = {
            "rot": {a: [[round(s), round(e)] for s, e in v if e - s >= 1] for a, v in X["rot"].items()},
            "clutch": {"sec": round(X["clutch"]["sec"]), "f": dict(X["clutch"]["f"]), "a": dict(X["clutch"]["a"]),
                       "p": {a: {k: int(v) for k, v in s.items() if v} for a, s in X["clutch"]["p"].items()}},
            "poss": X["poss"], "trans": X["trans"], "sc": X["sc"], "pts": X["pts"], "ok": okt,
            "fg": [[s, a, z, v, n] for (s, a, z, v), n in sorted(X["fg"].items())],
        }
    ev = {"espnId": eid, "date": doc["date"], "home": home, "away": away, "periods": last_per,
          "names": {k: v for k, v in {**{k: v for k, v in doc["names"].items() if v and v != k},
                    **{a["athlete"]["id"]: a["athlete"].get("displayName") for bt in d["boxscore"].get("players", []) for st in bt.get("statistics", []) for a in st.get("athletes", []) if a.get("athlete")},
                    **{p["id"]: p["name"] for pls in pdoc["teams"].values() for p in pls}}.items() if v},
          "teams": out, "source": doc["source"]}
    return ev


def club_of(pl):
    return CTX["tid2club"].get((pl.get("team") or {}).get("id"))


def main_events():
    db, espn, out = sys.argv[1:4]
    ids = sys.argv[4:] or sorted(os.path.basename(f)[:-5] for f in glob.glob(os.path.join(db, "players", "*.json")))
    os.makedirs(out, exist_ok=True); os.makedirs(espn, exist_ok=True)
    swap = None; n = 0
    for eid in ids:
        pf = os.path.join(db, "players", f"{eid}.json")
        if not os.path.exists(pf): print(eid, "no players doc"); continue
        d = fetch(eid, espn); pdoc = json.load(open(pf))
        if swap is None:
            g, b = check_sub_order(d, pdoc); swap = b > g
        ev = events_doc(eid, d, pdoc, swap)
        if ev is None: print(eid, "SKIP not NBL27 regular season, or no on-court solution this run"); continue
        json.dump(ev, open(os.path.join(out, f"{eid}.json"), "w"), separators=(",", ":"))
        n += 1
        s = "; ".join(f"{c.split()[-1]} poss {sum(v[0] for v in x['poss'].values())} trans {x['trans']} 2nd {x['sc']} clutch {x['clutch']['sec']}s"
                      for c, x in ev["teams"].items())
        print(eid, ev["date"], s)
    print("written", n)


if __name__ == "__main__":
    main_events()
