FROM python:3.12-slim
WORKDIR /app
COPY relay.py relay_config.py router.py providers.json models.json ./
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 RELAY_CONFIG=/data/config.json
EXPOSE 11437
ENTRYPOINT ["python", "relay.py"]
CMD ["serve", "--host", "0.0.0.0"]
