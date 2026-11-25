"""NWR Appendix A candidate track scoring engine.

Scores candidate Network Rail Track Model centrelines (4-digit ``track_id``
encoding ``D C NN``: Direction, Class, Sequence/Platform number) against
resolved TPS track identity hints.

The monolithic scorer is decomposed into focused, single-concern helpers so
each scoring dimension can be understood, documented, and unit-tested in
isolation while preserving exact numerical compatibility with the 194-case
UK regression suite.
"""

from dataclasses import dataclass
import re
from typing import Optional

# ---------------------------------------------------------------------------
# Token vocabularies used across scoring stages
# ---------------------------------------------------------------------------
_SIDING_WORDS = ("siding", "sding", "yard", "depot", "headshunt")
_SIDING_TOKENS = frozenset({"sdg", "ms1", "ms2", "ms3", "mbr"})
_ENGINE_WORDS = (
    "engine",
    "carriage",
    "cariage",
    "reception",
    "back line",
    "washer",
    "arrivals",
    "departures",
    "departure",
)
_ENGINE_TOKENS = frozenset({"crl", "ecl", "cl1", "cl2", "rec", "rcp1", "rcp2", "w1", "w2", "wrb"})
_FAST_TOKENS = frozenset({"uf", "df", "um", "dm", "ufl", "dfl", "uml", "dml", "fl", "ml"})
_SLOW_TOKENS = frozenset({"us", "ds", "usl", "dsl", "url", "drl", "ur", "dr", "sl", "rl"})
_LOOP_TOKENS = frozenset({"udl", "dgl", "ugl", "udgl", "dpl", "upl", "udpl", "dupl", "dbp", "ubp", "bgl", "rvl"})
_EXPLICIT_LOOP_TOKENS = frozenset({"dpl", "upl", "udpl", "dupl", "dgl", "ugl", "udgl"})
_GOODS_LOOP_TOKENS = frozenset({"dgl", "ugl", "udgl", "bgl"})
_GOODS_WORDS = ("goods", "freight", "independent", "indep")
_GOODS_TOKENS = frozenset({"dg", "ug", "gl", "dmi", "umi", "mil", "ind", "di", "ui"})
_ELECTRIC_SUB_TOKENS = frozenset({"el", "ue", "de", "uel", "del", "dsb", "usb"})
_SLOW_2_TOKENS = frozenset({"sl2", "ds2", "us2", "s2"})
_RUNNING_LINE_TOKENS = frozenset({
    "dm", "um", "df", "uf", "ds", "us", "dr", "ur",
    "ml", "fl", "sl", "rl", "dml", "uml", "dfl", "ufl",
    "dsl", "usl", "drl", "url", "dpl", "upl", "dep", "arr",
})
_MAIN_EXCLUSION_TOKENS = frozenset({
    "dm", "um", "df", "uf", "dn", "un", "ds", "us", "dr", "ur",
    "dg", "ug", "dl", "ul", "fl", "ml", "sl", "rl", "gl",
})


@dataclass(frozen=True)
class HintFlags:  # pylint: disable=too-many-instance-attributes
    """Normalized boolean flags and token sets parsed from a TPS ``name_hint``.

    Attributes:
        nh_l: Lowercased hint string (empty string if ``None``).
        tokens: Set of alphanumeric tokens extracted from ``nh_l``.
        is_siding: True if the hint indicates a siding, yard, depot, or headshunt.
        is_ul_dl_named_line: True for named ``UL``/``DL`` fast lines (e.g. Up/Down Leeds).
        is_electric_or_sub: True for Electric or Suburban running-line hints.
        is_slow_2: True for secondary Slow 2 / Relief 2 lines (``SL2``, ``US2``, ``DS2``).
        is_bay_or_term: True if the hint mentions ``bay`` or ``terminal``.
        is_running_line: True if the hint describes a running line (excluding sidings).
        is_secondary_fast: True for secondary/parallel fast-coded lines (``xx50–xx59``).
    """

    nh_l: str
    tokens: frozenset[str]
    is_siding: bool
    is_ul_dl_named_line: bool
    is_electric_or_sub: bool
    is_slow_2: bool
    is_bay_or_term: bool
    is_running_line: bool
    is_secondary_fast: bool


