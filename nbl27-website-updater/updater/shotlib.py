"""NBL27 shot-zone builder from ESPN public API.
Usage: python3 -I shots.py <data_dir>   (data_dir gets events.json, sum/<id>.json, out.json)
Frame: ESPN NBL coordinates are feet, x 0..50 across, y feet from basket toward halfway.
Calibrated basket (bx,by)=(25,0) (grid fit vs "N-foot" text, median abs err 0.24 ft over 3265 shots, 31 games).
Both teams share one half-court frame (all y < 47) so no mirroring; the mirror step is kept as a safeguard.
"""
import json, sys, os, re, math, subprocess, datetime, time
from zoneinfo import ZoneInfo

D = sys.argv[1] if len(sys.argv) > 1 else '.'
API = "https://site.api.espn.com/apis/site/v2/sports/basketball/nbl"
TEAMS = [("Phoenix", "South East Melbourne Phoenix"), ("South East", "South East Melbourne Phoenix"),
         ("36ers", "Adelaide 36ers"), ("Adelaide", "Adelaide 36ers"), ("Bullets", "Brisbane Bullets"),
         ("Brisbane", "Brisbane Bullets"), ("Taipans", "Cairns Taipans"), ("Cairns", "Cairns Taipans"),
         ("Hawks", "Illawarra Hawks"), ("Illawarra", "Illawarra Hawks"), ("United", "Melbourne United"),
         ("Breakers", "New Zealand Breakers"), ("New Zealand", "New Zealand Breakers"),
         ("Wildcats", "Perth Wildcats"), ("Perth", "Perth Wildcats"), ("Kings", "Sydney Kings"),
         ("Sydney", "Sydney Kings"), ("JackJumpers", "Tasmania JackJumpers"), ("Tasmania", "Tasmania JackJumpers")]
BX, BY = 25.0, 0.0
RA, KEY_HALF, KEY_DEPTH, C3_DEPTH = 4.10, 8.04, 13.86, 4.64
USE_VOID = True
ZONES = ["ra", "paint", "mid", "c3", "ab3", "ft", "unk"]


def get(url):
    for k in range(5):
        r = subprocess.run(["curl", "-sS", "--compressed", url], capture_output=True, text=True).stdout
        try:
            return json.loads(r)
        except Exception:
            time.sleep(2)
    raise RuntimeError("fetch failed " + url)


def team_name(s):
    for k, v in TEAMS:
        if k.lower() in s.lower():
            return v
    return None


def events(start, end):
    ev = {}
    d = start
    while d <= end:   # range query returns 400, so fetch day by day
        for e in get(f"{API}/scoreboard?dates={d:%Y%m%d}").get('events', []):
            ev[e['id']] = e
        d += datetime.timedelta(1)
    return ev


def classify(p):
    t = p['text'].lower()
    c = p.get('coordinate') or {}
    x, y = c.get('x'), c.get('y')
    ok = x is not None and y is not None and -100 < x < 150 and -100 < y < 150
    if ok and y > 47:      # mirrored frame safeguard
        x, y = 50 - x, 94 - y
    if 'free throw' in t:
        return 'ft', (p.get('scoreValue', 0) > 0 or ' makes ' in f' {t} ')
    made = p.get('scoreValue', 0) > 0 or p.get('scoringPlay', False)
    if 'three point' in t:
        if not ok:
            return 'ab3', made
        return ('c3' if y - BY <= C3_DEPTH else 'ab3'), made
    if not ok:
        if any(k in t for k in ('dunk', 'tip', 'layup', 'lay up', 'alley')):
            return 'ra', made
        return 'unk', made
    d = math.hypot(x - BX, y - BY)
    if d <= RA:
        return 'ra', made
    if abs(x - BX) <= KEY_HALF and y - BY <= KEY_DEPTH:
        return 'paint', made
    return 'mid', made


