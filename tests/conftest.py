"""Sets dummy values for every environment variable a platform package reads at import
time (see tests/README.md), before pytest collects any test module - a bare `import
platforms.twitch.bot` would otherwise raise KeyError in an environment without a real
.env, which is exactly the environment CI/a fresh clone runs tests in."""

import os

os.environ.setdefault("TWITCH_CHANNEL", "test_channel")
os.environ.setdefault("TWITCH_CLIENT_ID", "test_client_id")
os.environ.setdefault("TWITCH_CLIENT_SECRET", "test_client_secret")
os.environ.setdefault("TWITCH_CHAT_ACCESS_TOKEN", "test_access_token")
os.environ.setdefault("TWITCH_CHAT_REFRESH_TOKEN", "test_refresh_token")
os.environ.setdefault("TWITCH_CHAT_CLIENT_ID", "test_client_id")
os.environ.setdefault("DISCORD_TOKEN", "test_discord_token")
os.environ.setdefault("OBS_BRIDGE_TOKEN", "test_obs_token")
