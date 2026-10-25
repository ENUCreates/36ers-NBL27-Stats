"""Build updater/overseas.json - the one-off, curated career data that the official NBL and ESPN feeds don't have:
overseas leagues, G League, NBL seasons before 2015-16, and US college / NBA seasons ESPN couldn't link.

It is built once from page extractions saved in overseas/raw/ (October 2026) and is never refreshed by the daily
rebuild: bios.py only merges overseas.json into data/bios.json. Run it again only after adding or fixing raw files:

    python3 build.py <repo_root>        -> writes updater/overseas.json and prints a check report

Raw files (overseas/raw/<ESPN athlete id>.<kind>):
  bbr.jsonl / bbr2.jsonl  Basketball-Reference international page: regular-season Totals (All view), one JSON object per
                          row; bbr2 includes the career row. Two reads of the same page are compared row by row.
  rgm.jsonl               RealGM player Summary page: NBA / G League / NCAA / International season lines (per game).
  rgm2.txt                a second, independent read of the same RealGM page (pipe-separated, with career rows).
  wiki.jsonl              Wikipedia career history (clubs only), used through curated.json.
curated.json: hand-mapped journey stops (club, league, country, season) with the source they were read from; the order
of a season's clubs where a source dates them ('order', shown on the cards through each row's 'seq'); and the school of
a player with no RealGM page ('college').

What is used, and the checks it must pass:
  * Basketball-Reference rows (stats): leagues other than the NBL, and NBL seasons before the official data (2015-16).
    Every row must add up (FG = 2P + 3P, FGA = 2PA + 3PA, TRB = ORB + DRB, PTS = 2*FG + 3P + FT; a row the source itself
    gets wrong is reported, not changed) and all rows must sum to the page's career row. NBL rows from 2015-16 on are only
    compared with the official numbers (reported).
  * RealGM G League rows, and NBA / NCAA rows for seasons ESPN doesn't have (stats, per game): both reads must agree and
    the per-season lines must add up to the career line (games exactly, averages within rounding).
  * RealGM International lines: RealGM folds every competition of a season into one line (preseason and playoffs too).
    A line that is a single league Basketball-Reference doesn't cover is kept as a stats row marked allGames; every
    other club becomes a journey stop without stats.
  * Identity: the date of birth on each page must match ours (a day's difference allowed and reported).
"""
import json, os, re, sys, glob, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
OFFICIAL_FROM = "2015-16"          # first season of the official NBL box scores (historyNBL)

# ---------- leagues ----------
# Basketball-Reference league codes -> (label, country or None when it depends on the club)
BBR_LEAGUE = {"NBL": ("NBL", "AU"), "Liga ACB": ("ACB", "ES"), "ACB": ("ACB", "ES"), "LNB Pro A": ("LNB", "FR"), "LNB Élite": ("LNB", "FR"),
              "LBA": ("LBA", "IT"), "GBL": ("GBL", "GR"), "CBA": ("CBA", "CN"), "ABA": ("ABA", None),
              "EuroLeague": ("EuroLeague", None), "Euroleague": ("EuroLeague", None), "EuroCup": ("EuroCup", None), "Eurocup": ("EuroCup", None), "BSL": ("BSL", "TR"),
              "IPL": ("IBSL", "IL"), "Israeli BSL": ("IBSL", "IL"), "IBSL": ("IBSL", "IL"), "VTB": ("VTB", None)}
BBR_TOURNAMENT = {"FIBA", "WC", "OLY", "Olympics", "World Cup"}
is_tournament = lambda lg: lg in BBR_TOURNAMENT or bool(re.search(r"\b(19|20)\d\d$", lg or ""))
COUNTRY = {"Spain": "ES", "Brazil": "BR", "France": "FR", "Italy": "IT", "Greece": "GR", "Turkey": "TR", "Israel": "IL",
           "China": "CN", "Australia": "AU", "Germany": "DE", "Lithuania": "LT", "Serbia": "RS", "Montenegro": "ME",
           "Russia": "RU", "Slovenia": "SI", "Croatia": "HR", "Bosnia and Herzegovina": "BA", "North Macedonia": "MK",
           "Latvia": "LV", "Poland": "PL", "Belgium": "BE", "Netherlands": "NL", "Monaco": "MC", "Czech Republic": "CZ"}
# seasons Basketball-Reference covers, by label (from its international index)
BBR_FROM = {"ACB": "1983-84", "LBA": "1998-99", "LNB": "2002-03", "GBL": "2001-02", "EuroLeague": "2000-01",
            "EuroCup": "2002-03", "CBA": "2011-12", "NBL": "2011-12", "ABA": "2020-21", "BSL": "2020-21",
            "IBSL": "2020-21", "VTB": "2020-21"}
