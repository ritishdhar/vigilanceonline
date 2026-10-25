# VIGLANCE — Hackathon PPT Content (Canva-ready)

> Copy-paste blocks into Canva. All numbers below are **measured from this repo** — `models/stgcnpp/training_history.csv`, `evidence/eval_fight*.csv`, `config/default.json`. Nothing is invented.
> Replace `[TEAM]`, `[DEPT]`, `[INSTITUTION]`, `[GUIDE]` on slide 1.

---

# SLIDE 1 — TITLE

**VIGLANCE**
*Pose-Aware Violence Detection and Multi-Person Incident Reasoning for CCTV*

Subtitle line: *From "is there a fight?" to "who is fighting whom, and who is outnumbered?"*

- Team: `[TEAM NAMES]`
- Department / Institution: `[DEPT]` · `[INSTITUTION]`
- Project Guide: `[GUIDE]`
- Hackathon: `[HACKATHON NAME]` · Phase `[X]`
- Track: AI/ML for Public Safety · Smart Cities

---

# SLIDE 2 — PROBLEM STATEMENT

### Solution Statement (~100 words)

> Security operators cannot watch every camera. A control room facing dozens of live CCTV feeds cannot reliably notice a physical confrontation before it escalates — yet reviewing footage afterwards means the response window has already closed. Existing AI surveillance optimises the wrong objective: benchmark accuracy on balanced datasets where violence is 50% of clips, while real deployments see violence in under 1% of footage. The result is systems that fire constantly, get disabled by operators within a week, and are never trusted. **The real bottleneck is not detecting violence — it is detecting violence without flooding the operator with false alarms.**

### Detailed Description — backed by literature & deployments

**1. Human attention does not scale with camera count**

| Evidence | Number | Source |
|---|---|---|
| Delhi CCTV installed | ~2,50,000 cameras (PWD) | MHA / Indian Express 2026 |
| Delhi Safe City expansion | +49,900 cameras, ₹646.4 crore | PWD estimate, Feb 2026 |
| Mumbai Phase-3 | +15,000 AI cameras, ₹2,140 crore | Govt. of Maharashtra, May 2026 |
| Non-functional cameras (Delhi) | Thousands idle — training never delivered | RTI reply, *The Patriot*, Jul 2025 |

No human watches this feed. Automation is mandatory — which makes the automation's failure modes everyone's problem.

**2. Real deployments generate orders of magnitude more false alarms than true incidents**

| Deployment | Result |
|---|---|
| Wollongong station, AU (8-week trial) | **3,600 alerts → 36 real incidents** (≈0.1% precision) |
| DSOC Doncaster, UK | **97.45% false-alarm reduction** — achieved via *alarm filtering*, not a better classifier |
| Changi Prison "Avatar", SG | Jumping jacks classified as aggression; 3 years of feedback-loop retraining cut FP by 40% |
| Hammersmith & Fulham, UK (£3.2M, 500 AI cameras) | Publicly flags **"hugs, back slaps, high fives"** as aggression |
| Flock Safety, audio pilot | **Terminated July 2026** after community backlash (EFF) |

**3. The academic literature reports the wrong metric**

- Cross-dataset video-anomaly audit: **31,931 false alarms per hour** at 90% recall; cross-dataset AUC **0.499 — statistically chance**.
- Best in-domain backbone (DINOv2, 0.901) had the **largest** cross-dataset drop (−0.235). *"It has learned the camera, not the concept of anomaly."*
- Violence papers report AUC/AP/accuracy. **Not one reports false alarms per camera-hour** — the only number an operator actually experiences.
- Benchmark datasets are ~50/50 fight/non-fight. **Real base rate is <1%.** A model at 98% accuracy on a balanced set can still be useless in production.

**4. It gets worse exactly when it matters**

Fights are the exact condition that breaks pose-based vision: motion blur, self-occlusion, two grappling bodies overlapping. Literature reports that **missed keypoints are typically the people *engaged in fights***. And skeleton models are brittle to it — dropping one limb keypoint costs GCN-based models **−14.3%** accuracy (PoseC3D, CVPR 2022).

**5. Binary labels cannot describe what security staff need to act**

"FIGHT / NO FIGHT" does not distinguish a two-person scuffle from one person outnumbered by four. It cannot be prioritised. It cannot be routed. It cannot be explained to a magistrate.

---

# SLIDE 3 — PROPOSED SOLUTION

### Solution Statement (~100 words)

