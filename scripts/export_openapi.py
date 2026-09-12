"""Export static API schema without credentials or contacting AWS/Epic."""
import json
import secrets
from pathlib import Path
from fhir_agent.service.app import ServiceSettings, create_app

if __name__ == '__main__':
    app = create_app(ServiceSettings(secrets.token_bytes(32)), client_factory=lambda: None)
    Path('examples/typescript/openapi.json').write_text(json.dumps(app.openapi(), indent=2) + '\n')
