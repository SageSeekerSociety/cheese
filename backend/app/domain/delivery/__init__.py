"""Durable intent, receiver identity and evidence settlement.

``receipts`` validates registered identity and locks delivery, input and blocks
before applying ``block.input_effects``. Native acceptance, echo and work
completion stay distinct.
"""
