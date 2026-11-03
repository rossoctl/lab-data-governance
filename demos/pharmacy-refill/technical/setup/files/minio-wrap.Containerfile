# The travel app's manifest pins docker.io/minio/minio:RELEASE.2024-11-07T00-52-20Z, which MinIO no longer serves anywhere. This is the
# same release from Bitnami's archive; the wrapper only replaces its entrypoint so the manifest's `server /data …` args work.
FROM docker.io/bitnamilegacy/minio:2024.11.7
ENTRYPOINT ["/opt/bitnami/minio/bin/minio"]
CMD []
