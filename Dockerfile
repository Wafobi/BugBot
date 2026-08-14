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

COPY . .
RUN chown -R bugbot:bugbot /app

USER bugbot
ENV PYTHONUNBUFFERED=1

CMD ["python3", "bugbot.py"]
