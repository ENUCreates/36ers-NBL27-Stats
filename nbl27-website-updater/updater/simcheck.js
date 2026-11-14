#!/usr/bin/env node
// Checks for the game simulator (the Simulator tab). Not part of the daily update: run it after changing the engine.
//
//     node simcheck.js <repo_root> [games per check]
//
// The engine is read out of the published page (index.html, between /*SIM-ENGINE-START*/ and /*SIM-ENGINE-END*/),
// so what is checked is what is published. Its tuning constants (PACE_T, BLK_Z, AST_K, REB_K, FTA_K, FG_K, FG3_K,
// USE_EFF, USE_TOV and the home edge) were set from these three checks:
//   1. the season: every game played, re-played with the players who played it and its real starters, against the
//      real team-game averages (possessions, points, shooting, free throws, rebounds, assists, steals, blocks,
//      turnovers, fouls), each player's minutes and points a game, and the home edge (the tip model's, from ladder.json)
//   2. the games to come: win chances and margins (each side's usual rotation, the injury list) against the tips
//   3. sitting each team's three biggest-minute players: the change in margin against what the tip model's
//      availability adjustment says each is worth (availability.py, fitted on 1,458 NBL games)
const fs = require("fs"), path = require("path");
const root = process.argv[2] || ".", N = +process.argv[3] || 20;
const J = f => JSON.parse(fs.readFileSync(path.join(root, "data", f)));
const page = fs.readFileSync(path.join(root, "index.html"), "utf8");
const a = page.indexOf("/*SIM-ENGINE-START*/"), b = page.indexOf("/*SIM-ENGINE-END*/");
if (a < 0 || b < 0) { console.error("simcheck: the engine isn't in index.html"); process.exit(1); }
const SIM = new Function(page.slice(a, b) + ";return SIM;")();
const SINCE = (page.match(/const SEASON_START="([0-9-]+)"/) || [])[1] || "";
const meta = J("meta.json"), P = J("players.json").filter(d => d.date >= SINCE), inj = J("injuries.json").players;
const M = SIM.model({ players: P, pshots: J("pshots.json"), lineups: J("lineups.json"), since: SINCE, basket: meta.pshots_status?.basket });
M.homePts = J("ladder.json").status.homeEdge;          // as the page does: home court = the tip model's home edge
const K = ["pts", "fga", "fgm", "tpa", "tpm", "fta", "ftm", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf"];
const corr = (x, y) => { const mx = x.reduce((s, v) => s + v, 0) / x.length, my = y.reduce((s, v) => s + v, 0) / y.length; let a = 0, b = 0, c = 0; x.forEach((v, i) => { a += (v - mx) * (y[i] - my); b += (v - mx) ** 2; c += (y[i] - my) ** 2 }); return a / Math.sqrt(b * c) };

// 1. the season
{
  const real = Object.fromEntries(K.map(k => [k, 0])), sim = Object.fromEntries(K.map(k => [k, 0]));
  let rg = 0, sg = 0, rp = 0, sp = 0, hm = 0, hn = 0, rm = 0, h0 = 0;const pr = {};
  for (const d of P) {
    const [H, A] = [d.home, d.away];if (!d.teams[H] || !d.teams[A]) continue;
    const pl = [H, A].map(t => { const rows = d.teams[t].filter(r => r.min > 0), ids = new Set(rows.map(r => r.id));
      return SIM.plan(M, t, { out: (M.P[t] || []).filter(p => !ids.has(p.id)).map(p => p.id), start: rows.filter(r => r.starter).map(r => r.id) }) });
    for (const t of [H, A]) { rg++;const s = Object.fromEntries(K.map(k => [k, 0]));
      for (const r of d.teams[t]) { K.forEach(k => s[k] += r[k] || 0);const q = pr[t + "|" + r.id] || (pr[t + "|" + r.id] = { n: r.name, rm: 0, sm: 0, rp: 0, sp: 0, g: 0 });q.rm += r.min || 0;q.rp += r.pts;q.g++ }
      K.forEach(k => real[k] += s[k]);rp += s.fga - s.oreb + s.tov + 0.44 * s.fta }
    rm += d.teams[H].reduce((a, r) => a + r.pts, 0) - d.teams[A].reduce((a, r) => a + r.pts, 0);
    for (let i = 0; i < N; i++) {
      const g = SIM.game(M, H, A, pl, 1000 + i * 7919 + rg, { log: false });hm += g.score[0] - g.score[1];hn++;
      const g0 = SIM.game(M, H, A, pl, 1000 + i * 7919 + rg, { log: false, homeEdge: 0 });h0 += g0.score[0] - g0.score[1];
      g.box.forEach((bx, ti) => { sg++;const s = Object.fromEntries(K.map(k => [k, 0])), t = ti ? A : H;
        for (const r of bx) { K.forEach(k => s[k] += r[k]);const q = pr[t + "|" + r.id];if (q) { q.sm += r.min / N;q.sp += r.pts / N } }
        K.forEach(k => sim[k] += s[k]);sp += s.fga - s.oreb + s.tov + 0.44 * s.fta }) }
  }
  console.log(`1. The season: ${P.length} games, each re-played ${N} times. Team-game averages, real against simulated:`);
  console.log(`   possessions ${(rp / rg).toFixed(1)} / ${(sp / sg).toFixed(1)}`);
  for (const k of K) console.log(`   ${k.padEnd(5)} ${(real[k] / rg).toFixed(1).padStart(6)} / ${(sim[k] / sg).toFixed(1)}`);
  console.log(`   FG% ${(real.fgm / real.fga).toFixed(3)} / ${(sim.fgm / sim.fga).toFixed(3)}, 3P% ${(real.tpm / real.tpa).toFixed(3)} / ${(sim.tpm / sim.tpa).toFixed(3)}, FT% ${(real.ftm / real.fta).toFixed(3)} / ${(sim.ftm / sim.fta).toFixed(3)}`);
  console.log(`   home margin: simulated ${(hm / hn).toFixed(2)}, of which the home edge ${((hm - h0) / hn).toFixed(2)} (tip model ${J("ladder.json").status.homeEdge}); real ${(rm / P.length).toFixed(2)}`);
  const ps = Object.values(pr).filter(p => p.g >= 3);
  console.log(`   ${ps.length} players with 3+ games: minutes a game off by ${(ps.reduce((s, p) => s + Math.abs(p.sm - p.rm) / p.g, 0) / ps.length).toFixed(2)} on average, points by ${(ps.reduce((s, p) => s + Math.abs(p.sp - p.rp) / p.g, 0) / ps.length).toFixed(2)}`);
}
// 2. the games to come
{
  const lad = J("ladder.json"), up = lad.games.filter(g => g.hs == null && g.pHome != null).slice(0, 24), n = Math.max(200, N * 25), xs = [];
  for (const g of up) { const pl = [g.home, g.away].map(t => SIM.plan(M, t, { out: Object.keys(SIM.usualOut(M, t, inj)) }));
    let w = 0, m = 0;for (let i = 0; i < n; i++) { const x = SIM.game(M, g.home, g.away, pl, i * 104729 + 7, { log: false });w += x.score[0] > x.score[1];m += x.score[0] - x.score[1] }
    xs.push([w / n, g.pHome, m / n, g.margin]) }
  console.log(`2. The next ${up.length} games, ${n} times each: win chance off the tip's by ${(100 * xs.reduce((s, x) => s + Math.abs(x[0] - x[1]), 0) / xs.length).toFixed(1)} points on average; margins correlate ${corr(xs.map(x => x[2]), xs.map(x => x[3])).toFixed(2)}`);
}
// 3. sitting a star
{
  const gs = x => x.pts + 0.4 * x.fgm - 0.7 * x.fga - 0.4 * (x.fta - x.ftm) + 0.7 * x.oreb + 0.3 * x.dreb + x.stl + 0.7 * x.ast + 0.7 * x.blk - 0.4 * x.pf - x.tov;
  let lgs = 0, lmin = 0;for (const ps of Object.values(M.P)) for (const p of ps) { lgs += gs(p);lmin += p.min }
  const n = Math.max(300, N * 40), out = [];
  for (const t of M.teams) { const usual = Object.keys(SIM.usualOut(M, t, inj)), opp = M.teams[(M.teams.indexOf(t) + 3) % M.teams.length];
    const po = SIM.plan(M, opp, { out: Object.keys(SIM.usualOut(M, opp, inj)) }), base = SIM.plan(M, t, { out: usual });
    const run = pl => { let m = 0;for (let i = 0; i < n; i++) { const g = SIM.game(M, t, opp, [pl, po], i * 7919 + 11, { log: false });m += g.score[0] - g.score[1] }return m / n };
    const m0 = run(base);
    for (const [p] of base.ps.map((p, i) => [p, base.T[i]]).sort((a, b) => b[1] - a[1]).slice(0, 3)) {
      const w = p.min / (p.min + 200), val = 0.616 * w * (40 * gs(p) / p.min - 40 * lgs / lmin) + 1.654;
      out.push([m0 - run(SIM.plan(M, t, { out: usual.concat([p.id]) })), p.mpg / 40 * val]) } }
  const slope = out.reduce((s, o) => s + o[0] * o[1], 0) / out.reduce((s, o) => s + o[1] * o[1], 0);
  console.log(`3. Sitting ${out.length} players: the simulator's cost ${(out.reduce((s, o) => s + o[0], 0) / out.length).toFixed(2)} points on average, availability.py's ${(out.reduce((s, o) => s + o[1], 0) / out.length).toFixed(2)}; slope ${slope.toFixed(2)}, correlation ${corr(out.map(o => o[0]), out.map(o => o[1])).toFixed(2)}`);
}
