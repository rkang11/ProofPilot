FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl python3 \
    && rm -rf /var/lib/apt/lists/*

ENV ELAN_HOME=/opt/elan
ENV PATH=/opt/elan/bin:$PATH

RUN curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf \
    | sh -s -- -y --default-toolchain none

WORKDIR /app

COPY lean-toolchain .
RUN elan toolchain install "$(cat lean-toolchain)"

COPY . .

ENV PROOFPILOT_HOST=0.0.0.0
ENV PROOFPILOT_DB=/data/proofpilot.sqlite3

EXPOSE 8000

CMD ["python3", "backend/server.py"]
