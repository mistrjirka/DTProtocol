from manimlib import *
import numpy as np

"""Animated DTProtocol v4 architecture explainer.

This source targets ManimGL, the 3Blue1Brown branch of Manim.
It intentionally avoids narration-dependent pacing and large walls of text:
short captions name the invariant currently visible on screen.
"""

BG = "#0B1020"
PANEL = "#121B31"
PANEL_2 = "#17233D"
GRID = "#263755"
WHITE_SOFT = "#ECF3FF"
MUTED = "#9FB2CB"
BLUE = "#58C4DD"
BLUE_2 = "#7DD3FC"
GREEN = "#83C167"
YELLOW = "#F7C948"
ORANGE = "#FF9F43"
RED = "#FC6255"
PURPLE = "#B189E7"

FONT = "DejaVu Sans"
MONO = "DejaVu Sans Mono"

# Explicit render layers. ManimGL otherwise uses creation order when z_index is
# equal, which made later path highlights and connectors paint over node labels
# and cards. The values are deliberately sparse so scene-specific overlays can
# sit between the standard classes.
Z_RANGE = 1
Z_EDGE = 5
Z_ROUTE = 10
Z_CONNECTOR = 15
Z_NODE = 30
Z_PANEL = 40
Z_BADGE = 50
Z_PACKET = 60
Z_MARK = 70
Z_CAPTION = 90
Z_TITLE = 100

# The bottom 0.9-ish frame units are reserved for make_caption(). Content that
# descends below this is visually competing with the caption even with correct
# z-order.
CONTENT_BOTTOM_Y = -2.65


def set_layer(mob, z_index):
    """Assign one semantic render layer to a mobject and every submobject."""
    try:
        family = mob.get_family()
    except Exception:
        family = [mob]
    for member in family:
        member.set_z_index(z_index)
    return mob


def text_mob(text, size=34, color=WHITE_SOFT, bold=False, font=FONT):
    weight = "BOLD" if bold else "NORMAL"
    return Text(text, font=font, font_size=size, color=color, weight=weight)


def fit_width(mob, width):
    if mob.get_width() > width:
        mob.set_width(width)
    return mob


def keep_above_caption(mob, margin=0.10):
    """Move content up if it enters the persistent bottom caption band."""
    minimum = CONTENT_BOTTOM_Y + margin
    bottom = mob.get_bottom()[1]
    if bottom < minimum:
        mob.shift(UP * (minimum - bottom))
    return mob


def make_title(text, accent=BLUE):
    label = text_mob(text, 34, WHITE_SOFT, True)
    fit_width(label, 12.1)
    label.to_edge(UP, buff=0.28)
    line = Line(LEFT * 6.35, RIGHT * 6.35, color=GRID, stroke_width=2)
    line.next_to(label, DOWN, buff=0.18)
    accent_line = Line(line.get_left(), line.get_left() + RIGHT * 1.2, color=accent, stroke_width=5)
    return set_layer(VGroup(label, line, accent_line), Z_TITLE)


def make_caption(text, accent=BLUE):
    label = text_mob(text, 26, WHITE_SOFT)
    fit_width(label, 11.8)
    box = RoundedRectangle(
        width=max(5.0, label.get_width() + 0.55),
        height=0.62,
        corner_radius=0.12,
        stroke_color=accent,
        stroke_width=1.5,
        fill_color=PANEL,
        fill_opacity=1.0,
    )
    label.move_to(box)
    group = VGroup(box, label)
    group.to_edge(DOWN, buff=0.25)
    return set_layer(group, Z_CAPTION)


def make_badge(text, color=BLUE, size=24):
    label = text_mob(text, size, WHITE_SOFT, True)
    box = RoundedRectangle(
        width=label.get_width() + 0.38,
        height=label.get_height() + 0.22,
        corner_radius=0.12,
        stroke_color=color,
        stroke_width=2,
        fill_color=PANEL_2,
        fill_opacity=1.0,
    )
    label.move_to(box)
    return set_layer(VGroup(box, label), Z_BADGE)


def make_node(name, color=BLUE, radius=0.34):
    # Semantic nodes are opaque. Radio range is a separate, intentionally
    # translucent object so edges behind a node can never bleed through text.
    body = Circle(radius=radius).set_fill(PANEL_2, opacity=1.0).set_stroke(color, width=4)
    inner = Circle(radius=radius * 0.78).set_fill(color, opacity=0.10).set_stroke(width=0)
    label = text_mob(name, 27, WHITE_SOFT, True)
    label.move_to(body)
    return set_layer(VGroup(body, inner, label), Z_NODE)


def node_radius(node):
    return node[0].get_width() / 2.0


def boundary_points(a, b, clearance=0.08):
    """Clip a segment to the outside of two circular node bodies."""
    pa = np.array(a.get_center())
    pb = np.array(b.get_center())
    delta = pb - pa
    distance = np.linalg.norm(delta)
    if distance < 1e-8:
        return pa, pb
    unit = delta / distance
    ra = node_radius(a) + clearance
    rb = node_radius(b) + clearance
    if ra + rb >= distance:
        midpoint = (pa + pb) / 2.0
        return midpoint, midpoint
    return pa + unit * ra, pb - unit * rb


def make_radio_range(node, color=BLUE, radius=2.25):
    ring = Circle(radius=radius)
    ring.set_fill(opacity=0)
    ring.set_stroke(color=color, width=2, opacity=0.22)
    ring.move_to(node.get_center())
    return set_layer(ring, Z_RANGE)


def make_edge(a, b, color=GRID, width=5, dashed=False, layer=Z_EDGE, clearance=0.08):
    cls = DashedLine if dashed else Line
    start, end = boundary_points(a, b, clearance)
    return set_layer(cls(start, end, color=color, stroke_width=width), layer)


