# ThermoGuard AI — Human Annotation Guide

**Audience:** Dataset Annotators, Quality Reviewers, and Lead Electrical Inspectors  
**Version:** 1.0.0  
**Effective Date:** September 2026  

---

## 1. Quick Reference: Golden Rules of Electrical Annotation

1. **Tight Bounding Boxes:** Draw bounding boxes around the full structural boundary of the component (including terminal screws and mounting shoulders), but do not include loose outgoing wires that belong to adjacent components.
2. **Never Guess "Loose Connection":** You are annotating visible optical features from photographs. Do NOT label a connection `verified_loose_connection` unless an accompanying physical inspection ticket (e.g. calibrated torque test or micro-ohmmeter measurement) is attached to the record. If a wire is visibly displaced or tilted, use `mechanical_misalignment`.
3. **Multi-Label is Supported:** If a breaker has both a cracked toggle and surface corrosion on its line terminals, annotate the breaker bounding box and attach both `physical_casing_damage` and `surface_corrosion_oxidation`.
4. **Identify Hard Negatives:** When a dark spot is an ink QC stamp or a wire shadow, do NOT ignore it or label it as scorching. Annotate it as a `HardNegative` (`manufacturing_marking` or `shadow_or_ambient_darkening`).

---

## 2. Decision Trees for Ambiguous Cases

### 2.1 Dark Spot on Plastic Casing: Burn Mark vs. Shadow vs. Ink Stamp
```
Is the dark spot located on the casing?
├── Does it have crisp alphanumeric edges or uniform circular shape?
│   └── YES ➔ Annotate as HardNegative: manufacturing_marking
├── Does its position shift or follow the path of an overhead wire?
│   └── YES ➔ Annotate as HardNegative: shadow_or_ambient_darkening
└── Does it have feathering, carbon soot texture, blistering, or melting?
    └── YES ➔ Attach fault: scorching_charring
```

### 2.2 Terminal Appearance: Corrosion vs. Dielectric Grease vs. Heat Tint
```
Is there abnormal color on the terminal or copper busbar?
├── Is the surface glossy, translucent, amber/yellow, or viscous?
│   └── YES ➔ Annotate as HardNegative: protective_coating (anti-oxidant grease)
├── Is the surface powdery, green (copper verdigris), or crusty rust?
│   └── YES ➔ Attach fault: surface_corrosion_oxidation
└── Is the copper or brass showing iridescent blue/purple/straw heat bands?
    └── YES ➔ Attach fault: thermal_discoloration
```

### 2.3 Wiring & Terminals: Exposed Conductor vs. Normal Strip Length
```
Is bare copper or aluminum conductor visible outside the terminal throat?
├── Is visible bare conductor < 2 mm (standard manufacturer tolerance)?
│   └── YES ➔ Mark as healthy (normal strip tolerance)
└── Is visible bare conductor > 3 mm or within 10 mm of an adjacent phase?
    └── YES ➔ Attach fault: exposed_uninsulated_conductor
```

---

## 3. Component Boundary Conventions

| Component Type | In-Box Boundary Guidance |
|---|---|
| **Multi-Pole Breakers (2-Pole / 3-Pole)** | If the poles are mechanically tied by an internal tie-bar or single handle, annotate as **ONE single bounding box** (`mcb` or `mccb`). If they are independent individual 1-pole breakers mounted side-by-side, annotate each pole as an individual box. |
| **Terminal Blocks on DIN Rail** | When multiple terminal blocks (e.g. 10 beige terminal slices) are grouped, annotate each individual wired terminal connection block as a separate `terminal` bounding box. |
| **DIN Rail Busbars** | Annotate the visible metallic bar span as `busbar`. If an insulating comb cover is present, annotate the cover and conductor assembly together. |
| **Contactor with Overload Relay** | If a motor starter comprises a magnetic contactor coupled to a thermal overload block, annotate the upper unit as `contactor` and the assembly or lower unit as `motor_starter`. |

---

## 4. Ambiguity Handling & Reviewer Disagreement

### 4.1 Ambiguity Flagging
When an annotator cannot conclusively identify a component class due to extreme camera angle, heavy dust layer, or unfamiliar foreign hardware:
- Set `quality: "occluded"` or add a note in `review_notes`.
- Do **NOT** force a speculative guess. Mark `insufficient_evidence: true` if applicable.

### 4.2 Multi-Reviewer Disagreement & Escalation Protocol
Every dataset batch undergoing validation requires a minimum 10% overlap sample dual-annotated by two independent reviewers:
1. **Consensus Requirement:** Bounding box Intersection over Union (IoU) $\ge 0.70$ and identical component class.
2. **Disagreement Resolution:** If Reviewer A and Reviewer B disagree on class or fault presence:
   - Sample enters `ReviewStatus.IN_REVIEW`.
   - Adjudication is performed by a **Certified Level II Thermographer** or **Master Electrician**.
   - The adjudicator's decision is final and recorded in the audit log.
