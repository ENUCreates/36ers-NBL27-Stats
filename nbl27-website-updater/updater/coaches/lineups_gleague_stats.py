#!/usr/bin/env python3
"""Lineups for the Head coaches tab's G League / D-League seasons that ESPN does not cover (lineups_gleague.py does the
ones it does), from the league's own stats site, into data/coachlineups.json; each script replaces only its own seasons.

    python3 lineups_gleague_stats.py <repo_root>

One-off. Source: stats.gleague.nba.com/game/<id>/ (official box score) and .../playbyplay/ (official play-by-play).
The site can't be downloaded from a script here, so the pages were read in a browser and saved as
coaches/gleague/<club><season>.json.gz: the club's regular-season games. Saved per game: id, label ("MAR 14, 2018 -
IWA @ RNO"), line = the line score, box = for each side (visitors first) the box-score table as shown, each row led by
the player's id, pbp = the play-by-play table as shown: [visitors' play, "clock [score]", home play], and
["Start of Q1"] ... rows between the periods.

Who is on the floor: the play-by-play gives every substitution ("SUB: A FOR B") but not the five who start a period.
The first quarter's are the box score's starters (the ones with a position). For the later periods the five are read
from the period itself: anyone who does something, or is taken off, before being brought on started it; where that
finds fewer than five, the rest are the ones that make the game's minutes agree with the box score.
The feed names players by surname (of those who played in the game); where two share one, it gives one of them an
initial ("P. Williams") and the bare surname is the other.

Free throws go to the ten on the floor when the foul was called (the NBA's plus-minus rule), not to anyone brought on
before they are shot.

The feed's substitutions are not always entered at the moment they happened (before the free throws they came between,
say). Where the plus-minus does not come out, a substitution may be taken just after a score listed within 10 seconds
after it, or just before one within 10 seconds before it - one such move at a time, or two where no single one helps.

A game counts only if the result reproduces the official box score: five on the floor for the club all game, both
teams' points equal to the final score, and every one of the club's players' minutes within 30 seconds and plus-minus
equal to the box score's. The others are listed and left out ("ok": false), as on the NBL seasons.

Each stretch of game clock goes to its five, and every shot, free throw, rebound (players' only) and turnover (the
team's own included) goes to the five on the floor for it (f) or against it (a).
Game time: 12-minute quarters; an overtime's length is the clock it starts on.
"""
import collections, datetime, gzip, itertools, json, os, re, sys, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
K = ("pts", "fga", "fgm", "tpa", "tpm", "fta", "ftm", "oreb", "dreb", "tov")
# file -> (season, team as the page names the season, club code on the league's site, coach, league as the page names it)
SEASONS = {
    "iowa2017": ("2017-18", "Iowa Wolves", "IWA", "scott-roth", "NBA G League"),
}
# a season on the Head coaches tab that this source can't do: the page says why
NONE = {"2008-09|Bakersfield Jam": ("scott-roth", "NBA D-League",
                                    "the league's stats site has the box scores of this season but no play-by-play (\"currently unavailable\")")}
SRC = "NBA G League play-by-play"
NOTE = ("the league's own play-by-play (stats.gleague.nba.com), which gives every substitution; the five who start each "
        "later period are worked out from it. Checked game by game against the official box scores (both teams' points "
        "and each of the club's players' minutes and +/-). Regular season only")


def norm(n):
    return re.sub(r"[^a-z ]", "", unicodedata.normalize("NFKD", n or "").encode("ascii", "ignore").decode().lower().replace("-", " ")).split()


