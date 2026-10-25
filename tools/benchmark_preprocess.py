"""Compare the audited Python-loop preprocessing with the optimized path."""

from pathlib import Path
import json
from statistics import median
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from core.demo import make_person
from core.skeleton import frame_normalize, normalize_person
from core.stgcn_adapter import preprocess_pair


def fixture():
    return [
        {
            "t": i / 30,
            "a": normalize_person(make_person(1, 40 + i * 0.2)),
            "b": normalize_person(make_person(2, 220 - i * 0.1)),
        }
        for i in range(121)
    ]


def reference(buffer):
    times = [entry["t"] for entry in buffer]
    grid = [times[-1] - 4 + (i + 0.5) * 4 / 100 for i in range(100)]
    sequence = [
        buffer[min(range(len(times)), key=lambda i: abs(times[i] - t))] for t in grid
    ]
    return np.stack(
        [
            np.stack(
                [
                    frame_normalize(
                        (entry[key].xy * entry[key].scale + entry[key].root).astype(
                            np.float32
                        ),
                        entry[key].confidence,
                        (240, 320),
                    )
                    for entry in sequence
                ]
            )
            for key in ("a", "b")
        ]
    )


def main():
    buffer = fixture()
    optimized = lambda value: preprocess_pair(value, 4, 100, img_hw=(240, 320))
    np.testing.assert_allclose(reference(buffer), optimized(buffer), rtol=0, atol=1e-6)
    measurements = {}
    for label, fn in (("reference", reference), ("optimized", optimized)):
        samples = []
        for _ in range(3):
            start = perf_counter()
            for _ in range(16):
                fn(buffer)
            samples.append(1000 * (perf_counter() - start))
        measurements[label + "_16_pairs_ms"] = round(median(samples), 3)
    measurements["equivalent"] = True
    print(json.dumps(measurements))


if __name__ == "__main__":
    main()
