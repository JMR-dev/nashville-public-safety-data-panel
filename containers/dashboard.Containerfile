# The dashboard is static files. It is built with the project's own toolchain and then served
# by an unprivileged static server; nothing from the build stage reaches the running image.
FROM docker.io/library/node:26-slim AS build

ENV PNPM_HOME=/usr/local/share/pnpm \
    PATH=/usr/local/share/pnpm:$PATH
RUN npm install --global pnpm@12.4.2

WORKDIR /src
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY frontend/package.json frontend/package.json
RUN pnpm install --frozen-lockfile

COPY frontend frontend
# Map tiles are deployment-specific; the defaults credit OpenStreetMap.
ARG VITE_TILE_URL
ARG VITE_TILE_ATTRIBUTION
RUN pnpm --filter frontend build


FROM docker.io/library/debian:trixie-slim

RUN apt-get update \
    && apt-get install --yes --no-install-recommends nginx \
    && rm --recursive --force /var/lib/apt/lists/* /etc/nginx/sites-enabled/default \
    && useradd --uid 10002 --user-group --no-create-home --shell /usr/sbin/nologin dashboard

COPY containers/dashboard.nginx.conf /etc/nginx/nginx.conf
COPY --from=build /src/frontend/dist /srv/dashboard

USER dashboard
EXPOSE 8080
ENTRYPOINT ["nginx", "-g", "daemon off;"]
