FROM python:3.14-slim

WORKDIR /app

# The image only brings C and C.utf8. Without a real locale, strftime("%A") returns "Sunday"
# instead of the operator's language - silently, with a correctly computed date and only the
# wrong word. Everything that spells out weekdays or months is affected, above all the variables
# from features/variables/variables.json.
#
# Exactly one is generated, not the whole set: locales-all would be ~200 MB for languages
# nobody reads. Anyone wanting a different one builds with
# `podman build --build-arg LOCALE=fr_FR.UTF-8 -t bugbot .` and enters the same name in
# variables.json under "locale" - generating and using are two separate steps.
ARG LOCALE=de_DE.UTF-8
RUN apt-get update \
    && apt-get install -y --no-install-recommends locales \
    && echo "${LOCALE} UTF-8" >> /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 bugbot

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# TTS (platforms/twitch/features/tts): Piper and its voice models. Not in requirements.txt -
# the bot never imports Piper, it runs it as a separate process (`python -m piper`, see
# synth.py), and local development/tests do not need several hundred MB of it.
#
# The models go to /opt/piper-voices rather than under /app, so the `chown -R` further down
# does not copy them into a second layer. voices.txt is copied on its own before `COPY . .`:
# this layer only rebuilds when the list of models changes, not on every code change - and
# not when the voice is switched in tts.json either, which is why the list is not kept there.
ENV TTS_VOICES_DIR=/opt/piper-voices
RUN pip install --no-cache-dir "piper-tts>=1.8,<2"
COPY platforms/twitch/features/tts/voices.txt /tmp/voices.txt
RUN mkdir -p "${TTS_VOICES_DIR}" \
    && python3 -m piper.download_voices --data-dir "${TTS_VOICES_DIR}" \
       $(sed -e 's/#.*//' -e '/^[[:space:]]*$/d' /tmp/voices.txt) \
    && rm /tmp/voices.txt

COPY . .
RUN chown -R bugbot:bugbot /app

USER bugbot
ENV PYTHONUNBUFFERED=1

CMD ["python3", "bugbot.py"]
