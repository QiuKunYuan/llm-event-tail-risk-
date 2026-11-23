"""Disclosure-age bounded residual fusion. PyTorch, no graph-library dependency."""
from __future__ import annotations

import torch
from torch import nn
from torch.nn import functional as F


def weighted_mean(messages, index, weights, size):
    out = messages.new_zeros((size, messages.shape[-1]))
    denom = messages.new_zeros((size, 1))
    out.index_add_(0, index, messages * weights[:, None])
    denom.index_add_(0, index, weights[:, None])
    return out / denom.clamp_min(1e-8)


def peer_messages(messages, companies, institutions, weights, size):
    """Exclude each destination company from its institution's message.

    One edge per company/institution pair is required after disclosure deduplication.
    Singleton institutions cannot supply cross-company evidence.
    """
    pairs = torch.stack((companies, institutions), dim=1)
    if len(pairs) != len(torch.unique(pairs, dim=0)):
        raise ValueError("Duplicate company-institution edge")
    count = int(institutions.max()) + 1 if institutions.numel() else 0
    total = messages.new_zeros((count, messages.shape[-1]))
    denom = weights.new_zeros(count)
    total.index_add_(0, institutions, messages * weights[:, None])
    denom.index_add_(0, institutions, weights)
    peer_weight = denom[institutions] - weights
    valid = peer_weight > 1e-8
    peer = (total[institutions] - messages * weights[:, None]) / peer_weight.clamp_min(1e-8)[:, None]
    return peer * valid[:, None], weights * valid


