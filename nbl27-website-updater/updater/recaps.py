#!/usr/bin/env python3
"""What happened in each played NBL27 game -> the 'recap' of that game in data/matchups_played.json (Projected ladder
tab: click a played game in the round-by-round tips). Run after matchups.py (rebuild.py and injuries.py both do).

    python3 recaps.py <repo_root> [--all]

Every sentence is worked out from this site's own data, nothing is written by hand:
  games.json, players.json   the box scores (team and player), and each team's and player's numbers BEFORE the game
  events.json                transition, second-chance and clutch points (from the play-by-play)
  shots.json                 points in the paint
  ladder.json                the tip (chance and expected margin) and the result
  ESPN's game summary        quarter scores and the running score (site.api.espn.com, the box scores' source; read
                             from NBL_ESPN_CACHE when the daily update has it, else downloaded once)

A recap has the quarter scores and four groups of facts:
  tip    the tip against the result, and why it was right or wrong: the result is replayed with both teams making
         their usual share (their season percentages before the game) of the threes and free throws they took. If
         that flips the winner, the game turned on shooting on the night; if not, on the other edges listed.
  won    the numbers that mattered, biggest first: each team's shooting against its own season marks (in points, on the
         shots it took), turnovers, offensive rebounds and second-chance points, free throws, transition, the paint,
         the benches, and season highs for points scored
  perf   outlier performances against each player's own season average before the game (and season / league highs),
         plus regulars who did not play
  flow   how it unfolded: the deciding quarter, biggest leads, comebacks, runs, lead changes, time in front, clutch

A game is recapped once, the first morning after it (like its tip, a recap is then left as it is, so "season high"
always means up to that game). --all writes every game again; so does a change of VERSION. Comparisons with a team's
or player's own numbers need 3 earlier games; before that those facts are left out. The running-score facts are used
only when ESPN's play-by-play adds up to the final score and to every quarter score; otherwise they are left out.
"""
import collections, datetime, json, os, re, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ladder as L  # noqa: E402
from matchups import SHORT, load, ordn  # noqa: E402

VERSION = 1
API = "https://site.api.espn.com/apis/site/v2/sports/basketball/nbl/summary?event={}"
MIN_GAMES = 3
QN = {1: "first", 2: "second", 3: "third", 4: "fourth"}


def summary(eid):
    d = os.environ.get("NBL_ESPN_CACHE")
    fp = os.path.join(d, f"{eid}.json") if d else None
    if fp and os.path.exists(fp):
        return json.load(open(fp))
    with urllib.request.urlopen(urllib.request.Request(API.format(eid), headers={"User-Agent": "Mozilla/5.0"}), timeout=60) as r:
        j = json.loads(r.read())
    if fp:
        json.dump(j, open(fp, "w"))
    return j


def pc(m, a):
    return f"{100 * m / a:.0f}%" if a else "–"


def num(x):
    """a points figure as words use it: 'about 12'"""
    return f"{abs(x):.0f}"


def clock_secs(s):
    p = [float(x) for x in str(s).split(":")]
    return p[0] * 60 + p[1] if len(p) == 2 else p[0]


def plen(per):
    return 600 if per <= 4 else 300


def poff(per):
    return 600 * (per - 1) if per <= 4 else 2400 + 300 * (per - 5)


def when(g):
    """game seconds -> 'early in the third quarter'"""
    per = 1
    while g >= poff(per) + plen(per) and per < 12:
        per += 1
    left = poff(per) + plen(per) - g
    name = f"the {QN[per]} quarter" if per <= 4 else ("overtime" if per == 5 else f"the {ordn(per - 4)} overtime")
    part = "early in" if left > plen(per) * 2 / 3 else "late in" if left <= plen(per) / 3 else "midway through"
    return f"{part} {name}"