def extract_hint_flags(name_hint: Optional[str]) -> HintFlags:
    """Parse a raw TPS track-name hint into structured ``HintFlags``.

    Args:
        name_hint: Combined TPS station-track/route hint string (or ``None``).

    Returns:
        A populated ``HintFlags`` instance.
    """
    nh_l = (name_hint or "").lower()
    tokens = frozenset(re.findall(r"[a-z0-9]+", nh_l)) if nh_l else frozenset()

    is_siding = any(w in nh_l for w in _SIDING_WORDS) or bool(tokens & _SIDING_TOKENS)

    # Named UL/DL lines (e.g. York 'Up Leeds' / 'Down Leeds' coded xx50) must not be
    # confused with Up/Down Loops ('UL'/'DL' with 'loop').
    is_ul_dl_named_line = (
        bool(nh_l)
        and bool(tokens & {"dl", "ul"})
        and "loop" not in nh_l
        and (
            bool(re.search(r"\bfast\b", nh_l))
            or bool(
                re.search(
                    r"\b(?:up|down)\s+(?!loop\b|goods\b|freight\b|siding\b|slow\b|relief\b|main\b|line\b)[a-z]{3,}\b",
                    nh_l,
                )
            )
        )
    )

    is_electric_or_sub = bool(nh_l) and (
        bool(tokens & _ELECTRIC_SUB_TOKENS) or bool(re.search(r"\b(?:suburb|electric)\b", nh_l))
    )

    is_slow_2 = bool(nh_l) and (
        bool(tokens & _SLOW_2_TOKENS) or bool(re.search(r"\b(?:slow|relief)\s*2\b", nh_l))
    )

    is_bay_or_term = bool(nh_l) and any(w in nh_l for w in ("bay", "terminal"))

    is_running_line = bool(nh_l) and not is_siding and (
        is_electric_or_sub
        or is_ul_dl_named_line
        or bool(re.search(r"\b(?:fast|main|slow|relief|goods|freight|loop)\b", nh_l))
        or bool(tokens & _RUNNING_LINE_TOKENS)
    )

    is_secondary_fast = bool(nh_l) and (
        is_ul_dl_named_line
        or bool(re.search(r"\bsuburb", nh_l))
        or bool(tokens & {"dsb", "usb"})
        or (
            bool(re.match(r"^(?:d|u)-(?:d|u)[a-z]\b", nh_l))
            and not bool(tokens & _MAIN_EXCLUSION_TOKENS)
            and not bool(
                re.search(
                    r"\b(?:main|fast|slow|relief|goods|freight|loop|single|siding|yard|depot|platform|bay|terminal)\b",
                    nh_l,
                )
            )
        )
    )

    return HintFlags(
        nh_l=nh_l,
        tokens=tokens,
        is_siding=is_siding,
        is_ul_dl_named_line=is_ul_dl_named_line,
        is_electric_or_sub=is_electric_or_sub,
        is_slow_2=is_slow_2,
        is_bay_or_term=is_bay_or_term,
        is_running_line=is_running_line,
        is_secondary_fast=is_secondary_fast,
    )


def score_mileage_coverage(c_start: float, c_end: float, target_m: Optional[float]) -> float:
    """Score how well a centreline's mileage span ``[c_start, c_end]`` covers ``target_m``.

    Strict containment (``+85``) strongly outranks near-window matches (``+35``)
    and non-overlapping segments (``-40`` minus ``0.15/m``, capped at ``-80``)
    so a nearby segment cannot hijack an asset away from its actual chainage.

    Args:
        c_start: Start chainage of the NWR centreline in metres.
        c_end: End chainage of the NWR centreline in metres.
        target_m: Target TPS mileage in metres (or ``None``).

    Returns:
        Mileage coverage score adjustment.
    """
    if target_m is None:
        return 0.0
    min_m = min(c_start, c_end)
    max_m = max(c_start, c_end)
    if min_m <= target_m <= max_m:
        return 85.0
    if min_m - 50.0 <= target_m <= max_m + 50.0:
        return 35.0
    dist_to_seg = min(abs(min_m - target_m), abs(max_m - target_m))
    return -(40.0 + min(40.0, dist_to_seg * 0.15))


