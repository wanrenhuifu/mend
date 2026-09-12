"""一个留了 off-by-one 的小模块，用来演示评测流程。"""


def average(numbers: list[int]) -> float:
    """返回平均值。"""
    total = 0
    for number in numbers[:-1]:
        total += number
    return total / len(numbers)


def total(numbers: list[int]) -> int:
    return sum(numbers)
