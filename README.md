# FHIR Agent

A read-only Epic FHIR R4 client with a Python library, CLI, and containerized HTTP
API for TypeScript backends. Includes Terraform for KMS signing and public JWKS
hosting. No chat UI or model integration yet.

## Local container

Requires Docker Desktop, Python 3, and Node.js 22+.

```sh
export FHIR_LOCAL_UID="$(id -u)" FHIR_LOCAL_GID="$(id -g)"
python3 scripts/init_local_service.py
docker compose -f compose.yaml -f compose.test.yaml up --build -d --wait
npm ci --prefix examples/typescript
npm run smoke --prefix examples/typescript
```

The API runs at `http://localhost:8000`. This setup uses synthetic data and makes
no AWS or Epic calls. The smoke test checks patient reads, conditions searches,
and authorization failures.

```sh
docker compose -f compose.yaml -f compose.test.yaml down
```

## Python client

Requires Python 3.9+; use an OpenSSL-based build for Epic HTTPS requests.

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[aws,test]'
cp .env.example .env
```

Configure your Epic client ID and signing key in `.env`, then register the public
JWKS URL in Epic's non-production app settings.

```sh
.venv/bin/fhir-agent auth-check
.venv/bin/fhir-agent get Patient/FHIR_PATIENT_ID
.venv/bin/fhir-agent search Observation --param 'patient=FHIR_PATIENT_ID' --param 'category=laboratory'
```

## Guides

- [Python library and CLI](docs/client.md): authentication, patient context, pagination, and errors.
- [HTTP service](docs/service.md): application grants, TypeScript client, Docker, and EKS.
- [Epic authentication infrastructure](infra/README.md): Terraform deployment, JWKS publishing, and sandbox connection setup.

## Tests

```sh
.venv/bin/python -m pytest -q
```