def score_direction_alignment(
    tid: str,
    dir_hint: Optional[str],
    secondary_dir: Optional[str],
) -> tuple[float, bool]:
    """Score the 1st digit of an NWR ``track_id`` (``1``=Up, ``2``=Down, ``3``=Reversible).

    Args:
        tid: 4-digit NWR ``track_id`` string.
        dir_hint: Primary resolved TPS direction (``'up'``, ``'down'``, ``'reversible'``).
        secondary_dir: Physical line direction tiebreaker for reversible tracks.

    Returns:
        Tuple ``(score_delta, is_rev_hint)``.
    """
    if not dir_hint:
        return 0.0, False

    dh = dir_hint.lower()
    if "rev" in dh or "bi" in dh:
        if tid.startswith("3"):
            return 70.0, True
        if tid.startswith(("1", "2")):
            # Break ties on bi-directionalised 1xxx/2xxx lines using secondary_dir.
            if secondary_dir == "down":
                return (30.0 if tid.startswith("2") else 10.0), True
            if secondary_dir == "up":
                return (30.0 if tid.startswith("1") else 10.0), True
            return 20.0, True
        return 0.0, True

    if "down" in dh or dh == "dn":
        if tid.startswith("2"):
            return 70.0, False
        if tid.startswith("3"):
            return 25.0, False
        if tid.startswith("1"):
            return -60.0, False
    elif "up" in dh:
        if tid.startswith("1"):
            return 70.0, False
        if tid.startswith("3"):
            return 25.0, False
        if tid.startswith("2"):
            return -60.0, False

    return 0.0, False


def _score_secondary_running_suffix(tid: str, flags: HintFlags, tid_nn: int) -> float:
    """Score secondary running-line suffixes (``Slow 2``, ``xx50–xx59`` Electric/Suburban, ``xx00``)."""
    if flags.is_slow_2 and tid[1] == "2" and tid_nn in (2, 52):
        return 25.0
    if (flags.is_secondary_fast or flags.is_electric_or_sub) and tid[1] == "1" and 50 <= tid_nn <= 59:
        if tid_nn == 52 and (bool(flags.tokens & {"el", "ue", "de"}) or "electric" in flags.nh_l):
            return 22.0
        if tid_nn == 54 and (bool(flags.tokens & {"dsb", "usb"}) or "suburb" in flags.nh_l):
            return 22.0
        return 20.0
    if tid_nn == 0 and tid[1] in ("1", "2", "4", "5") and not (
        flags.is_secondary_fast or flags.is_electric_or_sub or flags.is_slow_2
    ):
        return 5.0
    return 0.0


