from pokescan.ebay.errors import BudgetExhausted


class RateBudget:
    """Persistent daily call counter. daily_limit is mutable so hot-reloaded config can change it."""

    def __init__(self, usage_repo, daily_limit: int):
        self.usage, self.daily_limit = usage_repo, daily_limit

    def remaining(self) -> int:
        return max(0, self.daily_limit - self.usage.calls_today())

    def consume(self) -> None:
        if self.remaining() <= 0:
            raise BudgetExhausted(f"Daily budget of {self.daily_limit} calls used")
        self.usage.increment()
