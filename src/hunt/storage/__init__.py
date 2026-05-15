from hunt.storage.local import LocalDisk
from hunt.storage.manager import Storage
from hunt.storage.s3 import S3Disk

__all__ = ["LocalDisk", "S3Disk", "Storage"]