def score_platform_and_line_seq(
    tid: str,
    flags: HintFlags,
    dir_hint: Optional[str],
    is_rev_hint: bool,
    secondary_dir: Optional[str],
    platform_num: Optional[int],
    c_start: float,
    c_end: float,
    target_m: Optional[float],
    edge_vmax: Optional[int],
) -> tuple[float, bool]:
    """Score the 3rd & 4th digits (``NN``) of a 4-digit NWR ``track_id``.

    Handles:
      - Platform 0 bays (``3600``, ``3800``)
      - Numbered platforms/sidings ``01..99`` (with opposite-direction, high-speed
        through-platform, low-speed 1st-loop, and loop-index guards)
      - Lettered (``A``–``H``) and numbered (``1L``–``8L``) approach lines
      - Secondary running lines (``Slow 2``, ``xx50–xx59`` Electric/Suburban, ``xx00``)

    Args:
        tid: Candidate 4-digit NWR ``track_id``.
        flags: Parsed ``HintFlags``.
        dir_hint: Primary TPS direction hint.
        is_rev_hint: Whether ``dir_hint`` is reversible.
        secondary_dir: Physical line direction tiebreaker.
        platform_num: Extracted 0..99 platform/siding number (or ``None``).
        c_start: Centreline start chainage (m).
        c_end: Centreline end chainage (m).
        target_m: Target TPS mileage (m).
        edge_vmax: TPS edge speed limit in km/h (or ``None``).

    Returns:
        Tuple ``(score_delta, exact_line_match)``.
    """
    score = 0.0
    exact_line_match = False

    if platform_num is not None and 0 <= platform_num <= 99 and len(tid) == 4 and tid[2:4].isdigit():
        tid_nn = int(tid[2:4])
        if platform_num == 0:
            # Platform 0 matches xx00 only on bay/station-platform classes (6, 8).
            if tid[1] in ("6", "8"):
                score += 120.0 if tid_nn == 0 else -55.0
        else:
            if flags.is_siding:
                allowed_classes = ("8", "9")
            elif flags.is_running_line:
                allowed_classes = ("1", "2", "4", "5", "6", "8")
            else:
                allowed_classes = ("1", "2", "4", "5", "6", "8", "9")

            if tid[1] in allowed_classes:
                is_opp_running_dir = (
                    (tid[1] in ("1", "2") or (tid[1] == "5" and not is_rev_hint))
                    and not flags.is_bay_or_term
                    and (
                        (secondary_dir == "up" and tid.startswith("2"))
                        or (secondary_dir == "down" and tid.startswith("1"))
                    )
                )
                is_loop_hint = bool(flags.nh_l) and (
                    "loop" in flags.nh_l or bool(flags.tokens & _EXPLICIT_LOOP_TOKENS)
                )
                is_fast_through_plat = bool(
                    tid[1] == "5"
                    and flags.is_running_line
                    and not is_loop_hint
                    and edge_vmax
                    and edge_vmax >= 70
                )
                is_first_loop_low_speed = bool(
                    tid[1] == "5"
                    and tid_nn == 1
                    and edge_vmax
                    and 10 <= edge_vmax <= 48
                    and not is_opp_running_dir
                    and not bool(flags.tokens & _FAST_TOKENS)
                    and (target_m is None or min(c_start, c_end) - 2.0 <= target_m <= max(c_start, c_end) + 2.0)
                )
                if tid_nn == platform_num and not is_opp_running_dir and not is_fast_through_plat:
                    score += 120.0
                elif 0 < tid_nn < 50 and not is_first_loop_low_speed:
                    # Softer penalty (-15) on explicit x5xx loop hints whose NWR loop index
                    # differs from the TPS platform number (e.g. Paddock Wood 3503).
                    score -= 15.0 if (is_loop_hint and tid[1] == "5") else 55.0
            elif flags.is_siding and tid[1] == "5" and tid_nn == platform_num:
                # Numbered siding falling back to numbered x5xx loops (e.g. SCM2 U-3SDG -> 1503).
                score += 25.0

        score += _score_secondary_running_suffix(tid, flags, tid_nn)
        return score, exact_line_match

    if platform_num is None and len(tid) == 4 and tid[1] in ("1", "2", "4"):
        # Lettered (A..H) or numbered (1L..8L) major-terminus approach lines.
        lnum = None
        letter_m = re.search(r"(?:^|\|\s*)(?:(?:u|d|e)-)?([a-h])(?:~|\s*\||\s*$|\s+)", flags.nh_l)
        if letter_m:
            lnum = ord(letter_m.group(1)) - ord("a") + 1
        else:
            num_line_m = re.search(
                r"(?:^|\|\s*)(?:(?:u|d|e)-([1-8])l[a-z]?|l([1-8]))(?:\s*\||\s*$|\s+)",
                flags.nh_l,
            )
            if num_line_m:
                lnum = int(num_line_m.group(1) or num_line_m.group(2))

        if lnum is not None and tid[2:4].isdigit() and 1 <= int(tid[2:4]) <= 9:
            tnn = int(tid[2:4])
            dh_l = (dir_hint or "").lower()
            is_opp_dir = (
                ("up" in dh_l and not is_rev_hint and tid.startswith("2"))
                or (("down" in dh_l or dh_l == "dn") and not is_rev_hint and tid.startswith("1"))
            )
            in_cov_win = target_m is None or (min(c_start, c_end) - 50.0 <= target_m <= max(c_start, c_end) + 50.0)
            if tnn == lnum and not is_opp_dir:
                if in_cov_win:
                    score += 125.0
                    exact_line_match = True
                else:
                    score += 40.0
            elif lnum > 1 and tnn > 1:
                score -= 30.0 + min(10.0, abs(tnn - lnum) * 2.0)
            else:
                score -= 40.0

        if flags.is_slow_2 and tid[1] == "2" and tid[2:4] in ("02", "52"):
            score += 25.0
        elif flags.is_secondary_fast or flags.is_electric_or_sub:
            if tid[1] == "1" and tid[2:4].isdigit() and 50 <= int(tid[2:4]) <= 59:
                tnn5 = int(tid[2:4])
                if tnn5 == 52 and (bool(flags.tokens & {"el", "ue", "de"}) or "electric" in flags.nh_l):
                    score += 22.0
                elif tnn5 == 54 and (bool(flags.tokens & {"dsb", "usb"}) or "suburb" in flags.nh_l):
                    score += 22.0
                else:
                    score += 20.0
        elif tid[2:4] == "00" and not flags.is_slow_2:
            score += 5.0
        elif tid[2:4] == "01" and tid[1] == "2":
            score += 5.0

    return score, exact_line_match


