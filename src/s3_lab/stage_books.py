#!/usr/bin/env python3
"""Download a Gutenberg corpus and publish its books and index in S3."""

import json
import logging
import os
import sys
import time
from urllib.parse import urlparse

import boto3
import requests
from botocore.exceptions import BotoCoreError, ClientError

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)
s3 = boto3.client("s3", region_name="us-east-1")


def parse_args():
    """Return the book-list path, index path, and bucket name."""
    if len(sys.argv) < 4:
        log.error("Usage: stage_books.py LIST_FILE INDEX_PATH BUCKET")
        sys.exit(1)
    log.info("Reading command-line arguments.")
    return sys.argv[1], sys.argv[2], sys.argv[3]


def download_books(list_file, dest_dir):
    """Download missing books and return paths to all available books."""
    file_paths = []
    try:
        os.makedirs(dest_dir, exist_ok=True)
        with open(list_file, encoding="utf-8") as source:
            urls = [line.strip() for line in source if line.strip()]
    except OSError as e:
        log.error(f"Error preparing downloads: {e}")
        return file_paths

    for url in urls:
        filename = os.path.basename(urlparse(url).path)
        if not filename or filename in (".", ".."):
            log.error(f"URL has no valid book filename: {url}")
            continue
        path = os.path.join(dest_dir, filename)
        if os.path.exists(path):
            log.info(f"Using existing book: {path}")
            file_paths.append(path)
            continue

        # Write to a temporary file so failed downloads are not cached as books.
        temp_path = path + ".part"
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            with open(temp_path, "wb") as output:
                output.write(response.content)
            os.replace(temp_path, path)
            file_paths.append(path)
            log.info(f"Downloaded: {path}")
        except requests.RequestException as e:
            log.error(f"Error downloading {url}: {e}")
        except OSError as e:
            log.error(f"Error saving {path}: {e}")
        finally:
            # Throttle attempted downloads, including failed requests.
            time.sleep(1)

    log.info(f"Books available locally: {len(file_paths)}")
    return file_paths


def create_bucket(bucket):
    """Return whether the bucket exists and is accessible after this call."""
    try:
        s3.head_bucket(Bucket=bucket)
        log.info(f"Using existing bucket: {bucket}")
        return True
    except ClientError as e:
        # A forbidden bucket is not evidence that its name is available.
        code = str(e.response.get("Error", {}).get("Code", ""))
        if code not in ("404", "NoSuchBucket", "NotFound"):
            log.error(f"Error checking bucket {bucket}: {e}")
            return False
        log.info(f"Bucket does not exist; creating: {bucket}")
    except BotoCoreError as e:
        log.error(f"Error checking bucket {bucket}: {e}")
        return False

    try:
        # us-east-1 requires no LocationConstraint.
        s3.create_bucket(Bucket=bucket)
        log.info(f"Created bucket: {bucket}")
        return True
    except (ClientError, BotoCoreError) as e:
        log.error(f"Error creating bucket {bucket}: {e}")
        return False


def make_public(bucket):
    """Allow public reads of objects in the bucket."""
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": "*",
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{bucket}/*",
            }
        ],
    }
    try:
        # Disable the bucket block before attaching its public-read policy.
        s3.put_public_access_block(
            Bucket=bucket,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": False,
                "IgnorePublicAcls": False,
                "BlockPublicPolicy": False,
                "RestrictPublicBuckets": False,
            },
        )
        s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps(policy))
        log.info(f"Enabled public object reads: {bucket}")
        return True
    except (ClientError, BotoCoreError) as e:
        log.error(f"Error making bucket {bucket} public: {e}")
        return False


def upload_files(bucket, file_paths):
    """Upload books at the bucket root and return the successful count."""
    uploaded = 0
    for path in file_paths:
        key = os.path.basename(path)
        try:
            with open(path, "rb") as source:
                s3.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=source,
                    ContentType="text/plain; charset=utf-8",
                )
            uploaded += 1
            log.info(f"Uploaded {path} to s3://{bucket}/{key}")
        except OSError as e:
            log.error(f"Error opening book {path}: {e}")
        except (ClientError, BotoCoreError) as e:
            log.error(f"Error uploading {path}: {e}")
    log.info(f"Uploaded {uploaded} of {len(file_paths)} books.")
    return uploaded


def upload_index(bucket, index_path):
    """Upload the HTML index and return whether the upload succeeded."""
    try:
        with open(index_path, "rb") as source:
            s3.put_object(
                Bucket=bucket,
                Key="index.html",
                Body=source,
                ContentType="text/html",
            )
        log.info(f"Uploaded index: {index_path}")
        return True
    except FileNotFoundError:
        log.error(f"Local file not found: {index_path}")
    except OSError as e:
        log.error(f"Error opening index {index_path}: {e}")
    except (ClientError, BotoCoreError) as e:
        log.error(f"Error uploading index: {e}")
    return False


def main():
    """Stage the corpus and report its public index URL."""
    list_file, index_path, bucket = parse_args()
    file_paths = download_books(list_file, "books")
    if not file_paths:
        log.error("Staging failed: no books are available to upload.")
        sys.exit(1)
    if not create_bucket(bucket):
        log.error("Staging failed: bucket could not be created or accessed.")
        sys.exit(1)
    if not make_public(bucket):
        log.error("Staging failed: bucket could not be made public.")
        sys.exit(1)

    uploaded = upload_files(bucket, file_paths)
    index_uploaded = upload_index(bucket, index_path)
    log.info(f"Index URL: https://{bucket}.s3.amazonaws.com/index.html")

    if uploaded != len(file_paths) or not index_uploaded:
        log.error("Staging failed: one or more uploads were unsuccessful.")
        sys.exit(1)
    log.info(f"Staging successful: uploaded {uploaded} books and index.html.")


if __name__ == "__main__":
    main()
