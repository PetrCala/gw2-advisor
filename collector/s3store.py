"""S3 storage with a hard total-size gate.

The Actions workflow is the only principal with write access to the bucket,
so refusing writes here is an effective cap on how large the bucket can grow:
a runaway bug fails the job instead of growing the bill. Lifecycle rules
(see infra/setup_aws.py) independently bound the raw/ prefix.
"""

import gzip
import json
import os

import boto3
from botocore.exceptions import ClientError

QUOTA_BYTES = int(os.environ.get("QUOTA_BYTES", str(25 * 1024**3)))
MAX_OBJECT_BYTES = int(os.environ.get("MAX_OBJECT_BYTES", str(64 * 1024**2)))


class Store:
    def __init__(self, bucket):
        self.bucket = bucket
        self.s3 = boto3.client("s3")
        self._total = None

    def total_size(self):
        if self._total is None:
            total = 0
            paginator = self.s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket):
                for obj in page.get("Contents", []):
                    total += obj["Size"]
            self._total = total
        return self._total

    def list_keys(self, prefix):
        keys = []
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return sorted(keys)

    def get_json_gz(self, key):
        """Return the decoded object, or None if the key does not exist."""
        try:
            body = self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None
            raise
        return json.loads(gzip.decompress(body))

    def put_json_gz(self, key, payload):
        data = gzip.compress(json.dumps(payload, separators=(",", ":")).encode())
        self.put_bytes(key, data)

    def put_bytes(self, key, data):
        if len(data) > MAX_OBJECT_BYTES:
            raise RuntimeError(
                f"refusing {key}: {len(data)} bytes exceeds per-object cap of {MAX_OBJECT_BYTES}"
            )
        if self.total_size() + len(data) > QUOTA_BYTES:
            raise RuntimeError(
                f"refusing {key}: bucket at {self.total_size()} bytes, "
                f"upload of {len(data)} would exceed the {QUOTA_BYTES} byte quota"
            )
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data)
        self._total += len(data)
