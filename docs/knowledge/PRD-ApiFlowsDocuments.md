# API Flows — Documents

---

## POST /api/documents/

```
Client
  │
  └─ POST /api/documents/
      multipart/form-data: { file }
      Authorization: Bearer <jwt>
      │
      ├─ backend: validate JWT → resolve user
      │
      ├─ backend: rate-limit check (sliding window, Redis)
      │     key: ratelimit:{user_id}
      │     → 429 Too Many Requests if exceeded
      │
      ├─ backend: validate file
      │     extension must be pdf | docx | txt | md   → 422 if not
      │     file_size ≤ MAX_UPLOAD_SIZE_MB (default 50 MB)  → 413 if too large
      │     file_size > 0                              → 422 if empty
      │
      ├─ backend: store file
      │     file_storage.save(file, doc_id)
      │     STORAGE_BACKEND=local  → /data/uploads/{doc_id}.{ext}
      │     STORAGE_BACKEND=s3    → S3 bucket key
      │     → 500 if storage fails
      │
      ├─ backend: persist document record
      │     INSERT documents (id, user_id, filename, file_path, file_type, file_size, status='processing')
      │
      ├─ backend: publish ingestion job
      │     kafka_producer.send("doc.ingest", {
      │       doc_id, user_id, file_path, file_type, filename
      │     })
      │     [Kafka publish failure is logged but does NOT roll back the DB insert]
      │     [document stays status='processing' until manually retried]
      │
      └─ response 201 Created
            { doc_id, filename, file_type, file_size, status: "processing", created_at }

                          │ (async, Kafka consumer)
                          ▼
                     Worker (doc.ingest)
                       │
                       ├─ extract text
                       │     pdf  → pdfplumber
                       │     docx → python-docx
                       │     txt/md → read raw
                       │
                       ├─ detect language
                       │     langdetect → "en" | "ja"
                       │
                       ├─ chunk text
                       │     en → word split (CHUNK_SIZE=512, CHUNK_OVERLAP=64)
                       │     ja → fugashi (MeCab) tokenizer
                       │
                       ├─ generate embeddings  [retry: 5 attempts, 30s on rate-limit]
                       │     openai  → text-embedding-3-small (1536 dims, default)
                       │     voyage  → voyage-3
                       │     cohere  → embed-english-v3.0
                       │     mock    → zeros (tests only)
                       │
                       ├─ store chunks
                       │     INSERT chunks (doc_id, user_id, content, embedding, language, chunk_index, metadata)
                       │
                       └─ UPDATE documents SET status = 'ready'
                             (status = 'failed' on unrecoverable error)
```

---

## GET /api/documents/

```
Client
  │
  └─ GET /api/documents/?page=1&per_page=20
      Authorization: Bearer <jwt>
      │
      ├─ backend: validate JWT → resolve user
      │
      ├─ backend: rate-limit check
      │
      ├─ backend: query documents
      │     SELECT documents
      │     WHERE user_id = <jwt.sub>
      │     ORDER BY created_at DESC
      │     LIMIT per_page OFFSET (page-1)*per_page
      │
      └─ response 200 OK
            {
              page, per_page,
              documents: [{ doc_id, filename, file_type, file_size, status, created_at }]
            }
```

---

## DELETE /api/documents/{doc_id}

```
Client
  │
  └─ DELETE /api/documents/{doc_id}
      Authorization: Bearer <jwt>
      │
      ├─ backend: validate JWT → resolve user
      │
      ├─ backend: validate doc_id is a valid UUID
      │     → 422 Unprocessable Entity if malformed
      │
      ├─ backend: verify ownership
      │     SELECT documents WHERE id=doc_id AND user_id=<jwt.sub>
      │     → 404 Not Found if missing or belongs to another user
      │
      ├─ backend: publish delete event
      │     kafka_producer.send("doc.delete", { doc_id })
      │     [failure is logged but does not block the delete]
      │
      ├─ backend: delete DB record
      │     DELETE documents WHERE id=doc_id
      │     → chunks cascade via FK (ON DELETE CASCADE)
      │
      ├─ backend: delete stored file
      │     file_storage.delete(file_path)
      │     [failure is logged; DB record is already gone]
      │
      └─ response 200 OK
            { doc_id, deleted: true }

                          │ (async, Kafka consumer)
                          ▼
                     Worker (doc.delete)
                       │
                       └─ DELETE chunks WHERE doc_id = ?
                             (vectors removed from pgvector)
```