# RealGM competitions, by the name in the League cell and by the league segment of team links:
#   (label on the cards, country or None when it depends on the club, kind, played inside one calendar year?)
#   kind: dom = a domestic league; cont = a continental club competition; cup = a cup / qualifier (the club's
#   league is found from the rest of the line); youth = junior event (club left out); nbl = the NBL / its preseason
LG = {
    "AUS NBL": ("NBL", "AU", "nbl", False), "Australian-NBL": ("NBL", "AU", "nbl", False),
    "NBL Blitz": ("NBL", "AU", "nbl", False), "NBL-Blitz": ("NBL", "AU", "nbl", False),
    "NZ NBL": ("NZNBL", "NZ", "dom", True), "New-Zealand-NBL": ("NZNBL", "NZ", "dom", True),
    "KLS": ("KLS", "RS", "dom", False), "Serbian-KLS": ("KLS", "RS", "dom", False),
    "HEBA A1": ("GBL", "GR", "dom", False), "Greek-HEBA-A1": ("GBL", "GR", "dom", False),
    "I-BSL": ("IBSL", "IL", "dom", False), "Israeli-BSL": ("IBSL", "IL", "dom", False),
    "CBA": ("CBA", "CN", "dom", False), "Chinese-CBA": ("CBA", "CN", "dom", False),
    "Lega A": ("LBA", "IT", "dom", False), "Italian-Lega-Basket-Serie-A": ("LBA", "IT", "dom", False),
    "BAL": ("BAL", None, "dom", True), "Basketball-Africa-League": ("BAL", None, "dom", True),
    "Liga ABA": ("ABA", None, "dom", False), "Adriatic-League-Liga-ABA": ("ABA", None, "dom", False),
    "PBA CC": ("PBA", "PH", "dom", False), "PBA--Commissioners-Cup": ("PBA", "PH", "dom", False),
    "PBA GC": ("PBA", "PH", "dom", False), "PBA--Governors-Cup": ("PBA", "PH", "dom", False),
    "LEB DivA": ("Lebanon D1", "LB", "dom", False), "Lebanese-Division-A": ("Lebanon D1", "LB", "dom", False),
    "BSN": ("BSN", "PR", "dom", True), "Puerto-Rican-BSN": ("BSN", "PR", "dom", True),
    "G Pro A": ("ProA", "DE", "dom", False), "German-Pro-A": ("ProA", "DE", "dom", False),
    "G Pro B": ("ProB", "DE", "dom", False), "German-Pro-B": ("ProB", "DE", "dom", False),
    "G-BBL": ("BBL", "DE", "dom", False), "German-BBL": ("BBL", "DE", "dom", False),
    "LKL": ("LKL", "LT", "dom", False), "Lithuanian-LKL": ("LKL", "LT", "dom", False),
    "ELBL": ("ELBL", None, "dom", False), "Estonian-Latvian-Basketball-League": ("ELBL", None, "dom", False),
    "Jeep Elite": ("LNB", "FR", "dom", False), "French-Jeep-Elite": ("LNB", "FR", "dom", False),
    "Primera FEB": ("Primera FEB", "ES", "dom", False), "Spanish-Primera-FEB": ("Primera FEB", "ES", "dom", False),
    "CEBL": ("CEBL", "CA", "dom", True), "Canadian-Elite-Basketball-League": ("CEBL", "CA", "dom", True),
    "A Bundeslig": ("Austria BSL", "AT", "dom", False), "Austrian-A-Bundesliga": ("Austria BSL", "AT", "dom", False),
    "LNA": ("SB League", "CH", "dom", False), "Swiss-LNA": ("SB League", "CH", "dom", False),
    "Korisliiga": ("Korisliiga", "FI", "dom", False), "Finnish-Korisliiga": ("Korisliiga", "FI", "dom", False),
    "Ma Superleague": ("Macedonia SL", "MK", "dom", False), "Macedonian-Superleague": ("Macedonia SL", "MK", "dom", False),
    "T-BSL": ("BSL", "TR", "dom", False), "Turkish-BSL": ("BSL", "TR", "dom", False),
    "T-TBL": ("TBL", "TR", "dom", False), "Turkish-TBL": ("TBL", "TR", "dom", False),
    "Extraliga": ("Slovak Extraliga", "SK", "dom", False), "Slovakian-Extraliga": ("Slovak Extraliga", "SK", "dom", False),
    "ACB": ("ACB", "ES", "dom", False), "Spanish-ACB": ("ACB", "ES", "dom", False),
    "P-OBL": ("PLK", "PL", "dom", False), "Polish-OBL": ("PLK", "PL", "dom", False),
    "B.League": ("B.League", "JP", "dom", False), "Japanese-BLeague": ("B.League", "JP", "dom", False),
    "KBL": ("KBL", "KR", "dom", False), "South-Korean-KBL": ("KBL", "KR", "dom", False),
    "B-BBL": ("British BBL", "GB", "dom", False), "British-BBL": ("British BBL", "GB", "dom", False),
    "LNB Pro B": ("LNB Pro B", "FR", "dom", False), "French-LNB-Pro-B": ("LNB Pro B", "FR", "dom", False),
    "BAL - Qual": ("BAL qualifiers", None, "dom", False), "Basketball-Africa-League--Qualification": ("BAL qualifiers", None, "dom", False),
    "Basketball-Champions-League-Europe": ("BCL", None, "cont", False),
    "Leaders Cup LNB B": (None, None, "cup", False), "French-Leaders-Cup-LNB-B": (None, None, "cup", False),
    "Leaders Cup LNB A": (None, None, "cup", False), "French-Leaders-Cup-LNB-A": (None, None, "cup", False),
    "FBC": (None, None, "cup", False), "French-Basketball-Cup": (None, None, "cup", False),
    "Turkish Cup": (None, None, "cup", False), "Spanish Cup": (None, None, "cup", False), "Spanish-Cup": (None, None, "cup", False),
    "Italian Cup": (None, None, "cup", False), "Italian-Cup": (None, None, "cup", False),
    "Intercontinental Cup": ("Intercontinental Cup", None, "cup", False), "Intercontinental-Cup": ("Intercontinental Cup", None, "cup", False),
    "ROM DivA": ("Liga Națională", "RO", "dom", False), "Romanian-Divizia-A": ("Liga Națională", "RO", "dom", False),
    "Euroleague": ("EuroLeague", None, "cont", False), "Eurocup": ("EuroCup", None, "cont", False),
    "BCL-Eu": ("BCL", None, "cont", False), "Europe Cup": ("FIBA Europe Cup", None, "cont", False),
    "FIBA-Europe-Cup": ("FIBA Europe Cup", None, "cont", False),
    "Eurochallenge": ("FIBA EuroChallenge", None, "cont", False),
    "BCL-Eu Qual": (None, None, "cup", False), "Basketball-Champions-League-Europe--Qualification": (None, None, "cup", False),
    "German Cup": (None, None, "cup", False), "German-Cup": (None, None, "cup", False),
    "Lithuanian Citadele": (None, None, "cup", False),
    "ANGT": (None, None, "youth", False), "adidas-Next-Generation-Tournament": (None, None, "youth", False),
    "Kaunas": (None, None, "youth", False), "Kaunas-Tournament-ANGT": (None, None, "youth", False),
    "Belgrade": (None, None, "youth", False), "Belgrade-Tournament-ANGT": (None, None, "youth", False),
    "Costa del Sol": (None, None, "youth", False), "Costa-del-Sol-Tournament-ANGT": (None, None, "youth", False),
    "Ciutat De L'Hospitalet": (None, None, "youth", False), "Ciutat-De-LHospitalet-Tournament-ANGT": (None, None, "youth", False),
}
# club -> country, for leagues that span countries (ABA, ELBL, BAL, VTB) and continental competitions
CLUB_CC = {"Mornar": "ME", "KK Mornar": "ME", "Mornar Bar": "ME", "Bayern": "DE", "Bayern Munich": "DE",
           "Anadolu Efes": "TR", "Al Ahly": "EG", "Lokomotiv Kuban": "RU", "Tartu Ulikool Maks&Moorits": "EE",
           "FC Barcelona": "ES", "Neptūnas Klaipėda": "LT", "Maccabi Tel Aviv": "IL", "Al Ahli Tripoli": "LY",
           "Ratiopharm Ulm": "DE", "Beşiktaş": "TR", "Auxilium Torino": "IT", "Basket Zaragoza": "ES",
           "Tasmania Southern Huskies": "AU", "ASVEL": "FR", "Bangui Sporting Club": "CF",
           "Hapoel Bank Yahav Jerusalem": "IL", "Hapoel Jerusalem": "IL", "Al Ahly Benghazi": "LY", "Al Ittihad Alexandria": "EG",
           "Rigas Zelli": "LV", "Rīgas Zeļļi": "LV", "Budućnost": "ME", "Fenerbahçe": "TR", "Cholet Basket": "FR",
           "Dubai Basketball": "AE", "Dubai": "AE"}
# RealGM club -> its domestic league (League-cell name), for lines read without team links or where the link
# points at a cup or continental competition and the line has more than one domestic league
RGM_CLUB = {"Wellington Saints": "NZ NBL", "Super City Rangers": "NZ NBL", "Franklin Bulls": "NZ NBL",
            "Canterbury Rams": "NZ NBL", "Aukland Tuatara": "NZ NBL", "Auckland Tuatara": "NZ NBL",
            "Manawatu Jets": "NZ NBL", "KK Mega Bemax": "KLS", "ASA Koroivos Amaliadas": "HEBA A1",
            "Ironi Nahariya": "I-BSL", "Zhejiang Guangsha": "CBA", "Virtus Roma": "Lega A", "Al Ahly": "BAL",
            "KK Mornar": "Liga ABA", "Zhejiang Chouzhou": "CBA", "Magnolia Hotshots": "PBA CC",
            "Jilin Northeast": "CBA", "Sagesse Al Hekmeh Beirut": "LEB DivA", "Ningbo Rockets": "CBA",
            "Santeros de Aguada": "BSN", "Capitanes de Arecibo": "BSN", "Guangdong Dongguan": "CBA",
            "Taranaki Mountain Airs": "NZ NBL", "Hawke's Bay Hawks": "NZ NBL", "Art Giants Dusseldorf": "G Pro A",
            "Karpos Sokoli": "Ma Superleague", "Al Ahli Tripoli": "Intercontinental Cup"}
