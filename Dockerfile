FROM node:24-bookworm-slim
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev \
    && mkdir /app/data && chown node:node /app/data
COPY bot.mjs store.mjs ./
USER node
CMD ["node", "bot.mjs"]

