"""End-to-end fight accuracy check on labeled video clips.

Runs each clip through the full VIGLANCE pipeline (YOLO + tracker +
PairTracks + trained ST-GCN++ head) and scores clip-level fight/no-fight
from the max pair probability. Reports accuracy, precision/recall/F1,
confusion matrix, and an F1-optimal threshold suggestion.

Usage:
    # labeled folders (writes the manifest for you, then evaluates it)
    python eval_fight.py --fight-dir Fight-videos --no-fight-dir No-FIGHT

    # explicit manifest
    python eval_fight.py --manifest val_manifest.csv --fight-checkpoint models/stgcnpp/best_fight_stgcnpp_v1.pth

Manifest CSV columns: video_path,label   (label: 1=fight, 0=no-fight)

This evaluates the complete detector/tracker/model path on the supplied
manifest. Accuracy claims require representative labeled data and a test
set separate from threshold calibration.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def build_manifest(fight_dir, no_fight_dir, manifest_path):
    """Write video_path,label rows from two labeled folders."""
    import csv as _csv

    def _videos(folder):
        if not folder:
            return []
        ext = (".mp4", ".avi", ".mov", ".mkv", ".mpeg", ".mpg", ".webm", ".wmv")
        base = Path(folder)
        if not base.is_dir():
            raise FileNotFoundError(f"not a directory: {folder}")
        return sorted(
            p for p in base.rglob("*") if p.is_file() and p.suffix.lower() in ext
        )

    rows = [(str(p.resolve()), 1) for p in _videos(fight_dir)]
    rows += [(str(p.resolve()), 0) for p in _videos(no_fight_dir)]
    if not rows:
        raise FileNotFoundError(
            f"no videos found under {fight_dir!r} / {no_fight_dir!r}"
        )
    out = Path(manifest_path)
    if out.parent and str(out.parent):
        out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = _csv.writer(fh)
        writer.writerow(["video_path", "label"])
        writer.writerows(rows)
    print(
        f"manifest -> {out} ({sum(1 for _, l in rows if l == 1)} fight, "
        f"{sum(1 for _, l in rows if l == 0)} no-fight)"
    )
    return str(out)


def score_clip(pipeline, cap, fps, max_frames=0):
    """Run one clip; return (max fight prob, scored pair-frames, coverage).

    coverage explains a 0.000 score: a pair needs a continuous history
    (``fight.window_seconds``) before the model ever sees it, so tracker ID
    churn on short clips can leave a clip structurally unscored.
    """
    best = 0.0
    scored_pairs = 0
    max_span = 0.0
    distinct_pairs = set()
    import cv2
    import math
    from core.source_utils import CaptureClock

    expected_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    expected_frames = (
        int(expected_frames)
        if math.isfinite(expected_frames) and expected_frames > 0
        else 0
    )
    idx = 0
    clock = CaptureClock(False, fps)
    assessment_errors = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if max_frames and idx >= max_frames:
            break
        res = pipeline.process(
            frame, timestamp=clock.timestamp(idx, cap.get(cv2.CAP_PROP_POS_MSEC))
        )
        if getattr(res, "fight_status", "") == "ERROR":
            assessment_errors.append(idx)
        idx += 1
        for key in res.pair_keys:
            edge = pipeline.graph.edges.get(key)
            if edge is not None and edge.score is not None:
                scored_pairs += 1
                best = max(best, float(edge.score))
        for track in pipeline.pair_manager.open_tracks():
            distinct_pairs.add(track.key)
            if len(track.buffer) >= 2:
                span = float(track.buffer[-1]["t"]) - float(track.buffer[0]["t"])
                max_span = max(max_span, span)
    coverage = {
        "max_pair_span_sec": round(max_span, 2),
        "distinct_pairs": len(distinct_pairs),
        "assessment_error_frames": assessment_errors,
        "decode_error": idx == 0
        or (expected_frames > idx and not (max_frames and idx >= max_frames)),
    }
    coverage.update(pipeline.track_diagnostics.summary())
    return best if scored_pairs else None, scored_pairs, coverage


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="VIGLANCE end-to-end fight accuracy")
    ap.add_argument("--manifest", default="evidence/val_manifest.csv")
    ap.add_argument("--fight-checkpoint", default=None)
    ap.add_argument("--config", default="config/default.json")
    ap.add_argument(
        "--fight-dir",
        default=None,
        help="folder of fight clips (builds --manifest from it)",
    )
    ap.add_argument(
        "--no-fight-dir",
        default=None,
        help="folder of non-fight clips (builds --manifest from it)",
    )
    ap.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="override the configured graph threshold",
    )
    ap.add_argument(
        "--window-seconds",
        type=float,
        default=None,
        help="override fight.window_seconds (pair history needed "
        "before a pair is scored); the 4.0s deployed default "
        "is unreachable on short clips with ID churn",
    )
    ap.add_argument(
        "--max-frames", type=int, default=0, help="cap frames per clip (0 = whole clip)"
    )
    ap.add_argument("--evidence", default="evidence/eval_fight.csv")
    ap.add_argument(
        "--allow-partial",
        action="store_true",
        help="write a partial report when clips cannot be opened (exit remains nonzero)",
    )
    args = ap.parse_args(argv)

    import cv2
    from config import VigilanceConfig
    from core.logging_utils import setup_logging
    from core.pipeline import Pipeline

    setup_logging("WARNING")
    cfg = VigilanceConfig.from_file(args.config)
    cfg.from_env()
    if args.fight_checkpoint is not None:
        cfg.fight_cfg.model.checkpoint = args.fight_checkpoint
    # Select once; do not autoload a different model before the override.
    cfg.fight_cfg.model.autoload = False
    cfg.enable_fire = cfg.enable_threats = cfg.enable_fall = cfg.enable_bag = False
    cfg.validate()
    threshold = (
        args.threshold
        if args.threshold is not None
        else cfg.fight_cfg.graph.violence_edge_threshold
    )
    if args.window_seconds is not None:
        import math

        if not math.isfinite(args.window_seconds) or args.window_seconds <= 0:
            ap.error("--window-seconds must be > 0")
        cfg.fight_cfg.window_seconds = float(args.window_seconds)
        print(
            f"window_seconds override: {cfg.fight_cfg.window_seconds:.2f}s "
            f"(temporal_length stays {cfg.fight_cfg.temporal_length})"
        )
    import math

    if not math.isfinite(threshold) or not 0 < threshold < 1:
        ap.error("--threshold must be finite and in (0,1)")
    cfg.validate()

    manifest_path = Path(args.manifest)
    if args.fight_dir or args.no_fight_dir or not manifest_path.exists():
        if not args.fight_dir and not args.no_fight_dir:
            ap.error(
                f"manifest {args.manifest} not found; pass --fight-dir "
                f"and --no-fight-dir to build one"
            )
        build_manifest(args.fight_dir, args.no_fight_dir, manifest_path)

    rows = []
    manifest_path = Path(args.manifest).resolve()
    with manifest_path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            video_path = Path(row["video_path"])
            if not video_path.is_absolute():
                video_path = manifest_path.parent / video_path
            rows.append((str(video_path), int(row["label"])))
            if rows[-1][1] not in (0, 1):
                ap.error("manifest labels must be 0 or 1")
    if not rows:
        print("empty manifest")
        return 2

    per_clip = []
    failed = []
    for path, label in rows:
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            cap.release()
            failed.append(path)
            print(f"ERROR: cannot open {path}")
            if not args.allow_partial:
                return 2
            continue
        fps = cap.get(cv2.CAP_PROP_FPS) or cfg.fps
        try:
            pipe = Pipeline(config=cfg)
            if not pipe.pair_adapter.is_ready:
                loaded = pipe.load_fight_model(cfg.fight_cfg.model.checkpoint)
                if loaded is None:
                    print("ABORT: selected checkpoint could not be loaded")
                    return 2
            if not pipe.pair_adapter.head_trained:
                print("ABORT: checkpoint absent or has no trained fight head")
                return 2
            score, n, coverage = score_clip(pipe, cap, fps, args.max_frames)
        finally:
            cap.release()
        if coverage.get("decode_error"):
            failed.append(path)
            print(f"ERROR: incomplete decoding for {path}")
            if not args.allow_partial:
                return 2
            continue
        assessed = n > 0 and not coverage.get("assessment_error_frames")
        pred = int(score >= threshold) if assessed else None
        if not assessed:
            score = None
        per_clip.append((path, label, score, n, pred, coverage))
        print(
            f"{os.path.basename(path)} label={label} "
            f"max_pair={score if score is not None else 'UNKNOWN'} pairs={n} pred={pred} "
            f"ids={coverage['unique_ids']} "
            f"avg_people={coverage['avg_people_per_frame']:.2f} "
            f"max_span={coverage['max_pair_span_sec']:.2f}s"
        )

    if not per_clip:
        print("ABORT: no manifest videos could be evaluated")
        return 2

    metric_clips = [clip for clip in per_clip if clip[4] is not None]
    unknown = [path for path, _, _, _, pred, _ in per_clip if pred is None]
    tp = sum(1 for _, y, _, _, p, _ in metric_clips if y == 1 and p == 1)
    tn = sum(1 for _, y, _, _, p, _ in per_clip if y == 0 and p == 0)
    fp = sum(1 for _, y, _, _, p, _ in per_clip if y == 0 and p == 1)
    fn = sum(1 for _, y, _, _, p, _ in per_clip if y == 1 and p == 0)
    n = len(per_clip)
    scored = sum(npairs > 0 for _, _, _, npairs, _, _ in per_clip)
    acc = (tp + tn) / len(metric_clips) if metric_clips else None
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    print(
        f"\nthreshold={threshold} n={n} assessed={len(metric_clips)} acc={acc} "
        f"prec={prec:.4f} rec={rec:.4f} f1={f1:.4f}"
    )
    print(f"confusion [[TN={tn}, FP={fp}], [FN={fn}, TP={tp}]]")
    unscored = [
        os.path.basename(p) for p, _, _, npairs, _, _ in per_clip if npairs == 0
    ]
    print(
        f"clips with any scored pair frame: {scored}/{n}; "
        f"unscored (pair history never reached "
        f"{cfg.fight_cfg.window_seconds:.1f}s): {unscored or 'none'}"
    )

    scored_values = sorted({s for _, _, s, _, _, _ in metric_clips})
    best = (threshold, f1)
    for t in scored_values:
        ttp = sum(1 for _, y, s, _, _, _ in metric_clips if y == 1 and s >= t)
        tfp = sum(1 for _, y, s, _, _, _ in metric_clips if y == 0 and s >= t)
        tfn = sum(1 for _, y, s, _, _, _ in metric_clips if y == 1 and s < t)
        pr = ttp / (ttp + tfp) if ttp + tfp else 0.0
        rc = ttp / (ttp + tfn) if ttp + tfn else 0.0
        ff = 2 * pr * rc / (pr + rc) if pr + rc else 0.0
        if ff > best[1]:
            best = (t, ff)
    print(
        f"suggested threshold={best[0]:.3f} (F1={best[1]:.4f}); "
        f"re-run with --threshold {best[0]:.3f} to apply."
    )
    os.makedirs(os.path.dirname(args.evidence) or ".", exist_ok=True)
    with open(args.evidence, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "video_path",
                "label",
                "max_pair_score",
                "scored_pairs",
                "pred",
                "unique_ids",
                "avg_people_per_frame",
                "max_pair_span_sec",
            ]
        )
        for path, label, score, npairs, pred, cov in per_clip:
            w.writerow(
                [
                    path,
                    label,
                    round(score, 4) if score is not None else "",
                    npairs,
                    pred,
                    cov["unique_ids"],
                    cov["avg_people_per_frame"],
                    cov["max_pair_span_sec"],
                ]
            )
    print(f"per-clip evidence -> {args.evidence}")
    report = {
        "manifest_clips": len(rows),
        "evaluated_clips": n,
        "failed_clips": failed,
        "scored_clips": scored,
        "unassessed_clips": unknown,
        "complete": not failed and not unknown,
        "accuracy": acc,
        "precision": prec,
        "recall": rec,
        "f1": f1,
        "threshold": threshold,
    }
    import json

    Path(args.evidence + ".summary.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return 2 if failed or unknown else 0


if __name__ == "__main__":
    raise SystemExit(main())
