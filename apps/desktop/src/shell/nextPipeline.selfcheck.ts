import { nextPipelineTab } from "../types";

function assert(cond: boolean, msg: string) {
  if (!cond) throw new Error(msg);
}

assert(nextPipelineTab("overview") === "produce", "overview -> produce");
assert(nextPipelineTab("produce") === "review", "produce -> review");
assert(nextPipelineTab("review") === "publish", "review -> publish");
assert(nextPipelineTab("publish") === null, "publish end");
assert(nextPipelineTab("data") === null, "data not in pipeline");
assert(nextPipelineTab("ops") === null, "ops not in pipeline");
assert(nextPipelineTab("settings") === null, "settings not in pipeline");

console.log("nextPipelineTab selfcheck OK");
