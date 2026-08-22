from __future__ import annotations

import logging
import re

log = logging.getLogger("webcam-aggregator.categories")

_MAP: dict[str, str] = {
    "Animals": "Animals",
    "Birds": "Animals",
    "Pets & Animals": "Animals",
    "Aquariums": "Aquariums",
    "Beaches": "Beaches",
    "Water": "Water & Waterways",
    "Rivers Lakes": "Water & Waterways",
    "Pools": "Water & Waterways",
    "Ships": "Ports & Ships",
    "Ports": "Ports & Ships",
    "Mountains": "Mountains",
    "Ski Resorts": "Mountains",
    "Volcanoes": "Mountains",
    "Parks": "Nature & Parks",
    "Cities": "Cities",
    "Sights": "Landmarks",
    "Bridges": "Landmarks",
    "Buildings": "Landmarks",
    "Castles": "Landmarks",
    "Monuments": "Landmarks",
    "Religion": "Religion",
    "Churches": "Religion",
    "Vatican": "Religion",
    "Airports": "Airports",
    "Traffic": "Traffic",
    "Autos & Vehicles": "Traffic",
    "Trains": "Trains & Railways",
    "Railway Stations": "Trains & Railways",
    "Bars": "Bars & Nightlife",
    "Bars Clubs Restaurants": "Bars & Nightlife",
    "Sports": "Sports",
    "Stadiums": "Sports",
    "Hotels": "Hotels",
    "Schools Universities": "Education",
    "Education": "Education",
    "Music": "Music",
    "Space": "Space",
    "Christmas": "Seasonal",
    "Radio Studios": "Studios",
    "Weather": "Weather",
    "Sports Live": "Sports",
    "Watch Soccer Live": "Sports",
    # Non-content tags and a source's literal "Other" -> our "Other" (not Unmapped).
    "High Definition Hd": "Other",
    "Other": "Other",
}
# YouTube Data API category names that pass through as their own group. This MUST cover
# every value in sources.youtube_api._YT_CATEGORIES that _MAP doesn't remap, or those
# cams land in "Unmapped Category" — one undifferentiated bucket the user can only take
# or leave wholesale. Gaming, Film & Animation, Howto & Style and Comedy were missing,
# which is why 24/7 cartoon loops, Roblox giveaways and lottery draws couldn't be
# excluded separately. A test guards the two tables against drifting apart again.
_NATIVE_YT: set[str] = {
    "Entertainment",
    "Travel & Events",
    "People & Blogs",
    "News & Politics",
    "Science & Technology",
    "Nonprofits & Activism",
    "Gaming",
    "Film & Animation",
    "Howto & Style",
    "Comedy",
}


# Streams whose source gave a category we don't recognise land here — distinct from
# "Other" (the source gave NO category) — so a missing mapping is visible in the player
# and logged, instead of silently swelling "Other".
UNMAPPED = "Unmapped Category"
_seen_unmapped: set[str] = set()


def map_category(raw: str | None) -> str:
    if not raw:
        return "Other"
    if raw in _MAP:
        return _MAP[raw]
    if raw in _NATIVE_YT:
        return raw
    if raw not in _seen_unmapped:
        _seen_unmapped.add(raw)
        log.warning(
            "unmapped source category %r -> %s (add it to categories._MAP)",
            raw,
            UNMAPPED,
        )
    return UNMAPPED


