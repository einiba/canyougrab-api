FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* yarn.lock* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
# SPA routing: serve index.html for all non-file routes
COPY <<'EOF' /etc/nginx/conf.d/default.conf
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }
}
EOF
EXPOSE 80
HEALTHCHECK --interval=10s --timeout=3s \
    CMD curl -sf http://localhost/ || exit 1
