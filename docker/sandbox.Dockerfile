# The image the sandbox executes untrusted code in (ADR-006).
#
# It exists because the sandbox runs with --network none, so nothing can be
# installed at run time. Whatever a test suite needs has to be baked in here,
# where the build still has a network and the contents are reviewable.
#
# Deliberately minimal. Every package added here is available to code the user
# did not write, so the list is a security surface, not a convenience.
FROM python:3.11-slim

# pytest, and nothing else. No network client, no shell tooling, no compilers.
RUN pip install --no-cache-dir pytest==8.3.4 \
    && rm -rf /root/.cache

# The container is started with --user 65534:65534 and a read-only root, so
# nothing here may assume a writable HOME or an installable dependency.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

WORKDIR /workspace

# Set the umask for everything the sandbox runs. See the script for why: without
# it the sandbox creates files its own host cannot delete.
COPY docker/sandbox-entrypoint.sh /usr/local/bin/sandbox-entrypoint
RUN chmod 0755 /usr/local/bin/sandbox-entrypoint
ENTRYPOINT ["/usr/local/bin/sandbox-entrypoint"]
