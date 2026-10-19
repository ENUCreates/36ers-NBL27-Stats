#!/usr/bin/env python3
"""NBL27 lineup stint reconstruction from ESPN play-by-play (site.api.espn.com only).
Usage: python3 -I lineups.py <db_dir> <espn_dir> <out_dir> [ids...]
 db_dir/players/<id>.json : players docs (box scores) ; db_dir/shots/<id>.json : event list
 espn_dir/<id>.json        : cached summary (fetched if missing)
Writes out_dir/<id>.json lineup docs and prints validation summary.
"""
import json, os, sys, re, glob, urllib.request, datetime, collections
from zoneinfo import ZoneInfo

API = "https://site.api.espn.com/apis/site/v2/sports/basketball/nbl/summary?event={}"
CLUBS = ["Adelaide 36ers", "Brisbane Bullets", "Cairns Taipans", "Illawarra Hawks", "Melbourne United",
         "New Zealand Breakers", "Perth Wildcats", "South East Melbourne Phoenix", "Sydney Kings",
         "Tasmania JackJumpers"]
KEYS = [("36ers", "Adelaide 36ers"), ("adelaide", "Adelaide 36ers"), ("brisbane", "Brisbane Bullets"),
        ("cairns", "Cairns Taipans"), ("illawarra", "Illawarra Hawks"), ("hawks", "Illawarra Hawks"),
        ("phoenix", "South East Melbourne Phoenix"), ("south east", "South East Melbourne Phoenix"),
        ("melbourne united", "Melbourne United"), ("united", "Melbourne United"),
        ("breakers", "New Zealand Breakers"), ("new zealand", "New Zealand Breakers"),
        ("perth", "Perth Wildcats"), ("wildcats", "Perth Wildcats"), ("sydney", "Sydney Kings"),
        ("kings", "Sydney Kings"), ("tasmania", "Tasmania JackJumpers"), ("jackjumpers", "Tasmania JackJumpers")]
TOV_TYPES = {"turnover", "bad pass", "lost ball", "traveling", "offensive foul", "out of bounds bad pass turnover",
             "shot clock turnover", "5 sec inbound turnover", "8 second turnover", "3 second turnover",
             "double dribble turnover", "offensive goaltending turnover"}
