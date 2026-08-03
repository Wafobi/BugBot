# filters.py
# Die reinen Filter des Moderations-Features: Bannwörter, Links, Caps-/Symbol-/Emote-Spam.
# Absichtlich zustandslose Funktionen - die Eskalation (wie oft, wie lange stumm) sitzt
# in feature.py, das diese hier nur befragt.
#
# Schwellenwerte/Zusatz-Bannwörter kommen pro Plattform aus discord.json/twitch.json
# (siehe core/runtime_config.py) und werden hier mit den Defaults unten gemergt - dadurch
# lässt sich z.B. die Emoji-Spam-Empfindlichkeit oder die Bannwortliste zur Laufzeit
# je Plattform unterschiedlich einstellen, ohne den Bot neu zu starten.

import re
from functools import lru_cache

# Bannwörter/-phrasen: ursprüngliche Kuratierung + Merge aus der öffentlichen
# LDNOOBW-Liste (DE+EN, https://github.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words),
# dedupliziert. Bewusst ausgeschlossen aus der LDNOOBW-Quelle: die Phrasen
# "how to kill"/"how to murder" - in einem Gaming-Chat ("how to kill the boss")
# wären das garantierte Fehlalarme, nicht Missbrauch.
#
# Das ist die gemeinsame Basisliste für beide Plattformen. Zusätzliche, plattform-
# spezifische Wörter kommen zur Laufzeit über "moderation.extra_banned_words" in
# discord.json/twitch.json dazu (siehe build_settings unten).
BASE_BANNED_WORDS = [
    "bread",
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

# Klartext zu jedem Verstoß, wie er im Chat und im Mod-Log auftaucht. Die Schlüssel sind
# die Textschlüssel des Features (moderation.json, Abschnitt "texts") - wer die Meldungen
# umformulieren oder übersetzen will, ändert sie dort.
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

# Defaults, falls twitch.json/discord.json einen Wert (noch) nicht setzen.
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
    """None, wenn gar kein Wort übrig ist - ein leeres Pattern würde auf *jede* Nachricht
    passen, und "ich will keine Bannwörter" darf nicht "alles ist ein Verstoß" heißen."""
    if not words:
        return None
    # Wortgrenzen-Match statt reinem Substring, damit z.B. "opfer" nicht auch in
    # "Verkehrsopfer" o.ä. anschlägt.
    return re.compile(
        r"(?<!\w)(" + "|".join(re.escape(w) for w in words) + r")(?!\w)",
        re.IGNORECASE,
    )


def banned_word_list(word_config, extra_words=()):
    """Die tatsächliche Wortliste aus dem "banned_words"-Abschnitt von moderation.json:
    mitgelieferte Basisliste (abschaltbar) + "extra" + die plattformeigenen
    extra_banned_words, abzüglich "remove".

    Das Entfernen gibt es, weil die Basisliste breit kuratiert ist: was in der einen
    Community ein Verstoß ist, ist in der nächsten das Thema des Streams."""
    config = word_config or {}
    words = list(BASE_BANNED_WORDS) if config.get("use_builtin_list", True) else []
    words += list(config.get("extra") or [])
    words += list(extra_words or [])
    removed = {str(w).lower() for w in (config.get("remove") or [])}
    return tuple(sorted({w for w in (str(w).strip() for w in words) if w and w.lower() not in removed}))


def build_settings(base=None, overrides=None, word_config=None):
    """Legt die Werte übereinander - Defaults aus dem Code, dann moderation.json (`base`),
    dann der "moderation"-Abschnitt der Plattform (`overrides`) - und kompiliert das
    Bannwort-Pattern dazu. Wird pro Nachricht neu aufgerufen: billig, weil das
    Pattern-Compile per lru_cache an der tatsächlichen Wortliste hängt."""
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


def check_link_spam(text, settings):
    if not LINK_PATTERN.search(text):
        return False
    lowered = text.lower()
    return not any(domain in lowered for domain in settings["allowed_link_domains"])


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
    """Prüft eine Nachricht gegen alle Filter und gibt (reason, detail) des ersten
    Treffers zurück, sonst None. `relaxed=True` (z.B. für Subscriber) überspringt
    die reinen Spam-Heuristiken (Caps/Symbole/Emotes), Bannwörter & Links greifen weiterhin."""
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
