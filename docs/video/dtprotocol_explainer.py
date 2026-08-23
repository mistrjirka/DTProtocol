from manimlib import *

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
    return VGroup(label, line, accent_line).set_z_index(Z_TITLE)


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
        fill_opacity=0.96,
    )
    label.move_to(box)
    group = VGroup(box, label)
    group.to_edge(DOWN, buff=0.25)
    return group.set_z_index(Z_CAPTION)


def make_badge(text, color=BLUE, size=24):
    label = text_mob(text, size, WHITE_SOFT, True)
    box = RoundedRectangle(
        width=label.get_width() + 0.38,
        height=label.get_height() + 0.22,
        corner_radius=0.12,
        stroke_color=color,
        stroke_width=2,
        fill_color=color,
        fill_opacity=0.16,
    )
    label.move_to(box)
    return VGroup(box, label).set_z_index(Z_BADGE)


def make_node(name, color=BLUE, radius=0.34):
    halo = Circle(radius=radius).set_fill(color, opacity=0.16).set_stroke(width=0)
    rim = Circle(radius=radius).set_stroke(color, width=4)
    label = text_mob(name, 27, WHITE_SOFT, True)
    label.move_to(rim)
    return VGroup(halo, rim, label).set_z_index(Z_NODE)


def make_edge(a, b, color=GRID, width=5, dashed=False):
    cls = DashedLine if dashed else Line
    return cls(a.get_center(), b.get_center(), color=color, stroke_width=width).set_z_index(Z_EDGE)


def make_packet(label, color=BLUE, size=22):
    text = text_mob(label, size, WHITE_SOFT, True)
    box = RoundedRectangle(
        width=max(0.82, text.get_width() + 0.34),
        height=max(0.38, text.get_height() + 0.17),
        corner_radius=0.1,
        stroke_color=color,
        stroke_width=2,
        fill_color=color,
        fill_opacity=0.20,
    )
    text.move_to(box)
    return VGroup(box, text).set_z_index(Z_PACKET)


def make_card(title, rows, width=3.4, accent=BLUE, row_size=23):
    title_text = text_mob(title, 27, WHITE_SOFT, True)
    header = RoundedRectangle(
        width=width,
        height=0.55,
        corner_radius=0.12,
        stroke_color=accent,
        stroke_width=2,
        fill_color=accent,
        fill_opacity=0.17,
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
        fill_opacity=0.98,
    )
    row_mobs.move_to(body)
    content = VGroup(body, row_mobs)
    return VGroup(VGroup(header, title_text), content).arrange(DOWN, buff=0.08).set_z_index(Z_PANEL)