def make_path(nodes, color=GREEN, width=8, dashed=False, layer=Z_ROUTE, clearance=0.08):
    return set_layer(VGroup(*[
        make_edge(nodes[i], nodes[i + 1], color, width, dashed, layer, clearance)
        for i in range(len(nodes) - 1)
    ]), layer)


def make_packet(label, color=BLUE, size=22):
    text = text_mob(label, size, WHITE_SOFT, True)
    box = RoundedRectangle(
        width=max(0.82, text.get_width() + 0.34),
        height=max(0.38, text.get_height() + 0.17),
        corner_radius=0.1,
        stroke_color=color,
        stroke_width=2,
        fill_color=PANEL_2,
        fill_opacity=1.0,
    )
    text.move_to(box)
    return set_layer(VGroup(box, text), Z_PACKET)

def packet_half_extent(packet):
    """Conservative radius used to keep the whole packet box outside nodes."""
    return 0.5 * max(packet.get_width(), packet.get_height())


def packet_path_points(packet, start, end, clearance=0.13):
    return boundary_points(start, end, clearance + packet_half_extent(packet))


def make_card(title, rows, width=3.4, accent=BLUE, row_size=23):
    title_text = text_mob(title, 27, WHITE_SOFT, True)
    header = RoundedRectangle(
        width=width,
        height=0.55,
        corner_radius=0.12,
        stroke_color=accent,
        stroke_width=2,
        fill_color=PANEL_2,
        fill_opacity=1.0,
    )
    title_text.move_to(header)
    row_mobs = VGroup(*[text_mob(row, row_size, WHITE_SOFT, font=MONO) for row in rows])
    row_mobs.arrange(DOWN, aligned_edge=LEFT, buff=0.13)
    body_height = max(0.9, row_mobs.get_height() + 0.36)
    body = RoundedRectangle(
        width=width,
        height=body_height,
        corner_radius=0.12,
        stroke_color=GRID,
        stroke_width=1.5,
        fill_color=PANEL,
        fill_opacity=1.0,
    )
    row_mobs.move_to(body)
    content = VGroup(body, row_mobs)
    return set_layer(VGroup(VGroup(header, title_text), content).arrange(DOWN, buff=0.08), Z_PANEL)


def make_layer(name, subtitle, color, width=4.2):
    box = RoundedRectangle(
        width=width,
        height=0.82,
        corner_radius=0.13,
        stroke_color=color,
        stroke_width=2.5,
        fill_color=PANEL,
        fill_opacity=1.0,
    )
    title = text_mob(name, 27, WHITE_SOFT, True)
    sub = text_mob(subtitle, 19, MUTED)
    title.move_to(box.get_center() + UP * 0.14)
    sub.move_to(box.get_center() + DOWN * 0.19)
    return set_layer(VGroup(box, title, sub), Z_PANEL)


def make_metric(number, label, color=GREEN, width=2.55):
    num = text_mob(number, 44, color, True)
    lab = text_mob(label, 21, WHITE_SOFT)
    fit_width(lab, width - 0.3)
    group = VGroup(num, lab).arrange(DOWN, buff=0.13)
    box = RoundedRectangle(
        width=width,
        height=1.5,
        corner_radius=0.16,
        stroke_color=color,
        stroke_width=2,
        fill_color=PANEL,
        fill_opacity=1.0,
    )
    group.move_to(box)
    return set_layer(VGroup(box, group), Z_PANEL)


class DTScene(Scene):
    def prepare(self, title=None, accent=BLUE):
        self.camera.background_color = BG
        if title:
            title_group = make_title(title, accent)
            self.add(title_group)
            return title_group
        return None

    def show_caption(self, text, accent=BLUE, old=None, run_time=0.45):
        new = make_caption(text, accent)
        if old is None:
            self.play(FadeIn(new, shift=UP * 0.08), run_time=run_time)
        else:
            self.play(FadeOut(old, shift=DOWN * 0.03), run_time=run_time * 0.38)
            self.play(FadeIn(new, shift=UP * 0.05), run_time=run_time * 0.62)
        return new

    def clean_replace(self, old, new, run_time=0.5):
        """Replace semantic text/state without glyph morphing or transient overlap."""
        self.play(FadeOut(old), run_time=run_time * 0.36)
        self.play(FadeIn(new), run_time=run_time * 0.64)
        return new

    def outline_pulse(self, mob, color=GREEN, run_time=0.5, buff=0.08):
        outline = SurroundingRectangle(mob, color=color, buff=buff).set_z_index(Z_MARK)
        self.play(ShowCreation(outline), run_time=run_time * 0.45)
        self.play(FadeOut(outline), run_time=run_time * 0.55)

    def travel(self, packet, start, end, run_time=0.58, arc=0.0, clearance=0.13):
        p0, p1 = packet_path_points(packet, start, end, clearance)
        packet.move_to(p0)
        if abs(arc) < 1e-6:
            path = Line(p0, p1)
        else:
            path = ArcBetweenPoints(p0, p1, angle=arc)
        self.add(packet)
        self.play(MoveAlongPath(packet, path), run_time=run_time, rate_func=linear)
        self.remove(packet)


    def fade_scene(self, run_time=0.55):
        mobs = list(self.mobjects)
        if mobs:
            self.play(*[FadeOut(mob) for mob in mobs], run_time=run_time)


