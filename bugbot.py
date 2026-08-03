import asyncio
import signal
from pathlib import Path

from dotenv import load_dotenv

from core import registry

# Muss vor registry.* passieren: die Discovery liest BUGBOT_PLATFORMS/BUGBOT_FEATURES aus
# der Umgebung, und die Pakete werden erst danach importiert (die laden dieselbe .env noch
# einmal - load_dotenv ist idempotent und überschreibt nichts Gesetztes).
load_dotenv(Path(__file__).parent / ".env")


async def main():
    # Der Einstiegspunkt kennt weder Plattform noch Feature namentlich: die Registry
    # findet alle platforms/*- und features/*-Pakete, baut sie über ihre Fabrikfunktion
    # auf und meldet sie am Bus an (siehe core/registry.py).
    #
    # Features zuerst, und zwar vollständig eingerichtet: sie abonnieren in ihrem setup()
    # die Topics, auf denen die Plattformen gleich melden werden. Andersherum ginge die
    # erste Nachricht nach dem Start verloren.
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

    # asyncio.gather() gibt bereits ein Future-artiges, awaitbares/abbrechbares Objekt
    # zurück - kein zusätzliches create_task() nötig (und seit Python 3.14 auch nicht
    # mehr erlaubt: create_task() verlangt strikt eine Coroutine).
    runner = asyncio.gather(*(p.start() for p in platforms))
    stopper = asyncio.create_task(stop_event.wait())

    # Wartet, je nachdem was zuerst passiert: Strg+C/SIGTERM (stopper) oder ein
    # Absturz/Disconnect einer Plattform (runner). Im Crash-Fall gibt das abschließende
    # "await runner" die ursprüngliche Exception weiter, statt sie stillschweigend zu
    # verschlucken.
    await asyncio.wait({runner, stopper}, return_when=asyncio.FIRST_COMPLETED)
    stopper.cancel()

    # Jedes Teil einzeln absichern: eines, das sich beim Herunterfahren verschluckt, darf
    # die anderen nicht offen zurücklassen. Erst die Plattformen (sie hören auf zu
    # melden), dann die Features (sie schreiben das Gemeldete noch weg).
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
