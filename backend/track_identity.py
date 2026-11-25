"""TPS track identity, direction, platform-number, and mileage-polarity resolution.

Resolves the physical track direction (``'up'``, ``'down'``, ``'reversible'``),
secondary running-line tiebreaker, platform/siding sequence number, and
human-readable line-class hint for any TPS asset using a 4-stage pipeline:
  1. Direct ``station_tracks`` membership (GIN-indexed ``node_ids``)
  2. Direct ``routes`` membership
  3. Bounded straight-through schematic graph walk (up to 24 hops, following
     collinear turnout legs with ``cos_sim >= 0.92``)
  4. Data-driven per-ELR mileage polarity fallback
"""

import math
import re
from typing import Any, Optional

from config import decode_code

ELR_POLARITY_CACHE: Optional[dict[str, int]] = None


def reset_polarity_cache() -> None:
    """Clear the cached per-ELR mileage polarity map (used by unit tests)."""
    global ELR_POLARITY_CACHE
    ELR_POLARITY_CACHE = None


def load_elr_polarity_cache(cur) -> dict[str, int]:
    """Compute per-ELR mileage polarity from directed TPS ``station_tracks``.

    Across the UK rail network, ~247 of 1,106 ELRs are reverse-chained (Down
    direction corresponds to *decreasing* mileage, e.g. ``CMD1``/``CMD2``).
    This function votes over directed ``station_tracks`` rows to derive ``+1``
    (normal: Down increases km) or ``-1`` (reverse: Down decreases km) per ELR.

    Args:
        cur: An active PostgreSQL cursor returning dict rows.

    Returns:
        Dict mapping uppercase ELR code to ``+1`` or ``-1``.
    """
    global ELR_POLARITY_CACHE
    if ELR_POLARITY_CACHE is not None:
        return ELR_POLARITY_CACHE

    cache: dict[str, int] = {}
    try:
        cur.execute(
            """
            WITH track_dirs AS (
                SELECT n1.km_region_id,
                       CASE
                           WHEN st.description ILIKE '%down%' OR st.name ILIKE 'D-%' OR st.name ILIKE 'E-D%' THEN 'down'
                           WHEN st.description ILIKE '%up%' OR st.name ILIKE 'U-%' OR st.name ILIKE 'E-U%' THEN 'up'
                       END AS dir,
                       SIGN(n2.km_value - n1.km_value) AS km_sign
                FROM station_tracks st
                JOIN nodes n1 ON n1.node_id = st.node_ids[1]
                JOIN nodes n2 ON n2.node_id = st.node_ids[array_length(st.node_ids, 1)]
                WHERE st.directed = true
                  AND array_length(st.node_ids, 1) >= 2
                  AND n1.km_region_id > 0 AND n1.km_region_id = n2.km_region_id
                  AND n1.km_value > 0 AND n2.km_value > 0 AND n1.km_value <> n2.km_value
            )
            SELECT km_region_id,
                   COUNT(*) FILTER (WHERE (dir = 'down' AND km_sign > 0) OR (dir = 'up' AND km_sign < 0)) AS normal_votes,
                   COUNT(*) FILTER (WHERE (dir = 'down' AND km_sign < 0) OR (dir = 'up' AND km_sign > 0)) AS reverse_votes
            FROM track_dirs
            WHERE dir IS NOT NULL
            GROUP BY km_region_id
            """
        )
        for row in cur.fetchall():
            elr_name = decode_code("kmregionmasterdesc", row["km_region_id"])
            if not elr_name:
                continue
            elr_key = str(elr_name).strip().upper()
            if row["reverse_votes"] > row["normal_votes"]:
                cache[elr_key] = -1
            elif row["normal_votes"] > row["reverse_votes"]:
                cache[elr_key] = 1
    except Exception:
        pass

    ELR_POLARITY_CACHE = cache
    return cache


def infer_direction_from_km_delta(
    cur,
    elr: Optional[str],
    start_km: Optional[float],
    end_km: Optional[float],
) -> Optional[str]:
    """Infer ``'up'`` or ``'down'`` direction from a start/end mileage delta.

    Always multiplies ``(end_km - start_km)`` by the data-driven per-ELR
    polarity so reverse-chained ELRs are handled accurately.

    Args:
        cur: An active PostgreSQL cursor.
        elr: ELR code string (or ``None``).
        start_km: Starting mileage in metres.
        end_km: Ending mileage in metres.

    Returns:
        ``'down'``, ``'up'``, or ``None`` if mileages are missing or equal.
    """
    if start_km is None or end_km is None or start_km == end_km:
        return None
    polarity = 1
    if elr:
        pol_map = load_elr_polarity_cache(cur)
        polarity = pol_map.get(str(elr).strip().upper(), 1)
    delta = (end_km - start_km) * polarity
    return "down" if delta > 0 else "up"


