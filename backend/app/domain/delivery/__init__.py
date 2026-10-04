"""Durable intent, receiver identity and evidence settlement.

Ask producers use ``ask_wake`` for pure destination values and transaction-bound
intent writes, then ``agent.dispatch_pending`` only after commit. ``receipts``
validates registered identity and locks delivery, input and blocks before applying
``block.input_effects``. Native acceptance, echo and work completion stay distinct.
"""
