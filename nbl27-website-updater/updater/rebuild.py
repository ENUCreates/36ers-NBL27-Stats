"""NBL27 daily FULL REBUILD of the website data from ESPN (maximum-accuracy mode).

Every run rebuilds every NBL27 regular-season game from scratch, so any correction ESPN makes to any game,
however old, is picked up. Steps:
  1. List all completed regular-season NBL-vs-NBL games since 19 Sep 2026 (ESPN scoreboard, day by day).
  2. Skip games that tipped off less than SETTLE_HOURS ago (ESPN keeps correcting stats after the buzzer);
     they are added on the next run.
  3. Download each game's ESPN summary ONCE into a cache, so every step reads the same snapshot.
  4. Build games (official team totals via teamtotals.py + overrides.json), players, shots, lineups, pshots
     and events with the same scripts as before.
  5. Verify each game before publishing: ESPN status final; points = 2*FGM + 3PM + FTM for both teams;
     player lines add up to the team box (FGM/FGA/3PM/3PA/FTM/FTA/PTS). A game that fails is HELD: its
     previously published (already verified) documents are kept and a warning is recorded.
  6. Safety: a previously published game that ESPN no longer lists is kept, with a warning. If fewer than
     90% of the previously published games come back, nothing is written at all.
  7. Write data/*.json + data/meta.json and print a change report (also stored in meta.status.changes).

Usage: python3 rebuild.py <repo_root> [settle_hours=8]
"""
import json, os, sys, tempfile, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shotlib as S  # noqa: E402
import update as U  # noqa: E402

COLS = ["games", "shots", "players", "lineups", "pshots", "events"]
SYD = U.SYD


def key(g):
    return (g["date"], frozenset((g["home"]["team"], g["away"]["team"])))


def apply_overrides(g, eid, ov):
    for side in ("home", "away"):
        o = ov.get(eid, {}).get(g[side]["team"])
        if o:
            for k, v in o.items():
                if k in g[side]:
                    g[side][k] = v


