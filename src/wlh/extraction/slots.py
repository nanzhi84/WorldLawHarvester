"""Four host-local extraction slots shared by all Runs using one WLH work directory."""

import fcntl
import os
import time

from wlh.core import WlhError
from wlh.engine.objects import work_directory


class PDFSlots:
    def __init__(self):
        self.folder = work_directory() / "pdf-processes"
        self.folder.mkdir(exist_ok=True, mode=0o700)

    def check(self):
        if (self.folder / "blocked").exists():
            raise WlhError("extraction_cleanup_failed")

    def try_acquire(self):
        for index in range(4):
            slot = os.open(self.folder / str(index), os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(slot)
            except BaseException:
                os.close(slot)
                raise
            else:
                return slot
        return None

    def acquire_blocking(self, active=None):
        while True:
            self.check()
            if active is not None:
                active()
            slot = self.try_acquire()
            if slot is not None:
                return slot
            time.sleep(0.05)

    def block(self):
        # No document content, credentials or local input paths enter this marker.
        marker = os.open(self.folder / "blocked", os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(marker)