class Opening(DTScene):
    def construct(self):
        self.prepare()
        title = text_mob("Can the mesh send before it has settled?", 58, WHITE_SOFT, True)
        subtitle = text_mob("A already trusts a route to C. Knowledge of D is still crystallizing.", 29, BLUE_2)
        fit_width(subtitle, 11.5)
        VGroup(title, subtitle).arrange(DOWN, buff=0.22).move_to(UP * 2.35)

        a, b, c, d = [make_node(name, GREEN if name in "AC" else BLUE) for name in "ABCD"]
        nodes = VGroup(a, b, c, d).arrange(RIGHT, buff=1.55).move_to(UP * 0.45)
        known = make_path([a, b, c], GREEN, 8)
        tail = make_edge(c, d, BLUE, 4)

        committed = make_badge("A: C via B is committed", GREEN, 20).move_to(LEFT * 3.0 + DOWN * 1.55)
        pending = make_badge("D info is still pending at A", PURPLE, 20).move_to(RIGHT * 3.2 + DOWN * 1.55)
        cryst = make_packet("CRYST 1/2", PURPLE, 18).move_to((a.get_center() + b.get_center()) / 2 + DOWN * 0.70)

        self.play(FadeIn(title, shift=UP * 0.12), FadeIn(subtitle, shift=UP * 0.08), run_time=0.8)
        self.play(LaggedStart(*[FadeIn(n, scale=0.85) for n in nodes], lag_ratio=0.08), run_time=0.65)
        self.play(ShowCreation(tail), ShowCreation(known), FadeIn(committed), FadeIn(pending), FadeIn(cryst, scale=0.85), run_time=0.8)
        caption = self.show_caption("Does A have to wait for unrelated state, or can it use the route it already trusts?", YELLOW)

        question = make_packet("DATA ?", YELLOW, 22).next_to(a, RIGHT, buff=0.18)
        self.play(FadeIn(question, scale=0.85), run_time=0.3)
        p0, p1 = boundary_points(a, b, 0.16 + packet_half_extent(question))
        self.play(question.animate.move_to((p0 + p1) / 2), run_time=0.6)
        self.outline_pulse(committed, GREEN, 0.34)
        self.outline_pulse(cryst, PURPLE, 0.34, buff=0.06)
        self.wait(0.6)
        self.fade_scene()


class Architecture(DTScene):
    def construct(self):
        self.prepare("One protocol, five cooperating state machines", BLUE)

        app = make_layer("Application / Bluetooth", "payloads and callbacks", GREEN, 4.5)
        data = make_layer("Data plane", "identity, replay, hop limit, E2E ACK/NACK", BLUE, 4.5)
        sync = make_layer("Neighbour sync", "HELLO, CRYST_REQ, CRYST chunks", PURPLE, 4.5)
        repair = make_layer("Feasibility repair", "SEQ_REQ, backoff, flood escape", YELLOW, 4.5)
        sched = make_layer("TX scheduler", "responses, bounded repair, normal work", ORANGE, 4.5)
        db = make_layer("CrystDatabase", "candidates, feasibility, selected routes", BLUE_2, 4.5)
        lcmm = make_layer("LCMM", "one-hop ACK and bounded retry", GREEN, 4.1)
        mac = make_layer("MAC + RadioLib", "CCA, half-duplex radio, airtime", PURPLE, 4.1)

        app.move_to(LEFT * 3.7 + UP * 2.25)
        data.move_to(LEFT * 3.7 + UP * 1.05)
        sync.move_to(LEFT * 3.7 + DOWN * 0.15)
        repair.move_to(LEFT * 3.7 + DOWN * 1.35)
        sched.move_to(RIGHT * 1.45 + DOWN * 0.1)
        db.move_to(RIGHT * 1.45 + UP * 1.25)
        lcmm.move_to(RIGHT * 4.65 + DOWN * 1.35)
        mac.move_to(RIGHT * 4.65 + DOWN * 2.55)

        layers = VGroup(app, data, sync, repair, sched, db, lcmm, mac)
        self.play(LaggedStart(*[FadeIn(layer, shift=RIGHT * 0.1) for layer in layers], lag_ratio=0.08), run_time=1.5)

        arrows = VGroup(
            Arrow(app.get_bottom(), data.get_top(), buff=0.08, color=GREEN),
            Arrow(data.get_right(), db.get_left(), buff=0.1, color=BLUE),
            Arrow(sync.get_right(), db.get_left() + DOWN * 0.25, buff=0.1, color=PURPLE),
            Arrow(db.get_bottom(), repair.get_right(), buff=0.1, color=YELLOW),
            Arrow(data.get_right() + DOWN * 0.25, sched.get_left() + UP * 0.1, buff=0.1, color=BLUE),
            Arrow(sync.get_right() + DOWN * 0.1, sched.get_left() + DOWN * 0.15, buff=0.1, color=PURPLE),
            Arrow(repair.get_right(), sched.get_left() + DOWN * 0.3, buff=0.1, color=YELLOW),
            Arrow(sched.get_bottom(), lcmm.get_left(), buff=0.1, color=ORANGE),
            Arrow(lcmm.get_bottom(), mac.get_top(), buff=0.08, color=GREEN),
        ).set_z_index(Z_CONNECTOR)
        self.play(LaggedStart(*[GrowArrow(a) for a in arrows], lag_ratio=0.08), run_time=1.4)

        caption = self.show_caption("Each layer has a different definition of success.", BLUE)
        metrics = VGroup(
            make_metric("frame", "MAC completed one radio frame", PURPLE, 3.2),
            make_metric("hop", "LCMM received a link ACK", GREEN, 3.2),
            make_metric("message", "DTPK received final acceptance", BLUE, 3.2),
        ).arrange(RIGHT, buff=0.3).scale(0.78).move_to(DOWN * 2.30 + LEFT * 1.8)
        keep_above_caption(metrics)
        self.play(LaggedStart(*[FadeIn(m, shift=UP * 0.12) for m in metrics], lag_ratio=0.15), run_time=1.0)
        self.wait(1.7)
        self.fade_scene()


