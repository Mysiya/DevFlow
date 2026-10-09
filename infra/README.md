# Optional Milvus infrastructure

The application supports BM25 and Milvus hybrid retrieval. v0.8 has run the official Milvus Lite 3.2.1 server with offline Chinese CPU embeddings on Windows; see the root README for the separate `.vector-venv` and local startup scripts. Its listener is loopback-only and requests are serialized with `--max-workers 1`. This unauthenticated Lite route is for local development; it is not included in the application Compose image.

Run `scripts/prepare-milvus.ps1` from the repository root to download the official v3.0.2 Compose configuration. The helper binds its published ports to loopback and does not start Docker.

Source: https://github.com/milvus-io/milvus/releases/download/v3.0.2/milvus-standalone-docker-compose.yaml

After Docker Desktop is available, the optional standalone stack can be launched separately:

```powershell
docker compose -f infra/compose.milvus.yaml up -d
```

The downloaded configuration includes its own etcd/MinIO dependencies and volume initialization. This Standalone stack has not been run on the current machine. Stop the Lite server before using the same 19530 port; set `MILVUS_DEPLOYMENT=standalone` and rebuild the application index for the new deployment.
