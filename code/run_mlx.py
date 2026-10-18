"""
run_mlx.py — MLX (Apple Silicon) backend for the Drosophila brain model.

Functional equivalent of run_pytorch.py, using MLX for Metal GPU
acceleration on Apple Silicon. Replaces the PyTorch sparse CSR matmul
(unsupported on MPS) with an event-weighted scatter-add over the
connectome's COO edges.

Model (matching Shiu et al. / run_pytorch.py):
    Poisson input → sparse recurrent weights (scatter-add) → AlphaLIF
    where AlphaLIF = AlphaSynapse + LIFNeuron + refractory period.

Key difference from PyTorch: batch=1 is implicit (state vectors are 1-D).
The recurrent weight matrix W[post, pre] is stored as COO
(row_idx=post, col_idx=pre, val). The recurrent term
    weighted[post] = sum_pre val * spikes[pre]
is computed as a scatter-add over edges, which runs on Metal and is
~7× faster than the CPU sparse matmul.
"""

import numpy as np
import pandas as pd
import mlx.core as mx
from pathlib import Path

# ============================================================================
# Model Parameters (matching run_pytorch.py MODEL_PARAMS)
# ============================================================================

MODEL_PARAMS = {
    'tauSyn': 5.0,        # ms
    'tDelay': 1.8,        # ms
    'v0': -52.0,          # mV
    'vReset': -52.0,      # mV
    'vRest': -52.0,       # mV
    'vThreshold': -45.0,  # mV
    'tauMem': 20.0,       # ms
    'tRefrac': 2.2,       # ms
    'scalePoisson': 250,
    'wScale': 0.275,
}

DT = 0.1  # simulation timestep in ms


# ============================================================================
# MlxModel — LIF + alpha synapse + Poisson + sparse recurrent weights
# ============================================================================

class MlxModel:
    """
    Top-level spiking model: Poisson input + sparse connectome weights +
    AlphaLIF neurons. State vectors are 1-D (batch=1 implicit):
      conductance: (n,)
      delay_buffer: (steps_delay+1, n)
      spikes: (n,)
      v: (n,)
      refrac: (n,)

    The connectome edges are stored sorted by PRE-synaptic index, enabling
    EVENT-DRIVEN recurrent computation: only edges from actively-spiking
    neurons (fan-out ~109) are processed, instead of all 15M synapses.
    """

    def __init__(self, size, dt, params, row_idx, col_idx, val, pre_indptr):
        self.size = int(size)
        self.dt = dt

        # Synapse params
        self.time_factor_syn = dt / params['tauSyn']
        self.steps_delay = int(params['tDelay'] / dt)

        # Neuron params
        self.tau_mem = params['tauMem']
        self.v_reset = float(params['vReset'])
        self.v_rest = float(params['vRest'])
        self.v_threshold = float(params['vThreshold'])
        self.v_0 = float(params['v0'])
        self.time_factor_mem = dt / self.tau_mem
        self.steps_refrac = int(params['tRefrac'] / dt)

        # Poisson params
        self.prob_scale = dt / 1000.0
        self.scale_poisson = float(params['scalePoisson'])
        self.w_scale = float(params['wScale'])

        # Connectome weights (COO sorted by PRE: row=post, col=pre, val)
        self.row_idx = mx.array(row_idx.astype(np.int32))
        self.col_idx = mx.array(col_idx.astype(np.int32))
        self.val = mx.array(val.astype(np.float32))
        # Pre-synaptic CSR boundaries for event-driven gather (numpy for fast nz)
        self.pre_indptr = pre_indptr.astype(np.int32)

    def state_init(self):
        n = self.size
        conductance = mx.zeros((n,), dtype=mx.float32)
        # Ring buffer as a Python list: pointer-only rotation, no array copy.
        delay_buffer = [mx.zeros((n,), dtype=mx.float32)
                        for _ in range(self.steps_delay + 1)]
        spikes = mx.zeros((n,), dtype=mx.float32)
        v = mx.full((n,), self.v_0, dtype=mx.float32)
        refrac = mx.full((n,), float(self.steps_refrac), dtype=mx.float32)
        return conductance, delay_buffer, spikes, v, refrac

    def _recurrent(self, spikes):
        """Event-driven: weighted[post] = sum over ACTIVE pre of val[pre->post].

        Only edges from neurons currently spiking are processed. Under the
        network's sparse activity (~20 spikes/step), this touches ~2400 edges
        instead of the full 15M.
        """
        # Materialize spikes and find active (spiking) PRE neurons
        spikes_np = np.array(spikes).astype(np.float32)
        active = np.nonzero(spikes_np > 0.5)[0]
        if active.size == 0:
            return mx.zeros((self.size,), dtype=mx.float32)

        # Gather contiguous edge ranges for each active pre neuron
        ip = self.pre_indptr
        starts = ip[active]
        ends = ip[active + 1]
        lengths = ends - starts
        flat = np.concatenate([
            np.arange(starts[k], ends[k], dtype=np.int32)
            for k in range(active.size)
        ])
        if flat.size == 0:
            return mx.zeros((self.size,), dtype=mx.float32)

        flat_mx = mx.array(flat)
        posts = mx.take(self.row_idx, flat_mx)
        vals = mx.take(self.val, flat_mx)
        out = mx.zeros((self.size,), dtype=mx.float32)
        out = out.at[posts].add(vals)
        return out

    def step(self, rates, conductance, delay_buffer, spikes, v, refrac):
        # Poisson spike input
        input_spikes = mx.random.bernoulli(rates * self.prob_scale) * self.scale_poisson
        # Recurrent spikes via event-driven sparse connectome
        weighted = self._recurrent(spikes)
        total_input = self.w_scale * (input_spikes + weighted)

        # Refractory counter: +1 each step, reset to 1 on spike
        refrac = refrac * (1.0 - spikes) + 1.0
        in_refrac = (refrac > self.steps_refrac).astype(mx.float32)

        # Alpha synapse: decay + delayed input gated by refractoriness
        # Ring buffer: read oldest, shift via pointer-only list rotation.
        delayed = delay_buffer[0]
        conductance = (conductance * (1.0 - self.time_factor_syn)
                       + delayed * in_refrac)
        delay_buffer = delay_buffer[1:] + [total_input]

        # LIF neuron
        v = v + self.time_factor_mem * (conductance - (v - self.v_rest))
        spike = (v > self.v_threshold).astype(mx.float32)
        v = v - (v - self.v_reset) * spike

        # Reset conductance where spiked
        conductance = conductance - conductance * spike

        return conductance, delay_buffer, spike, v, refrac