class Crystallization(DTScene):
    def construct(self):
        self.prepare("Crystallization grows trusted knowledge step by step", PURPLE)

        a, b, c, d = [make_node(name, BLUE) for name in "ABCD"]
        nodes = VGroup(a, b, c, d).arrange(RIGHT, buff=1.40).move_to(UP * 0.40)

        caption = self.show_caption("The radios are physically nearby, but the routing layer has learned no neighbours yet.", MUTED)
        self.play(LaggedStart(*[FadeIn(n, scale=0.85) for n in nodes], lag_ratio=0.12), run_time=0.85)
        self.wait(0.35)

        # Show physical reach only while it matters. B is genuinely inside A's
        # ring at this standard spacing; learned links remain a separate visual.
        range_a = make_radio_range(a, BLUE, 2.25)
        caption = self.show_caption("A's radio can reach B. Physical reach is not yet a learned route.", BLUE, caption)
        self.play(ShowCreation(range_a), run_time=0.55)
        self.wait(0.25)
        self.travel(make_packet("HELLO", GREEN, 18), a, b, 0.65)
        self.travel(make_packet("CRYST_REQ", PURPLE, 17), b, a, 0.55)
        ab = make_edge(a, b, BLUE, 5, layer=Z_EDGE)
        ab_badge = make_badge("A ↔ B direct", BLUE, 18).next_to(ab, DOWN, buff=0.24)
        self.play(FadeOut(range_a), ShowCreation(ab), FadeIn(ab_badge, shift=UP * 0.06), run_time=0.55)

        caption = self.show_caption("Now repeat the same discovery locally: B finds C, then C finds D.", BLUE, caption)
        range_b = make_radio_range(b, BLUE, 2.25)
        self.play(ShowCreation(range_b), run_time=0.35)
        self.travel(make_packet("HELLO", BLUE, 17), b, c, 0.50)
        bc = make_edge(b, c, BLUE, 5)
        self.play(FadeOut(range_b), ShowCreation(bc), run_time=0.38)

        range_c = make_radio_range(c, BLUE, 2.25)
        self.play(ShowCreation(range_c), run_time=0.35)
        self.travel(make_packet("HELLO", BLUE, 17), c, d, 0.50)
        cd = make_edge(c, d, BLUE, 5)
        self.play(FadeOut(range_c), ShowCreation(cd), FadeOut(ab_badge), run_time=0.38)

        a_state = make_card("A's committed view", ["B direct", "C unknown", "D unknown"], 3.3, GREEN, 21)
        a_state.move_to(LEFT * 3.8 + DOWN * 1.35)
        pending = make_card("snapshot from B", ["chunk 0 / 2", "pending"], 3.0, PURPLE, 21)
        pending.move_to(RIGHT * 0.25 + DOWN * 1.35)
        keep_above_caption(a_state)
        keep_above_caption(pending)
        self.play(FadeIn(a_state), FadeIn(pending), run_time=0.65)

        caption = self.show_caption("Indirect knowledge does not appear when the first CRYST chunk arrives.", PURPLE, caption)
        self.travel(make_packet("CRYST 0/2", PURPLE, 17), b, a, 0.65)
        one = make_badge("1 / 2 received", PURPLE, 18).next_to(pending, UP, buff=0.16)
        self.play(FadeIn(one), run_time=0.28)
        self.outline_pulse(a_state, GREEN, 0.34)
        self.wait(0.35)

        caption = self.show_caption("Only a complete snapshot replaces the previous committed contribution.", GREEN, caption)
        self.travel(make_packet("CRYST 1/2", PURPLE, 17), b, a, 0.65)
        new_state = make_card("A's committed view", ["B direct", "C via B", "D unknown"], 3.3, GREEN, 21).move_to(a_state)
        complete = make_badge("2 / 2 → commit", GREEN, 18).next_to(pending, UP, buff=0.16)
        route_to_c = make_path([a, b, c], GREEN, 8)
        self.play(FadeOut(one), FadeOut(a_state), FadeOut(pending), run_time=0.25)
        self.play(FadeIn(complete), FadeIn(new_state), ShowCreation(route_to_c), run_time=0.60)

        next_pending = make_badge("D is still propagating farther through the mesh", PURPLE, 18).move_to(RIGHT * 3.0 + DOWN * 1.65)
        self.play(FadeIn(next_pending, shift=UP * 0.08), run_time=0.45)
        caption = self.show_caption("Crystallization expands usable knowledge locally; there is no global ready switch.", GREEN, caption)
        self.wait(1.05)
        self.fade_scene()


class Feasibility(DTScene):
    def construct(self):
        self.prepare("Feasibility rejects stale same-generation detours", YELLOW)

        a = make_node("A", GREEN).move_to(LEFT * 3.6 + UP * 1.15)
        b = make_node("B", BLUE).move_to(LEFT * 0.6 + UP * 1.15)
        d = make_node("D", GREEN).move_to(RIGHT * 4.1 + UP * 1.15)
        ab = make_edge(a, b, GRID, 5)
        claimed = make_edge(b, d, GRID, 4, dashed=True)
        self.play(FadeIn(a), FadeIn(b), FadeIn(d), ShowCreation(ab), ShowCreation(claimed), run_time=0.85)

        state = make_card("A remembers for D", ["generation 12", "feasible distance 2"], 3.55, YELLOW, 22)
        report = make_card("B reports", ["generation 12", "neighbour metric 2"], 3.35, BLUE, 22)
        state.move_to(LEFT * 3.5 + DOWN * 1.45)
        report.move_to(RIGHT * 3.3 + DOWN * 1.45)
        keep_above_caption(state)
        keep_above_caption(report)
        self.play(FadeIn(state), FadeIn(report), run_time=0.65)

        caption = self.show_caption("For the same generation, the neighbour metric must be strictly better than the feasible distance.", YELLOW)
        comparison = text_mob("2 < 2   →   false", 34, RED, True, MONO).move_to(DOWN * 0.35)
        blocked = make_edge(a, b, RED, 7, dashed=True, layer=Z_ROUTE)
        self.play(FadeIn(comparison, scale=0.88), ShowCreation(blocked), run_time=0.6)
        self.wait(0.55)

        caption = self.show_caption("A newer destination generation is fresh; metric selection happens after feasibility.", GREEN, caption)
        fresh_report = make_card("B reports", ["generation 13", "neighbour metric 3", "fresh → feasible"], 3.55, GREEN, 21).move_to(report)
        selected = make_badge("A selects B as next hop", GREEN, 20).next_to(ab, UP, buff=0.30)
        selected_link = make_edge(a, b, GREEN, 8, layer=Z_ROUTE)
        self.play(FadeOut(report), FadeOut(comparison), FadeOut(blocked), run_time=0.28)
        self.play(FadeIn(fresh_report), ShowCreation(selected_link), FadeIn(selected), run_time=0.52)
        self.wait(1.15)
        self.fade_scene()