STATS = ["pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "oreb", "dreb", "tov"]


def club(name):
    n = name.lower()
    for k, v in KEYS:
        if k in n:
            return v
    return None


def secs(clock):
    m, s = clock.split(":") if ":" in clock else ("0", clock)
    return int(round(int(m) * 60 + float(s)))


def plen(p):
    return 600 if p <= 4 else 300


def is_tov(t):
    t = t.strip().lower()
    return t in TOV_TYPES or t.endswith("turnover")


def fetch(eid, espn_dir):
    fp = os.path.join(espn_dir, f"{eid}.json")
    if not os.path.exists(fp):
        with urllib.request.urlopen(API.format(eid), timeout=60) as r:
            open(fp, "wb").write(r.read())
    return json.load(open(fp))


def sub_kind(pl):
    """returns (entering, leaving) lists"""
    parts = [x["athlete"]["id"] for x in pl.get("participants", []) if x.get("athlete")]
    txt = pl.get("text", "").lower()
    if "substitution out" in txt:
        return [], parts[:1]
    return parts[:1], parts[1:2]


def order_plays(d):
    """ESPN's array order is chronological (clock-monotonic); sequenceNumber is not always.
    Stable sort by (period, clock desc) keeps array order within the same clock."""
    pl = list(d.get("plays", []))
    idx = {id(x): i for i, x in enumerate(pl)}
    return sorted(pl, key=lambda x: (x["period"]["number"], -secs(x["clock"]["displayValue"]), idx[id(x)]))


def process(eid, d, pdoc, swap=False, tov_rule=None):
    tov_rule = tov_rule or (lambda pl: is_tov(pl["type"]["text"]) and pl["type"]["text"] != "Offensive Foul")
    tid2club = {t["team"]["id"]: club(t["team"]["displayName"]) for t in d["boxscore"]["teams"]}
    comp = d["header"]["competitions"][0]
    final = {tid2club[c["team"]["id"]]: int(c["score"]) for c in comp["competitors"]}
    home = next(tid2club[c["team"]["id"]] for c in comp["competitors"] if c["homeAway"] == "home")
    away = next(tid2club[c["team"]["id"]] for c in comp["competitors"] if c["homeAway"] == "away")
    clubs = [home, away]
    opp = {home: away, away: home}
    names, pteam = {}, {}
    for team, pls in pdoc["teams"].items():
        for p in pls:
            names[p["id"]] = p["name"]; pteam[p["id"]] = team
    plays = order_plays(d)

    def evs(pl):
        """list of (athlete, kind) kind in in/out/act"""
        parts = [x["athlete"]["id"] for x in pl.get("participants", []) if x.get("athlete", {}).get("id")]
        if pl["type"]["text"] == "Substitution":
            e, l = sub_kind(pl)
            if swap: e, l = l, e
            return [(a, "in") for a in e] + [(a, "out") for a in l]
        return [(a, "act") for a in parts]
    for pl in plays:
        tm = tid2club.get((pl.get("team") or {}).get("id"))
        for a, k in evs(pl):
            if a not in pteam and tm:
                pteam[a] = tm
    periods = sorted({pl["period"]["number"] for pl in plays})
    issues = []
    fixes = collections.Counter()
    stints = []
    prev_end = {c: {p["id"] for p in pdoc["teams"].get(c, []) if p.get("starter")} for c in clubs}
    newacc = lambda: {k: 0 for k in STATS}
    ps_h = ps_a = 0
    boxsec = {p["id"]: 60 * (p.get("min") or 0) for pls in pdoc["teams"].values() for p in pls}
    acc = collections.Counter()
    budget = lambda a: boxsec.get(a, 0) - acc[a]
    for per in periods:
        pp = [pl for pl in plays if pl["period"]["number"] == per]
        E = [evs(pl) for pl in pp]
        # per player list of (index, kind) in this period
        tl = collections.defaultdict(list)
        for i, ev in enumerate(E):
            for a, k in ev:
                tl[a].append((i, k))

        def nxt(a, i):
            for j, k in tl.get(a, []):
                if j >= i:
                    return j, k
            return None, None
        first = {a: v[0][1] for a, v in tl.items()}
        on = {}
        for c in clubs:
            st = {a for a, k in first.items() if k != "in" and pteam.get(a) == c}
            if len(st) < 5:
                for a in sorted(prev_end[c], key=lambda a: (-budget(a), a)):  # player id breaks ties so runs are repeatable
                    if len(st) >= 5: break
                    if first.get(a) != "in": st.add(a)
            if len(st) != 5:
                issues.append(f"P{per} {c} starters={len(st)}")
            on[c] = set(st)

        def normalize(c, i, actors):
            for a in actors:
                if a not in on[c]:
                    on[c].add(a); fixes["add_actor"] += 1
            while len(on[c]) > 5:
                def score(a):
                    j, k = nxt(a, i)
                    b = -budget(a)
                    if j is None: return (1, b, 0)
                    if k == "in": return (2, b, -j)
                    return (0, j, b)
                cand = sorted((a for a in on[c] if a not in actors), key=lambda a: (score(a), a), reverse=True)  # deterministic ties
                if not cand: break
                on[c].discard(cand[0]); fixes["drop_extra"] += 1
            while len(on[c]) < 5:
                cand = []
                for a in set(tl) | prev_end[c]:
                    if pteam.get(a) != c or a in on[c]: continue
                    j, k = nxt(a, i)
                    if j is not None and k in ("act", "out"):
                        cand.append((0, j, a))
                    elif j is None:
                        cand.append((1 if a in prev_end[c] else 2, -budget(a), a))
                if not cand: break
                on[c].add(min(cand)[2]); fixes["add_missing"] += 1
        L = plen(per)
        last_t = 0
        cur = {c: [None, 0, newacc(), newacc()] for c in clubs}

        def restart(c):
            s = cur[c]
            if s[0] is not None and (s[1] or any(s[2].values()) or any(s[3].values())):
                stints.append((c, s[0], s[1], s[2], s[3]))
            cur[c] = [frozenset(on[c]), 0, newacc(), newacc()]
        dirty = True
        for i, pl in enumerate(pp):
            tnow = L - secs(pl["clock"]["displayValue"])
            t = pl["type"]["text"]
            tm = tid2club.get((pl.get("team") or {}).get("id"))
            if t != "Substitution" or dirty and cur[clubs[0]][0] is None:
                pass
            if t != "Substitution":
                for c in clubs:
                    actors = [a for a, k in E[i] if pteam.get(a) == c]
                    normalize(c, i, actors)
                    if len(on[c]) != 5:
                        issues.append(f"P{per} {pl['clock']['displayValue']} {c} on={len(on[c])}")
                    if cur[c][0] != frozenset(on[c]):
                        # time up to now belongs to the old stint
                        pass
            # time accrual to current stints
            dt = tnow - last_t
            if dt > 0:
                for c in clubs:
                    if cur[c][0] is None:
                        cur[c] = [frozenset(on[c]), 0, newacc(), newacc()]
                    cur[c][1] += dt
                    for a in cur[c][0]: acc[a] += dt
            last_t = max(last_t, tnow)
            if t != "Substitution":
                for c in clubs:
                    if cur[c][0] != frozenset(on[c]):
                        restart(c)
            # points from score deltas (credited before applying a substitution)
            h = int(pl.get("homeScore") or 0); a_ = int(pl.get("awayScore") or 0)
            dh, da = h - ps_h, a_ - ps_a
            ps_h, ps_a = h, a_
            for c, dv in ((home, dh), (away, da)):
                if dv:
                    if cur[c][0] is None: restart(c)
                    cur[c][2]["pts"] += dv; cur[opp[c]][3]["pts"] += dv if cur[opp[c]][0] is not None else 0
            if t == "Substitution":
                for a, k in E[i]:
                    c = pteam.get(a, tm)
                    if c not in on: continue
                    if k == "out":
                        if a in on[c]: on[c].discard(a)
                        else: fixes["leave_not_on"] += 1
                    else:
                        on[c].add(a)
                continue
            if not tm:
                continue
            f = cur[tm][2]; ag = cur[opp[tm]][3]
            tl_ = t.lower(); txt = (pl.get("text") or "").lower()
            made = bool(pl.get("scoringPlay")) or int(pl.get("scoreValue") or 0) > 0
            delta = {}
            if tl_.startswith("free throw"):
                delta["fta"] = 1
                if made: delta["ftm"] = 1
            elif pl.get("shootingPlay"):
                three = "three point" in txt
                delta["fga"] = 1
                if three: delta["tpa"] = 1
                if made:
                    delta["fgm"] = 1
                    if three: delta["tpm"] = 1
            elif t == "Offensive Rebound":
                delta["oreb"] = 1
            elif t == "Defensive Rebound":
                delta["dreb"] = 1
            elif tov_rule(pl):
                delta["tov"] = 1
            for k, v in delta.items():
                f[k] += v; ag[k] += v
        dt = L - last_t
        for c in clubs:
            if cur[c][0] is None:
                cur[c] = [frozenset(on[c]), 0, newacc(), newacc()]
            if dt > 0: cur[c][1] += dt
            restart(c)
            prev_end[c] = set(on[c])
    agg = {c: {} for c in clubs}
    for c, ps, sec, f, a in stints:
        key = tuple(sorted(ps))
        g = agg[c].setdefault(key, {"sec": 0, "f": newacc(), "a": newacc()})
        g["sec"] += sec
        for k in STATS:
            g["f"][k] += f[k]; g["a"][k] += a[k]
    teams = {c: [{"p": list(k), "sec": v["sec"], "f": v["f"], "a": v["a"]}
                 for k, v in sorted(agg[c].items(), key=lambda kv: -kv[1]["sec"])] for c in clubs}
    reasons = []
    nper = max(periods) if periods else 4
    expect = 2400 + 300 * max(0, nper - 4)
    stats = {}
    for c in clubs:
        tot = sum(x["sec"] for x in teams[c])
        if abs(tot - expect) > 30: reasons.append(f"{c} sec {tot}!={expect}")
        if any(len(x["p"]) != 5 for x in teams[c]): reasons.append(f"{c} non-5 lineup")
        pts = sum(x["f"]["pts"] for x in teams[c])
        if pts != final[c]: reasons.append(f"{c} pts {pts}!={final[c]}")
        bp = pdoc["teams"].get(c, [])
        for k in ("fga", "fta"):
            s1 = sum(x["f"][k] for x in teams[c]); s2 = sum(p.get(k, 0) for p in bp)
            if abs(s1 - s2) > 2: reasons.append(f"{c} {k} pbp {s1} vs box {s2}")
        psec = collections.Counter()
        for x in teams[c]:
            for p in x["p"]: psec[p] += x["sec"]
        played = [p for p in bp if (p.get("min") or 0) > 0 or psec.get(p["id"])]
        ok = sum(1 for p in played if abs(psec.get(p["id"], 0) - 60 * (p.get("min") or 0)) <= 90)
        stats[c] = (ok, len(played), sum(x["f"]["tov"] for x in teams[c]), sum(p.get("tov", 0) for p in bp))
        if played and ok / len(played) < 0.9: reasons.append(f"{c} minutes within 90s {ok}/{len(played)}")
    used = {p for c in clubs for x in teams[c] for p in x["p"]}
    # Names: box score first, then ESPN's full game roster, and only then the bare ID. A player on court for
    # under 30 seconds is in the play-by-play but has 0 minutes, so he is not in the box score (players doc).
    roster = {a["athlete"]["id"]: a["athlete"].get("displayName")
              for bt in d["boxscore"].get("players", []) for st in bt.get("statistics", [])
              for a in st.get("athletes", []) if a.get("athlete", {}).get("id")}
    nm = {p: names.get(p) or roster.get(p) or p for p in sorted(used)}
    sydney = datetime.datetime.fromisoformat(comp["date"].replace("Z", "+00:00")).astimezone(ZoneInfo("Australia/Sydney"))
    doc = {"espnId": eid, "date": sydney.strftime("%Y-%m-%d"), "home": home, "away": away, "names": nm,
           "teams": teams, "source": f"https://www.espn.com/nbl/game/_/gameId/{eid}"}
    if reasons:
        doc["invalid"] = "; ".join(reasons)[:500]
    ok_season = sydney >= datetime.datetime(2026, 9, 19, tzinfo=ZoneInfo("Australia/Sydney")) and all(c in CLUBS for c in clubs) \
        and comp.get("status", {}).get("type", {}).get("completed", True)
    return doc, reasons, issues, (stats, dict(fixes)), ok_season


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


def main():
    db, espn, out = sys.argv[1:4]
    ids = sys.argv[4:] or sorted(os.path.basename(f)[:-5] for f in glob.glob(os.path.join(db, "shots", "*.json")))
    os.makedirs(out, exist_ok=True); os.makedirs(espn, exist_ok=True)
    swap = None
    nwritten = ninv = 0
    for eid in ids:
        d = fetch(eid, espn)
        pdoc = json.load(open(os.path.join(db, "players", f"{eid}.json")))
        if swap is None:
            g, b = check_sub_order(d, pdoc)
            swap = b > g
            print(f"SUB ORDER CHECK {eid}: participants[0]=entering matches {g}, reversed matches {b} -> swap={swap}")
        doc, reasons, issues, tov, ok = process(eid, d, pdoc, swap)
        if not ok:
            print(eid, "SKIP not NBL27 regular season"); continue
        json.dump(doc, open(os.path.join(out, f"{eid}.json"), "w"))
        nwritten += 1; ninv += bool(doc.get("invalid"))
        print(eid, doc["date"], doc["away"], "@", doc["home"], "OK" if not doc.get("invalid") else "INVALID: " + doc["invalid"], "|", tov, "| unresolved", len(issues))
    print(f"written {nwritten}, invalid {ninv}")


if __name__ == "__main__":
    main()
