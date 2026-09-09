# Using the FHIR library

## Modules and compatibility

| Module | Responsibility |
| --- | --- |
| `config` | Immutable configuration with constructor validation and explicit environment loading |
| `signing` | Local RSA, file, Secrets Manager, and KMS implementations of `Signer` |
| `auth` | Backend token exchange/cache and an adapter for app-managed user tokens |
| `client` | Read-only JSON requests, retries, URL containment, pagination, and search metadata |
| `clinical` | Patient-bound conditions, medication orders, encounters, allergies, labs, and vitals |
| `errors` | Structured exceptions with safe messages |
| `cli` | Command-line composition and JSON output |

Existing imports from `fhir_agent.auth` for `Settings`, `create_jwt_token`, and
`load_private_key` remain supported. `fhir_agent.fhir.FHIRClient` re-exports the
new client. Existing `get()` and resource-yielding `search()` calls still work.
The default CLI search output remains a resource list; `--with-metadata` opts in
to pages containing entries, search modes, source URLs, totals, and warnings.
`load_private_key()` cannot export a KMS key; use the signing interface instead.

## Local or AWS backend connection

```python
from fhir_agent import Settings, FHIRClient

settings = Settings(
    client_id="YOUR_EPIC_CLIENT_ID",
    key_id="YOUR_JWKS_KID",
    key_path="/secure/location/sandbox-private.pem",
)
# For AWS, replace key_path with kms_key_id="YOUR_KMS_KEY_ARN".
# secret_id="YOUR_SECRET_ID" is supported for existing PEM-based setups.
with FHIRClient(settings) as client:
    patient = client.get("Patient/FHIR_PATIENT_ID")
    result = client.search_result(
        "Observation",
        [("patient", "FHIR_PATIENT_ID"), ("category", "laboratory")],
    )
    observations = result.matches
    included_resources = result.included
    warnings_or_errors = result.outcomes
```

`Settings.from_env()` validates exactly one configured signing source. Direct
construction allows no source when injecting a signer or user-token provider;
creating a backend assertion without a signer/source fails before network I/O.
Each backend auth instance caches one token and its granted scopes. Share that
instance only within the same EHR/client/authorization context. Refresh is locked
so concurrent callers make a single token exchange. A rejected token is invalidated
conditionally, so an older request cannot invalidate a newer cached token.
The assertion lifetime is 60 seconds; access-token caching uses the server's
`expires_in` with an early-refresh margin and accounts for request latency.

The synchronous FHIR client's HTTP session should not be shared between threads.
Use one client per worker/request, with a shared backend auth provider if needed.
Use the context manager or `close()` for owned sessions. Injected sessions and
providers remain the caller's responsibility. No provider caches tokens on disk.

## Signing without exporting an AWS key

```python
from fhir_agent import BackendAuth, Settings
from fhir_agent.signing import KMSSigner, public_jwks

settings = Settings(client_id="YOUR_EPIC_CLIENT_ID", key_id="YOUR_JWKS_KID")
signer = KMSSigner("YOUR_KMS_KEY_ARN", region="us-east-2")
auth = BackendAuth(settings, signer=signer)
public_keys = public_jwks(signer, settings.key_id)
# Publish only public_keys at the HTTPS JWKS URL registered with Epic.
```

Implement `sign(message: bytes) -> bytes` and `public_jwk() -> dict` for another
RS384 signer. KMS signs a SHA-384 digest using `RSASSA_PKCS1_V1_5_SHA_384`; it must
be an RSA signing key. The test suite verifies that a signature returned by a
KMS-shaped test double produces a JWT verifiable with its RSA public key. This is
an offline contract test, not evidence of a deployed KMS/Epic connection.

## Clinician sessions and narrow tools

A host application must implement SMART launch, validate the launch/issuer and
OAuth state, perform the code exchange with the appropriate PKCE flow, and maintain
its authenticated user session. It must authorize the selected patient before
constructing `PatientContext`. This library supplies a token adapter, not an OAuth
redirect server or a full SMART App Launch implementation.