# ---------------------------------------------------------------- the running score (ESPN play-by-play)
def flow(j, hs, as_):
    """-> (line {h: [..], a: [..]} or None, timeline [(game sec, home, away)] or None)"""
    comp = j["header"]["competitions"][0]
    side = {c["homeAway"]: c for c in comp["competitors"]}
    try:
        line = {k[0]: [int(float(x["displayValue"])) for x in side[k].get("linescores", [])] for k in ("home", "away")}
    except Exception:
        line = None
    if not line or not line["h"] or sum(line["h"]) != hs or sum(line["a"]) != as_ or len(line["h"]) != len(line["a"]):
        line = None
    pts = []
    for p in j.get("plays") or []:
        try:
            per = int(p["period"]["number"])
            t = poff(per) + plen(per) - clock_secs(p["clock"]["displayValue"])
            pts.append((t, int(p.get("sequenceNumber") or 0), int(p.get("homeScore") or 0), int(p.get("awayScore") or 0)))
        except Exception:
            continue
    pts.sort()
    tl = [(0, 0, 0)]
    for t, _, h, a in pts:
        # a score that is later lower again was taken back (a basket waved off, or a row out of order): drop what
        # was counted above it. The checks below (final score, every quarter score) decide whether the rest holds.
        while len(tl) > 1 and (h < tl[-1][1] or a < tl[-1][2]):
            tl.pop()
        if (h, a) != (tl[-1][1], tl[-1][2]):
            tl.append((t, h, a))
    cur = (tl[-1][1], tl[-1][2])
    if cur != (hs, as_) or not line:
        return line, None
    for q in range(len(line["h"])):               # every quarter score must come out too
        end = poff(q + 1) + plen(q + 1)
        last = [x for x in tl if x[0] <= end + 1e-6][-1]
        if (last[1], last[2]) != (sum(line["h"][:q + 1]), sum(line["a"][:q + 1])):
            return line, None
    return line, tl


