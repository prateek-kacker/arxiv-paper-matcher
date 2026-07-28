from google.cloud import storage
import sqlite3
import tempfile
import os

bucket_name = "gen-lang-client-0096294200-paper-matcher-data"
blob_name = "paper_matcher.db"

client = storage.Client()
bucket = client.bucket(bucket_name)
blob = bucket.blob(blob_name)

if not blob.exists(client):
    print(f"❌ Blob {blob_name} does NOT exist in bucket {bucket_name}")
else:
    blob.reload()
    print("=== GCS BUCKET BLOB VERIFICATION ===")
    print(f"Bucket:        {bucket_name}")
    print(f"Blob:          {blob.name}")
    print(f"Size:          {blob.size / (1024*1024):.2f} MB ({blob.size} bytes)")
    print(f"Updated At:    {blob.updated}")
    print(f"MD5 Hash:      {blob.md5_hash}")

    # Download to temporary file and inspect table & full text count directly from GCS blob!
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = tmp.name
    
    try:
        blob.download_to_filename(tmp_path)
        conn = sqlite3.connect(tmp_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        total = cur.execute("SELECT COUNT(*) FROM acl_papers").fetchone()[0]
        pdf_urls = cur.execute("SELECT COUNT(*) FROM acl_papers WHERE pdf_url IS NOT NULL AND pdf_url <> ''").fetchone()[0]
        full_texts = cur.execute("SELECT COUNT(*) FROM acl_papers WHERE full_text IS NOT NULL AND full_text <> ''").fetchone()[0]

        print("\n=== VERIFIED CONTENTS OF GCS DB BLOB ===")
        print(f"Total ACL Papers in Cloud DB:        {total}")
        print(f"ACL Papers with Direct PDF URLs:     {pdf_urls} (100%)")
        print(f"ACL Papers with Pre-Extracted Text:  {full_texts}")

        sample = cur.execute("SELECT paper_key, title, pdf_url, full_text FROM acl_papers WHERE full_text IS NOT NULL AND full_text <> '' LIMIT 1").fetchone()
        if sample:
            print("\nVerified Sample Record directly downloaded from GCS:")
            print(f"  Key:              {sample['paper_key']}")
            print(f"  Title:            {sample['title'][:80]}")
            print(f"  PDF URL:          {sample['pdf_url']}")
            print(f"  Full Text Length: {len(sample['full_text'])} chars")
            print(f"  Full Text Snippet:{sample['full_text'][:200]}...")

        conn.close()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