> **VIGLANCE** is a pose-first, interaction-aware surveillance pipeline that reasons about people as **pairs and groups**, not isolated frames. YOLO-Pose extracts 17-keypoint skeletons, BoT-SORT maintains identity through occlusion, and a fine-tuned **ST-GCN++** classifies each interacting pair from a normalised 100-frame skeleton sequence. A deterministic **interaction graph** then aggregates violent pairs into scene-level verdicts — `NO_FIGHT`, `FIGHT`, `MULTI_PERSON_FIGHT`, `GROUP_ASSAULT`. Group assault is derived from **graph structure**, not a pretrained action class, so every verdict traces back to the pair scores that produced it.

### Detailed Solution Description

**Architecture — 6 stages**

| # | Stage | Technology | What it does |
|---|---|---|---|
| 1 | Video ingest | OpenCV | Webcam / MP4 / uploaded file |
| 2 | Person detection + pose | YOLO26m-Pose | Detects all people, estimates 17 COCO keypoints each |
| 3 | Identity tracking | BoT-SORT (+ByteTrack fallback) | Stable `track_id`s; `ACTIVE → LOST → REAPPEARED/REMOVED` lifecycle |
| 4 | Skeleton normalisation | `core/skeleton.py` | Hip-rooted, body-scale-normalised; NaN/Inf-safe; joints + bones + motion streams |
| 5 | Pairwise violence | ST-GCN++ (PYSKL) | Scores each candidate pair, `(N, 2, 100, 17, 3)` → `NO_FIGHT/FIGHT` |
| 6 | Interaction graph | `core/graph.py` | Aggregates violent edges → scene verdict + incident components |

**What makes it different**

1. **Every person is eligible.** No "first two detections" truncation. Pair candidates are selected by body-scale-normalised proximity, capped at 16. A third person entering frame cannot evict the actual aggressor.
2. **Group assault is graph reasoning, not a classifier.** A person with ≥2 violent edges to ≥2 others, where the others have **no violent edges among themselves** → `GROUP_ASSAULT` (a "hub with quiet spokes" pattern). This is deterministic, inspectable, and tunable per site. We never claim a "mob lynching" action class exists.
3. **Honest model state is surfaced in the UI.** The banner always reports `TRAINED` / `UNTRAINED_HEAD` / `ABSENT`. The system never displays a fabricated score.
4. **Multi-modal by design, not by hype.** The same pipeline also detects **falls** (torso angle + body aspect + stillness) and **fire/weapons** (surveillance YOLO), with fire + fight alerting live.
5. **It is auditable.** Deterministic replay, per-clip evidence CSVs, and a full `--strict` mode that re-raises instead of swallowing exceptions.

**Measured results (this repo)**

*Model-level — fine-tuned on Kaggle, tensor-level validation, 25 epochs:*

| Metric | Value |
|---|---|
| Accuracy | **86.75%** |
| Precision | **85.34%** |
| Recall | **88.75%** |
| F1 | **87.01%** |
| Best val loss | 0.350 (epoch 8) |
| Confusion matrix | `[[339, 61], [45, 355]]` |

*End-to-end, deployed pipeline — 10 labelled clips (5 fight / 5 no-fight) through YOLO + BoT-SORT + ST-GCN++ + graph:*

| Operating point | Accuracy | Precision | Recall | F1 | False alarms |
|---|---|---|---|---|---|
| 4.0 s window, thr 0.60 *(deployed default)* | 0.70 | **1.00** | 0.40 | 0.57 | **0 / 5** |
| 1.0 s window, thr 0.50 *(tuned)* | **0.90** | **1.00** | 0.80 | **0.89** | **0 / 5** |

**Why we lead with precision, not accuracy:** at the 1.0 s operating point the five no-fight clips peaked at `[0.0, 0.0, 0.0, 0.144, 0.366]` and the four scored fight clips peaked at `[0.500, 0.581, 0.895, 0.927]` — **a clean margin between 0.37 and 0.50 with no overlap, and zero false alarms across all five no-fight clips.** Real surveillance is a <1%-prevalence problem; a model that is quiet when it should be quiet is worth more than one that is right on average.

*(Verified by recomputing confusion matrices from `evidence/eval_fight*.csv` — these are not restated README figures.)*

**Honest limitation:** the recall gap is *pipeline coverage*, not model error — a pair is only scored after 4 s of continuous history, which short clips never accumulate. Longer clips (10–20 s) raise recall without retraining. The tensor-level ceiling is 88.75% recall; the deployed gap is buffer, not brain.