def make_layer(name, subtitle, color, width=4.2):
    box = RoundedRectangle(
        width=width,
        height=0.82,
        corner_radius=0.13,
        stroke_color=color,
        stroke_width=2.5,
        fill_color=PANEL,
        fill_opacity=0.98,
    )
    title = text_mob(name, 27, WHITE_SOFT, True)
    sub = text_mob(subtitle, 19, MUTED)
    title.move_to(box.get_center() + UP * 0.14)
    sub.move_to(box.get_center() + DOWN * 0.19)
    return VGroup(box, title, sub).set_z_index(Z_PANEL)


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
        fill_opacity=0.97,
    )
    group.move_to(box)
    return VGroup(box, group).set_z_index(Z_PANEL)


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
            self.play(ReplacementTransform(old, new), run_time=run_time)
        return new

    def travel(self, packet, start, end, run_time=0.58, arc=0.0):
        packet.move_to(start.get_center())
        if abs(arc) < 1e-6:
            path = Line(start.get_center(), end.get_center())
        else:
            path = ArcBetweenPoints(start.get_center(), end.get_center(), angle=arc)
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
        subtitle = text_mob("A route exists to C. The rest of the network is still crystallizing.", 29, BLUE_2)
        fit_width(subtitle, 11.5)
        VGroup(title, subtitle).arrange(DOWN, buff=0.22).move_to(UP * 2.35)

        a, b, c, d = [make_node(name, GREEN if name in "AC" else BLUE) for name in "ABCD"]
        nodes = VGroup(a, b, c, d).arrange(RIGHT, buff=2.0).move_to(UP * 0.55)
        edges = VGroup(*[make_edge(nodes[i], nodes[i + 1]) for i in range(3)])

        known = VGroup(
            Line(a.get_center(), b.get_center(), color=GREEN, stroke_width=8),
            Line(b.get_center(), c.get_center(), color=GREEN, stroke_width=8),
        ).set_z_index(Z_ROUTE)
        unknown = DashedLine(c.get_center(), d.get_center(), color=PURPLE, stroke_width=5).set_z_index(Z_EDGE)

        table = make_card("A knows", ["B via B", "C via B", "D unknown"], 3.2, GREEN, 22)
        table.move_to(LEFT * 3.4 + DOWN * 1.55)
        keep_above_caption(table)
        pending = make_card("still in progress", ["C ↔ D snapshot", "not committed"], 3.2, PURPLE, 22)
        pending.move_to(RIGHT * 3.4 + DOWN * 1.55)
        keep_above_caption(pending)

        self.play(FadeIn(title, shift=UP * 0.12), FadeIn(subtitle, shift=UP * 0.08), run_time=0.8)
        self.play(ShowCreation(edges), LaggedStart(*[FadeIn(n, scale=0.8) for n in nodes], lag_ratio=0.1), run_time=0.9)
        self.play(ShowCreation(known), ShowCreation(unknown), FadeIn(table), FadeIn(pending), run_time=0.8)
        caption = self.show_caption("Does A have to wait for D, or can it use the route it already trusts?", YELLOW)

        question = make_packet("DATA ?", YELLOW, 22).move_to(a)
        self.play(FadeIn(question, scale=0.8), run_time=0.25)
        self.play(question.animate.move_to((a.get_center() + b.get_center()) / 2), run_time=0.65)
        self.play(Indicate(known, color=GREEN), Indicate(pending, color=PURPLE), run_time=0.65)
        self.wait(0.7)
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
        self.prepare("Crystallization is transactional neighbour synchronization", PURPLE)

        a, b, c, d = [make_node(name, BLUE if name in "BC" else GREEN) for name in "ABCD"]
        nodes = VGroup(a, b, c, d).arrange(RIGHT, buff=2.0).move_to(UP * 1.55)
        edges = VGroup(*[make_edge(nodes[i], nodes[i + 1]) for i in range(3)])
        self.play(ShowCreation(edges), LaggedStart(*[FadeIn(n, scale=0.8) for n in nodes], lag_ratio=0.1), run_time=1.0)

        caption = self.show_caption("HELLO is a seven-byte digest: boot incarnation plus route version.", GREEN)
        self.travel(make_packet("HELLO", GREEN), a, b, 0.7)
        digest = make_badge("inc 12  |  version 8", GREEN, 21).next_to(b, DOWN, buff=0.35)
        self.play(FadeIn(digest, shift=UP * 0.08), run_time=0.45)

        caption = self.show_caption("A mismatch triggers one direct CRYST_REQ.", PURPLE, caption)
        self.travel(make_packet("CRYST_REQ", PURPLE, 19), b, a, 0.7)

        old = make_card("B: committed contribution from A", ["A  seq12  d1", "C  seq12  d2"], 3.9, BLUE_2, 21)
        old.move_to(LEFT * 3.5 + DOWN * 1.25)
        staging = make_card("staging: version 8", ["chunk 0 / 2", "chunk 1 / 2"], 3.2, PURPLE, 21)
        staging.move_to(RIGHT * 1.5 + DOWN * 1.25)
        self.play(FadeIn(old), FadeIn(staging), run_time=0.7)

        caption = self.show_caption("CRYST may span several frames. Chunks enter a private assembly.", BLUE, caption)
        p0 = make_packet("CRYST 0/2", BLUE, 19)
        self.travel(p0, a, b, 0.72)
        mark0 = make_badge("received", GREEN, 18).move_to(staging[1][0].get_center() + UP * 0.18)
        self.play(FadeIn(mark0, scale=0.8), run_time=0.35)
        old_box = SurroundingRectangle(old, color=GREEN, buff=0.12).set_z_index(Z_MARK)
        self.play(ShowCreation(old_box), run_time=0.35)

        caption = self.show_caption("The previous complete table remains active until every chunk is present.", GREEN, caption)
        self.wait(0.8)
        self.travel(make_packet("CRYST 1/2", BLUE, 19), a, b, 0.72)
        mark1 = make_badge("received", GREEN, 18).move_to(staging[1][0].get_center() + DOWN * 0.18)
        self.play(FadeIn(mark1, scale=0.8), run_time=0.35)

        new = make_card("B: committed contribution from A", ["A  seq12  d1", "C  seq12  d2", "D  seq17  d3"], 3.9, GREEN, 21)
        new.move_to(old)
        caption = self.show_caption("Then the entire neighbour contribution is replaced atomically.", GREEN, caption)
        self.play(ReplacementTransform(old, new), FadeOut(staging), FadeOut(mark0), FadeOut(mark1), FadeOut(old_box), run_time=0.85)

        report = make_card("Advertisement received from B", ["D  seq17  metric2"], 3.4, YELLOW, 22)
        local = make_card("Candidate stored at A", ["D  via B  seq17  metric3"], 4.0, BLUE, 22)
        report.move_to(LEFT * 2.5 + DOWN * 2.28)
        local.move_to(RIGHT * 2.6 + DOWN * 2.28)
        keep_above_caption(report)
        keep_above_caption(local)
        rewrite = Arrow(report.get_right(), local.get_left(), buff=0.15, color=YELLOW).set_z_index(Z_CONNECTOR)
        self.play(FadeIn(report), GrowArrow(rewrite), FadeIn(local), run_time=0.9)
        caption = self.show_caption("A receiver rewrites each route as: via sender, neighbour metric plus one.", YELLOW, caption)
        self.wait(1.4)
        self.fade_scene()


