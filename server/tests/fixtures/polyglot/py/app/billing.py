from .tax import rate
import json


class Invoice:
    def total(self, amount):
        return amount + rate(amount)


def export(invoice):
    return json.dumps(invoice.__dict__)
