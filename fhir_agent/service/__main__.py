"""Run without request or traceback logging, which can expose clinical data."""
import logging
import sys
import uvicorn

if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    try:
        uvicorn.run("fhir_agent.service.app:create_app", factory=True, host="0.0.0.0", port=8000,
                    access_log=False, log_config=None, proxy_headers=False,
                    limit_concurrency=32, timeout_graceful_shutdown=30)
    except Exception:
        print("FHIR service could not start; check runtime configuration.", file=sys.stderr)
        sys.exit(1)
