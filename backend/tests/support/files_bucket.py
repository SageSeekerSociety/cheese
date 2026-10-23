"""An S3-compatible bucket for tests: moto's server, on a free local port.

What it checks is the S3 protocol the platform speaks to the files bucket —
presigned PUTs, ranged GETs, copies — through the platform's own client, not a
hand-written imitation of the parts a test happens to use.
"""

import contextlib
import uuid
from collections.abc import Iterator

import boto3
from moto.server import ThreadedMotoServer

from app.core.blob_store import BucketStore
from app.core.storage import S3StorageBackend

ACCESS_KEY = "test"
SECRET_KEY = "test"
REGION = "us-east-1"


@contextlib.contextmanager
def files_bucket() -> Iterator[dict[str, str]]:
    """A fresh bucket, as the ``files_bucket*`` settings that reach it."""
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    try:
        host, port = server.get_host_and_port()
        endpoint = f"http://{host}:{port}"
        name = f"files-{uuid.uuid4().hex[:12]}"
        boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=ACCESS_KEY,
            aws_secret_access_key=SECRET_KEY,
            region_name=REGION,
        ).create_bucket(Bucket=name)
        yield {
            "files_bucket": name,
            "files_bucket_endpoint_url": endpoint,
            "files_bucket_access_key": ACCESS_KEY,
            "files_bucket_secret_key": SECRET_KEY,
            "files_bucket_region": REGION,
        }
    finally:
        server.stop()


def bucket_store(config: dict[str, str]) -> BucketStore:
    return BucketStore(
        S3StorageBackend(
            bucket=config["files_bucket"],
            endpoint_url=config["files_bucket_endpoint_url"],
            access_key=config["files_bucket_access_key"],
            secret_key=config["files_bucket_secret_key"],
            region=config["files_bucket_region"],
        )
    )