class Feasibility(DTScene):
    def construct(self):
        self.prepare("Feasibility prevents count-to-infinity loops", YELLOW)

        a = make_node("A", BLUE).move_to(LEFT * 3 + UP * 1.1)
        b = make_node("B", BLUE).move_to(ORIGIN + UP * 2.0)
        c = make_node("C", BLUE).move_to(RIGHT * 3 + UP * 1.1)
        d = make_node("D", GREEN).move_to(DOWN * 1.4)
        edges = VGroup(
            make_edge(a, b), make_edge(b, c), make_edge(c, a),
            make_edge(a, d, GREEN), make_edge(c, d, GREEN),
        )
        self.play(ShowCreation(edges), LaggedStart(*[FadeIn(n, scale=0.8) for n in (a, b, c, d)], lag_ratio=0.1), run_time=1.0)

        caption = self.show_caption("Assume A has advertised D at generation 12 with feasible distance 2.", YELLOW)
        state = make_card("A: feasibility state for D", ["generation 12", "feasible distance 2"], 3.8, YELLOW, 22)
        state.move_to(LEFT * 3.7 + DOWN * 2.25)
        keep_above_caption(state)
        self.play(FadeIn(state), run_time=0.6)

        self.play(FadeOut(edges[3]), FadeOut(edges[4]), run_time=0.5)
        cut1 = Cross(edges[3], stroke_color=RED, stroke_width=7).set_z_index(Z_MARK)
        cut2 = Cross(edges[4], stroke_color=RED, stroke_width=7).set_z_index(Z_MARK)
        self.play(ShowCreation(cut1), ShowCreation(cut2), run_time=0.45)

        candidate = make_card("B reports a candidate", ["generation 12", "neighbour metric 2"], 3.7, BLUE, 22)
        candidate.move_to(RIGHT * 3.4 + DOWN * 2.25)
        keep_above_caption(candidate)
        self.play(FadeIn(candidate), run_time=0.6)
        inequality = text_mob("2 < 2   is false", 34, RED, True, MONO).move_to(DOWN * 0.8).set_z_index(Z_BADGE)
        self.play(FadeIn(inequality, scale=0.85), run_time=0.45)
        blocked = DashedLine(a.get_center(), b.get_center(), color=RED, stroke_width=7).set_z_index(Z_ROUTE)
        self.play(ShowCreation(blocked), run_time=0.5)
        caption = self.show_caption("Same-generation candidates must improve the recorded feasible distance.", RED, caption)
        self.wait(0.8)

        seq = make_packet("SEQ_REQ: need gen 13", YELLOW, 18)
        seq.move_to(a)
        path = VMobject().set_points_as_corners([a.get_center(), b.get_center(), c.get_center(), d.get_center()])
        self.add(seq)
        self.play(MoveAlongPath(seq, path), run_time=1.4, rate_func=linear)
        self.remove(seq)
        new_gen = make_badge("D advances to generation 13", GREEN, 22).next_to(d, DOWN, buff=0.38)
        self.play(FadeIn(new_gen, shift=UP * 0.1), run_time=0.55)

        accepted = make_card("Fresh candidate", ["generation 13", "metric 3", "accepted"], 3.5, GREEN, 22)
        accepted.move_to(candidate)
        self.play(ReplacementTransform(candidate, accepted), FadeOut(inequality), FadeOut(blocked), run_time=0.7)
        route = VGroup(
            Line(a.get_center(), b.get_center(), color=GREEN, stroke_width=8),
            Line(b.get_center(), c.get_center(), color=GREEN, stroke_width=8),
            Line(c.get_center(), d.get_center(), color=GREEN, stroke_width=8),
        ).set_z_index(Z_ROUTE)
        self.play(ShowCreation(route), run_time=0.8)
        caption = self.show_caption("Freshness makes a route feasible; distance chooses among feasible routes.", GREEN, caption)
        self.wait(1.3)
        self.fade_scene()