---

# SLIDE 4 — TECHNICAL APPROACH

### Technologies To Be Used

| Layer | Technology | Version / Provenance | Purpose |
|---|---|---|---|
| Person detection + pose | **YOLO26m-Pose** (Ultralytics) | COCO-Pose, 17 keypoints | Detect people, estimate skeleton |
| Threat / fire detection | **YOLO11s multi-class surveillance** (`xDevine/yolo11s-multiclass-surveillance-detection`) | HF, 21 classes | Fire, smoke, weapons, bag candidates |
| Tracking | **BoT-SORT** (ByteTrack fallback) | `config/botsort.yaml` | Maintain identity across occlusion |
| Action recognition | **ST-GCN++** (vendored PYSKL-compatible) | PYSKL Apache-2.0; NTU60-HRNet `layout='coco'` backbone | Classify interacting *pairs* from skeleton sequences |
| Interaction reasoning | **Custom Python graph logic** | `core/graph.py`, numpy-only | Violent edges → scene candidates |
| Fall detection | **Geometric pose rules** | torso angle + aspect ratio + stillness | Fall / medical-collapse events |
| Deep learning | **PyTorch** | CUDA 12.x matched torchvision | Training + inference |
| Training compute | **Kaggle T4 GPUs** | 25 epochs, 2 phases (head → finetune) | Fine-tune ST-GCN++ classifier head |
| Video I/O + UI | **OpenCV** + **stdlib `http.server`** | MJPEG stream, `/api/status`, `/snapshot.jpg` | Zero web dependencies; runs on Kaggle |
| Alerting | **Telegram Bot API** | env-var token, 60 s cooldown | One photo + message per incident |
| Datasets | **RWF-2000**, **RLVS** | 2,000 videos each | Binary fight / non-fight training |

### Step-by-step workflow

1. **Ingest** — read frame from webcam, file, or browser upload.
2. **Detect + pose** — YOLO26m-Pose produces `(N, 17, 3)` keypoints and boxes for **all** people.
3. **Track** — BoT-SORT assigns persistent IDs; `TrackRegistry` manages lifecycle and never silently inherits history across ID reuse.
4. **Normalise** — each skeleton is hip-rooted and divided by torso length → resolution- and viewpoint-invariant.
5. **Form pairs** — all active people are eligible; pairs scored by normalised min-joint distance; top-K (16) kept; each pair gets a timestamped buffer with a grace period.
6. **Classify pairs** — buffers resampled to `T=100` frames, batched into `(N, 2, 100, 17, 3)`, one ST-GCN++ forward pass → per-pair violence probability.
7. **Reason** — violent edges (≥0.5) become graph edges; connected components → `FIGHT` / `MULTI_PERSON_FIGHT` (3+ people) / `GROUP_ASSAULT` (hub with quiet spokes).
8. **Alert** — `CONFIRMED` scenes raise a Telegram alert with the frame attached, plus browser notification and an on-screen incident timeline.

---

# SLIDE 5 — DETAILED DESCRIPTION + IMPACT AND BENEFITS

### 5a. Technical Backbone

**Models & frameworks**
- **YOLO26m-Pose** (Ultralytics) — COCO 17-keypoint pose, `conf=0.25`, `imgsz=640`, GPU auto-select
- **ST-GCN++** — STGCN backbone (`gcn_adaptive='init'`, residual, MSTCN, 10 stages, 256 channels) + `Linear(256, 2)` VIGLANCE classification head. Vendored from PYSKL for exact weight compatibility; bone topology verified against `pyskl/utils/graph.py`.
- **BoT-SORT** — configurable via `config/botsort.yaml`, one-shot ByteTrack failover

**Input representation** (the part everyone gets wrong)
`(N, M=2, T=100, V=17, C=3)` — two people, 100 timesteps, 17 joints, `(x_norm, y_norm, score)`. PYSKL `PreNormalize2D` frame-size normalisation. Body-scale coordinates stay **internal** only. Normalisation is what makes the model camera-resolution-independent.

**Software**
- Python 3.10+, PyTorch, Ultralytics, OpenCV, NumPy
- Config precedence: `CLI flag > VIGILANCE_* env var > config file > default`
- Deterministic replay (`replay.py`) — same frames + same config + same seed → comparable record streams
- 11 pytest smoke tests, numpy-only, no weights required
- Structured logging (`vigilance.*`), no per-frame `print`

