# --- Stufe 1: Build (Node) ---
FROM node:22-alpine AS build

WORKDIR /app

# package*.json zuerst -> Layer-Cache fuer npm ci
COPY package*.json ./
RUN npm ci

COPY . .

# API-URL wird ZUR BUILD-ZEIT ins Bundle gebacken (Vite ersetzt import.meta.env.VITE_*).
# Default = host-erreichbare Backend-URL, weil die App im Browser laeuft.
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build

# --- Stufe 2: Auslieferung (nginx, schlankes Image ohne Node) ---
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
