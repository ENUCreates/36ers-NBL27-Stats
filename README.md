# Adelaide 36ers — NBL27 Advanced Data

Team, player, shot-zone, lineup, rotation and player-card data for the NBL27 regular season.

- `index.html` — the site (served by GitHub Pages).
- `data/` — season data, rebuilt from scratch every morning (8:58am Australia/Sydney) and verified before it is published.
- `nbl27-website-updater/updater/` — the scripts that build `data/` (`rebuild.py` runs everything).

Sources
- ESPN box scores and play-by-play (site.api.espn.com) for every game: team and player stats, shots, lineups, rotations.
- The NBL's official box scores (the league's own API, prod.rosetta.nbl.com.au) — every game is checked against them; a few
  counting stats where ESPN differs (assists, rebounds, steals, blocks, turnovers, team rebound splits) are set to the
  official figure, and player names use the official spelling. Points, shooting, minutes and +/- already match exactly.
- Player cards: official NBL27 roster profiles and headshots (NBL API), earlier NBL regular seasons from 2015-16 averaged
  from the league's official game lines (republished by the nblR project, github.com/JaseZiv/nblr_data), NBA and US college
  seasons, birthplace and college from ESPN.