def _extract_direction_from_tokens(
    name: Optional[str],
    description: Optional[str],
    sa_code: Optional[str],
    rop_code: Optional[str],
) -> Optional[str]:
    """Extract physical track direction (``'up'``, ``'down'``, or ``'reversible'``) from TPS metadata.

    Evaluates explicit bi-directional tokens first, then physical-line suffixes
    (e.g. ``U-DML`` = wrong-direction working on physical Down Main), natural-language
    descriptions, SA/ROP line codes, and finally ``D-``/``U-`` track-name prefixes.

    Args:
        name: TPS ``station_tracks.name`` (e.g. ``'U-2'``, ``'D-ML'``).
        description: TPS ``station_tracks.description`` (e.g. ``'Down Fast'``).
        sa_code: Decoded Network Rail SA line code (e.g. ``'DM'``, ``'US'``).
        rop_code: Decoded ROP line code (e.g. ``'URL'``, ``'DRL'``).

    Returns:
        ``'up'``, ``'down'``, ``'reversible'``, or ``None``.
    """
    desc_l = (description or "").strip().lower()
    sa_u = (sa_code or "").strip().upper()
    rop_u = (rop_code or "").strip().upper()
    name_u = (name or "").strip().upper()

    if sa_u in ("NOT SET", "_", "NONE", "N/A"):
        sa_u = ""
    if rop_u in ("NOT SET", "_", "NONE", "N/A"):
        rop_u = ""

    # 1. Explicit reversible / bi-directional / Up & Down markers (before D-/U- prefixes).
    if any(
        w in desc_l
        for w in (
            "reversible",
            "bi-directional",
            "bidirectional",
            "bi-dir",
            "up & down",
            "up and down",
            "down & up",
            "down and up",
            "u&d",
            "d&u",
        )
    ):
        return "reversible"
    for code in (sa_u, rop_u, name_u):
        if not code:
            continue
        if re.search(
            r"(?:^|[^A-Z])(?:UD[A-Z]*|U&D[A-Z]*|D&U[A-Z]*|U/D[A-Z]*|D/U[A-Z]*|RVL|BGL|REV|BI|BD)(?:$|[^A-Z])",
            code,
        ):
            return "reversible"

    # 1b. Track name physical-line suffix before working-direction prefix (e.g. 'U-DML' -> Down Main).
    if re.match(r"^(?:U|D|E)-D(?:M|ML|F|FL|S|SL|G|GL|L|LL)\b", name_u):
        return "down"
    if re.match(r"^(?:U|D|E)-U(?:M|ML|F|FL|S|SL|G|GL|L|LL)\b", name_u):
        return "up"

    # 2. Physical line direction in description (handles wrong-direction phrasing like 'Down Up Slow').
    has_up_word = bool(re.search(r"\bup\b", desc_l))
    has_down_word = bool(re.search(r"\bdown\b|\bdn\b", desc_l))
    if has_up_word and not has_down_word:
        return "up"
    if has_down_word and not has_up_word:
        return "down"
    if has_up_word and has_down_word:
        if re.search(r"\b(?:down|dn)\s+up\b", desc_l):
            return "up"
        if re.search(r"\bup\s+(?:down|dn)\b", desc_l):
            return "down"

    # 3. SA / ROP line code describing the physical running line.
    for code in (sa_u, rop_u):
        if not code:
            continue
        if code in ("DUS", "DUF", "DUM", "DUR"):
            return "up"
        if code in ("UDS", "UDF", "UDM", "UDR"):
            return "down"
        if re.match(r"^D(?:M|ML|F|FL|S|SL|G|GL|L|LL|R|RL|SDG|SGL|E|W|N|B)", code) or code in (
            "DM",
            "DF",
            "DS",
            "DG",
            "DL",
            "DN",
        ):
            return "down"
        if re.match(r"^U(?:M|ML|F|FL|S|SL|G|GL|L|LL|R|RL|SDG|SGL|E|W|N|B)", code) or code in (
            "UM",
            "UF",
            "US",
            "UG",
            "UL",
            "UP",
        ):
            return "up"

    # 4. Track name prefixes (D-ML, U-ML, E-DM, E-UM, D-1, U-2, D-SDG, U-SDG).
    if re.match(r"^(?:D-|E-D|DN\b)", name_u):
        return "down"
    if re.match(r"^(?:U-|E-U|UP\b)", name_u):
        return "up"

    return None