class EarlyData(DTScene):
    def construct(self):
        self.prepare("A committed route can carry DATA while other state is pending", GREEN)

        a, b, c, d = [make_node(name, GREEN if name in "AC" else BLUE) for name in "ABCD"]
        nodes = VGroup(a, b, c, d).arrange(RIGHT, buff=1.55).move_to(UP * 1.05)
        direct = VGroup(make_edge(a, b), make_edge(b, c), make_edge(c, d))
        committed = make_path([a, b, c], GREEN, 8)
        self.play(FadeIn(nodes), ShowCreation(direct), ShowCreation(committed), run_time=0.9)

        known = make_badge("A knows C via B · D still unknown", GREEN, 20).move_to(LEFT * 3.2 + DOWN * 1.35)
        pending = make_badge("C's newer snapshot is pending at B", PURPLE, 20).move_to(RIGHT * 3.2 + DOWN * 1.35)
        self.play(FadeIn(known), FadeIn(pending), run_time=0.55)

        caption = self.show_caption("A does not wait for the whole network. It sends along the route it already trusts.", GREEN)
        self.travel(make_packet("DATA", GREEN), a, b, 0.68)
        hop = make_badge("forward", GREEN, 17).next_to(b, UP, buff=0.28)
        self.play(FadeIn(hop, scale=0.85), run_time=0.22)
        self.travel(make_packet("DATA", GREEN), b, c, 0.68)
        self.play(FadeOut(hop), run_time=0.18)
        delivered = make_badge("delivered", GREEN, 19).next_to(c, UP, buff=0.30)
        self.play(FadeIn(delivered, scale=0.88), run_time=0.35)

        caption = self.show_caption("Control work can advance between message hops instead of being globally paused.", PURPLE, caption)
        self.travel(make_packet("CRYST 1/2", PURPLE, 17), c, b, 0.55)
        pending2 = make_badge("C snapshot: 1 / 2 received", PURPLE, 19).move_to(pending)
        self.clean_replace(pending, pending2, 0.35)

        breadcrumb = VGroup(
            make_edge(c, b, YELLOW, 4, dashed=True, layer=Z_CONNECTOR, clearance=0.13),
            make_edge(b, a, YELLOW, 4, dashed=True, layer=Z_CONNECTOR, clearance=0.13),
        )
        crumb_label = make_badge("reverse breadcrumb", YELLOW, 18).move_to(b.get_center() + DOWN * 0.78)
        self.play(ShowCreation(breadcrumb), FadeIn(crumb_label), run_time=0.55)
        caption = self.show_caption("The end-to-end ACK follows the remembered reverse path, one hop at a time.", YELLOW, caption)
        self.travel(make_packet("E2E ACK", YELLOW, 17), c, b, 0.58)
        self.travel(make_packet("E2E ACK", YELLOW, 17), b, a, 0.58)

        caption = self.show_caption("Afterward, crystallization simply continues from where it was.", BLUE, caption)
        self.travel(make_packet("CRYST 2/2", PURPLE, 17), c, b, 0.55)
        committed2 = make_badge("C snapshot committed at B", GREEN, 19).move_to(pending2)
        self.clean_replace(pending2, committed2, 0.4)
        self.wait(1.0)
        self.fade_scene()


class Reliability(DTScene):
    def construct(self):
        self.prepare("Per-hop reliability and end-to-end completion", BLUE)

        s = make_node("S", GREEN).move_to(LEFT * 4.6 + UP * 1.25)
        r = make_node("R", BLUE).move_to(ORIGIN + UP * 1.25)
        d = make_node("D", GREEN).move_to(RIGHT * 4.6 + UP * 1.25)
        edges = VGroup(make_edge(s, r), make_edge(r, d))
        self.play(ShowCreation(edges), FadeIn(s), FadeIn(r), FadeIn(d), run_time=0.9)

        identity = make_card("message identity", ["source S", "boot 42", "packet 7"], 2.9, BLUE, 22)
        identity.move_to(DOWN * 1.4)
        app_count = make_metric("1", "destination app callbacks", GREEN, 3.0).move_to(RIGHT * 4.5 + DOWN * 1.45)
        self.play(FadeIn(identity), FadeIn(app_count), run_time=0.65)

        caption = self.show_caption("LCMM confirms each next hop; DTPK confirms the complete logical message.", BLUE)
        self.travel(make_packet("DATA 7", GREEN), s, r, 0.65)
        self.travel(make_packet("link ACK", YELLOW, 17), r, s, 0.40)
        self.travel(make_packet("DATA 7", GREEN), r, d, 0.65)
        self.travel(make_packet("link ACK", YELLOW, 17), d, r, 0.40)
        self.travel(make_packet("E2E ACK 7", PURPLE, 18), d, r, 0.58)
        lost = make_packet("E2E ACK 7", PURPLE, 18).move_to((r.get_center() + s.get_center()) / 2)
        self.play(FadeIn(lost, scale=0.8), run_time=0.25)
        cross = Cross(lost, stroke_color=RED, stroke_width=6).set_z_index(Z_MARK)
        self.play(ShowCreation(cross), FadeOut(lost), run_time=0.35)

        caption = self.show_caption("If the final ACK is lost, the source retries with the same identity.", ORANGE, caption)
        self.travel(make_packet("retry DATA 7", ORANGE, 18), s, r, 0.65)
        self.travel(make_packet("retry DATA 7", ORANGE, 18), r, d, 0.65)
        replay = make_badge("replay hit: ACK again, do not deliver again", YELLOW, 20).next_to(d, DOWN, buff=0.32)
        self.play(FadeIn(replay, shift=UP * 0.08), run_time=0.35)
        self.outline_pulse(app_count, GREEN, 0.30)
        self.travel(make_packet("E2E ACK 7", PURPLE, 18), d, r, 0.58)
        self.travel(make_packet("E2E ACK 7", PURPLE, 18), r, s, 0.58)

        caption = self.show_caption("Exactly-once delivery is a replay rule, not an assumption that radio frames arrive once.", GREEN, caption)
        self.wait(1.3)
        self.fade_scene()


