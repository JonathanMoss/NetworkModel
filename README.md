# Network Rail TPS Schematic & Geographical Track Model Explorer

Interactive web explorer for the **Network Rail TPS Schematic Model** and **NWR Geographical Track Model (OSGB36)** backed by PostgreSQL 16 and FastAPI.

## Reference Data Prerequisite (Not Distributed)

This repository **does not** distribute Network Rail reference datasets. You must obtain the following two datasets separately before building the container:

1. **TPS Infrastructure & Schematic Model**: Extracted XML file (e.g. `XML_p.xml`) or compressed archive (`TPS_Data.tar.gz` / `.zip`).
2. **NWR Geographical Track Model (OSGB36)**: Extracted Shapefile directory (containing `NWR_TrackNodes.shp`, `NWR_TrackCentreLines.shp`, `NWR_ELRs.shp`, `NWR_Waymarks.shp`, and accompanying `.dbf`/`.shx`/`.prj` files) or compressed `.zip` archive (e.g. `NWR_TrackModel20261026` or `NWR_TrackModel20261026.zip`).

## Configuring Reference Data Paths in the `Dockerfile`

Place your obtained reference datasets inside the project root (they are automatically ignored by `.gitignore`) and configure their paths at the top of [`Dockerfile`](./Dockerfile):

```dockerfile
ARG TPS_MODEL_PATH=./XML_p.xml
ARG NWR_MODEL_PATH=./NWR_TrackModel20261026
```

> Both extracted files/directories and compressed archives (`.tar.gz`, `.tgz`, `.zip`) are supported automatically by the ingestion pipeline.

You can also pass custom paths at build time without editing the file:

```bash
docker compose build \
  --build-arg TPS_MODEL_PATH=./TPS_Data.tar.gz \
  --build-arg NWR_MODEL_PATH=./NWR_TrackModel20261026.zip
```

## Running the Application

```bash
docker compose up --build -d
```

Once running, open **http://localhost:8090** in your browser. On first launch, the backend streams and indexes both models into PostgreSQL automatically.