def _extract_platform_number(
    name: Optional[str],
    longname: Optional[str],
    description: Optional[str],
    sa_code: Optional[str],
    station_longname: Optional[str] = None,
) -> Optional[int]:
    """Extract a ``0..99`` platform, bay, line, or siding sequence number from TPS metadata.

    Args:
        name: TPS ``station_tracks.name``.
        longname: TPS ``station_tracks.longname``.
        description: TPS ``station_tracks.description``.
        sa_code: Decoded SA line code.
        station_longname: Optional parent station name (for numbered siding stations).

    Returns:
        Integer platform/siding number ``0..99``, or ``None`` if unnumbered.
    """
    # 1. Pure numeric or sub-platform longname/SA code (e.g. '0', '0F', '12', '10B').
    for candidate in (longname, sa_code):
        s = (candidate or "").strip()
        m = re.match(r"^(\d{1,2})[A-FR]?$", s, flags=re.IGNORECASE)
        if m:
            val = int(m.group(1))
            if 0 <= val <= 99:
                return val

    # 2. Explicit 'Platform 12' / 'Bay 10B' / 'Siding 1' / 'No3 Siding' / '3SDG'.
    for candidate in (description, name, sa_code):
        s = (candidate or "").strip()
        m = re.search(
            r"\b(?:platform|plat|bay|siding|sdg|line|p)\s*[-#]?\s*(\d{1,2})[A-FR]?\b",
            s,
            flags=re.IGNORECASE,
        )
        if not m:
            m = re.search(r"\b(?:n[o0]\.?\s*)?(\d{1,2})\s*(?:siding|sdg)\b", s, flags=re.IGNORECASE)
        if m:
            val = int(m.group(1))
            if 0 <= val <= 99:
                return val

    # 3. Directed platform/line/siding track name like 'D-0F~A', 'D-12', 'U-L3', 'D-MS1'.
    name_s = (name or "").strip().upper()
    m = re.match(r"^(?:D|U|E)-(?:MS|SDG|DL|CL|RCP|S|L|W)?(\d{1,2})[A-FR]?(?:~[A-Z0-9]+)?$", name_s)
    if m:
        val = int(m.group(1))
        if 0 <= val <= 99:
            return val

    # 4. Parent station longname fallback for numbered siding stations (e.g. 'GLASGOW CENTRAL N0.1 SDG').
    if station_longname:
        meta_l = f"{name or ''} {description or ''} {sa_code or ''}".lower()
        if "siding" in meta_l or "sdg" in meta_l:
            st_s = station_longname.strip()
            m = re.search(r"\b(?:n[o0]\.?\s*)?(\d{1,2})\s*(?:siding|sdg)\b", st_s, flags=re.IGNORECASE)
            if not m:
                m = re.search(
                    r"\b(?:siding|sdg)\s*[-#]?\s*(?:n[o0]\.?\s*)?(\d{1,2})\b",
                    st_s,
                    flags=re.IGNORECASE,
                )
            if m:
                val = int(m.group(1))
                if 0 <= val <= 99:
                    return val

    return None


