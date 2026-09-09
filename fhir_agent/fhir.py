"""Compatibility imports; new integrations should import fhir_agent.client."""
from .client import FHIRClient, SearchEntry, SearchPage, SearchResult

__all__ = ["FHIRClient", "SearchEntry", "SearchPage", "SearchResult"]
