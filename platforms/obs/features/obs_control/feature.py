# feature.py
# The remote control of OBS as a feature - that is, what people type in chat: !obs, !scene,
# !rec, !replay, !obssource.
#
# Why a feature and not commands in platforms/obs/bot.py: a *platform's* commands only apply
# on itself (see platforms/twitch/commands.py), and OBS has no chat to type in at all. As a
# feature they land in the bus's command directory instead - and therefore work everywhere
# somebody enters commands: in Twitch chat as well as on Discord, without either of the two
# platforms knowing OBS.
#
# The location (platforms/obs/features/) at the same time ensures that they only exist when
# the OBS platform exists at all - core/registry.py loads platform-owned features only
# together with their platform. And because this feature imports bot.py, the same all-or-
# nothing applies as for the platform: if OBS_BRIDGE_TOKEN is missing from the .env, config.py
# already fails to load, and the commands do not show up in the first place instead of
# reaching into nothing on every call.
#
# All commands are mod_only: they intervene in a running stream. A !scene from the open chat
# would be a remote control for every viewer.

from core import feature as feature_api

from ... import bot
from ...bot import OBS_CONFIG, text
from ...link import OBSError


class OBSControlFeature(feature_api.Feature):
    name = "obs_control"

    # Texts and command names are shared with the platform: both live in obs.json, and the
    # bus applies the "command_names" section when collecting them (core/events.py).
    config = OBS_CONFIG

    # Declares no capability: it records nothing and answers no queries from other features
    # - it only brings commands along. That is exactly what an empty `provides` is allowed
    # for (see core/feature.py).

    def commands(self):
        return (
            feature_api.Command("!obs", self.cmd_status, mod_only=True, help=text("help.obs")),
            feature_api.Command("!scene", self.cmd_scene, mod_only=True, help=text("help.scene")),
            feature_api.Command("!rec", self.cmd_record, mod_only=True, help=text("help.rec")),
            feature_api.Command("!replay", self.cmd_replay, mod_only=True, help=text("help.replay")),
            feature_api.Command("!obssource", self.cmd_source, mod_only=True, help=text("help.source")),
        )

    # Every command can meet a line that does not currently exist (OBS machine off, relay
    # not started) - hence the same answer everywhere instead of an error in the log and a
    # silent chat line.
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
        # StopRecord reveals where it was written to - for clips/post-processing the one
        # piece of information you go looking for afterwards.
        reply = text(reply_key)
        path = answer.get("outputPath")
        return text("rec.file", reply=reply, path=path) if path else reply

    async def cmd_replay(self, message):
        if not bot.link.connected:
            return self._offline()
        try:
            await bot.link.request("SaveReplayBuffer")
        except OBSError as e:
            # The most common case: the replay buffer is not active in OBS at all.
            return text("replay.failed", error=e)
        return text("replay.saved")

    async def cmd_source(self, message):
        if not bot.link.connected:
            return self._offline()

        name, _, state = message.arg_text.rpartition(" ")
        state = state.strip().lower()
        # "an"/"aus" stay alongside "on"/"off": they are chat input, and dropping them would
        # break the command for anyone already typing it that way.
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
