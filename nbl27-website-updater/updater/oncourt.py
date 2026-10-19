#!/usr/bin/env python3
"""Who is on the floor at every play of an NBL27 game. Shared by lineups.py and events.py.

ESPN's NBL play-by-play misses roughly 4 in 10 substitutions, so the on-court five can't simply be read off it.
Each team-game is solved as one optimisation (a mixed-integer program, solved with scipy's HiGHS):

  unknown   the five players on the floor in every 'slot': the run of plays between two moments when a
            substitution can happen (a dead ball, a recorded substitution, a period break)
  evidence  recorded substitutions; every player who records an action (he must be on the floor); the five
            starters; and the official box score: each player's minutes (ESPN rounds to the nearest minute, so
            anything within 30 seconds matches) and his plus-minus
  minimise  substitutions ESPN didn't record + recorded substitutions ignored + actions by players placed on the
            bench + seconds outside each player's box-score minutes + points away from his box-score plus-minus

Validation on the first 36 games (Oct 2026): with only minutes as a guide, rebuilt plus-minus (never shown to the
solver) matched the box exactly for 69% of player-games, against 53% for the old play-by-play patching. Guiding
with half the players' plus-minus raised the other half's exact matches further (67-71% -> 71-78%), so both are
used.

Solutions are cached in data/oncourt.json, keyed by a hash of every input, so a game is only solved again when
ESPN changes something the solver reads or SOLVER_VERSION / W change. Set NBL_ONCOURT_CACHE to that file's path
(rebuild.py does). Needs numpy and scipy (>= 1.9).
"""
import json, os, sys, collections, datetime, hashlib, time
from zoneinfo import ZoneInfo

SOLVER_VERSION = "1"
# unrecorded swap (in + out), swap at a period break, recorded sub ignored (per player), action by a benched player,
# same for a plain 'Foul' row (can be a bench technical), cost per second outside +/-TOL of box minutes, cost per
# point of plus-minus error, tie-break size, MIP gap, time limit per team (s)
W = {"U": 1.0, "Ubrk": 0.5, "V": 2.0, "A": 8.0, "Asoft": 2.0, "Mout": 0.1, "pm": 1.0, "eps": 0.01, "gap": 1e-3, "tl": 60}
TOL = 30
# seconds of solving per run; games still unsolved keep their previous version and are solved on the next run
# (keeps a full re-solve of a long season inside the daily task's 10-minute command limit)
BUDGET = float(os.environ.get("NBL_ONCOURT_BUDGET", "300"))

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


def _made(pl):
    return bool(pl.get("scoringPlay")) or int(pl.get("scoreValue") or 0) > 0


def change_ok(pl):
    """Can a substitution happen right after this play? (FIBA: only when the ball is dead.)"""
    t = pl["type"]["text"]
    tl = t.strip().lower()
    if t == "Substitution":
        return True
    if t in ("Defensive Rebound", "Offensive Rebound"):
        return False
    if tl.startswith("free throw"):   # before the first free throw, or after a made last one
        try:
            x, y = (int(v.strip()) for v in tl.replace("free throw", "").split("of"))
        except Exception:
            return True
        return x == y and _made(pl)
    if pl.get("shootingPlay"):        # a made basket can be followed by a timeout; a miss is live
        return _made(pl)
    if tl in ("bad pass", "turnover") and len(pl.get("participants", [])) >= 2:
        return False                  # a steal: live ball
    return True                       # fouls, violations, jump balls, end of period


