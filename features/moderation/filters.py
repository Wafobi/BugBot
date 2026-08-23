# filters.py
# The pure filters of the moderation feature: banned words, links, caps/symbol/emote spam.
# Deliberately stateless functions - the escalation (how often, how long muted) sits in
# feature.py, which only asks these.
#
# Thresholds/additional banned words come per platform from discord.json/twitch.json (see
# core/runtime_config.py) and are merged here with the defaults below - so that, say, the
# emoji spam sensitivity or the banned word list can be set differently per platform at
# runtime, without restarting the bot.

import re
from functools import lru_cache
from urllib.parse import urlsplit

# Banned words/phrases: the original curation + a merge of the public LDNOOBW list
# (DE+EN, https://github.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words),
# deduplicated. Deliberately excluded from the LDNOOBW source: the phrases
# "how to kill"/"how to murder" - in a gaming chat ("how to kill the boss") those would be
# guaranteed false alarms, not abuse.
#
# This is the shared base list for both platforms. Additional, platform-specific words are
# added at runtime via "moderation.extra_banned_words" in discord.json/twitch.json (see
# build_settings below).
BASE_BANNED_WORDS = [
    "2 girls 1 cup", "2g1c", "acrotomophilia", "alabama hot pocket", "alaskan pipeline", "anal",
    "analritter", "anilingus", "anus", "apeshit", "arsch", "arschficker", "arschkriecher",
    "arschlecker", "arschloch", "arsehole", "ass", "asshole", "assmunch", "autist", "auto erotic",
    "autoerotic", "babeland", "baby batter", "baby juice", "ball gag", "ball gravy",
    "ball kicking", "ball licking", "ball sack", "ball sucking", "bangbros", "bangbus", "bareback",
    "barely legal", "barenaked", "bastard", "bastardo", "bastinado", "bbw", "bdsm", "beaner",
    "beaners", "beastiality", "beaver cleaver", "beaver lips", "behindert", "bestiality",
    "big black", "big breasts", "big knockers", "big tits", "bimbo", "bimbos", "birdlock", "bitch",
    "bitches", "black cock", "blonde action", "blonde on blonde action", "blow job",
    "blow your load", "blowjob", "blue waffle", "blumpkin", "bollocks", "bondage", "boner",
    "bonze", "boob", "boobs", "booty call", "bratze", "brown showers", "brunette action",
    "bukkake", "bulldyke", "bullet vibe", "bullshit", "bumsen", "bung hole", "bunghole", "busty",
    "butt", "buttcheeks", "butthole", "camel toe", "camgirl", "camslut", "camwhore",
    "carpet muncher", "carpetmuncher", "chink", "chocolate rosebuds", "cialis", "circlejerk",
    "cleveland steamer", "clit", "clitoris", "clover clamps", "clusterfuck", "cock", "cocks",
    "coon", "coons", "coprolagnia", "coprophilia", "cornhole", "creampie", "cum", "cumming",
    "cumshot", "cumshots", "cunnilingus", "cunt", "darkie", "date rape", "daterape", "deep throat",
    "deepthroat", "dendrophilia", "dick", "dildo", "dingleberries", "dingleberry", "dirty pillows",
    "dirty sanchez", "dog style", "doggie style", "doggiestyle", "doggy style", "doggystyle",
    "dolcett", "domination", "dominatrix", "dommes", "donkey punch", "double dong",
    "double penetration", "dp action", "drecksau", "dry hump", "dvda", "dyke", "dödel",
    "eat my ass", "ecchi", "ejaculation", "erotic", "erotism", "escort", "eunuch", "fag", "faggot",
    "fecal", "felch", "fellatio", "feltch", "female squirting", "femdom", "fick", "ficken",
    "figging", "fingerbang", "fingering", "fisting", "flittchen", "foot fetish", "footjob",
    "fotze", "fratze", "free discord nitro", "frotting", "fuck", "fuck buttons", "fuckin",
    "fucking", "fucktards", "fudge packer", "fudgepacker", "futanari", "g-spot", "gang bang",
    "gangbang", "gay sex", "geh sterben", "genitals", "giant cock", "girl on", "girl on top",
    "girls gone wild", "goatcx", "goatse", "god damn", "gokkun", "golden shower", "goo girl",
    "goodpoop", "gook", "goregasm", "grope", "group sex", "guro", "hackfresse", "hand job",
    "handjob", "hard core", "hardcore", "hentai", "homoerotic", "honkey", "hooker", "horny",
    "hot carl", "hot chick", "huge fat", "humping", "hure", "hurensohn", "huso", "hänge dich",
    "incest", "intercourse", "ische", "jack off", "jail bait", "jailbait", "jelly donut",
    "jerk off", "jigaboo", "jiggaboo", "jiggerboo", "jizz", "judensau", "juggs", "kackbratze",
    "kacke", "kacken", "kackwurst", "kampflesbe", "kanake", "kike", "kill yourself", "kimme",
    "kinbaku", "kinkster", "kinky", "knobbing", "kys", "lappen", "leather restraint",
    "leather straight jacket", "lemon party", "livesex", "loli", "lolita", "lovemaking", "lümmel",
    "make me come", "male squirting", "masturbate", "masturbating", "masturbation",
    "menage a trois", "MILF", "missionary position", "miststück", "mong", "morgenlatte",
    "motherfucker", "mound of venus", "mr hands", "muff diver", "muffdiving", "mufti", "muschi",
    "möpse", "möse", "nackt", "nambla", "nawashi", "neger", "negro", "neonazi", "nig nog", "nigga",
    "nigger", "nimphomania", "nippel", "nipple", "nipples", "nitro for free", "nsfw",
    "nsfw images", "nude", "nudity", "nutte", "nutten", "nympho", "nymphomania", "octopussy",
    "omorashi", "onanieren", "one cup two girls", "one guy one jar", "onlyfans free", "opfer",
    "orgasm", "orgasmus", "orgy", "paedophile", "paki", "panties", "panty", "pedobear",
    "pedophile", "pegging", "penis", "phone sex", "piece of shit", "pikey", "pimmel", "pimpern",
    "pinkeln", "piss pig", "pissen", "pisser", "pissing", "pisspig", "playboy", "pleasure chest",
    "pole smoker", "ponyplay", "poof", "poon", "poontang", "poop chute", "poopchute", "popel",
    "poppen", "porn", "porno", "pornography", "prince albert piercing", "pthc", "pubes", "punany",
    "pussy", "queaf", "queef", "quim", "raghead", "raging boner", "rape", "raping", "rapist",
    "rectum", "retard", "retarded", "reudig", "reverse cowgirl", "rimjob", "rimming", "rosette",
    "rosy palm", "rosy palm and her 5 sisters", "rusty trombone", "s&m", "sadism", "santorum",
    "scat", "schabracke", "scheisser", "scheiße", "schiesser", "schlampe", "schlong", "schnackeln",
    "schwanzlutscher", "schwuchtel", "scissoring", "semen", "sex", "sexcam", "sexo", "sexual",
    "sexuality", "sexually", "sexy", "shaved beaver", "shaved pussy", "shemale", "shibari", "shit",
    "shitblimp", "shitty", "shota", "shrimping", "skeet", "slanteye", "slut", "smut", "snatch",
    "snowballing", "sodomize", "sodomy", "spast", "spasti", "spastic", "spic", "splooge",
    "splooge moose", "spooge", "spread legs", "spunk", "strap on", "strapon", "strappado",
    "strip club", "style doggy", "suck", "sucks", "suicide girls", "sultry women", "swastika",
    "swinger", "tainted love", "taste my", "tea bagging", "threesome", "throating", "thumbzilla",
    "tied up", "tight white", "tit", "tits", "tittchen", "titten", "titties", "titty",
    "tongue in a", "topless", "tosser", "towelhead", "tranny", "tribadism", "tub girl", "tubgirl",
    "tushy", "twat", "twink", "twinkie", "two girls one cup", "undressing", "upskirt",
    "urethra play", "urophilia", "vagina", "venus mound", "viagra", "vibrator", "violet wand",
    "vollidiot", "vollpfosten", "vorarephilia", "voyeur", "voyeurweb", "voyuer", "vulva", "vögeln",
    "wank", "wet dream", "wetback", "whatsapp group", "white power", "whore", "wichse", "wichsen",
    "wichser", "wixer", "worldsex", "wrapping men", "wrinkled starfish", "xxx", "yaoi",
    "yellow showers", "yiffy", "zoophilia",
]

