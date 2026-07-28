import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core_engine as core
from google.cloud import storage

def sync_to_gcs():
    print("Uploading local paper_matcher.db (with 6,422 ACL 2026 papers) to GCS...")
    bucket_name = "gen-lang-client-0096294200-paper-matcher-data"
    blob_name = "paper_matcher.db"
    
    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_filename("paper_matcher.db")
        print(f"✅ Successfully uploaded paper_matcher.db to gs://{bucket_name}/{blob_name}!")
    except Exception as e:
        print(f"❌ Failed to upload to GCS: {e}")

if __name__ == '__main__':
    sync_to_gcs()
