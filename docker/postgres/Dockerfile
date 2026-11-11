FROM pgvector/pgvector:pg17

# Install PostGIS 3.6 for PostgreSQL 17
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       postgresql-17-postgis-3=3.6.4+dfsg-2.pgdg12+1 \
       postgresql-17-postgis-3-scripts=3.6.4+dfsg-2.pgdg12+1 \
    && rm -rf /var/lib/apt/lists/*
