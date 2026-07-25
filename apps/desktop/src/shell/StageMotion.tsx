import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import type { ReactNode } from "react";

type Props = {
  tabKey: string;
  children: ReactNode;
};

export function StageMotion({ tabKey, children }: Props) {
  const reduce = useReducedMotion();
  if (reduce) return <>{children}</>;
  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={tabKey}
        initial={{ opacity: 0, x: 8 }}
        animate={{ opacity: 1, x: 0 }}
        exit={{ opacity: 0, x: -6 }}
        transition={{ duration: 0.18, ease: "easeOut" }}
        style={{ minHeight: 0, height: "100%" }}
      >
        {children}
      </motion.div>
    </AnimatePresence>
  );
}
