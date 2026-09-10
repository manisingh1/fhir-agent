"""Require the expected prevent_destroy failure in a mocked key-rotation plan."""
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    module = Path(__file__).resolve().parents[1] / "infra/modules/epic-auth"
    try:
        result = subprocess.run(
            [os.environ.get("TERRAFORM_BIN", "terraform"), f"-chdir={module}",
             "test", "-test-directory=tests/deletion", "-json"],
            capture_output=True, text=True, timeout=180, check=False,
        )
        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        errors = [event["diagnostic"] for event in events
                  if event.get("type") == "diagnostic" and event["diagnostic"]["severity"] == "error"]
        expected = (result.returncode == 1 and len(errors) == 1
                    and errors[0]["summary"] == "Instance cannot be destroyed"
                    and 'aws_kms_key.signing["v1"]' in errors[0]["detail"]
                    and "lifecycle.prevent_destroy" in errors[0]["detail"])
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
        expected = False
    if not expected:
        print("FAIL: expected exactly the retained-key prevent_destroy diagnostic.", file=sys.stderr)
        return 1
    print("PASS: removing the retained KMS key is blocked by prevent_destroy (mocked AWS).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
