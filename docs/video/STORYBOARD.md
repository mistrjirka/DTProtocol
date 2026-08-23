# DTProtocol explainer storyboard

The explainer is built around one concrete question: **can A send to C while unrelated mesh state is still crystallizing?** The architecture diagram comes later, after the viewer has seen the behavior it abstracts.

## Visual grammar

- **Radio range:** one faint transparent circle shown only while explaining physical discovery. Its neighbour is actually inside the circle; the compact four-node layout is reused across the core teaching scenes. This is the only routinely translucent semantic layer.
- **Direct neighbour relation:** thin blue/neutral link, clipped before both node bodies.
- **Committed selected route:** thicker green link/path.
- **Pending CRYST state:** purple, explicitly labelled pending.
- **DATA:** green packet, moved one physical/routing hop at a time.
- **Repair/control packet:** yellow/orange/purple according to role, also one hop at a time.
- **Node:** opaque dark body with a coloured rim. Lines are never allowed through the node or its label.
- **Cards/captions/packets:** opaque dark fill with coloured outline. Geometry behind them must not bleed through.

Semantic text/state changes use **clear then rebuild**, not glyph morphing. A successful render is not sufficient: sampled frames must be visually reviewed.

## Narrative order

### 1. Opening

Show four radios and their range circles. A already has a committed route A → B → C; knowledge of D is visibly pending. Put a DATA packet at A and ask whether A must wait.

### 2. Crystallization

1. Start with radios but no learned links.
2. Reveal A's physical range circle; B is actually inside it. Show A → B HELLO and B → A CRYST_REQ.
3. Fade the physical range circle and reveal the A–B learned direct-neighbour link.
4. Repeat with B's range to C and C's range to D. Show only one range circle at a time to avoid clutter.
5. Show A's committed view: B direct, C/D unknown.
6. Send CRYST chunk 0/2 from B to A. Keep A's old committed view unchanged.
7. Send CRYST chunk 1/2.
8. Clear the pending/old state, then reveal the newly committed view and A → B → C selected route.
9. Leave D visibly still propagating. There is no global ready switch.

### 3. Early DATA

Reuse the same four-node mental model. A's route to C is green and committed while a newer C snapshot is still pending at B. Move DATA A → B, then B → C. Between logical-message steps, let a CRYST chunk move C → B. Reveal the reverse breadcrumb and return the E2E ACK C → B → A one hop at a time. Finish the pending CRYST transaction afterward.

### 4. Feasibility

Keep this local rather than pretending a repair packet crosses a failed link. Show only the real A–B neighbour link; B's knowledge about D lives in the report card rather than a fake B–D radio edge. Show A's remembered feasible distance, the failed strict comparison, and then a newer generation becoming feasible. Green means selected next hop only after feasibility.

### 5. Reliability

Move DATA and link ACKs hop-by-hop. Lose the final E2E ACK, retry with the same logical identity, show replay suppression at the destination, then return the E2E ACK again. Emphasise the callback count with an outline rather than recolouring the whole card.

### 6. Multipart + compression

1. Use the measured 5,200 B → 590 B example only to explain the airtime-aware compression decision (23 raw fragments → 3 compressed fragments, ~37.8 s modeled saving).
2. Clear that graphic before introducing the network.
3. Send three fragments S → R → D step by step.
4. Lose fragment 1 after link retries are conceptually exhausted.
5. Show destination state 0 ✓, 1 missing, 2 ✓.
6. Remove the loss marker before sending STATUS 010 back D → R → S.
7. Retransmit only fragment 1 S → R → D.
8. Reveal complete assembly and return one whole-message ACK D → R → S.

### 7. Repair after a cut

Use A → B → C → E → D as the old route and A → X → E → D as the surviving physical branch. Cut C–E (not B–C), so a directed SEQ_REQ can truthfully progress A → B → C and stop at the cut. Show retry timing, then a periodic flood expanding through actual surviving links. Fresh state returns D → E → X → A, the green replacement route appears, and DATA immediately uses it. Clear retry UI before showing measured traffic reduction.

### 8. Scheduler

Explain priority as a service rule, not a wall of implementation text: response traffic first, bounded repair burst, then normal traffic. Keep only one token moving at a time.

### 9. Architecture

Now reveal the component diagram. The viewer has already seen the concrete behaviors represented by the boxes, so the diagram acts as a recap rather than an opening abstraction.

### 10. Validation

Report bounded measured results and physical limitations without implying proof of arbitrary RF environments.

### 11. Closing

Build a small network gradually, reveal a committed route, and move one DATA packet across it hop-by-hop. End on: trusted state becomes useful locally, not all at once.

## Visual QC gates

Before publication:

1. No raw center-to-center `Line`/`DashedLine` calls for node links.
2. No `ReplacementTransform` for unrelated semantic text/state.
3. No `Indicate` that recolours an entire card or node group.
4. Every node-link endpoint is outside the opaque node body.
5. Sample every scene through the full timeline; inspect transition frames, not just stable states.
6. Specifically reject frames where a stale cross/annotation accidentally applies to a later packet.
7. Reject any frame where the caption describes the previous visual after a new concept has already appeared.
