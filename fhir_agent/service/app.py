"""Read-only API accepting patient grants issued by a trusted application backend."""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from fhir_agent import ClinicalClient, FHIRClient, PatientContext, Settings
from fhir_agent.errors import FHIRError, PatientContextError

OPERATIONS = {
    "conditions": ("Condition", "get_conditions"),
    "encounters": ("Encounter", "get_encounters"),
    "medications": ("MedicationRequest", "get_medications"),
    "allergies": ("AllergyIntolerance", "get_allergies"),
    "labs": ("Observation", "get_labs"),
    "vitals": ("Observation", "get_vitals"),
}
PERMISSIONS = {"Patient", *(resource for resource, _ in OPERATIONS.values())}


@dataclass(frozen=True)
class ServiceSettings:
    grant_key: bytes = field(repr=False)
    issuer: str = "fhir-host-app"
    audience: str = "fhir-service"

    def __post_init__(self):
        if len(self.grant_key) < 32 or not self.issuer or not self.audience:
            raise ValueError("A random grant key of at least 32 bytes and issuer/audience are required.")

    @classmethod
    def from_env(cls):
        try:
            return cls(Path(os.environ["FHIR_GRANT_KEY_FILE"]).read_bytes(),
                       os.environ.get("FHIR_GRANT_ISSUER", "fhir-host-app"),
                       os.environ.get("FHIR_GRANT_AUDIENCE", "fhir-service"))
        except (OSError, KeyError, ValueError):
            raise ValueError("Service authorization configuration is missing or invalid.") from None


class ResourceResponse(BaseModel):
    resource: Dict[str, Any]


class SearchResponse(BaseModel):
    resources: List[Dict[str, Any]]
    traversal_complete: bool


def create_app(config: Optional[ServiceSettings] = None, client_factory=None):
    config = config or ServiceSettings.from_env()
    if client_factory is None:
        settings = Settings.from_env()
        # A fresh requests session/auth instance per request avoids sharing mutable
        # client state across worker threads. Optimize pooling separately if needed.
        client_factory = lambda: FHIRClient(settings)
    app = FastAPI(title="FHIR service", version="0.1.0", docs_url=None, redoc_url=None,
                  openapi_url=None, redirect_slashes=False)
    bearer = HTTPBearer(auto_error=False)

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # FastAPI's default includes input values; never echo patient/token inputs.
        return JSONResponse({"detail": "Invalid request."}, status_code=422)

    @app.exception_handler(FHIRError)
    async def upstream_failure(request, exc):
        return JSONResponse({"detail": "FHIR request could not be completed."}, status_code=502)

    @app.exception_handler(Exception)
    async def unexpected_failure(request, exc):
        return JSONResponse({"detail": "Request could not be completed."}, status_code=500,
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    def grant(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer)):
        try:
            if credentials is None or len(credentials.credentials) > 8192:
                raise ValueError
            claims = jwt.decode(credentials.credentials, config.grant_key, algorithms=["HS256"],
                                issuer=config.issuer, audience=config.audience,
                                options={"require": ["exp", "iat", "iss", "aud", "sub", "patient", "resources"]})
            if (not isinstance(claims["sub"], str) or not claims["sub"]
                    or not isinstance(claims["iat"], int) or not isinstance(claims["exp"], int)
                    or not 0 < claims["exp"] - claims["iat"] <= 300
                    or not isinstance(claims["patient"], str)
                    or not re.fullmatch(r"[A-Za-z0-9.-]{1,64}", claims["patient"])
                    or claims["patient"] in {".", ".."}
                    or not isinstance(claims["resources"], list)
                    or not claims["resources"]
                    or not all(isinstance(r, str) and r in PERMISSIONS for r in claims["resources"])):
                raise ValueError
            return claims
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise HTTPException(401, "Invalid or missing application grant.",
                                headers={"WWW-Authenticate": "Bearer"}) from None

    def authorize(claims, patient_id, resource):
        if claims["patient"] != patient_id or resource not in claims["resources"]:
            raise HTTPException(403, "Patient or operation is not authorized.")

    @app.get("/healthz", include_in_schema=False)
    def health():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def ready():
        # Configuration loaded successfully. Do not call Epic/KMS from probes.
        return {"status": "ready"}

    @app.get("/v1/patients/{patient_id}", response_model=ResourceResponse, operation_id="readPatient")
    def patient(patient_id: str, claims=Depends(grant)):
        authorize(claims, patient_id, "Patient")
        with client_factory() as client:
            data = client.get("Patient/" + patient_id)
            if data.get("resourceType") != "Patient" or data.get("id") != patient_id:
                raise PatientContextError("Patient response mismatch.")
            return {"resource": data}

    def register_operation(operation, resource, method):
        def search(patient_id: str, claims=Depends(grant)):
            authorize(claims, patient_id, resource)
            with client_factory() as client:
                context = PatientContext(patient_id, frozenset({resource}))
                result = getattr(ClinicalClient(client, context, max_pages=5), method)()
                if result.has_errors or not result.traversal_complete:
                    raise FHIRError("Incomplete search.")
                return {"resources": result.matches, "traversal_complete": True}
        app.get("/v1/patients/{patient_id}/" + operation, response_model=SearchResponse,
                operation_id="list" + operation.capitalize())(search)

    for operation, (resource, method) in OPERATIONS.items():
        register_operation(operation, resource, method)
    return app