def _score_loop_class(tid: str, d2: str, flags: HintFlags) -> float:
    """Score NWR class digit ``d2`` when the TPS hint indicates a Passenger or Goods Loop."""
    has_slow_word = bool(re.search(r"\b(?:slow|relief)\b", flags.nh_l))
    is_goods_loop = bool(flags.tokens & _GOODS_LOOP_TOKENS) or "goods" in flags.nh_l or "freight" in flags.nh_l
    if d2 == "3" and is_goods_loop:
        return 65.0
    if d2 == "5":
        return 55.0
    if d2 == "2" and has_slow_word:
        return 55.0 if (len(tid) == 4 and tid[2:4] == "02") else 50.0
    if d2 == "2":
        return 5.0
    if d2 == "1" and has_slow_word:
        return -15.0
    if d2 == "1":
        return 10.0 if bool(re.search(r"\b(?:fast|main)\b", flags.nh_l)) else -20.0
    if d2 == "3" and ("goods" in flags.nh_l or "g" in flags.tokens or "gl" in flags.nh_l):
        return 20.0
    if d2 == "6":
        return -60.0
    if d2 in ("7", "8", "9"):
        return -25.0
    return -10.0


def _score_dedicated_platform_class(
    tid: str,
    d2: str,
    flags: HintFlags,
    is_rev_hint: bool,
    platform_num: Optional[int],
    c_start: float,
    c_end: float,
    target_m: Optional[float],
    edge_vmax: Optional[int],
) -> float:
    """Score NWR class digit ``d2`` for dedicated station platform hints (without Fast/Slow/Bay words)."""
    has_fast_tok = bool(flags.tokens & _FAST_TOKENS)
    has_slow_tok = bool(flags.tokens & _SLOW_TOKENS)
    is_exact_plat_nn = (
        platform_num is not None and len(tid) == 4 and tid[2:4].isdigit() and int(tid[2:4]) == platform_num
    )
    is_wrong_plat_nn = (
        platform_num is not None
        and len(tid) == 4
        and tid[2:4].isdigit()
        and 0 < int(tid[2:4]) < 50
        and int(tid[2:4]) != platform_num
    )
    if d2 == "5":
        if flags.is_running_line and edge_vmax and edge_vmax >= 70:
            return -20.0
        if is_wrong_plat_nn and not (
            len(tid) == 4
            and tid[2:4] == "01"
            and edge_vmax
            and 10 <= edge_vmax <= 48
            and not has_fast_tok
            and (target_m is None or min(c_start, c_end) - 2.0 <= target_m <= max(c_start, c_end) + 2.0)
        ):
            return 15.0
        return 75.0
    if d2 == "1":
        return 35.0 if has_fast_tok else 15.0
    if d2 == "2":
        if has_slow_tok or (is_rev_hint and not has_fast_tok):
            return 35.0
        if not has_fast_tok and edge_vmax and 10 <= edge_vmax <= 80:
            return 30.0
        return 15.0
    if d2 == "4":
        return 15.0
    if d2 == "6":
        return -25.0
    if d2 == "8":
        return 5.0 if is_exact_plat_nn else -10.0
    if d2 in ("7", "9"):
        return -30.0
    return 0.0