class EarlyData(DTScene):
    def construct(self):
        self.prepare("DATA can flow before global convergence", GREEN)

        a, b, c, d = [make_node(name, GREEN if name in "AC" else BLUE) for name in "ABCD"]
        nodes = VGroup(a, b, c, d).arrange(RIGHT, buff=2.0).move_to(UP * 1.35)
        edges = VGroup(*[make_edge(nodes[i], nodes[i + 1]) for i in range(3)])
        self.play(ShowCreation(edges), LaggedStart(*[FadeIn(n, scale=0.8) for n in nodes], lag_ratio=0.1), run_time=1.0)

        table = make_card("A: selected routes", ["B via B  d1", "C via B  d2", "D unknown"], 3.5, GREEN, 22)
        table.move_to(LEFT * 3.8 + DOWN * 1.35)
        pending = make_card("Unrelated state", ["C↔D snapshot", "chunk 1 / 2"], 3.2, PURPLE, 22)
        pending.move_to(RIGHT * 3.6 + DOWN * 1.35)
        self.play(FadeIn(table), FadeIn(pending), run_time=0.7)

        caption = self.show_caption("A knows a valid route to C, although D is still unknown.", GREEN)
        self.travel(make_packet("DATA", GREEN), a, b, 0.65)
        self.travel(make_packet("DATA", GREEN), b, c, 0.65)

        direct = make_badge("provisional direct route to B", BLUE, 19).next_to(c, DOWN, buff=0.28)
        crumb = VGroup(
            DashedLine(c.get_center(), b.get_center(), color=YELLOW, stroke_width=4),
            DashedLine(b.get_center(), a.get_center(), color=YELLOW, stroke_width=4),
        ).set_z_index(Z_CONNECTOR)
        crumb_label = make_badge("reverse breadcrumb: C → B → A", YELLOW, 18).move_to(b.get_center() + DOWN * 0.62)
        self.play(FadeIn(direct, shift=UP * 0.08), ShowCreation(crumb), FadeIn(crumb_label), run_time=0.75)
        caption = self.show_caption("The true one-hop sender becomes a provisional reverse route; the original source gets a breadcrumb.", YELLOW, caption)

        self.travel(make_packet("E2E ACK", PURPLE, 19), c, b, 0.65)
        self.travel(make_packet("E2E ACK", PURPLE, 19), b, a, 0.65)
        delivered = make_badge("application delivered once", GREEN, 21).next_to(c, UP, buff=0.32)
        self.play(FadeIn(delivered, shift=DOWN * 0.08), run_time=0.45)

        self.travel(make_packet("CRYST 2/2", BLUE, 18), d, c, 0.65)
        complete = make_card("Unrelated state", ["C↔D snapshot", "committed"], 3.2, GREEN, 22).move_to(pending)
        self.play(ReplacementTransform(pending, complete), run_time=0.55)
        caption = self.show_caption("Message delivery and crystallization make progress at the same time.", GREEN, caption)
        self.wait(1.4)
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
        self.play(FadeIn(replay, shift=UP * 0.08), Indicate(app_count, color=GREEN), run_time=0.65)
        self.travel(make_packet("E2E ACK 7", PURPLE, 18), d, r, 0.58)
        self.travel(make_packet("E2E ACK 7", PURPLE, 18), r, s, 0.58)

        caption = self.show_caption("Exactly-once delivery is a replay rule, not an assumption that radio frames arrive once.", GREEN, caption)
        self.wait(1.3)
        self.fade_scene()


