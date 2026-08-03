FROM python:3.14-slim

WORKDIR /app

RUN useradd --create-home --uid 1000 bugbot

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chown -R bugbot:bugbot /app

USER bugbot
ENV PYTHONUNBUFFERED=1

CMD ["python3", "bugbot.py"]