def _parse_station_track_rows(
    rows: list[dict[str, Any]],
    start_node_id: Optional[int] = None,
    end_node_id: Optional[int] = None,
) -> dict[str, Any]:
    """Select the best-matching ``station_tracks`` row and extract direction, platform, and hints.

    Ranks candidate rows by node containment & traversal order, inherits missing
    SA/ROP line codes from sibling directed rows on the same physical track, and
    promotes tracks with opposing directions on identical nodes to ``'reversible'``
    while retaining the physical line's ``secondary_dir`` as a tiebreaker.

    Args:
        rows: List of ``station_tracks`` (or normalized ``routes``) dict rows.
        start_node_id: Optional TPS asset start node ID.
        end_node_id: Optional TPS asset end node ID.

    Returns:
        Dict with ``direction_hint``, ``secondary_dir``, ``track_name_hint``,
        ``platform_num``, and ``label``.
    """
    if not rows:
        return {
            "direction_hint": None,
            "secondary_dir": None,
            "track_name_hint": None,
            "platform_num": None,
            "label": None,
        }

    scored = []
    phys_dirs_on_same_nodes: set[tuple[str, str]] = set()
    for r in rows:
        nids = r.get("node_ids") or []
        sa_code = decode_code("salinecodedesc", r.get("sa_line_code")) if r.get("sa_line_code") else None
        rop_code = decode_code("roplinecodedesc", r.get("rop_line_code")) if r.get("rop_line_code") else None
        if sa_code and sa_code.strip().lower() in ("not set", "_"):
            sa_code = None
        if rop_code and rop_code.strip().lower() in ("not set", "_"):
            rop_code = None

        row_score = 0
        has_start = start_node_id is not None and start_node_id in nids
        has_end = end_node_id is not None and end_node_id in nids
        if has_start and has_end:
            row_score += 50
            try:
                if nids.index(start_node_id) < nids.index(end_node_id):
                    row_score += 40
            except ValueError:
                pass
        elif has_end or has_start:
            row_score += 20

        if r.get("description"):
            row_score += 10
        if sa_code:
            row_score += 10
        if (r.get("name") or "").strip().upper() == "BUS":
            row_score -= 100

        pdir = _extract_direction_from_tokens(r.get("name"), r.get("description"), sa_code, rop_code)
        if pdir and (has_start or has_end or (start_node_id is None and end_node_id is None)):
            phys_dirs_on_same_nodes.add((str(r.get("longname") or "").strip().upper(), pdir))

        scored.append((row_score, r, sa_code, rop_code, pdir))

    scored.sort(key=lambda x: x[0], reverse=True)
    _, best, sa_code, rop_code, best_dir = scored[0]
    secondary_dir = best_dir if best_dir in ("up", "down") else None
    if secondary_dir is None:
        desc_l = (best.get("description") or "").lower()
        has_u = bool(re.search(r"\bup\b", desc_l))
        has_d = bool(re.search(r"\bdown\b|\bdn\b", desc_l))
        if has_u and not has_d:
            secondary_dir = "up"
        elif has_d and not has_u:
            secondary_dir = "down"

    best_ln = str(best.get("longname") or "").strip().upper()
    if best_ln and (not sa_code or not rop_code or rop_code == "_"):
        for _, sib_r, sib_sa, sib_rop, _ in scored[1:]:
            if str(sib_r.get("longname") or "").strip().upper() != best_ln:
                continue
            if not sa_code and sib_sa and sib_sa != "_":
                sa_code = sib_sa
            if (not rop_code or rop_code == "_") and sib_rop and sib_rop != "_":
                rop_code = sib_rop

    # Explicit directional SA/ROP code (e.g. DM, URL) overrides travel direction as secondary_dir.
    explicit_code_dir = _extract_direction_from_tokens(None, None, sa_code, rop_code)
    if explicit_code_dir in ("up", "down"):
        secondary_dir = explicit_code_dir

    best_desc_l = str(best.get("description") or "").strip().lower()
    is_explicit_single_dir_line = bool(
        re.search(r"\b(?:up|down)\s+(?:slow|fast|main|relief)\b", best_desc_l)
        and not re.search(r"\b(?:up\s*(?:&|and)\s*down|down\s*(?:&|and)\s*up|reversible)\b", best_desc_l)
    )
    dirs_for_best = {d for ln, d in phys_dirs_on_same_nodes if ln == best_ln}
    if (
        "up" in dirs_for_best
        and "down" in dirs_for_best
        and (not is_explicit_single_dir_line or (secondary_dir in ("up", "down") and secondary_dir != best_dir))
    ):
        best_dir = "reversible"

    plat_num = _extract_platform_number(
        best.get("name"),
        best.get("longname"),
        best.get("description"),
        sa_code,
        station_longname=best.get("station_longname"),
    )

    parts = [p for p in (best.get("name"), best.get("description"), sa_code, rop_code) if p]
    stn_ln = (best.get("station_longname") or "").strip().lower()
    if stn_ln and re.search(
        r"\b(?:siding|sdg|sdgs|depot|yard|t\.?m\.?d\.?|c\.?s\.?|royal\s+mail|freight|works)\b",
        stn_ln,
    ):
        joined_l = " ".join(parts).lower()
        if not re.search(r"\b(?:fast|main|slow|relief|loop|siding|sdg|depot|yard|bay|terminal)\b", joined_l):
            parts.append("Siding")

    hint_str = " | ".join(parts) if parts else None
    short_lbl = " / ".join(p for p in (best.get("name"), best.get("description")) if p) or sa_code or rop_code
    return {
        "direction_hint": best_dir,
        "secondary_dir": secondary_dir,
        "track_name_hint": hint_str,
        "platform_num": plat_num,
        "label": short_lbl,
    }


