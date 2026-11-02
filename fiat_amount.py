"""Readable proposed Bitcoin amounts with a small, explicit AUD error bound."""
from decimal import Decimal,ROUND_HALF_UP
D=Decimal

def convert(amount,quote):
    try:aud=D(str(amount));price=D(str(quote['aud_per_btc']))
    except Exception:raise ValueError('Enter a positive AUD amount and obtain a current BTC/AUD quote.') from None
    if not aud.is_finite() or not price.is_finite() or aud<=0 or price<=0 or aud> D('1e15'):raise ValueError('Enter a positive, finite AUD amount.')
    exact=aud/price
    if exact>D(21000000):raise ValueError('The converted amount exceeds Bitcoin’s maximum supply.')
    # Choose the shortest decimal amount within 0.01%, capped at fifty cents.
    # One half-satoshi is the unavoidable precision floor, not an extra fee.
    tolerance=max(min(aud*D('.0001'),D('.50')),price*D('.000000005'))
    rounded=None
    for places in range(9):
        candidate=exact.quantize(D(1).scaleb(-places),rounding=ROUND_HALF_UP)
        if candidate>0 and abs(candidate*price-aud)<=tolerance:rounded=candidate;break
    if rounded is None:rounded=exact.quantize(D('.00000001'),rounding=ROUND_HALF_UP)
    sats=int(rounded*100000000)
    if sats<1:raise ValueError('That AUD amount is below one satoshi at the current quote.')
    return {'sats':str(sats),'btc':format(rounded,'f'),'requested_aud':str(aud),'estimated_aud':str((rounded*price).quantize(D('.01'),rounding=ROUND_HALF_UP)),'difference_aud':str(rounded*price-aud),'quote':quote,'rounding':'Shortest BTC decimal within 0.01%, capped at AUD$0.50; whole satoshis only.'}
