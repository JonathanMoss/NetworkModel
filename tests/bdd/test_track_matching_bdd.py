"""BDD step definitions for tests/bdd/features/track_matching.feature."""

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

import geometry
import track_identity as ti
import track_scoring as ts

pytestmark = pytest.mark.bdd

scenarios("features/track_matching.feature")


@pytest.fixture
def ctx():
    """Mutable per-scenario state bag."""
    return {}


@given(parsers.parse('an NWR corridor on ELR "{elr}" with candidate tracks:'))
def given_corridor(ctx, elr, datatable):
    """Store candidate tracks parsed from the Gherkin data table."""
    ctx["elr"] = elr
    headers = datatable[0]
    ctx["candidates"] = [
        {
            headers[i]: (float(row[i]) if headers[i].endswith("_m") else row[i])
            for i in range(len(headers))
        }
        for row in datatable[1:]
    ]


@when(
    parsers.parse(
        'I score the candidates at {target_m:g}m for direction "{direction}" and hint "{hint}"'
    )
)
def when_score_plain(ctx, target_m, direction, hint):
    """Score all corridor candidates without a platform number."""
    scored = [
        (
            c["track_id"],
            ts.score_track_candidate(
                c["track_id"], direction, hint, c["start_m"], c["end_m"], target_m
            ),
        )
        for c in ctx["candidates"]
    ]
    scored.sort(key=lambda item: item[1], reverse=True)
    ctx["best_track"] = scored[0][0]


@when(
    parsers.parse(
        'I score the candidates at {target_m:g}m for direction "{direction}" and hint "{hint}" on platform {platform_num:d}'
    )
)
def when_score_platform(ctx, target_m, direction, hint, platform_num):
    """Score all corridor candidates with an explicit platform number."""
    scored = [
        (
            c["track_id"],
            ts.score_track_candidate(
                c["track_id"],
                direction,
                hint,
                c["start_m"],
                c["end_m"],
                target_m,
                platform_num=platform_num,
            ),
        )
        for c in ctx["candidates"]
    ]
    scored.sort(key=lambda item: item[1], reverse=True)
    ctx["best_track"] = scored[0][0]


@then(parsers.parse('the highest-scoring track should be "{expected}"'))
def then_best_track(ctx, expected):
    """Assert the winning track_id matches expectation."""
    assert ctx["best_track"] == expected


@given(parsers.parse('TPS station track rows for "{name}" with platform code "{plat}"'))
def given_station_track_rows(ctx, name, plat, monkeypatch):
    """Prepare opposing directed rows sharing a physical track with an SA line code."""
    monkeypatch.setattr(
        ti,
        "decode_code",
        lambda cat, cid: "UM" if (cat == "salinecodedesc" and cid == 1) else None,
    )
    ctx["st_rows"] = [
        {
            "name": name,
            "longname": plat,
            "description": "Up Main",
            "node_ids": [10, 20],
            "sa_line_code": 1,
        },
        {
            "name": name,
            "longname": plat,
            "description": "Down Main",
            "node_ids": [20, 10],
            "sa_line_code": 1,
        },
    ]


@when(parsers.parse('I parse the station track identity with signal direction "{sig_dir}"'))
def when_parse_station_tracks(ctx, sig_dir):
    """Run _parse_station_track_rows with start/end nodes aligned to sig_dir."""
    start_nid, end_nid = (20, 10) if sig_dir == "down" else (10, 20)
    res = ti._parse_station_track_rows(
        ctx["st_rows"], start_node_id=start_nid, end_node_id=end_nid
    )
    ctx["parsed"] = {
        "direction": res["direction_hint"],
        "hint": res["track_name_hint"],
        "platform": res["platform_num"],
        "secondary_dir": res["secondary_dir"],
    }


@then(parsers.parse('the resolved direction should be "{expected}"'))
def then_resolved_dir(ctx, expected):
    """Assert resolved primary direction."""
    assert ctx["parsed"]["direction"] == expected


@then(parsers.parse('the resolved secondary direction should be "{expected}"'))
def then_resolved_sec_dir(ctx, expected):
    """Assert resolved secondary direction."""
    assert ctx["parsed"]["secondary_dir"] == expected


@then(parsers.parse("the resolved platform number should be {expected:d}"))
def then_resolved_platform(ctx, expected):
    """Assert resolved integer platform number."""
    assert ctx["parsed"]["platform"] == expected


@given(parsers.parse('a raw TPS signal name "{raw_name}"'))
def given_raw_name(ctx, raw_name):
    """Store raw TPS signal name."""
    ctx["raw_name"] = raw_name


@when(parsers.parse('I normalize it as asset type "{asset_type}"'))
def when_normalize_name(ctx, asset_type):
    """Normalize raw name via clean_rail_asset_name."""
    ctx["clean_name"] = geometry.clean_rail_asset_name(ctx["raw_name"], asset_type)


@then(parsers.parse('the normalized rail asset name should be "{expected}"'))
def then_clean_name(ctx, expected):
    """Assert normalized asset name."""
    assert ctx["clean_name"] == expected
