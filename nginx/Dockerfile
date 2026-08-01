FROM node:20-alpine AS frontend
WORKDIR /app
COPY web/package*.json ./
RUN npm ci --quiet
COPY web/ .
ARG VITE_API_URL=https://api.athenaindex.app
# vite.config.ts falls back to `git rev-parse --short HEAD` for the "which
# build is this" marker in the UI, but this image never gets a `.git`
# directory (only `web/` is copied in) and alpine's node image has no git
# binary anyway — so that always failed silently to "unknown" in prod. The
# real sha has to be computed on the host (where `.git` exists) and threaded
# through as a build arg instead; see docker-compose.prod.yml/deploy.sh.
ARG VITE_GIT_SHA=unknown
RUN VITE_API_URL=${VITE_API_URL} VITE_GIT_SHA=${VITE_GIT_SHA} npm run build

FROM nginx:1.27-alpine
COPY --from=frontend /app/dist /usr/share/nginx/html
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
EXPOSE 80 443
