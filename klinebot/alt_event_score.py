"""Research-only anomaly evidence scores, not event probabilities or trade signals.

Apply existing freshness/history/identity eligibility separately. Weights are fixed
engineering proposals; this module does not change live alerts or PA/ICT/A2 scores.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

VERSION = 'alt-evidence-score-research-v1'
WEIGHTS = {'A': 35., 'OB': 25., 'C': 15., 'E': 25.}
AE_WEIGHTS = {'A': 60., 'E': 40.}


def latest_research_scores(f: pd.DataFrame, state: dict) -> dict:
    """Freeze display-only scores with the same eligibility as this observation."""
    current = evidence_scores(f.tail(1)).iloc[0] if not f.empty else None
    result = {'version': VERSION}
    for model, weights in (('ae', AE_WEIGHTS), ('core', WEIGHTS)):
        reason = ''
        if state.get('spot_error'):
            reason = '行情读取失败'
        elif not state.get('fresh'):
            reason = '行情过期或尚未更新'
        elif not state.get('active'):
            reason = '当前K线无实际成交'
        elif not state.get('ae_ready'):
            reason = '价量历史基准不足'
        elif model == 'core' and (state.get('derivative_error') or not state.get('core_ready')):
            reason = 'OI／已结算费率不足'
        value = current[model + '_score'] if current is not None else np.nan
        if not reason and not np.isfinite(value):
            reason = '评分输入缺失或无效'
        ready = not reason
        result[model] = {
            'value': float(value) if ready else None,
            'parts': {key: float(current[key + '_strength'] * weight) if ready else None
                      for key, weight in weights.items()},
            'reason': reason,
        }
    return result


def strength(actual: pd.Series, boundary: pd.Series) -> pd.Series:
    """Half credit at original boundary; full at twice boundary; zero at zero.

    A zero historical absolute-return boundary gets zero for unchanged price and
    full credit for a nonzero move. Missing/negative boundaries remain unknown.
    """
    valid = np.isfinite(actual) & np.isfinite(boundary) & boundary.ge(0)
    result = (actual.clip(lower=0) / (2 * boundary.where(boundary.gt(0)))).clip(0, 1)
    result = result.mask(valid & boundary.eq(0), actual.gt(0).astype(float))
    return result.where(valid)


def evidence_scores(f: pd.DataFrame) -> pd.DataFrame:
    """Accept causal feature_frame/research-cache fields. Missing OI is not zero.

    Derivative eligibility is intentionally not inferred from numeric presence.
    The caller must gate core scores with its settled-funding/OI readiness mask.
    """
    def col(name):
        return f[name].astype(float) if name in f else pd.Series(np.nan, index=f.index)

    v1, vmed, v95 = col('v1'), col('vmed'), col('vq95')
    r1, r95 = col('r1'), col('r1q95')
    doi, oi95, fee = col('dOI'), col('oiq95'), col('F')
    fee95 = col('fq95') if 'fq95' in f else col('q95')
    fee05 = col('fq05') if 'fq05' in f else col('q05')
    a_boundary = pd.concat([2 * vmed, v95], axis=1).max(axis=1, skipna=False)
    a_boundary = a_boundary.where(vmed.gt(0) & v95.ge(0))
    ob_boundary = oi95.clip(lower=.1)
    a = strength(v1, a_boundary).where(v1.ge(0))
    ob = strength(doi, ob_boundary)
    e = strength(r1.abs(), r95)
    fee_known = np.isfinite(fee) & np.isfinite(fee95) & np.isfinite(fee05)
    # Keep the original settled-rate tail condition. No arbitrary division by a
    # possibly zero or opposite-sign funding quantile to manufacture intensity.
    c = (((fee > 0) & (fee >= fee95)) | ((fee < 0) & (fee <= fee05))).astype(float).where(fee_known)
    parts = pd.DataFrame({'A': a, 'OB': ob, 'C': c, 'E': e}, index=f.index)
    out = parts.rename(columns={k: k + '_strength' for k in parts}).copy()
    out['ae_score'] = (parts[['A', 'E']] * pd.Series(AE_WEIGHTS)).sum(axis=1, min_count=2)
    out['core_score'] = (parts * pd.Series(WEIGHTS)).sum(axis=1, min_count=4)
    discrete = pd.DataFrame({'A': (v1 >= a_boundary).astype(float).where(a.notna()),
                             'OB': (doi >= ob_boundary).astype(float).where(ob.notna()),
                             'C': c, 'E': (r1.abs() >= r95).astype(float).where(e.notna())}, index=f.index)
    out['core_count_score'] = discrete.sum(axis=1, min_count=4) * 25
    out['core_weighted_binary'] = (discrete * pd.Series(WEIGHTS)).sum(axis=1, min_count=4)
    return out
