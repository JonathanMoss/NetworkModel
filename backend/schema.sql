CREATE TABLE IF NOT EXISTS ingestion_meta (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lookup_codes (
    category TEXT NOT NULL,
    code_id TEXT NOT NULL,
    label TEXT,
    vanilla_text TEXT,
    description TEXT,
    raw_attrs JSONB NOT NULL,
    raw_children JSONB NOT NULL,
    PRIMARY KEY (category, code_id)
);

CREATE TABLE IF NOT EXISTS nodes (
    node_id INTEGER PRIMARY KEY,
    line_id INTEGER NOT NULL DEFAULT 0,
    business_number INTEGER,
    netx DOUBLE PRECISION NOT NULL,
    nety DOUBLE PRECISION NOT NULL,
    netz DOUBLE PRECISION,
    linex DOUBLE PRECISION,
    liney DOUBLE PRECISION,
    linez DOUBLE PRECISION,
    km_region_id INTEGER,
    km_value INTEGER,
    second_km_region_id INTEGER,
    second_km_value INTEGER,
    name TEXT,
    angle INTEGER,
    valid_from TEXT,
    valid_to TEXT,
    last_modified TEXT,
    raw_attrs JSONB NOT NULL,
    raw_children JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS edges (
    pk BIGSERIAL PRIMARY KEY,
    edge_id INTEGER,
    start_node_id INTEGER,
    end_node_id INTEGER,
    start_line_id INTEGER,
    end_line_id INTEGER,
    x0 DOUBLE PRECISION,
    y0 DOUBLE PRECISION,
    x1 DOUBLE PRECISION,
    y1 DOUBLE PRECISION,
    min_x DOUBLE PRECISION,
    max_x DOUBLE PRECISION,
    min_y DOUBLE PRECISION,
    max_y DOUBLE PRECISION,
    length INTEGER,
    gradient DOUBLE PRECISION,
    vmax INTEGER,
    electrified BOOLEAN,
    partial_release BOOLEAN,
    approach_signal BOOLEAN,
    tunnel_square_area DOUBLE PRECISION,
    tunnel_factor DOUBLE PRECISION,
    erratic_km_dist INTEGER,
    height_difference INTEGER,
    radius INTEGER,
    geogis_line_code INTEGER,
    valid_from TEXT,
    valid_to TEXT,
    last_modified TEXT,
    velocity_profiles JSONB NOT NULL,
    raw_attrs JSONB NOT NULL,
    raw_children JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS mastersignals (
    id INTEGER PRIMARY KEY,
    name TEXT,
    raw_attrs JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    pk BIGSERIAL PRIMARY KEY,
    id INTEGER,
    signal_id INTEGER,
    master_name TEXT,
    name TEXT,
    interlocking_sys_id INTEGER,
    zone_id INTEGER,
    start_node_id INTEGER,
    end_node_id INTEGER,
    point_node_id INTEGER,
    x DOUBLE PRECISION,
    y DOUBLE PRECISION,
    x1 DOUBLE PRECISION,
    y1 DOUBLE PRECISION,
    tmp_closed BOOLEAN,
    use_sec_sect_freeing_time BOOLEAN,
    sec_sect_freeing_time INTEGER,
    restriction_code_id INTEGER,
    bumper BOOLEAN,
    import_locked BOOLEAN,
    km_region_id INTEGER,
    line_track_id INTEGER,
    valid_from TEXT,
    valid_to TEXT,
    last_modified TEXT,
    security_sections JSONB NOT NULL,
    raw_attrs JSONB NOT NULL,
    raw_children JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS stations (
    station_id INTEGER PRIMARY KEY,
    abbrev TEXT,
    longname TEXT,
    crscode TEXT,
    stanox TEXT,
    nalco TEXT,
    nlccheckchr TEXT,
    uiccode TEXT,
    uicstationcode TEXT,
    station_type INTEGER,
    station_category INTEGER,
    std_stopping_time INTEGER,
    std_connection_time INTEGER,
    compulsory_stop BOOLEAN,
    is_parent BOOLEAN,
    km_region_id INTEGER,
    km_value INTEGER,
    x DOUBLE PRECISION,
    y DOUBLE PRECISION,
    min_x DOUBLE PRECISION,
    max_x DOUBLE PRECISION,
    min_y DOUBLE PRECISION,
    max_y DOUBLE PRECISION,
    track_count INTEGER DEFAULT 0,
    active_track_count INTEGER DEFAULT 0,
    positions JSONB NOT NULL,
    raw_attrs JSONB NOT NULL,
    raw_children JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS station_tracks (
    pk BIGSERIAL PRIMARY KEY,
    track_id INTEGER,
    station_id INTEGER,
    name TEXT,
    longname TEXT,
    seq INTEGER,
    business_reference_id TEXT,
    description TEXT,
    track_category INTEGER,
    effective_length INTEGER,
    rop_line_code INTEGER,
    sa_line_code INTEGER,
    line_property_template_id INTEGER,
    permissive_working BOOLEAN,
    directed BOOLEAN,
    track_tmp_closed BOOLEAN,
    valid_from TEXT,
    valid_to TEXT,
    last_modified TEXT,
    node_ids INTEGER[] NOT NULL,
    coords JSONB NOT NULL,
    min_x DOUBLE PRECISION,
    max_x DOUBLE PRECISION,
    min_y DOUBLE PRECISION,
    max_y DOUBLE PRECISION,
    raw_attrs JSONB NOT NULL,
    raw_children JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS station_boundaries (
    pk BIGSERIAL PRIMARY KEY,
    signal_id INTEGER,
    boundary_type_id INTEGER,
    station_ids INTEGER[] NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    last_modified TEXT,
    raw_attrs JSONB NOT NULL,
    raw_children JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS blocks (
    pk BIGSERIAL PRIMARY KEY,
    block_id INTEGER,
    line_id INTEGER,
    pre_signal_distance INTEGER,
    priority INTEGER,
    sight_distance INTEGER,
    atc_length INTEGER,
    atc_flag BOOLEAN,
    moving_block BOOLEAN,
    shunting_flag BOOLEAN,
    additional_aspects INTEGER,
    business_reference_id TEXT,
    valid_from TEXT,
    valid_to TEXT,
    last_modified TEXT,
    node_ids INTEGER[] NOT NULL,
    coords JSONB NOT NULL,
    min_x DOUBLE PRECISION,
    max_x DOUBLE PRECISION,
    min_y DOUBLE PRECISION,
    max_y DOUBLE PRECISION,
    raw_attrs JSONB NOT NULL,
    raw_children JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS routes (
    pk BIGSERIAL PRIMARY KEY,
    route_id INTEGER,
    line_id INTEGER,
    station_id INTEGER,
    rop_line_code_id INTEGER,
    sa_line_code_id INTEGER,
    prelocation BOOLEAN,
    valid_from TEXT,
    valid_to TEXT,
    last_modified TEXT,
    node_ids INTEGER[] NOT NULL,
    coords JSONB NOT NULL,
    min_x DOUBLE PRECISION,
    max_x DOUBLE PRECISION,
    min_y DOUBLE PRECISION,
    max_y DOUBLE PRECISION,
    raw_attrs JSONB NOT NULL,
    raw_children JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS sections (
    section_id INTEGER PRIMARY KEY,
    section_type INTEGER,
    name TEXT,
    last_modified TEXT,
    raw_attrs JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS attribute_sections (
    pk BIGSERIAL PRIMARY KEY,
    id INTEGER,
    section_id INTEGER,
    km_region_id INTEGER,
    short_description TEXT,
    description TEXT,
    valid_from TEXT,
    valid_to TEXT,
    last_modified TEXT,
    node_ids INTEGER[] NOT NULL,
    edge_pairs JSONB NOT NULL,
    section_attrs JSONB NOT NULL,
    coords JSONB NOT NULL,
    min_x DOUBLE PRECISION,
    max_x DOUBLE PRECISION,
    min_y DOUBLE PRECISION,
    max_y DOUBLE PRECISION,
    raw_attrs JSONB NOT NULL,
    raw_children JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS graphic_vectors (
    pk BIGSERIAL PRIMARY KEY,
    x0 DOUBLE PRECISION NOT NULL,
    y0 DOUBLE PRECISION NOT NULL,
    x1 DOUBLE PRECISION NOT NULL,
    y1 DOUBLE PRECISION NOT NULL,
    min_x DOUBLE PRECISION NOT NULL,
    max_x DOUBLE PRECISION NOT NULL,
    min_y DOUBLE PRECISION NOT NULL,
    max_y DOUBLE PRECISION NOT NULL,
    layers INTEGER,
    valid_from TEXT,
    valid_to TEXT,
    last_modified TEXT,
    raw_attrs JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS graphic_texts (
    pk BIGSERIAL PRIMARY KEY,
    x DOUBLE PRECISION NOT NULL,
    y DOUBLE PRECISION NOT NULL,
    size INTEGER,
    text TEXT,
    angle INTEGER,
    layers INTEGER,
    valid_from TEXT,
    valid_to TEXT,
    last_modified TEXT,
    raw_attrs JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS poly_areas (
    pk BIGSERIAL PRIMARY KEY,
    id INTEGER,
    name TEXT,
    description TEXT,
    corners JSONB NOT NULL,
    min_x DOUBLE PRECISION,
    max_x DOUBLE PRECISION,
    min_y DOUBLE PRECISION,
    max_y DOUBLE PRECISION,
    raw_attrs JSONB NOT NULL,
    raw_children JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS annotations (
    pk BIGSERIAL PRIMARY KEY,
    x DOUBLE PRECISION NOT NULL,
    y DOUBLE PRECISION NOT NULL,
    text TEXT,
    valid_from TEXT,
    valid_to TEXT,
    last_modified TEXT,
    raw_attrs JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_tps_elements (
    pk BIGSERIAL PRIMARY KEY,
    tag TEXT NOT NULL,
    entity_id TEXT,
    name TEXT,
    raw_attrs JSONB NOT NULL,
    raw_children JSONB NOT NULL
);

