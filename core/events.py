# events.py
# Der Bus: Nachrichtenverteilung *und* Verzeichnis aller Plattformen und Features. Alles,
# was die Teile des Bots voneinander wissen, geht hier durch - direkte Importe zwischen
# Plattform-Paketen oder von einer Plattform in ein Feature gibt es nicht mehr.
#
# Drei Wege:
#   publish(topic, **payload)  "das ist passiert" - Abonnenten ziehen eigenen Zustand
#       nach oder schreiben mit. Darüber läuft die gesamte Aufzeichnung: eine Plattform
#       meldet nur, dass eine Nachricht kam, und weiß nicht, ob ein Feature zuhört.
#       publish() gibt die Rückgaben der Abonnenten zurück - so kann ein Feature auch
#       antworten (siehe STREAM_END, das die Kennzahlen des Streams zurückliefert).
#   announce(announcement)     "postet das, wo ihr könnt" - geht an alle Plattformen mit
#       der Fähigkeit ANNOUNCE. Jede entscheidet selbst, ob und wie sie die `kind`
#       darstellt. Publiziert zusätzlich unter dem Topic announcement.kind.
#   feature(...)/command(...)  das Pull-Verzeichnis: wo eine Plattform eine *Antwort*
#       braucht (Moderations-Urteil) oder die Befehle der Features einsammelt.

import asyncio
from collections import defaultdict
from dataclasses import replace

from . import platform as platform_api


# --- Topics ------------------------------------------------------------------------
# Das gemeinsame Vokabular zwischen Plattformen (die publizieren) und Features (die
# abonnieren). Bewusst plattformneutral formuliert - "ein Ereignis mit Typ und Betrag"
# statt "ein Twitch-Cheer".

# Jede eingehende Nachricht, VOR der Moderation. Für den vollen Mitschnitt: gerade die
# später gelöschten Nachrichten will man im Nachhinein noch nachlesen können.
#   payload: message (feature.Message)
MESSAGE = "message.received"

# Nachricht hat die Moderation passiert. Alles, was einen Verstoß nicht mitzählen soll
# (Nachrichtenzähler, XP), hängt hier statt an MESSAGE.
#   payload: message (feature.Message)
MESSAGE_ACCEPTED = "message.accepted"

# Ein Befehl wurde ausgeführt.  payload: platform, command, user_name
COMMAND = "command.used"

# Eine Moderationsaktion wurde ausgeführt (durch den Bot oder einen Menschen).
#   payload: platform, user_name, reason, action ("delete"/"timeout"/"ban"/"warn"/"unban")
MOD_ACTION = "moderation.action"

# Ein typisiertes Live-Ereignis: Follow, Sub, Gift-Sub, Cheer, Raid, Hype-Train, ...
#   payload: platform, event_type, user_name, amount (Bits/Anzahl/Level/0)
PLATFORM_EVENT = "platform.event"

# Rohprotokoll: eine Benachrichtigung der Plattform im Originalzustand, auch wenn es
# dafür (noch) keinen Handler gibt.  payload: platform, event_type, payload
RAW_EVENT = "platform.raw"

# Stream-Zustand. STREAM_END gibt über die Abonnenten-Rückgabe die Abschluss-Kennzahlen
# zurück (als Tupel von platform.Field), aus denen die Plattform ihren Bericht baut.
#   STREAM_START payload: platform, title, category
#   STREAM_END   payload: platform
STREAM_START = "stream.start"
STREAM_END = "stream.end"

# Die Session ist geschlossen, ihre id steht fest. Getrennt von STREAM_END, weil die
# Reihenfolge sonst Glückssache wäre: wer den gerade beendeten Stream auswertet (Highscores,
# Abschlussbericht), braucht die id *nach* dem Schließen. Das Feature mit der Fähigkeit
# SESSIONS publiziert das, sobald es die Session zugemacht hat, und reicht die Rückgaben der
# Abonnenten an seinen eigenen STREAM_END-Aufrufer weiter - so bekommt die Plattform ihre
# Abschlussfelder weiterhin als Rückgabe von publish(STREAM_END).
#   payload: session_id
SESSION_ENDED = "session.ended"

# Titel-/Kategoriewechsel innerhalb eines Streams.  payload: platform, title, category
STREAM_SEGMENT = "stream.segment"

# Zuschauer-Stichprobe.  payload: platform, count
VIEWERS = "stream.viewers"

