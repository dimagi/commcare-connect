from storages.backends.s3boto3 import S3Boto3Storage


class StaticRootS3Boto3Storage(S3Boto3Storage):
    location = "static"
    default_acl = "public-read"


class MediaRootS3Boto3Storage(S3Boto3Storage):
    location = "media"
    file_overwrite = False


class ExportS3Boto3Storage(S3Boto3Storage):
    """
    Cleanup of expired files is handled by an S3 lifecycle rule
    targeting this prefix.
    """

    location = "media/exports"
    file_overwrite = False