def _find_collinear_turnout_pair(
    cx: float,
    cy: float,
    nbrs: set[int],
    node_xy: dict[int, tuple[float, float]],
    visited: set[int],
) -> list[int]:
    """Identify the collinear straight-through leg pair at a turnout node (``cos_sim <= -0.92``).

    Args:
        cx: Turnout node schematic X coordinate.
        cy: Turnout node schematic Y coordinate.
        nbrs: Set of neighbour node IDs incident to the turnout.
        node_xy: Map of ``node_id -> (x, y)`` schematic coordinates.
        visited: Set of already-visited node IDs to exclude.

    Returns:
        List of unvisited node IDs from the most collinear pair, or ``[]``.
    """
    nbr_list = [nb for nb in nbrs if nb in node_xy]
    best_pair: Optional[tuple[int, int]] = None
    min_pair_cos = 2.0
    for idx_a, na in enumerate(nbr_list):
        v_ax, v_ay = node_xy[na][0] - cx, node_xy[na][1] - cy
        la = math.hypot(v_ax, v_ay)
        if la <= 1e-6:
            continue
        for nb in nbr_list[idx_a + 1:]:
            v_bx, v_by = node_xy[nb][0] - cx, node_xy[nb][1] - cy
            lb = math.hypot(v_bx, v_by)
            if lb <= 1e-6:
                continue
            p_cos = (v_ax * v_bx + v_ay * v_by) / (la * lb)
            if p_cos < min_pair_cos:
                min_pair_cos = p_cos
                best_pair = (na, nb)
    if best_pair and min_pair_cos <= -0.92:
        return [nb for nb in best_pair if nb not in visited]
    return []


def _choose_straight_through_neighbors(
    curr_id: int,
    prev_id: Optional[int],
    nbrs: set[int],
    node_xy: dict[int, tuple[float, float]],
    visited: set[int],
    allow_hop1_pair: bool = False,
) -> list[int]:
    """Choose straight-through continuation neighbours from ``curr_id`` without diverging onto crossovers."""
    cand_nbrs = [nb for nb in nbrs if nb != prev_id and nb not in visited]
    if not cand_nbrs:
        return []
    if len(nbrs) <= 2:
        return cand_nbrs
    if prev_id is not None and prev_id in node_xy and curr_id in node_xy:
        px, py = node_xy[prev_id]
        cx, cy = node_xy[curr_id]
        dx_in, dy_in = cx - px, cy - py
        len_in = math.hypot(dx_in, dy_in)
        best_nb = None
        best_cos = -2.0
        if len_in > 1e-6:
            for nb in cand_nbrs:
                if nb not in node_xy:
                    continue
                nx, ny = node_xy[nb]
                dx_out, dy_out = nx - cx, ny - cy
                len_out = math.hypot(dx_out, dy_out)
                if len_out > 1e-6:
                    cos_sim = (dx_in * dx_out + dy_in * dy_out) / (len_in * len_out)
                    if cos_sim > best_cos:
                        best_cos = cos_sim
                        best_nb = nb
        if best_nb is not None and best_cos >= 0.92:
            return [best_nb]
        if allow_hop1_pair and len(nbrs) >= 3:
            return _find_collinear_turnout_pair(cx, cy, nbrs, node_xy, visited)
        return []
    if allow_hop1_pair and curr_id in node_xy and len(nbrs) >= 3:
        cx, cy = node_xy[curr_id]
        return _find_collinear_turnout_pair(cx, cy, nbrs, node_xy, visited)
    return []


