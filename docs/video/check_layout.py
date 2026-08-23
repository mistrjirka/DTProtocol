#!/usr/bin/env python3
"""Small geometry/layer contract for the ManimGL explainer."""

from dtprotocol_explainer import (
    BLUE,
    CONTENT_BOTTOM_Y,
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
    keep_above_caption,
    make_badge,
    make_caption,
    make_card,
    make_edge,
    make_node,
    make_packet,
    make_title,
)
from manimlib import DOWN


def main() -> int:
    assert Z_EDGE < Z_ROUTE < Z_NODE
    assert Z_CONNECTOR < Z_NODE
    assert Z_NODE < Z_PANEL < Z_PACKET < Z_CAPTION < Z_TITLE
    assert Z_BADGE < Z_CAPTION
    assert Z_MARK < Z_CAPTION

    a = make_node("A")
    b = make_node("B").shift(2 * DOWN)
    assert make_edge(a, b).z_index == Z_EDGE
    assert a.z_index == Z_NODE
    assert make_card("route", ["A via B"]).z_index == Z_PANEL
    assert make_badge("state").z_index == Z_BADGE
    assert make_packet("DATA").z_index == Z_PACKET
    assert make_caption("caption").z_index == Z_CAPTION
    assert make_title("title").z_index == Z_TITLE

    low = make_card("low", ["row 1", "row 2", "row 3"], width=3.0)
    low.shift(DOWN * 5)
    keep_above_caption(low)
    assert low.get_bottom()[1] >= CONTENT_BOTTOM_Y + 0.099
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
