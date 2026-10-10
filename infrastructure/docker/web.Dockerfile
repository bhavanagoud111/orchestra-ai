# Orchestra AI web console (Next.js standalone output). Build context: repository root.
FROM node:22-alpine AS deps
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY apps/web ./
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN addgroup -S orchestra && adduser -S orchestra -G orchestra
COPY --from=build --chown=orchestra:orchestra /app/.next/standalone ./
COPY --from=build --chown=orchestra:orchestra /app/.next/static ./.next/static
COPY --from=build --chown=orchestra:orchestra /app/public ./public
USER orchestra
EXPOSE 3000
CMD ["node", "server.js"]