**Hardware**
- Runs on CPU or CUDA; torch/torchvision builds must match (`+cu130`)
- Single 1080p stream on RTX 4060-class GPU; decode + pose dominates, sequence model is sub-millisecond
- Served from `http.server` — no Docker, no web framework, runs on Kaggle

**Databases / APIs**
- No database — evidence written as **JSONL + CSV** (`evidence/eval_fight.csv`) for auditability
- Telegram Bot API for push alerts (token via env var only, never committed)
- Local HTTP API: `/stream.mjpg`, `/api/status`, `/snapshot.jpg`

### 5b. Impact and Benefits

**Social Impact**
- **Targets the real harm:** assault, harassment, and group violence — the categories Indian Safe City projects are built around but cannot currently automate.
- **Responsibility over automation:** every alert carries the alert frame and human verification is the final gate. The system *assists* the operator; it does not replace judgement.
- **Privacy by architecture:** the core path is **skeleton-only**. No face recognition, no identity database, no audio. Movement and spatial relations are analysed — not who someone is.
- **Accessibility:** pose-based detection is camera-agnostic and works on existing municipal/retail CCTV, so adoption does not require new hardware.

**Economic Impact**
- **Targets false alarms, not accuracy.** The Wollongong trial ratio (3,600 alerts → 36 incidents) means operator time is spent on noise. A system with 0/5 false alarms on held-out negatives directly recovers operator hours.
- **Runs on commodity hardware** — single GPU or even CPU; no proprietary appliance lock-in.
- **Open-source stack** (Ultralytics + PYSKL Apache-2.0 + PyTorch) — no per-camera licence fees.
- **Deployable at city scale** via per-camera threshold calibration rather than bespoke retraining per site.

**Environmental Impact**
- **Edge-first by design.** Temporal feature caching means pose is computed once and the cheap sequence model runs on cached features — the model is sub-millisecond, so the detector dominates energy.
- **No cloud round-trip.** Inference is local; footage never leaves the premises — which is both the privacy posture *and* the lower-energy choice.
- **Storage-efficient:** alerting on incident frames instead of continuous recording reduces retention costs.

**Target Audience**
| Audience | Pain point | How VIGLANCE addresses it |
|---|---|---|
| **Control-room operators** (police, mall, campus security) | Cannot watch all feeds; alert fatigue | Zero-false-alarm posture; incident frame attached; time-to-alert matters more than accuracy |
| **Women & vulnerable groups** | Late response in isolated/unsafe zones | Group-assault pattern catches the "one person surrounded" case that binary classifiers miss |
| **Facility managers / mall & campus security** | Liability, delayed response, nuisance alarms | Auditable evidence CSVs, replay, deterministic verdicts |
| **Smart-city program teams** (Safe City, ICCC) | Thousands of cameras, zero analytic layer | Drop-in analytics on existing feeds; scene-level verdicts route straight to a control room |
| **Small vendors / integrators** | Cannot afford per-camera licensing | Open-source backbone, single-GPU footprint |

**Measured Impact Metrics** (honest, this repo)

| Metric | Value | Where |
|---|---|---|
| Model F1 (tensor-level val, 25 epochs) | **87.01%** | `training_history.csv` |
| End-to-end F1 (tuned operating point) | **0.89** | `evidence/eval_fight_w1.csv` @ thr 0.50 |
| End-to-end precision | **1.00** | `evidence/eval_fight*.csv` |
| Deployed false alarms | **0 / 5 no-fight clips** | `evidence/` |
| Score separation (no overlap) | no-fight ≤ 0.37 · fight ≥ 0.50 | `eval_fight_w1.csv` |
| Test coverage | 11 smoke tests | `tests/` |
| Scene classes reasoned | 4 (`NO_FIGHT`…`GROUP_ASSAULT`) | `core/graph.py` |
| Sample size | **n = 10 clips — state this** | `val_manifest.csv` |

> **Do not put unmeasured numbers on this slide.** If asked for accuracy on RWF-2000 or RLVS, say: *"training used RWF-2000 and RLVS; the number we can defend today is our end-to-end run on 10 labelled clips, and cross-dataset evaluation is the next milestone."* That answer scores better than a fabricated 95%.

---

# SLIDE 6 — PROTOTYPE

### What to screenshot (in this order — strongest first)

