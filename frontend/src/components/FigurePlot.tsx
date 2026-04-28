import { useEffect, useRef } from "react";

/**
 * Lazily imports plotly.js-dist-min and renders a figure JSON object.
 * Avoids pulling Plotly into the main bundle so the editorial empty
 * state loads instantly.
 */
export function FigurePlot({ figure }: { figure: unknown }) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    const el = ref.current;
    if (!el || !figure || typeof figure !== "object") return;

    const fig = figure as { data?: unknown; layout?: unknown };
    const data = Array.isArray(fig.data) ? fig.data : [];
    const layout = (fig.layout && typeof fig.layout === "object")
      ? { ...(fig.layout as Record<string, unknown>) }
      : {};

    // Force the editorial palette so charts feel typeset, not generic.
    const themed = {
      paper_bgcolor: "transparent",
      plot_bgcolor: "transparent",
      font: {
        family: '"IBM Plex Sans", system-ui, sans-serif',
        color: "#ece7dd",
        size: 12,
      },
      margin: { l: 56, r: 24, t: 32, b: 48 },
      colorway: ["#d76040", "#6c8da6", "#7fc18f", "#d7b86a", "#a07cc2", "#d77a6a"],
      xaxis: { gridcolor: "#22232c", linecolor: "#383b48", zerolinecolor: "#22232c" },
      yaxis: { gridcolor: "#22232c", linecolor: "#383b48", zerolinecolor: "#22232c" },
      ...layout,
    };

    void import("plotly.js-dist-min").then((mod) => {
      if (cancelled || !ref.current) return;
      const Plotly = (mod.default ?? mod) as {
        react: (
          el: HTMLDivElement,
          data: unknown[],
          layout: Record<string, unknown>,
          config: Record<string, unknown>,
        ) => void;
        purge: (el: HTMLDivElement) => void;
      };
      Plotly.react(ref.current, data, themed, {
        displaylogo: false,
        responsive: true,
        modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d"],
      });
    }).catch((err) => {
      console.error("plotly failed to load", err);
    });

    return () => {
      cancelled = true;
      if (el) {
        void import("plotly.js-dist-min").then((mod) => {
          const Plotly = (mod.default ?? mod) as { purge: (el: HTMLDivElement) => void };
          try { Plotly.purge(el); } catch { /* ignore */ }
        }).catch(() => undefined);
      }
    };
  }, [figure]);

  return <div ref={ref} style={{ width: "100%", minHeight: 320 }} />;
}