def _resolve_reversible_through_line_dir(
    cur,
    clean_nids: list[int],
    start_node_id: Optional[int],
    end_node_id: Optional[int],
    max_hops: int = 16,
) -> Optional[str]:
    """Walk straight-through out of a bi-directional platform to find its physical running-line direction.

    Args:
        cur: Active PostgreSQL cursor returning dict rows.
        clean_nids: Seed TPS node IDs.
        start_node_id: Optional asset start node ID.
        end_node_id: Optional asset end node ID.
        max_hops: Maximum graph hops to walk outwards.

    Returns:
        Unanimous physical running-line direction (``'up'`` or ``'down'``), or
        ``None`` if ambiguous or unresolved.
    """
    visited = set(clean_nids)
    if start_node_id is not None and end_node_id is not None and start_node_id != end_node_id:
        frontier: list[tuple[int, Optional[int]]] = [
            (int(end_node_id), int(start_node_id)),
            (int(start_node_id), int(end_node_id)),
        ]
    else:
        frontier = [(nid, None) for nid in clean_nids]

    found_dirs: set[str] = set()
    for _ in range(max_hops):
        if not frontier:
            break
        curr_ids = list({c for c, _ in frontier})
        cur.execute(
            "SELECT start_node_id, end_node_id FROM edges WHERE start_node_id = ANY(%s) OR end_node_id = ANY(%s)",
            (curr_ids, curr_ids),
        )
        adj_map: dict[int, set[int]] = {}
        for e in cur.fetchall():
            a, b = e["start_node_id"], e["end_node_id"]
            if a is not None and b is not None:
                adj_map.setdefault(a, set()).add(b)
                adj_map.setdefault(b, set()).add(a)

        coord_needed: set[int] = set()
        for curr_id, prev_id in frontier:
            nbrs = adj_map.get(curr_id, set())
            if len(nbrs) > 2:
                coord_needed.add(curr_id)
                if prev_id is not None:
                    coord_needed.add(prev_id)
                coord_needed.update(nbrs)

        node_xy: dict[int, tuple[float, float]] = {}
        if coord_needed:
            cur.execute("SELECT node_id, netx, nety FROM nodes WHERE node_id = ANY(%s)", (list(coord_needed),))
            for nr in cur.fetchall():
                if nr["netx"] is not None and nr["nety"] is not None:
                    node_xy[nr["node_id"]] = (float(nr["netx"]), float(nr["nety"]))

        next_frontier: list[tuple[int, Optional[int]]] = []
        next_nids: list[int] = []
        for curr_id, prev_id in frontier:
            nbrs = adj_map.get(curr_id, set())
            chosen = _choose_straight_through_neighbors(curr_id, prev_id, nbrs, node_xy, visited, allow_hop1_pair=False)
            for nb in chosen:
                if nb not in visited:
                    visited.add(nb)
                    next_frontier.append((nb, curr_id))
                    next_nids.append(nb)

        if not next_nids:
            break

        cur.execute(
            """
            SELECT st.name, st.longname, st.description, st.sa_line_code, st.node_ids
            FROM station_tracks st
            WHERE st.node_ids && %s::int[]
            LIMIT 20
            """,
            (next_nids,),
        )
        matched_running_nodes: set[int] = set()
        for r in cur.fetchall():
            sa_c = decode_code("salinecodedesc", r.get("sa_line_code")) if r.get("sa_line_code") else None
            if _extract_platform_number(r.get("name"), r.get("longname"), r.get("description"), sa_c) is not None:
                continue
            desc_l = str(r.get("description") or "").strip().lower()
            if re.search(r"\b(?:up\s*(?:&|and)\s*down|down\s*(?:&|and)\s*up|reversible)\b", desc_l):
                continue
            line_dir = None
            if re.search(r"\b(?:up\s+(?:main|fast|slow|relief|line)|(?:main|fast|slow|relief)\s+up)\b", desc_l):
                line_dir = "up"
            elif re.search(
                r"\b(?:(?:down|dn)\s+(?:main|fast|slow|relief|line)|(?:main|fast|slow|relief)\s+(?:down|dn))\b",
                desc_l,
            ):
                line_dir = "down"
            if line_dir:
                found_dirs.add(line_dir)
                r_nids = set(r.get("node_ids") or [])
                matched_running_nodes.update(n for n in next_nids if n in r_nids)

        if matched_running_nodes:
            next_frontier = [(n, p) for n, p in next_frontier if n not in matched_running_nodes]
        if len(found_dirs) > 1:
            return None
        frontier = next_frontier

    return next(iter(found_dirs)) if len(found_dirs) == 1 else None