# RealGM club -> [(first season, last season, name, country)] for a club that moved or changed its name; RealGM files
# every season under one name (sources: Wikipedia's club and season pages)
CLUB_ERA = {"Tasmania Southern Huskies": [("2020", "2020", "Auckland Huskies", "NZ")],   # Hobart club played 2020 in Auckland
            "Taranaki Mountain Airs": [("2010", "2021", "Taranaki Mountainairs", "NZ"),  # renamed Taranaki Airs for 2022
                                       ("2022", "9999", "Taranaki Airs", "NZ")],
            # Fraport's name came off the club at the start of 2024-25 (hessenschau.de, 29 Oct 2024)
            "Fraport Skyliners": [("0000", "2023-24", "Fraport Skyliners", "DE"),
                                  ("2024-25", "9999", "Skyliners Frankfurt", "DE")]}
NBL_CLUBS = {"New Zealand", "Adelaide", "Perth", "Brisbane", "Cairns", "Illawarra", "Melbourne", "Sydney", "Tasmania",
             "South East Melbourne", "Wollongong", "Townsville", "Gold Coast"}
RGM_NAME = {"Aukland Tuatara": "Auckland Tuatara", "Art Giants Dusseldorf": "ART Giants Düsseldorf",
            "Sagesse Al Hekmeh Beirut": "Sagesse (Al Hekmeh)", "KK Mega Bemax": "Mega Bemax",
            "ASA Koroivos Amaliadas": "Koroivos Amaliadas", "KK Mornar": "Mornar Bar",
            "7Bet-Lietkabelis Panevezys": "Lietkabelis Panevėžys", "Tartu Ulikool Maks&Moorits": "Tartu Ülikool",
            "Vytautas Prienu": "Vytautas Prienai", "Basketball Lowen Braunschweig": "Basketball Löwen Braunschweig",
            "Champagne Chalons-Reims CB": "Champagne Châlons-Reims", "Neptunas": "Neptūnas Klaipėda",
            "FC Barcelona II": "FC Barcelona B", "Zerofees Southland Sharks": "Southland Sharks",
            "Veolia Towers Hamburg": "Hamburg Towers", "Pinar Karsiyaka": "Pınar Karşıyaka", "Socar Petkimspor": "Petkimspor",
            "Shanxi Zhongyu": "Shanxi Loongs", "Liaoning": "Liaoning Flying Leopards", "Karpos Sokoli": "Karpoš Sokoli",
            "MLP Academics Heidelberg": "MLP Academics Heidelberg", "Shanghai Dongfang": "Shanghai Sharks",
            "Fiat Torino": "Auxilium Torino", "Antibes": "Antibes Sharks", "Baloncesto Fuenlabrada": "Baloncesto Fuenlabrada",
            "MHP Riesen": "MHP Riesen Ludwigsburg", "Indios de Mayaguez": "Indios de Mayagüez", "Casademont Zaragoza": "Basket Zaragoza",
            "Phoenix Hagen": "Phoenix Hagen", "Sporting Al Riyadi Beirut": "Al Riyadi Beirut",
            "LZ Harbour Heat": "Harbour Heat", "Whai": "Whai", "Atleticos de San German": "Atléticos de San Germán",
            "Xinjiang Guanghui": "Xinjiang Flying Tigers", "AS Karditsas": "AS Karditsas",
            "Saint-Quentin Basketball": "Saint-Quentin Basketball", "Besiktas Icrypex": "Beşiktaş",
            "Eisbaren Bremerhaven": "Eisbären Bremerhaven", "Nurnberg Falcons BC": "Nürnberg Falcons",
            "FC Bayern Munchen II": "FC Bayern München II", "Brose Baskets Bamberg": "Brose Bamberg",
            "BG Goettingen": "BG Göttingen", "Hapoel Bank Yahav Jerusalem": "Hapoel Jerusalem",
            "Vaqueros de Bayamon": "Vaqueros de Bayamón", "Osos de Manati": "Osos de Manatí", "Korfez Mansia": "Manisa",
            "Fenerbache Koleji 2nd Team": "Fenerbahçe Koleji", "BC Komarno": "BC Komárno", "Rigas Zelli": "Rīgas Zeļļi",
            "UCAM Murcia CB": "UCAM Murcia", "Lenovo Tenerife": "La Laguna Tenerife",
            "s.Oliver Baskets": "s.Oliver Würzburg", "Chiba Jets": "Chiba Jets Funabashi", "Monbus Obradoiro": "Obradoiro CAB",
            "Mersin BSB": "Mersin MSK", "CSP Limoges": "Limoges CSP", "Pallacanestro Trieste 2004": "Pallacanestro Trieste",
            "Dubai": "Dubai Basketball", "PAOK BC": "PAOK", "Strasbourg IG": "SIG Strasbourg",
            "Bahcesehir Koleji": "Bahçeşehir Koleji"}

# Basketball-Reference team link slug -> club name shown on the cards
BBR_CLUB = {"xinjiang": "Xinjiang Flying Tigers", "anadolu-efes": "Anadolu Efes", "brescia": "Germani Brescia",
            "miraflores": "San Pablo Burgos", "jiangsu-dragons": "Jiangsu Dragons", "bilbao": "Bilbao Basket",
            "gravelines": "BCM Gravelines-Dunkerque", "basquet-girona": "Bàsquet Girona", "bayern-muenchen": "Bayern Munich",
            "koroivos": "Koroivos Amaliadas", "roma": "Virtus Roma", "zhejiang-hangzhou": "Zhejiang Guangsha Lions",
            "mornar": "Mornar Bar", "zhejiang-yiwu": "Zhejiang Golden Bulls", "jilin": "Jilin Northeast Tigers",
            "ningbo": "Ningbo Rockets", "beijing-shougang": "Beijing Ducks", "guangdong": "Guangdong Southern Tigers", "san-sebastian": "Gipuzkoa Basket",
            "cremona": "Vanoli Cremona", "maccabi-ironi-ramat-gan": "Maccabi Ironi Ramat Gan",
            "murcia": "UCAM Murcia", "barcelona": "FC Barcelona", "champagne-basket": "Champagne Châlons-Reims",
            "napoli-basket": "Napoli Basket", "neptunas": "Neptūnas Klaipėda", "maccabi-tel-aviv": "Maccabi Tel Aviv",
            "fuenlabrada": "Baloncesto Fuenlabrada", "shanxi": "Shanxi Loongs", "trieste": "Pallacanestro Trieste",
            "gaziantep": "Gaziantep Basketbol", "tofas": "Tofaş", "turin": "Auxilium Torino", "antibes": "Antibes Sharks",
            "ulm": "Ratiopharm Ulm", "shanghai": "Shanghai Sharks", "liaoning": "Liaoning Flying Leopards",
            "karsiyaka": "Pınar Karşıyaka", "besiktas": "Beşiktaş", "zaragoza": "Basket Zaragoza", "paris-basket": "Paris Basketball",
            "granada": "CB Granada", "mykonos": "Mykonos", "villeurbanne": "ASVEL", "galatasaray": "Galatasaray",
            "jiangsu-tongxi": "Nanjing Tongxi Monkey Kings", "aek-athens": "AEK Athens", "pistoia": "Pistoia Basket",
            "manisa": "Manisa", "hapoel-eilat": "Hapoel Eilat", "le-mans": "Le Mans Sarthe Basket",
            "ulker-fenerbahce": "Fenerbahçe", "buducnost": "Budućnost", "cholet": "Cholet Basket", "joventut": "Joventut Badalona",
            "tenerife": "La Laguna Tenerife", "roanne": "Chorale Roanne Basket", "strasbourg": "SIG Strasbourg",
            "nanterre": "Nanterre 92", "le-havre": "STB Le Havre", "paok": "PAOK", "chalons-en-champagne": "Champagne Châlons-Reims",
            "limoges": "Limoges CSP", "dubai": "Dubai Basketball", "obradoiro": "Obradoiro CAB", "mersin": "Mersin MSK",
            "canarias": "La Laguna Tenerife"}