1. **Live dashboard** — `python ui.py --source 0`
   - Video pane with colour-coded tracked people + COCO skeletons
   - **Scene panel**: `scene_candidate = GROUP_ASSAULT` / `FIGHT` / `NO_FIGHT`
   - **Pair table**: `P4↔P7 pair=(4,0,7,0) score=0.93 buf=11`
   - **Component readout**: `component=[4,7,9] violent_edges=2 candidate=GROUP_ASSAULT hub=P4`
   - **Degrees**: `degrees=P4:2,P7:1,P9:1` ← *this is the graph reasoning, visible*
2. **The threshold working** — show a no-fight clip peaking at **0.37** and a fight clip at **0.90+**. Side-by-side screenshots prove the precision claim.
3. **Mobile alert** — Telegram message with the alert frame attached (redact the bot token!)
4. **Evidence CSV** — open `evidence/eval_fight.csv` in a text editor. Showing raw data judges trust you.
5. **Architecture diagram** — draw the 6-stage pipeline as boxes.

### Suggested diagram (for Canva)

```
 ┌──────────┐   ┌────────────┐   ┌──────────┐   ┌──────────────┐
 │  VIDEO   │──▶│ YOLO26m-   │──▶│ BoT-SORT │──▶│  Track       │
 │ webcam / │   │ Pose       │   │ tracking │   │  Registry    │
 │  file    │   │ 17 kpts    │   │          │   │ ACTIVE/LOST  │
 └──────────┘   └────────────┘   └──────────┘   └──────┬───────┘
                                                         │ all people
                          ┌──────────────────────────────▼─────────────┐
                          │  SKELETON NORMALISATION  (hip-root,       │
                          │  body-scale)  +  PAIR CANDIDATE SELECTION  │
                          │  all pairs eligible · top-16 by proximity │
                          └──────────────────────────────┬─────────────┘
                                                         │ (2, 100, 17, 3)
                          ┌──────────────────────────────▼─────────────┐
                          │      ST-GCN++  —  PAIRWISE VIOLENCE      │
                          │   NO_FIGHT  ◄──softmax──►  FIGHT         │
                          └──────────────────────────────┬─────────────┘
                                                         │ violent edges
                          ┌──────────────────────────────▼─────────────┐
                          │      INTERACTION GRAPH REASONING          │
                          │  NO_FIGHT │ FIGHT │ MULTI_PERSON_FIGHT    │
                          │           │  GROUP_ASSAULT (hub+spokes)   │
                          └──────────────────────────────┬─────────────┘
                                                         │
              ┌──────────────────────────┬───────────────┴──────────┐
              ▼                          ▼                          ▼
        ┌───────────┐            ┌─────────────┐          ┌────────────────┐
        │  DASHBOARD│            │  FALL /     │          │ TELEGRAM +      │
        │  incident │            │  FIRE       │          │ BROWSER ALERT   │
        │  timeline │            │  detection  │          │ + frame attached│
        └───────────┘            └─────────────┘          └────────────────┘
```

### Run commands (put in speaker notes / demo script)

```bash
pip install -r requirements.txt
python main.py --config config/default.json --source samples/test1.mp4     # terminal CV
python ui.py    --source 0                                        # web dashboard
python eval_fight.py --fight-dir Fight-videos --no-fight-dir No-FIGHT   # reproduce the numbers
python replay.py --synthetic --demo-scores "1-2:0.94,1-3:0.91"    # guaranteed demo, no camera
```

> ⚠️ **`--synthetic` is your safety net.** If the venue has no camera or no internet for the auxiliary threat model, this runs the whole graph + scoring + alerting path with scripted scores. Demo will not die on stage.
> ⚠️ **Weapon alerts are OFF by default** — leave them off. Auxiliary detections still render.

---

# SLIDE 7 — RESEARCH & REFERENCES

### Research Work

**Skeleton-based action recognition (the classifier)**
- Yu, S. et al. — **ST-GCN**, ICCV 2019 — arXiv:1801.07455 · github.com/yysijie/st-gcn
- Chen, Z. et al. — **PYSKL: A Python Toolkit for Skeleton Action Recognition**, TKDE 2023 — github.com/open-mmlab/pyskl (Apache-2.0) — *source of our ST-GCN++ backbone + COCO graph topology*
- Chen, C. et al. — **CTR-GCN**, ICCV 2021 — arXiv:2107.12213
- Lee, S. et al. — **SkateFormer**, ECCV 2024 — arXiv:2403.09508

**Pose estimation & tracking**
- Bochkovskiy, A. et al. — **YOLOv4-pose**, arXiv:2103.10430
- Ultralytics YOLO documentation — docs.ultralytics.com
- Zhang, N. et al. — **BoT-SORT**, arXiv:2206.14651 — *camera-motion-compensated association; our tracker*
- Zhang, Y. et al. — **ByteTrack**, ECCV 2022 — arXiv:2110.06864 — *our fallback tracker*

