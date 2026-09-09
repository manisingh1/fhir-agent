# FHIR Agent

A read-only Epic FHIR R4 client and connectivity foundation for a chat
application. It does not yet contain a chat UI or model integration.

The client supports RS384 backend authentication with local files, AWS Secrets
Manager, or AWS KMS signing. It also accepts app-managed SMART user tokens. Token
refresh is coordinated across threads; FHIR reads have bounded retries and
pagination. Search results retain source information, included resources, and
OperationOutcome warnings. Importing performs no file or network I/O.

FHIR IDs remain FHIR IDs, not MRNs. The client handles FHIR JSON; binary attachment
downloads and writes are not implemented.

See [the library guide](docs/client.md) for module boundaries, Python examples,
patient context, user-token integration, and error/retry behavior.

## Run locally

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[test]'
cp .env.example .env
```

Edit `.env`: set your Epic app's non-production client ID, set the key ID to match
your public JWKS, and configure exactly one key source. To use AWS Secrets Manager,
install `.[aws]`, set
`EPIC_PRIVATE_KEY_SECRET_ID=EPIC_PRIVATE_KEY` and `AWS_REGION=us-east-2`, and use
an authorized AWS profile. This accesses the secret only when you run a command.
The secret may be PEM text or JSON with an `EPIC_PRIVATE_KEY` field. For KMS, set
`EPIC_KMS_KEY_ID` to an asymmetric RSA `SIGN_VERIFY` key ARN and leave both other
key sources blank. Install `.[aws]` and grant the runtime `kms:Sign` for that key;
public JWKS export additionally needs `kms:GetPublicKey`. KMS uses
`RSASSA_PKCS1_V1_5_SHA_384` with a SHA-384 digest. The private key stays in KMS.
Use a stable key ARN and matching `EPIC_KEY_ID`; rotate keys with a JWKS overlap
period. File/Secrets Manager keys are cached in each signer instance, so recreate
the signer after rotation. No secrets are included in this repository.

Use a current Python build linked against OpenSSL for live HTTPS requests. This
machine's system Python 3.9 uses LibreSSL and urllib3 reports it as unsupported;
the offline suite passes, but that interpreter is not a validated live TLS setup.

Prefer a managed key/secret store. A private-key file path is supported for
local development; keep it outside source control.
Never publish a private key or reuse a production signing key for sandbox work.

```sh
# Export only the public key. Host this JSON at a stable public HTTPS URL.
.venv/bin/fhir-agent jwks > /tmp/epic-sandbox-jwks.json
# Once Epic's non-production JWKS configuration has propagated:
.venv/bin/fhir-agent auth-check
.venv/bin/fhir-agent get Patient --param 'identifier=MRN|203713'
# Substitute an actual FHIR Patient ID from sandbox test data or search results.
.venv/bin/fhir-agent search Observation --param 'patient=FHIR_PATIENT_ID' --param 'category=laboratory'
# Preserve page metadata, source URLs, and match/include/outcome distinctions:
.venv/bin/fhir-agent search Observation --param 'patient=FHIR_PATIENT_ID' --with-metadata
.venv/bin/python -m pytest -q
```

The MRN search is an example; its current sandbox availability has not been
verified. Commands return real responses only, with no mock fallback.
Read/search output may contain patient data; use synthetic sandbox records here.
Pagination raises on a limit or loop instead of silently returning a complete-looking result.
The CLI buffers a search until successful, while Python iterator callers must handle
exceptions after partial results. Each server's supported search parameters differ.

## Current Epic app and recommended API selection

Observed in an existing Epic sandbox app registration on September 5, 2026
(your registration may differ):

- Backend Systems, General use case, FHIR R4, SMART v1 selected.
- Patient, Encounter, Condition, Appointment and other APIs already selected,
  including some write operations and older FHIR versions.
- Core labs, vitals, allergies, medications and clinical-note APIs appear in
  Available rather than Selected.
- Non-Production JWK Set URL is blank. Production URL has a validation warning.
- No browser settings were changed or saved.

For broad chart question-answering, select the R4 Read and Search variants for
the relevant clinical representations below. Epic splits one FHIR resource into
several API entries; selecting one representation does not imply all the others.

| Chart area | API entries to include |
| --- | --- |
| Patient and visits | Patient (Demographics), Encounter (Patient Chart), Appointment (Appointments and Scheduled Surgeries) |
| Problems and history | Condition (Problems, Encounter Diagnosis, Medical History, Health Concerns), FamilyMemberHistory |
| Allergies | AllergyIntolerance (Patient Chart) |
| Medications | MedicationRequest (Signed Medication Order), MedicationAdministration, MedicationDispense (Verified Orders and Fill Status), Medication (Organization Med List), List (Medication List) |
| Measurements | Observation (Labs, Vital Signs, Social History, Core Characteristics); add other assessment types needed for your workflows |
| Results | DiagnosticReport (Results) |
| Notes and reports | DocumentReference (Clinical Notes, Document Information, Labs, Radiology Results), corresponding Binary.Read APIs for attachment content |
| Procedures | Procedure (Orders, Surgeries, Patient-Reported Surgical History) |
| Preventive care | Immunization (Patient Chart), ImmunizationRecommendation |
| Care coordination | CarePlan (Encounter, Longitudinal and applicable inpatient/outpatient variants), CareTeam, Goal |
| Reference details | Practitioner, PractitionerRole, Organization, Location (Organizational Directory) |

Expand to Outside Record variants for imported records, plus devices, questionnaires,
coverage and specialty resources as your questions require. Keep source/provenance
and timestamps so imported and local records are not silently treated as duplicates.
This is a proposed selection, not a claim that every variant has sandbox examples.

Selecting every Incoming API also selects non-FHIR administrative functions and
clinical writes. For maximum chart coverage, start with broad clinical R4 reads;
add writes as separately designed actions with review and explicit confirmation.
The transport intentionally exposes only GET. The clinical layer can restrict
queries to a patient authorized by the host application. It does not establish
the user's identity or authorization itself; those checks are required before
constructing a patient context for a multi-user application.

## What is needed to connect

1. Choose a dedicated sandbox signing key. Confirm whether the old AWS secret is
   still available and is non-production before reusing it, or provision a new
   sandbox key through your chosen secret/key-management service.
2. Export the public JWKS using the CLI and serve it as JSON over public HTTPS,
   without authentication. Configure the matching key ID locally.
3. Enter that exact URL in **Non-Production JWK Set URL**. Add the proposed clinical
   read APIs and select **Save & Ready for Sandbox**. No production readiness is
   required for Epic's public sandbox. Allow up to one hour for app changes.
4. Run `auth-check`, review granted scopes, then test Patient Read/Search and a
   patient-filtered clinical resource. A successful token alone does not prove
   every selected API is usable or populated.
5. Verify resource-specific search support and pagination using synthetic sandbox
   data. Investigate authentication errors separately from unavailable APIs,
   insufficient authorization, invalid search inputs and empty results.

The code and local tests can be completed before these external steps. Live
authentication remains unverified until a signing key and registered public URL
are available. No credentials or public hosting configuration were changed.

For a clinician-facing app launched inside Epic, evaluate a separate **Clinicians
or Administrative Users** SMART app registration with user/patient context. Keep
this backend integration for service workflows; do not assume its broad service
authorization represents the chat user's own permissions. Production deployment
also needs organization-specific enablement and authorization; catalog selection
alone does not grant access to all EHR data.

Sources: [Epic OAuth and JWKS documentation](https://fhir.epic.com/Documentation?docId=oauth2),
[Epic application user context](https://fhir.epic.com/Documentation?docId=usercontext),
[Epic API specifications](https://fhir.epic.com/Sandbox).
