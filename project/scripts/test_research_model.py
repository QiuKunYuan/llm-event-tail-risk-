"""Synthetic functional tests; no claim about financial performance."""
import unittest
import torch
from research_model import DisclosureModel


def batch():
    torch.manual_seed(42)
    return {"seq": torch.randn(6, 60, 4), "seq_mask": torch.ones(6, 60, 4, dtype=torch.bool),
            "events": torch.randn(6, 8, 128), "event_mask": torch.ones(6, 8, dtype=torch.bool),
            "event_age": torch.ones(6, 8), "event_quality": torch.ones(6, 8),
            "edges": torch.tensor([[0, 1, 2, 3, 4, 5], [0, 0, 1, 1, 2, 2]]),
            "edge_share": torch.full((6,), .1), "edge_age": torch.ones(6), "graph_age": torch.ones(6),
            "label": torch.tensor([0., 1., 0., 1., 0., 1.]), "supervised": torch.tensor([1, 1, 1, 1, 0, 0], dtype=torch.bool)}


class ModelTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(2)

    def test_missing_auxiliary_is_exact_sequence(self):
        b = batch(); b["event_mask"].zero_(); b["events"].fill_(float("nan"))
        b["edges"] = torch.empty((2, 0), dtype=torch.long)
        b["edge_age"] = b["edge_share"] = torch.empty(0)
        model = DisclosureModel()
        out = model(b)
        self.assertTrue(torch.equal(out["logit"], out["sequence_logit"]))
        self.assertTrue(torch.isfinite(out["logit"]).all())

    def test_bounded_correction_and_gradients(self):
        b = batch(); model = DisclosureModel(); out = model(b)
        self.assertTrue(((out["logit"] - out["sequence_logit"]).abs() <= out["event_weight"] + out["graph_weight"] + 1e-6).all())
        from torch.nn import functional as F
        event_ceiling = torch.exp(-F.softplus(model.age_rates[0]) * b["event_age"].min(1).values / 60)
        graph_ceiling = torch.exp(-F.softplus(model.age_rates[2]) * b["graph_age"] / 252)
        self.assertTrue((out["event_weight"] <= event_ceiling + 1e-6).all())
        self.assertTrue((out["graph_weight"] <= graph_ceiling + 1e-6).all())
        loss = model.loss(out, b["label"], b["supervised"]); loss.backward()
        for module in [model.gru, model.event_proj, model.company_message, model.event_gate]:
            self.assertTrue(any(p.grad is not None and torch.isfinite(p.grad).all() and p.grad.abs().sum() > 0 for p in module.parameters()))

    def test_one_training_step_and_all_modes(self):
        b = batch()
        for mode in ["full", "sequence", "concat", "no_age", "age_feature", "no_quality", "no_event", "no_graph"]:
            model = DisclosureModel(mode=mode)
            optimizer = torch.optim.AdamW(model.parameters(), lr=.001)
            before = model.sequence_head.weight.detach().clone()
            loss = model.loss(model(b), b["label"], b["supervised"])
            optimizer.zero_grad(); loss.backward(); optimizer.step()
            self.assertTrue(torch.isfinite(loss))
            self.assertFalse(torch.equal(before, model.sequence_head.weight))

if __name__ == "__main__":
    unittest.main()
