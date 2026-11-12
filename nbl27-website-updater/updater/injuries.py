#!/usr/bin/env python3
"""NBL27 injury list -> data/injuries.json (the website's Injuries tab).

The list itself is the league's own injury list as basketball.com.au republishes it (SOURCE below: every club's
table of Player | Status | Injury | Return, which the league updates by hand). The daily update reads that page with
WebFetch using the exact prompt in injury_prompt.txt, saves the answer unchanged to a text file, and passes it here.
This script never changes a status, injury or return date: it only parses the transcription, checks it, and adds what
this site's own box scores say (games missed, last game played, minutes and points per game).

Checks (any failure -> exit 3, nothing written; the daily update reads the page once more, then falls back to --keep):
  - all ten clubs' tables were read (CLUBS SEEN), and no club is reported missing;
  - the page's own counts are there (COUNTS) and the number of rows matches them;
  - every row has a known club, a player and a status, with no duplicates.

  python3 injuries.py <repo_root> <transcription.txt>     parse, check, write data/injuries.json
  python3 injuries.py <repo_root> --keep [reason]         the page couldn't be read reliably today: keep the previous
                                                          list (marked as not refreshed) and refresh the box-score columns
"""
import collections, datetime, json, os, re, sys, tempfile, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import official as O  # noqa: E402

SOURCE = "https://www.basketball.com.au/news/nbl-injury-report"
SOURCE_NAME = "the league's injury list, as published by basketball.com.au"
SYD = datetime.timezone(datetime.timedelta(hours=11))
CLUBS = ["Adelaide 36ers", "Brisbane Bullets", "Cairns Taipans", "Illawarra Hawks", "Melbourne United",
         "New Zealand Breakers", "Perth Wildcats", "South East Melbourne Phoenix", "Sydney Kings", "Tasmania JackJumpers"]
ALIASES = {"36ers": "Adelaide 36ers", "adelaide": "Adelaide 36ers", "bullets": "Brisbane Bullets", "brisbane": "Brisbane Bullets",
           "taipans": "Cairns Taipans", "cairns": "Cairns Taipans", "hawks": "Illawarra Hawks", "illawarra": "Illawarra Hawks",
           "united": "Melbourne United", "breakers": "New Zealand Breakers", "new zealand": "New Zealand Breakers",
           "nz": "New Zealand Breakers", "wildcats": "Perth Wildcats", "perth": "Perth Wildcats", "phoenix": "South East Melbourne Phoenix",
           "south east": "South East Melbourne Phoenix", "sem": "South East Melbourne Phoenix", "kings": "Sydney Kings",
           "sydney": "Sydney Kings", "jackjumpers": "Tasmania JackJumpers", "tasmania": "Tasmania JackJumpers"}
ROT_MPG, ROT_GP = 10, 3          # "also missing" list: players with 10+ minutes a game over 3+ games


def club(s):
    t = re.sub(r"\s+", " ", (s or "").strip()).lower()
    for c in CLUBS:
        if t == c.lower():
            return c
    for k, v in sorted(ALIASES.items(), key=lambda kv: -len(kv[0])):
        if re.search(r"\b" + re.escape(k) + r"\b", t):
            return v
    return None


def norm(name):
    s = unicodedata.normalize("NFKD", re.sub(r"\(.*?\)", " ", name or "")).encode("ascii", "ignore").decode().lower()
    toks = [t for t in re.sub(r"[^a-z ]", " ", s.replace("-", " ").replace("'", "")).split() if t not in ("jr", "sr", "ii", "iii", "iv")]
    return " ".join(toks)