def score_line_class(
    tid: str,
    flags: HintFlags,
    is_rev_hint: bool,
    platform_num: Optional[int],
    exact_line_match: bool,
    c_start: float,
    c_end: float,
    target_m: Optional[float],
    edge_vmax: Optional[int],
) -> tuple[float, bool]:
    """Score the 2nd digit (Appendix A line class ``1``–``9``) against TPS track hints.

    Args:
        tid: Candidate 4-digit NWR ``track_id``.
        flags: Parsed ``HintFlags``.
        is_rev_hint: Whether the primary direction hint is reversible.
        platform_num: Extracted platform/siding number (or ``None``).
        exact_line_match: Whether an exact lettered/numbered approach line matched.
        c_start: Centreline start chainage (m).
        c_end: Centreline end chainage (m).
        target_m: Target TPS mileage (m).
        edge_vmax: TPS edge speed limit in km/h (or ``None``).

    Returns:
        Tuple ``(score_delta, matched_class)``.
    """
    if not flags.nh_l or len(tid) < 2:
        return 0.0, False

    nh = flags.nh_l
    tokens = flags.tokens
    d2 = tid[1]

    if any(w in nh for w in _ENGINE_WORDS) or bool(tokens & _ENGINE_TOKENS):
        if d2 == "8":
            return 55.0, True
        if d2 == "9":
            return 35.0, True
        if d2 in ("1", "2"):
            return -25.0, True
        if d2 == "7":
            return -20.0, True
        return -10.0, True

    if flags.is_siding:
        if d2 == "9":
            return 55.0, True
        if d2 == "8":
            return 35.0, True
        if d2 in ("1", "2"):
            return -25.0, True
        if d2 == "7":
            return -20.0, True
        return -10.0, True

    if "crossover" in nh or "xover" in nh:
        return (50.0 if d2 == "7" else -15.0), True

    if "dead-end" in nh:
        if d2 == "6":
            return 60.0, True
        if d2 == "9":
            return 55.0, True
        if d2 == "8":
            return 35.0, True
        if d2 in ("1", "2", "7"):
            return -25.0, True
        return 0.0, True

    if flags.is_bay_or_term:
        if d2 == "6":
            return 60.0, True
        if d2 in ("1", "2"):
            return -20.0, True
        if d2 == "7":
            return -45.0, True
        if d2 in ("8", "9"):
            return -25.0, True
        return 0.0, True

    if (
        "loop" in nh
        or bool(tokens & _LOOP_TOKENS)
        or (bool(tokens & {"dl", "ul"}) and not flags.is_ul_dl_named_line)
        or bool(re.search(r"\b(?:d&u|u&d|d/u|u/d)(?:g|p)?l\b", nh))
    ):
        return _score_loop_class(tid, d2, flags), True

    if any(w in nh for w in _GOODS_WORDS) or bool(tokens & _GOODS_TOKENS):
        if d2 == "3":
            return 55.0, True
        if d2 == "2":
            return 25.0, True
        if d2 == "5":
            return 10.0, True
        if d2 == "1":
            return -20.0, True
        return -15.0, True

    if "single" in nh or bool(tokens & {"sgl"}):
        if d2 == "4":
            return (70.0 if tid.startswith("3") else 55.0), True
        return -10.0, True

    if (
        bool(re.search(r"\b(?:platform|plat)\b", nh))
        and not bool(re.search(r"\b(?:fast|main|slow|relief|goods|freight|bay|terminal)\b", nh))
    ):
        return (
            _score_dedicated_platform_class(
                tid, d2, flags, is_rev_hint, platform_num, c_start, c_end, target_m, edge_vmax
            ),
            True,
        )

    if (
        bool(re.search(r"\b(?:slow|relief|line 2|up 2|down 2|local)\b", nh))
        or bool(tokens & {"ds", "us", "sl", "rl", "dsl", "usl", "drl", "url", "l2"})
        or (bool(tokens & {"dr", "ur"}) and not bool(re.search(r"\bfast\b|u&dr|d&ur", nh)))
    ):
        if d2 == "2":
            return 50.0, True
        if d2 == "1":
            return (0.0 if exact_line_match else -15.0), True
        if d2 in ("6", "8"):
            return -30.0, True
        if d2 in ("7", "9"):
            return -40.0, True
        return 0.0, True

    if (
        flags.is_secondary_fast
        or bool(re.search(r"\b(?:fast|main|line 1|up 1|down 1)\b", nh))
        or bool(tokens & {"dm", "um", "df", "uf", "ml", "fl", "dml", "uml", "dfl", "ufl", "l1", "dfi", "ufi"})
    ):
        if d2 == "1":
            return 50.0, True
        if d2 == "2":
            return (10.0 if (platform_num is not None or exact_line_match) else -10.0), True
        if d2 == "6":
            return -35.0, True
        if d2 == "8":
            if platform_num is not None and len(tid) == 4 and tid[2:4].isdigit() and int(tid[2:4]) == platform_num:
                return 5.0, True
            return -35.0, True
        if d2 == "7":
            return -40.0, True
        if d2 == "9":
            return -45.0, True
        return 0.0, True

    if bool(re.search(r"\b(?:platform|plat)\b", nh)):
        if d2 in ("1", "2", "4", "5", "6"):
            return 25.0, True
        if d2 == "8":
            return -5.0, True
        if d2 in ("7", "9"):
            return -25.0, True
        return 0.0, True

    return 0.0, False


