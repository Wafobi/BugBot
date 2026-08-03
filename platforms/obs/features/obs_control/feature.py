# feature.py
# Die Fernsteuerung von OBS als Feature - also das, was Menschen im Chat tippen: !obs,
# !scene, !rec, !replay, !obssource.
#
# Warum ein Feature und keine Befehle in platforms/obs/bot.py: Befehle einer *Plattform*
# gelten nur auf ihr selbst (siehe platforms/twitch/commands.py), und OBS hat gar keinen
# Chat, auf dem man tippen könnte. Als Feature landen sie dagegen im Befehlsverzeichnis
# des Bus - und funktionieren damit überall, wo jemand Befehle eingibt: im Twitch-Chat wie
# auf Discord, ohne dass eine der beiden Plattformen OBS kennt.
#
# Der Ort (platforms/obs/features/) sorgt zugleich dafür, dass sie nur existieren, wenn es
# die OBS-Plattform überhaupt gibt - core/registry.py lädt plattformeigene Features nur
# mit ihrer Plattform. Und weil dieses Feature bot.py importiert, gilt dasselbe Alles-oder-
# nichts wie für die Plattform: fehlt OBS_BRIDGE_TOKEN in der .env, lässt sich schon
# config.py nicht laden, und die Befehle tauchen erst gar nicht auf, statt bei jedem
# Aufruf ins Leere zu greifen.
#
# Alle Befehle sind mod_only: sie greifen in einen laufenden Stream ein. Ein !scene aus
# dem offenen Chat wäre eine Fernbedienung für jeden Zuschauer.

from core import feature as feature_api

from ... import bot
from ...bot import OBS_CONFIG, text
from ...link import OBSError


class OBSControlFeature(feature_api.Feature):
    name = "obs_control"

    # Texte und Befehlsnamen teilt es sich mit der Plattform: beides steht in obs.json,
    # und den Abschnitt "command_names" wendet der Bus beim Einsammeln an (core/events.py).
    config = OBS_CONFIG

    # Meldet keine Fähigkeit an: es schreibt nichts mit und beantwortet keine Abfragen
    # anderer Features - es bringt nur Befehle mit. Genau dafür ist `provides` leer
    # erlaubt (siehe core/feature.py).

    def commands(self):
        return (
            feature_api.Command("!obs", self.cmd_status, mod_only=True, help=text("help.obs")),
            feature_api.Command("!scene", self.cmd_scene, mod_only=True, help=text("help.scene")),
            feature_api.Command("!rec", self.cmd_record, mod_only=True, help=text("help.rec")),
            feature_api.Command("!replay", self.cmd_replay, mod_only=True, help=text("help.replay")),
            feature_api.Command("!obssource", self.cmd_source, mod_only=True, help=text("help.source")),
        )

    # Jeder Befehl kann auf eine Leitung treffen, die es gerade nicht gibt (OBS-PC aus,
    # Relais nicht gestartet) - deshalb überall dieselbe Antwort statt eines Fehlers im
    # Log und einer stummen Chatzeile.
    @staticmethod
    def _offline():
        return text("offline")

    async def cmd_status(self, message):
        try:
            return await bot.status_announcement()
        except OBSError as e:
            return text("status.failed", error=e)

    async def cmd_scene(self, message):
        if not bot.link.connected:
            return self._offline()
        try:
            if not message.arg_text:
                current, names = await bot.scene_list()
                return text("scene.current", scene=current or "?", scenes=self._names(names))
            switched = await bot.switch_scene(message.arg_text)
            if not switched:
                _, names = await bot.scene_list()
                return text("scene.not_found", wanted=message.arg_text, scenes=self._names(names))
            return text("scene.switched", scene=switched)
        except OBSError as e:
            return text("scene.failed", error=e)

    @staticmethod
    def _names(names):
        return ", ".join(names) or text("scene.none")

    async def cmd_record(self, message):
        if not bot.link.connected:
            return self._offline()

        action = message.arg_text.strip().lower()
        requests = {
            "start": ("StartRecord", "rec.started"),
            "stop": ("StopRecord", "rec.stopped"),
            "pause": ("PauseRecord", "rec.paused"),
            "resume": ("ResumeRecord", "rec.resumed"),
        }
        if action not in requests:
            try:
                status = await bot.link.request("GetRecordStatus")
            except OBSError as e:
                return text("rec.status_failed", error=e)
            if not status.get("outputActive"):
                return text("rec.idle")
            state = text("status.record.paused" if status.get("outputPaused") else "status.record.running")
            return text("rec.running", state=state,
                        duration=(status.get("outputTimecode") or "").split(".")[0])

        request_type, reply_key = requests[action]
        try:
            answer = await bot.link.request(request_type)
        except OBSError as e:
            return text("rec.failed", action=action, error=e)
        # StopRecord verrät, wohin geschrieben wurde - für Clips/Nachbearbeitung die
        # einzige Information, die man danach noch sucht.
        reply = text(reply_key)
        path = answer.get("outputPath")
        return text("rec.file", reply=reply, path=path) if path else reply

    async def cmd_replay(self, message):
        if not bot.link.connected:
            return self._offline()
        try:
            await bot.link.request("SaveReplayBuffer")
        except OBSError as e:
            # Der häufigste Fall: der Replay-Puffer ist in OBS gar nicht aktiv.
            return text("replay.failed", error=e)
        return text("replay.saved")

    async def cmd_source(self, message):
        if not bot.link.connected:
            return self._offline()

        name, _, state = message.arg_text.rpartition(" ")
        state = state.strip().lower()
        if not name or state not in ("on", "off", "an", "aus"):
            return text("source.usage")

        visible = state in ("on", "an")
        source = name.strip()
        changed = await bot.set_source_visible(source, visible)
        if not changed:
            return text("source.missing", source=source)
        return text("source.done",
                    state=text("source.shown" if visible else "source.hidden"),
                    source=source, count=changed)


def create_feature():
    return OBSControlFeature()