def parse(text):
    rows, meta = [], {"clubsSeen": None, "clubsMissing": [], "counts": None, "updated": None}
    for line in text.splitlines():
        line = line.strip().strip("`").strip()
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        head = cells[0].upper()
        if head == "CLUBS SEEN":      # the page may be read in two parts (WebFetch offset): add the parts up
            meta["clubsSeen"] = sorted(set(meta["clubsSeen"] or []) | {c for c in (club(x) for x in re.split(r"[;,]", " ".join(cells[1:]))) if c})
        elif head == "CLUBS MISSING":
            meta["clubsMissing"] = sorted(set(meta["clubsMissing"]) | {c for c in (club(x) for x in re.split(r"[;,]", " ".join(cells[1:]))) if c})
        elif head == "COUNTS":            # the first one with the injured count (a second part may say "not present")
            t = " ".join(cells[1:])
            num = lambda pat: (lambda m: int(m.group(1)) if m else None)(re.search(pat, t, re.I))
            c = {"injured": num(r"injured\D{0,6}(\d+)"), "dayToDay": num(r"day.to.day\D{0,6}(\d+)"),
                 "players": num(r"all players\D{0,6}(\d+)"), "text": t[:200]}
            if meta["counts"] is None and c["injured"] is not None:
                meta["counts"] = c
        elif head == "UPDATED":           # likewise the first one that has a date or time in it
            t = " ".join(cells[1:])
            if meta["updated"] is None and re.search(r"\d", t):
                meta["updated"] = t[:240]
        elif head == "CLUB" or set(head) <= set("-: "):
            continue
        elif len(cells) >= 5:
            shown = re.sub(r"\s+", " ", re.sub(r"\(.*?\)", " ", cells[1])).strip() or cells[1]
            rows.append({"clubRaw": cells[0], "team": club(cells[0]), "name": shown, "listed": cells[1], "status": cells[2],
                         "injury": cells[3], "ret": cells[4]})
    return rows, meta


MONTHS = {m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                          "september", "october", "november", "december"])}


