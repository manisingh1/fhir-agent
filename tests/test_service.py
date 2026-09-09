import secrets
import time
from unittest.mock import Mock

import jwt
import pytest
from fastapi.testclient import TestClient
from fhir_agent import SearchEntry, SearchPage, SearchResult
from fhir_agent.errors import TransportError
from fhir_agent.service.app import ServiceSettings, create_app


@pytest.fixture
def setup():
    key = secrets.token_bytes(32)
    client = Mock(base_url="https://example.invalid/FHIR/R4/")
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.get.return_value = {"resourceType": "Patient", "id": "synthetic"}
    factory = Mock(return_value=client)
    api = TestClient(create_app(ServiceSettings(key), factory), raise_server_exceptions=False)
    return key, client, factory, api


def grant(key, **changes):
    now = int(time.time())
    claims = dict(iss="fhir-host-app", aud="fhir-service", sub="synthetic-user",
                  iat=now, exp=now+60, patient="synthetic", resources=["Patient", "Condition"])
    claims.update(changes)
    return {"Authorization": "Bearer " + jwt.encode(claims, key, algorithm="HS256")}


def test_health_without_upstream_access(setup):
    _, _, factory, api = setup
    assert api.get('/healthz').status_code == 200
    assert api.get('/readyz').status_code == 200
    factory.assert_not_called()


@pytest.mark.parametrize('changes', [dict(exp=1), dict(aud='wrong'), dict(iss='wrong'),
    dict(patient='../secret'), dict(resources=['*']), dict(resources='Patient'),
    dict(exp=int(time.time())+600), dict(sub=''), dict(iat=int(time.time())+600)])
def test_invalid_grants_never_contact_fhir(setup, changes):
    key, _, factory, api = setup
    assert api.get('/v1/patients/synthetic', headers=grant(key, **changes)).status_code == 401
    factory.assert_not_called()


def test_missing_or_forged_grant(setup):
    _, _, factory, api = setup
    assert api.get('/v1/patients/synthetic').status_code == 401
    assert api.get('/v1/patients/synthetic', headers=grant(secrets.token_bytes(32))).status_code == 401
    factory.assert_not_called()


@pytest.mark.parametrize('path,permissions', [('/v1/patients/other',['Patient']),
    ('/v1/patients/synthetic',['Condition']), ('/v1/patients/synthetic/conditions',['Patient'])])
def test_patient_and_operation_scope(setup, path, permissions):
    key, _, factory, api = setup
    assert api.get(path, headers=grant(key, resources=permissions)).status_code == 403
    factory.assert_not_called()


def test_patient_response_and_cache_headers(setup):
    key, client, _, api = setup
    response = api.get('/v1/patients/synthetic', headers=grant(key))
    assert response.status_code == 200
    assert response.json()['resource']['id'] == 'synthetic'
    assert response.headers['cache-control'] == 'no-store'
    client.__exit__.assert_called_once()
    client.get.return_value = {'resourceType':'Patient','id':'other'}
    assert api.get('/v1/patients/synthetic', headers=grant(key)).status_code == 502


def test_search_verifies_subject_and_fails_closed(setup):
    key, client, _, api = setup
    data = {'resourceType':'Condition', 'subject':{'reference':'Patient/synthetic'}}
    client.search_result.return_value = SearchResult((SearchPage('https://example.invalid', (SearchEntry(data),)),))
    assert api.get('/v1/patients/synthetic/conditions',headers=grant(key)).status_code == 200
    client.search_result.assert_called_once_with('Condition', [('patient','synthetic')],5)
    data['subject']['reference'] = 'Patient/other'
    response = api.get('/v1/patients/synthetic/conditions',headers=grant(key))
    assert response.status_code == 502 and 'other' not in response.text


@pytest.mark.parametrize('error',[TransportError('DO_NOT_LEAK'), RuntimeError('DO_NOT_LEAK')])
def test_errors_never_echo_upstream_details(setup, error):
    key, client, _, api = setup
    client.get.side_effect = error
    response = api.get('/v1/patients/synthetic',headers=grant(key))
    assert response.status_code in (500,502)
    assert 'DO_NOT_LEAK' not in response.text


def test_no_arbitrary_proxy_or_write_routes(setup):
    key, _, factory, api = setup
    assert api.post('/v1/patients/synthetic',headers=grant(key)).status_code == 405
    assert api.get('/v1/proxy',headers=grant(key)).status_code == 404
    assert api.get('/openapi.json').status_code == 404
    factory.assert_not_called()


def test_missing_config_fails_at_startup(monkeypatch):
    monkeypatch.delenv('FHIR_GRANT_KEY_FILE',raising=False)
    with pytest.raises(ValueError):
        create_app()


@pytest.mark.parametrize('route,resource,subject_key', [
    ('encounters','Encounter','subject'), ('medications','MedicationRequest','subject'),
    ('allergies','AllergyIntolerance','patient'), ('labs','Observation','subject'),
    ('vitals','Observation','subject')])
def test_clinical_routes_keep_patient_filter(setup, route, resource, subject_key):
    key, client, _, api = setup
    data = {'resourceType':resource, subject_key:{'reference':'Patient/synthetic'}}
    client.search_result.return_value = SearchResult((SearchPage('https://example.invalid', (SearchEntry(data),)),))
    response = api.get('/v1/patients/synthetic/'+route,headers=grant(key, resources=[resource]))
    assert response.status_code == 200
    assert client.search_result.call_args.args[0] == resource
    assert ('patient','synthetic') in client.search_result.call_args.args[1]


def test_search_outcome_errors_are_not_returned_as_success(setup):
    key, client, _, api = setup
    outcome = {'resourceType':'OperationOutcome','issue':[{'severity':'error','diagnostics':'DO_NOT_LEAK'}]}
    client.search_result.return_value = SearchResult((SearchPage('https://example.invalid', (SearchEntry(outcome, mode='outcome'),)),))
    response = api.get('/v1/patients/synthetic/conditions',headers=grant(key))
    assert response.status_code == 502
    assert 'DO_NOT_LEAK' not in response.text
