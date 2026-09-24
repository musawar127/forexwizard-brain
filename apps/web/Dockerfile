# ---- Build stage ----
FROM node:22-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

# Copy the rest of the web app source
COPY . .

# NEXT_PUBLIC_API_URL must be present at BUILD TIME so it gets inlined
# into the static bundle. It is set via Railway env vars at deploy time.
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL:-http://localhost:8000}

RUN npm run build

# ---- Runtime stage ----
FROM node:22-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production

# Next.js respects PORT and HOSTNAME env vars at start time.
# Only copy .next (the built app). node_modules is needed for next start.
# public/ is optional — only copy if it exists (use a wildcard + || true fallback).
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/next.config.ts ./next.config.ts
COPY --from=builder /app/tsconfig.json ./tsconfig.json
# Create empty public dir so Next.js can serve static assets if added later
RUN mkdir -p /app/public

EXPOSE 3000

# Shell form so $PORT is expanded at runtime.
CMD HOSTNAME=0.0.0.0 PORT=${PORT:-3000} npx next start
