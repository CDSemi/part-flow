# PartFlow frontend images: `production` (web: nginx serving the immutable build) and `development` (default, last stage).
FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Runs tsc, vite build and the production-boundary check (fails on any mock sentinel).
RUN npm run build

# Official nginx stable line, pinned (1.30.5-alpine, digest
# sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94 on 2026-10-08).
FROM nginx:1.30.5-alpine AS production
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