# NBL clubs before 2015-16 (Basketball-Reference slug -> official name)
BBR_NBL = {"nbl-new-zealand": "New Zealand Breakers", "perth": "Perth Wildcats", "adelaide": "Adelaide 36ers",
           "cairns": "Cairns Taipans", "sydney": "Sydney Kings", "townsville": "Townsville Crocodiles",
           "wollongong": "Wollongong Hawks", "illawarra": "Illawarra Hawks", "gold-coast": "Gold Coast Blaze"}
BBR_NBL_MELB = lambda season: "Melbourne Tigers" if season < "2014-15" else "Melbourne United"

NBA_ABBR = {"UTH": "Utah Jazz", "UTA": "Utah Jazz", "MEM": "Memphis Grizzlies", "PHX": "Phoenix Suns",
            "NOP": "New Orleans Pelicans", "ATL": "Atlanta Hawks", "DAL": "Dallas Mavericks", "NYK": "New York Knicks",
            "WAS": "Washington Wizards"}
G_ABBR = {"AUS": "Austin Spurs", "ERI": "Erie BayHawks", "IWA": "Iowa Wolves", "BIR": "Birmingham Squadron",
          "SLC": "Salt Lake City Stars", "SBL": "South Bay Lakers", "GOG": "Capital City Go-Go"}
JUCO = {"South Georgia Technical College", "Chipola", "Neosho County CC", "Indian Hills"}
# schools RealGM lists without a link whose level its career lines give: school -> [(first season, last season, level)]
COLLEGE_ERA = {"Rocky Mountain": [("0000", "9999", "NAIA")],            # Rocky Mountain College (Billings, MT)
               "Menlo College": [("0000", "2023-24", "NAIA"), ("2024-25", "9999", "D2")],   # NCAA Division II from 2024-25
               "Clarke University": [("0000", "9999", "NAIA")]}                          # Dubuque, Iowa
LEVEL_LABEL = {"D2": "NCAA D2", "JUCO": "NJCAA", "NAIA": "NAIA"}
NCAA_NAME = {"Detroit-Mercy": "Detroit Mercy", "Texas-RGV": "UT Rio Grande Valley", "Montana St.-Billings": "Montana State Billings",
             "Indian Hills": "Indian Hills CC"}   # RealGM short forms


def college_level(nm, href, season):
    if "/ncaa-dii/" in (href or ""):
        return "D2"
    if nm in JUCO:
        return "JUCO"
    for a, z, lv in COLLEGE_ERA.get(nm, []):
        if a <= season <= z:
            return lv
    return "D1"
NBA_FIX = {"Philadelphia-Sixers": "Philadelphia 76ers"}
G_FIX = {"Capital-City-Go-Go": "Capital City Go-Go", "Erie-BayHawks-2008": "Erie BayHawks", "Erie-BayHawks-2019": "Erie BayHawks"}
G_CC = {"Mexico City Capitanes": "MX", "Raptors 905": "CA"}      # G League clubs based outside the US


# ---------- helpers ----------
def season_lbl(s):
    s = re.sub(r"[^0-9-]", "", str(s))
    return s


def bbr_season(s, league):
    """Basketball-Reference labels the NBL's 2020-21 season '2021'."""
    s = season_lbl(s)
    if re.fullmatch(r"\d{4}", s) and league == "NBL":
        y = int(s); return f"{y - 1}-{str(y)[2:]}"
    return s


def cal_year(s):
    """RealGM files a calendar-year league season Y (e.g. NZNBL 2024) under '(Y-1)-Y'."""
    m = re.fullmatch(r"(\d{4})-(\d{2})", s)
    return str(int(m.group(1)) + 1) if m else s


def num(x):
    if x in (None, "", "-", "—"):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    x = str(x).strip().rstrip("%")
    try:
        v = float(x)
    except ValueError:
        return None
    return v


def pct(x):
    """'.420' / 0.42 / '42.0%' -> 0.42"""
    if x in (None, "", "-"):
        return None
    if isinstance(x, str) and x.strip().endswith("%"):
        return round(float(x.strip()[:-1]) / 100, 4)
    v = num(x)
    return None if v is None else round(v, 4)


def parse_date(s):
    if not s:
        return None
    s = re.split(r"\s+in\s+", str(s))[0].strip()
    for f in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y"):
        try:
            return datetime.datetime.strptime(s, f).date()
        except ValueError:
            pass
    return None


def jsonl(fp):
    out = []
    for ln in open(fp, encoding="utf-8"):
        ln = ln.strip()
        if ln:
            out.append(json.loads(ln))
    return out


BBR_NUM = ("g", "gs", "mp", "fg", "fga", "tp", "tpa", "twop", "twopa", "ft", "fta", "orb", "drb", "trb", "ast", "stl", "blk", "tov", "pf", "pts")


def load_bbr(fp):
    meta, head, rows, career, trunc = {}, {}, [], None, None
    for d in jsonl(fp):
        for k in BBR_NUM:                        # some reads give the numbers as text ("6"): same values, as numbers
            v = d.get(k)
            if isinstance(v, str) and re.fullmatch(r"-?\d+(\.\d+)?", v.strip()):
                d[k] = float(v) if "." in v else int(v)
        if "_meta" in d:
            meta = d["_meta"]
        elif "truncated" in d and len(d) <= 2:
            trunc = d["truncated"]
        elif d.get("career") is True or re.search(r"seasons?$", str(d.get("season", "")), re.I):
            career = d
        elif "season" in d:
            rows.append(d)
        else:
            head.update(d)
    return {"meta": meta, "head": head, "rows": rows, "career": career, "truncated": trunc}


def load_rgm(fp):
    meta, head, rows, trunc = {}, {}, [], False
    for d in jsonl(fp):
        if "_meta" in d:
            meta = d["_meta"]
        elif "tbl" in d:
            rows.append(d)
        elif "truncated" in d:
            trunc = bool(d["truncated"])
        else:
            head.update(d)
    rows = [r for r in rows if not r.get("cut")]
    is_tot = lambda r: r.get("total") or any((t.get("name") if isinstance(t, dict) else t) == "All Teams" for t in (r.get("teams") or []))
    return {"meta": meta, "head": head, "rows": [r for r in rows if not is_tot(r)], "totals": [r for r in rows if is_tot(r)],
            "truncated": trunc}


TBL = {"NBA Regular Season Stats": "NBA", "G League Regular Season Stats": "G", "NCAA Season Stats": "NCAA",
       "International Season Stats": "INT"}
COLS = ["season", "team", "league", "gp", "min", "pts", "reb", "ast", "stl", "blk", "tov", "fgp", "tpp", "ftp"]