def prepare(d, pdoc, swap=False):
    tid2club = {t["team"]["id"]: club(t["team"]["displayName"]) for t in d["boxscore"]["teams"]}
    comp = d["header"]["competitions"][0]
    home = next(tid2club[c["team"]["id"]] for c in comp["competitors"] if c["homeAway"] == "home")
    away = next(tid2club[c["team"]["id"]] for c in comp["competitors"] if c["homeAway"] == "away")
    final = {tid2club[c["team"]["id"]]: int(c["score"]) for c in comp["competitors"]}
    pteam, box = {}, {}
    for team, pls in pdoc["teams"].items():
        for p in pls:
            pteam[p["id"]] = team; box[p["id"]] = p
    plays = order_plays(d)

    def evs(pl):
        parts = [x["athlete"]["id"] for x in pl.get("participants", []) if x.get("athlete", {}).get("id")]
        if pl["type"]["text"] == "Substitution":
            e, l = sub_kind(pl)
            if swap: e, l = l, e
            return [(a, "in") for a in e] + [(a, "out") for a in l]
        return [(a, "act") for a in parts]
    for pl in plays:   # players missing from the box score take the team of the play they appear in
        tm = tid2club.get((pl.get("team") or {}).get("id"))
        for a, _ in evs(pl):
            if a not in pteam and tm:
                pteam[a] = tm
    rows, ph, pa = [], 0, 0
    for pl in plays:
        tm = tid2club.get((pl.get("team") or {}).get("id"))
        ins, outs, acts = {}, {}, {}
        for a, k in evs(pl):
            c = pteam.get(a, tm)
            if c is None:
                continue
            {"in": ins, "out": outs, "act": acts}[k].setdefault(c, []).append(a)
        h, a_ = int(pl.get("homeScore") or 0), int(pl.get("awayScore") or 0)
        per = pl["period"]["number"]; L = plen(per)
        rows.append({"per": per, "L": L, "t": L - secs(pl["clock"]["displayValue"]), "type": pl["type"]["text"],
                     "pl": pl, "tm": tm, "ins": ins, "outs": outs, "acts": acts,
                     "pts": {home: h - ph, away: a_ - pa}, "score": (h, a_), "ok": change_ok(pl)})
        ph, pa = h, a_
    return {"home": home, "away": away, "clubs": [home, away], "final": final, "pteam": pteam, "box": box,
            "rows": rows, "tid2club": tid2club, "comp": comp}


def team_slots(P, team):
    """Split the game into slots for one team (the lineup is constant within a slot)."""
    rows = P["rows"]; opp = P["away"] if team == P["home"] else P["home"]
    slots, cur, last_per, last_t = [], None, None, 0

    def new(per, start):
        return {"per": per, "start": start, "plays": [], "dur": 0, "mar": 0, "acts": collections.Counter(),
                "soft": collections.Counter(), "rec_in": [], "rec_out": []}
    for i, r in enumerate(rows):
        if r["per"] != last_per:
            if cur is not None:
                cur["dur"] += max(0, rows[i - 1]["L"] - last_t)
            cur = new(r["per"], True); slots.append(cur); last_per, last_t = r["per"], 0
        cur["plays"].append(i)
        cur["dur"] += max(0, r["t"] - last_t); last_t = max(last_t, r["t"])
        cur["mar"] += r["pts"][team] - r["pts"][opp]
        for a in r["acts"].get(team, []):
            (cur["soft"] if r["type"] == "Foul" else cur["acts"])[a] += 1
        if r["ok"] and i + 1 < len(rows) and rows[i + 1]["per"] == r["per"]:
            cur["rec_in"] = list(r["ins"].get(team, [])); cur["rec_out"] = list(r["outs"].get(team, []))
            cur = new(r["per"], False); slots.append(cur)
    if cur is not None:
        cur["dur"] += max(0, rows[-1]["L"] - last_t)
    return slots