class MultipartCompression(DTScene):
    def construct(self):
        self.prepare("Large messages: save airtime first, then repair selectively", ORANGE)

        raw = RoundedRectangle(width=5.5, height=0.70, corner_radius=0.12, stroke_color=BLUE, fill_color=PANEL_2, fill_opacity=1.0)
        raw_label = text_mob("5,200 B measured payload", 24, WHITE_SOFT, True).move_to(raw)
        raw_group = set_layer(VGroup(raw, raw_label), Z_PANEL).move_to(UP * 1.45)
        raw_frames = make_badge("23 raw fragments", BLUE, 19).next_to(raw_group, RIGHT, buff=0.24)
        self.play(FadeIn(raw_group), FadeIn(raw_frames), run_time=0.6)

        caption = self.show_caption("Compression is used only when the complete LoRa airtime estimate says it is worth it.", ORANGE)
        encoded = RoundedRectangle(width=2.15, height=0.70, corner_radius=0.12, stroke_color=GREEN, fill_color=PANEL_2, fill_opacity=1.0)
        encoded_label = text_mob("590 B", 24, WHITE_SOFT, True).move_to(encoded)
        encoded_group = set_layer(VGroup(encoded, encoded_label), Z_PANEL).move_to(LEFT * 1.5 + UP * 1.45)
        result = make_badge("3 fragments · ~37.8 s modeled airtime saved", GREEN, 19).next_to(encoded_group, RIGHT, buff=0.25)
        self.play(FadeOut(raw_group), FadeOut(raw_frames), run_time=0.26)
        self.play(FadeIn(encoded_group), FadeIn(result), run_time=0.54)
        fallback = make_badge("No worthwhile saving? Send the original bytes.", MUTED, 18).move_to(DOWN * 0.15)
        self.play(FadeIn(fallback), run_time=0.4)
        self.wait(0.55)
        self.play(FadeOut(encoded_group), FadeOut(result), FadeOut(fallback), run_time=0.45)

        caption = self.show_caption("The three fragments now travel over the same hop-by-hop transport.", BLUE, caption)
        s_node = make_node("S", GREEN).move_to(LEFT * 4.6 + UP * 1.35)
        relay = make_node("R", BLUE).move_to(ORIGIN + UP * 1.35)
        d_node = make_node("D", GREEN).move_to(RIGHT * 4.6 + UP * 1.35)
        links = VGroup(make_edge(s_node, relay), make_edge(relay, d_node))
        self.play(FadeIn(s_node), FadeIn(relay), FadeIn(d_node), ShowCreation(links), run_time=0.7)

        assembly = make_card("destination assembly", ["0  —", "1  —", "2  —"], 2.9, PURPLE, 21)
        assembly.move_to(RIGHT * 4.2 + DOWN * 1.45)
        self.play(FadeIn(assembly), run_time=0.4)

        self.travel(make_packet("frag 0", BLUE, 17), s_node, relay, 0.48)
        self.travel(make_packet("frag 0", BLUE, 17), relay, d_node, 0.48)
        a1 = make_card("destination assembly", ["0  ✓", "1  —", "2  —"], 2.9, PURPLE, 21).move_to(assembly)
        self.clean_replace(assembly, a1, 0.28)

        self.travel(make_packet("frag 1", BLUE, 17), s_node, relay, 0.48)
        lost = make_packet("frag 1", BLUE, 17)
        p0, p1 = boundary_points(relay, d_node, 0.13)
        lost.move_to((p0 + p1) / 2)
        self.play(FadeIn(lost), run_time=0.18)
        loss_mark = Cross(lost, stroke_color=RED, stroke_width=6).set_z_index(Z_MARK)
        self.play(ShowCreation(loss_mark), FadeOut(lost), run_time=0.35)

        self.travel(make_packet("frag 2", BLUE, 17), s_node, relay, 0.48)
        self.travel(make_packet("frag 2", BLUE, 17), relay, d_node, 0.48)
        a2 = make_card("destination assembly", ["0  ✓", "1  missing", "2  ✓"], 3.1, PURPLE, 21).move_to(a1)
        self.clean_replace(a1, a2, 0.3)
        self.play(FadeOut(loss_mark), run_time=0.20)

        caption = self.show_caption("Assume link retries for fragment 1 were exhausted: the receiver asks only for what is missing.", YELLOW, caption)
        self.travel(make_packet("STATUS 010", YELLOW, 16), d_node, relay, 0.48)
        self.travel(make_packet("STATUS 010", YELLOW, 16), relay, s_node, 0.48)
        self.travel(make_packet("frag 1 only", YELLOW, 16), s_node, relay, 0.48)
        self.travel(make_packet("frag 1 only", YELLOW, 16), relay, d_node, 0.48)
        caption = self.show_caption("Only after the complete logical message exists does the destination deliver it and acknowledge completion.", GREEN, caption)
        complete = make_card("destination assembly", ["0  ✓", "1  ✓", "2  ✓", "deliver once"], 3.1, GREEN, 21).move_to(a2)
        self.clean_replace(a2, complete, 0.45)
        self.travel(make_packet("message ACK", GREEN, 16), d_node, relay, 0.48)
        self.travel(make_packet("message ACK", GREEN, 16), relay, s_node, 0.48)
        self.wait(1.0)
        self.fade_scene()


