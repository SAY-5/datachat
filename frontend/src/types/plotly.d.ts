declare module "plotly.js-dist-min" {
  export function react(
    el: HTMLElement,
    data: unknown[],
    layout?: Record<string, unknown>,
    config?: Record<string, unknown>,
  ): Promise<HTMLElement>;
  export function purge(el: HTMLElement): void;
  const _default: {
    react: typeof react;
    purge: typeof purge;
  };
  export default _default;
}