class MultipartCompression(DTScene):
    def construct(self):
        self.prepare("Large messages: optimize airtime, then repair only what is missing", ORANGE)

        raw = RoundedRectangle(width=5.4, height=0.72, corner_radius=0.12, stroke_color=BLUE, fill_color=BLUE, fill_opacity=0.2)
        raw_label = text_mob("measured test payload: 5,200 bytes", 25, WHITE_SOFT, True).move_to(raw)
        raw_group = VGroup(raw, raw_label).move_to(UP * 2.0)
        compressed = RoundedRectangle(width=2.0, height=0.72, corner_radius=0.12, stroke_color=GREEN, fill_color=GREEN, fill_opacity=0.22)
        compressed_label = text_mob("encoded", 24, WHITE_SOFT, True).move_to(compressed)
        compressed_group = VGroup(compressed, compressed_label).move_to(UP * 0.95 + LEFT * 1.7)
        airtime_raw = make_badge("23 raw fragments", BLUE, 20).next_to(raw_group, RIGHT, buff=0.25)
        airtime_comp = make_badge("590 B → 3 fragments", GREEN, 20).next_to(compressed_group, RIGHT, buff=0.25)

        self.play(FadeIn(raw_group), FadeIn(airtime_raw), run_time=0.65)
        caption = self.show_caption("Compression is selected by complete LoRa airtime, not by byte count alone.", ORANGE)
        self.play(Transform(raw_group.copy(), compressed_group), FadeIn(airtime_comp), run_time=0.8)
        decision = make_badge("~37.8 s modeled airtime saved", GREEN, 21).move_to(RIGHT * 3.6 + UP * 0.95)
        self.play(FadeIn(decision, shift=LEFT * 0.1), run_time=0.5)

        caption = self.show_caption("If encoded bytes still exceed one frame, fragmentation happens after compression.", BLUE, caption)
        chunks = VGroup(*[make_packet(f"frag {i}", BLUE, 19) for i in range(5)]).arrange(RIGHT, buff=0.24).move_to(DOWN * 0.5)
        self.play(LaggedStart(*[FadeIn(ch, shift=DOWN * 0.1) for ch in chunks], lag_ratio=0.1), run_time=0.9)
        missing_cross = Cross(chunks[2], stroke_color=RED, stroke_width=6).set_z_index(Z_MARK)
        self.play(ShowCreation(missing_cross), run_time=0.35)

        receiver = make_card("destination assembly", ["0 ✓", "1 ✓", "2 missing", "3 ✓", "4 ✓"], 3.1, PURPLE, 21)
        receiver.move_to(RIGHT * 4.3 + DOWN * 1.75)
        bitmap = make_badge("missing bitmap: 00100", YELLOW, 20).move_to(LEFT * 3.6 + DOWN * 1.75)
        self.play(FadeIn(receiver), FadeIn(bitmap), run_time=0.7)
        caption = self.show_caption("The destination exposes no partial payload. It returns a compact missing bitmap.", PURPLE, caption)

        repair = make_packet("frag 2 only", YELLOW, 19).move_to(bitmap)
        self.play(FadeIn(repair, scale=0.8), run_time=0.25)
        self.play(repair.animate.move_to(receiver), run_time=0.8)
        self.play(FadeOut(repair), FadeOut(missing_cross), run_time=0.3)
        complete = make_card("destination assembly", ["all fragments present", "decode", "deliver once"], 3.4, GREEN, 21).move_to(receiver)
        self.play(ReplacementTransform(receiver, complete), run_time=0.65)
        self.play(FadeIn(make_badge("one whole-message ACK", GREEN, 20).next_to(complete, UP, buff=0.25)), run_time=0.45)

        stats = VGroup(
            make_badge("233 B single-frame payload", BLUE, 18),
            make_badge("230 B per fragment", BLUE, 18),
            make_badge("16 KiB default message cap", ORANGE, 18),
            make_badge("34 B status for 16 KiB", YELLOW, 18),
        ).arrange(RIGHT, buff=0.18).scale(0.9).to_edge(DOWN, buff=0.95)
        keep_above_caption(stats)
        self.play(LaggedStart(*[FadeIn(s, shift=UP * 0.08) for s in stats], lag_ratio=0.08), run_time=0.8)
        caption = self.show_caption("Selective repair keeps multipart overhead bounded even when one fragment is lost.", GREEN, caption)
        self.wait(1.4)
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
            box = RoundedRectangle(width=10.5, height=1.0, corner_radius=0.12, stroke_color=color, fill_color=PANEL, fill_opacity=0.98)
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
        self.prepare("Repair after a cut: persistent at the origin, one-shot at each hop", YELLOW)

        a = make_node("A", GREEN).move_to(LEFT * 5.2 + UP * 1.3)
        b = make_node("B", BLUE).move_to(LEFT * 2.5 + UP * 1.8)
        c = make_node("C", BLUE).move_to(RIGHT * 0.2 + UP * 1.3)
        e = make_node("E", BLUE).move_to(RIGHT * 2.9 + UP * 1.8)
        d = make_node("D", GREEN).move_to(RIGHT * 5.3 + UP * 1.3)
        alt = make_node("X", BLUE).move_to(DOWN * 0.8)
        nodes = VGroup(a, b, c, e, d, alt)
        main_edges = VGroup(make_edge(a, b), make_edge(b, c), make_edge(c, e), make_edge(e, d))
        alt_edges = VGroup(make_edge(a, alt, GREEN), make_edge(alt, e, GREEN))
        self.play(ShowCreation(main_edges), ShowCreation(alt_edges), LaggedStart(*[FadeIn(n) for n in nodes], lag_ratio=0.08), run_time=1.0)

        old_path = VGroup(*[Line(x.get_center(), y.get_center(), color=BLUE_2, stroke_width=8) for x, y in ((a, b), (b, c), (c, e), (e, d))]).set_z_index(Z_ROUTE)
        self.play(ShowCreation(old_path), run_time=0.65)
        caption = self.show_caption("The preferred path disappears. Feasibility blocks stale same-generation detours.", RED)
        cut = Cross(main_edges[1], stroke_color=RED, stroke_width=8).set_z_index(Z_MARK)
        self.play(ShowCreation(cut), FadeOut(old_path), run_time=0.55)

        seq = make_packet("SEQ_REQ", YELLOW, 19)
        self.travel(seq.copy(), a, b, 0.45)
        self.travel(seq.copy(), b, c, 0.45)
        lost = seq.copy().move_to((c.get_center() + e.get_center()) / 2)
        self.play(FadeIn(lost, scale=0.8), run_time=0.2)
        self.play(ShowCreation(Cross(lost, stroke_color=RED, stroke_width=6).set_z_index(Z_MARK)), FadeOut(lost), run_time=0.35)

        timeline = VGroup(
            make_badge("5 s", YELLOW, 18),
            make_badge("10 s", YELLOW, 18),
            make_badge("20 s", YELLOW, 18),
            make_badge("4th attempt: flood", ORANGE, 18),
            make_badge("40 s", YELLOW, 18),
            make_badge("60 s cap", YELLOW, 18),
        ).arrange(RIGHT, buff=0.18).scale(0.88).move_to(DOWN * 2.45)
        keep_above_caption(timeline)
        self.play(LaggedStart(*[FadeIn(t, shift=UP * 0.08) for t in timeline], lag_ratio=0.1), run_time=1.0)
        caption = self.show_caption("The requester retries the logical repair with backoff; every fourth wave floods as an escape hatch.", YELLOW, caption)

        flood = make_packet("flood SEQ_REQ", ORANGE, 18).move_to(a)
        self.play(FadeIn(flood, scale=0.8), run_time=0.2)
        self.play(flood.animate.move_to(d), run_time=1.25)
        self.play(FadeOut(flood), run_time=0.2)
        advance = make_badge("D advances generation", GREEN, 20).next_to(d, DOWN, buff=0.32)
        self.play(FadeIn(advance, shift=UP * 0.08), run_time=0.5)

        new_path = VGroup(
            Line(a.get_center(), alt.get_center(), color=GREEN, stroke_width=8),
            Line(alt.get_center(), e.get_center(), color=GREEN, stroke_width=8),
            Line(e.get_center(), d.get_center(), color=GREEN, stroke_width=8),
        ).set_z_index(Z_ROUTE)
        self.play(ShowCreation(new_path), run_time=0.8)
        caption = self.show_caption("Fresh CRYST state makes the longer surviving path feasible.", GREEN, caption)

        before = make_metric("2,630", "SEQ_REQ transmissions in hard seed 69", RED, 3.8)
        after = make_metric("290", "after one-shot-hop repair", GREEN, 3.8)
        arrow = Arrow(LEFT * 0.7, RIGHT * 0.7, color=YELLOW).set_z_index(Z_CONNECTOR)
        compare = VGroup(before, arrow, after).arrange(RIGHT, buff=0.3).scale(0.82).move_to(DOWN * 1.15)
        self.play(FadeIn(compare, shift=UP * 0.1), run_time=0.8)
        caption = self.show_caption("Removing five LCMM retries from every SEQ_REQ hop cut the feedback storm by about 89% in that trace.", GREEN, caption)
        self.wait(1.5)
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
        title = text_mob("DTProtocol in one sentence", 54, WHITE_SOFT, True).move_to(UP * 2.5)
        line1 = text_mob("Keep complete route state usable while new state is incomplete.", 34, GREEN, True)
        line2 = text_mob("Use feasibility for safety, generation repair for liveness,", 31, WHITE_SOFT)
        line3 = text_mob("and layered acknowledgements for reliable application delivery.", 31, WHITE_SOFT)
        summary = VGroup(line1, line2, line3).arrange(DOWN, buff=0.25).move_to(UP * 0.55)
        self.play(FadeIn(title, shift=UP * 0.15), FadeIn(summary, shift=UP * 0.12), run_time=1.1)

        nodes = VGroup(*[make_node(str(i + 1), BLUE if i not in (0, 5) else GREEN, 0.28) for i in range(6)])
        nodes.arrange(RIGHT, buff=1.45).move_to(DOWN * 1.5)
        edges = VGroup(*[make_edge(nodes[i], nodes[i + 1], GRID, 4) for i in range(5)])
        route = VGroup(*[Line(nodes[i].get_center(), nodes[i + 1].get_center(), color=GREEN, stroke_width=7) for i in range(5)]).set_z_index(Z_ROUTE)
        self.play(ShowCreation(edges), LaggedStart(*[FadeIn(n) for n in nodes], lag_ratio=0.08), run_time=0.8)
        self.play(ShowCreation(route), run_time=0.65)
        packet = make_packet("DATA", GREEN, 18).move_to(nodes[0])
        path = VMobject().set_points_as_corners([n.get_center() for n in nodes])
        self.add(packet)
        self.play(MoveAlongPath(packet, path), run_time=1.7, rate_func=linear)
        self.remove(packet)
        footer = text_mob("Source and render scripts live in docs/video/.", 22, MUTED).to_edge(DOWN, buff=0.35)
        self.play(FadeIn(footer), run_time=0.45)
        self.wait(1.8)
        self.fade_scene()
