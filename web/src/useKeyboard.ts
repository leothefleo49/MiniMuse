import { useEffect } from "react";

/**
 * The software keyboard: `interactive-widget=resizes-content` (index.html) makes the
 * layout viewport shrink with it, so the composer rides just above the keys. This hook
 * reads that shrink (the window loses height while its width stays) and puts it where
 * the CSS needs it: `--nm-kb` holds the keyboard's height, and once it is up,
 * `kb-open` lands on <html> — the floating tab bar hides while the keyboard is open,
 * where it only took space behind the keys. Two thresholds, one to open and a lower
 * one to close, keep the class from flickering while the keyboard animates in. A
 * width change (a rotation, a desktop resize) re-baselines, so a phone in landscape
 * is not treated as a keyboard.
 */
export function useKeyboard(): void {
  useEffect(() => {
    const vv = window.visualViewport;
    const root = document.documentElement;
    let open = false;
    let base = { w: window.innerWidth, h: window.innerHeight };
    const update = () => {
      const w = window.innerWidth;
      const h = vv ? Math.min(window.innerHeight, vv.height) : window.innerHeight;
      if (w !== base.w) {
        // the window itself changed shape: this is a rotation or a desktop resize,
        // not a keyboard; measure again from here
        base = { w, h };
        root.style.setProperty("--nm-kb", "0px");
        root.classList.toggle("kb-open", false);
        open = false;
        return;
      }
      if (h > base.h) {
        base = { w, h };
      }
      const kb = Math.max(0, base.h - h);
      root.style.setProperty("--nm-kb", `${kb}px`);
      const next = open ? kb > 60 : kb > 150;
      if (next !== open) {
        open = next;
        root.classList.toggle("kb-open", open);
      }
    };
    update();
    window.addEventListener("resize", update);
    vv?.addEventListener("resize", update);
    vv?.addEventListener("scroll", update);
    return () => {
      window.removeEventListener("resize", update);
      vv?.removeEventListener("resize", update);
      vv?.removeEventListener("scroll", update);
      root.classList.remove("kb-open");
      root.style.removeProperty("--nm-kb");
    };
  }, []);
}