def clock(t):
    m = re.match(r"\s*(\d+):(\d+)(?:\.(\d))?", t or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


class Side:
    def __init__(self, table, text=""):
        self.rows = {}
        for r in table[1:]:
            if not r[0]:
                continue
            nm = r[1]
            pos = re.search(r"\s([FGC])$", nm)
            if pos:
                nm = nm[:pos.start()]
            mm = re.match(r"(\d+)(?:\.\d+)?:(\d+)", r[2] if len(r) > 2 else "")
            sec = int(mm.group(1)) * 60 + int(mm.group(2)) if mm else 0
            pm = r[-1] if len(r) > 10 and re.fullmatch(r"-?\d+", r[-1]) else None
            self.rows[r[0]] = {"name": nm.strip(), "s5": bool(pos), "sec": sec, "pm": int(pm) if pm is not None else None}
        self.keys = {}
        sur = collections.defaultdict(list)
        for p, r in self.rows.items():
            if not r["sec"]:
                continue      # a surname is one of those who played
            t = norm(r["name"])
            for k in range(1, len(t)):          # every tail of the name: "webb iii", "iii" ...
                sur[" ".join(t[k:])].append(p)
            self.full = getattr(self, "full", {})
            self.full[" ".join(t)] = p
            if len(t) > 1:
                sur[t[0][0] + " " + " ".join(t[1:])].append(p)      # "c williams"
        self.sur = {k: v[0] for k, v in sur.items() if len(v) == 1}
        # two who played share a surname: the feed gives one of them an initial ("P. Williams"), the other none
        said = " ".join(norm(text))
        for k, v in sur.items():
            if len(v) > 1 and " " not in k:
                plain = [p for p in v if (" " + norm(self.rows[p]["name"])[0][0] + " " + k + " ") not in (" " + said + " ")]
                if len(plain) == 1:
                    self.sur[k] = plain[0]

    def who(self, text):
        """the player a play's text starts with -> id (longest name that fits)"""
        t = norm(text)
        for n in range(min(4, len(t)), 0, -1):
            k = " ".join(t[:n])
            if k in self.full:
                return self.full[k]
            if k in self.sur:
                return self.sur[k]
        return None


def parse(side, text):
    """one cell of the play-by-play -> (kind, actor id, extra)"""
    if text.startswith("SUB:"):
        m = re.match(r"SUB:\s*(.+?)\s+FOR\s+(.+)$", text)
        return ("sub", side.who(m.group(1)), side.who(m.group(2))) if m else ("?", None, None)
    miss = text.startswith("MISS ")
    body = text[5:] if miss else text
    p = side.who(body)
    if "Free Throw" in body:
        return ("ft", p, not miss)
    if miss:
        return ("fg", p, (False, "3PT" in body))
    if re.search(r"\(\d+ PTS\)", body):
        a = re.search(r"\(([^()]+?) \d+ AST\)", body)
        return ("fg", p, (True, "3PT" in body, side.who(a.group(1)) if a else None))
    m = re.search(r"REBOUND \(Off:(\d+) Def:(\d+)\)", body)
    if m:
        return ("reb", p, (int(m.group(1)), int(m.group(2))))
    if "Turnover" in body:
        return ("tov", p, None)
    if re.search(r"T\.FOUL|Technical|Ejection|Timeout|Instant Replay|Delay", body, re.I):
        return ("other", None, None)
    if re.search(r"FOUL|Foul|STEAL|BLOCK|Violation", body):
        return ("act", p, None)
    return ("other", None, None)


def game(g, code):
    """-> (units {five: [sec, f, a]}, why not usable or None, {player id: name})"""
    if not g.get("pbp") or not g.get("box"):
        return {}, "the league's page for it shows no box score", {}      # Iowa's three games with Northern Arizona, 2017-18
    m = re.search(r"-\s*(\w+)\s+(@|vs\.)\s+(\w+)", g["label"])
    me = 0 if m.group(2) == "@" else 1              # visitors are listed first
    sides = [Side(g["box"][s], " | ".join(r[s * 2] for r in g["pbp"] if len(r) >= 3)) for s in (0, 1)]
    final = [int(g["line"][1][-1]), int(g["line"][2][-1])]
    why = []
    periods, cur = [], None
    for r in g["pbp"]:
        if len(r) == 1 and r[0].startswith("Start of"):
            cur = []
            periods.append([r[0].split()[-1], cur])
            continue
        if cur is None or len(r) < 3:
            continue
        c = clock(r[1])
        if c is None:
            continue
        for s in (0, 1):
            if r[s * 2]:
                # a steal or block sits beside the other side's play: the play first
                cur.append((s, c, parse(sides[s], r[s * 2])))
    for p in periods:
        p[1].sort(key=lambda e: 0)      # as listed
        p.insert(0, 720 if p[0].startswith("Q") else (max(e[1] for e in p[1]) if p[1] else 120))
    periods = [(L, ev) for L, q, ev in periods]
    # in a row with both columns the acting play comes first
    starts = []
    for pi, (L, ev) in enumerate(periods):
        st, seen, act = [set(), set()], [set(), set()], [set(), set()]
        for s, c, (kind, p, x) in ev:
            if kind == "sub":
                if p is None or x is None:
                    if s == me:
                        why.append("can't tell which player a substitution names")
                    continue
                if x not in seen[s]:
                    st[s].add(x)
                seen[s].add(p); act[s].update((p, x))
                continue
            for q in [p] + ([x[2]] if kind == "fg" and x[0] and len(x) > 2 else []):
                if q is None:
                    continue
                if q not in seen[s]:
                    st[s].add(q)
                act[s].add(q)
        if pi == 0:
            for s in (0, 1):
                s5 = {p for p, r in sides[s].rows.items() if r["s5"]}
                if len(s5) == 5:
                    st[s] = s5
        starts.append((st, act))
    if why:
        return {}, "; ".join(sorted(set(why))), {}

    def run(starts_, late=None):
        units = {}
        onsec, pm, pts = collections.Counter(), collections.Counter(), collections.Counter()
        bad = []
        Z = lambda: dict.fromkeys(K, 0)
        for pi, (L, ev) in enumerate(periods):
            on = [set(starts_[pi][0]), set(starts_[pi][1])]
            mv = {a: b for (p, a), b in (late or {}).items() if p == pi}
            if mv:
                after, before = collections.defaultdict(list), collections.defaultdict(list)
                for a, b in mv.items():
                    (after if b > a else before)[b].append(ev[a])
                out = []
                for k, e in enumerate(ev):
                    out += before[k]
                    if k not in mv:
                        out.append(e)
                    out += after[k]
                ev = out
            clk = L
            snap = [set(on[0]), set(on[1])]      # who was on when the clock last stopped where it is

            def unit():
                return units.setdefault(tuple(sorted(on[me])), [0, Z(), Z()])
            for s, c, (kind, p, x) in ev + [(0, 0, ("end", None, None))]:
                c = min(c, clk)
                d = clk - c
                if d > 0:
                    if len(on[me]) != 5 and not bad:
                        bad.append(f"{len(on[me])} on the floor in period {pi + 1} with {clk}s left")
                    for t in (0, 1):
                        for q in on[t]:
                            onsec[(t, q)] += d
                    unit()[0] += d
                    clk = c
                    snap = [set(on[0]), set(on[1])]
                if kind == "sub":
                    if p is not None and x is not None:
                        on[s].discard(x); on[s].add(p)
                    continue
                # free throws belong to the ten on the floor when the foul was called (the NBA's rule for plus-minus):
                # the fives as they stood when the clock stopped, whatever substitutions were made before the shots
                cred = snap if kind == "ft" else on
                u = units.setdefault(tuple(sorted(cred[me])), [0, Z(), Z()])[1 if s == me else 2]
                made = 0
                if kind == "fg":
                    ok, three = x[0], x[1]
                    u["fga"] += 1; u["fgm"] += ok
                    if three:
                        u["tpa"] += 1; u["tpm"] += ok
                    made = ok * (3 if three else 2)
                elif kind == "ft":
                    u["fta"] += 1; u["ftm"] += x
                    made = x * 1
                elif kind == "reb" and p is not None:
                    o0, d0 = REB.get((id(g), s, p), (0, 0))
                    if x[0] > o0:
                        u["oreb"] += 1
                    elif x[1] > d0:
                        u["dreb"] += 1
                    REB[(id(g), s, p)] = x
                elif kind == "tov":
                    u["tov"] += 1
                if made:
                    u["pts"] += made
                    pts[s] += made
                    for t in (0, 1):
                        for q in cred[t]:
                            pm[(t, q)] += made if t == s else -made
        for k in [k for k in REB if k[0] == id(g)]:
            del REB[k]
        err = sum(abs(onsec[(me, p)] - r["sec"]) for p, r in sides[me].rows.items())
        return units, onsec, pm, pts, bad, err

    opts = []
    for pi, (st, act) in enumerate(starts):
        per = []
        for s in (0, 1):
            need = 5 - len(st[s])
            if need < 0:
                if s == me:
                    return {}, f"{len(st[s])} players look like starters of period {pi + 1}", {}
                per.append([st[s]])
                continue
            if s != me:
                per.append([st[s]])      # the other side's five is of no consequence for the club's
                continue
            cand = [p for p, r in sides[s].rows.items() if r["sec"] > 0 and p not in act[s]]
            per.append([st[s] | set(c) for c in itertools.combinations(cand, need)] or [st[s]])
        opts.append([(a, b) for a in per[0] for b in per[1]])
    n = 1
    for o in opts:
        n *= len(o)
    if n > 20000:
        return {}, f"too many ways to fill the periods' starting fives ({n})", {}
    best = combo_best = None
    for combo in itertools.product(*opts):
        r = run(combo)
        if best is None or (len(r[4]), r[5]) < (len(best[4]), best[5]):
            best, combo_best = r, combo
            if not r[4] and r[5] == 0:
                break
    pmbad = lambda r: sum(abs(r[2][(me, p)] - x["pm"]) for p, x in sides[me].rows.items() if x["pm"] is not None and (r[1][(me, p)] or x["sec"]))
    minok = lambda r: max(abs(r[1][(me, p)] - x["sec"]) for p, x in sides[me].rows.items()) <= 30
    if not best[4] and pmbad(best):
        cands = {}
        scored = lambda e: (e[2][0] == "fg" and e[2][2][0]) or (e[2][0] == "ft" and e[2][2])
        for pi, (L, ev) in enumerate(periods):
            for a, e in enumerate(ev):
                if e[0] != me or e[2][0] != "sub":
                    continue
                c = []
                for b in range(a + 1, len(ev)):
                    if ev[b][1] < e[1] - 10:
                        break
                    if scored(ev[b]):
                        c.append(b)
                for b in range(a - 1, -1, -1):
                    if ev[b][1] > e[1] + 10:
                        break
                    if scored(ev[b]):
                        c.append(b)
                if c:
                    cands[(pi, a)] = c
        cur, late, improved = pmbad(best), {}, True
        while improved and cur:
            improved = False
            for gk, c in cands.items():
                for b in c + [None]:
                    trial = dict(late)
                    trial.pop(gk, None)
                    if b is not None:
                        trial[gk] = b
                    r = run(combo_best, trial)
                    if not r[4] and pmbad(r) < cur and minok(r):
                        cur, late, best, improved = pmbad(r), trial, r, True
            if cur and not improved:
                flat = [(gk, b) for gk, c in cands.items() for b in c if late.get(gk) != b]
                if len(flat) <= 90:
                    for (g1, b1), (g2, b2) in itertools.combinations(flat, 2):
                        if g1 == g2:
                            continue
                        trial = dict(late); trial[g1] = b1; trial[g2] = b2
                        r = run(combo_best, trial)
                        if not r[4] and pmbad(r) < cur and minok(r):
                            cur, late, best, improved = pmbad(r), trial, r, True
                            break
    units, onsec, pm, pts, bad, err = best
    why += bad
    for s in (0, 1):
        if pts[s] != final[s]:
            why.append(f"points {pts[s]} vs final score {final[s]} for the {'home side' if s else 'visitors'}")
    off = [(r["name"], onsec[(me, p)] - r["sec"]) for p, r in sides[me].rows.items() if abs(onsec[(me, p)] - r["sec"]) > 30]
    if off:
        why.append("minutes off: " + ", ".join(f"{n} {d:+d}s" for n, d in off[:4]))
    wrong = [(r["name"], pm[(me, p)], r["pm"]) for p, r in sides[me].rows.items() if r["pm"] is not None and (onsec[(me, p)] or r["sec"]) and pm[(me, p)] != r["pm"]]
    if wrong:
        why.append("plus-minus off: " + ", ".join(f"{n} {a:+d} vs {b:+d}" for n, a, b in wrong[:4]))
    return units, ("; ".join(why) or None), {p: r["name"] for p, r in sides[me].rows.items()}


REB = {}


def main(root):
    key = lambda n: "".join(norm(n))
    bios = json.load(open(os.path.join(root, "data", "bios.json")))
    espn = {key(b["name"]): str(b["id"]) for b in bios if b.get("id")}
    fp = os.path.join(root, "data", "coachlineups.json")
    D = json.load(open(fp))
    for f, (season, team, code, coach, league) in SEASONS.items():
        path = os.path.join(HERE, "gleague", f + ".json.gz")
        if not os.path.exists(path):
            continue
        raw = json.load(gzip.open(path, "rt"))
        date = lambda g: datetime.datetime.strptime(g["label"].split(" - ")[0].title(), "%b %d, %Y").date().isoformat()
        L = sorted(raw, key=lambda g: (date(g), g["id"]))
        games, names, tot = [], {}, {}
        for gi, g in enumerate(L):
            m = re.search(r"-\s*(\w+)\s+(@|vs\.)\s+(\w+)", g["label"])
            home = m.group(2) != "@"
            units, why, nm = game(g, code)
            names.update(nm)
            games.append({"id": str(g["id"]), "date": date(g), "opp": m.group(3), "home": home, "ok": not why})
            if why:
                print(f"  {season} {date(g)} v {m.group(3)}: left out ({why})")
                continue
            for five, (sec, fo, ag) in units.items():
                if len(five) != 5:
                    continue
                t = tot.setdefault(five, [0, dict.fromkeys(K, 0), dict.fromkeys(K, 0), []])
                t[0] += sec
                for k in K:
                    t[1][k] += fo[k]; t[2][k] += ag[k]
                t[3].append(gi)
        ids = sorted({p for five in tot for p in five}, key=lambda p: names.get(p, ""))
        ix = {p: i for i, p in enumerate(ids)}
        players = [[names.get(p) or p, espn.get(key(names.get(p) or "")), None] for p in ids]
        units = [[sorted(ix[p] for p in five), float(sec), [fo[k] for k in K], [ag[k] for k in K], gl]
                 for five, (sec, fo, ag, gl) in sorted(tot.items(), key=lambda kv: -kv[1][0]) if sec > 0 or any(fo.values()) or any(ag.values())]
        D["seasons"][f"{season}|{team}"] = {"coach": coach, "league": league, "src": SRC, "note": NOTE, "games": games, "players": players, "units": units}
        used = sum(1 for x in games if x["ok"])
        fo = {k: sum(u[2][i] for u in units) for i, k in enumerate(K)}
        ag = {k: sum(u[3][i] for u in units) for i, k in enumerate(K)}
        print(f"{season} {team}: {used} of {len(games)} games used, {len(units)} fives, {len(players)} players, "
              f"{sum(u[1] for u in units) / 60:.0f} min; for {fo['pts']} pts {fo['fgm']}/{fo['fga']} FG {fo['oreb']}+{fo['dreb']} reb {fo['tov']} tov, against {ag['pts']} pts")
    for k, (coach, league, reason) in NONE.items():
        D["seasons"][k] = {"coach": coach, "league": league, "none": reason}
    if "--dry" not in sys.argv:
        tmp = fp + ".tmp"
        json.dump(D, open(tmp, "w"), separators=(",", ":"), ensure_ascii=False)
        os.replace(tmp, fp)


if __name__ == "__main__":
    main(sys.argv[1])
