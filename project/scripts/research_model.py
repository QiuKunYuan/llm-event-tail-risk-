"""Actual five-session loss probabilities; synthetic CPU implementation, not efficacy evidence.

Each market-day batch contains target and (optional) neighbor nodes. Frozen body
vectors and predicted semantic fields are inputs, never future outcomes. Four
mutually exclusive bins are >=0, (-.05,0), (-.10,-.05], <=-.10.
"""
from __future__ import annotations

import torch
from torch import nn
from torch.nn import functional as F


def _nonnegative(value, name):
    if not torch.isfinite(value).all() or (value < 0).any():
        raise ValueError(f"{name} must be finite and nonnegative")


def _unit(value, name):
    _nonnegative(value, name)
    if (value > 1).any():
        raise ValueError(f"{name} must be in [0,1]")


def weighted_mean(messages, index, weights, size):
    """Reusable nonnegative weighted aggregation; empty destinations return zero."""
    if messages.ndim != 2 or index.shape != weights.shape or len(messages) != len(index):
        raise ValueError("Invalid weighted aggregation shapes")
    _nonnegative(weights, "weights")
    if not torch.isfinite(messages).all():
        raise ValueError("Nonfinite messages")
    if index.dtype != torch.long or ((index < 0) | (index >= size)).any():
        raise ValueError("Invalid aggregation index")
    out = messages.new_zeros((size, messages.shape[-1]))
    denom = messages.new_zeros((size, 1))
    out.index_add_(0, index, messages * weights[:, None])
    denom.index_add_(0, index, weights[:, None])
    return out / denom.clamp_min(torch.finfo(messages.dtype).tiny)


def peer_messages(messages, companies, institutions, weights, size):
    """Institutional mean excluding the target's entire company/institution pair.

    Upstream must select accepted complete disclosures and deduplicate pairs.
    A singleton (or zero peer weight) is not cross-company information.
    """
    if companies.dtype != torch.long or institutions.dtype != torch.long:
        raise ValueError("Graph ids must be integer tensors")
    if companies.shape != institutions.shape or companies.shape != weights.shape or len(messages) != len(companies):
        raise ValueError("Invalid graph shapes")
    if ((companies < 0) | (companies >= size)).any() or (institutions < 0).any():
        raise ValueError("Invalid graph ids")
    _nonnegative(weights, "edge weights")
    if not torch.isfinite(messages).all():
        raise ValueError("Nonfinite graph messages")
    pairs = torch.stack((companies, institutions), dim=1)
    if len(pairs) != len(torch.unique(pairs, dim=0)):
        raise ValueError("Duplicate company-institution edge")
    # Compact arbitrary institution ids, without allocating max(id) rows.
    _, compact = torch.unique(institutions, sorted=True, return_inverse=True)
    count = int(compact.max()) + 1 if compact.numel() else 0
    total = messages.new_zeros((count, messages.shape[-1]))
    denom = weights.new_zeros(count)
    total.index_add_(0, compact, messages * weights[:, None])
    denom.index_add_(0, compact, weights)
    peer_weight = denom[compact] - weights
    valid = peer_weight > 0
    peer = (total[compact] - messages * weights[:, None]) / peer_weight.clamp_min(torch.finfo(weights.dtype).tiny)[:, None]
    return peer * valid[:, None], weights * valid


def reliability_mixture(price, event, graph, u_event, u_graph):
    """Shared-bin convex mixture. g is a weight construction, not loss-event union."""
    _unit(u_event, "event reliability"); _unit(u_graph, "graph reliability")
    g = 1 - (1 - u_event) * (1 - u_graph)
    denom = u_event + u_graph
    positive = denom > 0
    safe = torch.where(positive, denom, torch.ones_like(denom))
    ae = torch.where(positive, g * u_event / safe, torch.zeros_like(g))
    ag = torch.where(positive, g * u_graph / safe, torch.zeros_like(g))
    alphas = torch.stack((1 - g, ae, ag), -1)
    probability = (torch.stack((price, event, graph), 1) * alphas[..., None]).sum(1)
    return probability, alphas


def loss_probabilities(probability):
    return {"p_loss": 1 - probability[..., 0], "p_loss_5pct": probability[..., 2:].sum(-1), "p_loss_10pct": probability[..., 3]}


