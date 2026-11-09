"""Choose a contiguous suffix of verified earlier candles for blind replay."""
from secrets import randbelow
from .datasets import INTERVAL, combine_datasets, iso_timestamp, load_history_datasets


def replay_prefix(dataset, directory, requested):
    if requested != 'random' and (type(requested) is not int or requested < 0):
        raise ValueError('Invalid replay history length.')
    if requested == 0:
        return []
    matching = {key: d for key, d in load_history_datasets(directory).items()
                if (d.market, d.timeframe, d.venue) == (dataset.market, dataset.timeframe, dataset.venue)}
    previous = dataset.rows[0]['ts'] - INTERVAL[dataset.timeframe]
    for history in combine_datasets(matching).values():
        meta = history.describe()
        start, end = map(iso_timestamp, (meta['start'], meta['end']))
        if start <= previous <= end:
            available = 1 + int((previous-start)/INTERVAL[dataset.timeframe])
            count = 1 + randbelow(available) if requested == 'random' else requested
            if count <= available:
                return history.rows[available-count:available]
    if requested != 'random':
        raise ValueError('The earlier candles for this saved replay are unavailable.')
    return []