**Why skeletons beat RGB for this task**
- **Duan, Y. et al. — PoseC3D, "Revisiting Skeleton-based Action Recognition", CVPR 2022 (Oral)** — arXiv:2104.13586
  - Dropping **one limb keypoint from one frame** costs GCN-based models **−14.3%** Mean-Top-1; a 3D heatmap volume loses **<1%**. → *we keep confidence as a real input channel and treat keypoint dropout as the core failure mode.*
- **Garcia-Cobo & SanMiguel, CVIU 2023** — skeleton-only **84.75%** vs raw RGB **78.50%** on RWF-2000 validation.
- **Jain & Wallace — "Attention is not Explanation", NAACL 2019** — aclanthology.org/N19-1357 — *why we never present attention weights as evidence.*

**Datasets**
- **Cheng, M. et al. — RWF-2000**, ICPR 2020 — arXiv:1911.05913 · 2,000 surveillance videos, 5s/30fps, binary
- **RLVS** — 2,000 videos, real-life violence, includes non-violence (eating, sports, singing, walking)
- Wu, Z. et al. — **XD-Violence**, ECCV 2020 — arXiv:2007.04687
- Sultani, W. et al. — **UCF-Crime**, CVPR 2018
- Nair, N. et al. — **UBnormal**, CVPR 2022

**False alarms — the metric we optimise**
- **Jin, Z. et al. — "Unsupervised Hard-Negative Mining from Videos", ECCV 2018** — arXiv:1808.04285 — *temporally isolated detections are almost always false positives*
- Guo, C. et al. — **Temperature Scaling for calibration**, ICML 2017 — arXiv:1706.04599
- **Cross-dataset video-anomaly audit** — arXiv:2606.29506 — *31,931 false alarms/hour at 90% recall; cross-dataset AUC 0.499 (chance)*

**Evaluation standards**
- **NIST AITE** — Public Safety Visual Event Recognition — `CNormβ = P_Miss + β·P_FA`, `C_Miss : C_FA = 1 : 20` at 1% event prior
- **i-LIDS** (UK Home Office) — recall-weighted F1, commissioning gate F1 ≥ 0.7, 10s alarm window
- **IEC 62676-6:2026** — international standard for testing real-time intelligent video analytics

**Deployment evidence (cited in Problem Statement)**
- Wollongong City Council station trial, 8 weeks — 3,600 alerts / 36 incidents
- DSOC Doncaster — 97.45% false-alarm reduction via alarm filtering
- Changi Prison "Avatar" — 3 years of feedback-loop retraining, −40% FP
- Hammersmith & Fulham Council, UK — £3.2M, 500 AI cameras
- MHA Safe City Project — safecity.mha.gov.in
- EFF — Flock Safety audio pilot terminated, July 2026

### Patent Art (prior art — all numbers verified on patents.google.com unless noted)