def solve_team(P, team):
    import numpy as np
    from scipy.optimize import milp, LinearConstraint, Bounds
    from scipy import sparse
    slots = team_slots(P, team)
    S = len(slots)
    seen = set()
    for s in slots:
        seen |= set(s["acts"]) | set(s["soft"]) | set(s["rec_in"]) | set(s["rec_out"])
    ids = sorted(a for a, c in P["pteam"].items() if c == team and ((P["box"].get(a, {}).get("min") or 0) > 0 or a in seen))
    n = len(ids); pi = {a: k for k, a in enumerate(ids)}
    if n < 5 or S == 0:
        return None
    starters = [a for a in ids if P["box"].get(a, {}).get("starter")]
    nx, nb = n * S, S - 1
    X = lambda p, s: p * S + s
    IN = lambda p, b: nx + p * nb + b
    OUT = lambda p, b: nx + n * nb + p * nb + b
    base = nx + 2 * n * nb
    tgt = {a: 60 * (P["box"].get(a, {}).get("min") or 0) for a in ids}
    pmt = {a: P["box"][a]["pm"] for a in ids if a in P["box"] and P["box"][a].get("pm") is not None}
    pms = sorted(pmt)
    EHI = lambda p: base + 2 * p
    ELO = lambda p: base + 2 * p + 1
    QHI = lambda k: base + 2 * n + 2 * k
    QLO = lambda k: base + 2 * n + 2 * k + 1
    nv = base + 2 * n + 2 * len(pms)
    c = np.zeros(nv)
    lb = np.zeros(nv); ub = np.ones(nv); ub[base:] = np.inf
    integ = np.zeros(nv); integ[:nx] = 1
    R, C, V, LO, HI = [], [], [], [], []

    def con(terms, lo, hi):
        r = len(LO)
        for j, v in terms:
            R.append(r); C.append(j); V.append(v)
        LO.append(lo); HI.append(hi)
    for s in range(S):
        con([(X(p, s), 1) for p in range(n)], 5, 5)
        for a, k in slots[s]["acts"].items():
            if a in pi: c[X(pi[a], s)] -= W["A"] * k
        for a, k in slots[s]["soft"].items():
            if a in pi: c[X(pi[a], s)] -= W["Asoft"] * k
    if len(starters) == 5:
        for p in range(n):
            lb[X(p, 0)] = ub[X(p, 0)] = 1 if ids[p] in starters else 0
    for b in range(nb):
        unrec = (W["Ubrk"] if slots[b + 1]["start"] else W["U"]) / 2
        rin, rout = set(slots[b]["rec_in"]), set(slots[b]["rec_out"])
        for p, a in enumerate(ids):
            con([(IN(p, b), 1), (X(p, b + 1), -1), (X(p, b), 1)], 0, np.inf)
            con([(OUT(p, b), 1), (X(p, b), -1), (X(p, b + 1), 1)], 0, np.inf)
            if a in rin:   # reward honouring the record; 'in' must then equal the real entry
                c[IN(p, b)] = -W["V"]
                con([(IN(p, b), 1), (X(p, b + 1), -1)], -np.inf, 0)
                con([(IN(p, b), 1), (X(p, b), 1)], -np.inf, 1)
            else:
                c[IN(p, b)] = unrec + W["eps"] * (((p * 7919 + b * 104729) % 1000) / 1000.0)
            if a in rout:
                c[OUT(p, b)] = -W["V"]
                con([(OUT(p, b), 1), (X(p, b), -1)], -np.inf, 0)
                con([(OUT(p, b), 1), (X(p, b + 1), 1)], -np.inf, 1)
            else:
                c[OUT(p, b)] = unrec + W["eps"] * (((p * 6271 + b * 15731) % 1000) / 1000.0)
    dur = np.array([s["dur"] for s in slots], float)
    mar = np.array([s["mar"] for s in slots], float)
    for p, a in enumerate(ids):
        T = tgt[a]
        con([(X(p, s), dur[s]) for s in range(S)] + [(EHI(p), -1)], -np.inf, T + TOL)
        con([(X(p, s), dur[s]) for s in range(S)] + [(ELO(p), 1)], T - TOL, np.inf)
        c[EHI(p)] = c[ELO(p)] = W["Mout"]
    for k, a in enumerate(pms):
        p = pi[a]
        con([(X(p, s), mar[s]) for s in range(S)] + [(QHI(k), -1)], -np.inf, pmt[a])
        con([(X(p, s), mar[s]) for s in range(S)] + [(QLO(k), 1)], pmt[a], np.inf)
        c[QHI(k)] = c[QLO(k)] = W["pm"]
    A = sparse.csr_matrix((V, (R, C)), shape=(len(LO), nv))
    t0 = time.time()
    res = milp(c, constraints=LinearConstraint(A, LO, HI), integrality=integ, bounds=Bounds(lb, ub),
               options={"time_limit": W["tl"], "mip_rel_gap": W["gap"], "disp": False})
    took = time.time() - t0
    if res.x is None:
        return None
    x = np.round(res.x[:nx]).reshape(n, S)
    lineup = [frozenset(ids[p] for p in range(n) if x[p, s] > 0.5) for s in range(S)]
    if any(len(l) != 5 for l in lineup):
        return None
    return {"slots": slots, "lineup": lineup, "fit": fit(P, team, slots, lineup, status=int(res.status), took=took)}


