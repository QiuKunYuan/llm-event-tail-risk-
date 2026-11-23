"""Measure synthetic daily-batch compute; never a financial efficacy experiment."""
import argparse
import json
import resource
import time
from pathlib import Path
import torch
from research_model import DisclosureModel


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--nodes", type=int, default=512)
    p.add_argument("--steps", type=int, default=3)
    p.add_argument("--device", default="cpu")
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if a.nodes < 2 or a.steps < 1:
        raise ValueError("Need at least two nodes and one measured step")
    torch.manual_seed(42); torch.set_num_threads(4)
    n = a.nodes; d = torch.device(a.device)
    b = {"seq": torch.randn(n, 60, 5, device=d), "seq_mask": torch.ones(n, 60, 5, dtype=torch.bool, device=d),
         "events": torch.randn(n, 8, 128, device=d), "event_mask": torch.ones(n, 8, dtype=torch.bool, device=d),
         "event_quality": torch.ones(n, 8, device=d), "event_age": torch.ones(n, 8, device=d),
         "edges": torch.stack((torch.arange(n, device=d), torch.arange(n, device=d) // 4)),
         "edge_share": torch.full((n,), .1, device=d), "edge_age": torch.ones(n, device=d),
         "graph_age": torch.ones(n, device=d), "label": (torch.arange(n, device=d) % 10 == 0).float()}
    model = DisclosureModel(features=5).to(d)
    optimizer = torch.optim.AdamW(model.parameters(), lr=.001)
    durations = []
    for step in range(a.steps + 1):
        if d.type == "cuda": torch.cuda.synchronize()
        start = time.perf_counter()
        optimizer.zero_grad(); loss = model.loss(model(b), b["label"]); loss.backward(); optimizer.step()
        if d.type == "cuda": torch.cuda.synchronize()
        if step: durations.append(time.perf_counter() - start)
    report = {"scope": "synthetic_functional_compute_only", "nodes": n, "edges": n, "features": 5,
              "event_dimension": 128, "measured_steps": a.steps, "seconds_per_step": durations,
              "mean_seconds_per_step": sum(durations) / len(durations), "device": str(d),
              "torch": torch.__version__, "peak_process_RSS_MiB": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
              "peak_CUDA_MiB": torch.cuda.max_memory_allocated() / 2**20 if d.type == "cuda" else None}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
