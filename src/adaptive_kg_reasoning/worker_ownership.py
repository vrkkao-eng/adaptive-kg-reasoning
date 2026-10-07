"""Non-blocking lifetime locks for a single host and local filesystem only."""
from pathlib import Path
import os


class OwnershipBusy(ValueError):
    """A live owner still holds the resource; never reclaim it by PID."""


class FileLock:
    def __init__(self, path: Path):
        self.path, self.stream = Path(path), None

    def acquire(self):
        if self.stream is not None:
            raise RuntimeError("Lock instance is already acquired")
        stream = self.path.open("a+b")
        try:
            if stream.seek(0, 2) == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            if exc.errno not in (11, 13, 35, 36):
                raise
            raise OwnershipBusy("Resource has a live owner") from exc
        self.stream = stream
        return self

    def close(self):
        if self.stream is not None:
            # Closing releases the kernel lock, including after process death.
            # Never unlink the file: replacing its inode can split ownership.
            self.stream.close()
            self.stream = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *args):
        self.close()
def kill_owned_process(process):
    """Stop only descendants of a still-live Popen handle, then its launcher.

    Windows virtual-environment redirectors can introduce a second Python PID.
    The fixed worker never launches arbitrary commands or detached descendants.
    """
    import psutil
    if process.poll() is not None:
        return
    try:
        owned = psutil.Process(process.pid)
        descendants = owned.children(recursive=True)
        for child in reversed(descendants):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
    except psutil.NoSuchProcess:
        pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)
