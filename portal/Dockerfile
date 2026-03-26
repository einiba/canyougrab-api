# Built from REPO ROOT, not portal/:  docker build -f portal/Dockerfile .
FROM node:20-alpine AS build
WORKDIR /app

# Copy shared module first (path alias @shared → ../shared)
COPY shared/ /app/shared/

# Install portal deps
COPY portal/package.json portal/package-lock.json* portal/yarn.lock* /app/portal/
WORKDIR /app/portal
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# Copy portal source and build
COPY portal/ /app/portal/
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/portal/dist /usr/share/nginx/html
RUN printf 'server {\n    listen 80;\n    server_name _;\n    root /usr/share/nginx/html;\n    index index.html;\n    location / {\n        try_files $uri $uri/ /index.html;\n    }\n}\n' > /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=10s --timeout=3s \
    CMD curl -sf http://localhost/ || exit 1