# Title-keyword fallback for cams a source left uncategorised (-> "Other"). Ordered
# specific -> generic, FIRST MATCH WINS, so a more telling word (a species, "harbour")
# beats a generic one ("street", "city"). Matched against the cam NAME only (the part
# before the with_location " — geo" suffix) so a region in the geo doesn't false-trigger.
# Every target MUST be in ALL_CATEGORIES (guarded by a test). Keep these GENERAL keywords,
# not one-off cam names — the goal is a map that generalises, not memorising a snapshot.
#
# The rules are NOT English-only: whole sources are Japanese (tomarigi, shareju), and a
# cam its source left uncategorised has nothing else to go on. Three conventions for
# non-Latin keywords, each learned the hard way:
#   * NO \b around them. Japanese is unspaced, so `\b国道\b` cannot match 国道17号 — the
#     digit after 道 is a word character, so there is no boundary there to match. Each
#     rule keeps its English `\b(...)\b` group and ALTERNATES a boundary-free group.
#   * Prefer multi-character tokens: a lone kanji is usually also a place name. 港 sits
#     inside 空港 (airport) and 港区 (a Tokyo ward); 山 and 城 inside 岡山 and 茨城.
#   * Where a lone kanji is unavoidable — 川/湖/滝, because Japanese water cams are
#     named for their river, lake or fall — guard it by what FOLLOWS: 旭川市, 湖西市
#     and 滝沢市 run on into more name, a watercourse does not.
_TITLE_RULES: tuple[tuple[str, str], ...] = (
    (
        "Animals",
        r"\b(bear|eagle|osprey|falcon|peregrine|hawk|owl|heron|egret|stork|puffin|gannet"
        r"|kittiwake|\btern\b|penguin|manatee|otter|\bseal\b|sea ?lion|walrus|dolphin|whale"
        r"|orca|shark|panda|tiger|\blion\b|elephant|giraffe|gorilla|lemur|sloth|monkey|bison"
        r"|buffalo|\bdeer\b|moose|\belk\b|\bfox\b|\bwolf|\bbat\b|\bbee\b|alpaca|llama|sheep"
        r"|\bgoat|\bpig\b|horse|pony|cattle|\bcow\b|alligator|crocodile|tortoise|turtle"
        r"|kitten|pupp|\bcat cam|\bdog\b|guide dogs|\bbird|wildlife|\bzoo\b|aquarium|reef"
        r"|coral|salmon|hummingbird|feeder|\bnest(s|ing)?\b|aviary|sanctuary|\bgull|\bswan"
        r"|\bduck\b|goose|flamingo|parrot|condor|vulture|badger|hedgehog|squirrel|raccoon"
        r"|toucan|rhino|\bmares?\b|foal|musk ox|guillemot|roller|canine|safari|africam"
        r"|\bmara\b|kalahari|savanna|pasture|fishing hole)"
        r"|動物園|水族館|野鳥|野生動物|コウノトリ",
    ),
    ("Weather", r"\b(weather|northern lights|aurora)\b" r"|天気|気象"),
    (
        "Religion",
        r"\b(church(es)?|mosque|temple|chapel|basilica|cathedral|minster|abbey|shrine"
        r"|synagogue)\b"
        r"|神社|神宮|大社|寺院|参道|教会|大仏",
    ),
    (
        "Music",
        r"\b(philharmon|concert|opera|theatre|theater|orchestra|symphony|bandstand|amphitheat)",
    ),
    (
        "Entertainment",
        r"\b(aquapark|waterpark|amusement|theme park|funfair|casino|fairground|karaoke)",
    ),
    (
        "Sports",
        r"\b(sailing club|\bgolf\b|gliding|\bregatta|stadium|\btennis|football|cricket"
        # Swedish/German/Dutch write it as one word, which \bgolf\b cannot match; the
        # compounds are spelled out because a bare \bgolf prefix would eat Golfo di Napoli
        r"|\bgolf(klubb|club|platz|baan|bana|park))"
        r"|競技場|スタジアム|ゴルフ場|野球場",
    ),
    ("Space", r"\b(observatory|\biss\b|telescope|starry|space station|cosmodrome)"),
    ("Airports", r"\b(airport|airfield|runway|aerodrome)\b" r"|空港|飛行場"),
    (
        "Trains & Railways",
        r"\b(railway|railroad|\btrain|locomotive|\brail\b)"
        # 道の駅 is a roadside REST STOP, not a station — the Traffic rule claims it.
        r"|(?<!道の)駅|鉄道|新幹線|電車|踏切",
    ),
    (
        "Ports & Ships",
        r"\b(harbou?r|marina|\bpier\b|wharf|\bquay|lifeboat|\byacht|\bdocks?\b|seaport"
        r"|\bport\b|\bferry\b|shipping|\bhafen\b)"
        r"|漁港|港湾|埠頭|マリーナ|フェリー",
    ),
    ("Bars & Nightlife", r"\b(\bpub\b|tavern|nightclub|\bbar\b|brewery|cocktail)"),
    (
        "Mountains",
        r"\b(\bski\b|\balps\b|alpine|glacier|volcano|\bpiste|\bsummit|\bmount\b|mountain"
        r"|\bpeak\b|dolomite|\bdome\b|\bbutte\b|\bmesa\b|\bgorge\b|narciarsk|montagn[ae]\b"
        r"|bergbahn|gondelbahn|seilbahn|bergstation)"
        r"|スキー場|ゲレンデ|山頂|富士山|登山",
    ),
    ("Education", r"\b(universit|college|campus|schule)" r"|大学|学校|キャンパス"),
    ("Hotels", r"\b(hotell?|gasthof|guest ?house|pension)\b" r"|旅館|温泉宿"),
    (
        "Landmarks",
        r"\b(\bbridge|castle|\btowers?\b|\bfort\b|fortress|monument|palace|lighthouse|statue"
        r"|memorial|\barch\b|citadel|obelisk|granar|windmill)"
        # bridge in the languages our sources actually use. NOT `ponte`: Ponte di Legno
        # is a town, and a Landmarks match here would pre-empt the later Cities rule
        r"|puente|brücke" r"|お城|城跡|天守|タワー|灯台|展望台",
    ),
    (
        "Beaches",
        r"\b(beach|\bsurf|seafront|esplanade|\bplaya|\bpraia|\bsands?\b|\bshore|boardwalk"
        # plaża/plaža only: bare "plaza" is a town square and belongs to Cities
        # "the strand" is a common English street/place name (London); the German
        # beach word never takes a definite article in front of it
        r"|promenade|\bcove\b|\bdunes?\b|\bcoast|(?<!the )\bstrand|spiaggia|\bplage\b"
        r"|pla[żž])"
        r"|海岸|砂浜|ビーチ|海水浴場",
    ),
    (
        "Water & Waterways",
        r"\b(\blake|\briver|\bfalls\b|waterfall|\bcanal|lagoon|\bpond|\bloch\b|reservoir"
        r"|estuary|\bcreek|\bweir\b|fjord|\bdam\b|rapids|\bbay\b|\bcaverns?\b|\bcaves?\b"
        r"|\bfirth\b|\bsound\b|jezior|jezero)"
        r"|河川|水害|水位|映像監視局|用水|ダム|運河|合流|支流"
        # 川/湖/滝 alone only where they read as the water itself: a monitoring word
        # (or 畔, lakeside) follows, or the name simply ends (西除川, 【神田川】, 琵琶湖,
        # 白糸の滝) — unlike 旭川市/川崎/湖西市/滝沢市, which run on into more name.
        r"|[川湖滝](?:(?=映像|カメラ|ライブ|合流|上流|下流|水位|畔)"
        r"|(?![ぁ-んァ-ヶ一-龥A-Za-z0-9]))",
    ),
    (
        "Traffic",
        r"\b(\btraffic|highway|motorway|interstate|freeway|roundabout|expressway"
        r"|\bhwy\b|I-\d{1,3}\b)"
        r"|国道|県道|道道|市道|道の駅|交差点|高速|自動車道|バイパス|街道|渋滞|道路|トンネル"
        r"|ラウンドアバウト|インターチェンジ",
    ),
    (
        "Nature & Parks",
        r"\b(national park|\bpark\b|nature reserve|\bgarden|\bforest|\bmeadow|\bvalley"
        r"|\bcanyon|botanic|\bmoor\b|\bheath\b|\bwoods?\b|wetland)"
        r"|公園|森林|渓谷|湿原",
    ),
    (
        "Cities",
        r"\b(skyline|cityscape|\bsquare\b|downtown|\bplaza|boulevard|\bcity\b|old town"
        r"|piazza|\bstreet\b|panorama|\btown\b|village|\bcentre\b|\bcenter\b|centar"
        r"|altstadt|innenstadt|\brynek\b)"
        r"|市街地|商店街|繁華街|歩行者天国",
    ),
)
_TITLE_COMPILED: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (cat, re.compile(pat, re.I)) for cat, pat in _TITLE_RULES
)
# A name carrying a "City, Region, Country"-style location is a place view: the " — geo"
# suffix with_location appends, a trailing ", State"/", Country", or " - Country" at the end.
_GEO_HINT = re.compile(r" — |,\s+[A-Z]| - [A-Z][a-z]+\s*\.?\s*$")


