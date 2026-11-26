"""Synthetic functional tests only; no financial performance assertions."""
import copy
import unittest
import numpy as np
import torch
from research_model import DisclosureModel, MaskedTemporalEncoder, peer_messages, reliability_mixture, weighted_mean
from train_research_model import synthetic_batch


def batch():
    return {key: torch.as_tensor(value) for key, value in synthetic_batch().items() if value.dtype.kind not in "US"}


def model(**kwargs):
    return DisclosureModel(features=3, market_features=2, sector_features=2, event_dim=8, structured_dim=2, **kwargs)


class ModelTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(42); torch.set_num_threads(2)

    def test_missing_auxiliary_exact_price_and_masked_nan(self):
        b = batch(); b["event_mask"].zero_(); b["body_available"].zero_(); b["field_mask"].zero_()
        b["events"].fill_(float("nan")); b["predicted_fields"].fill_(float("nan"))
        b["edges"] = torch.empty((2, 0), dtype=torch.long)
        for key in ("edge_age", "edge_share", "edge_accepted_complete"):
            b[key] = b[key][:0]
        for mode in ("full", "concat", "standard_moe", "ordinary_gate"):
            out = model(mode=mode)(b)
            self.assertTrue(torch.equal(out["probability"], out["price_probability"]))
            self.assertTrue(torch.isfinite(out["probability"]).all())

    def test_normalization_nesting_raw_bounds_and_gradients(self):
        b = batch(); m = model(); out = m(b)
        for branch in ("probability", "price_probability", "event_probability", "graph_probability"):
            torch.testing.assert_close(out[branch].sum(-1), torch.ones(8))
            self.assertTrue((out[branch] >= 0).all())
        self.assertTrue((out["p_loss_10pct"] <= out["p_loss_5pct"]).all())
        self.assertTrue((out["p_loss_5pct"] <= out["p_loss"] + 1e-7).all())
        torch.testing.assert_close(out["alphas"].sum(-1), torch.ones(8))
        self.assertTrue((out["alphas"][:, 1] <= out["event_reliability"] + 1e-7).all())
        self.assertTrue((out["alphas"][:, 2] <= out["graph_reliability"] + 1e-7).all())
        loss = m.loss(out, b["label_bin"], b["supervised"]); loss.backward()
        for module in (m.stock_encoder, m.market_encoder, m.sector_encoder, m.event_proj, m.field_proj,
                       m.event_query, m.company_message, m.event_gate, m.graph_gate):
            self.assertTrue(any(p.grad is not None and torch.isfinite(p.grad).all() and p.grad.abs().sum() > 0 for p in module.parameters()))

    def test_single_fresh_event_can_rescue_tiny_price_risk(self):
        b = batch(); b["event_age"].zero_(); b["event_mask"][:, 1] = False
        m = model(mode="no_graph")
        with torch.no_grad():
            m.price_head.weight.zero_(); m.price_head.bias.copy_(torch.tensor([12., 0., 0., 0.]))
            m.event_head.weight.zero_(); m.event_head.bias.copy_(torch.tensor([0., 0., 0., 12.]))
            m.event_gate.weight.zero_(); m.event_gate.bias.fill_(20.)
        out = m(b)
        self.assertTrue((out["price_probability"][:, 2:].sum(-1) < .0001).all())
        self.assertTrue((out["p_loss_5pct"] > .999).all())

    def test_context_query_and_all_experts_condition_on_context(self):
        b = batch(); m = model(); first = m(b)
        altered = copy.deepcopy(b); altered["market_seq"] += 3
        second = m(altered)
        self.assertFalse(torch.allclose(first["event_attention"], second["event_attention"]))
        for expert in ("price", "event", "graph"):
            self.assertFalse(torch.allclose(first[expert + "_probability"], second[expert + "_probability"]))
        control = model(mode="no_context_attention")
        torch.testing.assert_close(control(b)["event_attention"], control(altered)["event_attention"])

    def test_body_survives_bad_json_and_is_frozen(self):
        b = batch(); b["event_json_valid"].zero_(); b["field_mask"].zero_()
        b["predicted_fields"].fill_(float("nan")); b["events"].requires_grad_()
        m = model(); out = m(b)
        self.assertTrue(out["event_available"].all())
        m.loss(out, b["label_bin"], b["supervised"]).backward()
        self.assertIsNone(b["events"].grad)

    def test_neighbor_event_is_explicit_graph_message_without_future_labels(self):
        b = batch(); altered = copy.deepcopy(b); altered["events"][1] += 5
        m = model(); first, second = m(b), m(altered)
        torch.testing.assert_close(first["event_probability"][0], second["event_probability"][0], rtol=0, atol=0)
        self.assertFalse(torch.allclose(first["graph_probability"][0], second["graph_probability"][0]))

    def test_fields_only_events_are_available_without_body(self):
        b = batch(); b["body_available"].zero_(); b["events"].fill_(float("nan"))
        out = model()(b)
        self.assertTrue(out["event_available"].all())
        self.assertTrue(torch.isfinite(out["probability"]).all())

    def test_masked_sessions_do_not_advance_gru(self):
        enc = MaskedTemporalEncoder(2, 4)
        seq = torch.randn(3, 2, 2); mask = torch.ones_like(seq, dtype=torch.bool)
        padded = torch.cat((seq, torch.full((3, 4, 2), float("nan"))), 1)
        padded_mask = torch.cat((mask, torch.zeros((3, 4, 2), dtype=torch.bool)), 1)
        torch.testing.assert_close(enc(seq, mask), enc(padded, padded_mask), rtol=0, atol=0)
        torch.testing.assert_close(enc(padded, torch.zeros_like(padded_mask)), torch.zeros(3, 4))

    def test_all_controls_same_four_bin_nll_and_training_step(self):
        b = batch()
        for mode in DisclosureModel.MODES:
            m = model(mode=mode); out = m(b)
            selected = b["supervised"]
            expected = torch.nn.functional.nll_loss(out["probability"][selected].log(), b["label_bin"][selected])
            loss = m.loss(out, b["label_bin"], selected)
            torch.testing.assert_close(loss, expected)
            optimizer = torch.optim.AdamW(m.parameters(), lr=.001)
            before = m.stock_encoder.gru.weight_ih.detach().clone()
            optimizer.zero_grad(); loss.backward(); optimizer.step()
            self.assertFalse(torch.equal(before, m.stock_encoder.gru.weight_ih))

    def test_price_only_ignores_outcomes_events_and_graph(self):
        b = batch(); alt = copy.deepcopy(b)
        alt["events"].fill_(float("nan")); alt["edge_share"].fill_(float("nan")); alt["label_bin"].fill_(3)
        full = model()
        labels_only = copy.deepcopy(b); labels_only["label_bin"].fill_(3)
        labels_only["supervised"].zero_(); labels_only["outcome_end_ordinal"].fill_(999999)
        torch.testing.assert_close(full(b)["probability"], full(labels_only)["probability"], rtol=0, atol=0)
        m = model(mode="price_only")
        torch.testing.assert_close(m(b)["probability"], m(alt)["probability"], rtol=0, atol=0)
        m.loss(m(b), b["label_bin"], b["supervised"]).backward()
        self.assertIsNone(m.event_proj.weight.grad); self.assertIsNone(m.company_message.weight.grad)

    def test_singletons_duplicates_negative_edges(self):
        b = batch(); b["edges"][1] = torch.arange(8)
        out = model()(b)
        self.assertFalse(out["graph_available"].any()); self.assertFalse(out["graph_reliability"].any())
        b = batch(); b["edges"][:, 1] = b["edges"][:, 0]
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            model()(b)
        b = batch(); b["edge_share"][0] = -1
        with self.assertRaisesRegex(ValueError, "nonnegative"):
            model()(b)
        b = batch(); b["edge_accepted_complete"][0] = False
        with self.assertRaisesRegex(ValueError, "complete"):
            model()(b)

    def test_leave_target_out_and_sparse_institution_ids(self):
        messages = torch.tensor([[100.], [2.], [3.]])
        peer, weights = peer_messages(messages, torch.tensor([0, 1, 2]), torch.tensor([999999, 999999, 4]), torch.ones(3), 3)
        torch.testing.assert_close(peer, torch.tensor([[2.], [100.], [0.]]))
        torch.testing.assert_close(weights, torch.tensor([1., 1., 0.]))
        pooled = weighted_mean(peer, torch.tensor([0, 1, 2]), weights, 3)
        torch.testing.assert_close(pooled, peer)

    def test_age_quality_bounds_and_shared_mixture(self):
        p = torch.tensor([[.97, .01, .01, .01]]).repeat(4, 1)
        e = torch.tensor([[.01, .01, .01, .97]]).repeat(4, 1)
        ue = torch.tensor([0., .2, .7, 1.], requires_grad=True)
        ug = torch.tensor([0., .8, .1, 1.], requires_grad=True)
        out, alpha = reliability_mixture(p, e, e, ue, ug)
        self.assertTrue(torch.equal(out[0], p[0]))
        self.assertTrue((alpha[:, 1] <= ue + 1e-7).all()); self.assertTrue((alpha[:, 2] <= ug + 1e-7).all())
        out[:, 3].sum().backward()
        self.assertTrue(torch.isfinite(ue.grad).all()); self.assertTrue(torch.isfinite(ug.grad).all())
        b = batch(); m = model(); b["event_age"] *= 300; b["graph_age"] *= 300
        out = m(b)
        event_bound = torch.exp(-torch.nn.functional.softplus(m.age_rates[0]) * b["event_age"].min(1).values / 60)
        self.assertTrue((out["event_reliability"] <= event_bound + 1e-7).all())

    def test_mapping_deduplicated_and_synthetic_identity_equivalent(self):
        b = batch(); m = model(); expected = m(b)["probability"]
        copied = copy.deepcopy(b)
        for prefix in ("market", "sector"):
            copied[prefix + "_seq"] = b[prefix + "_seq"][b[prefix + "_index"]]
            copied[prefix + "_mask"] = b[prefix + "_mask"][b[prefix + "_index"]]
            copied[prefix + "_index"] = torch.arange(8)
        torch.testing.assert_close(expected, m(copied)["probability"])


if __name__ == "__main__":
    unittest.main()
