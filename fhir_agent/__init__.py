"""Reusable read-only FHIR client. Importing performs no file or network I/O."""

from .auth import BackendAuth, UserTokenAuth
from .client import FHIRClient, SearchEntry, SearchPage, SearchResult
from .clinical import ClinicalClient, PatientContext
from .config import Settings

__all__ = ["BackendAuth", "UserTokenAuth", "FHIRClient", "SearchEntry", "SearchPage",
           "SearchResult", "ClinicalClient", "PatientContext", "Settings"]
