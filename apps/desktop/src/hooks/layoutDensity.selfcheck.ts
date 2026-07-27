import { densityFromWidth, resolveDensity } from "./useLayoutDensity";

function assert(cond: boolean, msg: string) {
  if (!cond) throw new Error(msg);
}

assert(densityFromWidth(1280) === "compact", "1280 -> compact");
assert(densityFromWidth(1440) === "standard", "1440 -> standard");
assert(densityFromWidth(1680) === "wide", "1680 -> wide");
assert(resolveDensity("compact", 2000) === "compact", "manual compact wins");
assert(resolveDensity("comfort", 1400) === "standard", "comfort mid");
assert(resolveDensity("comfort", 1800) === "wide", "comfort wide");
assert(resolveDensity("auto", 1300) === "compact", "auto compact");

console.log("useLayoutDensity selfcheck OK");
