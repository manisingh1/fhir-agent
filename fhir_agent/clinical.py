"""Narrow chart operations bound to patient context supplied by a trusted host app."""
import re
from dataclasses import dataclass, field
from datetime import date
from typing import FrozenSet

from .errors import PatientContextError

RESOURCE_TYPES = frozenset({"Condition", "MedicationRequest", "Observation", "Encounter", "AllergyIntolerance"})


def _patient_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9.-]{1,64}", value):
        raise PatientContextError("A valid FHIR patient ID is required.")
    return value


@dataclass(frozen=True)
class PatientContext:
    """Construct only after the application authorizes the user for this patient.

    This is a local restriction, not proof of authorization and not a substitute
    for EHR permissions. Do not accept these fields directly from model tool input.
    """
    patient_id: str = field(repr=False)
    allowed_resource_types: FrozenSet[str]

    def __post_init__(self):
        _patient_id(self.patient_id)
        object.__setattr__(self, "allowed_resource_types", frozenset(self.allowed_resource_types))
        if not self.allowed_resource_types <= RESOURCE_TYPES:
            raise PatientContextError("Unsupported clinical resource permission.")


class ClinicalClient:
    def __init__(self, client, context, *, max_pages=20):
        if not isinstance(context, PatientContext):
            raise PatientContextError("A trusted patient context is required.")
        self.client, self.context, self.max_pages = client, context, max_pages

    def _search(self, resource, patient_id, extra=()):
        patient_id = self.context.patient_id if patient_id is None else _patient_id(patient_id)
        if patient_id != self.context.patient_id or resource not in self.context.allowed_resource_types:
            raise PatientContextError("The requested patient or resource is outside the authorized context.")
        result = self.client.search_result(resource, [("patient", patient_id), *extra], self.max_pages)
        expected = {"Patient/" + patient_id, self.client.base_url + "Patient/" + patient_id}
        for entry in result.entries:
            if entry.mode == "outcome":
                continue
            data = entry.resource
            # These helpers do not request _include. Reject unexpected resource types
            # or unresolvable subjects before returning any clinical data to a caller.
            subject = data.get("patient" if resource == "AllergyIntolerance" else "subject", {})
            if data.get("resourceType") != resource or not isinstance(subject, dict) or subject.get("reference") not in expected:
                raise PatientContextError("FHIR results could not be verified against the authorized patient.")
        return result

    def get_conditions(self, patient_id=None):
        return self._search("Condition", patient_id)

    def get_medications(self, patient_id=None):
        """Medication orders only; not a reconciled list of medications being taken."""
        return self._search("MedicationRequest", patient_id)

    def get_encounters(self, patient_id=None):
        return self._search("Encounter", patient_id)

    def get_allergies(self, patient_id=None):
        return self._search("AllergyIntolerance", patient_id)

    def get_labs(self, patient_id=None, *, since=None):
        return self._observations(patient_id, "laboratory", since)

    def get_vitals(self, patient_id=None, *, since=None):
        return self._observations(patient_id, "vital-signs", since)

    def _observations(self, patient_id, category, since):
        params = [("category", category)]
        if since is not None:
            try:
                since = date.fromisoformat(since).isoformat()
            except (ValueError, TypeError):
                raise PatientContextError("since must be a calendar date in YYYY-MM-DD format.") from None
            params.append(("date", "ge" + since))
        return self._search("Observation", patient_id, params)
