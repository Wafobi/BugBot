FROM python:3.14-slim

WORKDIR /app

# Das Image bringt nur C und C.utf8 mit. Ohne ein echtes Locale liefert strftime("%A")
# "Sunday" statt "Sonntag" - und zwar lautlos, mit richtig gerechnetem Datum und nur
# falschem Wort. Betroffen ist alles, was Wochentage oder Monate ausschreibt, allen voran
# die Variablen aus features/variables/variables.json.
#
# Erzeugt wird genau eines, nicht der ganze Satz: locales-all wären ~200 MB für eine
# Sprache, die niemand liest. Wer eine andere will, baut mit
# `podman build --build-arg LOCALE=fr_FR.UTF-8 -t bugbot .` und trägt denselben Namen in
# variables.json unter "locale" ein - erzeugen und benutzen sind zwei Schritte.
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
