"""Publish only public KMS verification keys. No Terraform state or secrets are read."""
import argparse
import json
import re
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import requests
from jwt.algorithms import RSAAlgorithm

from fhir_agent.signing import KMSSigner, public_jwks


class PublicationError(RuntimeError):
    pass


def validate_manifest(manifest):
    """Require the exact non-secret output shape before contacting any service."""
    try:
        if set(manifest) != {"account_id", "region", "bucket", "object_key", "distribution_id", "jwks_url", "keys"}:
            raise ValueError
        account, region = manifest["account_id"], manifest["region"]
        if not re.fullmatch(r"[0-9]{12}", account) or not re.fullmatch(r"[a-z]{2}-[a-z]+-[0-9]", region):
            raise ValueError
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", manifest["bucket"]):
            raise ValueError
        if manifest["object_key"] != "jwks.json" or not re.fullmatch(r"[A-Z0-9]+", manifest["distribution_id"]):
            raise ValueError
        url = urlsplit(manifest["jwks_url"])
        if (url.scheme != "https" or not re.fullmatch(r"[a-z0-9]+\.cloudfront\.net", url.netloc)
                or url.path != "/jwks.json" or url.query or url.fragment):
            raise ValueError
        keys = manifest["keys"]
        if not isinstance(keys, list) or not 1 <= len(keys) <= 10:
            raise ValueError
        for key in keys:
            if (set(key) != {"arn", "kid"} or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", key["kid"])
                    or not re.fullmatch(rf"arn:aws:kms:{re.escape(region)}:{account}:key/[a-f0-9-]{{36}}", key["arn"])):
                raise ValueError
        if len({k["kid"] for k in keys}) != len(keys) or len({k["arn"] for k in keys}) != len(keys):
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        raise PublicationError("Invalid publisher manifest; use the Terraform publisher_config output.") from None
    return manifest


def validate_jwks(value):
    """Reject private fields, unexpected formats, weak keys, and ambiguous key IDs."""
    try:
        if not isinstance(value, dict) or set(value) != {"keys"}:
            raise ValueError
        keys = value["keys"]
        if not isinstance(keys, list) or not 1 <= len(keys) <= 100:
            raise ValueError
        for key in keys:
            if not isinstance(key, dict) or set(key) - {"kty", "n", "e", "kid", "alg", "use", "key_ops"}:
                raise ValueError
            if (key.get("kty") != "RSA" or key.get("alg") != "RS384" or key.get("use") != "sig"
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", key.get("kid", ""))
                    or key.get("key_ops", ["verify"]) != ["verify"]):
                raise ValueError
            if RSAAlgorithm.from_jwk(json.dumps(key)).key_size < 2048:
                raise ValueError
        if len({key["kid"] for key in keys}) != len(keys):
            raise ValueError
    except Exception:
        raise PublicationError("JWKS must contain unique RS384 public RSA keys only.") from None
    return value


def merge_jwks(existing, new, retire_kids=()):
    validate_jwks(new)
    old_keys = validate_jwks(existing)["keys"] if existing is not None else []
    retire = set(retire_kids)
    if retire & {key["kid"] for key in new["keys"]}:
        raise PublicationError("Cannot retire a key still present in the Terraform publishing manifest.")
    if retire - {key["kid"] for key in old_keys}:
        raise PublicationError("A requested retirement key is not currently published.")
    merged = {key["kid"]: key for key in old_keys if key["kid"] not in retire}
    for key in new["keys"]:
        previous = merged.get(key["kid"])
        if previous and (previous["n"], previous["e"]) != (key["n"], key["e"]):
            raise PublicationError("Refusing to replace public key material under an existing kid.")
        merged[key["kid"]] = key
    return validate_jwks({"keys": [merged[kid] for kid in sorted(merged)]})


