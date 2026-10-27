"""Elapsed-time matching choices shared by HTTP and calculation workers."""
WINDOWS = {
    'day': (24, '±1 day'), 'week': (168, '±1 week'),
    'month': (720, '±1 month'), 'quarter': (2160, '±3 months'),
    'half_year': (4320, '±6 months'), 'year': (8760, '±1 year'),
}
DEFAULT_WINDOW = 'week'

def matching_window(value=DEFAULT_WINDOW):
    if not isinstance(value, str) or value not in WINDOWS:
        raise ValueError('Choose a supported transaction matching window.')
    hours, label = WINDOWS[value]
    return {'key': value, 'label': label, 'hours': hours, 'seconds': hours * 3600}
