RATE = 0.2


def rate(amount):
    if amount > 100:
        return amount * RATE
    return 0
