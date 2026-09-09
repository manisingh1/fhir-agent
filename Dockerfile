FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 AWS_EC2_METADATA_DISABLED=true
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY fhir_agent ./fhir_agent
RUN pip install --no-cache-dir '.[aws,service]' && useradd --uid 10001 --create-home app
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
CMD ["python", "-m", "fhir_agent.service"]
