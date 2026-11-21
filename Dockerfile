# RoboDE Studio, Google edition: one container for Cloud Run (Spring Boot API + Python engine + built web client).
# Build:  gcloud builds submit --tag REGION-docker.pkg.dev/PROJECT/studio/robode-studio   (see deploy/cloudrun/README.md)
# No key or token is baked in: GEMINI_API_KEY and STUDIO_TOKEN arrive as environment variables at run time.

# 1. Web client
FROM node:24-slim AS client
WORKDIR /src
COPY package.json package-lock.json ./
COPY client/package.json client/
COPY packages/core/package.json packages/core/
RUN npm ci
COPY client client
COPY packages packages
RUN npm run build

# 2. Java API (tests are run in development, not here)
FROM eclipse-temurin:25-jdk AS api
WORKDIR /src/server-java
COPY server-java/pom.xml server-java/mvnw ./
COPY server-java/.mvn .mvn
RUN ./mvnw -q -B dependency:go-offline
COPY server-java/src src
RUN ./mvnw -q -B -DskipTests package && cp target/studio-server-*.jar /studio-server.jar

# 3. Runtime: a JRE plus Python 3.12 with the engine's packages
FROM eclipse-temurin:25-jre
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv libgl1 libglu1-mesa libxrender1 libxext6 libsm6 \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/requirements.txt
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r /app/requirements.txt
WORKDIR /app
COPY server /app/server
COPY --from=client /src/client/dist /app/client/dist
COPY --from=api /studio-server.jar /app/studio-server.jar
RUN useradd --uid 10001 --create-home studio && mkdir -p /data/runtime && chown -R studio /data
USER studio
ENV STUDIO_REPO_ROOT=/app \
    STUDIO_PYTHON=/opt/venv/bin/python \
    STUDIO_RUNTIME=/data/runtime \
    STUDIO_HOST=0.0.0.0
# Java refuses to start on 0.0.0.0 without STUDIO_TOKEN; Cloud Run's $PORT becomes the listening port.
CMD ["sh", "-c", "exec java -XX:MaxRAMPercentage=60 -Dstudio.port=${PORT:-8080} -jar /app/studio-server.jar"]
