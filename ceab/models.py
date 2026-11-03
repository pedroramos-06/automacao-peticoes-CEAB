from dataclasses import asdict, dataclass
from decimal import Decimal


class AutomationError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class Benefit:
    process: str
    kind: str
    species: str
    dib: str
    dip: str
    dcb: str | None = None
    nb: str | None = None
    amount: Decimal | None = None

    def to_dict(self):
        result = asdict(self)
        result['amount'] = str(self.amount) if self.amount is not None else None
        return result

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        if data.get('amount') is not None:
            data['amount'] = Decimal(str(data['amount']))
        return cls(**data)


@dataclass(frozen=True)
class Decision:
    event: str
    event_text: str
    destination: str
    destination_text: str
    minute: bool
    warnings: tuple[str, ...] = ()

    def to_dict(self):
        return asdict(self)