def flow_facts(tl, line, H, A, hs, as_, total):
    W, Lo = (H, A) if hs > as_ else (A, H)
    sgn = 1 if hs > as_ else -1                    # the winner's margin = sgn * (home - away)
    out = []
    nq = len(line["h"])
    if nq > 4:
        out.append((9, f"Went to {'overtime' if nq == 5 else f'{nq - 4} overtimes'}: {sum(line['h'][:4])}–{sum(line['a'][:4])} after 40 minutes."))
    # the quarter that decided it
    qm = [(sgn * (line["h"][q] - line["a"][q]), q) for q in range(min(nq, 4))]
    best = max(qm)
    if best[0] >= 8:
        q = best[1]
        w, l = (line["h"][q], line["a"][q]) if sgn > 0 else (line["a"][q], line["h"][q])
        rest = abs(hs - as_) - best[0]
        tail = (f"the other three quarters were level" if rest == 0 else
                f"{SHORT[Lo]} won the rest of the game by {-rest}" if rest < 0 else f"the rest of the game went {SHORT[W]}'s way by {rest}")
        out.append((8, f"{SHORT[W]} won the {QN[q + 1]} quarter {w}–{l}; {tail}."))
    if not tl:
        return out
    # leads, ties, lead changes, time in front
    lead = lambda x: x[1] - x[2]
    changes = ties = 0
    last_side = 0
    front = {1: 0.0, -1: 0.0}
    big = {1: (0, 0), -1: (0, 0)}                  # side -> (lead, when)
    for i, x in enumerate(tl):
        d = lead(x)
        s = (d > 0) - (d < 0)
        if s == 0 and i:
            ties += 1
        if s and last_side and s != last_side:
            changes += 1
        if s:
            last_side = s
            if abs(d) > big[s][0]:
                big[s] = (abs(d), x[0])
        nxt = tl[i + 1][0] if i + 1 < len(tl) else total
        if s:
            front[s] += max(0, nxt - x[0])
    wl, ll = big[sgn], big[-sgn]
    if ll[0] >= 10:
        out.append((10, f"{SHORT[W]} came from {ll[0]} down ({when(ll[1])}) to win."))
    elif ll[0] == 0:
        out.append((7, f"{SHORT[W]} never trailed."))
    if wl[0] >= 15 and wl[0] - abs(hs - as_) >= 10:
        out.append((6, f"{SHORT[W]} led by as many as {wl[0]} ({when(wl[1])}) before {SHORT[Lo]} cut it to {abs(hs - as_)}."))
    elif wl[0] >= 20:
        out.append((5, f"{SHORT[W]} led by as many as {wl[0]} ({when(wl[1])})."))
    # the biggest unanswered run
    run = {"n": 0}
    cur_s, cur_n, cur_t0 = 0, 0, 0
    for i in range(1, len(tl)):
        dh, da = tl[i][1] - tl[i - 1][1], tl[i][2] - tl[i - 1][2]
        s = 1 if dh > 0 and da == 0 else -1 if da > 0 and dh == 0 else 0
        if s == 0:
            cur_s, cur_n = 0, 0
            continue
        if s != cur_s:
            cur_s, cur_n, cur_t0 = s, 0, tl[i][0]
        cur_n += dh + da
        if cur_n > run["n"]:
            run = {"n": cur_n, "s": s, "t0": cur_t0, "t1": tl[i][0]}
    if run["n"] >= 10:
        t = H if run["s"] > 0 else A
        mins = (run["t1"] - run["t0"]) / 60
        out.append((4 + run["n"] / 10, f"{SHORT[t]} scored {run['n']} in a row {when((run['t0'] + run['t1']) / 2)}"
                    + (f", in {mins:.1f} minutes." if mins >= 0.5 else ".")))
    if changes >= 8:
        out.append((6, f"{changes} lead changes and {ties} ties."))
    elif changes == 0 and ll[0] > 0:
        pass
    share = front[sgn] / total if total else 0
    if 0 < share <= 0.35:
        out.append((6, f"{SHORT[W]} were in front for only {front[sgn] / 60:.0f} of the {total / 60:.0f} minutes."))
    elif share >= 0.85 and ll[0] > 0:
        out.append((3, f"{SHORT[W]} were in front for {front[sgn] / 60:.0f} of the {total / 60:.0f} minutes."))
    # the last lead change
    last = None
    side_now = 0
    for x in tl:
        d = lead(x)
        s = (d > 0) - (d < 0)
        if s and s != side_now:
            if side_now or s == sgn:
                last = x[0]
            side_now = s
    if last is not None and total - last <= 180 and changes:
        left = total - last
        out.append((8, f"The winning lead came with {int(left // 60)}:{int(left % 60):02d} to play."))
    return out