def verify_public_url(url, expected, session, *, attempts=6, sleeper=time.sleep):
    for attempt in range(attempts):
        try:
            with session.get(url, timeout=30, allow_redirects=False, stream=True) as response:
                if response.status_code != 200 or response.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError
                body = bytearray()
                for chunk in response.iter_content(8192):
                    body.extend(chunk)
                    if len(body) > 262144:
                        raise ValueError
                received = validate_jwks(json.loads(body))
                if received != expected:
                    raise ValueError
                return
        except (requests.RequestException, ValueError, PublicationError):
            if attempt + 1 < attempts:
                sleeper(5)
    raise PublicationError("Public JWKS verification failed; upload may have succeeded. Recheck before registering or rotating.")


def publish(manifest, aws_session, http_session, *, apply=False, retire_kids=()):
    validate_manifest(manifest)
    region = manifest["region"]
    identity = aws_session.client("sts", region_name=region).get_caller_identity()
    if identity["Account"] != manifest["account_id"]:
        raise PublicationError("AWS identity does not match the manifest account.")
    kms = aws_session.client("kms", region_name=region)
    keys = [public_jwks(KMSSigner(key["arn"], region, client=kms), key["kid"])["keys"][0]
            for key in manifest["keys"]]
    validate_jwks({"keys": keys})
    s3 = aws_session.client("s3", region_name=region)
    target = {"Bucket": manifest["bucket"], "Key": manifest["object_key"], "ExpectedBucketOwner": manifest["account_id"]}
    listing = s3.list_objects_v2(Bucket=target["Bucket"], Prefix=target["Key"], MaxKeys=2,
                               ExpectedBucketOwner=target["ExpectedBucketOwner"])
    exists = any(item["Key"] == target["Key"] for item in listing.get("Contents", []))
    existing, etag = None, None
    if exists:
        result = s3.get_object(**target)
        try:
            raw = result["Body"].read(262145)
            if len(raw) > 262144:
                raise PublicationError("Existing JWKS exceeds the allowed size.")
            existing = json.loads(raw)
            etag = result["ETag"]
        finally:
            result["Body"].close()
    desired = merge_jwks(existing, {"keys": keys}, retire_kids)
    if not apply:
        return {"applied": False, "key_count": len(desired["keys"]), "message": "Validated; rerun with --apply to publish."}
    # Conditional writes prevent concurrent publishers from discarding each other's keys.
    condition = {"IfMatch": etag} if exists else {"IfNoneMatch": "*"}
    s3.put_object(**target, **condition, Body=json.dumps(desired, sort_keys=True).encode(),
                  ContentType="application/json", CacheControl="public, max-age=300",
                  ServerSideEncryption="AES256")
    cloudfront = aws_session.client("cloudfront", region_name=region)
    result = cloudfront.create_invalidation(DistributionId=manifest["distribution_id"], InvalidationBatch={
        "Paths": {"Quantity": 1, "Items": ["/" + manifest["object_key"]]}, "CallerReference": str(uuid.uuid4())})
    cloudfront.get_waiter("invalidation_completed").wait(
        DistributionId=manifest["distribution_id"], Id=result["Invalidation"]["Id"],
        WaiterConfig={"Delay": 5, "MaxAttempts": 60})
    verify_public_url(manifest["jwks_url"], desired, http_session)
    return {"applied": True, "verified": True, "key_count": len(desired["keys"]), "jwks_url": manifest["jwks_url"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="Terraform publisher_config JSON output")
    parser.add_argument("--profile", help="Existing AWS profile; uses the normal credential chain when omitted")
    parser.add_argument("--apply", action="store_true", help="Upload public JWKS and invalidate its CloudFront cache")
    parser.add_argument("--retire-kid", action="append", default=[], help="Explicitly remove an old published kid after rotation overlap")
    args = parser.parse_args(argv)
    try:
        manifest = validate_manifest(json.loads(args.config.read_text()))
        import boto3
        with requests.Session() as http:
            result = publish(manifest, boto3.Session(profile_name=args.profile), http,
                             apply=args.apply, retire_kids=args.retire_kid)
        print(json.dumps(result, indent=2))
        return 0
    except Exception:
        # AWS/request exceptions can contain credentials or local/remote payloads.
        print("JWKS publication did not complete. Check configuration, permissions, and deployment state; no error payload was logged.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
