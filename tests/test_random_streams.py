import datetime as dt

import numpy as np

from harness import RandomStreams

DAY = dt.date(2026, 3, 8)


def draws(streams, day=DAY, name="failures"):
    return streams.generator(day, name).random(8)


def test_a_stream_is_reproducible_from_its_key():
    assert np.array_equal(draws(RandomStreams("baseline", 7)), draws(RandomStreams("baseline", 7)))


def test_every_part_of_the_key_gives_a_different_stream():
    base = draws(RandomStreams("baseline", 7))

    others = [
        draws(RandomStreams("storm_houston", 7)),
        draws(RandomStreams("baseline", 8)),
        draws(RandomStreams("baseline", 7), day=DAY + dt.timedelta(days=1)),
        draws(RandomStreams("baseline", 7), name="deployments"),
    ]

    assert not any(np.array_equal(base, other) for other in others)


def test_drawing_from_one_stream_never_shifts_another():
    streams = RandomStreams("baseline", 7)
    before = draws(streams, name="deployments")

    streams.generator(DAY, "failures").random(1000)
    streams.generator(DAY, "soc").random(1000)

    assert np.array_equal(draws(streams, name="deployments"), before)
