# Development image for the PartFlow frontend (Phase 1).
FROM node:24-alpine

WORKDIR /app

# Install dependencies first so source edits reuse this cached layer.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