class Scheduler(DTScene):
    def construct(self):
        self.prepare("The scheduler protects control progress without starving DATA", ORANGE)

        labels = [
            ("responses", "ACK · NACK · fragment status", GREEN),
            ("repair", "CRYST_REQ · SEQ_REQ · query", YELLOW),
            ("normal", "DATA · HELLO · CRYST", BLUE),
        ]
        lanes = VGroup()
        for i, (name, detail, color) in enumerate(labels):
            box = RoundedRectangle(width=10.5, height=1.0, corner_radius=0.12, stroke_color=color, fill_color=PANEL, fill_opacity=1.0)
            name_mob = text_mob(name, 28, color, True).move_to(box.get_left() + RIGHT * 1.0)
            detail_mob = text_mob(detail, 23, WHITE_SOFT).move_to(box.get_center() + LEFT * 0.5)
            gate = make_badge("eligible", color, 18).move_to(box.get_right() + LEFT * 0.75)
            lane = VGroup(box, name_mob, detail_mob, gate)
            lane.move_to(UP * (1.35 - i * 1.25))
            lanes.add(lane)
        self.play(LaggedStart(*[FadeIn(lane, shift=RIGHT * 0.1) for lane in lanes], lag_ratio=0.12), run_time=1.0)

        caption = self.show_caption("Responses win first. Repair gets a bounded burst. Normal traffic must still run.", ORANGE)
        tokens = [
            make_packet("ACK", GREEN, 18).move_to(lanes[0].get_left() + RIGHT * 3.3),
            make_packet("SEQ_REQ", YELLOW, 18).move_to(lanes[1].get_left() + RIGHT * 3.3),
            make_packet("DATA", BLUE, 18).move_to(lanes[2].get_left() + RIGHT * 3.3),
        ]
        self.play(*[FadeIn(t, scale=0.8) for t in tokens], run_time=0.4)
        for token in tokens:
            self.play(token.animate.shift(RIGHT * 5.4), run_time=0.65)
            self.play(FadeOut(token), run_time=0.2)

        wait = make_card("one local E2E waiter", ["blocks a second local app send", "does not stop relay/control work"], 5.2, PURPLE, 23)
        wait.move_to(DOWN * 2.05)
        keep_above_caption(wait)
        bypass = VGroup(
            make_badge("HELLO", GREEN, 18),
            make_badge("CRYST", BLUE, 18),
            make_badge("relayed DATA", ORANGE, 18),
            make_badge("ACK / NACK", PURPLE, 18),
        ).arrange(RIGHT, buff=0.22).next_to(wait, UP, buff=0.28)
        self.play(FadeIn(wait), LaggedStart(*[FadeIn(b, shift=UP * 0.08) for b in bypass], lag_ratio=0.1), run_time=0.8)
        caption = self.show_caption("Waiting for an application ACK does not pause crystallization or turn relays into stop-and-wait nodes.", PURPLE, caption)
        self.wait(1.4)
        self.fade_scene()