# Plain text for every offence, as it appears in chat and in the mod log. The keys are the
# feature's text keys (moderation.json, section "texts") - anyone wanting to rephrase or
# translate the notices changes them there. The values below are only the fallback for a
# missing file, which is why they are in the operator's language, not in the code's.
VIOLATION_REASON_LABELS = {
    "reason.banned_word": "unerlaubtes Wort",
    "reason.link_spam": "nicht erlaubter Link",
    "reason.excessive_caps": "zu viele Großbuchstaben",
    "reason.symbol_spam": "Symbol-Spam",
    "reason.emote_spam": "Wort-/Emote-Spam",
}

LINK_PATTERN = re.compile(
    r"(https?://\S+|www\.\S+|\b[\w-]+\.(?:com|net|org|tv|gg|io|co|de|xyz|shop)\b)",
    re.IGNORECASE
)

# Defaults in case twitch.json/discord.json does not (yet) set a value.
DEFAULT_MODERATION_SETTINGS = {
    "extra_banned_words": [],
    "allowed_link_domains": ["twitch.tv", "steampowered.com", "discord.gg", "discord.com"],
    "caps_min_length": 10,
    "caps_ratio_threshold": 0.7,
    "symbol_min_length": 8,
    "symbol_ratio_threshold": 0.5,
    "emote_spam_min_tokens": 6,
    "emote_spam_min_repeats": 6,
    "violation_window_minutes": 10,
    "timeout_threshold": 3,
    "timeout_duration_seconds": 60,
}


