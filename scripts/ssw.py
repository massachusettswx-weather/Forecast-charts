#!/usr/bin/env python3
"""
SSW watch: the probability that the polar vortex breaks.

Criterion (user-specified, 2026-10-27): a member counts as REVERSED when the
60N zonal wind at 10 hPa is EASTMERY (< 0 m/s) for 24 HOURS OR MORE - i.e. four
consecutive 6-hourly steps below zero (the window is measured backwards from the
valid time, so "24 h or more" holds for every step of a longer reversal).

Products
    * per member: the 24 h-easterly state, and the onset time of the reversal
    * per system: % of members reversed at each valid time
    * the reversal probability is what the page leads with

The member series come straight from the run store, so this is pure product
work - no new archive access.
"""
import numpy as np

STEP_HOURS = 6


def easterly_state(u, min_hours=24, step_hours=STEP_HOURS):
    """u: (nstep, nmember) -> bool (nstep, nmember).

    True where the member has been easterly for >= min_hours ending at that step.
    """
    u = np.asarray(u, float)
    need = max(1, int(round(min_hours / step_hours)))
    below = u < 0.0
    out = np.zeros(below.shape, dtype=bool)
    for i in range(below.shape[0]):
        lo = max(0, i - need + 1)
        out[i] = below[lo:i + 1].all(axis=0)
    return out


def reversal_onsets(state):
    """First valid index at which each member satisfies the criterion."""
    state = np.asarray(state, bool)
    onsets = np.full(state.shape[1], -1, dtype=int)
    for m in range(state.shape[1]):
        idx = np.where(state[:, m])[0]
        if len(idx):
            onsets[m] = int(idx[0])
    return onsets


def reversal_probability(u, min_hours=24, step_hours=STEP_HOURS):
    """(probability % per step, state mask, onsets per member)."""
    state = easterly_state(u, min_hours, step_hours)
    prob = 100.0 * state.mean(axis=1)
    return prob, state, reversal_onsets(state)


def segments(state):
    """Contiguous (start, end) index pairs of True runs, per member."""
    segs = []
    for m in range(state.shape[1]):
        s = state[:, m]
        i = 0
        while i < len(s):
            if s[i]:
                j = i
                while j + 1 < len(s) and s[j + 1]:
                    j += 1
                segs.append((m, i, j))
                i = j + 1
            else:
                i += 1
    return segs


def reversal_segments(u, min_hours=24, step_hours=STEP_HOURS):
    """The easterly stretches that satisfy the 24 h criterion.

    Returns (member, start_index, end_index) per segment, where the start is
    the first easterly step of the run that contains the qualifying 24 h
    window (not the fourth step of it) - the highlight should show the whole
    reversal, not just the part that already satisfies the criterion.
    """
    u = np.asarray(u, float)
    below = u < 0.0
    state = easterly_state(u, min_hours, step_hours)
    out = []
    for m in range(below.shape[1]):
        s = state[:, m]
        b = below[:, m]
        i = 0
        while i < len(s):
            if s[i]:
                j = i
                while j + 1 < len(s) and s[j + 1]:
                    j += 1
                # walk back to the start of the underlying easterly run
                k = i
                while k > 0 and b[k - 1]:
                    k -= 1
                out.append((m, k, j))
                i = j + 1
            else:
                i += 1
    return out
