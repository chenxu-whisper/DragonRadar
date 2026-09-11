"""Exclusive credential files, protected at creation rather than after writing."""

import os
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import BinaryIO


def output_path(path: Path) -> Path:
    path = Path(path)
    if ".." in path.parts:
        raise ValueError("Invalid output path")
    path = path.absolute()
    if os.name == "nt" and (
        len(path.drive) != 2
        or path.drive[1] != ":"
        or any(":" in part or part.endswith((" ", ".")) for part in path.parts[1:])
    ):
        raise ValueError("Invalid output path")
    return path


@contextmanager
def secure_output(path: Path) -> Iterator[BinaryIO]:
    """Reserve a new file before issuance; never chmod a caller's mounted root."""
    path = output_path(path)
    with ExitStack() as stack:
        if os.name == "nt":
            from dragon_radar.owner.windows_files import open_private_file

            fd = open_private_file(path, stack)

            def remove():
                path.unlink()

        else:
            parent = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
            stack.callback(os.close, parent)
            for component in path.parts[1:-1]:
                parent = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=parent,
                )
                stack.callback(os.close, parent)
            fd = os.open(
                path.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )

            def remove():
                os.unlink(path.name, dir_fd=parent)

        try:
            with os.fdopen(fd, "wb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise OSError("Unsafe output file")
                if os.name != "nt" and (
                    stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid()
                ):
                    raise OSError("Unsafe output permissions")
                yield stream
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            remove()
            raise
