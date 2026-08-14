import asyncio
import signal
from pathlib import Path

from dotenv import load_dotenv

from core import registry

# Has to happen before registry.*: discovery reads BUGBOT_PLATFORMS/BUGBOT_FEATURES from the
# environment, and the packages are only imported afterwards (they load the same .env once
# more - load_dotenv is idempotent and overwrites nothing already set).
load_dotenv(Path(__file__).parent / ".env")


async def main():
    # The entry point knows neither platform nor feature by name: the registry finds every
    # platforms/* and features/* package, builds them through their factory function and
    # registers them on the bus (see core/registry.py).
    #
    # Features first, and fully set up at that: in their setup() they subscribe to the topics
    # the platforms are about to report on. The other way round, the first message after
    # startup would be lost.
    features = await registry.load_features()
    platforms = registry.load()

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def request_shutdown(signal_name):
        names = ", ".join(p.name for p in platforms)
        print(f"\n🛑 {signal_name} empfangen, fahre {names} sauber herunter...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, request_shutdown, sig.name)

    # asyncio.gather() already returns a future-like, awaitable/cancellable object - no
    # additional create_task() needed (and since Python 3.14 no longer allowed either:
    # create_task() strictly demands a coroutine).
    runner = asyncio.gather(*(p.start() for p in platforms))
    stopper = asyncio.create_task(stop_event.wait())

    # Waits on whichever happens first: Ctrl+C/SIGTERM (stopper) or a crash/disconnect of a
    # platform (runner). In the crash case the closing "await runner" passes the original
    # exception on, rather than swallowing it in silence.
    await asyncio.wait({runner, stopper}, return_when=asyncio.FIRST_COMPLETED)
    stopper.cancel()

    # Guard each part individually: one that chokes during shutdown must not leave the others
    # open. Platforms first (they stop reporting), then the features (they still write away
    # what was reported).
    for part in (*platforms, *features):
        try:
            await part.close()
        except Exception as e:
            print(f"⚠️ Herunterfahren von '{part.name}' fehlgeschlagen: {e!r}")

    if not runner.done():
        runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass

    print("✅ BugBot beendet.")


asyncio.run(main())