def load_rgm2(fp):
    """Pipe-separated second read -> {tbl: {"rows": [...], "career": [...]}}; a table followed by CUT is incomplete."""
    meta, tbls, cur = {}, {}, None
    for ln in open(fp, encoding="utf-8"):
        ln = ln.rstrip("\n")
        if ln.startswith("#"):
            k, _, v = ln[1:].partition(" "); meta[k] = v; continue
        if not ln.strip():
            continue
        ln = ln.strip()
        if ln.startswith("|") and ln.endswith("|"):            # a markdown table row
            ln = ln[1:-1]
        title = re.sub(r"\s*(\(Per Game\)|- Per Game)$", "", ln.strip())
        if title in TBL:
            cur = TBL[title]; tbls[cur] = {"rows": [], "career": [], "totals": [], "cut": False}; continue
        if ln.strip() == "CUT":
            if cur:
                tbls[cur]["cut"] = True
            continue
        cells = [c.strip() for c in ln.split("|")]
        if cur is None or len(cells) != len(COLS) or cells[0].lower() == "season" or re.fullmatch(r":?-{3,}:?", cells[0]):
            continue                                   # not a table row, or the column header
        d = dict(zip(COLS, cells))
        car = d["season"].upper().startswith("CAREER") or d["season"] in ("-", "")   # RealGM sometimes labels it '-'
        tot = d["team"] == "All Teams"       # a season total; the rows under it are per team and league
        (tbls[cur]["career"] if car else tbls[cur]["totals"] if tot else tbls[cur]["rows"]).append(d)
    return meta, tbls


