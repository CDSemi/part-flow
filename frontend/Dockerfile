# PartFlow frontend images: `production` (web: nginx serving the immutable build) and `development` (default, last stage).
FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# The release this bundle is built for (compose.production.build.yaml passes it); never `development`.
ARG PARTFLOW_RELEASE
RUN if ! printf '%s' "$PARTFLOW_RELEASE" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$' \
        || [ "$PARTFLOW_RELEASE" = development ]; then \
        echo "PartFlow web: build argument PARTFLOW_RELEASE must name the release." >&2; exit 1; fi
# Runs tsc, vite build and the production-boundary check; the bundle carries its release.
RUN VITE_PARTFLOW_RELEASE="$PARTFLOW_RELEASE" npm run build

# Official nginx stable line, pinned (1.30.5-alpine, digest
# sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94 on 2026-10-08).
FROM nginx:1.30.5-alpine AS production
ARG PARTFLOW_RELEASE
ARG PARTFLOW_COMMIT
RUN if ! printf '%s' "$PARTFLOW_COMMIT" | grep -Eq '^[0-9a-f]{40}$'; then \
        echo "PartFlow web: build argument PARTFLOW_COMMIT must be the full 40-character commit SHA." >&2; exit 1; fi
LABEL org.opencontainers.image.version=${PARTFLOW_RELEASE} org.opencontainers.image.revision=${PARTFLOW_COMMIT}
ENV NGINX_ENVSUBST_FILTER=^PARTFLOW_
COPY nginx/templates/ /etc/nginx/templates/
COPY nginx/partflow/ /etc/nginx/partflow/
COPY --chmod=0755 nginx/entrypoint/15-partflow-trusted-proxy.envsh /docker-entrypoint.d/
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80

# Development image (Phase 1): Vite dev server; Docker Compose builds this default stage.
FROM node:24-alpine AS development
WORKDIR /app
# Install dependencies first so source edits reuse this cached layer.
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