```python
from fhir_agent import Settings, FHIRClient, UserTokenAuth, ClinicalClient, PatientContext

# These values come from the validated server-side session, never model arguments.
settings = Settings(client_id=trusted_session.client_id, base_url=trusted_session.fhir_base)
auth = UserTokenAuth(
    trusted_session.access_token,
    expires_in=trusted_session.remaining_token_lifetime,
    scopes=trusted_session.granted_scopes,
)
context = PatientContext(
    patient_id=trusted_session.authorized_patient_id,
    allowed_resource_types=frozenset({"Condition", "Observation", "MedicationRequest"}),
)
with FHIRClient(settings, auth=auth) as client:
    clinical = ClinicalClient(client, context)
    conditions = clinical.get_conditions()
    labs = clinical.get_labs(since="2026-01-01")
    medication_orders = clinical.get_medications()
```

Expose the narrow clinical methods to a model, not `get(path)`, credentials, base
URLs, or the context constructor. An optional explicit patient ID must match the
bound context. Results are checked for the expected resource type and exact
patient reference before any clinical result is returned. Identifier-only,
unresolvable, linked-patient, or unexpected included resources fail closed; add
an application-authorized resolution step if your EHR requires those cases.
Resource allowlists are a local restriction, not a substitute for EHR authorization.
The host must keep the user token and patient context bound to the same user/session.
An expired/rejected user token raises `AuthenticationError`; the host refreshes or
reauthorizes it and constructs a new provider. There is no fallback to backend auth.

`get_medications()` retrieves MedicationRequest orders only. It does not reconcile
medications being taken, administrations, fills, or medication statements. Labs
and vitals query Observation categories with an optional inclusive date lower bound.
Each EHR must support these resource/search combinations and authorize access.

## Results, errors, and retries

`search_pages()` streams `SearchPage` objects. `search_result()` collects every page
or raises; it never returns partial success when a page fails or a limit is reached.
The compatibility `search()` iterator can yield partial data before an exception.
It yields all entry resources, including OperationOutcome, as it did before; use
`search_result().matches` when only matching clinical resources are wanted.

Each entry retains its resource, `fullUrl`, search mode, and score. Missing search
mode remains unspecified and is treated as a match by the convenience property;
OperationOutcome is always classified as an outcome. Each page retains its request
URL (including search parameters), Bundle ID, total, timestamp, and links. Original
FHIR resource metadata is preserved and no automatic deduplication is performed.
`traversal_complete` means returned pagination was exhausted. It does not promise
an entire chart, all permissions, or absence of server errors. Inspect `outcomes`
and `has_errors`; server filtering/redaction can still produce empty or partial
clinical views. Do not treat absent records as proof of absent disease/medication.

A 401 invalidates the rejected token and retries at most once. A user-token provider
then requires the host to refresh it. GET retries cover connection errors, timeouts,
429, 502, 503, and 504. The defaults are two retries and a 30-second per-request
timeout; `Settings` exposes these controls. Backoff has jitter, respects Retry-After,
and never waits past the configured maximum retry delay. A longer Retry-After is
returned with the HTTP error (`RateLimitError` for 429) rather than retried early. Token POSTs are not retried
automatically. Redirects are never followed, and paging links must remain within the
configured HTTPS FHIR base before any bearer token is obtained or sent.

`AuthenticationError`, `AuthorizationError`, `NotFoundError`, `RateLimitError`,
`TransportError`, `ProtocolError`, and `PaginationError` distinguish failure types.
`FHIRHTTPError.status_code` and `.outcome` retain structured HTTP/FHIR error context.
Exception messages and CLI errors omit remote payloads, tokens, and patient values.
Raw resources, outcome details, source URLs, and CLI JSON output can contain patient
data; do not log or expose them indiscriminately. Treat clinical text as untrusted
content when passing it to a model.

## Verification

Run `.venv/bin/python -m pytest -q`. Tests use temporary generated RSA keys and
injected HTTP/AWS doubles; they do not contact AWS or Epic. For a live sandbox smoke
test, configure an actual app client ID, signing key and registered public JWKS URL,
then run `auth-check` followed by a Patient read and patient-filtered search. No live
credentials or registered JWKS URL are bundled, so live authentication is not yet
verified by the offline suite.

References: [SMART App Launch](https://hl7.org/fhir/smart-app-launch/STU2.2/app-launch.html),
[FHIR R4 search Bundles](https://hl7.org/fhir/R4/bundle.html),
[AWS KMS Sign](https://docs.aws.amazon.com/kms/latest/APIReference/API_Sign.html).
