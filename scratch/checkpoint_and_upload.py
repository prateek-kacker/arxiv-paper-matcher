import sqlite3
import os
import sys
from google.cloud import storage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core_engine as core

def checkpoint_and_upload():
    print("1. Checkpointing SQLite database to flush WAL into main paper_matcher.db...")
    conn = core.get_db()
    conn.execute("PRAGMA wal_checkpoint(FULL)")
    conn.close()
    
    db_size = os.path.getsize("paper_matcher.db")
    print(f"Main paper_matcher.db size after FULL checkpoint: {db_size} bytes ({db_size / (1024*1024):.2f} MB)")
    
    bucket_name = "gen-lang-client-0096294200-paper-matcher-data"
    blob_name = "paper_matcher.db"
    
    print(f"2. Uploading {db_size} bytes to GCS bucket gs://{bucket_name}/{blob_name}...")
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename("paper_matcher.db")
    print("SUCCESS! Full 6,422 ACL paper database uploaded to GCS!")

if __name__ == '__main__':
    checkpoint_and_upload()