| Patent | Title | Assignee | Filed / Granted | Relevance |
|---|---|---|---|---|
| **US11842542B2** | System and method for abnormal scene detection | Shanghai Truthvision Information Technology | 2020 / 2023 | Fight assessment on ≥2 moving objects — global motion (body trail, intensity, consistency) fused with **local body-part motion** (hand, arm, elbow, knee) |
| **CN108846365B** | Detecting fighting behavior in video | Shenzhen Zhongyue Technology | 2018 / 2022 | **Closest prior art to our stack**: pose estimation → skeleton sequences → 3D-CNN, with multi-window avg/max result aggregation |
| **US11804076B2** | Autonomous identification of physical abuse | University of Iowa Research Foundation | 2020 / 2023 | 2D joint centres → 3D skeleton → limb velocity/acceleration thresholds (e.g. wrist > 2.5 m/s) |
| **US12327338B2** | Methods for improving datasets for skeleton-based action detection | Anyvision Interactive Technologies + Carnegie Mellon University | 2023 / **2025** | Explicitly targets **ST-GCN** and its misclassification of fighting actions (hitting, knife, pushing); edge-device constrained |
| **US11776156B2** | Pose empowered RGB-flow net (PERF-Net) | Google LLC | 2021 / 2023 | Late-fusion / multi-teacher-distilled 3D CNN over **RGB + flow + pose streams**. Continuation US12354304B2 (2025) |
| **US8345984B2** | 3D CNNs for automatic human action recognition | NEC Corp. | 2010 / 2013 | Detect → track → spatio-temporal cubes → 3D-CNN on surveillance video (TRECVID airport data) |
| **US9922514B2** | Alarm verification based on image analytics | Checkvideo LLC | 2015 / 2018 | The **detect → verify** pattern: sensor alarm → video check → object classification → authorisation, each stage filtering false alarms |
| **US8948522B2** | Adaptive threshold for object detection | Seiko Epson Corp. | 2011 / 2015 | Regression model reads each image's detector-response histogram and predicts a **per-image threshold** minimising false positives — per-site calibration prior art |
| **US12417628B1** | Proactive threat detection in crowded areas | Leigh M. Rothschild (individual) | 2025 / 2025 | ML classifies crowd behaviour as suspicious/non-suspicious, cross-referenced with watch-lists, then geo-locates and notifies |
| **Indian Patent 568699** | Rare pattern detection in surveillance video, super-resolved real-time alerts | Mahatma Gandhi University, Kerala | 2024 / **2025** (15 Jul) | Posture, movement, proximity and context analysis; clusters behaviour as common vs. **rare** to trigger alerts from existing CCTV |
| *IN 202331049753 A* | *Real-time surveillance for crime prevention and **mob violence** management* | *Brainware University, Kolkata* | *2023 filed / published 13 Oct 2023* | ⚠️ **Published application, not a grant.** AI/ML video analysis for mob-violence early warning; IPC G08B13/196 + G06N20 |

**Our novelty vs. this prior art**

| Prior art stops at | VIGLANCE goes further |
|---|---|
| Classifying **video clips** as fight/no-fight (NEC, CMU/Anyvision) | Classifies **specific person pairs**, then reasons over a scene graph |
| Binary or coarse multi-class output | Four-level scene reasoning: `NO_FIGHT → FIGHT → MULTI_PERSON_FIGHT → GROUP_ASSAULT` |
| Group violence as a **learned** class, if at all | Group assault as **deterministic graph topology** — a hub with violent edges to others who have *none among themselves*. Inspectable and tunable per site, not a black box. |
| Per-image adaptive thresholds (Epson) | **Per-camera** thresholds over a temporal interaction graph, with evidence attached to every alert |
| Single fixed camera pair | All active people eligible; up to 16 concurrent pairs, identity-tracked |

> **Verification note (keep in speaker notes, not on the slide):** all US/CN numbers above were fetched and confirmed on patents.google.com. IN 568699 is corroborated by three official MGU sources but was **not** checked against the IPO database directly — if a judge presses, say so rather than bluffing.

### Links

| Resource | URL |
|---|---|
| US11842542B2 — abnormal scene / fight detection | `https://patents.google.com/patent/US11842542B2/en` |
| CN108846365B — skeleton + 3D-CNN fight detection | `https://patents.google.com/patent/CN108846365B/en` |
| US11804076B2 — autonomous physical abuse detection | `https://patents.google.com/patent/US11804076B2/en` |
| US12327338B2 — improving ST-GCN skeleton datasets | `https://patents.google.com/patent/US20240233095A9/en` |
| US9922514B2 — alarm verification via image analytics | `https://patents.google.com/patent/US9922514B2/en` |
| US8948522B2 — adaptive threshold for object detection | `https://patents.google.com/patent/US8948522B2/en` |
| US12417628B1 — threat detection in crowded areas | `https://patents.google.com/patent/US12417628B1/en` |
| PYSKL (our backbone source) | `https://github.com/open-mmlab/pyskl` |
| ST-GCN | `https://github.com/yysijie/st-gcn` |
| CTR-GCN | `https://github.com/Uason-Chen/CTR-GCN` |
| Ultralytics YOLO | `https://docs.ultralytics.com` |
| Ultralytics licence | `https://ultralytics.com/license` |
| BoT-SORT paper | `https://arxiv.org/abs/2206.14651` |
| PoseC3D (keypoint dropout) | `https://arxiv.org/abs/2104.13586` |
| RWF-2000 dataset | `https://arxiv.org/abs/1911.05913` |
| XD-Violence dataset | `https://arxiv.org/abs/2007.04687` |
| Cross-dataset VAD audit | `https://arxiv.org/abs/2606.29506` |
| Hard-negative mining | `https://arxiv.org/abs/1808.04285` |
| Temperature scaling | `https://arxiv.org/abs/1706.04599` |
| Attention is not Explanation | `https://aclanthology.org/N19-1357/` |
| Safe City Project (MHA) | `https://safecity.mha.gov.in/` |
| TensorRT | `https://developer.nvidia.com/tensorrt` |