def resolve_tps_track_identity(
    cur,
    node_ids: list,
    start_node_id: Optional[int] = None,
    end_node_id: Optional[int] = None,
    elr: Optional[str] = None,
    start_km: Optional[float] = None,
    end_km: Optional[float] = None,
) -> dict[str, Any]:
    """Resolve physical track direction, platform number, and line class for any TPS asset.

    Args:
        cur: Active PostgreSQL cursor returning dict rows.
        node_ids: Seed TPS node IDs associated with the asset.
        start_node_id: Optional start node ID for directed assets.
        end_node_id: Optional end node ID for directed assets.
        elr: Optional TPS ELR code for mileage-polarity fallback.
        start_km: Optional start chainage (m).
        end_km: Optional end chainage (m).

    Returns:
        Dict containing ``direction_hint``, ``secondary_dir`` (optional),
        ``track_name_hint``, ``platform_num``, and ``source``.
    """
    clean_nids = [int(n) for n in (node_ids or []) if n is not None]
    partial_hint: Optional[str] = None
    partial_plat: Optional[int] = None
    partial_source: Optional[str] = None

    if clean_nids:
        # 1. Direct station_tracks lookup (GIN indexed on node_ids).
        cur.execute(
            """
            SELECT st.track_id, st.name, st.longname, st.description,
                   st.rop_line_code, st.sa_line_code, st.directed, st.node_ids,
                   stn.longname AS station_longname
            FROM station_tracks st
            LEFT JOIN stations stn ON stn.station_id = st.station_id
            WHERE st.node_ids && %s::int[]
            LIMIT 20
            """,
            (clean_nids,),
        )
        st_rows = cur.fetchall()
        if st_rows:
            parsed = _parse_station_track_rows(st_rows, start_node_id=start_node_id, end_node_id=end_node_id)
            if parsed["direction_hint"]:
                sec_dir = parsed.get("secondary_dir") or (
                    infer_direction_from_km_delta(cur, elr, start_km, end_km)
                    if parsed["direction_hint"] == "reversible"
                    else None
                )
                hint_l = (parsed.get("track_name_hint") or "").lower()
                has_explicit_sa_dir = any(
                    _extract_direction_from_tokens(
                        None,
                        None,
                        decode_code("salinecodedesc", r.get("sa_line_code")) if r.get("sa_line_code") else None,
                        None,
                    )
                    in ("up", "down")
                    for r in st_rows
                )
                if (
                    parsed["direction_hint"] == "reversible"
                    and parsed.get("platform_num") is not None
                    and not has_explicit_sa_dir
                    and "terminal" not in hint_l
                    and "bay" not in hint_l
                ):
                    phys_dir = _resolve_reversible_through_line_dir(
                        cur, clean_nids, start_node_id=start_node_id, end_node_id=end_node_id
                    )
                    if phys_dir in ("up", "down"):
                        sec_dir = phys_dir
                return {
                    "direction_hint": parsed["direction_hint"],
                    "secondary_dir": sec_dir,
                    "track_name_hint": parsed["track_name_hint"],
                    "platform_num": parsed["platform_num"],
                    "source": f"TPS Track ({parsed['label']})" if parsed["label"] else "TPS Track",
                }
            if parsed["track_name_hint"]:
                partial_hint = parsed["track_name_hint"]
                partial_plat = parsed["platform_num"]
                partial_source = f"TPS Track ({parsed['label']})" if parsed["label"] else "TPS Track"

        # 2. Direct routes lookup (GIN indexed on node_ids).
        cur.execute(
            """
            SELECT route_id, rop_line_code_id, sa_line_code_id, node_ids
            FROM routes
            WHERE node_ids && %s::int[]
            LIMIT 20
            """,
            (clean_nids,),
        )
        rt_rows = cur.fetchall()
        if rt_rows:
            norm_rt = [
                {
                    "name": None,
                    "longname": None,
                    "description": None,
                    "rop_line_code": r.get("rop_line_code_id"),
                    "sa_line_code": r.get("sa_line_code_id"),
                    "node_ids": r.get("node_ids"),
                }
                for r in rt_rows
            ]
            parsed = _parse_station_track_rows(norm_rt, start_node_id=start_node_id, end_node_id=end_node_id)
            if parsed["direction_hint"]:
                sec_dir = parsed.get("secondary_dir") or (
                    infer_direction_from_km_delta(cur, elr, start_km, end_km)
                    if parsed["direction_hint"] == "reversible"
                    else None
                )
                return {
                    "direction_hint": parsed["direction_hint"],
                    "secondary_dir": sec_dir,
                    "track_name_hint": parsed["track_name_hint"] or partial_hint,
                    "platform_num": parsed["platform_num"] if parsed["platform_num"] is not None else partial_plat,
                    "source": f"TPS Route ({parsed['label']})" if parsed["label"] else "TPS Route",
                }
            if parsed["track_name_hint"] and not partial_hint:
                partial_hint = parsed["track_name_hint"]
                partial_plat = parsed["platform_num"]
                partial_source = f"TPS Route ({parsed['label']})" if parsed["label"] else "TPS Route"

        # 3. Bounded straight-through graph walk along TPS edges (up to 24 hops).
        visited = set(clean_nids)
        if start_node_id is not None and end_node_id is not None and start_node_id != end_node_id:
            frontier: list[tuple[int, Optional[int]]] = [
                (int(end_node_id), int(start_node_id)),
                (int(start_node_id), int(end_node_id)),
            ]
        else:
            frontier = [(nid, None) for nid in clean_nids]

        for hop in range(1, 25):
            if not frontier:
                break
            curr_ids = list({c for c, _ in frontier})
            cur.execute(
                "SELECT start_node_id, end_node_id FROM edges WHERE start_node_id = ANY(%s) OR end_node_id = ANY(%s)",
                (curr_ids, curr_ids),
            )
            adj_map: dict[int, set[int]] = {}
            for e in cur.fetchall():
                a, b = e["start_node_id"], e["end_node_id"]
                if a is not None and b is not None:
                    adj_map.setdefault(a, set()).add(b)
                    adj_map.setdefault(b, set()).add(a)

            coord_needed: set[int] = set()
            for curr_id, prev_id in frontier:
                nbrs = adj_map.get(curr_id, set())
                if len(nbrs) > 2:
                    coord_needed.add(curr_id)
                    if prev_id is not None:
                        coord_needed.add(prev_id)
                    coord_needed.update(nbrs)

            node_xy: dict[int, tuple[float, float]] = {}
            if coord_needed:
                cur.execute("SELECT node_id, netx, nety FROM nodes WHERE node_id = ANY(%s)", (list(coord_needed),))
                for nr in cur.fetchall():
                    if nr["netx"] is not None and nr["nety"] is not None:
                        node_xy[nr["node_id"]] = (float(nr["netx"]), float(nr["nety"]))

            next_frontier: list[tuple[int, Optional[int]]] = []
            next_nids: list[int] = []
            for curr_id, prev_id in frontier:
                nbrs = adj_map.get(curr_id, set())
                chosen = _choose_straight_through_neighbors(
                    curr_id, prev_id, nbrs, node_xy, visited, allow_hop1_pair=(hop == 1)
                )
                for nb in chosen:
                    if nb not in visited:
                        visited.add(nb)
                        next_frontier.append((nb, curr_id))
                        next_nids.append(nb)

            if not next_nids:
                break

            cur.execute(
                """
                SELECT st.track_id, st.name, st.longname, st.description,
                       st.rop_line_code, st.sa_line_code, st.directed, st.node_ids,
                       stn.longname AS station_longname
                FROM station_tracks st
                LEFT JOIN stations stn ON stn.station_id = st.station_id
                WHERE st.node_ids && %s::int[]
                LIMIT 20
                """,
                (next_nids,),
            )
            walk_rows = cur.fetchall()
            if walk_rows:
                parsed = _parse_station_track_rows(walk_rows)
                if parsed["direction_hint"]:
                    walk_dir = parsed["direction_hint"]
                    sec_dir = parsed.get("secondary_dir") or (
                        infer_direction_from_km_delta(cur, elr, start_km, end_km)
                        if walk_dir == "reversible"
                        else walk_dir
                    )
                    if (
                        walk_dir in ("up", "down")
                        and start_node_id is not None
                        and end_node_id is not None
                        and start_node_id != end_node_id
                    ):
                        cur.execute(
                            """
                            SELECT 1 FROM signals
                            WHERE start_node_id = %s AND end_node_id = %s
                              AND bumper = false AND name IS NOT NULL AND name <> '' AND name NOT ILIKE 'POINT%%'
                            LIMIT 1
                            """,
                            (end_node_id, start_node_id),
                        )
                        if cur.fetchone():
                            walk_dir = "reversible"
                    return {
                        "direction_hint": walk_dir,
                        "secondary_dir": sec_dir,
                        "track_name_hint": parsed["track_name_hint"] or partial_hint,
                        "platform_num": parsed["platform_num"] if parsed["platform_num"] is not None else partial_plat,
                        "source": f"TPS Track Walk ({hop} hop{'s' if hop > 1 else ''}: {parsed['label']})",
                    }
                if parsed["track_name_hint"] and not partial_hint:
                    partial_hint = parsed["track_name_hint"]
                    partial_plat = parsed["platform_num"]
                    partial_source = f"TPS Track Walk ({hop} hop{'s' if hop > 1 else ''}: {parsed['label']})"
            frontier = next_frontier[:16]

    # 3b. Dead-end buffer-stop detection on valency-1 nodes.
    if not partial_hint and (start_node_id is not None or end_node_id is not None):
        ep_nids = [int(n) for n in (start_node_id, end_node_id) if n is not None]
        cur.execute(
            "SELECT start_node_id, end_node_id FROM edges WHERE start_node_id = ANY(%s) OR end_node_id = ANY(%s)",
            (ep_nids, ep_nids),
        )
        ep_nbrs: dict[int, set[int]] = {n: set() for n in ep_nids}
        for e in cur.fetchall():
            a, b = e["start_node_id"], e["end_node_id"]
            if a in ep_nbrs and b is not None and b != a:
                ep_nbrs[a].add(b)
            if b in ep_nbrs and a is not None and a != b:
                ep_nbrs[b].add(a)
        if any(len(nbrs) == 1 for nbrs in ep_nbrs.values()):
            partial_hint = "Dead-End Bay"
            partial_source = "TPS Dead-End Spur"

    # 4. Data-driven per-ELR mileage polarity fallback.
    km_dir = infer_direction_from_km_delta(cur, elr, start_km, end_km)
    if km_dir:
        pol_src = f"ELR {elr} Mileage Polarity" if elr else "Mileage Polarity"
        return {
            "direction_hint": km_dir,
            "track_name_hint": partial_hint,
            "platform_num": partial_plat,
            "source": f"{partial_source} + {pol_src}" if partial_source else pol_src,
        }

    if partial_hint:
        return {
            "direction_hint": None,
            "track_name_hint": partial_hint,
            "platform_num": partial_plat,
            "source": partial_source,
        }

    return {"direction_hint": None, "track_name_hint": None, "platform_num": None, "source": None}

