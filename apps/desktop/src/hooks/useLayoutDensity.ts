import { useEffect, useMemo, useState } from "react";
import type { LayoutDensity, LayoutDensityPref } from "../types";

const PREF_KEY = "suying.layoutDensityPref.v1";

function readPref(): LayoutDensityPref {
  try {
    const v = localStorage.getItem(PREF_KEY);
    if (v === "auto" || v === "comfort" || v === "compact") return v;
  } catch {
    /* ignore */
  }
  return "auto";
}

function densityFromWidth(w: number): LayoutDensity {
  if (w < 1360) return "compact";
  if (w >= 1680) return "wide";
  return "standard";
}

function resolveDensity(pref: LayoutDensityPref, width: number): LayoutDensity {
  if (pref === "compact") return "compact";
  if (pref === "comfort") return width >= 1680 ? "wide" : "standard";
  return densityFromWidth(width);
}

export function useLayoutDensity() {
  const [pref, setPrefState] = useState<LayoutDensityPref>(() => readPref());
  const [width, setWidth] = useState(() =>
    typeof window !== "undefined" ? window.innerWidth : 1440,
  );
  const [dpr, setDpr] = useState(() =>
    typeof window !== "undefined" ? window.devicePixelRatio || 1 : 2,
  );

  useEffect(() => {
    const onResize = () => {
      setWidth(window.innerWidth);
      setDpr(window.devicePixelRatio || 1);
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(PREF_KEY, pref);
    } catch {
      /* ignore */
    }
  }, [pref]);

  const density = useMemo(() => resolveDensity(pref, width), [pref, width]);

  useEffect(() => {
    document.documentElement.setAttribute("data-density", density);
  }, [density]);

  const setPref = (next: LayoutDensityPref) => setPrefState(next);

  return {
    pref,
    setPref,
    density,
    width,
    dpr,
    isCompact: density === "compact",
    isWide: density === "wide",
  };
}

export { densityFromWidth, resolveDensity };