def fit(P, team, slots, lineup, status=None, took=None):
    """How well a solution matches the evidence (stored with the solution)."""
    sec, pm = collections.Counter(), collections.Counter()
    for s, l in zip(slots, lineup):
        for a in l:
            sec[a] += s["dur"]; pm[a] += s["mar"]
    unrec = ign = 0
    for b in range(len(slots) - 1):
        rin, rout = set(slots[b]["rec_in"]), set(slots[b]["rec_out"])
        ent, lev = lineup[b + 1] - lineup[b], lineup[b] - lineup[b + 1]
        unrec += len(ent - rin) + len(lev - rout); ign += len(rin - ent) + len(rout - lev)
    off = sum(k for s, l in zip(slots, lineup) for a, k in s["acts"].items() if a not in l)
    box = {a: p for a, p in P["box"].items() if P["pteam"].get(a) == team}
    played = set(box) | {a for l in lineup for a in l}
    merr = max((abs(sec[a] - 60 * (box.get(a, {}).get("min") or 0)) for a in played), default=0)
    perr = max((abs(pm[a] - p["pm"]) for a, p in box.items() if p.get("pm") is not None), default=0)
    out = {"unrecorded": unrec // 2, "ignored": ign, "offcourt": off, "maxMinErr": int(merr), "maxPmErr": int(perr)}
    if status is not None:
        out["optimal"] = status == 0; out["solveSec"] = round(took, 1)
    return out


def game_key(P):
    rows = [[r["per"], r["t"], r["type"], int(r["ok"]), r["ins"], r["outs"], r["acts"], r["pts"]] for r in P["rows"]]
    box = sorted([a, P["pteam"].get(a), b.get("min"), b.get("pm"), bool(b.get("starter"))] for a, b in P["box"].items())
    blob = json.dumps([SOLVER_VERSION, W, TOL, P["home"], P["away"], rows, box], sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()


# ---------------- cache ----------------
_CACHE = None


def cache_path():
    return os.environ.get("NBL_ONCOURT_CACHE")


def cache():
    global _CACHE
    if _CACHE is None:
        _CACHE = {}
        p = cache_path()
        if p and os.path.exists(p):
            try:
                _CACHE = json.load(open(p)).get("games", {})
            except Exception as ex:
                print("oncourt cache unreadable, starting empty:", ex)
    return _CACHE


def save_cache():
    p = cache_path()
    if p:
        json.dump({"about": "Solved on-court lineups per game (see nbl27-website-updater/updater/oncourt.py). "
                            "Cache only: safe to delete, the next rebuild solves every game again.",
                   "version": SOLVER_VERSION, "games": cache()},
                  open(p, "w"), separators=(",", ":"), sort_keys=True)


def _runs(lineup):
    out, prev = [], None
    for s, l in enumerate(lineup):
        if l != prev:
            out.append([s, sorted(l)]); prev = l
    return out


def _expand(runs, S):
    lineup, k = [], -1
    for s in range(S):
        while k + 1 < len(runs) and runs[k + 1][0] <= s:
            k += 1
        lineup.append(frozenset(runs[k][1]))
    return lineup


def solve_game(eid, P, allow_solve=True):
    """{team: {"slots", "lineup", "fit"}} from the cache or a fresh solve; None if unavailable."""
    key = game_key(P)
    ent = cache().get(eid)
    if ent and ent.get("key") == key:
        out = {}
        for team in P["clubs"]:
            slots = team_slots(P, team)
            out[team] = {"slots": slots, "lineup": _expand(ent["teams"][team]["runs"], len(slots)),
                         "fit": ent["teams"][team]["fit"]}
        return out
    if not allow_solve:
        return None
    out, ent = {}, {"key": key, "teams": {}}
    for team in P["clubs"]:
        r = solve_team(P, team)
        if r is None:
            return None
        out[team] = r
        ent["teams"][team] = {"runs": _runs(r["lineup"]), "fit": r["fit"]}
    cache()[eid] = ent
    return out


def _job(args):
    eid, espn_fp, players_fp, swap = args
    try:
        P = prepare(json.load(open(espn_fp)), json.load(open(players_fp)), swap)
        key = game_key(P)
        ent = {"key": key, "teams": {}}
        for team in P["clubs"]:
            r = solve_team(P, team)
            if r is None:
                return eid, None, "no solution"
            ent["teams"][team] = {"runs": _runs(r["lineup"]), "fit": r["fit"]}
        return eid, ent, None
    except Exception as ex:
        return eid, None, f"{type(ex).__name__}: {ex}"


def presolve(jobs, swap, budget=None, workers=None):
    """Solve every game not already cached, in parallel, within the time budget.
    jobs: [(eid, espn_json_path, players_doc_path)]. Returns the eids left unsolved."""
    import multiprocessing as mp
    todo = []
    for eid, efp, pfp in jobs:
        try:
            P = prepare(json.load(open(efp)), json.load(open(pfp)), swap)
        except Exception as ex:
            print(eid, "oncourt: cannot prepare:", ex); continue
        ent = cache().get(eid)
        if not (ent and ent.get("key") == game_key(P)):
            todo.append((eid, efp, pfp, swap))
    if not todo:
        print(f"oncourt: all {len(jobs)} games cached"); return []
    budget = BUDGET if budget is None else budget
    workers = workers or max(1, min(4, os.cpu_count() or 1))
    print(f"oncourt: solving {len(todo)} of {len(jobs)} games ({workers} workers, budget {budget:.0f}s)")
    t0, done, left = time.time(), set(), []
    pool = mp.get_context("fork").Pool(workers)
    try:
        it = pool.imap_unordered(_job, todo)
        while len(done) < len(todo):
            remaining = budget - (time.time() - t0)
            if remaining <= 0:
                break
            try:
                eid, ent, err = it.next(timeout=remaining)
            except mp.TimeoutError:
                break
            done.add(eid)
            if ent is None:
                print(eid, "oncourt: not solved:", err); left.append(eid); continue
            cache()[eid] = ent
            f = " | ".join(f"{t.split()[-1]} swaps {v['fit']['unrecorded']} min±{v['fit']['maxMinErr']}s "
                           f"pm±{v['fit']['maxPmErr']} {'opt' if v['fit'].get('optimal') else 'limit'} {v['fit'].get('solveSec')}s"
                           for t, v in ent["teams"].items())
            print(eid, "oncourt solved:", f)
    finally:
        pool.terminate(); pool.join()
    left += [j[0] for j in todo if j[0] not in done]
    if left:
        print(f"oncourt: {len(left)} games deferred to the next run (budget): {left}")
    save_cache()
    return left


# ---------------- stints (same outputs as the old process()) ----------------
def process(eid, d, pdoc, swap=False, tov_rule=None, hook=None, ctx=None, allow_solve=True):
    """Returns (doc, reasons, issues, (stats, fixes), ok_season), or None if no on-court solution is available.
    hook, if given, receives ("t", per, t0, t1, sets), ("e", per, tnow, i, play, sets) and ("s", per, home, away)
    records exactly as events.py expects; sets = {club: frozenset of the five on the floor}."""
    tov_rule = tov_rule or (lambda pl: is_tov(pl["type"]["text"]) and pl["type"]["text"] != "Offensive Foul")
    P = prepare(d, pdoc, swap)
    home, away, clubs = P["home"], P["away"], P["clubs"]
    opp = {home: away, away: home}
    if ctx is not None:
        ctx.update(pteam=P["pteam"], home=home, away=away, tid2club=P["tid2club"])
    sol = solve_game(eid, P, allow_solve)
    if sol is None:
        return None
    rows = P["rows"]
    during = {c: [None] * len(rows) for c in clubs}
    for c in clubs:
        for s, sl in enumerate(sol[c]["slots"]):
            for i in sl["plays"]:
                during[c][i] = sol[c]["lineup"][s]
    newacc = lambda: {k: 0 for k in STATS}
    stints = []
    periods = sorted({r["per"] for r in rows})
    for per in periods:
        idx = [i for i, r in enumerate(rows) if r["per"] == per]
        L = plen(per)
        cur = {c: None for c in clubs}

        def close(c):
            s = cur[c]
            if s is not None and (s[1] or any(s[2].values()) or any(s[3].values())):
                stints.append((c, s[0], s[1], s[2], s[3]))
            cur[c] = None

        def stint(c, lu):
            if cur[c] is None or cur[c][0] != lu:
                close(c); cur[c] = [lu, 0, newacc(), newacc()]
            return cur[c]
        last_t, sets = 0, None
        for k, i in enumerate(idx):
            r = rows[i]; pl = r["pl"]; tnow = r["t"]
            sets = {c: during[c][i] for c in clubs}
            dt = tnow - last_t
            for c in clubs:
                st = stint(c, sets[c])
                if dt > 0: st[1] += dt
            if hook: hook(("t", per, last_t, tnow, sets))
            last_t = max(last_t, tnow)
            for c in clubs:
                dv = r["pts"][c]
                if dv:
                    cur[c][2]["pts"] += dv; cur[opp[c]][3]["pts"] += dv
            if hook:
                hook(("e", per, tnow, k, pl, sets)); hook(("s", per, r["score"][0], r["score"][1]))
            t, tm = r["type"], r["tm"]
            if t == "Substitution" or tm not in opp:
                continue
            f, ag = cur[tm][2], cur[opp[tm]][3]
            tl_, txt = t.lower(), (pl.get("text") or "").lower()
            made = _made(pl)
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
            for kk, v in delta.items():
                f[kk] += v; ag[kk] += v
        if hook: hook(("t", per, last_t, L, sets))
        dt = L - last_t
        for c in clubs:
            if dt > 0: stint(c, sets[c])[1] += dt
            close(c)
    agg = {c: {} for c in clubs}
    for c, ps, sec, f, a in stints:
        g = agg[c].setdefault(tuple(sorted(ps)), {"sec": 0, "f": newacc(), "a": newacc()})
        g["sec"] += sec
        for k in STATS:
            g["f"][k] += f[k]; g["a"][k] += a[k]
    teams = {c: [{"p": list(k), "sec": v["sec"], "f": v["f"], "a": v["a"]}
                 for k, v in sorted(agg[c].items(), key=lambda kv: -kv[1]["sec"])] for c in clubs}
    reasons, stats = [], {}
    nper = max(periods) if periods else 4
    expect = 2400 + 300 * max(0, nper - 4)
    for c in clubs:
        tot = sum(x["sec"] for x in teams[c])
        if abs(tot - expect) > 30: reasons.append(f"{c} sec {tot}!={expect}")
        if any(len(x["p"]) != 5 for x in teams[c]): reasons.append(f"{c} non-5 lineup")
        pts = sum(x["f"]["pts"] for x in teams[c])
        if pts != P["final"][c]: reasons.append(f"{c} pts {pts}!={P['final'][c]}")
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
    issues, fixes = [], collections.Counter()
    for c in clubs:
        ft = sol[c]["fit"]
        fixes["unrecorded_swaps"] += ft["unrecorded"]; fixes["records_ignored"] += ft["ignored"]
        if ft["offcourt"]: issues.append(f"{c}: {ft['offcourt']} actions by players placed off court")
        if ft["maxMinErr"] > TOL: issues.append(f"{c}: a player's minutes {ft['maxMinErr']}s from the box")
    used = {p for c in clubs for x in teams[c] for p in x["p"]}
    names = {p["id"]: p["name"] for pls in pdoc["teams"].values() for p in pls}
    roster = {a["athlete"]["id"]: a["athlete"].get("displayName")
              for bt in d["boxscore"].get("players", []) for st in bt.get("statistics", [])
              for a in st.get("athletes", []) if a.get("athlete", {}).get("id")}
    nm = {p: names.get(p) or roster.get(p) or p for p in sorted(used)}
    comp = P["comp"]
    sydney = datetime.datetime.fromisoformat(comp["date"].replace("Z", "+00:00")).astimezone(ZoneInfo("Australia/Sydney"))
    doc = {"espnId": eid, "date": sydney.strftime("%Y-%m-%d"), "home": home, "away": away, "names": nm,
           "teams": teams, "fit": {c: sol[c]["fit"] for c in clubs},
           "source": f"https://www.espn.com/nbl/game/_/gameId/{eid}"}
    if reasons:
        doc["invalid"] = "; ".join(reasons)[:500]
    ok_season = sydney >= datetime.datetime(2026, 9, 19, tzinfo=ZoneInfo("Australia/Sydney")) and all(c in CLUBS for c in clubs) \
        and comp.get("status", {}).get("type", {}).get("completed", True)
    return doc, reasons, issues, (stats, dict(fixes)), ok_season
