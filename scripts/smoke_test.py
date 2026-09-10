"""Verify Epic public-sandbox authentication and a Patient read without printing clinical data."""
import argparse
import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from fhir_agent import FHIRClient, Settings


def smoke_test(settings, patient_id, *, client_factory=FHIRClient):
    if (settings.base_url.rstrip("/") != Settings.base_url.rstrip("/")
            or settings.token_url != Settings.token_url):
        raise ValueError("Smoke test is restricted to Epic's public sandbox.")
    if not isinstance(patient_id, str) or not re.fullmatch(r"[A-Za-z0-9.-]{1,64}", patient_id) or patient_id in (".", ".."):
        raise ValueError("A synthetic sandbox FHIR Patient ID is required.")
    with client_factory(settings) as client:
        patient = client.get("Patient/" + patient_id)
        if patient.get("resourceType") != "Patient" or patient.get("id") != patient_id:
            raise ValueError("FHIR response does not match the requested Patient.")
    return {"authenticated": True, "patient_read": True, "resource_type": "Patient"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patient-id", required=True, help="Known synthetic Epic sandbox FHIR ID, not an MRN")
    args = parser.parse_args(argv)
    load_dotenv(Path.cwd() / ".env")
    try:
        result = smoke_test(Settings.from_env(), args.patient_id)
        print(json.dumps(result, indent=2))
        return 0
    except Exception:
        print("Sandbox smoke test failed. Check configuration, authorization, and the synthetic patient ID; no response payload was logged.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