# ---------------------------------------------------------------- numbers before the game
def team_before(games, date):
    """team -> totals of its games before the date"""
    T = collections.defaultdict(collections.Counter)
    hi = collections.defaultdict(lambda: {"pts": 0, "opp": 0})
    for g in games:
        if g["date"] >= date:
            continue
        for me, op in (("home", "away"), ("away", "home")):
            t, o = g[me], g[op]
            c = T[t["team"]]
            c["gp"] += 1
            for k in ("pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "oreb", "dreb", "tov", "ast"):
                c[k] += t[k]
            c["opp"] += o["pts"]
            hi[t["team"]]["pts"] = max(hi[t["team"]]["pts"], t["pts"])
            hi[t["team"]]["opp"] = max(hi[t["team"]]["opp"], o["pts"])
    return T, hi


def players_before(players, date):
    P = collections.defaultdict(lambda: {"gp": 0, "tot": collections.Counter(), "max": collections.Counter(), "team": None, "name": None, "games": []})
    tg = collections.defaultdict(list)
    for d in sorted(players, key=lambda d: d["date"]):
        if d["date"] >= date:
            continue
        for team, rows in d["teams"].items():
            tg[team].append(d["espnId"])
            for r in rows:
                if not r.get("min"):
                    continue
                x = P[r["id"]]
                x["gp"] += 1; x["team"] = team; x["name"] = r["name"]; x["games"].append(d["espnId"])
                for k in ("pts", "min", "reb", "ast", "stl", "blk", "tpm"):
                    v = r.get(k, (r.get("oreb", 0) + r.get("dreb", 0)) if k == "reb" else 0)
                    x["tot"][k] += v
                    x["max"][k] = max(x["max"][k], v)
    return P, tg


# ---------------------------------------------------------------- one game
def recap(x, g, pd, ev, sh, games, players, mp):
    H, A, hs, as_ = x["home"], x["away"], x["hs"], x["as"]
    W, Lo = (H, A) if hs > as_ else (A, H)
    box = {H: g["home"], A: g["away"]}
    date = g["date"]
    TB, HI = team_before(games, date)
    PB, TG = players_before(players, date)
    facts = []
    add = lambda k, w, t: facts.append({"k": k, "w": round(w, 2), "t": t})
    S = lambda t: SHORT.get(t, t)

    # ---- where the game was won (the winner's margin, in points, from each thing)
    usual = {}
    for t in (H, A):
        c = TB.get(t)
        if c and c["gp"] >= MIN_GAMES:
            usual[t] = {"tp": c["tpm"] / c["tpa"] if c["tpa"] else None, "ft": c["ftm"] / c["fta"] if c["fta"] else None,
                        "two": (c["fgm"] - c["tpm"]) / (c["fga"] - c["tpa"]) if c["fga"] > c["tpa"] else None,
                        "tov": c["tov"] / c["gp"], "oreb": c["oreb"] / c["gp"], "pts": c["pts"] / c["gp"], "opp": c["opp"] / c["gp"],
                        "fta": c["fta"] / c["gp"], "gp": c["gp"]}
    over = {}                                      # team -> points above its usual from threes and free throws
    for t in (H, A):
        b, u = box[t], usual.get(t)
        if not u:
            continue
        d3 = 3 * (b["tpm"] - b["tpa"] * u["tp"]) if u["tp"] is not None else 0
        dft = (b["ftm"] - b["fta"] * u["ft"]) if u["ft"] is not None else 0
        d2 = 2 * ((b["fgm"] - b["tpm"]) - (b["fga"] - b["tpa"]) * u["two"]) if u["two"] is not None else 0
        over[t] = {"tp": d3, "ft": dft, "two": d2}
        if abs(d3) >= 6:
            add("won", abs(d3), f"{S(t)} made {b['tpm']} of {b['tpa']} threes ({pc(b['tpm'], b['tpa'])}); their season mark coming in was "
                f"{100 * u['tp']:.1f}%. That is about {num(d3)} points {'more' if d3 > 0 else 'fewer'} than usual from the same shots.")
        if abs(d2) >= 8:
            add("won", abs(d2) * 0.8, f"{S(t)} made {b['fgm'] - b['tpm']} of {b['fga'] - b['tpa']} twos ({pc(b['fgm'] - b['tpm'], b['fga'] - b['tpa'])}) "
                f"against a season mark of {100 * u['two']:.1f}%: about {num(d2)} points {'more' if d2 > 0 else 'fewer'} than usual.")
        if abs(dft) >= 5:
            add("won", abs(dft), f"{S(t)} made {b['ftm']} of {b['fta']} free throws ({pc(b['ftm'], b['fta'])}; season {100 * u['ft']:.1f}%): "
                f"about {num(dft)} points {'more' if dft > 0 else 'fewer'} than usual at the line.")
        dt = b["tov"] - u["tov"]
        if abs(dt) >= 5:
            add("won", abs(dt) * 1.1, f"{S(t)} turned it over {b['tov']} times, {num(dt)} {'more' if dt > 0 else 'fewer'} than their average ({u['tov']:.1f}).")
        dp = b["pts"] - u["pts"]
        if b["pts"] > max(v["pts"] for v in HI.values()) and len(HI) >= 6:
            add("won", 9, f"{S(t)}'s {b['pts']} is the most any team has scored in a game this season.")
        elif b["pts"] > HI[t]["pts"] and u["gp"] >= 5:
            add("won", 6, f"{S(t)}'s {b['pts']} is their season high ({dp:+.0f} on their average).".replace("+", "+").replace("-", "−"))
        elif dp <= -15:
            add("won", 6, f"{S(t)} were held to {b['pts']}, {num(dp)} under their average ({u['pts']:.1f}).")
    bw, bl = box[W], box[Lo]
    if abs(bw["tov"] - bl["tov"]) >= 6:
        lo_, hi_ = (W, Lo) if bw["tov"] < bl["tov"] else (Lo, W)
        add("won", abs(bw["tov"] - bl["tov"]) * 1.0, f"Turnovers: {S(hi_)} {box[hi_]['tov']}, {S(lo_)} {box[lo_]['tov']}.")
    evt = (ev or {}).get("teams", {})
    ok_ev = all(evt.get(t, {}).get("ok") for t in (H, A)) if evt else False
    if abs(bw["oreb"] - bl["oreb"]) >= 6 or (ok_ev and abs(evt[W]["sc"][1] - evt[Lo]["sc"][1]) >= 10):
        t1, t2 = (W, Lo) if bw["oreb"] >= bl["oreb"] else (Lo, W)
        sc = f", for second-chance points of {evt[t1]['sc'][1]}–{evt[t2]['sc'][1]}" if ok_ev else ""
        w = abs(evt[W]["sc"][1] - evt[Lo]["sc"][1]) if ok_ev else abs(bw["oreb"] - bl["oreb"])
        add("won", w, f"Offensive rebounds: {S(t1)} {box[t1]['oreb']}, {S(t2)} {box[t2]['oreb']}{sc}.")
    if abs(bw["fta"] - bl["fta"]) >= 10:
        t1, t2 = (W, Lo) if bw["fta"] > bl["fta"] else (Lo, W)
        add("won", abs(box[t1]["ftm"] - box[t2]["ftm"]), f"{S(t1)} shot {box[t1]['fta']} free throws to {S(t2)}'s {box[t2]['fta']} "
            f"(made {box[t1]['ftm']} to {box[t2]['ftm']}; fouls {box[t2]['pf']} to {box[t1]['pf']}).")
    if ok_ev and abs(evt[W]["trans"][1] - evt[Lo]["trans"][1]) >= 8:
        t1, t2 = (W, Lo) if evt[W]["trans"][1] > evt[Lo]["trans"][1] else (Lo, W)
        add("won", abs(evt[W]["trans"][1] - evt[Lo]["trans"][1]), f"Transition points: {S(t1)} {evt[t1]['trans'][1]}, {S(t2)} {evt[t2]['trans'][1]}.")
    sht = (sh or {}).get("teams", {})
    if all(t in sht for t in (H, A)):
        paint = {t: 2 * (sht[t]["ra"]["m"] + sht[t]["paint"]["m"]) for t in (H, A)}
        if abs(paint[W] - paint[Lo]) >= 12:
            t1, t2 = (W, Lo) if paint[W] > paint[Lo] else (Lo, W)
            add("won", abs(paint[W] - paint[Lo]) * 0.8, f"Points in the paint: {S(t1)} {paint[t1]}, {S(t2)} {paint[t2]}.")
    rows = pd["teams"] if pd else {}
    if all(t in rows for t in (H, A)):
        bench = {t: sum(r["pts"] for r in rows[t] if not r.get("starter")) for t in (H, A)}
        if abs(bench[W] - bench[Lo]) >= 15:
            t1, t2 = (W, Lo) if bench[W] > bench[Lo] else (Lo, W)
            add("won", abs(bench[W] - bench[Lo]) * 0.8, f"Bench points: {S(t1)} {bench[t1]}, {S(t2)} {bench[t2]}.")

    # ---- the tip
    tip = x.get("tip") or {}
    if tip.get("p") is not None:
        p, m = tip["p"], tip.get("m") or 0
        fav, dog = (H, A) if p >= 0.5 else (A, H)
        pf = max(p, 1 - p)
        res = hs - as_
        right = (res > 0) == (p >= 0.5)
        swing = abs(res - m)
        bt = "The back-tested tip" if tip.get("src") == "backtest" else "The tip"
        s = (f"{bt} was {S(fav)} ({100 * pf:.0f}%, by {abs(m):.0f}). {S(W)} won by {abs(res)}"
             + (f": {'right' if right else 'wrong'}, and {swing:.0f} points away from the expected margin." if swing >= 1 else ": right, and on the expected margin."))
        if not right:
            s += f" The model gave {S(W)} a {100 * (1 - pf):.0f}% chance."
        add("tip", 100, s)
        # the same game with both teams making their usual share of the threes and free throws they took
        if H in over and A in over:
            luck = (over[H]["tp"] + over[H]["ft"]) - (over[A]["tp"] + over[A]["ft"])     # in home points
            adj = res - luck
            lw = luck if res > 0 else -luck                                              # how much it helped the winner
            aw = adj if res > 0 else -adj                                                # winner's margin without it
            sh_line = lambda t: (f"{S(t)} {box[t]['tpm']} of {box[t]['tpa']} threes ({pc(box[t]['tpm'], box[t]['tpa'])}; season {100 * usual[t]['tp']:.0f}%) and "
                                 f"{box[t]['ftm']} of {box[t]['fta']} free throws ({pc(box[t]['ftm'], box[t]['fta'])}; season {100 * usual[t]['ft']:.0f}%)")
            if abs(lw) >= 5 and all(usual[t]["tp"] is not None and usual[t]["ft"] is not None for t in (H, A)):
                add("tip", 85 if abs(lw) >= 5 else 0, f"{sh_line(W)}; {sh_line(Lo)}.")
            if abs(lw) >= 5:
                if aw < -0.5:
                    add("tip", 90, f"Shooting on the night decided it: with both teams making their usual share of the threes and free throws "
                        f"they took, {S(Lo)} win by about {num(aw)}. " + ("That is where the tip went wrong." if not right else
                        f"The tip was right, but {S(W)} needed the hot hand."))
                elif lw > 0:
                    add("tip", 80, f"Shooting on the night was worth about {num(lw)} points to {S(W)}; at both teams' usual percentages on the same "
                        f"threes and free throws they still win, by about {num(aw)}." + ("" if right else f" The tip missed on more than shooting: see the numbers that mattered."))
                else:
                    add("tip", 80, f"{S(W)} won despite the shooting: at both teams' usual percentages on the same threes and free throws "
                        f"the margin is about {num(aw)}, not {abs(res)}.")
            elif not right:
                add("tip", 80, f"Shooting luck doesn't explain it (worth about {num(lw)} points to {S(W if lw >= 0 else Lo)}): "
                    f"{S(W)} were the better team on the day. See where the game was won.")
            elif swing >= 12:
                add("tip", 70, f"Shooting on the night doesn't account for the size of it (about {num(lw)} points to {S(W if lw >= 0 else Lo)}): see the numbers that mattered.")

    # ---- performances
    if all(t in rows for t in (H, A)):
        lg_max = max((v["max"]["pts"] for v in PB.values()), default=0)
        for t in (H, A):
            played = set()
            for r in rows[t]:
                if not r.get("min"):
                    continue
                played.add(r["id"])
                b = PB.get(r["id"])
                reb = r.get("oreb", 0) + r.get("dreb", 0)
                shoot = f"{r['fgm']} of {r['fga']} from the field" + (f", {r['tpm']} of {r['tpa']} from three" if r.get("tpa", 0) >= 4 else "")
                has = b and b["gp"] >= MIN_GAMES
                avg = {k: b["tot"][k] / b["gp"] for k in b["tot"]} if has else {}
                said = False
                if r["pts"] > lg_max and len(PB) > 40 and r["pts"] >= 30:
                    add("perf", 30 + r["pts"] / 5, f"{r['name']} ({S(t)}) scored {r['pts']}, the most by anyone in a game this season ({shoot})."); said = True
                elif has and r["pts"] >= 20 and r["pts"] - avg["pts"] >= 10:
                    hi_ = " and a season high" if r["pts"] > b["max"]["pts"] and b["gp"] >= 5 else ""
                    add("perf", r["pts"] - avg["pts"] + 8, f"{r['name']} ({S(t)}) scored {r['pts']} on {shoot}: {r['pts'] - avg['pts']:.0f} above his average ({avg['pts']:.1f}){hi_}."); said = True
                elif r["pts"] >= 30:
                    add("perf", 12 + r["pts"] / 5, f"{r['name']} ({S(t)}) scored {r['pts']} on {shoot}."); said = True
                elif has and avg["pts"] >= 14 and avg["pts"] - r["pts"] >= 10 and r["min"] >= 15:
                    add("perf", avg["pts"] - r["pts"] + 6, f"{r['name']} ({S(t)}) was held to {r['pts']} on {shoot} in {r['min']:.0f} minutes; he averages {avg['pts']:.1f}."); said = True
                extra = []
                if r.get("tpm", 0) >= 6 and not said:
                    extra.append((r["tpm"] * 2, f"hit {r['tpm']} of {r['tpa']} threes"))
                if reb >= 15 or (has and reb >= 10 and reb - avg["reb"] >= 7):
                    extra.append((reb, f"had {reb} rebounds" + (f" (averages {avg['reb']:.1f})" if has else "")))
                if r.get("ast", 0) >= 11 or (has and r.get("ast", 0) >= 8 and r["ast"] - avg["ast"] >= 5):
                    extra.append((r["ast"] * 1.5, f"had {r['ast']} assists" + (f" (averages {avg['ast']:.1f})" if has else "")))
                if r.get("stl", 0) >= 5:
                    extra.append((r["stl"] * 3, f"had {r['stl']} steals"))
                if r.get("blk", 0) >= 4:
                    extra.append((r["blk"] * 3.5, f"blocked {r['blk']} shots"))
                if extra:
                    add("perf", max(e[0] for e in extra), f"{r['name']} ({S(t)}) " + " and ".join(e[1] for e in extra) + ".")
            # the best and worst plus-minus where it stands out from the result
            pm = [r for r in rows[t] if r.get("min", 0) >= 10 and r.get("pm") is not None]
            if pm:
                tm = (hs - as_) if t == H else (as_ - hs)
                top = max(pm, key=lambda r: r["pm"]) if tm > 0 else min(pm, key=lambda r: r["pm"])
                against = max(pm, key=lambda r: r["pm"]) if tm < 0 else min(pm, key=lambda r: r["pm"])
                if abs(top["pm"]) >= 20 and abs(top["pm"] - tm) >= 8:
                    add("pm", abs(top["pm"]) / 2.5, f"{S(t)} were {top['pm']:+d} in {top['name']}'s {top['min']:.0f} minutes and {tm - top['pm']:+d} in the rest.".replace("-", "−"))
                if against["pm"] * tm < 0 and abs(against["pm"] - tm) >= 18:
                    add("pm", abs(against["pm"] - tm) / 3, f"{S(t)} {'won' if tm > 0 else 'lost'} by {abs(tm)} but were {against['pm']:+d} with {against['name']} on the floor ({against['min']:.0f} minutes).".replace("-", "−"))
            # regulars who did not play
            n = len(TG.get(t, []))
            if n >= MIN_GAMES:
                last3 = set(TG[t][-3:])
                for pid, b in PB.items():
                    if b["team"] != t or pid in played or b["gp"] < MIN_GAMES:
                        continue
                    if b["tot"]["min"] / b["gp"] >= 15 and b["gp"] >= 0.6 * n and last3 & set(b["games"]):
                        add("perf", 10 + b["tot"]["pts"] / b["gp"], f"{b['name']} ({S(t)}; {b['tot']['pts'] / b['gp']:.1f} points and {b['tot']['min'] / b['gp']:.0f} minutes a game) did not play.")

    # ---- how it unfolded
    line = tl = None
    try:
        line, tl = flow(summary(g["id"].replace("espn-", "")), hs, as_)
    except Exception as ex:
        print("recaps: no ESPN summary for", g["id"], repr(ex))
    if line:
        total = 2400 + 300 * max(0, len(line["h"]) - 4)
        for w, t in flow_facts(tl, line, H, A, hs, as_, total):
            add("flow", w, t)
    if ok_ev and evt[H]["clutch"]["sec"] >= 60:
        ch, ca = evt[H]["clutch"]["f"].get("pts", 0), evt[A]["clutch"]["f"].get("pts", 0)
        add("flow", 7, f"In the clutch (last five minutes or overtime, margin five or fewer; {evt[H]['clutch']['sec'] / 60:.1f} minutes of it): "
            f"{S(H)} {ch}, {S(A)} {ca}.")
    elif abs(hs - as_) <= 5:
        add("flow", 5, f"Decided by {abs(hs - as_)}.")
    for f in sorted((f for f in facts if f["k"] == "pm"), key=lambda f: -f["w"])[2:]:      # the two biggest plus-minus splits
        facts.remove(f)
    for f in facts:
        if f["k"] == "pm":
            f["k"] = "perf"
    order = {"tip": 0, "won": 1, "perf": 2, "flow": 3}
    cap = {"tip": 3, "won": 6, "perf": 7, "flow": 6}
    out = []
    for k in order:
        fs = sorted((f for f in facts if f["k"] == k), key=lambda f: -f["w"])[:cap[k]]
        out += [{"k": f["k"], "t": f["t"]} for f in fs]
    return {"v": VERSION, "at": datetime.datetime.now(L.SYD).isoformat(timespec="seconds"), "line": line, "tracked": bool(tl), "facts": out}


def build(root, every=False):
    mp = load(root, "matchups_played")
    lad = load(root, "ladder")
    if not mp or not lad:
        raise RuntimeError("needs data/ladder.json and data/matchups_played.json (run ladder.py and matchups.py first)")
    games, players = load(root, "games", []), load(root, "players", [])
    events = {e["espnId"]: e for e in load(root, "events", [])}
    shots = {s["espnId"]: s for s in load(root, "shots", [])}
    pdoc = {p["espnId"]: p for p in players}
    n = collections.Counter()
    for x in lad["games"]:
        m = mp["games"].get(x["id"])
        if x.get("hs") is None or m is None:
            continue
        if not every and (m.get("recap") or {}).get("v") == VERSION:
            n["kept"] += 1
            continue
        c = [g for g in games if g["home"]["team"] == x["home"] and g["away"]["team"] == x["away"]
             and abs((datetime.date.fromisoformat(g["date"]) - datetime.date.fromisoformat(x["date"])).days) <= 2]
        if not c:
            n["no box score yet"] += 1
            continue
        g = min(c, key=lambda g: abs((datetime.date.fromisoformat(g["date"]) - datetime.date.fromisoformat(x["date"])).days))
        if (g["home"]["pts"], g["away"]["pts"]) != (x["hs"], x["as"]):
            n["score differs from the fixture"] += 1
            continue
        eid = g["id"].replace("espn-", "")
        m["recap"] = recap(x, g, pdoc.get(eid), events.get(eid), shots.get(eid), games, players, mp)
        n["written"] += 1
    return mp, n


def main():
    root = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "."
    try:
        mp, n = build(root, "--all" in sys.argv)
    except Exception as ex:
        print("recaps: not written, previous kept:", repr(ex))
        sys.exit(1)
    fp = os.path.join(root, "data", "matchups_played.json")
    with open(fp + ".tmp", "w") as f:
        json.dump(mp, f, separators=(",", ":"), ensure_ascii=False)
    os.replace(fp + ".tmp", fp)
    print("recaps: " + ", ".join(f"{v} {k}" for k, v in n.items()))


if __name__ == "__main__":
    main()
