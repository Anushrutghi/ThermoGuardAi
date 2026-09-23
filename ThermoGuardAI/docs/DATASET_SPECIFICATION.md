# ThermoGuard AI — Dataset Specification & Taxonomy

**Document Version:** 1.0.0  
**Status:** Canonical Engineering Specification  
**Scope:** Object Detection & Multi-Label Visible Fault Recognition  
**Last Updated:** September 2026  

---

## 1. Purpose & Core Principles

ThermoGuard AI requires a high-fidelity dataset for training YOLO object detectors and visual fault classifiers. To prevent dangerous false positives and ungrounded diagnostics, this specification establishes four non-negotiable principles:

1. **Decouple Component Identity from Visible Fault Evidence:**
   Object detection networks identify *what* the component is (e.g. `circuit_breaker`, `contactor`, `terminal`). Visible fault models identify *what physical surface evidence is present* (e.g. `thermal_discoloration`, `surface_corrosion_oxidation`). They are never merged into hybrid classes like `"faulty_breaker"`.
2. **Multi-Label Visual Findings:**
   A single component may exhibit multiple distinct visible anomalies simultaneously (e.g. an over-stripped conductor entering a corroded terminal). The annotation schema supports arbitrary lists of visible fault labels per component.
3. **No "Loose Connection" from RGB Rule:**
   An ordinary visual photograph cannot ascertain whether an electrical screw terminal is loose. A gap between a lug and terminal may be an insulation gap, or a mechanically loose terminal may look completely flush. Therefore, `verified_loose_connection` is strictly gated behind an audited verification procedure (`calibrated_torque_test`, `micro_ohmmeter_test`, or `thermal_rise_under_load`). Ordinary visual findings are labeled strictly as `mechanical_misalignment` or `exposed_uninsulated_conductor`.
4. **Distinguish Surface Discoloration from Electrical Failure:**
   Surface rust on a steel chassis or mild copper patina on a busbar is often cosmetic; severe carbon charring or pitting on a contactor pole indicates electrical failure. Annotations separate cosmetic oxidation from destructive thermal scorching.

---

## 2. Taxonomy Definitions

### 2.1 Component Detection Classes (17 Canonical Classes)

| Class Index | Class Identifier | Scope & Visual Boundaries |
|---|---|---|
| `0` | `circuit_breaker` | General low-voltage circuit breaker (molded housing, toggle handle, terminal lugs). |
| `1` | `mcb` | Miniature Circuit Breaker (typically 18mm DIN rail width per pole). |
| `2` | `mccb` | Molded Case Circuit Breaker (industrial heavy-duty frame, rotary or heavy toggle). |
| `3` | `rccb` | Residual Current Circuit Breaker / GFCI / RCD (includes test button and trip indicator). |
| `4` | `fuse` | Cartridge fuse, ceramic fuse holder, or NH-style blade fuse block. |
| `5` | `relay` | Control relay, timing relay, or solid-state relay module with socket base. |
| `6` | `contactor` | Magnetic contactor / motor switch with visible line and load terminal blocks. |
| `7` | `busbar` | Rigid copper or aluminum electrical distribution bar (insulated or uninsulated). |
| `8` | `terminal` | Screw terminal, cage clamp, distribution block, or neutral/ground bar connection point. |
| `9` | `cable` | Insulated electrical conductor, multi-core feeder wire, or conduit entry bundle. |
| `10` | `transformer` | Step-down / control power transformer or toroidal current transformer (CT). |
| `11` | `motor_starter` | Manual motor protector, combination starter, or soft starter assembly. |
| `12` | `disconnect_switch` | Rotary isolator switch, knife switch, or main incoming disconnect handle. |
| `13` | `power_supply` | DIN-rail mounted 24V/12V DC switch-mode power supply (SMPS). |
| `14` | `indicator_light` | Pilot lamp, illuminated pushbutton, or panel status LED. |
| `15` | `panel_door` | Metal or polycarbonate outer enclosure door / inner deadfront panel shield. |
| `16` | `warning_label` | Arc flash hazard label, high-voltage warning sticker, or panel schedule placard. |

---

### 2.2 Visible Fault Evidence Classes (Multi-Label)