def game(eid, ev):
    j = get(f"{API}/summary?event={eid}")
    comp = ev['competitions'][0]
    idmap, home, away, score = {}, None, None, {}
    for c in comp['competitors']:
        n = team_name(c['team']['displayName'])
        idmap[c['team']['id']] = n
        score[n] = int(c['score'])
        if c['homeAway'] == 'home': home = n
        else: away = n
    teams = {n: {z: {"m": 0, "a": 0} for z in ZONES} for n in idmap.values()}
    # roster: athlete id -> team (ESPN play team.id is sometimes wrong on orphan plays)
    roster = {}
    for bt in j['boxscore'].get('players', []):
        tn = idmap.get(str(bt['team']['id']))
        for st in bt.get('statistics', []):
            for a in st.get('athletes', []):
                roster[a['athlete']['id']] = tn
    side = {c['homeAway']: team_name(c['team']['displayName']) for c in comp['competitors']}
    plays = j.get('plays', [])
    sc = [(int(p.get('homeScore') or 0), int(p.get('awayScore') or 0)) for p in plays]

    def voided(i):
        # a scoring play whose score rise is reverted at the next score change is a phantom/corrected entry
        if not (plays[i].get('scoreValue', 0) > 0) or i == 0:
            return False
        dh, da = sc[i][0] - sc[i - 1][0], sc[i][1] - sc[i - 1][1]
        for k in range(i + 1, len(plays)):
            if sc[k] != sc[i]:
                return (dh > 0 and sc[k][0] < sc[i][0]) or (da > 0 and sc[k][1] < sc[i][1])
        return False

    prev = (0, 0)
    for i, p in enumerate(plays):
        cur = sc[i]
        dh, da = cur[0] - prev[0], cur[1] - prev[1]
        prev = cur
        if not p.get("shootingPlay") or (USE_VOID and voided(i)):
            continue
        n = None
        parts = p.get('participants') or []
        if parts:
            n = roster.get(parts[0].get('athlete', {}).get('id'))
        if n is None and p.get('scoreValue', 0) > 0:
            if dh > 0 and da == 0: n = side['home']
            elif da > 0 and dh == 0: n = side['away']
        if n is None:
            n = idmap.get(str(p.get('team', {}).get('id')))
        if n is None:
            continue
        z, made = classify(p)
        teams[n][z]['a'] += 1
        teams[n][z]['m'] += int(bool(made))
    probs = []
    for bt in j['boxscore']['teams']:
        n = idmap[str(bt['team']['id'])]
        st = {s['name']: s.get('displayValue') for s in bt['statistics']}
        fgm, fga = map(int, st['fieldGoalsMade-fieldGoalsAttempted'].split('-'))
        tpm, tpa = map(int, st['threePointFieldGoalsMade-threePointFieldGoalsAttempted'].split('-'))
        T = teams[n]
        m = sum(T[z]['m'] for z in ZONES if z != 'ft'); a = sum(T[z]['a'] for z in ZONES if z != 'ft')
        m3 = T['c3']['m'] + T['ab3']['m']; a3 = T['c3']['a'] + T['ab3']['a']
        pts = 2 * (T['ra']['m'] + T['paint']['m'] + T['mid']['m'] + T['unk']['m']) + 3 * m3 + T['ft']['m']
        if (m, a) != (fgm, fga): probs.append(f"{n} FG {m}-{a} vs box {fgm}-{fga}")
        if (m3, a3) != (tpm, tpa): probs.append(f"{n} 3P {m3}-{a3} vs box {tpm}-{tpa}")
        if pts != score[n]: probs.append(f"{n} pts {pts} vs {score[n]}")
    tip = datetime.datetime.fromisoformat(ev['date'].replace('Z', '+00:00')).astimezone(ZoneInfo('Australia/Sydney'))
    doc = {"espnId": eid, "date": tip.strftime('%Y-%m-%d'), "home": home, "away": away, "teams": teams,
           "source": f"https://www.espn.com/nbl/game/_/gameId/{eid}"}
    if probs: doc['mismatch'] = "; ".join(probs)
    return doc


if __name__ == '__main__':
    ev = events(datetime.date(2026, 9, 17), datetime.date.today() + datetime.timedelta(1))
    out = {}
    for eid, e in ev.items():
        c = e['competitions'][0]['competitors']
        if not e['status']['type']['completed'] or any(team_name(x['team']['displayName']) is None for x in c):
            continue
        out[eid] = game(eid, e)
        print(eid, out[eid]['date'], out[eid]['home'], 'v', out[eid]['away'], out[eid].get('mismatch', 'ok'))
    json.dump(out, open(os.path.join(D, 'out.json'), 'w'))
