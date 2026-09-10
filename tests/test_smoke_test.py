import json
from unittest.mock import Mock

import pytest

from fhir_agent import Settings
from scripts.smoke_test import smoke_test


def test_smoke_test_returns_summary_without_patient_data():
    factory = Mock()
    client = factory.return_value.__enter__ = Mock()
    factory.return_value.__exit__ = Mock(return_value=False)
    client.return_value.get.return_value = {"resourceType": "Patient", "id": "synthetic", "name": [{"text": "DO_NOT_LOG"}]}
    result = smoke_test(Settings(client_id="test"), "synthetic", client_factory=factory)
    assert result == {"authenticated": True, "patient_read": True, "resource_type": "Patient"}
    assert "DO_NOT_LOG" not in json.dumps(result)
    with pytest.raises(ValueError):
        smoke_test(Settings(client_id="test", base_url="https://production.example/FHIR/R4"), "synthetic", client_factory=factory)
    assert factory.call_count == 1


def test_smoke_test_wrong_patient_is_failure():
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=Mock(get=Mock(return_value={"resourceType": "Patient", "id": "wrong"})))
    factory.return_value.__exit__ = Mock(return_value=False)
    with pytest.raises(ValueError):
        smoke_test(Settings(client_id="test"), "synthetic", client_factory=factory)
