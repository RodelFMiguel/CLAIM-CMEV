"""Evidence bytes remain behind the API. Object keys never use filenames."""
from pathlib import Path
from .runtime import setting


class Storage:
    def __init__(self, directory=None):
        self.s3 = setting("STORAGE_BACKEND", "local") == "s3"
        self.directory = Path(directory or setting("STORAGE_PATH", "runtime/evidence"))
        if self.s3:
            import boto3
            self.bucket = setting("OBJECT_STORE_BUCKET", "cmev-evidence", "S3_BUCKET")
            self.client = boto3.client("s3", endpoint_url=setting("OBJECT_STORE_ENDPOINT", alias="S3_ENDPOINT_URL"),
                aws_access_key_id=setting("OBJECT_STORE_ACCESS_KEY", alias="AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=setting("OBJECT_STORE_SECRET_KEY", alias="AWS_SECRET_ACCESS_KEY"), region_name="us-east-1")
        else:
            self.directory.mkdir(parents=True, exist_ok=True)

    def initialize(self):
        if self.s3:
            from botocore.exceptions import ClientError
            try:
                self.client.head_bucket(Bucket=self.bucket)
            except ClientError as exc:
                if exc.response["Error"]["Code"] not in ("404", "NoSuchBucket"):
                    raise
                self.client.create_bucket(Bucket=self.bucket)

    def write(self, key, data, media_type):
        if self.s3:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=media_type)
        else:
            (self.directory / key).write_bytes(data)

    def read(self, key):
        if self.s3:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        return (self.directory / key).read_bytes()

    def ready(self):
        if self.s3:
            self.client.head_bucket(Bucket=self.bucket)
        else:
            if not self.directory.is_dir():
                raise RuntimeError("storage unavailable")