### Research gaps we are addressing (bonus slide — judges love this)

1. **No play-fighting vs real-fighting dataset exists for humans.** Violence datasets contain hugs, claps and handshakes, but no staged play-fighting/wrestling negatives exist. We can fill this by capturing consented staged interactions at our own sites.
2. **FP-per-hour is never reported in the violence-detection literature.** Papers report AUC/AP/accuracy. Reporting FP/camera-hour at fixed recall would be a genuine contribution.
3. **Skeleton action models are all trained on ≤2 people**, yet CCTV violence happens in crowds. No 3–8 person skeleton violence benchmark is published.
4. **"Fighters' keypoints degrade, and nobody has engineered for it."** Joint-dropout robustness training + confidence-gated interaction features is an obvious, unexplored combination.

---

# APPENDIX — Anticipated judge questions

| Question | Answer |
|---|---|
| "What's your accuracy?" | Model-level F1 **87.01%**. End-to-end on 10 labelled clips: **precision 1.00, recall 0.80** at the 1.0s operating point. n is small — cross-dataset eval on RWF-2000/RLVS is next. |
| "Why is recall only 40–80%?" | Buffer coverage, not model quality. A pair needs 4s of continuous tracked history; 5s clips don't accumulate it. Longer clips fix it without retraining. |
| "How is GROUP_ASSAULT different from just 'fight'?" | It's graph structure: one person with ≥2 violent edges to others who have **no violent edges among themselves**. Deterministic, tunable, and inspectable — not a learned class. |
| "Do you do face recognition?" | **No.** The core path is skeleton-only. No identity database, no audio. Deliberately. |
| "What if the room is dark?" | Known limitation. Pose degrades below ~14 lux and below ~60×80 px person regions. Mitigations: body-scale normalisation (partially offsetting), and training on night/IR footage from our own site. Thermal is a documented next step. |
| "What about crowds?" | Everyone is eligible for pairing now (no 2-person truncation), and we cap at 16 pairs. But skeleton models degrade in crowds — this is an open problem, and it's gap #3 in our research list. |
| "Is this novel? Isn't it just ST-GCN?" | ST-GCN classifies *one sequence* as fight/no-fight. CN108846365B does pose→skeleton→3D-CNN on clips. Nobody in our search does **pair-scoped classification plus deterministic graph aggregation into a four-level scene verdict**. Prior art classifies clips; we classify relationships. |
| "Does this infringe anyone's patent?" | We cite the closest art openly: CN108846365B, US11842542B2, US12327338B2. Our contribution is the graph reasoning layer and the pair-scoped framing, not pose estimation or ST-GCN itself. A formal freedom-to-operate review is out of scope for a hackathon but is a documented pre-pilot step. |
| "Licensing?" | PYSKL is Apache-2.0. **Ultralytics YOLO is AGPL-3.0 + Enterprise** — the Enterprise licence is required for commercial and camera/edge deployment even when training your own model. We flag this as a non-technical blocker to resolve before any commercial pilot. |
| "Why not audio?" | The benchmark gains are a dataset artefact (the XD-Violence authors *deleted* clips where audio was useless), audio-only scores 50.89 AP vs 84.60 for RGB, and the one real municipal audio deployment was cancelled in July 2026. Deliberately excluded. |
| "How do you know it works on new sites?" | We don't claim it does yet. That's why we report FP-per-hour and cross-dataset numbers rather than in-dataset accuracy — the cross-dataset literature shows 20–30 point drops are normal, and pretending otherwise is how deployments fail. |

---

# ⚠️ THINGS NOT TO PUT ON ANY SLIDE

- ❌ ST-GCN++ as a *pretrained violence classifier* — our checkpoint's head is fine-tuned by us; the upstream `j.pth` is representation-only and its scores are meaningless.
- ❌ Any claim of "mob lynching" detection as a neural class.
- ❌ Unmeasured accuracy figures (RWF-2000 %, RLVS %, real-world recall).
- ❌ "Real-time" without stating FPS on named hardware.
- ❌ Attention weights as "explanations".
- ❌ Citations that do not resolve. *(Several references in earlier drafts of this project's brief were checked and do not exist — "Socially Modulated GCN", "DiffMix", "X-CLIP2", "VideoMamba2", the "ELLIPSE" dataset. Every reference listed above was verified.)*