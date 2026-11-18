"""Export the observed learning curves from a completed production.json report."""
import argparse
import json
from pathlib import Path


def plot(report_file, output_file):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    data = json.loads(Path(report_file).read_text())
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    for name, color in (("noise", "#285690"), ("trend", "#b44726")):
        records = [r for r in data["results"] if r["scenario"] == name]
        if not records:
            continue
        run = max(records, key=lambda r: r["bars"])
        points = [p for p in run["learning_curve"] if p["status"] == "ÉVALUÉ"]
        x = [p["requested_candidates"] for p in points]
        label = f"{name} · seed {run['seed']}"
        axes[0].plot(x, [p["forecast"]["auc"] for p in points], marker="o", label=label, color=color)
        axes[1].plot(x, [p["portfolio"]["capital_eur"] for p in points], marker="o", label=label, color=color)
    for ax, title, baseline in zip(axes, ("AUC sur la période finale", "Capital fictif final (€)"), (.5, 30)):
        ax.axhline(baseline, color="#666", linestyle="--", linewidth=1)
        ax.set_xscale("log")
        ax.set_xticks([400, 500, 1000, 5000], labels=["400", "500", "1 000", "5 000"])
        ax.set_xlabel("Candidats disponibles pour l’entraînement")
        ax.set_title(title)
        ax.grid(axis="y", alpha=.3)
        ax.legend(frameon=False, fontsize=9)
    fig.savefig(output_file, dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    plot(args.report, args.output)