# ============================================================================
# Data Utilities
# ============================================================================

def get_hash_tables(comp_path):
    """Build flywire ID <-> tensor index mappings from completeness CSV."""
    df_comp = pd.read_csv(comp_path, index_col=0)
    flyid2i = {j: i for i, j in enumerate(df_comp.index)}
    i2flyid = {j: i for i, j in flyid2i.items()}
    return flyid2i, i2flyid


def load_weights(conn_path, comp_path):
    """Load connectome edges as COO arrays, sorted by PRE index, + CSR indptr."""
    data_conn = pd.read_parquet(conn_path)
    data_name = pd.read_csv(comp_path)
    num_neurons = data_name.shape[0]

    row_idx = data_conn['Postsynaptic_Index'].values.astype(np.int32)
    col_idx = data_conn['Presynaptic_Index'].values.astype(np.int32)
    val = data_conn['Excitatory x Connectivity'].values.astype(np.float32)

    # Sort edges by PRE-synaptic index for event-driven contiguous gather
    sort_order = np.argsort(col_idx, kind='stable')
    row_idx = row_idx[sort_order]
    col_idx = col_idx[sort_order]
    val = val[sort_order]

    # Build CSR row-pointer (pre-synaptic boundary)
    counts = np.bincount(col_idx, minlength=num_neurons)
    pre_indptr = np.concatenate([[0], np.cumsum(counts)]).astype(np.int32)

    return num_neurons, row_idx, col_idx, val, pre_indptr


def build_model(comp_path, conn_path):
    """Build an MlxModel from completeness + connectivity data."""
    num_neurons, row_idx, col_idx, val, pre_indptr = load_weights(conn_path, comp_path)
    model = MlxModel(num_neurons, DT, MODEL_PARAMS,
                     row_idx, col_idx, val, pre_indptr)
    return model, num_neurons