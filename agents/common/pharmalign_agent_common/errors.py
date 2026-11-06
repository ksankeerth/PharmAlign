"""Ways a run can end without a result."""


class BudgetExceeded(Exception):
    """The run would go past the request's model-call or tool-call budget."""


class OutputInvalid(Exception):
    """The final answer broke the result model or a rule, even after one retry."""


class OutputTruncated(Exception):
    """The model ran out of output tokens (finish reason `length`)."""
