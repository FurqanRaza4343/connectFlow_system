# Builds the React app and the marketing website, then serves both with Caddy (one container, no nginx).
# Build context = repo root (see docker-compose.yml).
FROM node:22-alpine AS app
ARG VITE_SITE_URL
ENV VITE_SITE_URL=$VITE_SITE_URL
WORKDIR /app
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ .
RUN npm run build

FROM node:22-alpine AS site
ARG PUBLIC_APP_URL
ENV PUBLIC_APP_URL=$PUBLIC_APP_URL
WORKDIR /site
# Astro site arrives in milestone M1; until then website/ holds a static placeholder (no package.json).
COPY website/ .
RUN if [ -f package.json ]; then npm install --no-audit --no-fund && npm run build; else mkdir -p dist && cp -r ./*.html dist/ 2>/dev/null || true; fi

FROM caddy:2-alpine
COPY infra/Caddyfile /etc/caddy/Caddyfile
COPY --from=app /app/dist /srv/app
COPY --from=site /site/dist /srv/website