| Fault Identifier | Physical Description | Verification Requirements |
|---|---|---|
| `thermal_discoloration` | Heat tinting (straw, purple, blue) on metal conductors, or yellowing/browning/crazing of plastic housings. | Visual confirmation of localized chromatic shift relative to unaffected adjacent components. |
| `scorching_charring` | Carbonized soot deposits, localized burn marks, or melted plastic material resulting from open combustion or severe heat. | Visual evidence of carbonization; distinct from surface dust. |
| `arc_damage_pitting` | Molten metal spatter, small copper beads, or electrical erosion pits on terminals or contacts. | Visual magnification showing localized cratering or fused metallic droplets. |
| `surface_corrosion_oxidation` | Red rust on ferrous hardware, green verdigris on copper, or white powdery chalking on aluminum. | Visible chemical degradation; note whether structural or superficial. |
| `exposed_uninsulated_conductor` | Over-stripped wire length extending beyond terminal throat, nicked insulation, or uninsulated live metal accessible to finger touch. | Visual identification of bare copper/aluminum beyond enclosure barrier. |
| `physical_casing_damage` | Cracked or chipped housing, broken toggle lever, broken DIN rail latch, or cracked arc chute. | Visual mechanical fracture. |
| `missing_deadfront_cover` | Unfilled breaker slot without blanking plate, exposed live busbar opening, or missing inner cover. | Direct open aperture into live enclosure interior. |
| `mechanical_misalignment` | Breaker visibly canted on DIN rail, loose mounting clip, or uneven switch seating. | Geometric deviation $> 5^\circ$ from rail alignment. |
| `foreign_object_debris` | Accumulation of conductive metal shavings, drilling swarf, cobwebs, rodent intrusion, or heavy industrial dust. | Extraneous physical material inside electrical enclosure. |
| `moisture_liquid_ingress` | Water droplets, active condensation, dried water flow trails, or mineral tide marks. | Physical liquid or dry residue indicative of fluid intrusion. |
| `verified_loose_connection` | Mechanically under-torqued or loose terminal screw / lug. | **MANDATORY AUDIT PROCEDURE:** Cannot be assigned from visual RGB alone. Requires calibrated torque wrench test, micro-ohmmeter resistance measurement, or confirmed thermal rise under load. |

---

## 3. Hard Negatives & Edge Case Catalog

To prevent models from triggering false alarms, the dataset deliberately incorporates high quantities of hard negative examples:

1. **Manufacturing Markings:**
   - Factory ink stamps, QC check marks, date codes, and molded part numbers often present as dark spots.
   - *Guidance:* Annotated as `HardNegativeType.manufacturing_marking`; must not be labeled as scorching or damage.
2. **Shadows & Wiring Darkening:**
   - Shadows cast by thick wiring bundles or cabinet frame edges often mimic localized burn marks.
   - *Guidance:* Annotated as `HardNegativeType.shadow_or_ambient_darkening`.
3. **Protective Coatings & Grease:**
   - Anti-corrosion dielectric grease, conformal silicone coatings, and copper antioxidant paste resemble moisture or discoloration.
   - *Guidance:* Annotated as `HardNegativeType.protective_coating`.
4. **Harsh Specular Glare:**
   - High-intensity flash reflections on polished breaker handles mimic arc flash pitting or molten metal beads.
   - *Guidance:* Annotated as `HardNegativeType.reflection_glare`.
5. **Non-Electrical Structural Hardware:**
   - Cable zip ties, DIN rail end stops, grounding braids, and mounting screws must not be confused with active terminals.

---

## 4. Grouped Hierarchical Splits (Zero Data Leakage)

### 4.1 The Video Frame Leakage Problem
In electrical inspections, cameras often record continuous 30 FPS video or burst photos of a single panel. Splitting individual frames randomly across train, validation, and test causes severe **data leakage**:
- The model memorizes the exact dust particles, wiring routing, and camera angles of a panel in `train` and achieves artificially inflated $>99\%$ mAP on `val`, but fails catastrophically when deployed to an unseen panel in production.

### 4.2 Grouping Invariant
The dataset enforces strict hierarchical grouping:
$$\text{Partition}(\text{Sample}) = f(\text{site\_id}, \text{panel\_id})$$
- **All video frames, burst photos, and temporal inspections of a physical panel belong exclusively to one split.**
- Target split proportions:
  - **Train:** $\approx 70\%$ of unique panels
  - **Validation:** $\approx 15\%$ of unique panels
  - **Test:** $\approx 15\%$ of unique panels
- Manifest validation rejects any dataset configuration where a `panel_id` is detected across multiple splits.

---

## 5. Provenance & Ingestion Governance

### 5.1 Metadata Requirements per Sample
Every ingested sample record in `training/manifest_schema.py` must track:
- `site_id`: Installation site/facility code.
- `panel_id`: Physical electrical switchboard identifier.
- `session_id`: Unique recording session identifier.
- `camera_model`: Hardware optical sensor.
- `timestamp`: UTC ISO 8601 capture time.
- `data_rights`: Commercial licensing clearance (`internal_inspection`, `customer_cleared`, `open_dataset`).
- `sha256`: Cryptographic content hash for immutability.
- `dhash`: 64-bit perceptual difference hash for duplicate/near-duplicate review.

### 5.2 Review Queue Gate
- Newly ingested images and video frames enter the `ReviewQueue` as `PENDING_REVIEW`.
- **Training Gate:** Only samples marked `APPROVED` by an authorized reviewer and having `annotation_status == ANNOTATED` are allowed into training exports.
- **Production Isolation:** Field inspection evidence captured by end-users does **NOT** automatically enter training datasets without explicit client consent and administrative authorization.