# ---------- main ----------
def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    bios = {b["id"]: b for b in json.load(open(os.path.join(root, "data", "bios.json")))}
    curated = {k: v for k, v in json.load(open(os.path.join(HERE, "curated.json"))).items() if not k.startswith("_")}
    pids = sorted({os.path.basename(f).split(".")[0] for f in glob.glob(os.path.join(RAW, "*"))} | set(curated))
    out, report = {}, []
    for pid in pids:
        b = bios.get(pid)
        if not b:
            report.append(f"{pid}: not an NBL27 player in data/bios.json - skipped"); continue
        name, dob = b.get("name"), b.get("dob")
        rep = lambda m: report.append(f"{name}: {m}")
        f = lambda kind: os.path.join(RAW, f"{pid}.{kind}")
        rows, stints, college, sources = [], [], None, {}
        ok_ident = True
        # ----- identity -----
        for kind in ("bbr2.jsonl", "bbr.jsonl", "rgm.jsonl", "wiki.jsonl"):
            if not os.path.exists(f(kind)):
                continue
            head = {}
            for d in jsonl(f(kind)):
                if "born" in d:
                    head = d; break
            bd = parse_date(head.get("born"))
            why = (curated.get(pid) or {}).get("identity")
            if bd and dob and why and abs((bd - datetime.date.fromisoformat(dob)).days) > 1:
                rep(f"{kind}: born {bd}, ours {dob} - same player per curated note: {why}")
                continue
            if bd and dob:
                gap = abs((bd - datetime.date.fromisoformat(dob)).days)
                if gap > 1:
                    rep(f"{kind}: born {bd} but ours is {dob} - this page is not used"); ok_ident = False
                elif gap == 1:
                    rep(f"{kind}: born {bd}, ours {dob} (a day apart) - same player")
        if not ok_ident:
            continue
        drop_ = set((curated.get(pid) or {}).get("espnDrop") or [])        # ESPN lines left out on the cards (see curated.json)
        espn = {(h.get("league"), h.get("season")) for h in b.get("history") or [] if f"{h.get('season')}|{h.get('league')}" not in drop_}
        official = {h.get("season") for h in b.get("historyNBL") or []}

        # ----- Basketball-Reference -----
        reads = [load_bbr(f(k)) for k in ("bbr2.jsonl", "bbr.jsonl") if os.path.exists(f(k))]
        bbr_keys = set()
        if reads:
            main_ = reads[0]
            url = main_["meta"].get("url"); sources["bbr"] = url
            R = main_["rows"]
            car = main_["career"]
            usable = [r for r in R if not is_tournament(r.get("league")) and not (r.get("league") == "NBL" and bbr_season(r["season"], "NBL") >= OFFICIAL_FROM)]
            fields = ["g", "mp", "fg", "fga", "tp", "tpa", "twop", "twopa", "ft", "fta", "orb", "drb", "trb", "ast", "stl", "blk", "tov", "pf", "pts"]
            good = True
            if car:
                bad = [k for k in fields if car.get(k) is not None and sum(int(r.get(k) or 0) for r in R) != int(car[k])]
                if bad:
                    rep(f"Basketball-Reference rows don't sum to the career line ({', '.join(bad)}) - not used"); good = False
            elif usable:
                rep("Basketball-Reference read has no career line - rows checked only against the second read")
            if len(reads) > 1:
                other = {(r["season"], r["team"], r["league"]): r for r in reads[1]["rows"]}
                for r in R:
                    o = other.get((r["season"], r["team"], r["league"]))
                    if o is None:
                        if main_["career"] and reads[1]["meta"].get("view") == "league" and is_tournament(r["league"]):
                            continue
                        rep(f"BBR {r['season']} {r['team']} {r['league']}: only in one read" + (" (career line confirms it)" if car and good else ""))
                        continue
                    diff = [k for k in fields if o.get(k) is not None and r.get(k) != o.get(k)]
                    if diff and r in usable:
                        rep(f"BBR {r['season']} {r['team']}: the two reads differ on {diff} - not used"); good = False
                    elif diff:
                        rep(f"BBR {r['season']} {r['team']} {r['league']}: the two reads differ on {diff} (a row that isn't used; "
                            f"the read used matches the career line)" if good and car else
                            f"BBR {r['season']} {r['team']}: the two reads differ on {diff}")
            elif not car:
                good = False
                if usable:
                    rep("Basketball-Reference: one read and no career line - not used")
            if good:
                for r in R:
                    lg = r.get("league")
                    if is_tournament(lg):
                        continue
                    if lg not in BBR_LEAGUE:
                        rep(f"BBR league {lg!r} not mapped - row skipped"); continue
                    label, cc = BBR_LEAGUE[lg]
                    season = bbr_season(r["season"], lg)
                    slug = (re.search(r"/teams/([^/]+)/", r.get("team_href") or "") or [None, None])[1]
                    arith = []
                    if r["fg"] != r["tp"] + r["twop"]: arith.append("FG")
                    if r["fga"] != r["tpa"] + r["twopa"]: arith.append("FGA")
                    if r["trb"] != r["orb"] + r["drb"]: arith.append("TRB")
                    if r["pts"] != 2 * r["fg"] + r["tp"] + r["ft"]: arith.append(f"PTS ({r['pts']} vs {2 * r['fg'] + r['tp'] + r['ft']})")
                    if label == "NBL":
                        team = BBR_NBL.get(slug) or (BBR_NBL_MELB(season) if slug == "melbourne" else None)
                        if season >= OFFICIAL_FROM:
                            continue            # official NBL data covers it (compared separately)
                        if not team:
                            rep(f"BBR NBL club {slug!r} not mapped - row skipped"); continue
                        cc = "NZ" if team == "New Zealand Breakers" else "AU"
                    else:
                        team = BBR_CLUB.get(slug) or r["team"]
                        if slug not in BBR_CLUB:
                            rep(f"BBR club slug {slug!r} not in BBR_CLUB - showing {r['team']!r}")
                        rc = (r.get("country") or "").strip()
                        rc = rc.upper() if len(rc) == 2 and rc.lower() != "eu" else COUNTRY.get(rc)
                        cc = cc or CLUB_CC.get(team) or CLUB_CC.get(r["team"]) or rc
                        if not cc:
                            rep(f"BBR {team}: no country - row skipped"); continue
                    if arith:
                        rep(f"BBR {season} {team}: the source's own totals don't add up ({', '.join(arith)}) - shown as published")
                    g = r["g"]
                    if not g:
                        continue
                    p = lambda a, b_: round(a / b_, 4) if b_ else None
                    rows.append({"season": season, "team": team, "league": label, "country": cc, "gp": g, "gs": None,
                                 "mpg": round(r["mp"] / g, 1), "pts": round(r["pts"] / g, 1), "reb": round(r["trb"] / g, 1),
                                 "oreb": round(r["orb"] / g, 1), "dreb": round(r["drb"] / g, 1), "ast": round(r["ast"] / g, 1),
                                 "stl": round(r["stl"] / g, 1), "blk": round(r["blk"] / g, 1), "tov": round(r["tov"] / g, 1),
                                 "fgm": round(r["fg"] / g, 1), "fga": round(r["fga"] / g, 1), "fgp": p(r["fg"], r["fga"]),
                                 "tpm": round(r["tp"] / g, 1), "tpa": round(r["tpa"] / g, 1), "tpp": p(r["tp"], r["tpa"]),
                                 "ftm": round(r["ft"] / g, 1), "fta": round(r["fta"] / g, 1), "ftp": p(r["ft"], r["fta"]),
                                 "src": "BBR", "url": url,
                                 "tot": {"sec": r["mp"] * 60, "pts": r["pts"], "fgm": r["fg"], "fga": r["fga"], "tpm": r["tp"],
                                         "tpa": r["tpa"], "ftm": r["ft"], "fta": r["fta"], "oreb": r["orb"], "dreb": r["drb"],
                                         "ast": r["ast"], "stl": r["stl"], "blk": r["blk"], "tov": r["tov"]}})
                    bbr_keys.add((season, label))
            # official comparison (report only)
            offrows = {}
            for h in b.get("historyNBL") or []:
                offrows.setdefault(h["season"], []).append(h)
            bseas = {}
            for r in R:
                if r.get("league") == "NBL":
                    bseas.setdefault(bbr_season(r["season"], "NBL"), []).append(r)
            for s, rs in sorted(bseas.items()):
                if s < OFFICIAL_FROM or s not in offrows:
                    continue
                t = {k: sum((h.get("tot") or {}).get(k, 0) for h in offrows[s]) for k in ("pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "ast")}
                gp = sum(h["gp"] for h in offrows[s])
                bt = {k: sum(r[bk] for r in rs) for k, bk in (("pts", "pts"), ("fgm", "fg"), ("fga", "fga"), ("tpm", "tp"), ("tpa", "tpa"), ("ftm", "ft"), ("fta", "fta"), ("ast", "ast"), ("gp", "g"))}
                d = {k: (bt[k], t[k] if k != "gp" else gp) for k in bt if bt[k] != (t[k] if k != "gp" else gp)}
                if d:
                    rep(f"note: BBR NBL {s} vs official (BBR, official): {d}")

        # ----- RealGM -----
        A = load_rgm(f("rgm.jsonl")) if os.path.exists(f("rgm.jsonl")) else None
        B_meta, B = load_rgm2(f("rgm2.txt")) if os.path.exists(f("rgm2.txt")) else ({}, {})
        if A:
            sources["realgm"] = A["meta"].get("url")
            if A["head"].get("school"):
                college = A["head"]["school"]
            # per-game tables: NBA (only seasons ESPN lacks), G League, NCAA (only seasons ESPN lacks)
            for tbl, league in (("NBA", "NBA"), ("G", "G League"), ("NCAA", "NCAA")):
                ra = [r for r in A["rows"] if r["tbl"] == tbl and num(r.get("gp"))
                      and not (league in ("NBA", "NCAA") and (league, season_lbl(r["season"])) in espn)]
                if not ra:
                    continue
                tb = B.get(tbl)
                if not tb:
                    rep(f"RealGM {tbl}: no second read - not used"); continue
                rb = {}
                for r in tb["rows"]:
                    rb.setdefault(season_lbl(r["season"]), []).append(r)
                okt = True
                for r in ra:
                    cands = rb.get(season_lbl(r["season"])) or []
                    if len(cands) > 1:                    # several teams in one season: match the team too
                        tn = ", ".join((t.get("name") if isinstance(t, dict) else t) or "" for t in r.get("teams") or [])
                        cands = [x for x in cands if (x.get("team") or "") == tn]
                    o = cands[0] if len(cands) == 1 else None
                    if not o:
                        rep(f"RealGM {tbl} {r['season']}: missing from the second read - table not used"); okt = False; continue
                    for k in ("gp", "min", "pts", "reb", "ast", "stl", "blk", "tov"):
                        if num(r.get(k)) != num(o.get(k)):
                            rep(f"RealGM {tbl} {r['season']}: reads differ on {k} ({r.get(k)} vs {o.get(k)}) - table not used"); okt = False
                    for k in ("fgp", "tpp", "ftp"):
                        if pct(r.get(k)) != pct(o.get(k)):
                            rep(f"RealGM {tbl} {r['season']}: reads differ on {k} - table not used"); okt = False
                # career lines: games exact, averages within rounding. College tables have one career line per
                # level (NCAA DI, NJCAA ..., sometimes none for Division II), so each level is checked on its own.
                lines = [r for r in tb["rows"] if num(r["gp"])]
                groups = {"": lines}
                if tbl == "NCAA" and len(tb["career"]) > 1:
                    lvl_of = {}
                    for r in A["rows"]:
                        if r["tbl"] != "NCAA":
                            continue
                        t0 = (r.get("teams") or [{}])[0]
                        nm, href = (t0.get("name"), t0.get("href") or "") if isinstance(t0, dict) else (t0, "")
                        lvl_of[season_lbl(r["season"])] = college_level(nm, href, season_lbl(r["season"]))
                    groups = {}
                    for r in lines:
                        groups.setdefault(lvl_of.get(season_lbl(r["season"]), "D1"), []).append(r)
                    cars = {}
                    for c in tb["career"]:
                        t = " ".join(x for x in (c.get("team"), c.get("league")) if x and x != "-").upper()
                        cars["JUCO" if "NJCAA" in t else "NAIA" if "NAIA" in t else "D2" if ("NCAA DII" in t or "NCAA D2" in t) else "D1"] = [c]
                    for g in groups:
                        if g not in cars:
                            rep(f"RealGM NCAA: no career line for the {g} seasons - those rows rest on the two reads only")
                    careers = cars
                else:
                    careers = {"": tb["career"]} if tb["career"] else {}
                for g, cl in careers.items():
                    rows_g = groups.get(g, [])
                    tot_gp = sum(num(r["gp"]) for r in rows_g)
                    car_gp = sum(num(c["gp"]) or 0 for c in cl)
                    if len(cl) == 1 and tot_gp > car_gp:
                        # a career line that stops before the latest season(s): check it against the seasons it covers
                        srt = sorted(rows_g, key=lambda r: season_lbl(r["season"]))
                        k_ = next((k for k in range(1, len(srt)) if sum(num(r["gp"]) for r in srt[:k]) == car_gp), None)
                        if k_:
                            rep(f"RealGM {tbl}{' ' + g if g else ''}: the career line leaves out {', '.join(season_lbl(r['season']) for r in srt[k_:])} - "
                                f"checked against the seasons before; those rows rest on the two reads")
                            rows_g, tot_gp = srt[:k_], car_gp
                    if len(cl) != 1 or car_gp != tot_gp:
                        rep(f"RealGM {tbl}{' ' + g if g else ''}: seasons add up to {tot_gp:g} games, career line says {car_gp:g} - table not used"); okt = False
                        continue
                    for k in ("min", "pts", "reb", "ast"):
                        w = sum(num(r["gp"]) * num(r[k]) for r in rows_g) / tot_gp
                        if abs(w - num(cl[0][k])) > 0.1:
                            rep(f"RealGM {tbl}{' ' + g if g else ''}: {k} seasons average {w:.2f}, career line {cl[0][k]} - table not used"); okt = False
                if not careers and not tb["cut"]:
                    rep(f"RealGM {tbl}: no career line in the second read")
                if not okt:
                    continue
                for r in ra:
                    s = season_lbl(r["season"])
                    teams = r.get("teams") or []
                    names = []
                    lvl = league
                    for t in teams:
                        if isinstance(t, dict):
                            nm, href = t.get("name"), t.get("href") or ""
                        else:
                            nm, href = t, ""
                        if tbl == "NBA":
                            m = re.search(r"/nba/teams/([^/]+)/", href)
                            nm = NBA_FIX.get(m.group(1), m.group(1).replace("-", " ")) if m and m.group(1) != "N-A" else ", ".join(NBA_ABBR.get(x.strip(), x.strip()) for x in nm.split(","))
                        elif tbl == "G":
                            m = re.search(r"/gleague/teams/([^/]+)/", href)
                            nm = G_FIX.get(m.group(1), m.group(1).replace("-", " ")) if m else ", ".join(G_ABBR.get(x.strip(), x.strip()) for x in nm.split(","))
                            if nm == "Cleveland Charge" and s < "2021-22":
                                nm = "Canton Charge"          # the club's name until 2021
                        else:
                            lv = college_level(nm, href, s)
                            if lv != "D1":
                                lvl = LEVEL_LABEL[lv]
                            elif isinstance(t, dict) and t.get("href") is None:
                                rep(f"RealGM NCAA {s}: {nm!r} has no link (not NCAA Division I?) - add it to JUCO or COLLEGE_ERA, or skip")
                            nm = NCAA_NAME.get(nm, nm)
                        if tbl == "G" and (not nm or (isinstance(t, dict) and "/teams/-/" in (t.get("href") or ""))):
                            continue                      # the empty 'several teams' entry RealGM puts first
                        names.append(nm)
                    if any(n in (None, "") for n in names) or not names:
                        rep(f"RealGM {tbl} {s}: no team name - row skipped"); continue
                    team = " / ".join(n.replace(", ", " / ") for n in names)
                    gp = int(num(r["gp"]))
                    rows.append({"season": s, "team": team, "league": lvl, "country": G_CC.get(team, "US") if tbl == "G" else "US", "gp": gp, "gs": None,
                                 "mpg": num(r.get("min")), "pts": num(r.get("pts")), "reb": num(r.get("reb")), "oreb": None,
                                 "dreb": None, "ast": num(r.get("ast")), "stl": num(r.get("stl")), "blk": num(r.get("blk")),
                                 "tov": num(r.get("tov")), "fgm": None, "fga": None, "fgp": pct(r.get("fgp")), "tpm": None,
                                 "tpa": None, "tpp": pct(r.get("tpp")), "ftm": None, "fta": None, "ftp": pct(r.get("ftp")),
                                 "src": "RealGM", "url": A["meta"].get("url"), **({"school": team} if lvl.startswith("N") and tbl == "NCAA" else {})})
            # International lines
            ints = [r for r in A["rows"] if r["tbl"] == "INT"]
            if not reads:
                need = sorted({season_lbl(r["season"]) for r in ints if any(x in ("AUS NBL",) for x in (r.get("leagues") or []))
                               and season_lbl(r["season"]) < OFFICIAL_FROM})
                cov = sorted({f"{season_lbl(r['season'])} {LG[x][0]}" for r in ints for x in (r.get("leagues") or [])
                              if x in LG and LG[x][0] in BBR_FROM and LG[x][0] != "NBL" and season_lbl(r["season"]) >= BBR_FROM[LG[x][0]]})
                nob = (curated.get(pid) or {}).get("noBBR")
                if (need or cov) and nob:
                    rep(f"no Basketball-Reference page ({nob}) - {', '.join(need + cov)}: RealGM's line where both reads agree, otherwise club only")
                elif need or cov:
                    rep(f"NEEDS Basketball-Reference: {', '.join(need + cov)}")
            if A.get("truncated") and not os.path.exists(f("wiki.jsonl")) and pid not in curated:
                rep("NEEDS a club list (RealGM page cut off before the International table)")
            tb = B.get("INT") or {"rows": [], "totals": []}
            bints = {}
            for x in tb["rows"]:
                lgs, tms = [y.strip() for y in str(x.get("league") or "").split(",")], [y.strip() for y in str(x.get("team") or "").split(",")]
                if len(lgs) == 1 and len(tms) == 1:
                    bints.setdefault((season_lbl(x["season"]), lgs[0], tms[0]), []).append(x)
            tname = lambda r: [(t.get("name") if isinstance(t, dict) else t) for t in (r.get("teams") or [])]
            # seasons split into per-league rows must add up to the season total (a missed row would show here)
            tots = {season_lbl(t["season"]): t for t in (A.get("totals") or []) + (tb.get("totals") or [])}
            for ts, t in sorted(tots.items()):
                part = [r for r in ints if season_lbl(r["season"]) == ts]
                if len(part) > 1 and sum(num(r.get("gp")) or 0 for r in part) != num(t.get("gp")):
                    rep(f"RealGM INT {ts}: per-league rows add up to {sum(num(r.get('gp')) or 0 for r in part):g} games, the season total says {t.get('gp')}")
            dom_pairs = {(season_lbl(r["season"]), tname(r)[0]) for r in ints if len(r.get("leagues") or []) == 1
                         and len(tname(r)) == 1 and LG.get((r["leagues"][0] or "").strip(), (0, 0, ""))[2] == "dom"}
            url = A["meta"].get("url")
            for r in ints:
                s = season_lbl(r["season"])
                leagues = [x.strip() for x in (r.get("leagues") or [])]
                unknown = [x for x in leagues if x not in LG]
                if unknown:
                    rep(f"RealGM INT {s}: competition(s) {unknown} not mapped - line skipped"); continue
                if len(leagues) == 1 and LG[leagues[0]][2] in ("cont", "cup", "youth") and len(tname(r)) == 1 and (s, tname(r)[0]) in dom_pairs:
                    continue                              # a per-league row for a club whose league row is there too
                doms = [x for x in leagues if LG[x][2] == "dom"]
                clubs = []
                for i, t in enumerate(r.get("teams") or []):
                    if isinstance(t, dict):
                        nm, href = t.get("name"), t.get("href") or ""
                        m = re.search(r"/international/league/\d+/([^/]+)/team/", href)
                        slug = m.group(1) if m else None
                    else:
                        nm = t
                        tl = r.get("team_leagues") or []
                        slug = tl[i] if i < len(tl) else None
                    if nm in NBL_CLUBS:
                        continue
                    kind = LG[slug][2] if slug in LG else None
                    if slug and slug not in LG:
                        rep(f"RealGM INT {s}: league link {slug!r} not mapped")
                    if kind in ("nbl", "youth"):
                        continue
                    if kind == "dom":
                        lg = slug
                    elif len([d for d in doms if LG[d][2] == "dom"]) == 1:
                        lg = doms[0]
                    else:
                        lg = RGM_CLUB.get(nm) or (leagues[0] if len(leagues) == 1 and LG[leagues[0]][2] == "cont" else None)
                    if not lg:
                        rep(f"RealGM INT {s}: club {nm!r} has no league - left out"); continue
                    clubs.append((nm, lg))
                if not clubs:
                    continue
                single = len(leagues) == 1 and LG[leagues[0]][2] == "dom" and len(clubs) == 1
                for nm, lg in clubs:
                    label, cc, kind, cal = LG[lg]
                    if label == "Primera FEB" and s < "2023-24":
                        label = "LEB Oro"
                    disp = RGM_NAME.get(nm, nm)
                    cc = CLUB_CC.get(nm) or CLUB_CC.get(disp) or cc     # a club playing in another country's league
                    ss = cal_year(s) if cal else s
                    for a, z, nm2, cc2 in CLUB_ERA.get(nm, []):             # a club that moved or played under another name
                        if a <= ss <= z:
                            disp, cc = nm2, cc2
                    if not cc:
                        rep(f"RealGM {disp}: no country - left out"); continue
                    covered = label in BBR_FROM and ss >= BBR_FROM[label]
                    if (ss, label) in bbr_keys:
                        continue                                  # Basketball-Reference has the stats
                    # a league Basketball-Reference covers: its page is read first; RealGM's line stands in only when
                    # that page has no row for the season (or no page could be found)
                    if single and (not covered or reads or (curated.get(pid) or {}).get("noBBR")):
                        os_ = bints.get((s, leagues[0], nm)) or []
                        o = os_[0] if len(os_) == 1 else None
                        same = o and all(num(o.get(k)) == num(r.get(k)) for k in ("gp", "min", "pts", "reb", "ast", "stl", "blk", "tov")) \
                            and all(pct(o.get(k)) == pct(r.get(k)) for k in ("fgp", "tpp", "ftp"))
                        if same:
                            rows.append({"season": ss, "team": disp, "league": label, "country": cc, "gp": int(num(r["gp"])), "gs": None,
                                         "mpg": num(r.get("min")), "pts": num(r.get("pts")), "reb": num(r.get("reb")), "oreb": None, "dreb": None,
                                         "ast": num(r.get("ast")), "stl": num(r.get("stl")), "blk": num(r.get("blk")), "tov": num(r.get("tov")),
                                         "fgm": None, "fga": None, "fgp": pct(r.get("fgp")), "tpm": None, "tpa": None, "tpp": pct(r.get("tpp")),
                                         "ftm": None, "fta": None, "ftp": pct(r.get("ftp")), "src": "RealGM", "allGames": True, "url": url,
                                         # a league Basketball-Reference covers, but no line could be had from it (no
                                         # page found or readable, or the page has no row for the season)
                                         **({"bbrGap": True} if covered else {})})
                            if covered:
                                rep(f"RealGM INT {s} {disp} ({label}): " + ("no Basketball-Reference page" if not reads else "Basketball-Reference covers this league but has no row") + " - RealGM's line used")
                            continue
                        rep(f"RealGM INT {s} {disp}: " + ("no second read" if not o else "the two reads differ") + " - journey only")
                    stints.append({"season": ss, "team": disp, "league": label, "country": cc, "src": "RealGM", "url": url})
        # ----- curated -----
        for key, nm in ((curated.get(pid) or {}).get("rename") or {}).items():
            ks, kl = key.split("|")
            hit = [r for r in rows if r["season"] == ks and r["league"] == kl]
            if len(hit) != 1:
                rep(f"curated rename {key}: {len(hit)} rows match - not applied"); continue
            hit[0]["team"] = nm
        for st in (curated.get(pid) or {}).get("stints") or []:
            if (st["season"], st["league"]) in bbr_keys or any(r["season"] == st["season"] and r["league"] == st["league"] for r in rows):
                rep(f"curated stint {st['season']} {st['team']} already has a stats row - skipped"); continue
            stints.append(dict(st))
        # de-duplicate stints (same season, club, league) and drop those a stats row already covers
        seen, st2 = set(), []
        for st in stints:
            k = (st["season"], st["team"], st["league"])
            if k in seen or any(r["season"] == st["season"] and r["league"] == st["league"] and r["team"] == st["team"] for r in rows):
                continue
            seen.add(k); st2.append(st)
        # the order of a season's clubs where a source gives it (curated 'order': season -> clubs in the order he played
        # for them). Basketball-Reference and RealGM lines carry no dates, so without it the cards can only place a
        # season's clubs by the seasons around it; 'seq' is the club's place in that season
        for season, clubs in ((curated.get(pid) or {}).get("order") or {}).items():
            for i, club in enumerate(clubs, 1):
                hit = [x for x in rows + st2 if x["season"] == season and x["team"] == club]
                if not hit:
                    rep(f"curated order {season}: {club!r} has no row or stop - not applied"); continue
                for x in hit:
                    x["seq"] = i
        fetched = []
        for fp_ in glob.glob(os.path.join(RAW, pid + ".*")):
            for ln in open(fp_, encoding="utf-8"):
                m = re.search(r'"(?:fetched|int_reread)":\s*"([0-9-]+)"|^#(?:fetched|int_reread)\s+([0-9-]+)', ln)
                if m:
                    fetched.append(m.group(1) or m.group(2))
        if (curated.get(pid) or {}).get("checked"):
            fetched.append(curated[pid]["checked"])     # searched for, nothing to read
        entry = {"checked": max(fetched)} if fetched else {}
        if rows:
            entry["history"] = sorted(rows, key=lambda r: r["season"])
        if st2:
            entry["career"] = sorted(st2, key=lambda r: r["season"])
        if college and not b.get("college"):
            entry["college"] = college
            entry["src"] = {"college": "RealGM"}
        elif (curated.get(pid) or {}).get("college") and not b.get("college"):
            entry["college"] = curated[pid]["college"]            # no RealGM page: the school named by the curated source
            entry["src"] = {"college": curated[pid].get("collegeSrc", "Club")}
        if sources:
            entry["sources"] = sources
        if (curated.get(pid) or {}).get("espnDrop"):
            entry["espnDrop"] = curated[pid]["espnDrop"]
        if entry:
            out[pid] = entry
        if not rows and not st2:
            rep("checked - nothing outside the NBL / ESPN data")
    days = sorted({v["checked"] for v in out.values() if v.get("checked")})
    when = days[0] if len(days) < 2 else f"{days[0]} to {days[-1]}"
    about = ("One-off curated career data (overseas leagues, G League, NBL before 2015-16, college/NBA seasons ESPN "
             f"couldn't link), built by overseas/build.py from page reads saved in overseas/raw/ ({when}). "
             "Sources: Basketball-Reference (season totals, every row checked), RealGM (season lines read twice), "
             "Wikipedia and clubs' own announcements (clubs only). Not refreshed by the daily rebuild.")
    res = {"_about": about, **{k: out[k] for k in sorted(out)}}
    fp = os.path.join(os.path.dirname(HERE), "overseas.json")
    json.dump(res, open(fp, "w"), indent=1, ensure_ascii=False, sort_keys=False)
    print(f"{len(out)} players -> {fp}")
    print(f"{sum(len(v.get('history', [])) for v in out.values())} season rows, {sum(len(v.get('career', [])) for v in out.values())} journey-only stops")
    verbose = "-v" in sys.argv
    notes = [r for r in report if ": note: " in r]
    print("\n".join(r for r in report if ": note: " not in r))
    if notes:
        print(f"({len(notes)} Basketball-Reference vs official NBL notes" + (":)\n" + "\n".join(notes) if verbose else " - run with -v to list them)"))


if __name__ == "__main__":
    main()
