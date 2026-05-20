FROM node:20-alpine AS frontend
WORKDIR /app
COPY web/package*.json ./
RUN npm ci --quiet
COPY web/ .
ARG VITE_API_URL=https://api.athenaindex.app
RUN VITE_API_URL=${VITE_API_URL} npm run build

FROM nginx:1.27-alpine
COPY --from=frontend /app/dist /usr/share/nginx/html
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
EXPOSE 80 443
