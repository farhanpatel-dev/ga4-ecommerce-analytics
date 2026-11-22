"""Shared matplotlib style so every notebook figure looks like one report."""

from __future__ import annotations

import matplotlib.pyplot as plt

from ga4_analytics.config import FIGURES_DIR

# Colour-blind-safe categorical palette (Okabe-Ito order), one colour per series in a chart.
PALETTE = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#F0E442", "#000000"]
NEUTRAL = "#8C8C8C"
HIGHLIGHT = "#D55E00"


def use_style() -> None:
    plt.rcParams.update({
        "figure.figsize": (9, 4.5),
        "figure.dpi": 110,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "axes.titleweight": "bold",
        "axes.titlesize": 12,
        "axes.labelsize": 10,
        "legend.frameon": False,
        "axes.prop_cycle": plt.cycler(color=PALETTE),
    })


def save(fig, name: str) -> None:
    """Save a figure to docs/figures for the readout (PNG, tight bounding box)."""
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURES_DIR / f"{name}.png", bbox_inches="tight", dpi=130)
