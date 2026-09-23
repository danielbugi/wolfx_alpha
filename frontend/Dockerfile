# frontend/Dockerfile
#
# Only needed under the Option A self-hosted-frontend fallback (docs/devops/TARGET_ARCHITECTURE.md
# §2 recommends Vercel instead) or for local full-stack validation via Docker Compose. Build context
# is frontend/ — this app is self-contained, it doesn't reach outside its own directory.
#
# NEXT_PUBLIC_API_BASE_URL is inlined into the browser bundle at build time (see next.config.ts,
# which refuses a production build without it) — it MUST be passed as a build ARG, setting it as a
# runtime environment variable on the running container has no effect on an already-built bundle.
FROM node:20-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_API_BASE_URL
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
# Isolated build output dir (next.config.ts's NEXT_DIST_DIR) — same mechanism this project already
# uses locally (CLAUDE.md §8) to keep a verification build from colliding with a `next dev` cache;
# here it just makes the final COPY --from=builder path explicit and unambiguous.
ENV NEXT_DIST_DIR=.next
RUN npm run build

FROM node:20-slim AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "require('http').get('http://localhost:3000', r => process.exit(r.statusCode < 500 ? 0 : 1)).on('error', () => process.exit(1))"

CMD ["npm", "run", "start"]
