FROM node:22-alpine AS web
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json* ./
COPY frontend /src
RUN npm install && npm run build

FROM nginx:1.27-alpine
RUN apk add --no-cache openssl
COPY nginx/nginx.conf /etc/nginx/nginx.conf
COPY nginx/entrypoint.sh /entrypoint.sh
COPY --from=web /src/dist /usr/share/nginx/html
RUN chmod +x /entrypoint.sh
EXPOSE 80 443
ENTRYPOINT ["/entrypoint.sh"]
