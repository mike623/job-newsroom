# Two targets from one source tree. They differ only in whether Chromium is present: the runner
# spawns crawls and needs it, the dashboard renders files and never launches a browser.
#
# Stage order is deliberate. Chromium is ~1GB and slow to fetch, so it is installed before any
# source is copied — a code edit then invalidates only the final COPY, in both targets.

FROM python:3.11-slim AS deps
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt

# What the runner needs beyond Python: Chromium for the crawled boards, and himalaya for the
# email board, which reads a mailbox over IMAP instead of crawling.
FROM deps AS runner-tools
RUN playwright install --with-deps chromium

# Fetched with Python rather than curl, which this base image does not carry. The asset names
# are keyed on `uname -m` exactly (aarch64-linux, x86_64-linux), so this builds on an Apple
# Silicon laptop and an x86 server without a second code path.
ARG HIMALAYA_VERSION=1.2.0
RUN python -c "import io, platform, tarfile, urllib.request; \
url = f'https://github.com/pimalaya/himalaya/releases/download/v${HIMALAYA_VERSION}/himalaya.{platform.machine()}-linux.tgz'; \
tarfile.open(fileobj=io.BytesIO(urllib.request.urlopen(url, timeout=180).read())).extract('himalaya', '/usr/local/bin')" \
 && chmod +x /usr/local/bin/himalaya \
 && himalaya --version


# The interface. A React application, built once here so the runtime image carries no node —
# the dashboard serves the bundle as static files and nothing else.
FROM node:22-slim AS client
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build


# Reads report files and serves them. No crawling, so no browser.
FROM deps AS dashboard
COPY . .
# After the source, so the built client wins over anything a host build left behind.
COPY --from=client /dashboard/static ./dashboard/static
EXPOSE 8080
CMD ["python", "-m", "dashboard"]


# Spawns the crawls.
FROM runner-tools AS runner
COPY . .
EXPOSE 8081
CMD ["python", "-m", "runner"]