def verify(g, p, j):
    probs = []
    st = j["header"]["competitions"][0].get("status", {}).get("type", {})
    if not st.get("completed"):
        probs.append("ESPN status not final")
    for side in ("home", "away"):
        t = g[side]
        if 2 * t["fgm"] + t["tpm"] + t["ftm"] != t["pts"]:
            probs.append(f"{t['team']} points don't reconcile")
    if p.get("mismatch"):
        probs.append("player lines don't add up: " + p["mismatch"])
    if set(p["teams"]) != {g["home"]["team"], g["away"]["team"]}:
        probs.append("player teams don't match")
    return probs


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    settle = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0
    prev = {c: U.load(root, c) for c in COLS}
    meta = U.load(root, "meta")
    ov = json.load(open(os.path.join(HERE, "overrides.json")))
    ov = {k: v for k, v in ov.items() if not k.startswith("_")}
    now = datetime.datetime.now(SYD)

    evs = S.events(U.SEASON_START, now.date() + datetime.timedelta(1))
    todo, pending = [], []
    for eid, e in sorted(evs.items()):
        if not e["status"]["type"].get("completed") or e.get("season", {}).get("type") != 2:
            continue
        side = {x["homeAway"]: x["team"]["displayName"] for x in e["competitions"][0]["competitors"]}
        if len(side) != 2 or set(side.values()) - U.CLUBS:
            continue
        tip = datetime.datetime.fromisoformat(e["date"].replace("Z", "+00:00")).astimezone(SYD)
        if tip.date() < U.SEASON_START:
            continue
        date = tip.strftime("%Y-%m-%d")
        if now - tip < datetime.timedelta(hours=settle):
            pending.append(f"{date} {side['away']} at {side['home']}")
            continue
        todo.append((eid, e, date, side["home"], side["away"]))

    tmp = tempfile.mkdtemp()
    espn = os.path.join(tmp, "espn"); os.makedirs(espn)
    os.environ["NBL_ESPN_CACHE"] = espn
    db = os.path.join(tmp, "db"); os.makedirs(os.path.join(db, "players")); os.makedirs(os.path.join(db, "shots"))
    new = {c: {} for c in COLS}          # games keyed by (date, pair); others by espnId
    warnings, held = [], []
    prev_games = {key(g): g for g in prev["games"]}
    prev_by = {c: {d.get("espnId"): d for d in prev[c]} for c in COLS[1:]}

    ok_ids = []
    for eid, e, date, home, away in todo:
        try:
            j = U.summary(eid, espn)
            g, err = U.game_doc(eid, j, date)
            if err:
                raise ValueError(err)
            apply_overrides(g, eid, ov)
            p = U.player_doc(eid, j, date, home, away)
            probs = verify(g, p, j)
            if probs:
                raise ValueError("; ".join(probs))
            sd = S.game(eid, e)
        except Exception as ex:
            held.append(f"{date} {away} at {home}: {ex}")
            k = (date, frozenset((home, away)))
            if k in prev_games:
                new["games"][k] = prev_games[k]
                for c in COLS[1:]:
                    if eid in prev_by[c]:
                        new[c][eid] = prev_by[c][eid]
            continue
        new["games"][key(g)] = g
        new["players"][eid] = p
        new["shots"][eid] = sd
        json.dump(p, open(os.path.join(db, "players", f"{eid}.json"), "w"))
        json.dump(sd, open(os.path.join(db, "shots", f"{eid}.json"), "w"))
        ok_ids.append(eid)

    # play-by-play derived collections, built from the same cached snapshots
    if ok_ids:
        for col, script in (("lineups", "lineups.py"), ("events", "events.py")):
            out = os.path.join(tmp, col)
            U.run_script(script, [db, espn, out] + ok_ids)
            for i in ok_ids:
                fp = os.path.join(out, f"{i}.json")
                if os.path.exists(fp):
                    new[col][i] = json.load(open(fp))
                elif i in prev_by[col]:
                    new[col][i] = prev_by[col][i]; warnings.append(f"{i}: {col} not rebuilt, kept previous")
        out = os.path.join(tmp, "pshots")
        U.run_script("pshots.py", [tmp, os.path.join(db, "shots"), os.path.join(db, "players"), out])
        for i in ok_ids:
            fp = os.path.join(out, f"{i}.json")
            if os.path.exists(fp):
                new["pshots"][i] = json.load(open(fp))
            elif i in prev_by["pshots"]:
                new["pshots"][i] = prev_by["pshots"][i]; warnings.append(f"{i}: pshots not rebuilt, kept previous")

    # safety: keep previously published games ESPN no longer lists (unless they are only waiting to settle)
    pend_keys = set()
    for eid, e in evs.items():
        side = {x["homeAway"]: x["team"]["displayName"] for x in e["competitions"][0]["competitors"]}
        tip = datetime.datetime.fromisoformat(e["date"].replace("Z", "+00:00")).astimezone(SYD)
        pend_keys.add((tip.strftime("%Y-%m-%d"), frozenset(side.values())))
    for k, g in prev_games.items():
        if k not in new["games"] and k not in pend_keys:
            new["games"][k] = g
            warnings.append(f"{g['date']} {g['away']['team']} at {g['home']['team']}: not listed by ESPN, kept previous")
    if prev["games"] and len([k for k in prev_games if k in new["games"]]) < 0.9 * len(
            [k for k in prev_games if k not in pend_keys or k in new["games"]]):
        print("ABORT: rebuild lost too many games; nothing written. Held:", held)
        sys.exit(2)

    # change report
    changes = []
    for k, g in new["games"].items():
        if k not in prev_games:
            changes.append(f"added {g['date']} {g['away']['team']} {g['away']['pts']} at {g['home']['team']} {g['home']['pts']}")
            continue
        pg = prev_games[k]
        for side in ("home", "away"):
            t = g[side]
            ps = pg["home"] if pg["home"]["team"] == t["team"] else pg["away"]
            d = {f: (ps.get(f), t[f]) for f in t if f != "team" and ps.get(f) != t[f]}
            if d:
                changes.append(f"corrected {g['date']} {t['team']}: " + ", ".join(f"{f} {a}->{b}" for f, (a, b) in d.items()))
    for k, g in prev_games.items():
        if k not in new["games"]:
            changes.append(f"removed until settled {g['date']} {g['away']['team']} at {g['home']['team']}")
    for c in COLS[1:]:
        n = sum(1 for i, d in new[c].items() if i in prev_by[c] and json.dumps(d.get("teams"), sort_keys=True)
                != json.dumps(prev_by[c][i].get("teams"), sort_keys=True))
        if n:
            changes.append(f"{c}: {n} game(s) updated from ESPN")

    data = {"games": sorted(new["games"].values(), key=lambda g: (g["date"], g["id"]))}
    for c in COLS[1:]:
        data[c] = sorted(new[c].values(), key=lambda d: d.get("espnId", ""))
    nowiso = now.replace(microsecond=0).isoformat()
    st = meta.setdefault("status", {})
    st.update(updatedAt=nowiso, lastGameDate=max(g["date"] for g in data["games"]),
              source="ESPN (official team totals), full rebuild once a day", games=len(data["games"]),
              changes=changes[:60])
    msgs = [f"held (failed checks, previous version kept): {h}" for h in held] + warnings
    if msgs:
        st["warning"] = "; ".join(msgs)
    else:
        st.pop("warning", None)
    if pending:
        st["pending"] = pending
    else:
        st.pop("pending", None)
    st.pop("github_error", None)
    meta.setdefault("shots_status", {}).update(checkedAt=nowiso, games=len(data["shots"]),
                                                mismatches=sum(1 for d in data["shots"] if d.get("mismatch")))
    meta.setdefault("players_status", {}).update(checkedAt=nowiso, games=len(data["players"]),
                                                  mismatches=sum(1 for d in data["players"] if d.get("mismatch")))
    meta.setdefault("lineups_status", {}).update(checkedAt=nowiso, games=len(data["lineups"]),
                                                  invalid=sum(1 for d in data["lineups"] if d.get("invalid")))
    meta.setdefault("pshots_status", {}).update(checkedAt=nowiso, games=len(data["pshots"]))
    bad = [f"{d['espnId']} {t}" for d in data["events"] for t, x in d.get("teams", {}).items() if x.get("ok") is False]
    meta.setdefault("events_status", {}).update(checkedAt=nowiso, games=len(data["events"]), badTeamGames=len(bad),
                                                 notes="Team-games failing reconciliation: " + ", ".join(bad))
    for c in COLS:
        json.dump(data[c], open(os.path.join(root, "data", c + ".json"), "w"), separators=(",", ":"))
    json.dump(meta, open(os.path.join(root, "data", "meta.json"), "w"), indent=1)
    print(json.dumps({"games": len(data["games"]), "held": held, "pending": pending, "warnings": warnings,
                      "changes": changes}, indent=1))


if __name__ == "__main__":
    main()
