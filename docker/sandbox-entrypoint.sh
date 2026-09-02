#!/bin/sh
# Everything the sandbox creates must be removable by the host user that owns
# the workspace.
#
# The container runs as uid 65534 while the workspace belongs to whoever runs
# the application. With a default umask, files and directories the sandbox
# creates come out owned by 65534 and mode 0755, so the host user cannot delete
# their contents and cannot chmod them either -- they do not own them. Cleanup
# of the temporary workspace then fails with EPERM, and every patch validation
# leaks a directory nothing can remove.
#
# umask 0000 makes them world-writable instead, so the owner of the workspace
# can always clean up. That widens nothing outside the workspace: it is a
# disposable per-run directory, and reaching it still requires traversing a
# parent whose permissions are untouched.
umask 0000
exec "$@"
