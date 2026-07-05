FROM leanprover/lean4:v4.31.0

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . .

ENV PROOFPILOT_HOST=0.0.0.0
ENV PROOFPILOT_DB=/data/proofpilot.sqlite3

EXPOSE 8000

CMD ["python3", "backend/server.py"]
