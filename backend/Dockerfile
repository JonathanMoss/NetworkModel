FROM python:3.12-slim

# ==============================================================================
# USER CONFIGURATION: Network Rail Reference Data Locations
# ------------------------------------------------------------------------------
# Specify the locations of the TPS Model (.xml file, directory, or .tar.gz/.zip)
# and the NWR Geographical Track Model (shapefile directory or .zip/.tar.gz)
# relative to the build context (repository root).
# ==============================================================================
ARG TPS_MODEL_PATH=./XML_p.xml
ARG NWR_MODEL_PATH=./NWR_TrackModel20261026

# Container paths read by the FastAPI ingestion worker
ENV XML_PATH=/data/tps
ENV NWR_DIR=/data/nwr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python dependencies
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy backend application & static assets
COPY backend/ /app/

# Copy the user-specified TPS and NWR reference models into the container
COPY ${TPS_MODEL_PATH} /data/tps/
COPY ${NWR_MODEL_PATH} /data/nwr/

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
