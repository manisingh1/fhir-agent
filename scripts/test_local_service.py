"""Exercise the local synthetic container without printing credentials or bodies."""
import argparse
import sys
import time
from pathlib import Path

import jwt
import requests


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000, help="Local container port (default: 8000)")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")

    # Resolve from this script, so invocation does not depend on the working directory.
    key_path = Path(__file__).resolve().parents[1] / ".runtime" / "grant-key"
    try:
        key = key_path.read_bytes()
        if len(key) < 32:
            raise ValueError
    except (OSError, ValueError):
        print("Local grant key unavailable. Run scripts/init_local_service.py first.", file=sys.stderr)
        return 1

    now = int(time.time())
    token = jwt.encode({
        "iss": "fhir-host-app", "aud": "fhir-service", "sub": "synthetic-test-user",
        "patient": "synthetic", "resources": ["Patient", "Condition"],
        "iat": now, "exp": now + 60,
    }, key, algorithm="HS256")
    checks = [
        ("Patient read", "/v1/patients/synthetic", True, 200),
        ("Conditions search", "/v1/patients/synthetic/conditions", True, 200),
        ("Different patient denied", "/v1/patients/different-patient", True, 403),
        ("Unauthorized operation denied", "/v1/patients/synthetic/encounters", True, 403),
        ("Missing grant denied", "/v1/patients/synthetic", False, 401),
    ]
    failures = 0
    with requests.Session() as session:
        # Keep the local grant off proxy servers and refuse redirects elsewhere.
        session.trust_env = False
        for label, path, authenticated, expected in checks:
            try:
                response = session.get(
                    f"http://127.0.0.1:{args.port}{path}", timeout=10, allow_redirects=False,
                    headers={"Authorization": f"Bearer {token}"} if authenticated else {},
                )
                valid = response.status_code == expected
                if valid and expected == 200:
                    body = response.json()
                    if label == "Patient read":
                        resource = body["resource"]
                        valid = resource["resourceType"] == "Patient" and resource["id"] == "synthetic"
                    else:
                        resources = body["resources"]
                        valid = (body["traversal_complete"] is True and len(resources) == 1
                                 and resources[0]["resourceType"] == "Condition"
                                 and resources[0]["subject"]["reference"] == "Patient/synthetic")
                print(f"{'PASS' if valid else 'FAIL'}: {label} (HTTP {response.status_code}, expected {expected})")
                failures += not valid
            except (requests.RequestException, ValueError, KeyError, TypeError, IndexError):
                print(f"FAIL: {label} — request failed or response had an unexpected format.")
                failures += 1
    print("All local synthetic checks passed." if not failures else
          "Checks failed. Confirm the container is running with compose.test.yaml and the same grant key.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
