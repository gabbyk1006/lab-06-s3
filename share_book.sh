#!/bin/bash
set -euo pipefail

# Require a book URL, bucket name, and expiration.
if [[ $# -ne 3 ]]; then
    echo "Usage: $0 BOOK_URL BUCKET_NAME EXPIRATION_SECONDS" >&2
    exit 1
fi

BOOK_URL="$1"
BUCKET="$2"
EXPIRATION="$3"

# Keep the filename from the URL.
FILENAME="$(basename "$BOOK_URL")"

# Download the book into the books folder.
mkdir -p "books"
curl --fail --location "$BOOK_URL" --output "books/$FILENAME"

# Create the bucket if it does not exist or cannot be listed.
if ! aws s3 ls "s3://$BUCKET" > /dev/null 2>&1; then
    aws s3 mb "s3://$BUCKET"
fi

# Upload without granting public access.
aws s3 cp "books/$FILENAME" "s3://$BUCKET/$FILENAME" \
    --content-type "text/plain; charset=utf-8"

# Generate and print the temporary download link.
echo "Presigned URL:"
aws s3 presign "s3://$BUCKET/$FILENAME" \
    --expires-in "$EXPIRATION"