def category_from_title(title: str) -> str | None:
    """Best-effort category from a cam's title, for cams a source left uncategorised.
    Keyword rules first (first match wins), then a geo-suffix fallback (a named place with
    no content word is a location view -> "Travel & Events"). None = nothing matched, so
    the cam stays "Other". Never call this when the source DID give a category."""
    head = title.split(" — ")[0]
    for category, rx in _TITLE_COMPILED:
        if rx.search(head):
            return category
    return "Travel & Events" if _GEO_HINT.search(title) else None


# Every category the title fallback can emit — drift-guarded against ALL_CATEGORIES.
TITLE_FALLBACK_CATEGORIES: frozenset[str] = frozenset(
    {cat for cat, _ in _TITLE_RULES} | {"Travel & Events"}
)


# Every category that can appear as a playlist group-title — i.e. the full set a user
# may name in EXCLUDE_CATEGORIES. Source of truth; the README list is drift-guarded by
# a test (tests/categories_test.py).
ALL_CATEGORIES: tuple[str, ...] = tuple(
    sorted(set(_MAP.values()) | _NATIVE_YT | {"Other", UNMAPPED})
)

# Fail fast at import if a title rule names a category outside the taxonomy (a typo guard,
# mirroring the registry's extractor-name validation at startup).
if not TITLE_FALLBACK_CATEGORIES <= set(ALL_CATEGORIES):
    raise ValueError(
        f"title rules emit unknown categories: "
        f"{TITLE_FALLBACK_CATEGORIES - set(ALL_CATEGORIES)}"
    )

_ALL_CASEFOLDED: frozenset[str] = frozenset(c.casefold() for c in ALL_CATEGORIES)


def unknown_categories(names: frozenset[str]) -> frozenset[str]:
    """Casefolded names that aren't real categories — likely typos in EXCLUDE_CATEGORIES."""
    return frozenset(n for n in names if n.casefold() not in _ALL_CASEFOLDED)
