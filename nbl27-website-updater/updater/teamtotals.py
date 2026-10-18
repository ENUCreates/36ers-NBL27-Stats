"""Official-style NBL team totals from an ESPN summary (site.api.espn.com).

ESPN's team box gives player-only offensive/defensive rebounds and player-only turnovers. The official
(FIBA/NBL) team totals also count TEAM rebounds and TEAM turnovers, so:
  OREB/DREB = player rebounds + ESPN play-by-play team-rebound rows ("X offensive/defensive team rebound"),
              skipping dead-ball team rebounds logged after a missed non-final free throw; DREB is then
              adjusted so OREB+DREB equals ESPN's full total ("teamRebounds" stat, which includes team rebounds).
  TOV       = ESPN "teamTurnovers" stat (player + team turnovers).
Checked against 45 official RealGM team lines: 34 exact, the rest within 1 rebound.
"""
import re


def official_totals(j):
    tid = {bt["team"]["id"]: bt["team"]["displayName"] for bt in j["boxscore"]["teams"]}
    tr = {n: [0, 0] for n in tid.values()}
    prev = None
    for p in j.get("plays", []):
        t = p["type"]["text"]
        if t in ("Offensive Rebound", "Defensive Rebound") and not p.get("participants"):
            dead = False
            if prev and prev["type"]["text"].lower().startswith("free throw"):
                m = re.search(r"(\d) of (\d)", (prev.get("text") or "").lower())
                dead = bool(m and m.group(1) != m.group(2))
            n = tid.get((p.get("team") or {}).get("id"))
            if n and not dead:
                tr[n][0 if t.startswith("Off") else 1] += 1
        if t != "Substitution" and "Foul" not in t:
            prev = p
    out = {}
    for bt in j["boxscore"]["teams"]:
        st = {s["name"]: s["displayValue"] for s in bt["statistics"]}
        n = bt["team"]["displayName"]
        oreb = int(st["offensiveRebounds"]) + tr[n][0]
        dreb = int(st["defensiveRebounds"]) + tr[n][1]
        try:
            total = int(st["teamRebounds"])
            if total >= int(st["totalRebounds"]):
                dreb = total - oreb
        except (KeyError, ValueError):
            pass
        try:
            tov = int(st["teamTurnovers"])
        except (KeyError, ValueError):
            tov = int(st["turnovers"])
        out[n] = {"oreb": oreb, "dreb": dreb, "tov": tov}
    return out
