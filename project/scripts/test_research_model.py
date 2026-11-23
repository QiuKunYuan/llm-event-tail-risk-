"""Synthetic functional tests; no claim about financial performance."""
import unittest
import torch
from research_model import DisclosureModel, peer_messages


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
        for mode in ["full", "sequence", "concat", "no_age", "age_feature", "no_quality", "no_event", "no_graph", "no_aux_loss", "no_edge_decay"]:
            model = DisclosureModel(mode=mode)
            optimizer = torch.optim.AdamW(model.parameters(), lr=.001)
            before = model.sequence_head.weight.detach().clone()
            loss = model.loss(model(b), b["label"], b["supervised"])
            optimizer.zero_grad(); loss.backward(); optimizer.step()
            self.assertTrue(torch.isfinite(loss))
            self.assertFalse(torch.equal(before, model.sequence_head.weight))

    def test_singleton_institution_is_not_graph_information(self):
        b = batch(); b["edges"][1] = torch.arange(6)
        out = DisclosureModel()(b)
        self.assertFalse(out["graph_available"].any())
        self.assertFalse(out["graph_weight"].any())

    def test_leave_self_out(self):
        m = torch.tensor([[100.], [2.], [3.]])
        peer, weights = peer_messages(m, torch.tensor([0, 1, 2]), torch.tensor([0, 0, 1]), torch.ones(3), 3)
        torch.testing.assert_close(peer, torch.tensor([[2.], [100.], [0.]]))
        torch.testing.assert_close(weights, torch.tensor([1., 1., 0.]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            peer_messages(m[:2], torch.tensor([0, 0]), torch.tensor([0, 0]), torch.ones(2), 1)

    def test_returns_only_loss_ignores_auxiliary_information(self):
        import copy
        b = batch(); alt = copy.deepcopy(b)
        alt["events"].fill_(float("nan")); alt["event_mask"].zero_()
        alt["edges"] = torch.empty((2, 0), dtype=torch.long)
        alt["edge_share"] = alt["edge_age"] = torch.empty(0)
        model = DisclosureModel(mode="sequence")
        gradients = []
        for item in [b, alt]:
            model.zero_grad(set_to_none=True)
            out = model(item)
            loss = model.loss(out, item["label"], item["supervised"])
            loss.backward()
            gradients.append(model.gru.weight_ih_l0.grad.clone())
            self.assertIsNone(model.event_proj.weight.grad)
            self.assertIsNone(model.company_message.weight.grad)
        torch.testing.assert_close(*gradients, rtol=0, atol=0)
        with self.assertRaisesRegex(ValueError, "structured"):
            DisclosureModel(mode="sequence", structured_dim=1)

    def test_concat_receives_same_structured_controls(self):
        b = batch(); b["structured"] = torch.zeros(6, 1)
        model = DisclosureModel(mode="concat", structured_dim=1)
        with torch.no_grad():
            model.concat_head.weight.zero_(); model.concat_head.bias.zero_()
            model.concat_head.weight[0, -1] = 1
        before = model(b)["logit"]
        b["structured"].fill_(1)
        after = model(b)["logit"]
        torch.testing.assert_close(after - before, torch.ones(6))

    def test_no_auxiliary_loss_is_main_bce(self):
        b = batch(); model = DisclosureModel(mode="no_aux_loss"); out = model(b)
        target = b["supervised"]
        expected = torch.nn.functional.binary_cross_entropy_with_logits(out["logit"][target], b["label"][target])
        torch.testing.assert_close(model.loss(out, b["label"], target), expected)


if __name__ == "__main__":
    unittest.main()
