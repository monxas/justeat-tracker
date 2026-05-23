FROM python:3.13-alpine

# Non-root user
RUN adduser -D -u 1000 tracker

WORKDIR /app
COPY tracker.py .
RUN chown -R tracker:tracker /app

USER tracker
ENV STATE_PATH=/data/state.json
ENV PYTHONUNBUFFERED=1

# Healthcheck: state.json parseable + expires_at populated
HEALTHCHECK --interval=2m --timeout=10s --start-period=30s --retries=3 \
  CMD test -f /data/state.json && \
      python3 -c "import json,sys;d=json.load(open('/data/state.json'));sys.exit(0 if d.get('expires_at',0)>0 else 1)" \
      || exit 1

CMD ["python3", "tracker.py"]
