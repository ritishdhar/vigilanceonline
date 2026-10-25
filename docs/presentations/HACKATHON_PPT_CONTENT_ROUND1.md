# VIGLANCE — First-Round Pitch Content

A concise, evidence-based version for a hackathon first-round deck. External deployment anecdotes and patent comparisons are omitted. Do not present the 200-clip evaluation below as completed work.

---

## Slide 1 — Title

# VIGLANCE
### Pose-aware violence detection with multi-person interaction reasoning

From a fight/no-fight score to an inspectable scene candidate for a human operator.

- Team: [TEAM]
- Institution: [INSTITUTION]
- Hackathon: [HACKATHON]
- Track: AI/ML for Public Safety

---

## Slide 2 — The Problem

Security teams may have more camera feeds than an operator can continuously watch. A short physical confrontation can be easy to miss, while a noisy alert system can overwhelm the people expected to respond.

**Our focus:** surface potentially violent interactions for human review, with evidence about which tracked people and pair scores contributed to the scene candidate.

VIGLANCE is an operator-assistance prototype. It does not identify people, determine guilt, or replace a human decision.

---

## Slide 3 — The Solution

VIGLANCE processes video as a sequence of tracked people and pairwise interactions:

1. Detect people and estimate 17 body keypoints.
2. Track people across frames.
3. Build a time window for each candidate pair.
4. Score the pair with a fine-tuned ST-GCN++ model.
5. Aggregate pair scores into an inspectable scene candidate.
6. Show the candidate, pair scores, and evidence in the dashboard.

Scene outputs include no fight, fight, multi-person fight, and group-assault candidate. The graph can suggest a pattern; it does not establish attacker or victim roles.

---

## Slide 4 — What We Built

### Prototype pipeline

| Stage | Component | Purpose |
|---|---|---|
| Video input | OpenCV | Read a webcam or video file |
| Pose | YOLO-Pose | Estimate body keypoints |
| Tracking | BoT-SORT | Keep track IDs over time |
| Pair history | Pair manager | Form and buffer candidate interactions |
| Fight scoring | Fine-tuned ST-GCN++ | Score two-person skeleton sequences |
| Scene reasoning | Interaction graph | Aggregate pair scores into a scene candidate |
| Review | Dashboard and optional alert | Display scores and incident evidence |

### Design choices

- Pair scores are inspectable and can be replayed.
- The model state is surfaced honestly when a trained checkpoint is unavailable.
- No face recognition is used.
- A synthetic demo mode exists; scripted scores must be labeled as a simulation.

---

## Slide 5 — Additional Safety Events

These modules extend the fight pipeline, but their validation status differs. Present them as separate event cues, not as one jointly validated model.

| Event | Prototype approach | Current status |
|---|---|---|
| Fall | Pose-based torso angle, body shape, and short-term stillness rules | Enabled by default; rule-based and not independently evaluated |
| Fire and smoke | Auxiliary surveillance object detector with detections required across frames | Alerts enabled when the auxiliary model loads; not evaluated on a labeled VIGLANCE test set |
| Weapon candidates | Auxiliary object detector with confidence and persistence checks | Candidates can be displayed; alerts are off by default because detections need review |
| Unattended bag candidate | Bag detection plus distance from tracked people and persistence | Candidate logic exists, but alerts are off by default; current logic does not establish the bag's owner or require a long stationary interval |

**Operator wording:** “possible fall,” “fire/smoke candidate,” “weapon candidate,” and “unattended-bag candidate.” These cues support review; they do not confirm an emergency or threat.

---

## Slide 6 — Early Evaluation

**Current evidence: 10 labeled video clips — 5 fight and 5 non-fight. This is a small prototype check, not a real-world accuracy estimate.**

| Setting | Fight clips flagged | Non-fight alerts | Clips with any model score |
|---|---:|---:|---:|
| Configured 4-second window, threshold 0.60 | 2 of 5 | 0 of 5 | 4 of 10 |
| Exploratory 1-second window, threshold 0.50 | 4 of 5 | 0 of 5 | 6 of 10 |

**How to read this:** at the exploratory setting, three non-fight clips and one fight clip produced no pair score. A clip with no score is unassessed by the pair classifier; it should not be described as a confident no-fight prediction. The threshold and window results need testing on a larger, held-out set.

