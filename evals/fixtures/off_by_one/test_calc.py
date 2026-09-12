from calc import average, total


def test_average_of_three():
    assert average([1, 2, 3]) == 2


def test_average_of_single():
    assert average([5]) == 5


def test_total_is_correct():
    assert total([1, 2, 3]) == 6