class MaskedTemporalEncoder(nn.Module):
    """GRU updates only on observed sessions; masked padding cannot advance state."""
    def __init__(self, features, width):
        super().__init__()
        self.features = features
        self.gru = nn.GRUCell(features * 2, width)
        self.width = width

    def forward(self, sequence, mask):
        if sequence.ndim != 3 or sequence.shape != mask.shape or sequence.shape[-1] != self.features:
            raise ValueError("Invalid temporal sequence/mask shape")
        mask = mask.bool()
        x = torch.where(mask, sequence, 0.)
        if not torch.isfinite(x).all():
            raise ValueError("Observed sequence contains nonfinite values")
        h = x.new_zeros((len(x), self.width))
        for t in range(x.shape[1]):
            proposed = self.gru(torch.cat((x[:, t], mask[:, t].to(x.dtype)), -1), h)
            h = torch.where(mask[:, t].any(-1, keepdim=True), proposed, h)
        return h


class DisclosureModel(nn.Module):
    """64 stock + 32 market + 32 historical sector = 128 shared price context.

    market_index/sector_index map nodes into deduplicated context sequences. The
    strict disk contract enforces market once/date and sector once/category/date;
    direct synthetic callers may use N context sequences with identity mappings.
    Body availability is separate from JSON validity and externally measured
    quality. No self-reported model confidence is accepted as a quality source.
    """
    MODES = {"full", "price_only", "sequence", "concat", "ordinary_gate", "standard_moe",
             "no_age", "no_quality", "no_event", "no_graph", "no_market", "no_sector",
             "no_context_attention", "no_edge_decay", "no_aux_loss"}

    def __init__(self, features=4, event_dim=128, hidden=128, mode="full", structured_dim=0,
                 market_features=4, sector_features=4, modality_dropout=0., expert_aux=None):
        super().__init__()
        if mode not in self.MODES:
            raise ValueError(mode)
        if hidden != 128:
            raise ValueError("Shared stock64/market32/sector32 context must have width 128")
        if not 0 <= modality_dropout < 1:
            raise ValueError("Invalid matched modality dropout")
        self.mode, self.structured_dim, self.modality_dropout = mode, structured_dim, modality_dropout
        self.expert_aux = expert_aux or {}
        if set(self.expert_aux) - {"price", "event", "graph"} or any(not 0 <= float(x) <= 1 for x in self.expert_aux.values()):
            raise ValueError("Expert auxiliary NLL must be explicitly controlled")
        self.stock_encoder = MaskedTemporalEncoder(features, 64)
        self.market_encoder = MaskedTemporalEncoder(market_features, 32)
        self.sector_encoder = MaskedTemporalEncoder(sector_features, 32)
        self.event_proj = nn.Linear(event_dim, 128)
        self.field_proj = nn.Linear(structured_dim * 2, 128) if structured_dim else None
        self.event_query = nn.Linear(128, 128, bias=False)
        self.event_key = nn.Linear(128, 128, bias=False)
        self.static_attention = nn.Linear(128, 1)
        self.company_message = nn.Linear(256, 128)
        self.institution_message = nn.Linear(128, 128)
        self.graph_proj = nn.Linear(256, 128)
        self.price_head = nn.Linear(128, 4)
        self.event_head = nn.Linear(256, 4)
        self.graph_head = nn.Linear(256, 4)
        self.event_gate = nn.Linear(259, 1)
        self.graph_gate = nn.Linear(259, 1)
        self.moe_gate = nn.Linear(390, 3)
        self.concat_head = nn.Linear(390, 4)
        self.age_rates = nn.Parameter(torch.full((3,), -2.))

    def _context(self, b):
        stock = self.stock_encoder(b["seq"], b["seq_mask"])
        contexts = []
        for name, encoder, disabled in [("market", self.market_encoder, self.mode == "no_market"),
                                         ("sector", self.sector_encoder, self.mode == "no_sector")]:
            index = b[name + "_index"]
            if index.shape != (len(stock),) or index.dtype != torch.long:
                raise ValueError("Invalid context mapping tensor")
            sequence = b[name + "_seq"]
            if ((index < 0) | (index >= len(sequence))).any():
                raise ValueError("Context mapping out of bounds")
            state = stock.new_zeros((len(sequence), 32)) if disabled else encoder(sequence, b[name + "_mask"])
            contexts.append(state[index])
        return torch.cat((stock, *contexts), -1)

    def _events(self, b, context):
        shape = b["event_mask"].shape
        if len(shape) != 2 or shape[0] != len(context) or b["events"].shape[:2] != shape:
            raise ValueError("Invalid event shapes")
        for key in ("body_available", "event_json_valid", "body_quality", "evidence_quality", "event_age"):
            if b[key].shape != shape:
                raise ValueError("Invalid " + key + " shape")
        for key in ("body_quality", "evidence_quality"):
            _unit(b[key], key)
        _nonnegative(b["event_age"], "event_age")
        body = b["event_mask"].bool() & b["body_available"].bool()
        body_values = torch.where(body[..., None], b["events"].detach(), 0.)
        if not torch.isfinite(body_values).all():
            raise ValueError("Nonfinite available body vectors")
        ev = self.event_proj(body_values) * body[..., None]
        field_available = torch.zeros_like(body)
        if self.structured_dim:
            fields, fm = b["predicted_fields"], b["field_mask"].bool()
            if fields.shape != (*shape, self.structured_dim) or fm.shape != fields.shape:
                raise ValueError("Invalid predicted semantic fields")
            fm = fm & b["event_mask"][..., None].bool() & b["event_json_valid"][..., None].bool()
            fields = torch.where(fm, fields.detach(), 0.)
            if not torch.isfinite(fields).all():
                raise ValueError("Nonfinite predicted fields")
            field_available = fm.any(-1)
            ev = ev + self.field_proj(torch.cat((fields, fm.to(fields.dtype)), -1)) * field_available[..., None]
        ev = torch.tanh(ev)
        available = body | field_available
        q = torch.maximum(b["body_quality"] * body,
                          b["evidence_quality"] * field_available)
        if self.mode == "no_quality":
            q = available.to(context.dtype)
        valid = available & (q > 0)
        age = b["event_age"] / 60
        if self.mode == "no_age":
            age = torch.zeros_like(age)
        if shape[1] == 0:
            return context.new_zeros(context.shape), context.new_zeros((len(context), 3)), context.new_zeros(shape)
        scores = self.static_attention(ev).squeeze(-1) if self.mode == "no_context_attention" else (
            self.event_key(ev) * self.event_query(context)[:, None]).sum(-1) / 128**.5
        # Safe masked softmax even for all-missing nodes; no NaN softmax(-inf,...).
        scores = scores.masked_fill(~valid, -torch.inf)
        maximum = scores.max(1, keepdim=True).values
        maximum = torch.where(valid.any(1, keepdim=True), maximum, torch.zeros_like(maximum))
        weights = torch.exp(scores - maximum) * q
        weights = weights * torch.exp(-F.softplus(self.age_rates[0]) * age)
        attention = weights / weights.sum(1, keepdim=True).clamp_min(torch.finfo(context.dtype).tiny)
        state = (ev * attention[..., None]).sum(1)
        newest = age.masked_fill(~valid, torch.inf).min(1).values
        newest = torch.where(valid.any(1), newest, 0.)
        quality = (q * attention).sum(1)
        available_node = valid.any(1).to(context.dtype)
        if self.training and self.modality_dropout:
            available_node = available_node * (torch.rand_like(available_node) >= self.modality_dropout)
        state = state * available_node[:, None]
        return state, torch.stack((available_node, newest, quality), -1), attention

    def _graph(self, b, context, event):
        edges = b["edges"]
        if edges.ndim != 2 or edges.shape[0] != 2 or edges.dtype != torch.long:
            raise ValueError("Invalid edges")
        ci, ii = edges
        for key in ("edge_share", "edge_age", "edge_accepted_complete"):
            if b[key].shape != ci.shape:
                raise ValueError("Invalid " + key + " shape")
        _nonnegative(b["edge_share"], "edge_share"); _nonnegative(b["edge_age"], "edge_age")
        if not b["edge_accepted_complete"].bool().all():
            raise ValueError("Only accepted complete holdings may enter the graph")
        if b["graph_age"].shape != (len(context),) or b["graph_quality"].shape != (len(context),):
            raise ValueError("Invalid graph metadata shape")
        _nonnegative(b["graph_age"], "graph_age"); _unit(b["graph_quality"], "graph_quality")
        shares = b["edge_share"]
        if self.mode not in {"no_age", "no_edge_decay"}:
            shares = shares * torch.exp(-F.softplus(self.age_rates[1]) * b["edge_age"] / 252)
        # Explicit known price + event state, not merely sequence-only propagation.
        if ((ci < 0) | (ci >= len(context))).any():
            raise ValueError("Company graph id out of bounds")
        messages = torch.tanh(self.company_message(torch.cat((context[ci], event[ci]), -1)))
        peers, peer_shares = peer_messages(messages, ci, ii, shares, len(context))
        pooled = weighted_mean(torch.tanh(self.institution_message(peers)), ci, peer_shares, len(context))
        available = shares.new_zeros(len(context))
        available.index_add_(0, ci, peer_shares)
        available = (available > 0).to(context.dtype)
        if self.training and self.modality_dropout:
            available = available * (torch.rand_like(available) >= self.modality_dropout)
        state = torch.tanh(self.graph_proj(torch.cat((context, pooled), -1))) * available[:, None]
        age = b["graph_age"] / 252 if self.mode != "no_age" else torch.zeros_like(available)
        quality = b["graph_quality"] if self.mode != "no_quality" else torch.ones_like(available)
        return state, torch.stack((available, age, quality), -1)

    def forward(self, b):
        context = self._context(b)
        price = torch.softmax(self.price_head(context), -1)
        n = len(context)
        event = graph = torch.zeros_like(context)
        em = gm = context.new_zeros((n, 3))
        attention = None
        if self.mode not in {"no_event", "sequence", "price_only"}:
            event, em, attention = self._events(b, context)
        if self.mode not in {"no_graph", "sequence", "price_only"}:
            graph, gm = self._graph(b, context, event)
        event_p = torch.softmax(self.event_head(torch.cat((context, event), -1)), -1)
        graph_p = torch.softmax(self.graph_head(torch.cat((context, graph), -1)), -1)
        ue = em[:, 0] * em[:, 2] * torch.exp(-F.softplus(self.age_rates[0]) * em[:, 1]) * torch.sigmoid(
            self.event_gate(torch.cat((context, event, em), -1))).squeeze(-1)
        ug = gm[:, 0] * gm[:, 2] * torch.exp(-F.softplus(self.age_rates[2]) * gm[:, 1]) * torch.sigmoid(
            self.graph_gate(torch.cat((context, graph, gm), -1))).squeeze(-1)
        probability, alphas = reliability_mixture(price, event_p, graph_p, ue, ug)
        joined = torch.cat((context, event, graph, em, gm), -1)
        any_aux = (em[:, 0] + gm[:, 0]) > 0
        if self.mode in {"standard_moe", "ordinary_gate"}:
            logits = self.moe_gate(joined)
            available = torch.stack((torch.ones_like(any_aux), em[:, 0].bool(), gm[:, 0].bool()), -1)
            if self.mode == "ordinary_gate":
                independent = torch.sigmoid(logits) * available
                alphas = independent / independent.sum(-1, keepdim=True)
            else:
                alphas = torch.softmax(logits.masked_fill(~available, -torch.inf), -1)
            probability = (torch.stack((price, event_p, graph_p), 1) * alphas[..., None]).sum(1)
        if self.mode == "concat":
            probability = torch.where(any_aux[:, None], torch.softmax(self.concat_head(joined), -1), price)
        out = {"probability": probability, "price_probability": price, "event_probability": event_p,
               "graph_probability": graph_p, "alphas": alphas, "event_reliability": ue,
               "graph_reliability": ug, "event_available": em[:, 0], "graph_available": gm[:, 0],
               "price_context": context, "event_attention": attention, "expert_aux": self.expert_aux,
               "raw_reliability_bound_applies": self.mode not in {"concat", "standard_moe", "ordinary_gate"}}
        out.update(loss_probabilities(probability))
        return out

    @staticmethod
    def loss(out, labels, supervised=None):
        selected = torch.ones_like(labels, dtype=torch.bool) if supervised is None else supervised.bool()
        if labels.ndim != 1 or selected.shape != labels.shape or not selected.any():
            raise ValueError("Batch has no valid supervised target companies")
        target = labels[selected]
        if not torch.isfinite(target).all() or not torch.isin(target, target.new_tensor([0, 1, 2, 3])).all():
            raise ValueError("Four-bin labels must be integers 0..3")
        def nll(probability, mask):
            return F.nll_loss(probability[mask].clamp_min(torch.finfo(probability.dtype).tiny).log(), labels[mask].long())
        loss = nll(out["probability"], selected)
        # Nonprimary matched four-bin sensitivity only; no old binary branch BCE.
        for branch, coefficient in out.get("expert_aux", {}).items():
            mask = selected if branch == "price" else selected & out[branch + "_available"].bool()
            if coefficient and mask.any():
                loss = loss + coefficient * nll(out[branch + "_probability"], mask)
        return loss
