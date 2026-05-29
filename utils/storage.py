"""utils/storage.py — Supabase Storage S3 upload utility."""
import os
import boto3
from botocore.config import Config

def upload_to_supabase(file_obj, folder_name: str, filename: str) -> str | None:
    """
    Uploads a file-like object directly to the Supabase Storage bucket 'Images'.
    Returns the absolute public URL of the uploaded object, or None if failed.
    """
    if not file_obj:
        return None

    # Load S3 compatible settings from environment
    key_id = os.getenv("SUPABASE_STORAGE_BUCKET_KEY_ID")
    secret_key = os.getenv("SUPABASE_STORAGE_BUCKET_SECRET_ACCESS_KEY")
    endpoint = os.getenv("S3_PROTOCOL_ENDPOINT")
    region = os.getenv("S3_REGION", "ap-northeast-2")

    if not all([key_id, secret_key, endpoint]):
        print("[STORAGE] Error: Missing Supabase S3 configuration environment variables.")
        return None

    try:
        s3_client = boto3.client(
            's3',
            endpoint_url=endpoint,
            aws_access_key_id=key_id,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(signature_version='s3v4')
        )

        bucket_name = "Images"
        object_key = f"{folder_name}/{filename}"

        # Ensure the file stream is at the beginning
        file_obj.seek(0)
        content_type = getattr(file_obj, 'content_type', 'application/octet-stream')

        # Upload the object
        s3_client.put_object(
            Bucket=bucket_name,
            Key=object_key,
            Body=file_obj.read(),
            ContentType=content_type
        )

        # Extract project reference ID dynamically from S3 endpoint
        # e.g., https://gwypqtuvmbnrjexgqqkf.storage.supabase.co/storage/v1/s3 -> gwypqtuvmbnrjexgqqkf
        project_id = endpoint.split("//")[1].split(".")[0]

        # Construct the absolute public URL
        public_url = f"https://{project_id}.supabase.co/storage/v1/object/public/{bucket_name}/{object_key}"
        return public_url

    except Exception as e:
        print(f"[STORAGE ERROR] Failed to upload {folder_name}/{filename}: {e}")
        return None
