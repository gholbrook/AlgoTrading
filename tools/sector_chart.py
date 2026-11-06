"""Optional PNG output for the sector scanner."""
from datetime import datetime, timezone

from matplotlib.collections import LineCollection
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator, StrMethodFormatter


def draw_scan(path, panels, cutoff):
    fig = Figure(figsize=(11, 1.35 + 2.55 * len(panels)), layout="constrained",
                 facecolor="#f7f8f5")
    stamp = datetime.fromtimestamp(cutoff, timezone.utc).strftime("%d %b %Y at %H:%M UTC")
    fig.suptitle(f"Sector scan   /   {stamp}\n"
                 "Blue box = area estimated from prior closes    •    Gold dot = decision close",
                 fontsize=14, ha="left", x=0.06)
    axes = fig.subplots(len(panels), 1, squeeze=False)
    for (result, bars), (ax,) in zip(panels, axes):
        title = f'{result["market"]}   /   {result["venue"]}     ·     {result["sector_status"]}'
        evaluation = result.get("evaluation", {})
        if "fully_contained_minutes" in evaluation:
            title += f'     ·     {evaluation["outcome"]} ({evaluation["fully_contained_minutes"]} min inside)'
        ax.set_title(title, loc="left", fontsize=11)
        if not bars:
            ax.set_axis_off()
            continue
        wicks, bodies, colors = [], [], []
        for bar in bars:
            x = (bar["ts"] + 150 - cutoff) / 3600
            wicks.append([(x, bar["low"]), (x, bar["high"])])
            bodies.append([(x, bar["open"]), (x, bar["close"])])
            colors.append("#167265" if bar["close"] >= bar["open"] else "#ae5543")
        ax.add_collection(LineCollection(wicks, colors=colors, linewidths=0.65))
        ax.add_collection(LineCollection(bodies, colors=colors, linewidths=1.7))
        if result["sector_status"] == "candidate":
            left = (bars[0]["ts"] - cutoff) / 3600
            floor, ceiling = result["floor"], result["ceiling"]
            ax.fill_between([left, 0], floor, ceiling, color="#4885be", alpha=0.15)
            ax.plot([left, 0, 0, left, left], [floor, floor, ceiling, ceiling, floor],
                    color="#316ea5", linewidth=1.15)
            if bars[-1]["ts"] >= cutoff:
                end = (bars[-1]["ts"] + 300 - cutoff) / 3600
                ax.hlines([floor, ceiling], 0, end, colors="#316ea5", linestyles="dashed")
        ax.axvline(0, color="#8b6617", linewidth=0.8, linestyle=":")
        ax.scatter([0], [result["close"]], c="#f6b944", edgecolors="#614718", zorder=4)
        ax.autoscale_view()
        ax.yaxis.set_major_locator(MaxNLocator(4))
        ax.yaxis.set_major_formatter(StrMethodFormatter("{x:.7g}"))
        ax.set_ylabel(result["market"].split("/")[-1])
        ax.grid(axis="y", alpha=0.13)
        ax.spines[["top", "right"]].set_visible(False)
    fig.supxlabel("Hours relative to decision   •   0 = decision time", fontsize=10)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=135)
