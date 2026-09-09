"""Synthetic local fixture, mounted only by compose.test.yaml; never calls AWS/Epic."""
import logging
from fhir_agent import SearchEntry, SearchPage, SearchResult
from fhir_agent.service.app import create_app
import uvicorn


class SyntheticClient:
    base_url = "https://synthetic.invalid/FHIR/R4/"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, path):
        return {"resourceType": "Patient", "id": path.split("/")[-1],
                "name": [{"text": "SYNTHETIC TEST PATIENT"}]}

    def search_result(self, resource, params, max_pages):
        patient = dict(params)["patient"]
        data = {"resourceType": resource, "id": "synthetic-resource",
                "patient" if resource == "AllergyIntolerance" else "subject": {"reference": "Patient/" + patient}}
        return SearchResult((SearchPage("https://synthetic.invalid", (SearchEntry(data),)),))


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    uvicorn.run(create_app(client_factory=SyntheticClient), host="0.0.0.0", port=8000,
                access_log=False, log_config=None, proxy_headers=False)
