# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""The tool contract: what a tool declares and returns, shared by the tools and the runtime.

A tool is a :class:`~trading_r1.contract.spec.ToolSpec`: a request model in, an output model
out. The output is the authoritative result: typed with the units of :mod:`.units`, unrounded,
stored as evidence as it is, and rendered once into the model's text by :mod:`.render`. The
package imports neither ``runtime`` nor ``tools``; both import it.
"""
