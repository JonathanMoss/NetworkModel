# Network Rail TPS Schematic & Geographical Track Model Explorer

Interactive web explorer for the **Network Rail TPS Schematic Model** and **NWR Geographical Track Model (OSGB36)** backed by PostgreSQL 16 and FastAPI.

## Reference Data Prerequisite (Not Distributed)

This repository **does not** distribute Network Rail reference datasets. You must obtain the following three datasets separately before building or deploying the container:

1. **TPS Infrastructure & Schematic Model**: Extracted XML file (e.g. `XML_p.xml`) or compressed archive (`TPS_Data.tar.gz` / `.zip`).
2. **NWR Geographical Track Model (OSGB36)**: Extracted Shapefile directory (containing `NWR_TrackNodes.shp`, `NWR_TrackCentreLines.shp`, `NWR_ELRs.shp`, `NWR_Waymarks.shp`, and accompanying `.dbf`/`.shx`/`.prj` files) or compressed `.zip` archive (e.g. `NWR_TrackModel20261026` or `NWR_TrackModel20261026.zip`).
3. **Network Fusion Dataset (Alan Morrison / GeoFurlong)**: Directory containing the 7 CSV files (`nf_core.csv`, `nf_tracks.csv`, `nf_elevation.csv`, `nf_climate.csv`, `nf_geology.csv`, `nf_gazetteer.csv`, `nf_curvature.csv`) or compressed archive (`network_fusion_2026-10-26.zip`).

## Configuring Reference Data Paths in the `Dockerfile`

Place your obtained reference datasets inside the project root (they are automatically ignored by `.gitignore`) and configure their paths at the top of [`Dockerfile`](./Dockerfile):

```dockerfile
ARG TPS_MODEL_PATH=./XML_p.xml
ARG NWR_MODEL_PATH=./NWR_TrackModel20261026
ARG NF_MODEL_PATH=./network_fusion_2026-10-26
```

> Both extracted files/directories and compressed archives (`.tar.gz`, `.tgz`, `.zip`) are supported automatically by the ingestion pipeline.

You can also pass custom paths at build time without editing the file:

```bash
docker compose build \
  --build-arg TPS_MODEL_PATH=./TPS_Data.tar.gz \
  --build-arg NWR_MODEL_PATH=./NWR_TrackModel20261026.zip \
  --build-arg NF_MODEL_PATH=./network_fusion_2026-10-26.zip
```

### Production Deployment Recommendation: Volume Mounting

In production, rather than baking large datasets (~750 MB total) into immutable container images, mount external or persistent storage volumes directly:

```yaml
services:
  backend:
    environment:
      XML_PATH: /data/tps
      NWR_DIR: /data/nwr
      NF_DIR: /data/network_fusion
    volumes:
      - /opt/rail_data/tps:/data/tps:ro
      - /opt/rail_data/nwr:/data/nwr:ro
      - /opt/rail_data/network_fusion:/data/network_fusion:ro
```

## Running the Application

```bash
docker compose up --build -d
```

Once running, open **http://localhost:8090** in your browser. On first launch, the backend streams and indexes both models into PostgreSQL automatically.

## Running Code Quality & Testing Pipeline (Pylint)

To run `pylint` in an isolated testing container against all Python code and automatically pull down the pipeline upon completion:

```bash
./run_tests.sh
```

Or run via Docker Compose directly:

```bash
# Run testing pipeline and stream output to stdout
docker compose -f docker-compose.test.yml up --abort-on-container-exit --exit-code-from lint

# Pull down the testing pipeline
docker compose -f docker-compose.test.yml down --volumes --remove-orphans
```