class DisclosureModel(nn.Module):
    """One market-day graph per batch; nodes include sampled neighbor companies.

    seq [N,T,F], seq_mask same shape, events [N,K,D], event_mask [N,K],
    event_age [N,K] in trading sessions, event_quality [N,K] in [0,1],
    edges [2,E] company/institution ids, edge_share/age [E], graph_age [N].
    Missing values are masked before encoding; do not encode future observations.
    """
    def __init__(self, features=4, event_dim=128, hidden=128, mode="full", structured_dim=0):
        super().__init__()
        if mode not in {"full", "sequence", "concat", "no_age", "age_feature", "no_quality", "no_graph", "no_event", "no_aux_loss", "no_edge_decay"}:
            raise ValueError(mode)
        if mode == "sequence" and structured_dim:
            raise ValueError("Returns-only sequence baseline cannot receive structured event controls")
        self.mode = mode
        self.structured_dim = structured_dim
        self.gru = nn.GRU(features * 2, 64, batch_first=True)
        self.seq_proj = nn.Linear(64, hidden)
        self.event_proj = nn.Linear(event_dim, hidden)
        self.event_attention = nn.Linear(hidden, 1)
        self.company_message = nn.Linear(hidden, hidden)
        self.institution_message = nn.Linear(hidden, hidden)
        self.graph_proj = nn.Linear(hidden * 2, hidden)
        self.sequence_head = nn.Linear(hidden + structured_dim, 1)
        self.event_head = nn.Linear(hidden, 1)
        self.graph_head = nn.Linear(hidden, 1)
        self.event_gate = nn.Linear(hidden * 2 + 3, 1)
        self.graph_gate = nn.Linear(hidden * 2 + 3, 1)
        self.concat_head = nn.Linear(hidden * 3 + 6 + structured_dim, 1)
        self.age_rates = nn.Parameter(torch.full((3,), -2.0))

    def forward(self, b):
        mask = b["seq_mask"].bool()
        x = torch.where(mask, b["seq"], 0.)
        if not torch.isfinite(x).all():
            raise ValueError("Observed sequence contains nonfinite values")
        _, h = self.gru(torch.cat((x, mask.to(x.dtype)), -1))
        s = torch.tanh(self.seq_proj(h[-1]))
        n = len(s)
        age_on = self.mode not in {"no_age", "age_feature"}
        quality = b["event_quality"].clamp(0, 1) if self.mode != "no_quality" else torch.ones_like(b["event_quality"])
        em = b["event_mask"].bool() & (quality > 0)
        if self.mode in {"no_event", "sequence"}:
            em = torch.zeros_like(em)
        ev = torch.tanh(self.event_proj(torch.where(em[..., None], b["events"], 0.)))
        ea = b["event_age"].clamp_min(0) / 60
        scores = self.event_attention(ev).squeeze(-1)
        weights = torch.exp(scores.clamp(-20, 20)) * em * quality
        if age_on:
            weights = weights * torch.exp(-F.softplus(self.age_rates[0]) * ea)
        e = (ev * weights[..., None]).sum(1) / weights.sum(1).clamp_min(1e-8)[:, None]
        available_e = em.any(1).to(x.dtype)
        newest = ea.masked_fill(~em, float("inf")).min(1).values
        newest = torch.where(em.any(1), newest, 0.)
        q = (quality * em).sum(1) / em.sum(1).clamp_min(1)
        ci, ii = b["edges"].long()
        shares = b["edge_share"].clamp_min(0)
        if age_on and self.mode != "no_edge_decay":
            shares = shares * torch.exp(-F.softplus(self.age_rates[1]) * b["edge_age"].clamp_min(0) / 252)
        if self.mode in {"no_graph", "sequence"}:
            shares = torch.zeros_like(shares)
        ih, peer_shares = peer_messages(torch.tanh(self.company_message(s[ci])), ci, ii, shares, n)
        gh = weighted_mean(torch.tanh(self.institution_message(ih)), ci, peer_shares, n)
        available_g = x.new_zeros(n)
        available_g.index_add_(0, ci, peer_shares)
        available_g = (available_g > 0).to(x.dtype)
        g = torch.tanh(self.graph_proj(torch.cat((s, gh), -1))) * available_g[:, None]
        ga = b["graph_age"].clamp_min(0) / 252
        e_meta = torch.stack((available_e, newest, q), -1)
        g_meta = torch.stack((available_g, ga, torch.ones_like(ga)), -1)
        ze = self.event_head(e).squeeze(-1)
        zg = self.graph_head(g).squeeze(-1)
        controls = b.get("structured")
        if self.structured_dim:
            if controls is None or controls.shape != (n, self.structured_dim) or not torch.isfinite(controls).all():
                raise ValueError("Missing or invalid structured controls")
            zs = self.sequence_head(torch.cat((s, controls), -1)).squeeze(-1)
        else:
            zs = self.sequence_head(s).squeeze(-1)
        if self.mode == "no_age":
            e_meta[:, 1] = 0
            g_meta[:, 1] = 0
        ae = available_e * q * torch.sigmoid(self.event_gate(torch.cat((s, e, e_meta), -1))).squeeze(-1)
        ag = available_g * torch.sigmoid(self.graph_gate(torch.cat((s, g, g_meta), -1))).squeeze(-1)
        if age_on:
            ae = ae * torch.exp(-F.softplus(self.age_rates[0]) * newest)
            ag = ag * torch.exp(-F.softplus(self.age_rates[2]) * ga)
        z = zs + ae * torch.tanh(ze) + ag * torch.tanh(zg)
        if self.mode == "sequence":
            z = zs
        if self.mode == "concat":
            joined = torch.cat((s, e, g, e_meta, g_meta), -1)
            if self.structured_dim:
                joined = torch.cat((joined, controls), -1)
            z = self.concat_head(joined).squeeze(-1)
        return {"logit": z, "sequence_logit": zs, "event_logit": ze, "graph_logit": zg,
                "event_weight": ae, "graph_weight": ag, "event_available": available_e, "graph_available": available_g,
                "use_auxiliary_loss": self.mode not in {"sequence", "no_aux_loss"}}

    @staticmethod
    def loss(out, labels, supervised=None):
        selected = torch.ones_like(labels, dtype=torch.bool) if supervised is None else supervised.bool()
        if not selected.any():
            raise ValueError("Batch has no supervised target companies")
        loss = F.binary_cross_entropy_with_logits(out["logit"][selected], labels[selected])
        if not out["use_auxiliary_loss"]:
            return loss
        loss = loss + .2 * F.binary_cross_entropy_with_logits(out["sequence_logit"][selected], labels[selected])
        for branch in ("event", "graph"):
            m = selected & out[branch + "_available"].bool()
            if m.any():
                loss = loss + .1 * F.binary_cross_entropy_with_logits(out[branch + "_logit"][m], labels[m])
        return loss