The classifier also reached an internal tensor-level validation F1 of 0.87 on a balanced validation set. This is separate from the end-to-end video result and does not establish cross-camera performance.

---

## Slide 7 — Prototype Demo

Show these in order:

1. The live or recorded video with tracked people and pose overlays.
2. The pair table with candidate IDs, score, and buffer status.
3. The scene panel showing how pair scores contribute to a scene candidate.
4. The evidence CSV or replay view.
5. If using synthetic mode, label it clearly as a scripted demonstration.

### Demo commands

    python ui.py --source 0
    python eval_fight.py --fight-dir Fight-videos --no-fight-dir No-FIGHT
    python replay.py --synthetic --demo-scores "1-2:0.94,1-3:0.91"

---

## Slide 8 — Research Basis and Next Validation

### Planned work — not yet completed

- Expand the evaluation to 200+ diverse clips, with positive examples and hard negatives for each event type. This is a target, not completed testing.
- Split by source video and hold out cameras or locations so the final test is independent of threshold tuning.
- Report per-event recall and precision, unscored coverage, detection delay, and false alerts per camera-hour.
- Test different window lengths, lighting, distance, occlusion, camera motion, and crowded scenes.
- Run a shadow-mode pilot where people review alerts before any operational response.

### Success means

The system should provide useful, timely review candidates without overwhelming operators. We will claim deployment readiness only after it succeeds on independent camera footage and meets an agreed alert-burden target.

### Selected research references

- **Weapon detection:** Bhatti, Khan, Aslam, and Fiaz (2021), “Weapon Detection in Real-Time CCTV Videos Using Deep Learning,” *IEEE Access*, 9, 34366–34382. [DOI](https://doi.org/10.1109/ACCESS.2021.3059170). Discusses CCTV-specific difficulties including viewing angle and occlusion; its reported results are not VIGLANCE results.
- **Fire and smoke:** Hosseini, Hashemzadeh, and Farajzadeh (2022), “UFS-Net: A unified flame and smoke detection method for early detection of fire in video surveillance applications using CNNs,” *Journal of Computational Science*, 61, 101638. [DOI](https://doi.org/10.1016/j.jocs.2022.101638). Studies joint flame/smoke recognition and temporal voting.
- **Unattended baggage:** Lin, Chen, Chen, Lin, and Hung (2015), “Abandoned Object Detection via Temporal Consistency Modeling and Back-Tracing Verification for Visual Surveillance,” *IEEE Transactions on Information Forensics and Security*, 10(7), 1359–1370. [DOI](https://doi.org/10.1109/TIFS.2015.2408263). Uses temporal evidence and owner trajectory; this is more complete than a single-frame bag detection.
- **Unattended-object evaluation:** Luna et al. (2018), “Abandoned Object Detection in Video-Surveillance: Survey and Comparison,” *Sensors*, 18(12), 4290. [DOI](https://doi.org/10.3390/s18124290). Defines abandonment using both object stationarity and whether the owner is nearby, and discusses long-term and scene-change challenges.
- **Pose-based fall detection:** Raza, Yousaf, Ahmad, Velastin, and Viriri (2025), “Human fall detection using pose estimation: From traditional machine learning to vision transformers,” *Engineering Applications of Artificial Intelligence*, 143, 109809. [DOI](https://doi.org/10.1016/j.engappai.2024.109809). Evaluates pose-based approaches on UR-Fall, UP-Fall, and Le2i datasets.

These papers motivate design and evaluation choices. Their benchmark results do not validate VIGLANCE’s auxiliary model or rule-based fall and bag modules.

---

## Speaker Notes — Claims to Keep Accurate

- The completed end-to-end evidence in this project is 10 clips, not 200.
- The 200+ clip figure is a proposed next evaluation milestone.
- The 1-second result is exploratory; the configured default uses a 4-second window.
- Fall and fire/smoke modules are enabled in the current configuration, but do not have a labeled VIGLANCE evaluation in this deck.
- Weapon alerts and unattended-bag alerts are disabled by default; describe them as candidates, not validated alerts.
- Research papers support the problem and methods; never present their published scores as VIGLANCE scores.
- State how many clips produced model scores whenever showing clip-level metrics.
- Do not call a graph pattern a confirmed assault or infer who started a fight.
- The source video files are not included in this checkout, so the saved evidence CSVs alone cannot reproduce the video evaluation.

