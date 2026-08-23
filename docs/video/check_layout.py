#!/usr/bin/env python3
"""Geometry/layer contracts for the ManimGL explainer.

This is deliberately stricter than a render-smoke test. The video uses a small
visual grammar: transparent range rings live behind everything, physical links
stop outside opaque nodes, committed routes sit above ordinary links, and
semantic text/state never morphs through unrelated text.
"""

from pathlib import Path
import re
import numpy as np

from dtprotocol_explainer import (
    BLUE,
    CONTENT_BOTTOM_Y,
    Z_RANGE,
    Z_BADGE,
    Z_CAPTION,
    Z_CONNECTOR,
    Z_EDGE,
    Z_MARK,
    Z_NODE,
    Z_PACKET,
    Z_PANEL,
    Z_ROUTE,
    Z_TITLE,
    boundary_points,
    keep_above_caption,
    make_badge,
    make_caption,
    make_card,
    make_edge,
    make_node,
    make_packet,
    packet_half_extent,
    packet_path_points,
    make_radio_range,
    make_title,
    node_radius,
)
from manimlib import DOWN, RIGHT


def assert_family_layer(mob, expected):
    for member in mob.get_family():
        assert member.z_index == expected, (type(member).__name__, member.z_index, expected)


def main() -> int:
    assert Z_RANGE < Z_EDGE < Z_ROUTE < Z_NODE
    assert Z_CONNECTOR < Z_NODE
    assert Z_NODE < Z_PANEL < Z_PACKET < Z_CAPTION < Z_TITLE
    assert Z_BADGE < Z_CAPTION
    assert Z_MARK < Z_CAPTION

    a = make_node("A")
    b = make_node("B").shift(3 * RIGHT)
    edge = make_edge(a, b)
    ring = make_radio_range(a, BLUE)
    adjacent = make_node("N").shift(2.08 * RIGHT)
    teaching_ring = make_radio_range(a, BLUE, 2.25)
    assert np.linalg.norm(adjacent.get_center() - a.get_center()) < teaching_ring.get_width() / 2.0

    assert edge.z_index == Z_EDGE
    assert ring.z_index == Z_RANGE
    assert_family_layer(a, Z_NODE)
    assert_family_layer(make_card("route", ["A via B"]), Z_PANEL)
    assert_family_layer(make_badge("state"), Z_BADGE)
    assert_family_layer(make_packet("DATA"), Z_PACKET)
    assert_family_layer(make_caption("caption"), Z_CAPTION)
    assert_family_layer(make_title("title"), Z_TITLE)

    # Links must begin/end outside the protected node body; labels therefore
    # cannot be crossed even if a later animation changes draw order.
    start, end = edge.get_start(), edge.get_end()
    assert np.linalg.norm(start - a.get_center()) > node_radius(a)
    assert np.linalg.norm(end - b.get_center()) > node_radius(b)
    p0, p1 = boundary_points(a, b, 0.08)
    assert np.allclose(start, p0)
    assert np.allclose(end, p1)

    wide = make_packet("STATUS 010")
    q0, q1 = packet_path_points(wide, a, b, 0.13)
    whole_packet_clearance = packet_half_extent(wide) + 0.13
    assert np.linalg.norm(q0 - a.get_center()) >= node_radius(a) + whole_packet_clearance - 1e-6
    assert np.linalg.norm(q1 - b.get_center()) >= node_radius(b) + whole_packet_clearance - 1e-6

    low = make_card("low", ["row 1", "row 2", "row 3"], width=3.0)
    low.shift(DOWN * 5)
    keep_above_caption(low)
    assert low.get_bottom()[1] >= CONTENT_BOTTOM_Y + 0.099

    # These were the main visual failure modes in the old render: direct
    # center-to-center segments and text/glyph morphs during state changes.
    source = Path(__file__).with_name("dtprotocol_explainer.py").read_text()
    assert not re.search(r"(?:Line|DashedLine)\([^\n]*get_center\(\)", source)
    assert "ReplacementTransform(" not in source
    assert "Indicate(" not in source
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
