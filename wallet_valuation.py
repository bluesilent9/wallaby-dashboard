"""Reprice an independent display copy; holdings and cost evidence stay unchanged."""
from copy import deepcopy
from decimal import Decimal,ROUND_HALF_UP

def revalue(report,quote):
    if not report:return None
    value=deepcopy(report);balances=value['balances'];price=Decimal(str(quote['aud_per_btc']))
    if not price.is_finite() or price<=0:raise ValueError('Invalid BTC/AUD quote.')
    for row in balances.get('wallets',[]):
        if row.get('btc') is None:continue
        market=Decimal(row['btc'])*price;cash=Decimal(row.get('cash_aud') or '0');cost=Decimal(row['cost_basis_aud']) if row.get('cost_basis_aud') is not None else None
        row.update(btc_value_aud=str(market),value_aud=str(market+cash),unrealised_gain_aud=str(market-cost) if cost is not None else None)
        gain=(market-cost).quantize(Decimal('.01'),rounding=ROUND_HALF_UP) if cost is not None else None
        row['state']='unavailable' if gain is None else 'profit' if gain>0 else 'loss' if gain<0 else 'neutral'
    balances.update(price_aud_per_btc=str(price),valuation_utc=quote['observed_utc'],quote_observed_utc=quote['observed_utc'],quote_source=quote['source'],valuation_error=None)
    return value
