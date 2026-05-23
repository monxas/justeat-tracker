FROM python:3.13-alpine

# Non-root user
RUN adduser -D -u 1000 tracker

WORKDIR /app
COPY tracker.py .
RUN chown -R tracker:tracker /app

USER tracker
ENV STATE_PATH=/data/state.json
ENV PYTHONUNBUFFERED=1
ENV METRICS_PORT=9100

EXPOSE 9100

# Healthcheck: hit /health (returns 200 if last HA push within 30 min)
HEALTHCHECK --interval=2m --timeout=5s --start-period=30s --retries=3 \
  CMD python3 -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9100/health',timeout=3).status==200 else 1)" || exit 1

CMD ["python3", "tracker.py"]