class Repair(DTScene):
    def construct(self):
        self.prepare("Repair after a cut: persist at the origin, avoid a retry storm", YELLOW)

        a = make_node("A", GREEN).move_to(LEFT * 5.2 + UP * 1.35)
        b = make_node("B", BLUE).move_to(LEFT * 2.6 + UP * 1.75)
        c = make_node("C", BLUE).move_to(RIGHT * 0.0 + UP * 1.35)
        e = make_node("E", BLUE).move_to(RIGHT * 2.8 + UP * 1.75)
        d = make_node("D", GREEN).move_to(RIGHT * 5.35 + UP * 1.35)
        x = make_node("X", BLUE).move_to(LEFT * 0.2 + DOWN * 0.75)
        nodes = VGroup(a, b, c, e, d, x)
        links = VGroup(
            make_edge(a, b), make_edge(b, c), make_edge(c, e), make_edge(e, d),
            make_edge(a, x), make_edge(x, e),
        )
        self.play(FadeIn(nodes), ShowCreation(links), run_time=0.9)

        old_path = make_path([a, b, c, e, d], BLUE_2, 8)
        self.play(ShowCreation(old_path), run_time=0.65)
        caption = self.show_caption("A currently reaches D through A → B → C → E → D.", BLUE)

        cut = Cross(links[2], stroke_color=RED, stroke_width=8).set_z_index(Z_MARK)
        self.play(ShowCreation(cut), FadeOut(old_path), run_time=0.55)
        caption = self.show_caption("The C–E link disappears. The old path can no longer reach D.", RED, caption)

        caption = self.show_caption("A directed repair wave advances hop by hop until it reaches the break.", YELLOW, caption)
        self.travel(make_packet("SEQ_REQ", YELLOW, 17), a, b, 0.46)
        self.travel(make_packet("SEQ_REQ", YELLOW, 17), b, c, 0.46)
        stopped = make_badge("stops at the cut", RED, 18).next_to(c, DOWN, buff=0.35)
        self.play(FadeIn(stopped), run_time=0.35)

        retries = VGroup(
            make_badge("5 s", YELLOW, 17), make_badge("10 s", YELLOW, 17),
            make_badge("20 s", YELLOW, 17), make_badge("4th: flood", ORANGE, 17),
        ).arrange(RIGHT, buff=0.18).move_to(DOWN * 2.10)
        keep_above_caption(retries)
        self.play(LaggedStart(*[FadeIn(t, shift=UP * 0.05) for t in retries], lag_ratio=0.18), run_time=0.8)
        caption = self.show_caption("The origin retries with backoff. Periodically one wave floods to escape stale routing information.", ORANGE, caption)

        self.play(FadeOut(stopped), run_time=0.2)
        # Show the flood as expansion through actual surviving links, not a teleport.
        self.travel(make_packet("flood", ORANGE, 16), a, b, 0.38)
        self.travel(make_packet("flood", ORANGE, 16), a, x, 0.38)
        self.travel(make_packet("flood", ORANGE, 16), x, e, 0.42)
        self.travel(make_packet("flood", ORANGE, 16), e, d, 0.42)
        advance = make_badge("D advances generation", GREEN, 19).next_to(d, DOWN, buff=0.34)
        self.play(FadeIn(advance), run_time=0.35)

        caption = self.show_caption("Fresh state comes back through the surviving branch, again one hop at a time.", GREEN, caption)
        self.travel(make_packet("fresh CRYST", GREEN, 15), d, e, 0.42)
        self.travel(make_packet("fresh CRYST", GREEN, 15), e, x, 0.42)
        self.travel(make_packet("fresh CRYST", GREEN, 15), x, a, 0.42)
        new_path = make_path([a, x, e, d], GREEN, 8)
        self.play(ShowCreation(new_path), run_time=0.7)

        caption = self.show_caption("The repaired route is immediately useful.", GREEN, caption)
        self.travel(make_packet("DATA", GREEN, 17), a, x, 0.42)
        self.travel(make_packet("DATA", GREEN, 17), x, e, 0.42)
        self.travel(make_packet("DATA", GREEN, 17), e, d, 0.42)
        self.play(FadeOut(retries), FadeOut(advance), run_time=0.28)
        evidence = make_badge("hard seed 69: 2,630 → 290 SEQ_REQ transmissions", GREEN, 18).move_to(DOWN * 1.55)
        self.play(FadeIn(evidence, shift=UP * 0.06), run_time=0.45)
        caption = self.show_caption("One-shot intermediate repair removed most of the feedback amplification in the pathological trace.", GREEN, caption)
        self.wait(1.1)
        self.fade_scene()


class Validation(DTScene):
    def construct(self):
        self.prepare("What the current implementation has actually demonstrated", GREEN)

        metrics = VGroup(
            make_metric("325 / 325", "normal real-C++ suite", GREEN, 3.0),
            make_metric("325 / 325", "ASan + UBSan suite", GREEN, 3.0),
            make_metric("900 / 900", "startup / early-send matrix", BLUE, 3.0),
            make_metric("100 / 100", "cut-heal convergence + delivery", BLUE, 3.0),
        ).arrange_in_grid(n_rows=2, n_cols=2, buff=0.45).move_to(UP * 0.8)
        self.play(LaggedStart(*[FadeIn(m, shift=UP * 0.12) for m in metrics], lag_ratio=0.12), run_time=1.1)

        zero = make_metric("0", "routing loops in the cut-heal matrix", YELLOW, 3.4).move_to(DOWN * 1.25)
        self.play(FadeIn(zero, scale=0.9), run_time=0.55)
        caption = self.show_caption("These are bounded empirical results, not a claim that every physical RF environment is solved.", MUTED)

        limits = VGroup(
            make_badge("capture / near-far", PURPLE, 18),
            make_badge("hidden terminals", PURPLE, 18),
            make_badge("power loss", PURPLE, 18),
            make_badge("long hardware soak", PURPLE, 18),
            make_badge("duplicate node IDs", PURPLE, 18),
        ).arrange(RIGHT, buff=0.2).scale(0.92).move_to(DOWN * 2.55)
        keep_above_caption(limits)
        self.play(LaggedStart(*[FadeIn(l, shift=UP * 0.08) for l in limits], lag_ratio=0.08), run_time=0.8)
        self.wait(1.5)
        self.fade_scene()


class Closing(DTScene):
    def construct(self):
        self.prepare()
        title = text_mob("DTProtocol in one picture", 54, WHITE_SOFT, True).move_to(UP * 2.5)
        line1 = text_mob("Trusted state becomes useful locally, not all at once.", 35, GREEN, True)
        line2 = text_mob("New state assembles transactionally; DATA keeps moving on committed routes.", 29, WHITE_SOFT)
        summary = VGroup(line1, line2).arrange(DOWN, buff=0.25).move_to(UP * 0.75)
        self.play(FadeIn(title, shift=UP * 0.15), FadeIn(summary, shift=UP * 0.12), run_time=1.0)

        nodes = VGroup(*[make_node(str(i + 1), BLUE if i not in (0, 5) else GREEN, 0.28) for i in range(6)])
        nodes.arrange(RIGHT, buff=1.45).move_to(DOWN * 1.35)
        self.play(LaggedStart(*[FadeIn(n) for n in nodes], lag_ratio=0.10), run_time=0.75)
        edges = VGroup(*[make_edge(nodes[i], nodes[i + 1], GRID, 4) for i in range(5)])
        self.play(LaggedStart(*[ShowCreation(e) for e in edges], lag_ratio=0.15), run_time=0.85)
        route = make_path(list(nodes), GREEN, 7)
        self.play(ShowCreation(route), run_time=0.65)
        for i in range(len(nodes) - 1):
            self.travel(make_packet("DATA", GREEN, 16), nodes[i], nodes[i + 1], 0.34)
        footer = text_mob("Source, claims, and reproducible render scripts: docs/video/", 22, MUTED).to_edge(DOWN, buff=0.35)
        self.play(FadeIn(footer), run_time=0.45)
        self.wait(1.3)
        self.fade_scene()
