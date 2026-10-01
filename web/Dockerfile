# ---------------------------------------------------------------------------
# SPA image: built with Node, served by nginx.
#
# Node does not ship. The runtime layer is nginx plus a directory of static
# files, which is both smaller and a much smaller thing to attack.
# ---------------------------------------------------------------------------

FROM node:22-alpine AS builder

WORKDIR /app

# Lockfile first, so editing a component does not reinstall node_modules.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# tsc runs as part of this, so a type error fails the build rather than
# shipping. See the "build" script in package.json.
RUN npm run build


FROM nginx:1.27-alpine AS runtime

RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/app.conf
COPY --from=builder /app/dist /usr/share/nginx/html

# 8080 rather than 80 so the server can run unprivileged. The stock nginx image
# runs its master as root to bind low ports; nothing here needs that.
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:8080/index.html || exit 1

CMD ["nginx", "-g", "daemon off;"]