def score_fallback_tiebreakers(
    tid: str,
    matched_class: bool,
    asset_type: Optional[str],
    edge_vmax: Optional[int],
) -> float:
    """Apply line-speed and running-line tiebreakers when no explicit TPS class matched.

    Args:
        tid: Candidate 4-digit NWR ``track_id``.
        matched_class: Whether ``score_line_class`` matched an explicit class.
        asset_type: TPS asset type (``'signal'``, ``'points'``, ``'node'``).
        edge_vmax: TPS edge speed limit in km/h (or ``None``).

    Returns:
        Fallback tiebreaker score delta.
    """
    if matched_class or len(tid) < 2:
        return 0.0
    delta = 0.0
    if edge_vmax and edge_vmax > 0:
        if edge_vmax >= 145 and tid[1] == "1":
            delta += 20.0
        elif edge_vmax <= 80 and tid[1] == "2":
            delta += 15.0
    if asset_type == "signal":
        if tid[1] in ("1", "2", "4"):
            delta += 10.0
        elif tid[1] == "7":
            delta -= 45.0
        elif tid[1] in ("8", "9"):
            delta -= 15.0
    return delta


def score_track_candidate(
    track_id: str,
    dir_hint: Optional[str],
    name_hint: Optional[str],
    c_start: float,
    c_end: float,
    target_m: Optional[float],
    asset_type: Optional[str] = None,
    platform_num: Optional[int] = None,
    secondary_dir: Optional[str] = None,
    edge_vmax: Optional[int] = None,
) -> float:
    """Score a candidate NWR centreline to select the single best geographic track match.

    Combines five independent scoring dimensions starting from a base of ``50.0``:
      1. Mileage coverage (``score_mileage_coverage``)
      2. Direction alignment (``score_direction_alignment``)
      3. Platform / line sequence ``NN`` (``score_platform_and_line_seq``)
      4. Appendix A line class ``C`` (``score_line_class``)
      5. Unclassified speed & running-line tiebreakers (``score_fallback_tiebreakers``)

    Args:
        track_id: NWR 4-digit ``track_id`` string.
        dir_hint: Primary TPS direction (``'up'``, ``'down'``, ``'reversible'``).
        name_hint: Combined TPS track/route description hint.
        c_start: Start chainage of the candidate centreline in metres.
        c_end: End chainage of the candidate centreline in metres.
        target_m: Target TPS asset mileage in metres (or ``None``).
        asset_type: Optional asset type (``'signal'``, ``'points'``, ``'node'``).
        platform_num: Optional 0..99 platform/siding number.
        secondary_dir: Optional physical running-line direction tiebreaker.
        edge_vmax: Optional TPS edge speed limit in km/h.

    Returns:
        Composite floating-point score (higher is better).
    """
    tid = str(track_id or "")
    flags = extract_hint_flags(name_hint)

    score = 50.0
    score += score_mileage_coverage(c_start, c_end, target_m)

    dir_delta, is_rev_hint = score_direction_alignment(tid, dir_hint, secondary_dir)
    score += dir_delta

    seq_delta, exact_line_match = score_platform_and_line_seq(
        tid, flags, dir_hint, is_rev_hint, secondary_dir, platform_num, c_start, c_end, target_m, edge_vmax
    )
    score += seq_delta

    class_delta, matched_class = score_line_class(
        tid, flags, is_rev_hint, platform_num, exact_line_match, c_start, c_end, target_m, edge_vmax
    )
    score += class_delta

    score += score_fallback_tiebreakers(tid, matched_class, asset_type, edge_vmax)
    return score