# Werbepause.  payload: platform, duration_seconds
AD_BREAK = "stream.ad_break"

# Ein User ist im Level aufgestiegen.  payload: message (die auslösende Message), level
LEVEL_UP = "level.up"


class EventBus:
    def __init__(self):
        self._platforms = {}
        self._features = {}
        self._commands = None
        self._commands_version = None
        self._handlers = defaultdict(list)

    # --- Plattform-Registry ---------------------------------------------------------

    def register(self, platform):
        if platform.name in self._platforms:
            raise ValueError(f"Plattform '{platform.name}' ist bereits registriert")
        self._platforms[platform.name] = platform

    @property
    def platforms(self):
        return tuple(self._platforms.values())

    def get(self, name):
        return self._platforms.get(name)

    def with_capability(self, capability):
        return tuple(p for p in self._platforms.values() if p.supports(capability))

    def resolve_platforms(self, tokens):
        """Aus einer Liste von Fähigkeiten und/oder Plattformnamen die gemeinte Menge von
        Namen. Leere Liste -> None, also "alle".

        Damit lässt sich eine Einschränkung auch in einer Konfigurationsdatei ausdrücken,
        ohne einen Dienst zu nennen: ["stream"] heißt "die Plattformen, die einen Stream
        melden" und stimmt auch dann noch, wenn das morgen eine andere ist. Ein Name geht
        weiterhin, wird aber gemeldet, wenn ihn gerade keine geladene Plattform trägt -
        genau der Fall, der sonst still zu "trifft nie zu" wird."""
        if not tokens:
            return None
        known = {p.name for p in self._platforms.values()}
        resolved = set()
        for token in tokens:
            token = str(token).strip().lower()
            if not token:
                continue
            matching = self.with_capability(token)
            if matching:
                resolved |= {p.name for p in matching}
            elif token in known:
                resolved.add(token)
            elif token in platform_api.CAPABILITIES:
                # Eine gültige Fähigkeit, die hier gerade niemand hat - kein Fehler, nur
                # im Moment leer.
                continue
            else:
                print(f"⚠️ '{token}' ist weder eine geladene Plattform noch eine Fähigkeit "
                      f"({', '.join(sorted(platform_api.CAPABILITIES))}) - wird ignoriert.")
        return resolved

    async def wait_ready(self, timeout=None):
        """Wartet, bis alle registrierten Plattformen bereit sind. False bei Timeout -
        der Aufrufer entscheidet dann selbst, ob er trotzdem weitermacht."""
        if not self._platforms:
            return True
        try:
            await asyncio.wait_for(
                asyncio.gather(*(p.wait_ready() for p in self.platforms)), timeout=timeout
            )
            return True
        except asyncio.TimeoutError:
            print(f"⚠️ Nicht alle Plattformen waren nach {timeout}s bereit.")
            return False

    # --- Feature-Registry -----------------------------------------------------------

    def register_feature(self, feature):
        if feature.name in self._features:
            raise ValueError(f"Feature '{feature.name}' ist bereits registriert")
        self._features[feature.name] = feature
        # Ein Feature soll auch dann an das Verzeichnis der Plattformen kommen, wenn es
        # nicht über core/registry.py aufgebaut wurde (Tests, ein Bot, der seine Features
        # selbst zusammenstellt). Die Registry setzt es zusätzlich schon vor setup() -
        # hier ist der späteste Moment, in dem es sicher stimmt.
        if feature.bus is None:
            feature.bus = self
        self._commands = None

    @property
    def features(self):
        return tuple(self._features.values())

    def feature(self, name):
        return self._features.get(name)

    def features_with(self, capability):
        return tuple(f for f in self._features.values() if f.supports(capability))

    def feature_with(self, capability):
        """Das erste Feature mit dieser Fähigkeit, oder None. Für den Normalfall, in dem
        genau eines sie anbietet (Ablage, Level) - wer mehrere durchlaufen will (etwa
        mehrere Moderationsfilter hintereinander), nimmt features_with."""
        found = self.features_with(capability)
        return found[0] if found else None

    def commands(self):
        """{Befehlsname: Command} aller Features zusammen. Die Plattformen hängen das in
        ihre eigene Befehlsauflösung ein, ohne die Features zu kennen. Bei einer Kollision
        gewinnt das zuerst registrierte Feature - der Konflikt wird gemeldet, statt still
        eines der beiden verschwinden zu lassen.

        Die Namen sind nicht zwingend die, die im Code stehen: bringt ein Feature eine
        eigene Konfiguration mit, darf deren Abschnitt "command_names" jeden Befehl
        umbenennen, ihm Aliase geben oder ihn abschalten (siehe core/runtime_config.py).
        Das passiert hier und nicht in den Features, damit keines dafür etwas tun muss -
        und der zurückgegebene Command trägt den *tatsächlichen* Namen, damit
        Befehlslisten wie !commands nicht die Namen aus dem Code anzeigen.

        Ergebnis wird gecacht: das hier läuft pro Chat-Nachricht. Der Cache verfällt,
        sobald eine der beteiligten Konfigurationen neu geladen wurde - sonst wäre
        ausgerechnet die Umbenennung das eine, was einen Neustart bräuchte."""
        version = self._command_config_version()
        if self._commands is not None and version == self._commands_version:
            return self._commands

        merged = {}
        for feature in self._features.values():
            declared = {command.name: command for command in feature.commands()}
            config = getattr(feature, "config", None)
            resolved = config.resolve_commands(declared) if config is not None else declared
            for name, command in resolved.items():
                if name in merged:
                    print(f"⚠️ Befehl {name} doppelt angeboten - '{feature.name}' wird ignoriert.")
                    continue
                merged[name] = command if command.name == name else replace(command, name=name)
        self._commands = merged
        self._commands_version = version
        return merged

    def _command_config_version(self):
        """Fingerabdruck über die Konfigurationsstände aller Features. Der Aufruf kostet
        je Feature ein stat() (siehe LiveConfig.reload) - dieselbe Größenordnung wie die
        Hot-Reload-Prüfung, die ohnehin pro Nachricht läuft.

        Die Identität der Konfiguration gehört mit hinein, nicht nur ihr Zählerstand:
        wird einem Feature eine andere LiveConfig untergeschoben (Tests, ein Feature das
        seine Konfiguration wechselt), fängt deren Zähler wieder bei eins an - der
        Zählerstand allein sähe dann aus wie "nichts passiert"."""
        return tuple(
            (feature.name, id(feature.config), feature.config.version if feature.config is not None else 0)
            for feature in self._features.values()
        )

    def command(self, name):
        return self.commands().get(name)

    # --- Pub/Sub --------------------------------------------------------------------

    def subscribe(self, topic, handler):
        """Meldet einen async-Handler für ein Topic an. Der Handler bekommt das payload
        von publish() als Keyword-Argumente."""
        self._handlers[topic].append(handler)
        return handler

    async def publish(self, topic, **payload):
        """Ruft alle Abonnenten des Topics nacheinander auf und gibt deren Rückgabewerte
        zurück. Ein fehlerhafter Abonnent reißt weder den Publisher noch die übrigen
        Abonnenten mit - dieselbe Regel wie bei den EventSub-Handlern in
        platforms/twitch/bot.py."""
        results = []
        for handler in list(self._handlers.get(topic, ())):
            try:
                results.append(await handler(**payload))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"⚠️ Fehler im Event-Handler für '{topic}': {e}")
        return results

    async def announce(self, announcement):
        """Verteilt eine Ankündigung an alle Plattformen mit der Fähigkeit ANNOUNCE und
        gibt zurück, wie viele sie tatsächlich gepostet haben.

        Die Quelle wird nicht ausgenommen: ein !bug aus dem Discord-Chat soll ja gerade
        im Discord-Bug-Kanal landen. Ob eine Plattform ihre eigenen Ankündigungen
        wiederholt, entscheidet sie in ihrem announce()."""
        await self.publish(announcement.kind, announcement=announcement)

        delivered = 0
        for target in self.with_capability(platform_api.ANNOUNCE):
            try:
                if await target.announce(announcement):
                    delivered += 1
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"⚠️ {target.name} konnte '{announcement.kind}' nicht ankündigen: {e}")
        return delivered


# Gemeinsame Instanz für den laufenden Bot. Modul-Global wie core/stats.py, statt sie
# durch jede Funktion durchzureichen - die Plattform-Module bestehen ohnehin
# überwiegend aus Modulfunktionen. Die Klasse bleibt trotzdem eigenständig
# instanziierbar (Tests, mehrere Bots in einem Prozess).
bus = EventBus()