@lru_cache(maxsize=8)
def _compile_banned_words_pattern(words):
    """None when no word is left at all - an empty pattern would match *every* message,
    and "I want no banned words" must not mean "everything is an offence"."""
    if not words:
        return None
    # Word-boundary match rather than a plain substring, so that e.g. "opfer" does not
    # also fire inside "Verkehrsopfer" and the like.
    return re.compile(
        r"(?<!\w)(" + "|".join(re.escape(w) for w in words) + r")(?!\w)",
        re.IGNORECASE,
    )


def banned_word_list(word_config, extra_words=()):
    """The actual word list from the "banned_words" section of moderation.json: the
    shipped base list (switchable) + "extra" + the platform's own extra_banned_words, minus
    "remove".

    Removal exists because the base list is broadly curated: what is an offence in one
    community is the subject of the stream in the next."""
    config = word_config or {}
    words = list(BASE_BANNED_WORDS) if config.get("use_builtin_list", True) else []
    words += list(config.get("extra") or [])
    words += list(extra_words or [])
    removed = {str(w).lower() for w in (config.get("remove") or [])}
    return tuple(sorted({w for w in (str(w).strip() for w in words) if w and w.lower() not in removed}))


def build_settings(base=None, overrides=None, word_config=None):
    """Lays the values on top of each other - defaults from the code, then moderation.json
    (`base`), then the platform's "moderation" section (`overrides`) - and compiles the
    banned-word pattern along with it. Called afresh per message: cheap, because the pattern
    compile hangs on the actual word list via lru_cache."""
    settings = {**DEFAULT_MODERATION_SETTINGS, **(base or {}), **(overrides or {})}
    settings["banned_words_pattern"] = _compile_banned_words_pattern(
        banned_word_list(word_config, settings.get("extra_banned_words"))
    )
    return settings


def check_banned_words(text, settings):
    pattern = settings["banned_words_pattern"]
    if pattern is None:
        return None
    match = pattern.search(text)
    return match.group(1) if match else None


def _link_host(link):
    """The hostname a matched link actually points at. LINK_PATTERN has no scheme for bare
    domains ("evil.xyz"), so urlsplit needs one added or it reads the domain as the path."""
    if "://" not in link:
        link = f"//{link}"
    return (urlsplit(link).hostname or "").lower().rstrip(".")


def _is_allowed_host(host, allowed_domains):
    # Suffix-on-a-label-boundary, not substring: "clips.twitch.tv" is allowed by "twitch.tv",
    # but "twitch.tv.evil.com" (a lookalike subdomain of evil.com) is not.
    return any(host == domain or host.endswith(f".{domain}")
               for domain in (d.lower() for d in allowed_domains))


def check_link_spam(text, settings):
    # Checked per link, not against the whole message - otherwise mentioning an allowed
    # domain anywhere in the text (even in a sentence, not as a link) would wave through
    # every other, unrelated link in the same message.
    allowed = settings["allowed_link_domains"]
    return any(not _is_allowed_host(_link_host(m.group(0)), allowed)
               for m in LINK_PATTERN.finditer(text))


def check_excessive_caps(text, settings):
    letters = [c for c in text if c.isalpha()]
    if len(letters) < settings["caps_min_length"]:
        return False
    caps_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
    return caps_ratio >= settings["caps_ratio_threshold"]


def check_symbol_spam(text, settings):
    if len(text) < settings["symbol_min_length"]:
        return False
    symbols = sum(1 for c in text if not c.isalnum() and not c.isspace())
    return (symbols / len(text)) >= settings["symbol_ratio_threshold"]


def check_emote_spam(text, settings):
    tokens = text.split()
    if len(tokens) < settings["emote_spam_min_tokens"]:
        return False
    most_common = max(set(tokens), key=tokens.count)
    return tokens.count(most_common) >= settings["emote_spam_min_repeats"]


def moderate_message(text, settings, relaxed=False):
    """Checks a message against all filters and returns (reason, detail) of the first hit,
    otherwise None. `relaxed=True` (e.g. for subscribers) skips the pure spam heuristics
    (caps/symbols/emotes); banned words and links still apply."""
    word = check_banned_words(text, settings)
    if word:
        return ("banned_word", word)
    if check_link_spam(text, settings):
        return ("link_spam", None)
    if relaxed:
        return None
    if check_excessive_caps(text, settings):
        return ("excessive_caps", None)
    if check_symbol_spam(text, settings):
        return ("symbol_spam", None)
    if check_emote_spam(text, settings):
        return ("emote_spam", None)
    return None