def as_of(text, today):
    """The latest real calendar date (not after today) written in the page's last-updated text, or None."""
    best = None
    t = (text or "").lower()
    pats = [(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(MONTHS) + r")\s*,?\s+(\d{4})", (0, 1, 2)),
            (r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s+(\d{4})", (1, 0, 2))]
    for pat, (di, mi, yi) in pats:
        for m in re.finditer(pat, t):
            g = m.groups()
            try:
                d = datetime.date(int(g[yi]), MONTHS[g[mi]], int(g[di]))
            except ValueError:
                continue
            if d <= today and (best is None or d > best):
                best = d
    return best.isoformat() if best else None


def check(rows, meta):
    probs = []
    seen = set(meta["clubsSeen"] or [])
    if not meta["clubsSeen"]:
        probs.append("no CLUBS SEEN line")
    elif seen != set(CLUBS):
        probs.append("clubs not read: " + ", ".join(sorted(set(CLUBS) - seen)))
    gone = set(meta["clubsMissing"]) - seen          # missing from one part but read in the other is fine
    if gone:
        probs.append("clubs reported missing: " + ", ".join(sorted(gone)))
    for r in rows:
        if not r["team"]:
            probs.append(f"unknown club {r['clubRaw']!r}")
        if not r["name"] or not r["status"]:
            probs.append(f"row without player or status: {r}")
    dup = [k for k, n in collections.Counter((r["team"], norm(r["name"])) for r in rows).items() if n > 1]
    if dup:
        probs.append(f"duplicate rows: {dup}")
    c = meta["counts"] or {}
    if c.get("injured") is None:
        probs.append("no COUNTS line with the page's injured count")
    else:
        dtd = sum(1 for r in rows if cls(r) == "dtd")
        if not (len(rows) == c["injured"] or (len(rows) - dtd == c["injured"] and dtd == (c.get("dayToDay") or 0))):
            probs.append(f"{len(rows)} rows read but the page counts {c['injured']} injured"
                         + (f" ({c['dayToDay']} day-to-day)" if c.get("dayToDay") is not None else ""))
    return probs


def cls(r):
    s, ret = (r.get("status") or "").lower(), (r.get("ret") or "").lower()
    if "retire" in s or "retire" in ret or "retired" in (r.get("listed") or r.get("name") or "").lower():
        return "retired"
    if "season" in s or ret.strip() in ("season", "out for the season"):
        return "season"
    if re.search(r"day.?to.?day|questionable|doubtful|probable", s):
        return "dtd"
    return "out"


def box(root):
    """This site's box scores: per team, its games (dates) and each player's games, minutes and points."""
    games = json.load(open(os.path.join(root, "data", "games.json")))
    players = json.load(open(os.path.join(root, "data", "players.json")))
    tg = collections.defaultdict(list)
    for g in games:
        for s in ("home", "away"):
            tg[g[s]["team"]].append(g["date"])
    pl = collections.defaultdict(dict)        # team -> id -> {name, dates, min, pts}
    for d in players:
        for t, L in d["teams"].items():
            for x in L:
                p = pl[t].setdefault(x["id"], {"name": x["name"], "dates": [], "min": 0, "pts": 0, "gp": 0})
                if (x.get("min") or 0) > 0:
                    p["dates"].append(d["date"]); p["min"] += x["min"]; p["pts"] += x.get("pts") or 0; p["gp"] += 1
    return {t: sorted(v) for t, v in tg.items()}, pl


def match(team, name, pl):
    """The player's id in this site's box scores: same club first (exact name, then surname + first initial), then
    any club (exact name only)."""
    n = norm(name)
    for pid, p in pl.get(team, {}).items():
        if norm(p["name"]) == n:
            return team, pid
    toks = n.split()
    if len(toks) >= 2:
        cand = [pid for pid, p in pl.get(team, {}).items()
                if norm(p["name"]).split()[-1:] == toks[-1:] and norm(p["name"])[:1] == toks[0][:1]]
        if len(cand) == 1:
            return team, cand[0]
    for t, P in pl.items():
        for pid, p in P.items():
            if norm(p["name"]) == n:
                return t, pid
    return team, None


def enrich(r, tg, pl):
    t, pid = match(r["team"], r["name"], pl)
    dates = tg.get(r["team"], [])
    out = {k: r[k] for k in ("team", "name", "status", "injury", "ret")}
    if r.get("listed") and r["listed"] != r["name"]:
        out["listed"] = r["listed"]
    out.update(cls=cls(r), id=pid, teamGames=len(dates))
    p = pl.get(t, {}).get(pid) if pid else None
    if p and p["gp"]:
        last = max(p["dates"])
        out.update(gp=p["gp"], mpg=round(p["min"] / p["gp"], 1), ppg=round(p["pts"] / p["gp"], 1), lastPlayed=last,
                   missed=sum(1 for d in dates if d > last), playedLast=bool(dates) and last >= dates[-1])
        if t != r["team"]:
            out["boxTeam"] = t
    else:
        out.update(gp=0, mpg=None, ppg=None, lastPlayed=None, missed=len(dates), playedLast=False)
    m = re.search(r"round\s*(\d+)", r.get("ret") or "", re.I)
    if m:
        out["round"] = int(m.group(1))
    return out


def rounds():
    """First game date of each round, from the NBL's fixture (to show when 'Round 10' is)."""
    try:
        lst = O.rget(f"nbl/matches/in/season/{O.ROSETTA_YEAR}?limit=400", tempfile.mkdtemp(), fresh=True)["data"]
    except Exception:
        return {}
    out = {}
    for m in lst:
        if O.sv(m.get("match_type")).lower() != "regular" or not O.sv(m.get("round")).isdigit():
            continue
        d = datetime.datetime.fromisoformat(O.sv(m.get("start_time"))[:19]).replace(tzinfo=datetime.timezone.utc).astimezone(SYD)
        k = int(O.sv(m.get("round")))
        out[k] = min(out.get(k, "9999"), d.strftime("%Y-%m-%d"))
    return out


def others(listed, tg, pl):
    """Rotation players who didn't play in their team's latest game and aren't on the injury list."""
    out = []
    on = {(r["team"], r["id"]) for r in listed if r.get("id")} | {(r.get("boxTeam"), r["id"]) for r in listed if r.get("id")}
    for t, P in pl.items():
        dates = tg.get(t, [])
        if not dates:
            continue
        for pid, p in P.items():
            if (t, pid) in on or p["gp"] < ROT_GP or p["min"] / p["gp"] < ROT_MPG:
                continue
            last = max(p["dates"])
            if last < dates[-1]:
                out.append({"team": t, "name": p["name"], "id": pid, "gp": p["gp"], "mpg": round(p["min"] / p["gp"], 1),
                            "ppg": round(p["pts"] / p["gp"], 1), "lastPlayed": last, "missed": sum(1 for d in dates if d > last),
                            "teamGames": len(dates)})
    return sorted(out, key=lambda r: (CLUBS.index(r["team"]) if r["team"] in CLUBS else 99, -r["mpg"]))


def write(root, doc):
    json.dump(doc, open(os.path.join(root, "data", "injuries.json"), "w"), separators=(",", ":"), ensure_ascii=False)


def main():
    root = sys.argv[1]
    now = datetime.datetime.now(SYD)
    tg, pl = box(root)
    rd = rounds()
    if sys.argv[2] == "--keep":
        fp = os.path.join(root, "data", "injuries.json")
        if not os.path.exists(fp):
            print("injuries: no previous list to keep"); sys.exit(1)
        prev = json.load(open(fp))
        reason = " ".join(sys.argv[3:]).strip() or "the page could not be read reliably"
        rows = [enrich(r, tg, pl) for r in prev["players"]]
        st = prev["status"]
        st.update(checkedAt=now.isoformat(timespec="seconds"), stale=True,
                  warning=f"Injury list not refreshed on {now.day} {now:%b %Y} ({reason}); showing the list checked on "
                          + ((lambda d: f"{d.day} {d:%b %Y}")(datetime.date.fromisoformat(st["listRead"])) if st.get("listRead") else "an earlier day"))
    else:
        rows_raw, meta = parse(open(sys.argv[2], encoding="utf-8").read())
        probs = check(rows_raw, meta)
        if probs:
            print("injuries: transcription failed the checks, nothing written:\n  " + "\n  ".join(probs))
            sys.exit(3)
        rows = [enrich(r, tg, pl) for r in rows_raw]
        st = {"checkedAt": now.isoformat(timespec="seconds"), "listRead": now.strftime("%Y-%m-%d"), "stale": False,
              "source": SOURCE, "sourceName": SOURCE_NAME, "counts": meta["counts"], "pageUpdated": meta["updated"],
              "asOf": as_of(meta["updated"], now.date())}
    for r in rows:
        if r.get("round") in rd:
            r["roundDate"] = rd[r["round"]]
    st["rows"] = len(rows)
    st["unmatched"] = [f"{r['team']}: {r['name']}" for r in rows if not r.get("id")]
    st["lastGame"] = max((d for v in tg.values() for d in v), default=None)
    order = {"season": 2, "retired": 3, "out": 0, "dtd": 1}
    rows.sort(key=lambda r: (CLUBS.index(r["team"]), order.get(r["cls"], 0), -(r.get("mpg") or 0), r["name"]))
    doc = {"status": st, "players": rows, "others": others(rows, tg, pl)}
    write(root, doc)
    # the tips (ladder.py: who's available) and the matchup facts use the injury list: redo both with this one
    import subprocess
    for script in ("ladder.py", "matchups.py", "recaps.py"):      # recaps.py: only games still without a recap
        r = subprocess.run([sys.executable, "-I", os.path.join(HERE, script), root], capture_output=True, text=True)
        out = (r.stdout or r.stderr).strip()
        print(out.splitlines()[-1][:300] if out else f"{script}: no output")
    by = collections.Counter(r["cls"] for r in rows)
    print(f"injuries: {len(rows)} listed ({by.get('out', 0)} out, {by.get('dtd', 0)} day-to-day, {by.get('season', 0)} for the season, "
          f"{by.get('retired', 0)} retired){' - KEPT FROM ' + str(st.get('listRead')) if st.get('stale') else ''}; "
          f"{len(doc['others'])} more rotation players missed their team's latest game; "
          f"{len(st['unmatched'])} listed players haven't played this season")


if __name__ == "__main__":
    main()
